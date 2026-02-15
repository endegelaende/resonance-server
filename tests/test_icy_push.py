"""
Tests for ICY title push notifications (Cometd/Eventing).

Verifies that ICY StreamTitle changes trigger push updates to connected
clients instead of relying solely on polling.

LMS reference: ``Slim::Music::Info::setCurrentTitle()`` (Info.pm L511-555)
fires ``['playlist', 'newsong', $title]`` when the ICY title changes,
which triggers Cometd subscription re-execution so hardware players
update Now Playing immediately.

Resonance equivalent:
1. ``StreamingServer.set_icy_title()`` returns ``True`` only when the
   title actually changed (change detection, mirrors LMS L516 check).
2. ``_log_icy_metadata()`` fires ``PlayerPlaylistEvent(action="newmetadata")``
   on change, which the CometdManager handles via debounced re-execution.
3. The CLI server maps ``action="newmetadata"`` to ``playlist newsong``.

Test structure:
- TestSetIcyTitleChangeDetection (6): change detection in StreamingServer
- TestLogIcyMetadataEventFiring (7): event firing in _log_icy_metadata
- TestCometdReexecOnNewmetadata (4): CometdManager handles the event
- TestCliNewmetadataMapping (3): CLI notification format
- TestIcyPushEndToEnd (3): full flow from ICY block to event
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resonance.core.events import (
    EventBus,
    PlayerPlaylistEvent,
    event_bus,
)
from resonance.streaming.server import StreamingServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_streaming_server() -> StreamingServer:
    """Create a minimal StreamingServer for testing ICY title storage."""
    # StreamingServer.__init__ requires certain attributes; we use a
    # lightweight approach by constructing and setting internal state.
    server = object.__new__(StreamingServer)
    server._icy_titles = {}
    return server


def _make_icy_metadata_block(title: str) -> bytes:
    """Build an ICY metadata bytes block like ``StreamTitle='...';``."""
    text = f"StreamTitle='{title}';"
    encoded = text.encode("utf-8")
    # Pad with null bytes to fill a 16-byte-aligned block
    padded_len = ((len(encoded) + 15) // 16) * 16
    return encoded.ljust(padded_len, b"\x00")


# =============================================================================
# StreamingServer.set_icy_title() change detection
# =============================================================================


class TestSetIcyTitleChangeDetection:
    """Verify that ``set_icy_title()`` only reports a change when the title
    actually differs from the previously stored value.

    LMS reference: ``setCurrentTitle()`` (Info.pm L516):
        ``if (getCurrentTitle($client, $url) ne ($title || ''))``
    """

    def test_first_title_returns_true(self) -> None:
        """First title for a player is always a change."""
        server = _make_streaming_server()
        assert server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song A") is True

    def test_same_title_returns_false(self) -> None:
        """Repeating the same title is not a change."""
        server = _make_streaming_server()
        server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song A")
        assert server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song A") is False

    def test_different_title_returns_true(self) -> None:
        """A new title is a change."""
        server = _make_streaming_server()
        server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song A")
        assert server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song B") is True

    def test_title_stored_on_change(self) -> None:
        """After a change, the new title is retrievable."""
        server = _make_streaming_server()
        server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song A")
        server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song B")
        assert server.get_icy_title("aa:bb:cc:dd:ee:ff") == "Song B"

    def test_per_player_isolation(self) -> None:
        """Different players have independent change detection."""
        server = _make_streaming_server()
        server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song A")
        # Same title on a different player is a new change for that player
        assert server.set_icy_title("11:22:33:44:55:66", "Song A") is True

    def test_clear_then_set_is_change(self) -> None:
        """After clearing, setting the same title again is a change."""
        server = _make_streaming_server()
        server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song A")
        server.clear_icy_title("aa:bb:cc:dd:ee:ff")
        assert server.set_icy_title("aa:bb:cc:dd:ee:ff", "Song A") is True


# =============================================================================
# _log_icy_metadata() event firing
# =============================================================================


class TestLogIcyMetadataEventFiring:
    """Verify that ``_log_icy_metadata()`` fires a ``PlayerPlaylistEvent``
    with ``action="newmetadata"`` when the ICY title changes."""

    def test_event_fired_on_title_change(self) -> None:
        """A new title fires a PlayerPlaylistEvent."""
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        mock_server.set_icy_title = MagicMock(return_value=True)
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            with patch.object(event_bus, "publish_sync") as mock_publish:
                meta = _make_icy_metadata_block("Coltrane - Giant Steps")
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

                mock_publish.assert_called_once()
                event = mock_publish.call_args[0][0]
                assert isinstance(event, PlayerPlaylistEvent)
                assert event.action == "newmetadata"
                assert event.player_id == "aa:bb:cc:dd:ee:ff"
        finally:
            streaming_mod._streaming_server = saved

    def test_no_event_on_same_title(self) -> None:
        """Repeating the same title does NOT fire an event."""
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        mock_server.set_icy_title = MagicMock(return_value=False)
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            with patch.object(event_bus, "publish_sync") as mock_publish:
                meta = _make_icy_metadata_block("Coltrane - Giant Steps")
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

                mock_publish.assert_not_called()
        finally:
            streaming_mod._streaming_server = saved

    def test_no_event_on_empty_title(self) -> None:
        """Empty StreamTitle does not fire an event."""
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            with patch.object(event_bus, "publish_sync") as mock_publish:
                meta = _make_icy_metadata_block("")
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

                mock_publish.assert_not_called()
                mock_server.set_icy_title.assert_not_called()
        finally:
            streaming_mod._streaming_server = saved

    def test_no_crash_without_streaming_server(self) -> None:
        """If _streaming_server is None, no crash and no event."""
        from resonance.web.routes import streaming as streaming_mod

        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = None

        try:
            with patch.object(event_bus, "publish_sync") as mock_publish:
                meta = _make_icy_metadata_block("Song Title")
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")
                mock_publish.assert_not_called()
        finally:
            streaming_mod._streaming_server = saved

    def test_event_not_fired_on_null_bytes_only(self) -> None:
        """All-null ICY metadata block → no event."""
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            with patch.object(event_bus, "publish_sync") as mock_publish:
                meta = b"\x00" * 32
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")
                mock_publish.assert_not_called()
        finally:
            streaming_mod._streaming_server = saved

    def test_unicode_title_fires_event(self) -> None:
        """Unicode ICY titles fire events correctly."""
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        mock_server.set_icy_title = MagicMock(return_value=True)
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            with patch.object(event_bus, "publish_sync") as mock_publish:
                meta = "StreamTitle='Ärzte - Schrei nach Liebe';".encode("utf-8") + b"\x00"
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

                mock_publish.assert_called_once()
                event = mock_publish.call_args[0][0]
                assert event.player_id == "aa:bb:cc:dd:ee:ff"
                assert event.action == "newmetadata"
        finally:
            streaming_mod._streaming_server = saved

    def test_set_icy_title_called_with_stripped_title(self) -> None:
        """The title passed to set_icy_title is the stripped StreamTitle value."""
        from resonance.web.routes import streaming as streaming_mod

        mock_server = MagicMock()
        mock_server.set_icy_title = MagicMock(return_value=True)
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = mock_server

        try:
            with patch.object(event_bus, "publish_sync"):
                meta = _make_icy_metadata_block("Miles Davis - So What")
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

                mock_server.set_icy_title.assert_called_once_with(
                    "aa:bb:cc:dd:ee:ff", "Miles Davis - So What"
                )
        finally:
            streaming_mod._streaming_server = saved


# =============================================================================
# CometdManager handles newmetadata events
# =============================================================================


class TestCometdReexecOnNewmetadata:
    """Verify that CometdManager schedules re-execution for newmetadata events."""

    @pytest.mark.asyncio
    async def test_newmetadata_triggers_reexec(self) -> None:
        """PlayerPlaylistEvent(action='newmetadata') schedules debounced re-execution."""
        from resonance.web.cometd import CometdManager

        manager = CometdManager()
        manager._schedule_debounced_reexec = MagicMock()

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="newmetadata",
            count=0,
        )
        await manager.handle_event(event)

        manager._schedule_debounced_reexec.assert_called_once()
        call_args = manager._schedule_debounced_reexec.call_args
        assert call_args[0][0] == "aa:bb:cc:dd:ee:ff"

    @pytest.mark.asyncio
    async def test_newmetadata_uses_default_debounce(self) -> None:
        """newmetadata uses the default debounce delay (0.3s), not the jump delay."""
        from resonance.web.cometd import (
            REEXEC_DEBOUNCE_DEFAULT,
            CometdManager,
        )

        manager = CometdManager()
        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="newmetadata",
            count=0,
        )
        delay = manager._get_reexec_delay(event)
        assert delay == REEXEC_DEBOUNCE_DEFAULT

    @pytest.mark.asyncio
    async def test_index_uses_jump_debounce(self) -> None:
        """Sanity check: action='index' uses the longer jump debounce."""
        from resonance.web.cometd import (
            REEXEC_DEBOUNCE_JUMP,
            CometdManager,
        )

        manager = CometdManager()
        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="index",
            count=0,
        )
        delay = manager._get_reexec_delay(event)
        assert delay == REEXEC_DEBOUNCE_JUMP

    @pytest.mark.asyncio
    async def test_newmetadata_does_not_deliver_raw_event(self) -> None:
        """Unlike PlayerStatusEvent, playlist events don't push raw events
        on a legacy channel — only debounced re-execution."""
        from resonance.web.cometd import CometdManager

        manager = CometdManager()
        manager._schedule_debounced_reexec = MagicMock()
        manager.deliver_event = AsyncMock()

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="newmetadata",
            count=0,
        )
        await manager.handle_event(event)

        # deliver_event should NOT be called (that's for PlayerStatusEvent)
        manager.deliver_event.assert_not_called()


# =============================================================================
# CLI notification mapping
# =============================================================================


class TestCliNewmetadataMapping:
    """Verify that the CLI server maps newmetadata to 'playlist newsong'."""

    def test_newmetadata_produces_playlist_newsong(self) -> None:
        """action='newmetadata' → 'playlist newsong' CLI notification."""
        from resonance.protocol.cli import CliServer

        server = object.__new__(CliServer)
        server._last_notified = {}

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="newmetadata",
            count=0,
        )
        results = server._event_to_notifications(event)

        assert len(results) == 1
        prefix, notification = results[0]
        assert prefix == "playlist"
        assert "playlist" in notification
        assert "newsong" in notification
        assert "aa:bb:cc:dd:ee:ff" in notification

    def test_index_produces_playlist_newsong_with_index(self) -> None:
        """Sanity: action='index' also produces 'playlist newsong'."""
        from resonance.protocol.cli import CliServer

        server = object.__new__(CliServer)
        server._last_notified = {}

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="index",
            index=3,
            count=10,
        )
        results = server._event_to_notifications(event)

        assert len(results) == 1
        prefix, notification = results[0]
        assert prefix == "playlist"
        assert "newsong" in notification
        assert "3" in notification

    def test_newmetadata_no_index_in_notification(self) -> None:
        """newmetadata notification does not include a playlist index
        (unlike 'index' action which includes the track number)."""
        from resonance.protocol.cli import CliServer

        server = object.__new__(CliServer)
        server._last_notified = {}

        event = PlayerPlaylistEvent(
            player_id="aa:bb:cc:dd:ee:ff",
            action="newmetadata",
            index=0,
            count=0,
        )
        results = server._event_to_notifications(event)

        _, notification = results[0]
        # Should be "aa:bb:cc:dd:ee:ff playlist newsong" without an index
        parts = notification.split()
        assert parts == ["aa:bb:cc:dd:ee:ff", "playlist", "newsong"]


# =============================================================================
# End-to-end: ICY block → change detection → event
# =============================================================================


class TestIcyPushEndToEnd:
    """Integration tests for the full ICY push flow using a real
    StreamingServer (not mocked) and patched event_bus."""

    def test_first_title_fires_event(self) -> None:
        """First ICY title → set_icy_title returns True → event fired."""
        from resonance.web.routes import streaming as streaming_mod

        server = _make_streaming_server()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = server

        try:
            with patch.object(event_bus, "publish_sync") as mock_publish:
                meta = _make_icy_metadata_block("Miles Davis - So What")
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

                mock_publish.assert_called_once()
                assert server.get_icy_title("aa:bb:cc:dd:ee:ff") == "Miles Davis - So What"
        finally:
            streaming_mod._streaming_server = saved

    def test_repeated_title_no_event(self) -> None:
        """Same ICY title repeated → set_icy_title returns False → no event."""
        from resonance.web.routes import streaming as streaming_mod

        server = _make_streaming_server()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = server

        try:
            # First call — fires event
            meta = _make_icy_metadata_block("Miles Davis - So What")
            streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")

            with patch.object(event_bus, "publish_sync") as mock_publish:
                # Second call with same title — no event
                streaming_mod._log_icy_metadata(meta, "aa:bb:cc:dd:ee:ff")
                mock_publish.assert_not_called()
        finally:
            streaming_mod._streaming_server = saved

    def test_title_change_fires_second_event(self) -> None:
        """Different ICY title → fires a new event."""
        from resonance.web.routes import streaming as streaming_mod

        server = _make_streaming_server()
        saved = streaming_mod._streaming_server
        streaming_mod._streaming_server = server

        try:
            # First title
            meta1 = _make_icy_metadata_block("Miles Davis - So What")
            streaming_mod._log_icy_metadata(meta1, "aa:bb:cc:dd:ee:ff")

            with patch.object(event_bus, "publish_sync") as mock_publish:
                # New title
                meta2 = _make_icy_metadata_block("Coltrane - Giant Steps")
                streaming_mod._log_icy_metadata(meta2, "aa:bb:cc:dd:ee:ff")

                mock_publish.assert_called_once()
                event = mock_publish.call_args[0][0]
                assert isinstance(event, PlayerPlaylistEvent)
                assert event.action == "newmetadata"
                assert server.get_icy_title("aa:bb:cc:dd:ee:ff") == "Coltrane - Giant Steps"
        finally:
            streaming_mod._streaming_server = saved
