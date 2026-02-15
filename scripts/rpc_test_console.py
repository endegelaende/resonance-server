#!/usr/bin/env python3
"""Interactive JSON-RPC test console for Resonance (CLI + GUI).

Goals:
- send playback commands quickly (play/pause/stop/next/prev/seek/volume)
- run simple handoff test scenarios from CLI
- show exact JSON sent and JSON received for each RPC

Examples:
  python scripts/rpc_test_console.py cli --base-url http://127.0.0.1:9000
  python scripts/rpc_test_console.py gui --base-url http://127.0.0.1:9000
  python scripts/rpc_test_console.py scenario-handoff --album-id 3 --seconds 35 --require-transitions 2
"""

from __future__ import annotations

import argparse
import itertools
import json
import shlex
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

TraceFn = Callable[[str], None]


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))


def _json_pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True)


class JsonRpcTraceClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        trace: TraceFn | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.trace = trace
        self._id_counter = itertools.count(1)

    def request(self, player_id: str, command: list[str]) -> dict[str, Any]:
        payload = {
            "id": next(self._id_counter),
            "method": "slim.request",
            "params": [player_id, command],
        }
        body_bytes = _json_dumps(payload).encode("utf-8")

        if self.trace is not None:
            self.trace(f">> {_json_dumps(payload)}")

        req = urllib.request.Request(
            url=f"{self.base_url}/jsonrpc.js",
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.URLError as exc:
            raise RuntimeError(f"request failed: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid JSON response: {exc}") from exc

        if self.trace is not None:
            self.trace(f"<< {_json_dumps(data)}")

        if "error" in data and data["error"] is not None:
            raise RuntimeError(f"rpc error: {data['error']}")

        result = data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"unexpected result shape: {result!r}")
        return result


def pick_player(result: dict[str, Any], requested: str | None) -> dict[str, Any]:
    players = result.get("players_loop")
    if not isinstance(players, list) or not players:
        raise RuntimeError("no players in serverstatus players_loop")

    if requested:
        needle = requested.lower()
        for p in players:
            pid = str(p.get("playerid", ""))
            if pid.lower() == needle:
                return p
        raise RuntimeError(f"player not found: {requested}")

    connected_audio = [
        p for p in players if int(p.get("connected", 0) or 0) == 1 and int(p.get("isplayer", 1) or 0) == 1
    ]
    if connected_audio:
        return connected_audio[0]

    connected_any = [p for p in players if int(p.get("connected", 0) or 0) == 1]
    if connected_any:
        return connected_any[0]

    return players[0]


def status_sample(client: JsonRpcTraceClient, player_id: str) -> dict[str, Any]:
    res = client.request(player_id, ["status", "-", "1", "tags:aAdlKkt"])
    mode = str(res.get("mode", "stop") or "stop")
    idx = res.get("playlist index")
    if idx is None:
        idx = res.get("playlist_cur_index")
    track_id = res.get("track_id")
    current_track = res.get("currentTrack") if isinstance(res.get("currentTrack"), dict) else {}
    title = str(current_track.get("title", "") or "")
    gen = res.get("stream_generation")
    return {
        "mode": mode,
        "idx": idx,
        "track_id": track_id,
        "title": title,
        "time": float(res.get("time", 0.0) or 0.0),
        "duration": float(res.get("duration", 0.0) or 0.0),
        "gen": gen,
    }


def run_handoff_scenario(
    client: JsonRpcTraceClient,
    player_id: str,
    *,
    album_id: int | None,
    seconds: float,
    poll_interval: float,
    require_transitions: int,
    strict_index_increment: bool,
    duration_tolerance: float,
) -> int:
    if album_id is not None:
        client.request(player_id, ["playlist", "loadtracks", f"album_id:{album_id}"])
        print(f"[scenario] started album_id:{album_id}")

    end_at = time.monotonic() + max(1.0, seconds)
    last: dict[str, Any] | None = None
    transitions = 0
    failures: list[str] = []
    sample_no = 0

    while time.monotonic() < end_at:
        sample_no += 1
        sample = status_sample(client, player_id)
        print(
            f"[{sample_no:03d}] mode={sample['mode']:<5} idx={sample['idx']!s:<3} gen={sample['gen']!s:<4} "
            f"track={sample['track_id']!s:<6} time={sample['time']:7.3f}/{sample['duration']:7.3f} title={sample['title']}"
        )

        if sample["duration"] > 0 and sample["time"] > sample["duration"] + duration_tolerance:
            failures.append(
                f"sample {sample_no}: time exceeds duration ({sample['time']:.3f} > {sample['duration']:.3f})"
            )

        if last is not None:
            if sample["track_id"] is not None and last["track_id"] is not None and sample["track_id"] != last["track_id"]:
                transitions += 1
                if strict_index_increment:
                    if sample["idx"] is None or last["idx"] is None or int(sample["idx"]) != int(last["idx"]) + 1:
                        failures.append(
                            f"sample {sample_no}: track change without index+1 ({last['idx']} -> {sample['idx']})"
                        )

        if failures:
            break

        last = sample
        time.sleep(max(0.05, poll_interval))

    if transitions < max(0, require_transitions):
        failures.append(f"observed transitions={transitions}, required={require_transitions}")

    print(f"[scenario] transitions={transitions}")
    if failures:
        print("[scenario] FAIL")
        for item in failures:
            print(f" - {item}")
        return 1

    print("[scenario] PASS")
    return 0


