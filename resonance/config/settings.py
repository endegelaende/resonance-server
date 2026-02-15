"""
Server Settings for Resonance.

Provides a TOML-based configuration system with:
- Default values for all settings
- TOML file loading (``tomllib``, Python 3.11+)
- TOML file saving (runtime-changeable settings written back)
- CLI argument override (highest priority)
- Clear separation of restart-required vs runtime-changeable settings

Priority order (highest wins):
  1. CLI arguments
  2. TOML config file
  3. Built-in defaults

Default config file search order:
  1. Explicit ``--config <path>``
  2. ``./resonance.toml`` (current working directory)
  3. ``~/.resonance/config.toml`` (user home)

Usage::

    from resonance.config.settings import load_settings, get_settings, save_settings

    # During startup (in __main__.py):
    settings = load_settings(config_path="resonance.toml", cli_overrides={...})

    # Anywhere else:
    settings = get_settings()

    # After runtime change via API:
    settings.default_volume = 60
    save_settings()
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Settings that can be changed at runtime (without server restart)
# =============================================================================

RUNTIME_CHANGEABLE: frozenset[str] = frozenset({
    # Library
    "music_folders",
    "scan_on_startup",
    "auto_rescan",
    # Playback defaults
    "default_volume",
    "default_repeat",
    "default_transition_type",
    "default_transition_duration",
    "default_replay_gain_mode",
    # Logging
    "log_level",
})

# Settings that require a server restart to take effect
RESTART_REQUIRED: frozenset[str] = frozenset({
    "host",
    "slimproto_port",
    "web_port",
    "cli_port",
    "cors_origins",
    "data_dir",
    "cache_dir",
    # Security
    "auth_enabled",
    "auth_username",
    "auth_password_hash",
    "rate_limit_enabled",
    "rate_limit_per_second",
})


# =============================================================================
# TOML section mapping
# =============================================================================

# Maps TOML section.key → dataclass field name
_TOML_SECTION_MAP: dict[str, dict[str, str]] = {
    "server": {
        "host": "host",
        "slimproto_port": "slimproto_port",
        "web_port": "web_port",
        "cli_port": "cli_port",
        "cors_origins": "cors_origins",
    },
    "library": {
        "music_folders": "music_folders",
        "scan_on_startup": "scan_on_startup",
        "auto_rescan": "auto_rescan",
    },
    "playback": {
        "default_volume": "default_volume",
        "default_repeat": "default_repeat",
        "default_transition_type": "default_transition_type",
        "default_transition_duration": "default_transition_duration",
        "default_replay_gain_mode": "default_replay_gain_mode",
    },
    "paths": {
        "data_dir": "data_dir",
        "cache_dir": "cache_dir",
    },
    "logging": {
        "log_level": "log_level",
        "log_file": "log_file",
    },
    "security": {
        "auth_enabled": "auth_enabled",
        "auth_username": "auth_username",
        "auth_password_hash": "auth_password_hash",
        "rate_limit_enabled": "rate_limit_enabled",
        "rate_limit_per_second": "rate_limit_per_second",
    },
}

# Reverse map: field name → (section, key)
_FIELD_TO_TOML: dict[str, tuple[str, str]] = {}
for _section, _keys in _TOML_SECTION_MAP.items():
    for _toml_key, _field_name in _keys.items():
        _FIELD_TO_TOML[_field_name] = (_section, _toml_key)


# =============================================================================
# ServerSettings dataclass
# =============================================================================


@dataclass
class ServerSettings:
    """
    All configurable server settings.

    Fields are grouped by category. Default values match the current
    hard-coded defaults throughout the codebase.
    """

    # ── Network ──────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    slimproto_port: int = 3483
    web_port: int = 9000
    cli_port: int = 9090
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    # ── Library ──────────────────────────────────────────────────────────
    music_folders: list[str] = field(default_factory=list)
    scan_on_startup: bool = True
    auto_rescan: bool = False

    # ── Playback Defaults ────────────────────────────────────────────────
    default_volume: int = 50
    default_repeat: int = 0  # 0=off, 1=song, 2=playlist
    default_transition_type: int = 0  # 0=none, 1=crossfade, 2=fade-in, 3=fade-out, 4=fade-in-out
    default_transition_duration: int = 5  # seconds
    default_replay_gain_mode: int = 0  # 0=off, 1=track, 2=album, 3=smart

    # ── Paths ────────────────────────────────────────────────────────────
    data_dir: str = "data"
    cache_dir: str = "cache"

    # ── Logging ──────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_file: str | None = None

    # ── Security ─────────────────────────────────────────────────────────
    auth_enabled: bool = False
    auth_username: str = ""
    auth_password_hash: str = ""
    rate_limit_enabled: bool = False
    rate_limit_per_second: int = 100

    # ── Internal (not persisted) ─────────────────────────────────────────
    _config_path: Path | None = field(default=None, repr=False, compare=False)

    def is_runtime_changeable(self, field_name: str) -> bool:
        """Check whether a setting can be changed without restart."""
        return field_name in RUNTIME_CHANGEABLE

    def is_restart_required(self, field_name: str) -> bool:
        """Check whether changing a setting requires a server restart."""
        return field_name in RESTART_REQUIRED

    def to_dict(self, *, include_internal: bool = False) -> dict[str, Any]:
        """
        Serialise all settings to a flat dictionary.

        Args:
            include_internal: If True, include underscore-prefixed fields.

        Returns:
            Dictionary of field_name → value.
        """
        result: dict[str, Any] = {}
        for f in fields(self):
            if f.name.startswith("_") and not include_internal:
                continue
            result[f.name] = getattr(self, f.name)
        return result

    def to_toml_dict(self) -> dict[str, dict[str, Any]]:
        """
        Serialise settings into nested TOML-style sections.

        Returns:
            ``{"server": {...}, "library": {...}, ...}``
        """
        result: dict[str, dict[str, Any]] = {}
        for section, keys in _TOML_SECTION_MAP.items():
            section_dict: dict[str, Any] = {}
            for toml_key, field_name in keys.items():
                value = getattr(self, field_name)
                # Skip None values (optional fields)
                if value is not None:
                    section_dict[toml_key] = value
            if section_dict:
                result[section] = section_dict
        return result

    def validate(self) -> list[str]:
        """
        Validate current settings and return a list of error messages.

        Returns:
            Empty list if all settings are valid, otherwise error strings.
        """
        errors: list[str] = []

        # Port range
        for port_field in ("slimproto_port", "web_port", "cli_port"):
            value = getattr(self, port_field)
            if port_field == "cli_port" and value == 0:
                continue  # 0 means "disabled"
            if not (1 <= value <= 65535):
                errors.append(f"{port_field} must be 1–65535 (got {value})")

        # Unique ports (only if cli_port is enabled)
        ports = [self.slimproto_port, self.web_port]
        if self.cli_port > 0:
            ports.append(self.cli_port)
        if len(ports) != len(set(ports)):
            errors.append("slimproto_port, web_port, and cli_port must be different")

        # Volume range
        if not (0 <= self.default_volume <= 100):
            errors.append(f"default_volume must be 0–100 (got {self.default_volume})")

        # Repeat mode
        if self.default_repeat not in (0, 1, 2):
            errors.append(f"default_repeat must be 0, 1, or 2 (got {self.default_repeat})")

        # Transition type
        if not (0 <= self.default_transition_type <= 4):
            errors.append(
                f"default_transition_type must be 0–4 (got {self.default_transition_type})"
            )

        # Transition duration
        if not (0 <= self.default_transition_duration <= 30):
            errors.append(
                f"default_transition_duration must be 0–30 (got {self.default_transition_duration})"
            )

        # Replay gain mode
        if self.default_replay_gain_mode not in (0, 1, 2, 3):
            errors.append(
                f"default_replay_gain_mode must be 0–3 (got {self.default_replay_gain_mode})"
            )

        # Log level
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_levels:
            errors.append(f"log_level must be one of {valid_levels} (got {self.log_level!r})")

        # Security: auth_enabled requires username + password_hash
        if self.auth_enabled:
            if not self.auth_username:
                errors.append("auth_enabled requires a non-empty auth_username")
            if not self.auth_password_hash:
                errors.append("auth_enabled requires a non-empty auth_password_hash")

        # Rate limit
        if not (1 <= self.rate_limit_per_second <= 10_000):
            errors.append(
                f"rate_limit_per_second must be 1–10000 (got {self.rate_limit_per_second})"
            )

        return errors


# =============================================================================
# Loading
# =============================================================================


def _find_config_file(explicit_path: str | Path | None = None) -> Path | None:
    """
    Locate the configuration file.

    Search order:
      1. Explicit path (``--config``)
      2. ``./resonance.toml``
      3. ``~/.resonance/config.toml``

    Returns:
        Path to the config file, or None if not found.
    """
    if explicit_path is not None:
        p = Path(explicit_path).expanduser()
        if p.is_file():
            return p
        logger.warning("Explicit config file not found: %s", p)
        return None

    # CWD
    cwd_config = Path("resonance.toml")
    if cwd_config.is_file():
        return cwd_config

    # Home directory
    home_config = Path.home() / ".resonance" / "config.toml"
    if home_config.is_file():
        return home_config

    return None


def _parse_toml(data: dict[str, Any]) -> dict[str, Any]:
    """
    Flatten a TOML dict (with sections) into field-name → value pairs.

    Unknown keys emit a warning and are skipped.
    """
    result: dict[str, Any] = {}
    known_sections = set(_TOML_SECTION_MAP.keys())

    for section, section_data in data.items():
        if section not in known_sections:
            if isinstance(section_data, dict):
                logger.warning("Unknown config section [%s] — ignored", section)
            continue

        if not isinstance(section_data, dict):
            logger.warning("Config section [%s] is not a table — ignored", section)
            continue

        key_map = _TOML_SECTION_MAP[section]
        for toml_key, value in section_data.items():
            if toml_key not in key_map:
                logger.warning("Unknown config key [%s].%s — ignored", section, toml_key)
                continue
            field_name = key_map[toml_key]
            result[field_name] = value

    return result


def _apply_cli_overrides(
    settings: ServerSettings, cli_overrides: dict[str, Any]
) -> None:
    """
    Apply CLI argument overrides to settings (in-place).

    Only non-None values are applied.  The ``cors_origins`` CLI argument
    is a comma-separated string that gets split into a list.
    """
    for key, value in cli_overrides.items():
        if value is None:
            continue

        # Map CLI arg names to settings field names
        field_map: dict[str, str] = {
            "port": "slimproto_port",
            "host": "host",
            "web_port": "web_port",
            "cli_port": "cli_port",
            "cors_origins": "cors_origins",
            "verbose": "_verbose",
            "config": "_config_path_cli",
        }

        field_name = field_map.get(key, key)

        # Special handling
        if field_name == "_verbose" and value is True:
            settings.log_level = "DEBUG"
            continue
        if field_name == "_config_path_cli":
            continue  # Already handled before this function

        if not hasattr(settings, field_name):
            continue

        # cors_origins: CLI sends comma-separated string
        if field_name == "cors_origins" and isinstance(value, str):
            if value.strip() == "*":
                value = ["*"]
            else:
                value = [o.strip() for o in value.split(",") if o.strip()]

        setattr(settings, field_name, value)


def load_settings(
    config_path: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ServerSettings:
    """
    Load settings with full priority chain.

    Args:
        config_path: Explicit path to a TOML config file (from ``--config``).
        cli_overrides: Dictionary of CLI argument values to override.

    Returns:
        Fully resolved ``ServerSettings`` instance.
    """
    # Start with defaults
    settings = ServerSettings()

    # Try to find and load TOML config
    found_path = _find_config_file(config_path)
    if found_path is not None:
        logger.info("Loading config from %s", found_path)
        try:
            with found_path.open("rb") as f:
                toml_data = tomllib.load(f)
            flat = _parse_toml(toml_data)
            for key, value in flat.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
                else:
                    logger.warning("Config key %r does not map to a setting — ignored", key)
        except Exception as exc:
            logger.error("Failed to load config from %s: %s", found_path, exc)
            logger.info("Continuing with default settings")
    else:
        if config_path is not None:
            logger.warning("Specified config file not found: %s", config_path)
        else:
            logger.debug("No config file found, using defaults")

    # Store the resolved config path (for save_settings)
    settings._config_path = found_path

    # Apply CLI overrides (highest priority)
    if cli_overrides:
        _apply_cli_overrides(settings, cli_overrides)

    # Validate
    errors = settings.validate()
    if errors:
        for err in errors:
            logger.error("Config validation error: %s", err)
        raise ValueError(f"Invalid configuration: {'; '.join(errors)}")

    return settings


# =============================================================================
# Saving
# =============================================================================


def _format_toml_value(value: Any) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        # Escape backslashes and quotes
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        if not value:
            return "[]"
        items = ", ".join(_format_toml_value(v) for v in value)
        return f"[{items}]"
    return repr(value)


def _serialise_toml(sections: dict[str, dict[str, Any]]) -> str:
    """
    Serialise a nested dict to TOML format.

    Produces clean, human-readable TOML with section headers and comments.
    """
    lines: list[str] = [
        "# Resonance Server Configuration",
        "# Generated — edit freely; CLI arguments override these values.",
        "",
    ]

    section_comments: dict[str, str] = {
        "server": "# Network and server settings (restart required to apply changes)",
        "library": "# Music library settings",
        "playback": "# Default playback settings for new players",
        "paths": "# Data and cache directories (restart required to apply changes)",
        "logging": "# Logging configuration",
    }

    for section, values in sections.items():
        if not values:
            continue
        comment = section_comments.get(section)
        if comment:
            lines.append(comment)
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_format_toml_value(value)}")
        lines.append("")

    return "\n".join(lines)


def save_settings(settings: ServerSettings | None = None) -> Path:
    """
    Save current settings to the TOML config file.

    If no config file path is known, creates ``./resonance.toml``.

    Args:
        settings: Settings to save. If None, uses the global singleton.

    Returns:
        Path to the written config file.

    Raises:
        RuntimeError: If no settings are available.
    """
    if settings is None:
        settings = get_settings()

    config_path = settings._config_path
    if config_path is None:
        config_path = Path("resonance.toml")
        settings._config_path = config_path

    # Build TOML content
    toml_dict = settings.to_toml_dict()
    content = _serialise_toml(toml_dict)

    # Write atomically (write to temp, then rename)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(".toml.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(config_path)
    except Exception:
        # Cleanup temp file on failure
        tmp_path.unlink(missing_ok=True)
        raise

    logger.info("Settings saved to %s", config_path)
    return config_path


# =============================================================================
# Runtime updates
# =============================================================================


def update_settings(updates: dict[str, Any]) -> tuple[ServerSettings, list[str]]:
    """
    Apply partial updates to the current settings.

    Only runtime-changeable settings are applied.  Restart-required
    settings are accepted but flagged in the returned warnings list.

    Args:
        updates: Dictionary of field_name → new_value.

    Returns:
        Tuple of (updated settings, list of warning messages).

    Raises:
        ValueError: If validation fails after applying updates.
        RuntimeError: If no global settings are loaded.
    """
    settings = get_settings()
    warnings: list[str] = []

    known_fields = {f.name for f in fields(settings) if not f.name.startswith("_")}

    for key, value in updates.items():
        if key not in known_fields:
            warnings.append(f"Unknown setting {key!r} — ignored")
            continue

        if key in RESTART_REQUIRED:
            warnings.append(f"Setting {key!r} requires a server restart to take effect")

        setattr(settings, key, value)

    # Validate after updates
    errors = settings.validate()
    if errors:
        raise ValueError(f"Invalid settings after update: {'; '.join(errors)}")

    return settings, warnings


def reset_settings() -> ServerSettings:
    """
    Reset all settings to their defaults.

    Preserves the config file path so ``save_settings()`` still works.

    Returns:
        The new default settings instance (also set as global).
    """
    global _settings

    config_path = _settings._config_path if _settings is not None else None
    _settings = ServerSettings(_config_path=config_path)
    return _settings


# =============================================================================
# Global singleton
# =============================================================================

_settings: ServerSettings | None = None


def get_settings() -> ServerSettings:
    """
    Get the global settings instance.

    Returns:
        The current ``ServerSettings``.

    Raises:
        RuntimeError: If settings have not been loaded yet.
    """
    if _settings is None:
        raise RuntimeError(
            "Settings not loaded yet. Call load_settings() during startup."
        )
    return _settings


def init_settings(settings: ServerSettings) -> None:
    """
    Set the global settings instance.

    Called once during startup after ``load_settings()``.

    Args:
        settings: The loaded settings to use globally.
    """
    global _settings
    _settings = settings
    logger.debug("Global settings initialised: %s", settings)


def settings_loaded() -> bool:
    """Check whether settings have been loaded."""
    return _settings is not None
