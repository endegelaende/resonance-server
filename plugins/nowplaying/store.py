"""Persistenz-Layer fuer das Now Playing Plugin.

JSON-backed play history with atomic writes and configurable
maximum entry count.  Tracks total play count across restarts
even when old entries are trimmed.

Storage format (``history.json``)::

    {
        "total": 42,
        "updated": "2026-02-14T18:30:00Z",
        "entries": [
            {
                "player_id": "aa:bb:cc:dd:ee:ff",
                "timestamp": "2026-02-14T18:30:00Z",
                "play_number": 41
            },
            ...
        ]
    }
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PlayHistory:
    """JSON-backed Play-History mit atomarem Write.

    Usage::

        store = PlayHistory(Path("data/plugins/nowplaying"))
        store.load()
        entry = store.record("aa:bb:cc:dd:ee:ff")
        print(store.total, store.count)
    """

    def __init__(self, data_dir: Path, *, max_entries: int = 500) -> None:
        self._file = data_dir / "history.json"
        self._max = max_entries
        self._entries: list[dict[str, Any]] = []
        self._total: int = 0

    # ── Properties ─────────────────────────────────────────────

    @property
    def total(self) -> int:
        """Gesamtzahl aller jemals gezaehlten Tracks."""
        return self._total

    @property
    def entries(self) -> list[dict[str, Any]]:
        """Gespeicherte History-Eintraege (neueste am Ende)."""
        return self._entries

    @property
    def count(self) -> int:
        """Anzahl gespeicherter Eintraege."""
        return len(self._entries)

    # ── Load / Save ────────────────────────────────────────────

    def load(self) -> None:
        """Lade History aus JSON-Datei.  Startet leer wenn Datei fehlt."""
        if not self._file.is_file():
            logger.info("No history file at %s — starting fresh", self._file)
            return

        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._entries = data.get("entries", [])
            self._total = data.get("total", len(self._entries))
            logger.info(
                "Loaded %d history entries (total: %d)",
                len(self._entries),
                self._total,
            )
        except Exception as exc:
            logger.error("Failed to load history: %s", exc)
            self._entries = []
            self._total = 0

    def save(self) -> None:
        """Speichere History atomar (write-to-tmp → rename)."""
        self._file.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "total": self._total,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "entries": self._entries,
        }

        tmp = self._file.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._file)
        except Exception as exc:
            logger.error("Failed to save history: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # ── Mutations ──────────────────────────────────────────────

    def record(self, player_id: str) -> dict[str, Any]:
        """Neuen Track-Play aufzeichnen.  Gibt den Eintrag zurueck."""
        self._total += 1

        entry: dict[str, Any] = {
            "player_id": player_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "play_number": self._total,
        }
        self._entries.append(entry)

        # Alte Eintraege entfernen
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max :]

        self.save()
        return entry

    def clear(self) -> None:
        """History leeren."""
        self._entries.clear()
        self._total = 0
        self.save()
