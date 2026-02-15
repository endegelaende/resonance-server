"""
Tests for SeekCoordinator.

Tests the latest-wins semantics, generation counter, and subprocess termination.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from resonance.streaming.seek_coordinator import (
    SeekCoordinator,
    cleanup_processes,
    get_seek_coordinator,
    init_seek_coordinator,
    set_seek_coordinator,
    terminate_subprocess_safely,
)


class TestSeekCoordinator:
    """Tests for SeekCoordinator class."""

    def test_creation(self) -> None:
        """SeekCoordinator can be created without a streaming server."""
        coordinator = SeekCoordinator()
        assert coordinator is not None

    def test_creation_with_streaming_server(self) -> None:
        """SeekCoordinator can be created with a streaming server."""
        mock_server = MagicMock()
        mock_server.get_stream_generation.return_value = 5
        coordinator = SeekCoordinator(streaming_server=mock_server)
        assert coordinator is not None

    def test_get_generation_without_server(self) -> None:
        """Generation counter works without streaming server."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        # Initial generation is 0
        assert coordinator.get_generation(player_mac) == 0

    def test_increment_generation(self) -> None:
        """Generation counter increments correctly."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        gen1 = coordinator._increment_generation(player_mac)
        assert gen1 == 1

        gen2 = coordinator._increment_generation(player_mac)
        assert gen2 == 2

    def test_increment_generation_with_server(self) -> None:
        """Generation counter syncs with streaming server."""
        mock_server = MagicMock()
        mock_server.get_stream_generation.return_value = 10

        coordinator = SeekCoordinator(streaming_server=mock_server)
        player_mac = "aa:bb:cc:dd:ee:ff"

        # Should be max(server_gen, internal_gen) + 1
        gen = coordinator._increment_generation(player_mac)
        assert gen == 11

    @pytest.mark.asyncio
    async def test_seek_executes_executor(self) -> None:
        """Seek calls the executor function."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        executed_positions = []

        async def executor(target: float) -> None:
            executed_positions.append(target)

        result = await coordinator.seek(player_mac, 30.0, executor)
        assert result is True
        assert 30.0 in executed_positions

    @pytest.mark.asyncio
    async def test_seek_latest_wins(self) -> None:
        """Only the latest seek executes when multiple seeks arrive quickly."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        executed_positions = []
        execution_started = asyncio.Event()
        allow_completion = asyncio.Event()

        async def slow_executor(target: float) -> None:
            execution_started.set()
            await allow_completion.wait()
            executed_positions.append(target)

        async def fast_executor(target: float) -> None:
            executed_positions.append(target)

        # Start a slow seek
        task1 = asyncio.create_task(coordinator.seek(player_mac, 10.0, slow_executor))
        await execution_started.wait()

        # Start a second seek - should cancel the first
        task2 = asyncio.create_task(coordinator.seek(player_mac, 50.0, fast_executor))

        # Allow first to complete (but it should be cancelled)
        allow_completion.set()

        result1 = await task1
        result2 = await task2

        # First seek was superseded
        assert result1 is False
        # Second seek succeeded
        assert result2 is True
        # Only the second position should be in the list
        assert 50.0 in executed_positions

    @pytest.mark.asyncio
    async def test_seek_coalesces_rapid_requests(self) -> None:
        """Rapid seeks are coalesced and only the last one executes."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        executed_positions = []

        async def executor(target: float) -> None:
            executed_positions.append(target)

        # Fire multiple seeks rapidly without awaiting
        task1 = asyncio.create_task(coordinator.seek(player_mac, 10.0, executor))
        task2 = asyncio.create_task(coordinator.seek(player_mac, 20.0, executor))
        task3 = asyncio.create_task(coordinator.seek(player_mac, 30.0, executor))

        results = await asyncio.gather(task1, task2, task3)

        # Only one should succeed
        assert sum(results) == 1
        # The last position should be executed
        assert 30.0 in executed_positions

    @pytest.mark.asyncio
    async def test_cancel_player_seeks(self) -> None:
        """cancel_player_seeks stops pending and active seeks."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        execution_started = asyncio.Event()
        allow_completion = asyncio.Event()

        async def blocking_executor(target: float) -> None:
            execution_started.set()
            await allow_completion.wait()

        # Start a seek
        task = asyncio.create_task(coordinator.seek(player_mac, 30.0, blocking_executor))
        await execution_started.wait()

        # Cancel all seeks for this player
        coordinator.cancel_player_seeks(player_mac)
        allow_completion.set()

        result = await task
        assert result is False

    def test_cleanup_player(self) -> None:
        """cleanup_player removes all state for a player."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        # Create some state
        coordinator._increment_generation(player_mac)
        coordinator._get_lock(player_mac)

        assert player_mac in coordinator._generations
        assert player_mac in coordinator._locks

        # Cleanup
        coordinator.cleanup_player(player_mac)

        assert player_mac not in coordinator._generations
        assert player_mac not in coordinator._locks

    @pytest.mark.asyncio
    async def test_different_players_independent(self) -> None:
        """Different players can seek independently."""
        coordinator = SeekCoordinator()
        player1 = "aa:bb:cc:dd:ee:01"
        player2 = "aa:bb:cc:dd:ee:02"

        executed = {}

        async def executor_for(player: str):
            async def executor(target: float) -> None:
                executed[player] = target

            return executor

        result1 = await coordinator.seek(player1, 10.0, await executor_for(player1))
        result2 = await coordinator.seek(player2, 20.0, await executor_for(player2))

        assert result1 is True
        assert result2 is True
        assert executed[player1] == 10.0
        assert executed[player2] == 20.0


