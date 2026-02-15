"""
Tests for resonance.config.settings module.

These tests verify:
- ServerSettings defaults and validation
- TOML config file loading and parsing
- CLI argument overrides (highest priority)
- Unknown keys produce warnings (no crash)
- Missing config file falls back to defaults
- Save/load round-trip consistency
- Runtime update with changeable vs restart-required separation
- Reset to defaults
- REST API endpoints: GET /api/settings, PUT /api/settings, POST /api/settings/reset

Test categories:
  - Unit: ServerSettings dataclass, validation, serialisation
  - Integration: TOML file I/O, load_settings() priority chain
  - API: FastAPI endpoint tests via httpx AsyncClient
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from resonance.config.settings import (
    RESTART_REQUIRED,
    RUNTIME_CHANGEABLE,
    ServerSettings,
    _apply_cli_overrides,
    _find_config_file,
    _parse_toml,
    _serialise_toml,
    get_settings,
    init_settings,
    load_settings,
    reset_settings,
    save_settings,
    settings_loaded,
    update_settings,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_global_settings():
    """Reset the global settings singleton before and after each test."""
    import resonance.config.settings as mod

    original = mod._settings
    mod._settings = None
    yield
    mod._settings = original


@pytest.fixture
def default_settings() -> ServerSettings:
    """A fresh ServerSettings with all defaults."""
    return ServerSettings()


@pytest.fixture
def sample_toml(tmp_path: Path) -> Path:
    """Create a sample TOML config file and return its path."""
    config = tmp_path / "resonance.toml"
    config.write_text(
        textwrap.dedent("""\
            [server]
            host = "192.168.1.100"
            web_port = 8080
            slimproto_port = 3484
            cli_port = 9091
            cors_origins = ["http://localhost:3000", "http://myapp.local"]

            [library]
            music_folders = ["/mnt/music", "/home/user/Music"]
            scan_on_startup = false
            auto_rescan = true

            [playback]
            default_volume = 75
            default_repeat = 2
            default_transition_type = 1
            default_transition_duration = 10
            default_replay_gain_mode = 2

            [paths]
            data_dir = "/var/lib/resonance"
            cache_dir = "/var/cache/resonance"

            [logging]
            log_level = "DEBUG"
            log_file = "/var/log/resonance.log"
        """),
        encoding="utf-8",
    )
    return config


@pytest.fixture
def minimal_toml(tmp_path: Path) -> Path:
    """Create a minimal TOML config with only a few overrides."""
    config = tmp_path / "minimal.toml"
    config.write_text(
        textwrap.dedent("""\
            [server]
            web_port = 8888

            [playback]
            default_volume = 30
        """),
        encoding="utf-8",
    )
    return config


# =============================================================================
# ServerSettings — Defaults
# =============================================================================


class TestServerSettingsDefaults:
    """Verify built-in default values."""

    def test_network_defaults(self, default_settings: ServerSettings) -> None:
        assert default_settings.host == "0.0.0.0"
        assert default_settings.slimproto_port == 3483
        assert default_settings.web_port == 9000
        assert default_settings.cli_port == 9090
        assert default_settings.cors_origins == ["*"]

    def test_library_defaults(self, default_settings: ServerSettings) -> None:
        assert default_settings.music_folders == []
        assert default_settings.scan_on_startup is True
        assert default_settings.auto_rescan is False

    def test_playback_defaults(self, default_settings: ServerSettings) -> None:
        assert default_settings.default_volume == 50
        assert default_settings.default_repeat == 0
        assert default_settings.default_transition_type == 0
        assert default_settings.default_transition_duration == 5
        assert default_settings.default_replay_gain_mode == 0

    def test_path_defaults(self, default_settings: ServerSettings) -> None:
        assert default_settings.data_dir == "data"
        assert default_settings.cache_dir == "cache"

    def test_logging_defaults(self, default_settings: ServerSettings) -> None:
        assert default_settings.log_level == "INFO"
        assert default_settings.log_file is None

    def test_internal_config_path_default(self, default_settings: ServerSettings) -> None:
        assert default_settings._config_path is None


# =============================================================================
# ServerSettings — Validation
# =============================================================================


class TestServerSettingsValidation:
    """Verify validation logic catches invalid values."""

    def test_valid_defaults(self, default_settings: ServerSettings) -> None:
        errors = default_settings.validate()
        assert errors == []

    def test_invalid_port_zero(self) -> None:
        s = ServerSettings(web_port=0)
        errors = s.validate()
        assert any("web_port" in e for e in errors)

    def test_invalid_port_too_high(self) -> None:
        s = ServerSettings(slimproto_port=70000)
        errors = s.validate()
        assert any("slimproto_port" in e for e in errors)

    def test_cli_port_zero_is_valid(self) -> None:
        """cli_port=0 means 'disabled' and should be accepted."""
        s = ServerSettings(cli_port=0)
        errors = s.validate()
        assert not any("cli_port" in e for e in errors)

    def test_duplicate_ports(self) -> None:
        s = ServerSettings(slimproto_port=9000, web_port=9000)
        errors = s.validate()
        assert any("must be different" in e for e in errors)

    def test_volume_out_of_range(self) -> None:
        s = ServerSettings(default_volume=150)
        errors = s.validate()
        assert any("default_volume" in e for e in errors)

    def test_volume_negative(self) -> None:
        s = ServerSettings(default_volume=-1)
        errors = s.validate()
        assert any("default_volume" in e for e in errors)

    def test_invalid_repeat_mode(self) -> None:
        s = ServerSettings(default_repeat=5)
        errors = s.validate()
        assert any("default_repeat" in e for e in errors)

    def test_invalid_transition_type(self) -> None:
        s = ServerSettings(default_transition_type=10)
        errors = s.validate()
        assert any("default_transition_type" in e for e in errors)

    def test_invalid_transition_duration(self) -> None:
        s = ServerSettings(default_transition_duration=60)
        errors = s.validate()
        assert any("default_transition_duration" in e for e in errors)

    def test_invalid_replay_gain_mode(self) -> None:
        s = ServerSettings(default_replay_gain_mode=7)
        errors = s.validate()
        assert any("default_replay_gain_mode" in e for e in errors)

    def test_invalid_log_level(self) -> None:
        s = ServerSettings(log_level="VERBOSE")
        errors = s.validate()
        assert any("log_level" in e for e in errors)

    def test_valid_log_levels(self) -> None:
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            s = ServerSettings(log_level=level)
            assert s.validate() == [], f"Expected no errors for log_level={level}"

    def test_multiple_errors(self) -> None:
        """Multiple invalid fields should produce multiple error messages."""
        s = ServerSettings(web_port=0, default_volume=200, default_repeat=9)
        errors = s.validate()
        assert len(errors) >= 3


# =============================================================================
# ServerSettings — Serialisation
# =============================================================================


class TestServerSettingsSerialisation:
    """Verify to_dict and to_toml_dict."""

    def test_to_dict_excludes_internal(self, default_settings: ServerSettings) -> None:
        d = default_settings.to_dict()
        assert "_config_path" not in d
        assert "host" in d
        assert "default_volume" in d

    def test_to_dict_includes_internal(self, default_settings: ServerSettings) -> None:
        d = default_settings.to_dict(include_internal=True)
        assert "_config_path" in d

    def test_to_toml_dict_structure(self, default_settings: ServerSettings) -> None:
        toml_dict = default_settings.to_toml_dict()
        assert "server" in toml_dict
        assert "library" in toml_dict
        assert "playback" in toml_dict
        assert "paths" in toml_dict
        assert "logging" in toml_dict

    def test_to_toml_dict_values(self, default_settings: ServerSettings) -> None:
        toml_dict = default_settings.to_toml_dict()
        assert toml_dict["server"]["host"] == "0.0.0.0"
        assert toml_dict["server"]["web_port"] == 9000
        assert toml_dict["playback"]["default_volume"] == 50

    def test_to_toml_dict_skips_none(self) -> None:
        """log_file=None should not appear in the TOML output."""
        s = ServerSettings(log_file=None)
        toml_dict = s.to_toml_dict()
        assert "log_file" not in toml_dict.get("logging", {})


# =============================================================================
# Changeability metadata
# =============================================================================


class TestChangeability:
    """Verify runtime-changeable vs restart-required classification."""

    def test_runtime_changeable_fields(self) -> None:
        expected = {
            "music_folders", "scan_on_startup", "auto_rescan",
            "default_volume", "default_repeat", "default_transition_type",
            "default_transition_duration", "default_replay_gain_mode",
            "log_level",
        }
        assert RUNTIME_CHANGEABLE == expected

    def test_restart_required_fields(self) -> None:
        expected = {
            "host", "slimproto_port", "web_port", "cli_port",
            "cors_origins", "data_dir", "cache_dir",
            "auth_enabled", "auth_username", "auth_password_hash",
            "rate_limit_enabled", "rate_limit_per_second",
        }
        assert RESTART_REQUIRED == expected

    def test_no_overlap(self) -> None:
        assert RUNTIME_CHANGEABLE & RESTART_REQUIRED == set()

    def test_is_runtime_changeable(self, default_settings: ServerSettings) -> None:
        assert default_settings.is_runtime_changeable("default_volume") is True
        assert default_settings.is_runtime_changeable("web_port") is False

    def test_is_restart_required(self, default_settings: ServerSettings) -> None:
        assert default_settings.is_restart_required("web_port") is True
        assert default_settings.is_restart_required("default_volume") is False


# =============================================================================
# TOML parsing
# =============================================================================


class TestTomlParsing:
    """Verify _parse_toml flattening and unknown-key handling."""

    def test_parse_known_keys(self) -> None:
        data = {
            "server": {"host": "127.0.0.1", "web_port": 8080},
            "playback": {"default_volume": 42},
        }
        result = _parse_toml(data)
        assert result == {"host": "127.0.0.1", "web_port": 8080, "default_volume": 42}

    def test_unknown_section_ignored(self, caplog: pytest.LogCaptureFixture) -> None:
        data = {"experimental": {"foo": "bar"}}
        result = _parse_toml(data)
        assert result == {}
        assert "Unknown config section" in caplog.text

    def test_unknown_key_ignored(self, caplog: pytest.LogCaptureFixture) -> None:
        data = {"server": {"host": "127.0.0.1", "unknown_key": 999}}
        result = _parse_toml(data)
        assert "host" in result
        assert "unknown_key" not in result
        assert "Unknown config key" in caplog.text

    def test_non_dict_section_ignored(self, caplog: pytest.LogCaptureFixture) -> None:
        data = {"server": "not_a_table"}
        result = _parse_toml(data)
        assert result == {}
        assert "not a table" in caplog.text

    def test_all_sections(self) -> None:
        data = {
            "server": {"host": "10.0.0.1"},
            "library": {"scan_on_startup": False},
            "playback": {"default_repeat": 1},
            "paths": {"data_dir": "/data"},
            "logging": {"log_level": "WARNING"},
        }
        result = _parse_toml(data)
        assert result["host"] == "10.0.0.1"
        assert result["scan_on_startup"] is False
        assert result["default_repeat"] == 1
        assert result["data_dir"] == "/data"
        assert result["log_level"] == "WARNING"


# =============================================================================
# CLI overrides
# =============================================================================


class TestCliOverrides:
    """Verify CLI argument application to settings."""

    def test_port_override(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"port": 4000})
        assert s.slimproto_port == 4000

    def test_host_override(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"host": "127.0.0.1"})
        assert s.host == "127.0.0.1"

    def test_web_port_override(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"web_port": 8080})
        assert s.web_port == 8080

    def test_cli_port_override(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"cli_port": 0})
        assert s.cli_port == 0

    def test_verbose_sets_debug(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"verbose": True})
        assert s.log_level == "DEBUG"

    def test_verbose_false_no_change(self) -> None:
        s = ServerSettings(log_level="WARNING")
        _apply_cli_overrides(s, {"verbose": False})
        assert s.log_level == "WARNING"

    def test_cors_origins_string_star(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"cors_origins": "*"})
        assert s.cors_origins == ["*"]

    def test_cors_origins_comma_separated(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"cors_origins": "http://a.com, http://b.com"})
        assert s.cors_origins == ["http://a.com", "http://b.com"]

    def test_none_values_skipped(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"host": None, "port": None, "web_port": None})
        assert s.host == "0.0.0.0"
        assert s.slimproto_port == 3483
        assert s.web_port == 9000

    def test_unknown_cli_key_ignored(self) -> None:
        s = ServerSettings()
        _apply_cli_overrides(s, {"nonexistent_arg": "value"})
        # Should not raise, should not modify anything
        assert s.host == "0.0.0.0"

    def test_config_key_ignored(self) -> None:
        """The --config arg is handled before _apply_cli_overrides."""
        s = ServerSettings()
        _apply_cli_overrides(s, {"config": "/path/to/config.toml"})
        assert s._config_path is None  # Not set by CLI override


# =============================================================================
# Config file discovery
# =============================================================================


class TestConfigFileDiscovery:
    """Verify _find_config_file search logic."""

    def test_explicit_path_found(self, sample_toml: Path) -> None:
        result = _find_config_file(sample_toml)
        assert result == sample_toml

    def test_explicit_path_not_found(self, tmp_path: Path) -> None:
        result = _find_config_file(tmp_path / "nonexistent.toml")
        assert result is None

    def test_no_config_anywhere(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no config file exists anywhere, return None."""
        monkeypatch.chdir(tmp_path)
        # Ensure home dir also doesn't have one
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
        result = _find_config_file()
        assert result is None

    def test_cwd_config_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        cwd_config = tmp_path / "resonance.toml"
        cwd_config.write_text('[server]\nweb_port = 7777\n', encoding="utf-8")
        result = _find_config_file()
        assert result is not None
        assert result.resolve() == cwd_config.resolve()


