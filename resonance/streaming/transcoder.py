"""
Transcoder module for legacy Squeezebox devices.

This module parses legacy.conf rules and provides on-the-fly transcoding
for audio formats that legacy hardware cannot decode natively.

The transcoding pipeline streams audio through external binaries (faad, flac, sox, lame)
and yields chunks for HTTP streaming.

Windows reliability note:
    On Windows, an asyncio-driven multi-process pipeline that manually copies bytes
    between subprocess stdio streams can prematurely EOF under cancellation/teardown
    pressure (rapid seeks). The original LMS (Perl) uses OS-level pipes.

    To match that behavior and avoid premature EOF/races, we implement a Popen-based
    pipeline for multi-stage transcodes. We then bridge the final stdout into the
    async world using a background thread and an asyncio.Queue.

Cancellation/Teardown (Windows-safe):
    - If the client disconnects or a seek cancels the HTTP stream, the async generator
      may be cancelled mid-read. We treat this as normal.
    - We terminate the Popen pipeline with best-effort escalation (terminate -> kill),
      and we always close pipes defensively.

Diagnostics:
    - We log subprocess stderr on early termination and on cancellation to diagnose
      cases where the pipeline yields only a small number of bytes and exits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

from resonance.streaming.seek_coordinator import cleanup_processes

logger = logging.getLogger(__name__)

# Path to config and binaries
CONFIG_DIR = Path(__file__).parent.parent / "config"
THIRD_PARTY_BIN = Path(__file__).parent.parent.parent / "third_party" / "bin"

# Buffer size for streaming (64KB chunks)
STREAM_BUFFER_SIZE = 65536

# If a transcode yields only a tiny amount of output, it's almost always a broken pipeline/EOF.
# Keep this conservative; we only use it for diagnostics.
_EARLY_TERMINATION_BYTES = 512 * 1024  # 512KB


@dataclass
class TranscodeRule:
    """A single transcoding rule from legacy.conf."""

    source_format: str
    dest_format: str
    device_type: str  # Device type pattern (* = any)
    device_id: str  # Device MAC pattern (* = any)
    command: str  # Command line template or "-" for passthrough
    capabilities: str = ""  # F, I, T flags

    def is_passthrough(self) -> bool:
        """Check if this rule is passthrough (no transcoding)."""
        return self.command.strip() == "-"

    def matches(
        self,
        source_format: str,
        device_type: str | None = None,
        device_id: str | None = None,
    ) -> bool:
        """
        Check if this rule matches the given parameters.

        Args:
            source_format: Source file format (e.g., "m4b").
            device_type: Device type name (optional).
            device_id: Device MAC address (optional).

        Returns:
            True if this rule matches.
        """
        # Check source format
        if self.source_format != source_format.lower():
            return False

        # Check device type (if specified)
        if device_type and self.device_type != "*":
            if self.device_type.lower() != device_type.lower():
                return False

        # Check device ID (if specified)
        if device_id and self.device_id != "*":
            if self.device_id.lower() != device_id.lower():
                return False

        return True


@dataclass
class TranscodeConfig:
    """Loaded transcoding configuration."""

    rules: list[TranscodeRule]

    def find_rule(
        self,
        source_format: str,
        dest_format: str | None = None,
        device_type: str | None = None,
        device_id: str | None = None,
    ) -> TranscodeRule | None:
        """
        Find the first matching transcoding rule.

        More specific rules (with device_type/device_id) are checked first
        because they should appear earlier in the config file.

        Args:
            source_format: Source file format (e.g., "m4b").
            dest_format: Desired output format (optional, None = any).
            device_type: Device type name (optional).
            device_id: Device MAC address (optional).

        Returns:
            The first matching TranscodeRule, or None if no match.
        """
        source_format = source_format.lower().lstrip(".")

        for rule in self.rules:
            if not rule.matches(source_format, device_type, device_id):
                continue

            # Check dest format if specified
            if dest_format and rule.dest_format != dest_format.lower():
                continue

            return rule

        return None

    def needs_transcoding(
        self,
        source_format: str,
        device_type: str | None = None,
        device_id: str | None = None,
    ) -> bool:
        """
        Check if a format needs transcoding for the given device.

        Args:
            source_format: Source file format (e.g., "m4b").
            device_type: Device type name (optional).
            device_id: Device MAC address (optional).

        Returns:
            True if transcoding is needed (rule exists and is not passthrough).
        """
        rule = self.find_rule(source_format, device_type=device_type, device_id=device_id)

        if rule is None:
            # No rule found - assume transcoding needed for safety
            return True

        return not rule.is_passthrough()


def parse_legacy_conf(config_path: Path | None = None) -> TranscodeConfig:
    """
    Parse the legacy.conf transcoding configuration file.

    Args:
        config_path: Path to legacy.conf. If None, uses default location.

    Returns:
        Parsed TranscodeConfig.
    """
    if config_path is None:
        config_path = CONFIG_DIR / "legacy.conf"

    logger.debug("Loading transcode config from %s", config_path)

    rules: list[TranscodeRule] = []
    current_header: tuple[str, str, str, str] | None = None
    current_capabilities: str = ""

    with config_path.open("r", encoding="utf-8") as f:
        for line in f:
            # Strip whitespace
            line = line.rstrip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                # Check for capability comment (# F, # FT, etc.)
                if line.startswith("#") and len(line) <= 10:
                    caps_match = re.match(r"#\s*([FIRTDBU]+)", line)
                    if caps_match:
                        current_capabilities = caps_match.group(1)
                continue

            # Check if this is a header line (format definition)
            parts = line.split()
            if len(parts) == 4 and not line.startswith("\t") and not line.startswith(" "):
                # This is a header: source dest device_type device_id
                current_header = (parts[0], parts[1], parts[2], parts[3])
                current_capabilities = ""
                continue

            # This should be a command line (indented)
            if current_header and (line.startswith("\t") or line.startswith(" ")):
                command = line.strip()

                # Check for inline capability comment
                if command.startswith("#"):
                    caps_match = re.match(r"#\s*([FIRTDBU]+)", command)
                    if caps_match:
                        current_capabilities = caps_match.group(1)
                    continue

                source, dest, dev_type, dev_id = current_header
                rules.append(
                    TranscodeRule(
                        source_format=source.lower(),
                        dest_format=dest.lower(),
                        device_type=dev_type,
                        device_id=dev_id,
                        command=command,
                        capabilities=current_capabilities,
                    )
                )
                current_header = None
                current_capabilities = ""

    logger.info("Loaded %d transcoding rules from %s", len(rules), config_path.name)
    return TranscodeConfig(rules=rules)


def resolve_binary(name: str) -> Path | None:
    """
    Resolve a binary name to its full path.

    Searches in order:
    1. third_party/bin/ directory
    2. System PATH

    Args:
        name: Binary name (e.g., "faad", "flac").

    Returns:
        Path to the binary, or None if not found.
    """
    is_windows = sys.platform.startswith("win")

    def _is_usable_binary(path: Path) -> bool:
        if not path.exists() or not path.is_file():
            return False
        if is_windows:
            return True
        return os.access(path, os.X_OK)

    # Prefer platform-appropriate binaries in third_party/bin.
    # On Linux/macOS we MUST NOT select *.exe files.
    candidate_paths: list[Path]
    if is_windows:
        candidate_paths = [THIRD_PARTY_BIN / f"{name}.exe", THIRD_PARTY_BIN / name]
    else:
        candidate_paths = [THIRD_PARTY_BIN / name]

    for bin_path in candidate_paths:
        if _is_usable_binary(bin_path):
            return bin_path

    # Check system PATH
    system_path = shutil.which(f"{name}.exe" if is_windows else name)
    if system_path:
        return Path(system_path)

    return None


def build_command(
    rule: TranscodeRule,
    file_path: Path,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[list[str]]:
    """
    Build the command pipeline from a transcoding rule.

    Args:
        rule: The TranscodeRule to use.
        file_path: Path to the source audio file.
        start_seconds: Optional seek start position in seconds.
            Flag is binary-aware: ``-ss`` for ffmpeg, ``-j`` for faad.
        end_seconds: Optional seek end position in seconds.
            Flag is binary-aware: ``-to`` for ffmpeg, ``-e`` for faad.

    Returns:
        List of command lists (for piping). Each inner list is a command + args.

    Raises:
        ValueError: If a required binary is not found.
    """
    command = rule.command

    # Split on pipe for pipeline commands
    pipe_parts = [p.strip() for p in command.split("|")]

    result: list[list[str]] = []

    for part in pipe_parts:
        # Use shlex to split arguments correctly (handles quotes)
        # Note: we use posix=False on Windows to better handle backslashes in paths
        # if they were present, but here we are parsing the config command line.
        # legacy.conf uses shell-like syntax.
        args = shlex.split(part)

        if not args:
            continue

        # Check for [binary] syntax in the first argument
        binary_match = re.match(r"\[(\w+)\]", args[0])
        segment_binary = None  # Track binary name for context-aware substitution
        if binary_match:
            segment_binary = binary_match.group(1).lower()
            binary_path = resolve_binary(segment_binary)
            if binary_path is None:
                raise ValueError(f"Binary not found: {segment_binary}")

            # Replace the [binary] placeholder with the full path
            cmd_args = [str(binary_path)] + args[1:]
        else:
            # Plain command - use as is (system path resolution happens in subprocess)
            cmd_args = args

        # Choose seek flags based on binary.
        # ffmpeg uses -ss (start) / -to (end); faad uses -j / -e.
        if segment_binary == "ffmpeg":
            start_flag, end_flag = "-ss", "-to"
        else:
            start_flag, end_flag = "-j", "-e"

        # Perform placeholder substitution on arguments
        final_args = []
        for arg in cmd_args:
            # Replace $FILE$
            if "$FILE$" in arg:
                arg = arg.replace("$FILE$", str(file_path))

            # Replace $START$
            if "$START$" in arg:
                if start_seconds is not None and start_seconds > 0:
                    # Expand to "<flag> <seconds>" (two args).
                    # e.g. ffmpeg: "-ss 123.456", faad: "-j 123.456"
                    replacement = f"{start_flag} {start_seconds:.3f}"
                    if arg == "$START$":
                        final_args.extend(replacement.split())
                        continue
                    else:
                        arg = arg.replace("$START$", replacement)
                else:
                    # Remove the placeholder (no seeking requested)
                    if arg == "$START$":
                        continue
                    arg = arg.replace("$START$", "")

            # Replace $END$
            if "$END$" in arg:
                if end_seconds is not None and end_seconds > 0:
                    replacement = f"{end_flag} {end_seconds:.3f}"
                    if arg == "$END$":
                        final_args.extend(replacement.split())
                        continue
                    else:
                        arg = arg.replace("$END$", replacement)
                else:
                    if arg == "$END$":
                        continue
                    arg = arg.replace("$END$", "")

            if arg:
                final_args.append(arg)

        result.append(final_args)

    return result


def _read_stream_in_thread(
    stream,
    loop: asyncio.AbstractEventLoop,
    out_q: queue.Queue[bytes | None],
) -> None:
    """
    Read a blocking file-like stream in a thread and forward bytes into a threadsafe queue.

    We intentionally do NOT push directly into an asyncio.Queue from the thread, because
    that can overflow (`QueueFull`) if the event loop is busy. Instead we use a standard
    `queue.Queue`, and the async side drains it (with backpressure) safely.

    The queue receives:
      - bytes chunks
      - None sentinel on EOF
    """
    try:
        while True:
            data = stream.read(STREAM_BUFFER_SIZE)
            if not data:
                break
            # Blocks when the async consumer is behind -> backpressure, no crashes.
            out_q.put(data)
    except Exception as e:
        logger.debug("Threaded stdout reader error (expected on teardown): %s", e)
    finally:
        with contextlib.suppress(Exception):
            out_q.put(None)
        with contextlib.suppress(Exception):
            stream.close()


def _terminate_popen_safely(proc: subprocess.Popen, timeout: float = 2.0) -> None:
    """
    Best-effort terminate/kill for subprocess.Popen processes.

    This is intentionally defensive; Windows is sensitive to teardown ordering.
    """
    if proc.poll() is not None:
        return

    with contextlib.suppress(Exception):
        proc.terminate()

    try:
        proc.wait(timeout=timeout)
        return
    except Exception:
        pass

    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(timeout=1.0)


def _cleanup_popen_pipeline_sync(procs: list[subprocess.Popen]) -> None:
    """
    Clean up a Popen pipeline - SYNCHRONOUS and FAST.

    This is designed for rapid seek scenarios where we need to abandon
    old transcoders quickly without waiting for them to fully terminate.

    LMS-style approach: Just close the pipes and kill the processes.
    Don't wait - the OS will clean them up.

    IMPORTANT: This function is intentionally NOT async. It must be callable
    from a finally block during asyncio.CancelledError handling, where
    await/create_task may not work correctly.
    """
    # Close pipe endpoints first to unblock any readers/writers
    for p in procs:
        with contextlib.suppress(Exception):
            if p.stdin:
                p.stdin.close()
        with contextlib.suppress(Exception):
            if p.stdout:
                p.stdout.close()
        with contextlib.suppress(Exception):
            if p.stderr:
                p.stderr.close()

    # Kill processes immediately - don't wait for graceful termination
    # This is critical for rapid seeks where we can't afford to block
    for p in reversed(procs):
        if p.poll() is None:  # Still running
            with contextlib.suppress(Exception):
                p.kill()  # SIGKILL - immediate, no waiting

    # Don't wait for processes to die - OS will reap them eventually
    # Waiting here can block for seconds if processes are slow to terminate


async def _log_popen_stderr(
    procs: list[subprocess.Popen],
    *,
    cancelled: bool,
    bytes_yielded: int,
) -> None:
    """
    Read and log stderr (best effort). Tools often write useful messages to stderr even with rc=0.

    IMPORTANT: This must not block! Use timeout and be defensive.
    """
    for i, p in enumerate(procs):
        if not p.stderr:
            continue
        data = b""
        try:
            # Use timeout to avoid blocking forever if process is still running
            data = await asyncio.wait_for(
                asyncio.to_thread(p.stderr.read, 4096),  # Read limited amount
                timeout=0.1,  # Very short timeout - we don't want to block
            )
        except (TimeoutError, Exception):
            # Timeout or error - just skip, this is best-effort logging
            pass
        if data:
            logger.info(
                "Transcode stderr (popen=%d, pid=%s, cancelled=%s, bytes=%d): %s",
                i,
                getattr(p, "pid", None),
                cancelled,
                bytes_yielded,
                data.decode(errors="ignore")[:2000],
            )


async def transcode_stream(
    file_path: Path,
    rule: TranscodeRule,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> AsyncGenerator[bytes, None]:
    """
    Stream transcoded audio data using the specified rule.

    This sets up a subprocess pipeline and yields chunks of transcoded data.

    Windows uses a Popen-based OS-pipe pipeline (to match LMS behavior and avoid
    premature EOF races). Other platforms use the asyncio pipeline.

    Args:
        file_path: Path to the source audio file.
        rule: TranscodeRule specifying how to transcode.
        start_seconds: Optional seek start position in seconds.
        end_seconds: Optional seek end position in seconds.

    Yields:
        Chunks of transcoded audio data.
    """
    logger.debug("[TRANSCODE] transcode_stream() called for %s", file_path.name)

    if rule.is_passthrough():
        raise ValueError("Cannot transcode with passthrough rule")

    try:
        commands = build_command(rule, file_path, start_seconds, end_seconds)
    except ValueError as e:
        logger.error("Failed to build transcode command: %s", e)
        raise

    if start_seconds:
        logger.info(
            "[TRANSCODE] Starting: %s -> %s using %d command(s), seeking to %.1fs",
            file_path.name,
            rule.dest_format,
            len(commands),
            start_seconds,
        )
    else:
        logger.info(
            "[TRANSCODE] Starting: %s -> %s using %d command(s)",
            file_path.name,
            rule.dest_format,
            len(commands),
        )

    if len(commands) > 1:
        # Windows-safe Popen pipeline (also used generally for multi-stage pipelines)
        procs: list[subprocess.Popen] = []
        out_q: queue.Queue[bytes | None] = queue.Queue(maxsize=32)
        reader_thread: threading.Thread | None = None
        loop = asyncio.get_running_loop()

        bytes_yielded = 0

        try:
            prev_stdout = None

            for i, cmd in enumerate(commands):
                logger.debug("[TRANSCODE] Starting Popen stage %d: %s", i, " ".join(cmd))

                proc = subprocess.Popen(
                    cmd,
                    stdin=prev_stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
                procs.append(proc)
                logger.debug("[TRANSCODE] Popen stage %d started, pid=%s", i, proc.pid)

                # The next stage consumes this stage's stdout
                prev_stdout = proc.stdout

            final_stdout = procs[-1].stdout
            if final_stdout is None:
                raise RuntimeError("No stdout from final transcode process (Popen)")

            logger.debug("[TRANSCODE] All Popen stages started, starting reader thread")

            # Start a blocking reader thread for the final stdout
            reader_thread = threading.Thread(
                target=_read_stream_in_thread,
                args=(final_stdout, loop, out_q),
                daemon=True,
            )
            reader_thread.start()

            logger.debug("[TRANSCODE] Reader thread started, entering yield loop")
            chunk_count = 0
            while True:
                # Use timeout on queue.get() to allow asyncio cancellation
                # Without this, CancelledError can't interrupt blocking get()
                try:
                    item = await asyncio.to_thread(out_q.get, timeout=0.5)
                except queue.Empty:
                    # Timeout - loop again to check for cancellation
                    continue
                if item is None:
                    logger.debug("[TRANSCODE] Received None from queue, EOF")
                    break
                bytes_yielded += len(item)
                chunk_count += 1
                if chunk_count <= 3 or chunk_count % 100 == 0:
                    logger.debug("[TRANSCODE] Yielded chunk %d, %d bytes total", chunk_count, bytes_yielded)
                yield item

            # Give the pipeline a moment to finalize and expose returncodes
            for p in procs:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(p.wait, 0.2)

            # Log suspicious early termination diagnostics (including stderr)
            early = bytes_yielded > 0 and bytes_yielded < _EARLY_TERMINATION_BYTES
            if early:
                await _log_popen_stderr(procs, cancelled=False, bytes_yielded=bytes_yielded)

            # If any stage failed, surface it
            for i, p in enumerate(procs):
                rc = p.poll()
                if rc is not None and rc != 0:
                    await _log_popen_stderr(procs, cancelled=False, bytes_yielded=bytes_yielded)
                    raise RuntimeError(f"Transcode stage {i} exited with code {rc}")

            logger.info("Transcode complete: %s, yielded %d bytes", file_path.name, bytes_yielded)

        except asyncio.CancelledError:
            was_cancelled = True
            logger.debug("[TRANSCODE] CancelledError caught, bytes_yielded=%d", bytes_yielded)
            # Don't try to read stderr on cancellation - just cleanup and go!
            raise
        finally:
            logger.debug("[TRANSCODE] Entering finally block, cleaning up pipeline")
            # Ensure pipeline is torn down and reader is unblocked
            # This MUST be fast and non-blocking for rapid seeks to work
            # Use SYNC cleanup - no await, no create_task - just close and kill
            _cleanup_popen_pipeline_sync(procs)

            # Best-effort join the reader thread to prevent thread accumulation on Windows.
            # The thread should exit quickly once we've closed the pipes above.
            # Use a short timeout to avoid blocking rapid seeks.
            if reader_thread is not None and reader_thread.is_alive():
                reader_thread.join(timeout=0.1)
                if reader_thread.is_alive():
                    logger.debug("[TRANSCODE] Reader thread still alive after join timeout")

            logger.debug("[TRANSCODE] Pipeline cleanup complete")

        return

    # Single-stage transcodes: keep existing asyncio subprocess path
    processes: list[asyncio.subprocess.Process] = []
    pipe_tasks: list[asyncio.Task[None]] = []

    try:
        for i, cmd in enumerate(commands):
            logger.debug("Starting subprocess %d: %s", i, " ".join(cmd))

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(proc)

        final_proc = processes[-1]
        if final_proc.stdout is None:
            raise RuntimeError("No stdout from transcode process")

        bytes_yielded = 0
        was_cancelled = False
        try:
            while True:
                chunk = await final_proc.stdout.read(STREAM_BUFFER_SIZE)
                if not chunk:
                    break
                bytes_yielded += len(chunk)
                yield chunk
        except asyncio.CancelledError:
            was_cancelled = True
            raise
        finally:
            early_termination = bytes_yielded > 0 and bytes_yielded < _EARLY_TERMINATION_BYTES
            if was_cancelled or early_termination:
                for i, proc in enumerate(processes):
                    if proc.stderr:
                        data = b""
                        with contextlib.suppress(Exception):
                            data = await proc.stderr.read()
                        if data:
                            logger.info(
                                "Transcode stderr (proc=%d, pid=%s, cancelled=%s, bytes=%d): %s",
                                i,
                                getattr(proc, "pid", None),
                                was_cancelled,
                                bytes_yielded,
                                data.decode(errors="ignore")[:2000],
                            )

        for i, proc in enumerate(processes):
            if proc.returncode is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=1.0)

            if proc.returncode is not None and proc.returncode != 0:
                stderr_data = b""
                if proc.stderr:
                    with contextlib.suppress(Exception):
                        stderr_data = await proc.stderr.read()

                logger.warning(
                    "Transcode process %d exited with code %d: %s",
                    i,
                    proc.returncode,
                    stderr_data.decode(errors="ignore")[:500] if stderr_data else "no stderr",
                )

        logger.info("[TRANSCODE] Complete: %s, yielded %d bytes", file_path.name, bytes_yielded)

    finally:
        await cleanup_processes(processes, pipe_tasks)


def get_output_content_type(dest_format: str) -> str:
    """
    Get the MIME content type for the transcoded output format.

    Args:
        dest_format: Output format (flc, pcm, mp3, etc.).

    Returns:
        MIME type string.
    """
    content_types = {
        "flc": "audio/flac",
        "flac": "audio/flac",
        "pcm": "audio/L16",
        "mp3": "audio/mpeg",
        "aac": "audio/aac",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
    }
    return content_types.get(dest_format.lower(), "application/octet-stream")


# Global singleton instance (lazy loaded)
_transcode_config: TranscodeConfig | None = None


def get_transcode_config() -> TranscodeConfig:
    """
    Get the global transcoding configuration (lazy loaded singleton).

    Returns:
        The TranscodeConfig instance.
    """
    global _transcode_config

    if _transcode_config is None:
        _transcode_config = parse_legacy_conf()

    return _transcode_config


def reload_transcode_config() -> TranscodeConfig:
    """
    Force reload of transcoding configuration.

    Returns:
        The newly loaded TranscodeConfig instance.
    """
    global _transcode_config
    _transcode_config = parse_legacy_conf()
    return _transcode_config
