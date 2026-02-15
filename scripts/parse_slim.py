#!/usr/bin/env python3
"""Parse Slimproto traffic from tshark TSV output.

Usage:
    1. Export with tshark:
       tshark -r <capture.pcapng> -Y "tcp.port == 3483 && tcp.len > 0" \
              -T fields -e frame.number -e frame.time_relative -e ip.src -e tcp.payload \
              > docs/slim_raw_capture.txt
    2. Run:
       python docs/parse_slim.py docs/slim_raw_capture.txt
"""

import struct
import sys
from pathlib import Path

SERVER_IP = "192.168.1.30"


def hex_to_bytes(h: str) -> bytes:
    return bytes.fromhex(h.strip())


def parse_stat(data: bytes) -> dict:
    """Parse a STAT message body (after the 4-byte 'STAT' opcode and 4-byte length)."""
    if len(data) < 43:
        return {"event": "???", "raw_len": len(data)}

    event = data[0:4].decode("ascii", errors="replace")
    # byte 4: num_crlf
    # byte 5: mas_initialized
    # byte 6: mas_mode
    buf_size = struct.unpack(">I", data[7:11])[0]
    fullness = struct.unpack(">I", data[11:15])[0]
    bytes_recv = struct.unpack(">Q", data[15:23])[0]
    sig_strength = struct.unpack(">H", data[23:25])[0]
    jiffies = struct.unpack(">I", data[25:29])[0]
    out_size = struct.unpack(">I", data[29:33])[0]
    out_full = struct.unpack(">I", data[33:37])[0]
    elapsed_s = struct.unpack(">I", data[37:41])[0]
    # voltage is uint16 (2 bytes) per LMS Slim::Networking::Slimproto.pm
    # pack format: 'a4CCCNNNNnNNNNnNNn' — the 'n' after elapsed is uint16.
    # Previously this was parsed as uint32 (4 bytes), which shifted
    # elapsed_ms to the wrong offset and produced garbled values.
    voltage = struct.unpack(">H", data[41:43])[0]
    elapsed_ms = struct.unpack(">I", data[43:47])[0] if len(data) >= 47 else 0

    return {
        "event": event,
        "buf_size": buf_size,
        "fullness": fullness,
        "bytes_recv": bytes_recv,
        "out_size": out_size,
        "out_full": out_full,
        "elapsed_s": elapsed_s,
        "elapsed_ms": elapsed_ms,
        "jiffies": jiffies,
    }