class TestTerminateSubprocessSafely:
    """Tests for terminate_subprocess_safely function."""

    @pytest.mark.asyncio
    async def test_already_dead_process(self) -> None:
        """Does nothing if process is already dead."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        await terminate_subprocess_safely(mock_proc)

        mock_proc.terminate.assert_not_called()
        mock_proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_graceful_termination(self) -> None:
        """Terminates gracefully if process responds to SIGTERM."""
        mock_proc = MagicMock()
        mock_proc.returncode = None

        # Simulate process dying after terminate
        async def mock_wait():
            mock_proc.returncode = 0

        mock_proc.wait = mock_wait

        await terminate_subprocess_safely(mock_proc, timeout=0.1)

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_escalates_to_sigkill(self) -> None:
        """Escalates to SIGKILL if process doesn't respond to SIGTERM."""
        mock_proc = MagicMock()
        mock_proc.returncode = None

        call_count = [0]

        async def slow_then_die():
            call_count[0] += 1
            if call_count[0] == 1:
                # First wait (after SIGTERM) times out
                await asyncio.sleep(1.0)
            else:
                # Second wait (after SIGKILL) succeeds
                mock_proc.returncode = -9

        mock_proc.wait = slow_then_die

        await terminate_subprocess_safely(mock_proc, timeout=0.01, kill_timeout=0.1)

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()


class TestCleanupProcesses:
    """Tests for cleanup_processes function."""

    @pytest.mark.asyncio
    async def test_cleanup_empty_lists(self) -> None:
        """Handles empty lists gracefully."""
        await cleanup_processes([], [])

    @pytest.mark.asyncio
    async def test_cancels_pipe_tasks(self) -> None:
        """Cancels pipe tasks before terminating processes."""
        # Create an actual asyncio.Task that we can cancel
        cancel_called = False

        async def dummy_coro():
            nonlocal cancel_called
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancel_called = True
                raise

        task = asyncio.create_task(dummy_coro())

        await cleanup_processes([], [task])

        assert cancel_called or task.cancelled()

    @pytest.mark.asyncio
    async def test_terminates_all_processes(self) -> None:
        """Terminates all provided processes."""
        procs = []
        for _ in range(3):
            mock_proc = MagicMock()
            mock_proc.returncode = None

            mock_proc.wait = AsyncMock(side_effect=lambda p=mock_proc: setattr(p, 'returncode', 0))
            procs.append(mock_proc)

        await cleanup_processes(procs)

        for proc in procs:
            proc.terminate.assert_called_once()


class TestGlobalCoordinator:
    """Tests for global coordinator management functions."""

    def test_get_creates_coordinator(self) -> None:
        """get_seek_coordinator creates a coordinator if none exists."""
        set_seek_coordinator(None)
        coordinator = get_seek_coordinator()
        assert coordinator is not None
        assert isinstance(coordinator, SeekCoordinator)

    def test_set_and_get(self) -> None:
        """set_seek_coordinator and get_seek_coordinator work together."""
        custom = SeekCoordinator()
        set_seek_coordinator(custom)
        assert get_seek_coordinator() is custom

        # Cleanup
        set_seek_coordinator(None)

    def test_init_with_streaming_server(self) -> None:
        """init_seek_coordinator creates a coordinator with streaming server."""
        mock_server = MagicMock()
        mock_server.get_stream_generation.return_value = 5

        coordinator = init_seek_coordinator(mock_server)

        assert coordinator is not None
        assert get_seek_coordinator() is coordinator
        assert coordinator._streaming_server is mock_server

        # Cleanup
        set_seek_coordinator(None)


class TestSeekCoordinatorEdgeCases:
    """Edge case tests for SeekCoordinator."""

    @pytest.mark.asyncio
    async def test_executor_exception_handled(self) -> None:
        """Executor exceptions are caught and logged."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        async def failing_executor(target: float) -> None:
            raise ValueError("Simulated failure")

        result = await coordinator.seek(player_mac, 30.0, failing_executor)
        assert result is False

    @pytest.mark.asyncio
    async def test_seek_after_cleanup(self) -> None:
        """Seek works after cleanup_player was called."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        executed = []

        async def executor(target: float) -> None:
            executed.append(target)

        # First seek
        await coordinator.seek(player_mac, 10.0, executor)

        # Cleanup
        coordinator.cleanup_player(player_mac)

        # Second seek after cleanup
        result = await coordinator.seek(player_mac, 20.0, executor)

        assert result is True
        assert 20.0 in executed

    @pytest.mark.asyncio
    async def test_generation_persists_across_seeks(self) -> None:
        """Generation counter persists across multiple seeks."""
        coordinator = SeekCoordinator()
        player_mac = "aa:bb:cc:dd:ee:ff"

        async def executor(target: float) -> None:
            pass

        await coordinator.seek(player_mac, 10.0, executor)
        gen1 = coordinator.get_generation(player_mac)

        await coordinator.seek(player_mac, 20.0, executor)
        gen2 = coordinator.get_generation(player_mac)

        await coordinator.seek(player_mac, 30.0, executor)
        gen3 = coordinator.get_generation(player_mac)

        assert gen2 > gen1
        assert gen3 > gen2