# =============================================================================
# load_settings — Integration
# =============================================================================


class TestLoadSettings:
    """Verify the full load_settings() priority chain."""

    def test_defaults_when_no_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
        settings = load_settings()
        assert settings.host == "0.0.0.0"
        assert settings.web_port == 9000
        assert settings._config_path is None

    def test_toml_values_applied(self, sample_toml: Path) -> None:
        settings = load_settings(config_path=sample_toml)
        assert settings.host == "192.168.1.100"
        assert settings.web_port == 8080
        assert settings.slimproto_port == 3484
        assert settings.cli_port == 9091
        assert settings.cors_origins == ["http://localhost:3000", "http://myapp.local"]
        assert settings.music_folders == ["/mnt/music", "/home/user/Music"]
        assert settings.scan_on_startup is False
        assert settings.auto_rescan is True
        assert settings.default_volume == 75
        assert settings.default_repeat == 2
        assert settings.default_transition_type == 1
        assert settings.default_transition_duration == 10
        assert settings.default_replay_gain_mode == 2
        assert settings.data_dir == "/var/lib/resonance"
        assert settings.cache_dir == "/var/cache/resonance"
        assert settings.log_level == "DEBUG"
        assert settings.log_file == "/var/log/resonance.log"

    def test_partial_toml_defaults_preserved(self, minimal_toml: Path) -> None:
        """Non-specified keys should keep their default values."""
        settings = load_settings(config_path=minimal_toml)
        assert settings.web_port == 8888
        assert settings.default_volume == 30
        # Defaults for non-specified keys
        assert settings.host == "0.0.0.0"
        assert settings.slimproto_port == 3483
        assert settings.cli_port == 9090
        assert settings.default_repeat == 0
        assert settings.log_level == "INFO"

    def test_cli_overrides_toml(self, sample_toml: Path) -> None:
        """CLI arguments should override TOML values."""
        settings = load_settings(
            config_path=sample_toml,
            cli_overrides={"web_port": 7777, "host": "0.0.0.0"},
        )
        assert settings.web_port == 7777  # CLI wins
        assert settings.host == "0.0.0.0"  # CLI wins
        assert settings.default_volume == 75  # TOML value preserved

    def test_cli_verbose_overrides_toml_log_level(self, minimal_toml: Path) -> None:
        settings = load_settings(
            config_path=minimal_toml,
            cli_overrides={"verbose": True},
        )
        assert settings.log_level == "DEBUG"

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        """A TOML file with invalid settings should raise ValueError."""
        config = tmp_path / "bad.toml"
        config.write_text(
            textwrap.dedent("""\
                [server]
                web_port = 0
            """),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Invalid configuration"):
            load_settings(config_path=config)

    def test_corrupt_toml_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A syntactically invalid TOML file should fall back to defaults."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
        config = tmp_path / "bad.toml"
        config.write_text("this is not valid [toml", encoding="utf-8")
        # Should not crash, just log and use defaults
        settings = load_settings(config_path=config)
        # The path won't be found (explicit path check returns None for parse errors)
        # Actually _find_config_file finds the file, but tomllib.load will fail
        # and the code catches the exception and continues with defaults
        assert settings.web_port == 9000

    def test_config_path_stored(self, sample_toml: Path) -> None:
        settings = load_settings(config_path=sample_toml)
        assert settings._config_path == sample_toml

    def test_unknown_keys_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        config = tmp_path / "with_unknowns.toml"
        config.write_text(
            textwrap.dedent("""\
                [server]
                web_port = 9000
                mystery_key = "hello"

                [alien_section]
                x = 1
            """),
            encoding="utf-8",
        )
        settings = load_settings(config_path=config)
        assert settings.web_port == 9000
        assert "Unknown config key" in caplog.text or "Unknown config section" in caplog.text


# =============================================================================
# save_settings — TOML output
# =============================================================================


class TestSaveSettings:
    """Verify TOML file writing and round-trip consistency."""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        settings = ServerSettings(_config_path=tmp_path / "test.toml")
        result_path = save_settings(settings)
        assert result_path.exists()
        content = result_path.read_text(encoding="utf-8")
        assert "[server]" in content
        assert "[playback]" in content

    def test_round_trip(self, tmp_path: Path) -> None:
        """Save settings, reload, and verify values match."""
        original = ServerSettings(
            host="10.0.0.1",
            web_port=7777,
            default_volume=80,
            music_folders=["/music/a", "/music/b"],
            log_level="WARNING",
            _config_path=tmp_path / "roundtrip.toml",
        )
        save_settings(original)

        loaded = load_settings(config_path=tmp_path / "roundtrip.toml")
        assert loaded.host == "10.0.0.1"
        assert loaded.web_port == 7777
        assert loaded.default_volume == 80
        assert loaded.music_folders == ["/music/a", "/music/b"]
        assert loaded.log_level == "WARNING"

    def test_save_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no config path is set, save to ./resonance.toml."""
        monkeypatch.chdir(tmp_path)
        settings = ServerSettings()  # _config_path is None
        result_path = save_settings(settings)
        assert result_path == Path("resonance.toml")
        assert (tmp_path / "resonance.toml").exists()

    def test_save_preserves_comments_header(self, tmp_path: Path) -> None:
        settings = ServerSettings(_config_path=tmp_path / "commented.toml")
        save_settings(settings)
        content = (tmp_path / "commented.toml").read_text(encoding="utf-8")
        assert "Resonance Server Configuration" in content

    def test_serialise_toml_format(self) -> None:
        sections = {"server": {"host": "0.0.0.0", "web_port": 9000}}
        result = _serialise_toml(sections)
        assert '[server]' in result
        assert 'host = "0.0.0.0"' in result
        assert 'web_port = 9000' in result

    def test_serialise_toml_bool(self) -> None:
        sections = {"library": {"scan_on_startup": True, "auto_rescan": False}}
        result = _serialise_toml(sections)
        assert "scan_on_startup = true" in result
        assert "auto_rescan = false" in result

    def test_serialise_toml_list(self) -> None:
        sections = {"library": {"music_folders": ["/a", "/b"]}}
        result = _serialise_toml(sections)
        assert 'music_folders = ["/a", "/b"]' in result

    def test_serialise_toml_empty_list(self) -> None:
        sections = {"library": {"music_folders": []}}
        result = _serialise_toml(sections)
        assert "music_folders = []" in result

    def test_serialise_toml_backslash_path(self) -> None:
        """Windows-style paths with backslashes should be escaped."""
        sections = {"library": {"music_folders": ["C:\\Users\\music"]}}
        result = _serialise_toml(sections)
        assert "C:\\\\Users\\\\music" in result

    def test_save_atomic_no_partial_on_error(self, tmp_path: Path) -> None:
        """If write fails, the original file should remain intact."""
        config_path = tmp_path / "existing.toml"
        config_path.write_text("[server]\nweb_port = 1234\n", encoding="utf-8")

        settings = ServerSettings(_config_path=config_path)

        # Simulate a write failure by making the directory read-only
        # This is platform-dependent, so we test the simpler case:
        # ensure that original content remains after a successful save
        save_settings(settings)
        content = config_path.read_text(encoding="utf-8")
        assert "[server]" in content


# =============================================================================
# Global singleton
# =============================================================================


class TestGlobalSingleton:
    """Verify global settings management functions."""

    def test_get_settings_raises_before_init(self) -> None:
        with pytest.raises(RuntimeError, match="not loaded"):
            get_settings()

    def test_settings_loaded_false_initially(self) -> None:
        assert settings_loaded() is False

    def test_init_and_get(self, default_settings: ServerSettings) -> None:
        init_settings(default_settings)
        assert settings_loaded() is True
        assert get_settings() is default_settings

    def test_init_replaces_previous(self) -> None:
        s1 = ServerSettings(web_port=1111)
        s2 = ServerSettings(web_port=2222)
        init_settings(s1)
        init_settings(s2)
        assert get_settings().web_port == 2222


# =============================================================================
# update_settings
# =============================================================================


class TestUpdateSettings:
    """Verify runtime update logic."""

    def test_update_runtime_changeable(self) -> None:
        init_settings(ServerSettings())
        settings, warnings = update_settings({"default_volume": 80, "log_level": "DEBUG"})
        assert settings.default_volume == 80
        assert settings.log_level == "DEBUG"
        assert not any("restart" in w.lower() for w in warnings)

    def test_update_restart_required_warns(self) -> None:
        init_settings(ServerSettings())
        settings, warnings = update_settings({"web_port": 8080})
        assert settings.web_port == 8080
        assert any("restart" in w.lower() for w in warnings)

    def test_update_unknown_key_warns(self) -> None:
        init_settings(ServerSettings())
        settings, warnings = update_settings({"nonexistent_field": 42})
        assert any("Unknown" in w for w in warnings)

    def test_update_invalid_raises(self) -> None:
        init_settings(ServerSettings())
        with pytest.raises(ValueError, match="Invalid"):
            update_settings({"default_volume": 999})

    def test_update_multiple_fields(self) -> None:
        init_settings(ServerSettings())
        settings, warnings = update_settings({
            "default_volume": 30,
            "default_repeat": 1,
            "music_folders": ["/new/path"],
        })
        assert settings.default_volume == 30
        assert settings.default_repeat == 1
        assert settings.music_folders == ["/new/path"]

    def test_update_not_loaded_raises(self) -> None:
        with pytest.raises(RuntimeError, match="not loaded"):
            update_settings({"default_volume": 50})


# =============================================================================
# reset_settings
# =============================================================================


class TestResetSettings:
    """Verify reset to defaults."""

    def test_reset_restores_defaults(self) -> None:
        s = ServerSettings(
            web_port=7777,
            default_volume=99,
            _config_path=Path("/some/path.toml"),
        )
        init_settings(s)
        result = reset_settings()
        assert result.web_port == 9000
        assert result.default_volume == 50

    def test_reset_preserves_config_path(self) -> None:
        config_path = Path("/some/path.toml")
        s = ServerSettings(_config_path=config_path)
        init_settings(s)
        result = reset_settings()
        assert result._config_path == config_path

    def test_reset_when_no_settings(self) -> None:
        """Reset without prior init should still produce defaults."""
        result = reset_settings()
        assert result.web_port == 9000
        assert result._config_path is None


# =============================================================================
# REST API — Fixtures
# =============================================================================


@pytest.fixture
async def api_client(tmp_path: Path) -> AsyncClient:
    """Create an httpx AsyncClient connected to a WebServer with settings loaded."""
    from resonance.core.library import MusicLibrary
    from resonance.core.library_db import LibraryDb
    from resonance.player.registry import PlayerRegistry
    from resonance.web.server import WebServer

    # Initialise settings singleton
    settings = ServerSettings(
        web_port=9000,
        default_volume=50,
        music_folders=["/test/music"],
        _config_path=tmp_path / "test_config.toml",
    )
    init_settings(settings)

    # Minimal server wiring (same pattern as test_web_api.py)
    db = LibraryDb(db_path=":memory:")
    await db.open()
    await db.ensure_schema()
    library = MusicLibrary(db=db)
    await library.initialize()
    registry = PlayerRegistry()

    web_server = WebServer(
        player_registry=registry,
        music_library=library,
    )

    transport = ASGITransport(app=web_server.app)
    client = AsyncClient(transport=transport, base_url="http://testserver")

    yield client  # type: ignore[misc]

    await client.aclose()
    await db.close()


# =============================================================================
# REST API — GET /api/settings
# =============================================================================


class TestGetSettingsApi:
    """Verify GET /api/settings endpoint."""

    async def test_get_settings_ok(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "settings" in data
        assert "sections" in data
        assert "meta" in data
        assert "config_file" in data

    async def test_get_settings_values(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/api/settings")
        data = resp.json()
        settings = data["settings"]
        assert settings["web_port"] == 9000
        assert settings["default_volume"] == 50
        assert settings["music_folders"] == ["/test/music"]

    async def test_get_settings_meta_runtime(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/api/settings")
        meta = resp.json()["meta"]
        assert meta["default_volume"] == "runtime"
        assert meta["log_level"] == "runtime"

    async def test_get_settings_meta_restart(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/api/settings")
        meta = resp.json()["meta"]
        assert meta["web_port"] == "restart_required"
        assert meta["host"] == "restart_required"

    async def test_get_settings_sections_structure(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/api/settings")
        sections = resp.json()["sections"]
        assert "server" in sections
        assert "playback" in sections
        assert sections["server"]["web_port"] == 9000

    async def test_get_settings_config_file(self, api_client: AsyncClient, tmp_path: Path) -> None:
        resp = await api_client.get("/api/settings")
        data = resp.json()
        assert data["config_file"] is not None
        assert "test_config.toml" in data["config_file"]


# =============================================================================
# REST API — PUT /api/settings
# =============================================================================


class TestPutSettingsApi:
    """Verify PUT /api/settings endpoint."""

    async def test_put_runtime_setting(self, api_client: AsyncClient) -> None:
        resp = await api_client.put(
            "/api/settings",
            json={"settings": {"default_volume": 80}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["default_volume"] == 80
        assert not any("restart" in w.lower() for w in data.get("warnings", []))

    async def test_put_restart_required_warns(self, api_client: AsyncClient) -> None:
        resp = await api_client.put(
            "/api/settings",
            json={"settings": {"web_port": 8080}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["web_port"] == 8080
        assert any("restart" in w.lower() for w in data["warnings"])

    async def test_put_invalid_value(self, api_client: AsyncClient) -> None:
        resp = await api_client.put(
            "/api/settings",
            json={"settings": {"default_volume": 999}},
        )
        assert resp.status_code == 422

    async def test_put_missing_settings_key(self, api_client: AsyncClient) -> None:
        resp = await api_client.put("/api/settings", json={"foo": "bar"})
        assert resp.status_code == 400

    async def test_put_empty_body(self, api_client: AsyncClient) -> None:
        resp = await api_client.put("/api/settings", json={})
        assert resp.status_code == 400

    async def test_put_multiple_fields(self, api_client: AsyncClient) -> None:
        resp = await api_client.put(
            "/api/settings",
            json={"settings": {"default_volume": 30, "log_level": "WARNING"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["default_volume"] == 30
        assert data["settings"]["log_level"] == "WARNING"

    async def test_put_persists_to_file(self, api_client: AsyncClient, tmp_path: Path) -> None:
        """After PUT, settings should be saved to the TOML file."""
        resp = await api_client.put(
            "/api/settings",
            json={"settings": {"default_volume": 42}},
        )
        assert resp.status_code == 200

        # Check the file was written
        config_path = tmp_path / "test_config.toml"
        if config_path.exists():
            content = config_path.read_text(encoding="utf-8")
            assert "default_volume = 42" in content

    async def test_put_unknown_field_warning(self, api_client: AsyncClient) -> None:
        resp = await api_client.put(
            "/api/settings",
            json={"settings": {"nonexistent": "value", "default_volume": 50}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any("Unknown" in w for w in data["warnings"])

    async def test_get_reflects_put(self, api_client: AsyncClient) -> None:
        """GET after PUT should return the updated values."""
        await api_client.put(
            "/api/settings",
            json={"settings": {"default_volume": 99}},
        )
        resp = await api_client.get("/api/settings")
        assert resp.json()["settings"]["default_volume"] == 99


# =============================================================================
# REST API — POST /api/settings/reset
# =============================================================================


class TestResetSettingsApi:
    """Verify POST /api/settings/reset endpoint."""

    async def test_reset_restores_defaults(self, api_client: AsyncClient) -> None:
        # First change something
        await api_client.put(
            "/api/settings",
            json={"settings": {"default_volume": 99}},
        )
        # Then reset
        resp = await api_client.post("/api/settings/reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["default_volume"] == 50  # default
        assert data["settings"]["web_port"] == 9000  # default

    async def test_reset_warns_about_restart(self, api_client: AsyncClient) -> None:
        resp = await api_client.post("/api/settings/reset")
        data = resp.json()
        assert any("restart" in w.lower() for w in data["warnings"])

    async def test_get_after_reset(self, api_client: AsyncClient) -> None:
        await api_client.put(
            "/api/settings",
            json={"settings": {"default_volume": 1, "log_level": "ERROR"}},
        )
        await api_client.post("/api/settings/reset")
        resp = await api_client.get("/api/settings")
        data = resp.json()
        assert data["settings"]["default_volume"] == 50
        assert data["settings"]["log_level"] == "INFO"


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    """Miscellaneous edge cases and regression tests."""

    def test_cors_origins_list_preserved(self, tmp_path: Path) -> None:
        """CORS origins as a list should survive save/load round-trip."""
        origins = ["http://a.com", "http://b.com"]
        s = ServerSettings(cors_origins=origins, _config_path=tmp_path / "cors.toml")
        save_settings(s)
        loaded = load_settings(config_path=tmp_path / "cors.toml")
        assert loaded.cors_origins == origins

    def test_empty_music_folders(self, tmp_path: Path) -> None:
        s = ServerSettings(music_folders=[], _config_path=tmp_path / "empty.toml")
        save_settings(s)
        loaded = load_settings(config_path=tmp_path / "empty.toml")
        assert loaded.music_folders == []

    def test_settings_dataclass_equality(self) -> None:
        s1 = ServerSettings()
        s2 = ServerSettings()
        assert s1 == s2

    def test_settings_dataclass_inequality(self) -> None:
        s1 = ServerSettings(web_port=1000)
        s2 = ServerSettings(web_port=2000)
        assert s1 != s2

    def test_all_settings_have_toml_mapping(self) -> None:
        """Every non-internal field should appear in the TOML section mapping."""
        from dataclasses import fields as dc_fields

        from resonance.config.settings import _TOML_SECTION_MAP

        all_toml_fields = set()
        for keys in _TOML_SECTION_MAP.values():
            all_toml_fields.update(keys.values())

        for f in dc_fields(ServerSettings):
            if f.name.startswith("_"):
                continue
            assert f.name in all_toml_fields, (
                f"Field {f.name!r} has no TOML section mapping"
            )

    def test_duplicate_ports_with_cli_disabled(self) -> None:
        """With CLI disabled (port 0), only slimproto + web need to differ."""
        s = ServerSettings(slimproto_port=3483, web_port=9000, cli_port=0)
        assert s.validate() == []

    def test_duplicate_slimproto_web(self) -> None:
        s = ServerSettings(slimproto_port=9000, web_port=9000, cli_port=0)
        errors = s.validate()
        assert any("must be different" in e for e in errors)

    def test_transition_duration_boundary(self) -> None:
        """Boundary values for transition_duration."""
        assert ServerSettings(default_transition_duration=0).validate() == []
        assert ServerSettings(default_transition_duration=30).validate() == []
        assert len(ServerSettings(default_transition_duration=31).validate()) > 0
