"""
JSON-RPC Command Handlers Package.

This package contains the handler modules for JSON-RPC commands.
Each module handles a specific category of commands.

Modules:
- library: artists, albums, titles, genres, roles, search
- playback: play, pause, stop, mode, power, mixer, button
- playlist: playlist command (play, add, clear, index, shuffle, etc.)
- seeking: time, perform_seek, calculate_byte_offset
- status: serverstatus, players, player, status, pref, rescan, wipecache
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resonance.core.artwork import ArtworkManager
    from resonance.core.library import MusicLibrary
    from resonance.core.playlist import PlaylistManager
    from resonance.player.registry import PlayerRegistry
    from resonance.plugin_installer import PluginInstaller
    from resonance.plugin_manager import PluginManager
    from resonance.plugin_repository import PluginRepository
    from resonance.protocol.slimproto import SlimprotoServer
    from resonance.streaming.server import StreamingServer


@dataclass
class CommandContext:
    """
    Context object passed to all command handlers.

    Contains references to all server components needed to process commands.
    This enables handlers to be stateless functions that receive all
    dependencies via this context.
    """

    player_id: str
    """The player MAC address (or '-' for server-level commands)."""

    music_library: MusicLibrary
    """The music library for browsing and searching."""

    player_registry: PlayerRegistry
    """Registry of connected players."""

    playlist_manager: PlaylistManager | None = None
    """Per-player playlist management."""

    streaming_server: StreamingServer | None = None
    """Streaming server for queueing files."""

    slimproto: SlimprotoServer | None = None
    """Slimproto server for player control commands."""

    artwork_manager: ArtworkManager | None = None
    """Artwork extraction and caching."""

    plugin_manager: PluginManager | None = None
    """Plugin manager used by pluginsettings/pluginmanager commands."""

    plugin_installer: PluginInstaller | None = None
    """Plugin installer for install/uninstall operations."""

    plugin_repository: PluginRepository | None = None
    """Plugin repository client for available plugin index."""

    server_host: str = "127.0.0.1"
    """Server hostname for generating URLs."""

    server_port: int = 9000
    """Server port for generating URLs."""

    server_uuid: str = "resonance"
    """Server UUID for identification (full UUID v4, 36 chars with dashes)."""

    def __post_init__(self) -> None:
        if self.server_host == "0.0.0.0":
            # Detect the actual LAN IP instead of using 127.0.0.1
            # This is critical for Squeezebox devices - they use this IP
            # to fetch resources like cover art and menu icons
            self.server_host = self._detect_lan_ip()

    @staticmethod
    def _detect_lan_ip() -> str:
        """
        Detect the primary LAN IP address of this machine.

        Uses the UDP socket trick: connect to a public DNS server
        (no packet is actually sent) to determine which local
        interface would be used for outbound traffic.

        This is the same method used by the Discovery server.
        """
        try:
            # Connect to a public DNS server to determine local interface
            # No actual packet is sent - we just need the routing decision
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            # Fallback to localhost if detection fails
            return "127.0.0.1"

    def get_player_url(self, path: str) -> str:
        """Generate a URL for player-accessible resources."""
        return f"http://{self.server_host}:{self.server_port}{path}"


__all__ = ["CommandContext"]