def format_stat(s: dict) -> str:
    ev = s["event"]
    buf_pct = (s["fullness"] * 100 // s["buf_size"]) if s.get("buf_size") else 0
    out_pct = (s["out_full"] * 100 // s["out_size"]) if s.get("out_size") else 0
    recv_kb = s.get("bytes_recv", 0) / 1024

    label = {
        "STMs": "STARTED",
        "STMd": "DECODE_READY",
        "STMu": "UNDERRUN(track_end)",
        "STMf": "FLUSHED",
        "STMp": "PAUSED",
        "STMr": "RESUMED",
        "STMt": "heartbeat",
        "STMc": "CONNECTED",
        "STMn": "NOT_SUPPORTED",
        "STMl": "BUFFER_READY",
        "vers": "vers_ack",
        "setd": "setd_ack",
        "aude": "aude_ack",
        "audg": "audg_ack",
    }.get(ev, ev)

    return (
        f"STAT/{ev} ({label})  "
        f"buf={buf_pct}%  out={out_pct}%  "
        f"recv={recv_kb:.0f}KB  "
        f"elapsed={s['elapsed_s']}s/{s['elapsed_ms']}ms"
    )


def parse_server_commands(data: bytes) -> list[str]:
    """Parse concatenated server->player commands (length-prefixed)."""
    results = []
    pos = 0
    while pos + 2 < len(data):
        length = struct.unpack(">H", data[pos : pos + 2])[0]
        if length == 0 or pos + 2 + length > len(data):
            break
        chunk = data[pos + 2 : pos + 2 + length]
        pos += 2 + length

        if len(chunk) < 4:
            results.append(f"unknown({length}b)")
            continue

        cmd = chunk[0:4].decode("ascii", errors="replace")

        if cmd == "strm":
            results.append(parse_strm(chunk))
        elif cmd == "audg":
            # audg: gain values
            if len(chunk) >= 18:
                gain_l = struct.unpack(">I", chunk[4:8])[0]
                gain_r = struct.unpack(">I", chunk[8:12])[0]
                seq = chunk[17] if len(chunk) > 17 else 0
                results.append(f"audg  gainL={gain_l} gainR={gain_r} seq={seq}")
            else:
                results.append("audg")
        elif cmd == "aude":
            spdif = chunk[4] if len(chunk) > 4 else "?"
            dac = chunk[5] if len(chunk) > 5 else "?"
            results.append(f"aude  spdif={spdif} dac={dac}")
        elif cmd == "vers":
            ver = chunk[4:].decode("ascii", errors="replace")
            results.append(f"vers  {ver}")
        elif cmd == "setd":
            setd_id = chunk[4] if len(chunk) > 4 else "?"
            results.append(f"setd  id={setd_id}")
        elif cmd == "grfe" or cmd == "grfb" or cmd == "grfd":
            results.append(f"{cmd}  ({length}b)")
        else:
            results.append(f"{cmd}  ({length}b)")

    return results


def parse_strm(chunk: bytes) -> str:
    """Parse a strm command."""
    if len(chunk) < 24:
        return f"strm  ({len(chunk)}b, too short)"

    subcmd = chr(chunk[4])
    autostart = chr(chunk[5])
    fmt_byte = chr(chunk[6])
    pcm_sample_size = chunk[7]
    pcm_sample_rate = chunk[8]
    pcm_channels = chunk[9]
    pcm_endian = chunk[10]
    threshold = chunk[11]
    spdif_enable = chunk[12]
    trans_period = chunk[13]
    trans_type = chunk[14]
    flags = chunk[15]
    output_threshold = chunk[16]
    # bytes 17: reserved
    replay_gain = struct.unpack(">I", chunk[18:22])[0]
    server_port = struct.unpack(">H", chunk[22:24])[0]

    if len(chunk) >= 28:
        server_ip_bytes = chunk[24:28]
        server_ip = ".".join(str(b) for b in server_ip_bytes)
    else:
        server_ip = "?"

    subcmd_name = {
        "s": "START",
        "q": "STOP",
        "f": "FLUSH",
        "p": "PAUSE",
        "u": "UNPAUSE",
        "t": "STATUS",
        "a": "SKIP_AHEAD",
    }.get(subcmd, subcmd)

    fmt_name = {
        "m": "mp3",
        "f": "flac",
        "w": "wav/pcm",
        "o": "ogg",
        "a": "aac",
        "l": "alac",
        "p": "pcm",
        "?": "?",
    }.get(fmt_byte, fmt_byte)

    result = f"strm/{subcmd} ({subcmd_name})  fmt={fmt_name}  auto={autostart}"

    if replay_gain:
        result += f"  replayGain=0x{replay_gain:04x}"

    if subcmd == "s":
        # Stream start - extract HTTP request
        http_start = chunk.find(b"GET ")
        if http_start > 0:
            http_end = chunk.find(b"\r\n", http_start)
            if http_end < 0:
                http_end = len(chunk)
            http_line = chunk[http_start:http_end].decode("ascii", errors="replace")
            result += f"  [{http_line}]"
        result += f"  port={server_port} ip={server_ip}"

    if subcmd == "q" or subcmd == "f":
        if replay_gain:
            result += f"  (with replay_gain)"

    return result


def parse_player_messages(data: bytes) -> list[str]:
    """Parse concatenated player->server messages."""
    results = []
    pos = 0
    while pos + 8 <= len(data):
        opcode = data[pos : pos + 4].decode("ascii", errors="replace")
        length = struct.unpack(">I", data[pos + 4 : pos + 8])[0]

        if pos + 8 + length > len(data):
            # Might be partial
            results.append(f"{opcode}  (truncated, need {length}b, have {len(data) - pos - 8}b)")
            break

        body = data[pos + 8 : pos + 8 + length]
        pos += 8 + length

        if opcode == "STAT":
            s = parse_stat(body)
            results.append(format_stat(s))
        elif opcode == "HELO":
            dev_id = body[0] if len(body) > 0 else "?"
            rev = body[1] if len(body) > 1 else "?"
            mac = ":".join(f"{b:02x}" for b in body[2:8]) if len(body) >= 8 else "?"
            # Find capabilities string
            cap_start = body.find(b"\x00", 36)
            if cap_start >= 0 and cap_start + 1 < len(body):
                caps = body[cap_start + 1 :].decode("ascii", errors="replace")
            else:
                caps = body[36:].decode("ascii", errors="replace") if len(body) > 36 else ""
            results.append(f"HELO  mac={mac}  caps={caps[:120]}...")
        elif opcode == "RESP":
            http_resp = body.decode("ascii", errors="replace")
            # Just show first line
            first_line = http_resp.split("\r\n")[0] if "\r\n" in http_resp else http_resp[:80]
            results.append(f"RESP  {first_line}")
        elif opcode == "DSCO":
            reason = body[0] if len(body) > 0 else "?"
            reason_name = {0: "TCP_close", 1: "TCP_reset", 2: "timeout", 3: "unreachable"}.get(
                reason, f"unknown({reason})"
            )
            results.append(f"DSCO  reason={reason_name}")
        elif opcode == "SETD":
            setd_id = body[0] if len(body) > 0 else "?"
            value = body[1:].decode("ascii", errors="replace") if len(body) > 1 else ""
            results.append(f"SETD  id={setd_id} value={value!r}")
        elif opcode == "ANIC":
            results.append(f"ANIC")
        elif opcode == "META":
            results.append(f"META  {body.decode('ascii', errors='replace')[:80]}")
        elif opcode == "BUTN":
            results.append(f"BUTN  ({length}b)")
        elif opcode == "IR  ":
            results.append(f"IR  ({length}b)")
        else:
            results.append(f"{opcode}  ({length}b)")

    return results


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <tshark_tsv.txt>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"File not found: {input_path}")
        sys.exit(1)

    track_num = 0
    last_event = None

    with open(input_path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            parts = line.split("\t")
            if len(parts) < 4:
                continue

            frame = parts[0].strip()
            time_str = parts[1].strip()
            src = parts[2].strip()
            payload_hex = parts[3].strip()

            try:
                time_f = float(time_str)
            except ValueError:
                continue

            try:
                data = hex_to_bytes(payload_hex)
            except ValueError:
                continue

            is_server = src == SERVER_IP
            direction = "S->P" if is_server else "P->S"
            arrow = ">>>" if is_server else "<<<"

            if is_server:
                messages = parse_server_commands(data)
            else:
                messages = parse_player_messages(data)

            for msg in messages:
                # Track numbering on STMs
                prefix = ""
                if "STMs" in msg and "STARTED" in msg:
                    track_num += 1
                    prefix = f"\n{'='*80}\n  *** TRACK {track_num} STARTED ***\n{'='*80}\n"
                elif "STMu" in msg and "UNDERRUN" in msg:
                    prefix = f"\n  --- TRACK {track_num} ENDED (underrun) ---\n"
                elif "STMd" in msg and "DECODE_READY" in msg:
                    prefix = f"  +++ DECODE COMPLETE (ready for next) +++\n"
                elif "strm/s" in msg and "START" in msg:
                    prefix = f"\n  >>> NEW STREAM COMMAND >>>\n"
                elif "strm/q" in msg and "STOP" in msg:
                    prefix = f"\n  <<< STOP/FLUSH COMMAND <<<\n"

                if prefix:
                    print(prefix, end="")

                print(f"  {time_f:7.1f}s  [{direction}] {arrow}  {msg}")

    print(f"\n{'='*80}")
    print(f"  Total tracks started: {track_num}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
