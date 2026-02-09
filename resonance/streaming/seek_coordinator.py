"""
Seek Coordinator for Resonance.

Provides server-side coordination of seek operations with:
- Latest-wins semantics (generation counter)
- Graceful subprocess termination
- Coalescing of rapid seek requests
- Prevention of InvalidStateError from subprocess races

This module addresses the problem of rapid seek requests during user scrubbing,
where each seek triggers an expensive pipeline restart (stop → flush → transcode → stream).
Without coordination, this leads to:
- Subprocess race conditions on Windows (asyncio pipe teardown races)
- Wasted CPU from immediately-cancelled transcode pipelines
- InvalidStateError exceptions from double-close scenarios

The SeekCoordinator implements the "Latest Wins" pattern:
1. Each seek increments a generation counter
2. In-flight seeks are cancelled when a new seek arrives
3. Only the seek matching the current generation completes
4. Subprocess cleanup is handled gracefully with timeouts
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from resonance.streaming.server import StreamingServer

logger = logging.getLogger(__name__)

# How long to wait for a graceful subprocess termination before SIGKILL
TERMINATE_TIMEOUT_SECONDS = 2.0

# How long to wait for SIGKILL to take effect
KILL_TIMEOUT_SECONDS = 1.0

# Minimum time between seek executions (coalescing window)
# Reduced from 50ms to 20ms for faster response during scrubbing
SEEK_COALESCE_DELAY_SECONDS = 0.02  # 20ms


@dataclass
class SeekRequest:
    """Represents a pending seek request."""

    player_mac: str
    target_seconds: float
    generation: int


class SeekCoordinator:
    """
    Coordinates seek operations with latest-wins semantics.

    This class ensures that rapid seek requests (e.g., from user scrubbing)
    are coalesced and only the latest seek actually executes. It also
    provides safe subprocess termination to avoid asyncio race conditions.

    Usage:
        coordinator = SeekCoordinator(streaming_server)

        # Instead of directly calling perform_seek:
        await coordinator.seek(player_mac, target_seconds, seek_executor)

    The seek_executor is a coroutine that performs the actual seek work:
        async def seek_executor(target_seconds: float) -> None:
            # Stop player, queue new stream, start track, etc.
            ...

    Thread Safety:
        This class uses per-player locks to ensure only one seek operation
        runs at a time per player. Different players can seek concurrently.
    """

    def __init__(self, streaming_server: StreamingServer | None = None) -> None:
        """
        Initialize the SeekCoordinator.

        Args:
            streaming_server: Optional StreamingServer for generation tracking.
                              If not provided, uses internal generation counters.
        """
        self._streaming_server = streaming_server

        # Per-player generation counters (used if no streaming_server)
        self._generations: dict[str, int] = {}

        # Per-player locks to serialize seek operations
        self._locks: dict[str, asyncio.Lock] = {}

        # Per-player pending seek (for coalescing)
        self._pending_seeks: dict[str, SeekRequest] = {}

        # Per-player active seek tasks
        self._active_tasks: dict[str, asyncio.Task[bool]] = {}

        # Per-player coalesce timers (currently used only for cancellation cleanup).
        # NOTE: Current implementation uses sleep-based coalescing, not timer-based.
        # This dict is reserved for a future optimization where we'd use a single
        # TimerHandle per player instead of spawning multiple sleeping coroutines.
        # For now it's only used to cancel any pending timer on new seek/cleanup.
        self._coalesce_timers: dict[str, asyncio.TimerHandle] = {}

    def _get_lock(self, player_mac: str) -> asyncio.Lock:
        """Get or create a lock for a player."""
        if player_mac not in self._locks:
            self._locks[player_mac] = asyncio.Lock()
        return self._locks[player_mac]

    def _increment_generation(self, player_mac: str) -> int:
        """
        Increment and return the generation counter for a player.

        If a StreamingServer is available, its generation is authoritative.
        Otherwise, we use our internal counter.
        """
        # Our internal counter always increments
        current = self._generations.get(player_mac, 0) + 1
        self._generations[player_mac] = current

        # If streaming server is available, sync with its generation
        if self._streaming_server is not None:
            server_gen = self._streaming_server.get_stream_generation(player_mac)
            if server_gen is not None and server_gen >= current:
                current = server_gen + 1
                self._generations[player_mac] = current

        return current

    def get_generation(self, player_mac: str) -> int:
        """Get the current generation counter for a player."""
        if self._streaming_server is not None:
            server_gen = self._streaming_server.get_stream_generation(player_mac)
            if server_gen is not None:
                return max(server_gen, self._generations.get(player_mac, 0))
        return self._generations.get(player_mac, 0)

    async def seek(
        self,
        player_mac: str,
        target_seconds: float,
        seek_executor: Callable[[float], Coroutine[Any, Any, None]],
    ) -> bool:
        """
        Request a seek operation with latest-wins semantics.

        If a seek is already in progress for this player, it will be cancelled
        and this seek will take precedence. If multiple seeks arrive in rapid
        succession, they are coalesced and only the last one executes.

        Args:
            player_mac: MAC address of the player.
            target_seconds: Target position in seconds.
            seek_executor: Async function that performs the actual seek.
                          Signature: async def executor(target_seconds: float) -> None

        Returns:
            True if this seek completed successfully.
            False if it was superseded by a newer seek.
        """
        generation = self._increment_generation(player_mac)
        request = SeekRequest(
            player_mac=player_mac,
            target_seconds=target_seconds,
            generation=generation,
        )

        logger.debug(
            "Seek request for player %s: %.1fs (generation %d)",
            player_mac,
            target_seconds,
            generation,
        )

        # Store as pending (overwrites any previous pending seek)
        self._pending_seeks[player_mac] = request

        # Cancel any existing coalesce timer
        if player_mac in self._coalesce_timers:
            self._coalesce_timers[player_mac].cancel()
            del self._coalesce_timers[player_mac]

        # Cancel any in-flight seek task - DON'T await, just fire-and-forget
        # Awaiting here can cause deadlocks if the old task is waiting on a lock
        if player_mac in self._active_tasks:
            task = self._active_tasks[player_mac]
            if not task.done():
                task.cancel()
                # Don't await - let it die in background

        # Execute the seek (with coalescing for very rapid requests)
        return await self._execute_seek_with_coalesce(player_mac, seek_executor)

    async def _execute_seek_with_coalesce(
        self,
        player_mac: str,
        seek_executor: Callable[[float], Coroutine[Any, Any, None]],
    ) -> bool:
        """
        Execute a seek with a small coalesce delay to batch rapid requests.

        This prevents multiple transcode pipeline restarts when the user
        is rapidly scrubbing through the track.
        """
        # Small delay to allow coalescing of rapid seeks
        await asyncio.sleep(SEEK_COALESCE_DELAY_SECONDS)

        # Get the latest pending seek (may have been updated during delay)
        request = self._pending_seeks.get(player_mac)
        if request is None:
            return False

        # Check if we're still the latest generation
        current_gen = self.get_generation(player_mac)
        if request.generation != current_gen:
            logger.debug(
                "Seek for player %s superseded (request gen=%d, current=%d)",
                player_mac,
                request.generation,
                current_gen,
            )
            return False

        # Clear pending since we're about to execute
        self._pending_seeks.pop(player_mac, None)

        # Create and track the task
        task = asyncio.create_task(
            self._execute_seek(player_mac, request, seek_executor)
        )
        self._active_tasks[player_mac] = task

        try:
            return await task
        except asyncio.CancelledError:
            logger.debug(
                "Seek for player %s cancelled (generation %d)",
                player_mac,
                request.generation,
            )
            return False

    async def _execute_seek(
        self,
        player_mac: str,
        request: SeekRequest,
        seek_executor: Callable[[float], Coroutine[Any, Any, None]],
    ) -> bool:
        """
        Execute the actual seek operation with generation checks.

        Returns True if the seek completed, False if superseded.
        """
        lock = self._get_lock(player_mac)

        # Try to acquire lock with timeout to prevent deadlock during rapid seeks
        # If we can't get the lock quickly, a newer seek is probably running
        try:
            await asyncio.wait_for(lock.acquire(), timeout=0.5)
        except TimeoutError:
            # This can happen during rapid scrubbing when the previous seek is still
            # executing (stop/flush/start_track). The seek is effectively "dropped".
            # Log at warning level so it's visible in production logs.
            logger.warning(
                "Seek DROPPED for player %s: lock timeout (gen=%d, target=%.1fs). "
                "Previous seek still executing - consider if this happens frequently.",
                player_mac,
                request.generation,
                request.target_seconds,
            )
            return False

        try:
            # Check generation before starting
            if self.get_generation(player_mac) != request.generation:
                return False

            try:
                logger.info(
                    "Executing seek for player %s: %.1fs (generation %d)",
                    player_mac,
                    request.target_seconds,
                    request.generation,
                )

                await seek_executor(request.target_seconds)

                # Check generation after execution
                if self.get_generation(player_mac) != request.generation:
                    logger.debug(
                        "Seek for player %s completed but was superseded",
                        player_mac,
                    )
                    return False

                logger.debug(
                    "Seek for player %s completed successfully (generation %d)",
                    player_mac,
                    request.generation,
                )
                return True

            except asyncio.CancelledError:
                # Re-raise to let the caller handle it
                raise

            except Exception as e:
                logger.exception(
                    "Seek failed for player %s at %.1fs: %s",
                    player_mac,
                    request.target_seconds,
                    e,
                )
                return False
        finally:
            lock.release()

    def cancel_player_seeks(self, player_mac: str) -> None:
        """
        Cancel all pending and active seeks for a player.

        Call this when the player disconnects or when switching tracks.
        """
        # Cancel coalesce timer
        if player_mac in self._coalesce_timers:
            self._coalesce_timers[player_mac].cancel()
            del self._coalesce_timers[player_mac]

        # Clear pending seek
        self._pending_seeks.pop(player_mac, None)

        # Cancel active task
        if player_mac in self._active_tasks:
            task = self._active_tasks[player_mac]
            if not task.done():
                task.cancel()

    def cleanup_player(self, player_mac: str) -> None:
        """
        Clean up all state for a player (e.g., on disconnect).
        """
        self.cancel_player_seeks(player_mac)
        self._generations.pop(player_mac, None)
        self._locks.pop(player_mac, None)
        self._active_tasks.pop(player_mac, None)


async def terminate_subprocess_safely(
    process: asyncio.subprocess.Process,
    timeout: float = TERMINATE_TIMEOUT_SECONDS,
    kill_timeout: float = KILL_TIMEOUT_SECONDS,
) -> None:
    """
    Terminate a subprocess gracefully with escalation to SIGKILL.

    This function handles the common pattern of graceful shutdown:
    1. Check if already dead (prevents double-close)
    2. Send SIGTERM and wait
    3. If timeout, send SIGKILL and wait

    This prevents InvalidStateError from asyncio subprocess pipe teardown
    races on Windows.

    Args:
        process: The subprocess to terminate.
        timeout: Seconds to wait after SIGTERM before SIGKILL.
        kill_timeout: Seconds to wait after SIGKILL.
    """
    # Already dead - nothing to do
    if process.returncode is not None:
        return

    # CRITICAL FIX for Windows asyncio InvalidStateError:
    # We MUST close stdin before terminating the process. If we don't,
    # the event loop might try to close the pipe later when the transport
    # reports connection lost, leading to "InvalidStateError: invalid state"
    # because the future is already done.
    if process.stdin:
        try:
            process.stdin.close()
        except (OSError, RuntimeError, ValueError):
            # Process might be dead or pipe already closed
            pass

    # Try graceful termination first
    with contextlib.suppress(ProcessLookupError, OSError):
        process.terminate()

    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
        return
    except TimeoutError:
        pass
    except asyncio.CancelledError:
        # Don't leave zombie processes on cancellation
        pass

    # Still alive - escalate to SIGKILL
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError, OSError):
            process.kill()

        try:
            await asyncio.wait_for(process.wait(), timeout=kill_timeout)
        except TimeoutError:
            logger.warning(
                "Subprocess PID %s did not die after SIGKILL",
                process.pid,
            )
        except asyncio.CancelledError:
            pass


async def cleanup_processes(
    processes: list[asyncio.subprocess.Process],
    pipe_tasks: list[asyncio.Task[None]] | None = None,
) -> None:
    """
    Clean up a list of subprocesses and their pipe tasks.

    This is designed to be called from a finally block and is fully
    defensive against all error conditions.

    Args:
        processes: List of subprocesses to terminate.
        pipe_tasks: Optional list of pipe copy tasks to cancel.
    """
    # Cancel pipe tasks first (they may be writing to downstream stdin)
    if pipe_tasks:
        for task in pipe_tasks:
            if not task.done():
                task.cancel()

        # Wait for all tasks to acknowledge cancellation and finish cleanup
        # This ensures they release any holds on stdin/stdout
        await asyncio.gather(*pipe_tasks, return_exceptions=True)

    # Terminate all processes
    for proc in processes:
        try:
            await terminate_subprocess_safely(proc)
        except Exception as e:
            logger.debug("Error during process cleanup: %s", e)


# Singleton coordinator instance (can be replaced in tests)
_coordinator: SeekCoordinator | None = None


def get_seek_coordinator() -> SeekCoordinator:
    """
    Get the global SeekCoordinator instance.

    Creates a new instance if one doesn't exist.
    """
    global _coordinator
    if _coordinator is None:
        _coordinator = SeekCoordinator()
    return _coordinator


def set_seek_coordinator(coordinator: SeekCoordinator | None) -> None:
    """
    Set the global SeekCoordinator instance.

    Useful for testing or for setting up with a StreamingServer.
    """
    global _coordinator
    _coordinator = coordinator


def init_seek_coordinator(streaming_server: StreamingServer) -> SeekCoordinator:
    """
    Initialize the global SeekCoordinator with a StreamingServer.

    Args:
        streaming_server: The StreamingServer for generation tracking.

    Returns:
        The initialized SeekCoordinator.
    """
    coordinator = SeekCoordinator(streaming_server)
    set_seek_coordinator(coordinator)
    return coordinator
