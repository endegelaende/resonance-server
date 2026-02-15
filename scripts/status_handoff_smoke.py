#!/usr/bin/env python3
"""Live smoke-test for track handover and status/progress consistency.

This tool talks directly to LMS-compatible JSON-RPC and validates the
prefetch handover path that caused UI/progress desync issues.

Checks during polling:
- mode reaches "play" (optional)
- time does not regress on the same track (with tolerance)
- time does not exceed duration (with tolerance)
- progress keeps moving while playing (stall detection)
- playlist index increments across track changes (optional strict mode)

Examples (Windows / micromamba env):
  python scripts/status_handoff_smoke.py --album-id 3 --seconds 90
  python scripts/status_handoff_smoke.py --autoplay-first-album --strict-index-increment
  python scripts/status_handoff_smoke.py --player-id 00:04:20:26:84:ae --seconds 45
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class StatusSample:
    mode: str
    playlist_index: int | None
    track_id: Any
    track_title: str
    stream_generation: int | None
    elapsed: float
    duration: float


class JsonRpcClient:
    def __init__(self, base_url: str, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._id_counter = itertools.count(1)

    def request(self, player_id: str, command: list[str]) -> dict[str, Any]:
        payload = {
            "id": next(self._id_counter),
            "method": "slim.request",
            "params": [player_id, command],
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self._base_url}/jsonrpc.js",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"JSON-RPC request failed: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid JSON-RPC response: {exc}") from exc

        if "error" in data and data["error"] is not None:
            raise RuntimeError(f"RPC error: {data['error']}")

        result = data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected RPC result shape: {result!r}")
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live smoke-test for prefetch handover + status progress",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:9000",
        help="Resonance base URL (default: http://127.0.0.1:9000)",
    )
    parser.add_argument(
        "--player-id",
        default=None,
        help="Player MAC/id (default: auto-detect first connected audio player)",
    )
    parser.add_argument(
        "--album-id",
        type=int,
        default=None,
        help="Album ID to start via playlist loadtracks before polling",
    )
    parser.add_argument(
        "--autoplay-first-album",
        action="store_true",
        help="Resolve first album via albums 0 1 and start it before polling",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=90.0,
        help="Polling duration in seconds (default: 90)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--require-play",
        action="store_true",
        help="Fail if mode never reaches play during the run",
    )
    parser.add_argument(
        "--require-transitions",
        type=int,
        default=0,
        help="Fail if fewer than N track transitions are observed",
    )
    parser.add_argument(
        "--strict-index-increment",
        action="store_true",
        help="Fail if playlist index does not increment by +1 on track change",
    )
    parser.add_argument(
        "--duration-tolerance",
        type=float,
        default=0.5,
        help="Allowed (time - duration) headroom before failing (default: 0.5s)",
    )
    parser.add_argument(
        "--backstep-tolerance",
        type=float,
        default=0.35,
        help="Allowed backward step on same track before failing (default: 0.35s)",
    )
    parser.add_argument(
        "--progress-epsilon",
        type=float,
        default=0.05,
        help="Minimum forward delta counted as progress (default: 0.05s)",
    )
    parser.add_argument(
        "--stall-seconds",
        type=float,
        default=6.0,
        help="Fail if no progress this long while mode=play (default: 6s)",
    )
    return parser.parse_args()


def choose_player(client: JsonRpcClient, requested_player_id: str | None) -> dict[str, Any]:
    serverstatus = client.request("-", ["serverstatus", "0", "100"])
    players = serverstatus.get("players_loop") or []
    if not isinstance(players, list) or not players:
        raise RuntimeError("No players found in serverstatus players_loop")

    if requested_player_id:
        for player in players:
            if str(player.get("playerid", "")).lower() == requested_player_id.lower():
                return player
        raise RuntimeError(f"Requested player not found: {requested_player_id}")

    connected_audio = [
        p for p in players if int(p.get("connected", 0) or 0) == 1 and int(p.get("isplayer", 1) or 0) == 1
    ]
    if connected_audio:
        return connected_audio[0]

    connected_any = [p for p in players if int(p.get("connected", 0) or 0) == 1]
    if connected_any:
        return connected_any[0]

    return players[0]


def resolve_first_album_id(client: JsonRpcClient) -> int:
    result = client.request("-", ["albums", "0", "1"])
    albums = result.get("albums_loop") or []
    if not albums:
        raise RuntimeError("No album found for --autoplay-first-album")

    first = albums[0]
    album_id = first.get("id")
    if album_id is None:
        raise RuntimeError("First album has no id")

    try:
        return int(album_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid album id returned: {album_id!r}") from exc


def fetch_status_sample(client: JsonRpcClient, player_id: str) -> StatusSample:
    result = client.request(player_id, ["status", "-", "1", "tags:aAdlKkt"])
    mode = str(result.get("mode", "stop") or "stop")

    idx_raw = result.get("playlist index")
    if idx_raw is None:
        idx_raw = result.get("playlist_cur_index")

    playlist_index: int | None
    try:
        playlist_index = int(idx_raw) if idx_raw is not None else None
    except (TypeError, ValueError):
        playlist_index = None

    current_track = result.get("currentTrack") if isinstance(result.get("currentTrack"), dict) else {}
    track_id = result.get("track_id", current_track.get("id"))
    track_title = str(current_track.get("title", "") or "")

    stream_generation_raw = result.get("stream_generation")
    try:
        stream_generation = int(stream_generation_raw) if stream_generation_raw is not None else None
    except (TypeError, ValueError):
        stream_generation = None

    elapsed = float(result.get("time", 0.0) or 0.0)
    duration = float(result.get("duration", 0.0) or 0.0)

    return StatusSample(
        mode=mode,
        playlist_index=playlist_index,
        track_id=track_id,
        track_title=track_title,
        stream_generation=stream_generation,
        elapsed=elapsed,
        duration=duration,
    )


def run_smoke(args: argparse.Namespace) -> int:
    client = JsonRpcClient(base_url=args.base_url)

    player = choose_player(client, args.player_id)
    player_id = str(player.get("playerid", ""))
    if not player_id:
        raise RuntimeError("Selected player has no playerid")

    print(
        f"[info] player={player_id} name={player.get('name', '')} model={player.get('model', '')} "
        f"isplayer={player.get('isplayer', 1)} connected={player.get('connected', 1)}"
    )

    album_id = args.album_id
    if args.autoplay_first_album and album_id is None:
        album_id = resolve_first_album_id(client)
        print(f"[info] resolved first album id={album_id}")

    if album_id is not None:
        client.request(player_id, ["playlist", "loadtracks", f"album_id:{album_id}"])
        print(f"[info] started album via playlist loadtracks album_id:{album_id}")

    start = time.monotonic()
    deadline = start + max(1.0, args.seconds)
    last: StatusSample | None = None
    no_progress_seconds = 0.0
    saw_play = False
    transitions = 0
    failures: list[str] = []

    sample_no = 0
    while time.monotonic() < deadline:
        sample_no += 1
        sample = fetch_status_sample(client, player_id)

        if sample.mode == "play":
            saw_play = True

        if sample.duration > 0 and sample.elapsed > sample.duration + args.duration_tolerance:
            failures.append(
                f"sample {sample_no}: time exceeds duration ({sample.elapsed:.3f}s > {sample.duration:.3f}s)"
            )

        if last is not None:
            same_track = (sample.track_id is not None and sample.track_id == last.track_id)
            same_generation = (
                sample.stream_generation is not None
                and last.stream_generation is not None
                and sample.stream_generation == last.stream_generation
            )

            if same_track and same_generation:
                both_playing = sample.mode == "play" and last.mode == "play"

                if both_playing and sample.elapsed + args.backstep_tolerance < last.elapsed:
                    failures.append(
                        f"sample {sample_no}: backward time step on same track/gen "
                        f"({last.elapsed:.3f} -> {sample.elapsed:.3f})"
                    )

                if both_playing:
                    if sample.elapsed <= last.elapsed + args.progress_epsilon:
                        no_progress_seconds += args.poll_interval
                    else:
                        no_progress_seconds = 0.0

                    if no_progress_seconds >= args.stall_seconds:
                        failures.append(
                            f"sample {sample_no}: progress stalled for >= {args.stall_seconds:.1f}s "
                            f"on track_id={sample.track_id}"
                        )
                else:
                    no_progress_seconds = 0.0
            else:
                no_progress_seconds = 0.0

            if (
                sample.track_id is not None
                and last.track_id is not None
                and sample.track_id != last.track_id
            ):
                transitions += 1
                if (
                    args.strict_index_increment
                    and sample.playlist_index is not None
                    and last.playlist_index is not None
                    and sample.playlist_index != last.playlist_index + 1
                ):
                    failures.append(
                        f"sample {sample_no}: track changed but playlist index not +1 "
                        f"({last.playlist_index} -> {sample.playlist_index})"
                    )

        print(
            f"[{sample_no:03d}] mode={sample.mode:<5} idx={sample.playlist_index!s:<3} "
            f"gen={sample.stream_generation!s:<4} track={sample.track_id!s:<6} "
            f"time={sample.elapsed:7.3f}/{sample.duration:7.3f} title={sample.track_title}"
        )

        last = sample
        if failures:
            break

        time.sleep(max(0.05, args.poll_interval))

    if args.require_play and not saw_play:
        failures.append("mode never reached 'play'")

    if transitions < max(0, int(args.require_transitions)):
        failures.append(
            f"observed transitions={transitions}, required={args.require_transitions}"
        )

    print("[summary] transitions=", transitions, "saw_play=", saw_play)

    if failures:
        print("[result] FAIL")
        for issue in failures:
            print(f" - {issue}")
        return 1

    print("[result] PASS")
    return 0


def main() -> int:
    args = parse_args()
    try:
        return run_smoke(args)
    except Exception as exc:
        print(f"[error] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())

