"""
Configuration management for Resonance.

This module loads and provides access to device capabilities and other
configuration from TOML files.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resonance.player.client import DeviceType

logger = logging.getLogger(__name__)

# Path to the config directory
CONFIG_DIR = Path(__file__).parent


class DeviceTier(Enum):
    """Device capability tiers."""

    LEGACY = "legacy"
    MODERN = "modern"
    FUTURE = "future"
    UNKNOWN = "unknown"


@dataclass
class DeviceCapabilities:
    """Capabilities for a device tier."""

    tier: DeviceTier
    description: str = ""
    devices: list[str] = field(default_factory=list)
    native_formats: list[str] = field(default_factory=list)
    transcode_required: list[str] = field(default_factory=list)
    streaming_protocols: list[str] = field(default_factory=list)


@dataclass
class DeviceConfig:
    """Loaded device configuration."""

    modern: DeviceCapabilities
    legacy: DeviceCapabilities
    future: DeviceCapabilities
    unknown_device_tier: DeviceTier = DeviceTier.LEGACY
    transcode_target: str = "mp3"
    transcode_fallback: str = "pcm"

    # Lookup cache: device_name -> tier
    _device_tier_map: dict[str, DeviceTier] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Build the device lookup map."""
        for device in self.modern.devices:
            self._device_tier_map[device.lower()] = DeviceTier.MODERN
        for device in self.legacy.devices:
            self._device_tier_map[device.lower()] = DeviceTier.LEGACY
        for device in self.future.devices:
            self._device_tier_map[device.lower()] = DeviceTier.FUTURE

    def get_tier(self, device_type: DeviceType | str) -> DeviceTier:
        """
        Get the capability tier for a device type.

        Args:
            device_type: DeviceType enum or string name.

        Returns:
            The DeviceTier for this device.
        """
        # Check if it's an enum with a name attribute
        if hasattr(device_type, "name") and not isinstance(device_type, str):
            name = str(device_type.name).lower()
        else:
            name = str(device_type).lower()

        return self._device_tier_map.get(name, self.unknown_device_tier)

    def get_capabilities(self, device_type: DeviceType | str) -> DeviceCapabilities:
        """
        Get the full capabilities for a device type.

        Args:
            device_type: DeviceType enum or string name.

        Returns:
            DeviceCapabilities for this device's tier.
        """
        tier = self.get_tier(device_type)

        if tier == DeviceTier.MODERN:
            return self.modern
        elif tier == DeviceTier.FUTURE:
            return self.future
        else:
            return self.legacy

    def can_decode_natively(self, device_type: DeviceType | str, format: str) -> bool:
        """
        Check if a device can decode a format natively (no transcoding).

        This checks BOTH native_formats (whitelist) AND transcode_required (blacklist).
        A format must be in native_formats AND NOT in transcode_required.

        Args:
            device_type: DeviceType enum or string name.
            format: Audio format extension (e.g., "m4b", "flac").

        Returns:
            True if the device can decode this format without transcoding.
        """
        caps = self.get_capabilities(device_type)
        format_lower = format.lower().lstrip(".")

        # Check if format is in the transcode_required list (blacklist)
        if format_lower in [f.lower() for f in caps.transcode_required]:
            return False

        # "*" means all formats supported (if not blacklisted above)
        if "*" in caps.native_formats:
            return True

        return format_lower in [f.lower() for f in caps.native_formats]

    def needs_transcoding(self, device_type: DeviceType | str, format: str) -> bool:
        """
        Check if a format requires transcoding for a device.

        Args:
            device_type: DeviceType enum or string name.
            format: Audio format extension (e.g., "m4b", "flac").

        Returns:
            True if transcoding is required.
        """
        caps = self.get_capabilities(device_type)
        format_lower = format.lower().lstrip(".")

        # Explicit check: if format is in transcode_required, always transcode
        if format_lower in [f.lower() for f in caps.transcode_required]:
            return True

        # Otherwise, transcode if not natively supported
        return not self.can_decode_natively(device_type, format)

    def is_legacy(self, device_type: DeviceType | str) -> bool:
        """Check if a device is legacy hardware."""
        return self.get_tier(device_type) == DeviceTier.LEGACY

    def is_modern(self, device_type: DeviceType | str) -> bool:
        """Check if a device is a modern software player."""
        return self.get_tier(device_type) == DeviceTier.MODERN


def _parse_capabilities(data: dict[str, object], tier: DeviceTier) -> DeviceCapabilities:
    """Parse a tier section from the TOML data."""
    return DeviceCapabilities(
        tier=tier,
        description=str(data.get("description", "")),
        devices=list(data.get("devices", [])),  # type: ignore[arg-type]
        native_formats=list(data.get("native_formats", [])),  # type: ignore[arg-type]
        transcode_required=list(data.get("transcode_required", [])),  # type: ignore[arg-type]
        streaming_protocols=list(data.get("streaming_protocols", [])),  # type: ignore[arg-type]
    )


def load_device_config(config_path: Path | None = None) -> DeviceConfig:
    """
    Load device configuration from TOML file.

    Args:
        config_path: Path to devices.toml. If None, uses default location.

    Returns:
        Loaded DeviceConfig instance.
    """
    if config_path is None:
        config_path = CONFIG_DIR / "devices.toml"

    logger.debug("Loading device config from %s", config_path)

    with config_path.open("rb") as f:
        data = tomllib.load(f)

    # Parse tier sections
    modern = _parse_capabilities(data.get("modern", {}), DeviceTier.MODERN)
    legacy = _parse_capabilities(data.get("legacy", {}), DeviceTier.LEGACY)
    future = _parse_capabilities(data.get("future", {}), DeviceTier.FUTURE)

    # Parse defaults
    defaults = data.get("defaults", {})
    unknown_tier_str = defaults.get("unknown_device_tier", "legacy")
    try:
        unknown_tier = DeviceTier(unknown_tier_str)
    except ValueError:
        unknown_tier = DeviceTier.LEGACY

    return DeviceConfig(
        modern=modern,
        legacy=legacy,
        future=future,
        unknown_device_tier=unknown_tier,
        transcode_target=defaults.get("transcode_target", "flac"),
        transcode_fallback=defaults.get("transcode_fallback", "pcm"),
    )


# Global singleton instance (lazy loaded)
_device_config: DeviceConfig | None = None


def get_device_config() -> DeviceConfig:
    """
    Get the global device configuration (lazy loaded singleton).

    Returns:
        The DeviceConfig instance.
    """
    global _device_config

    if _device_config is None:
        _device_config = load_device_config()

    return _device_config


def reload_device_config() -> DeviceConfig:
    """
    Force reload of device configuration.

    Returns:
        The newly loaded DeviceConfig instance.
    """
    global _device_config
    _device_config = load_device_config()
    return _device_config