def print_help() -> None:
    print(
        """
Commands:
  help
  players
  use <player-id-or-index>
  serverstatus
  status
  play | pause | stop | next | prev
  seek <seconds>
  volume <0-100>
  mute
  loadalbum <album-id>
  raw <json-array-command>    e.g. raw ["playlist","index","+1"]
  scenario handoff [album_id] [seconds]
  quit | exit
""".strip()
    )


def run_cli(args: argparse.Namespace) -> int:
    def trace(msg: str) -> None:
        print(msg)

    client = JsonRpcTraceClient(args.base_url, timeout_seconds=args.timeout, trace=trace)

    server = client.request("-", ["serverstatus", "0", "100"])
    current_player = pick_player(server, args.player_id)
    current_player_id = str(current_player.get("playerid", ""))

    print(
        f"Connected to {args.base_url} | player={current_player_id} "
        f"name={current_player.get('name', '')} model={current_player.get('model', '')}"
    )
    print_help()

    while True:
        try:
            line = input("rpc> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not line:
            continue

        try:
            parts = shlex.split(line)
        except ValueError as exc:
            print(f"[error] {exc}")
            continue

        cmd = parts[0].lower()

        try:
            if cmd in {"quit", "exit"}:
                return 0

            if cmd == "help":
                print_help()
                continue

            if cmd == "players":
                res = client.request("-", ["serverstatus", "0", "100"])
                players = res.get("players_loop") or []
                for i, p in enumerate(players):
                    marker = "*" if str(p.get("playerid", "")) == current_player_id else " "
                    print(
                        f"{marker} [{i}] id={p.get('playerid')} name={p.get('name')} model={p.get('model')} "
                        f"connected={p.get('connected')} isplayer={p.get('isplayer')}"
                    )
                continue

            if cmd == "use":
                if len(parts) < 2:
                    print("usage: use <player-id-or-index>")
                    continue
                token = parts[1]
                res = client.request("-", ["serverstatus", "0", "100"])
                players = res.get("players_loop") or []
                chosen: dict[str, Any] | None = None
                if token.isdigit():
                    idx = int(token)
                    if 0 <= idx < len(players):
                        chosen = players[idx]
                if chosen is None:
                    for p in players:
                        if str(p.get("playerid", "")).lower() == token.lower():
                            chosen = p
                            break
                if chosen is None:
                    print(f"[error] player not found: {token}")
                    continue
                current_player_id = str(chosen.get("playerid", ""))
                print(f"[ok] using player {current_player_id} ({chosen.get('name', '')})")
                continue

            if cmd == "serverstatus":
                res = client.request("-", ["serverstatus", "0", "100"])
                print(_json_pretty(res))
                continue

            if cmd == "status":
                res = client.request(current_player_id, ["status", "-", "1", "tags:aAdlKkt"])
                print(_json_pretty(res))
                continue

            if cmd == "play":
                print(_json_pretty(client.request(current_player_id, ["play"])))
                continue
            if cmd == "pause":
                print(_json_pretty(client.request(current_player_id, ["pause"])))
                continue
            if cmd == "stop":
                print(_json_pretty(client.request(current_player_id, ["stop"])))
                continue
            if cmd == "next":
                print(_json_pretty(client.request(current_player_id, ["playlist", "jump", "+1"])))
                continue
            if cmd == "prev":
                print(_json_pretty(client.request(current_player_id, ["playlist", "jump", "-1"])))
                continue

            if cmd == "seek":
                if len(parts) < 2:
                    print("usage: seek <seconds>")
                    continue
                print(_json_pretty(client.request(current_player_id, ["time", str(float(parts[1]))])))
                continue

            if cmd == "volume":
                if len(parts) < 2:
                    print("usage: volume <0-100>")
                    continue
                vol = max(0, min(100, int(float(parts[1]))))
                print(_json_pretty(client.request(current_player_id, ["mixer", "volume", str(vol)])))
                continue

            if cmd == "mute":
                print(_json_pretty(client.request(current_player_id, ["mixer", "muting", "toggle"])))
                continue

            if cmd == "loadalbum":
                if len(parts) < 2:
                    print("usage: loadalbum <album-id>")
                    continue
                aid = int(parts[1])
                print(
                    _json_pretty(
                        client.request(current_player_id, ["playlist", "loadtracks", f"album_id:{aid}", "sort:tracknum"])
                    )
                )
                continue

            if cmd == "raw":
                if len(parts) < 2:
                    print("usage: raw <json-array-command>")
                    continue
                raw_text = line[line.lower().find("raw") + 3 :].strip()
                parsed = json.loads(raw_text)
                if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
                    print("[error] raw command must be JSON array of strings")
                    continue
                print(_json_pretty(client.request(current_player_id, parsed)))
                continue

            if cmd == "scenario":
                if len(parts) < 2 or parts[1].lower() != "handoff":
                    print("usage: scenario handoff [album_id] [seconds]")
                    continue
                album_id = int(parts[2]) if len(parts) >= 3 else None
                seconds = float(parts[3]) if len(parts) >= 4 else 35.0
                rc = run_handoff_scenario(
                    client,
                    current_player_id,
                    album_id=album_id,
                    seconds=seconds,
                    poll_interval=1.0,
                    require_transitions=2,
                    strict_index_increment=True,
                    duration_tolerance=0.5,
                )
                print(f"[scenario] exit_code={rc}")
                continue

            print(f"unknown command: {cmd}")

        except Exception as exc:
            print(f"[error] {exc}")


def run_gui(args: argparse.Namespace) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
    except Exception as exc:
        print(f"[error] tkinter unavailable: {exc}")
        return 2

    class App:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title("Resonance RPC Test Console")
            self.root.geometry("1120x760")

            self.base_url_var = tk.StringVar(value=args.base_url)
            self.seek_var = tk.StringVar(value="10")
            self.volume_var = tk.StringVar(value="50")
            self.album_var = tk.StringVar(value="3")
            self.player_var = tk.StringVar(value="")

            self.players: list[dict[str, Any]] = []
            self.player_labels: list[str] = []

            top = ttk.Frame(root)
            top.pack(fill="x", padx=8, pady=8)

            ttk.Label(top, text="Base URL").grid(row=0, column=0, sticky="w")
            ttk.Entry(top, textvariable=self.base_url_var, width=42).grid(row=0, column=1, sticky="we", padx=4)
            ttk.Button(top, text="Refresh Players", command=self.refresh_players).grid(row=0, column=2, padx=4)

            ttk.Label(top, text="Player").grid(row=1, column=0, sticky="w")
            self.player_combo = ttk.Combobox(top, textvariable=self.player_var, state="readonly", width=62)
            self.player_combo.grid(row=1, column=1, columnspan=2, sticky="we", padx=4, pady=(4, 0))

            top.columnconfigure(1, weight=1)

            controls = ttk.Frame(root)
            controls.pack(fill="x", padx=8)

            for i, (label, fn) in enumerate(
                [
                    ("Serverstatus", lambda: self.send("-", ["serverstatus", "0", "100"])),
                    ("Status", lambda: self.send_selected(["status", "-", "1", "tags:aAdlKkt"])),
                    ("Play", lambda: self.send_selected(["play"])),
                    ("Pause", lambda: self.send_selected(["pause"])),
                    ("Stop", lambda: self.send_selected(["stop"])),
                    ("Prev", lambda: self.send_selected(["playlist", "jump", "-1"])),
                    ("Next", lambda: self.send_selected(["playlist", "jump", "+1"])),
                ]
            ):
                ttk.Button(controls, text=label, command=fn).grid(row=0, column=i, padx=2, pady=4)

            ttk.Label(controls, text="Seek(s)").grid(row=1, column=0, sticky="e")
            ttk.Entry(controls, textvariable=self.seek_var, width=8).grid(row=1, column=1, sticky="w")
            ttk.Button(
                controls,
                text="Send Seek",
                command=lambda: self.send_selected(["time", str(float(self.seek_var.get()))]),
            ).grid(row=1, column=2, padx=2)

            ttk.Label(controls, text="Volume").grid(row=1, column=3, sticky="e")
            ttk.Entry(controls, textvariable=self.volume_var, width=8).grid(row=1, column=4, sticky="w")
            ttk.Button(
                controls,
                text="Set Volume",
                command=lambda: self.send_selected(["mixer", "volume", str(max(0, min(100, int(float(self.volume_var.get())))))]),
            ).grid(row=1, column=5, padx=2)
            ttk.Button(
                controls,
                text="Mute",
                command=lambda: self.send_selected(["mixer", "muting", "toggle"]),
            ).grid(row=1, column=6, padx=2)

            ttk.Label(controls, text="Album ID").grid(row=1, column=7, sticky="e")
            ttk.Entry(controls, textvariable=self.album_var, width=8).grid(row=1, column=8, sticky="w")
            ttk.Button(
                controls,
                text="Load Album",
                command=lambda: self.send_selected(["playlist", "loadtracks", f"album_id:{int(self.album_var.get())}", "sort:tracknum"]),
            ).grid(row=1, column=9, padx=2)

            self.log = ScrolledText(root, wrap="none", font=("Consolas", 10))
            self.log.pack(fill="both", expand=True, padx=8, pady=8)

            self.refresh_players()

        def trace(self, message: str) -> None:
            timestamp = time.strftime("%H:%M:%S")
            self.log.insert("end", f"[{timestamp}] {message}\n")
            self.log.see("end")

        def client(self) -> JsonRpcTraceClient:
            return JsonRpcTraceClient(self.base_url_var.get(), timeout_seconds=args.timeout, trace=self.trace)

        def selected_player_id(self) -> str | None:
            label = self.player_var.get()
            if not label:
                return None
            for idx, candidate in enumerate(self.player_labels):
                if candidate == label:
                    return str(self.players[idx].get("playerid", ""))
            return None

        def refresh_players(self) -> None:
            try:
                res = self.client().request("-", ["serverstatus", "0", "100"])
            except Exception as exc:
                self.trace(f"[error] refresh players failed: {exc}")
                return

            players = res.get("players_loop")
            if not isinstance(players, list):
                players = []

            self.players = players
            self.player_labels = [
                f"{p.get('name', '')} | {p.get('playerid', '')} | model={p.get('model', '')}"
                for p in self.players
            ]
            self.player_combo["values"] = self.player_labels
            if self.player_labels and not self.player_var.get():
                self.player_var.set(self.player_labels[0])

            self.trace(f"[info] loaded players: {len(self.players)}")

        def send(self, player_id: str, command: list[str]) -> None:
            try:
                result = self.client().request(player_id, command)
                self.trace(f"== result\n{_json_pretty(result)}")
            except Exception as exc:
                self.trace(f"[error] {exc}")

        def send_selected(self, command: list[str]) -> None:
            pid = self.selected_player_id()
            if not pid:
                self.trace("[error] no player selected")
                return
            self.send(pid, command)

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


def run_scenario_handoff(args: argparse.Namespace) -> int:
    def trace(msg: str) -> None:
        if args.verbose_trace:
            print(msg)

    client = JsonRpcTraceClient(args.base_url, timeout_seconds=args.timeout, trace=trace)
    server = client.request("-", ["serverstatus", "0", "100"])
    player = pick_player(server, args.player_id)
    player_id = str(player.get("playerid", ""))
    print(f"[info] player={player_id} name={player.get('name', '')} model={player.get('model', '')}")

    return run_handoff_scenario(
        client,
        player_id,
        album_id=args.album_id,
        seconds=args.seconds,
        poll_interval=args.poll_interval,
        require_transitions=args.require_transitions,
        strict_index_increment=args.strict_index_increment,
        duration_tolerance=args.duration_tolerance,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resonance interactive RPC test console")
    parser.add_argument("--base-url", default="http://127.0.0.1:9000", help="Resonance base URL")
    parser.add_argument("--timeout", type=float, default=6.0, help="HTTP timeout in seconds")

    sub = parser.add_subparsers(dest="mode", required=True)

    p_cli = sub.add_parser("cli", help="interactive CLI")
    p_cli.add_argument("--player-id", default=None, help="player id/mac to use initially")
    p_cli.set_defaults(func=run_cli)

    p_gui = sub.add_parser("gui", help="Tkinter GUI with playback buttons + trace")
    p_gui.set_defaults(func=run_gui)

    p_scn = sub.add_parser("scenario-handoff", help="run handoff/progress scenario once")
    p_scn.add_argument("--player-id", default=None, help="player id/mac")
    p_scn.add_argument("--album-id", type=int, default=None, help="optional album id to load before polling")
    p_scn.add_argument("--seconds", type=float, default=35.0)
    p_scn.add_argument("--poll-interval", type=float, default=1.0)
    p_scn.add_argument("--require-transitions", type=int, default=2)
    p_scn.add_argument("--strict-index-increment", action="store_true")
    p_scn.add_argument("--duration-tolerance", type=float, default=0.5)
    p_scn.add_argument("--verbose-trace", action="store_true")
    p_scn.set_defaults(func=run_scenario_handoff)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"[error] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
