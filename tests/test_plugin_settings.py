from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from resonance.core.events import EventBus
from resonance.plugin import PluginContext, PluginManifest, SettingDefinition
from resonance.plugin_manager import PluginManager
from resonance.web.handlers.plugins import cmd_pluginsettings


def _write_plugin(
    root: Path,
    name: str,
    plugin_toml: str,
    init_code: str = "async def setup(ctx):\n    pass\n",
) -> None:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text(init_code, encoding="utf-8")
    (plugin_dir / "plugin.toml").write_text(plugin_toml, encoding="utf-8")


def _make_ctx(
    tmp_path: Path,
    plugin_id: str = "demo",
    settings_defs: tuple[SettingDefinition, ...] = (),
    plugin_version: str = "1.0.0",
) -> PluginContext:
    return PluginContext(
        plugin_id=plugin_id,
        event_bus=EventBus(),
        music_library=MagicMock(),
        player_registry=MagicMock(),
        data_dir=tmp_path / "data",
        settings_defs=settings_defs,
        plugin_version=plugin_version,
    )


# =============================================================================
# Phase A — TestSettingDefinitionValidation
# =============================================================================


class TestSettingDefinitionValidation:
    """Exhaustive validation tests for every setting type."""

    # -- String type --

    def test_validate_string_basic(self) -> None:
        d = SettingDefinition.from_toml("name", {"type": "string"})
        assert d.validate("hello") == (True, "")

    def test_validate_string_rejects_non_string(self) -> None:
        d = SettingDefinition.from_toml("name", {"type": "string"})
        ok, _ = d.validate(42)
        assert ok is False

    def test_validate_string_min_length(self) -> None:
        d = SettingDefinition.from_toml("name", {"type": "string", "min_length": 3})
        assert d.validate("ab")[0] is False
        assert d.validate("abc")[0] is True

    def test_validate_string_max_length(self) -> None:
        d = SettingDefinition.from_toml("name", {"type": "string", "max_length": 5})
        assert d.validate("abcde")[0] is True
        assert d.validate("abcdef")[0] is False

    def test_validate_string_min_and_max_length(self) -> None:
        d = SettingDefinition.from_toml(
            "name", {"type": "string", "min_length": 2, "max_length": 4}
        )
        assert d.validate("a")[0] is False
        assert d.validate("ab")[0] is True
        assert d.validate("abcd")[0] is True
        assert d.validate("abcde")[0] is False

    def test_validate_string_required_empty(self) -> None:
        d = SettingDefinition.from_toml(
            "name", {"type": "string", "required": True}
        )
        assert d.validate("")[0] is False
        assert d.validate(None)[0] is False
        assert d.validate("x")[0] is True

    def test_validate_string_pattern_valid(self) -> None:
        d = SettingDefinition.from_toml(
            "api_key", {"type": "string", "pattern": r"[a-z0-9]{4,8}"}
        )
        assert d.validate("abcd1234") == (True, "")

    def test_validate_string_pattern_invalid(self) -> None:
        d = SettingDefinition.from_toml(
            "api_key", {"type": "string", "pattern": r"[a-z0-9]{4,8}"}
        )
        assert d.validate("A-BAD")[0] is False

    def test_validate_string_empty_not_required(self) -> None:
        d = SettingDefinition.from_toml("name", {"type": "string"})
        assert d.validate("") == (True, "")

    # -- Int type --

    def test_validate_int_basic(self) -> None:
        d = SettingDefinition.from_toml("count", {"type": "int"})
        assert d.validate(42) == (True, "")

    def test_validate_int_range(self) -> None:
        d = SettingDefinition.from_toml(
            "volume", {"type": "int", "default": 10, "min": 0, "max": 100}
        )
        assert d.validate(50) == (True, "")
        assert d.validate(-1)[0] is False
        assert d.validate(200)[0] is False

    def test_validate_int_exact_bounds(self) -> None:
        d = SettingDefinition.from_toml(
            "val", {"type": "int", "min": 0, "max": 10}
        )
        assert d.validate(0) == (True, "")
        assert d.validate(10) == (True, "")

    def test_validate_int_no_constraints(self) -> None:
        d = SettingDefinition.from_toml("val", {"type": "int"})
        assert d.validate(-999999) == (True, "")
        assert d.validate(999999) == (True, "")

    def test_validate_int_rejects_string(self) -> None:
        d = SettingDefinition.from_toml("val", {"type": "int"})
        assert d.validate("x")[0] is False

    def test_validate_int_rejects_float(self) -> None:
        d = SettingDefinition.from_toml("val", {"type": "int"})
        assert d.validate(3.14)[0] is False

    def test_validate_int_rejects_bool(self) -> None:
        """bool is a subclass of int in Python — ensure it's rejected."""
        d = SettingDefinition.from_toml("val", {"type": "int"})
        assert d.validate(True)[0] is False
        assert d.validate(False)[0] is False

    # -- Float type --

    def test_validate_float_basic(self) -> None:
        d = SettingDefinition.from_toml("ratio", {"type": "float"})
        assert d.validate(3.14) == (True, "")

    def test_validate_float_accepts_int(self) -> None:
        """int is a valid numeric type for float settings."""
        d = SettingDefinition.from_toml("ratio", {"type": "float"})
        assert d.validate(5) == (True, "")

    def test_validate_float_range(self) -> None:
        d = SettingDefinition.from_toml(
            "ratio", {"type": "float", "min": 0.0, "max": 1.0}
        )
        assert d.validate(0.5) == (True, "")
        assert d.validate(-0.1)[0] is False
        assert d.validate(1.1)[0] is False

    def test_validate_float_exact_bounds(self) -> None:
        d = SettingDefinition.from_toml(
            "ratio", {"type": "float", "min": 0.0, "max": 1.0}
        )
        assert d.validate(0.0) == (True, "")
        assert d.validate(1.0) == (True, "")

    def test_validate_float_rejects_bool(self) -> None:
        d = SettingDefinition.from_toml("ratio", {"type": "float"})
        assert d.validate(True)[0] is False

    def test_validate_float_rejects_string(self) -> None:
        d = SettingDefinition.from_toml("ratio", {"type": "float"})
        assert d.validate("3.14")[0] is False

    # -- Bool type --

    def test_validate_bool_basic(self) -> None:
        d = SettingDefinition.from_toml("flag", {"type": "bool"})
        assert d.validate(True) == (True, "")
        assert d.validate(False) == (True, "")

    def test_validate_bool_rejects_int(self) -> None:
        """1 and 0 are NOT valid booleans for the 'bool' type."""
        d = SettingDefinition.from_toml("flag", {"type": "bool"})
        assert d.validate(1)[0] is False
        assert d.validate(0)[0] is False

    def test_validate_bool_rejects_string(self) -> None:
        d = SettingDefinition.from_toml("flag", {"type": "bool"})
        assert d.validate("true")[0] is False
        assert d.validate("yes")[0] is False

    # -- Select type --

    def test_validate_select_valid(self) -> None:
        d = SettingDefinition.from_toml(
            "service", {"type": "select", "options": ["spotify", "tidal"]}
        )
        assert d.validate("spotify") == (True, "")

    def test_validate_select_invalid_option(self) -> None:
        d = SettingDefinition.from_toml(
            "service", {"type": "select", "options": ["spotify", "tidal"]}
        )
        assert d.validate("deezer")[0] is False

    def test_validate_select_not_string(self) -> None:
        d = SettingDefinition.from_toml(
            "service", {"type": "select", "options": ["a", "b"]}
        )
        assert d.validate(123)[0] is False

    def test_select_requires_options(self) -> None:
        with pytest.raises(ValueError, match="requires non-empty 'options'"):
            SettingDefinition.from_toml("service", {"type": "select"})

    def test_select_requires_non_empty_options(self) -> None:
        with pytest.raises(ValueError, match="requires non-empty 'options'"):
            SettingDefinition.from_toml("service", {"type": "select", "options": []})

    # -- from_toml edge cases --

    def test_from_toml_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown setting type"):
            SettingDefinition.from_toml("val", {"type": "complex"})

    def test_from_toml_default_values_per_type(self) -> None:
        """Each type should get a sensible default when none is specified."""
        assert SettingDefinition.from_toml("s", {"type": "string"}).default == ""
        assert SettingDefinition.from_toml("i", {"type": "int"}).default == 0
        assert SettingDefinition.from_toml("f", {"type": "float"}).default == 0.0
        assert SettingDefinition.from_toml("b", {"type": "bool"}).default is False

    def test_from_toml_select_default_is_first_option(self) -> None:
        d = SettingDefinition.from_toml(
            "svc", {"type": "select", "options": ["alpha", "beta"]}
        )
        assert d.default == "alpha"

    def test_from_toml_explicit_default_overrides(self) -> None:
        d = SettingDefinition.from_toml(
            "val", {"type": "int", "default": 42}
        )
        assert d.default == 42

    # -- to_dict --

    def test_to_dict_includes_optional_fields(self) -> None:
        d = SettingDefinition.from_toml(
            "secret",
            {
                "type": "string",
                "label": "Secret",
                "secret": True,
                "required": True,
                "restart_required": True,
                "min_length": 2,
                "max_length": 100,
                "pattern": r"[a-z]+",
            },
        )
        data = d.to_dict()
        assert data["key"] == "secret"
        assert data["secret"] is True
        assert data["required"] is True
        assert data["restart_required"] is True
        assert data["min_length"] == 2
        assert data["max_length"] == 100
        assert data["pattern"] == r"[a-z]+"

    def test_to_dict_excludes_unset_optional_fields(self) -> None:
        d = SettingDefinition.from_toml("simple", {"type": "string"})
        data = d.to_dict()
        assert "secret" not in data
        assert "required" not in data
        assert "restart_required" not in data
        assert "min" not in data
        assert "max" not in data
        assert "min_length" not in data
        assert "max_length" not in data
        assert "pattern" not in data
        assert "options" not in data

    def test_to_dict_includes_options_for_select(self) -> None:
        d = SettingDefinition.from_toml(
            "svc", {"type": "select", "options": ["a", "b"]}
        )
        data = d.to_dict()
        assert data["options"] == ["a", "b"]

    def test_to_dict_includes_min_max_for_int(self) -> None:
        d = SettingDefinition.from_toml(
            "val", {"type": "int", "min": 0, "max": 100}
        )
        data = d.to_dict()
        assert data["min"] == 0
        assert data["max"] == 100


# =============================================================================
# Phase A — TestPluginManifestWithSettings
# =============================================================================


class TestPluginManifestWithSettings:
    def test_empty_settings(self, tmp_path: Path) -> None:
        plugin_toml = """
[plugin]
name = "bare"
version = "1.0.0"
"""
        _write_plugin(tmp_path, "bare", plugin_toml)
        manifest = PluginManager._parse_manifest(
            tmp_path / "bare" / "plugin.toml",
            tmp_path / "bare",
        )
        assert manifest.name == "bare"
        assert manifest.settings_defs == ()

    def test_settings_sorted_by_order_then_key(self, tmp_path: Path) -> None:
        plugin_toml = """
[plugin]
name = "demo"
version = "1.0.0"

[settings.charlie]
type = "string"
order = 2
default = "c"

[settings.alpha]
type = "string"
order = 1
default = "a"

[settings.bravo]
type = "string"
order = 1
default = "b"
"""
        _write_plugin(tmp_path, "demo", plugin_toml)
        manifest = PluginManager._parse_manifest(
            tmp_path / "demo" / "plugin.toml",
            tmp_path / "demo",
        )
        keys = [d.key for d in manifest.settings_defs]
        assert keys == ["alpha", "bravo", "charlie"]

    def test_invalid_settings_skipped_during_parse(self, tmp_path: Path) -> None:
        """Settings with unknown types should be silently skipped."""
        plugin_toml = """
[plugin]
name = "partial"
version = "1.0.0"

[settings.good]
type = "string"
default = "ok"

[settings.bad]
type = "imaginary_type"
"""
        _write_plugin(tmp_path, "partial", plugin_toml)
        manifest = PluginManager._parse_manifest(
            tmp_path / "partial" / "plugin.toml",
            tmp_path / "partial",
        )
        assert len(manifest.settings_defs) == 1
        assert manifest.settings_defs[0].key == "good"

    def test_manifest_missing_name_raises(self, tmp_path: Path) -> None:
        plugin_toml = """
[plugin]
version = "1.0.0"
"""
        _write_plugin(tmp_path, "noname", plugin_toml)
        with pytest.raises(ValueError, match="missing 'name'"):
            PluginManager._parse_manifest(
                tmp_path / "noname" / "plugin.toml",
                tmp_path / "noname",
            )

    def test_manifest_missing_version_raises(self, tmp_path: Path) -> None:
        plugin_toml = """
[plugin]
name = "nover"
"""
        _write_plugin(tmp_path, "nover", plugin_toml)
        with pytest.raises(ValueError, match="missing 'version'"):
            PluginManager._parse_manifest(
                tmp_path / "nover" / "plugin.toml",
                tmp_path / "nover",
            )

    def test_settings_defs_is_tuple(self, tmp_path: Path) -> None:
        plugin_toml = """
[plugin]
name = "tup"
version = "1.0.0"

[settings.a]
type = "int"
default = 1
"""
        _write_plugin(tmp_path, "tup", plugin_toml)
        manifest = PluginManager._parse_manifest(
            tmp_path / "tup" / "plugin.toml",
            tmp_path / "tup",
        )
        assert isinstance(manifest.settings_defs, tuple)

    def test_manifest_category_and_icon(self, tmp_path: Path) -> None:
        plugin_toml = """
[plugin]
name = "fancy"
version = "2.0.0"
category = "musicservices"
icon = "music"
"""
        _write_plugin(tmp_path, "fancy", plugin_toml)
        manifest = PluginManager._parse_manifest(
            tmp_path / "fancy" / "plugin.toml",
            tmp_path / "fancy",
        )
        assert manifest.category == "musicservices"
        assert manifest.icon == "music"

    def test_non_dict_settings_table_ignored(self, tmp_path: Path) -> None:
        """If [settings] is not a table of tables, it should be ignored."""
        plugin_toml = """
[plugin]
name = "weird"
version = "1.0.0"

[settings]
# a scalar here instead of sub-tables — should not crash
"""
        _write_plugin(tmp_path, "weird", plugin_toml)
        manifest = PluginManager._parse_manifest(
            tmp_path / "weird" / "plugin.toml",
            tmp_path / "weird",
        )
        assert manifest.settings_defs == ()


# =============================================================================
# Phase A — TestPluginContextSettings
# =============================================================================


class TestPluginContextSettings:
    @pytest.fixture
    def sample_defs(self) -> tuple[SettingDefinition, ...]:
        return (
            SettingDefinition.from_toml(
                "threshold",
                {"type": "int", "default": 5, "min": 0, "max": 10},
            ),
            SettingDefinition.from_toml(
                "api_secret",
                {"type": "string", "default": "", "secret": True},
            ),
            SettingDefinition.from_toml(
                "enabled",
                {"type": "bool", "default": True},
            ),
            SettingDefinition.from_toml(
                "service",
                {"type": "select", "options": ["spotify", "tidal"], "default": "spotify"},
            ),
        )

    def test_get_all_settings_returns_defaults(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        all_settings = ctx.get_all_settings()
        assert all_settings["threshold"] == 5
        assert all_settings["api_secret"] == ""
        assert all_settings["enabled"] is True
        assert all_settings["service"] == "spotify"

    def test_get_settings_definitions(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        defs = ctx.get_settings_definitions()
        assert len(defs) == 4
        assert all(isinstance(d, dict) for d in defs)
        keys = [d["key"] for d in defs]
        assert "threshold" in keys
        assert "api_secret" in keys

    def test_has_settings_true(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        assert ctx.has_settings is True

    def test_has_settings_false(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=())
        assert ctx.has_settings is False

    def test_set_settings_batch(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        changed = ctx.set_settings({"threshold": 8, "enabled": False})
        assert sorted(changed) == ["enabled", "threshold"]
        assert ctx.get_setting("threshold") == 8
        assert ctx.get_setting("enabled") is False

    def test_set_settings_returns_only_changed_keys(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        # Set threshold to the default — should not appear as changed
        changed = ctx.set_settings({"threshold": 5})
        assert changed == []

    def test_set_setting_validates(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        with pytest.raises(ValueError):
            ctx.set_setting("threshold", 999)

    def test_unknown_setting_get_raises(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=())
        with pytest.raises(KeyError):
            ctx.get_setting("missing")

    def test_unknown_setting_set_raises(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=())
        with pytest.raises(KeyError):
            ctx.set_setting("missing", "value")

    def test_mask_secret_short(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        ctx.set_setting("api_secret", "abc")
        masked = ctx.get_all_settings_masked()
        assert masked["api_secret"] == "***"
        assert "abc" not in masked["api_secret"]

    def test_mask_secret_long(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        ctx.set_setting("api_secret", "secret1234")
        masked = ctx.get_all_settings_masked()
        assert masked["api_secret"].endswith("1234")
        assert "secret" not in masked["api_secret"]

    def test_mask_secret_empty_string_not_masked(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        masked = ctx.get_all_settings_masked()
        # Empty string secret should stay empty (or remain as-is)
        assert masked["api_secret"] == "" or masked["api_secret"] == "****"

    def test_non_secret_not_masked(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        ctx.set_setting("threshold", 7)
        masked = ctx.get_all_settings_masked()
        assert masked["threshold"] == 7

    def test_persistence_roundtrip(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx1 = _make_ctx(tmp_path, settings_defs=sample_defs)
        ctx1.set_setting("threshold", 8)
        ctx1.set_setting("api_secret", "secret1234")
        ctx1.set_setting("service", "tidal")
        assert (tmp_path / "data" / "settings.json").is_file()

        ctx2 = _make_ctx(tmp_path, settings_defs=sample_defs)
        assert ctx2.get_setting("threshold") == 8
        assert ctx2.get_setting("api_secret") == "secret1234"
        assert ctx2.get_setting("service") == "tidal"
        assert ctx2.get_setting("enabled") is True  # unchanged default

    def test_set_select_valid(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        ctx.set_setting("service", "tidal")
        assert ctx.get_setting("service") == "tidal"

    def test_set_select_invalid_raises(
        self, tmp_path: Path, sample_defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=sample_defs)
        with pytest.raises(ValueError):
            ctx.set_setting("service", "deezer")


# =============================================================================
# Phase A — TestSettingsPersistence
# =============================================================================


class TestSettingsPersistence:
    @pytest.fixture
    def defs(self) -> tuple[SettingDefinition, ...]:
        return (
            SettingDefinition.from_toml(
                "count", {"type": "int", "default": 10, "min": 0, "max": 100}
            ),
            SettingDefinition.from_toml(
                "label", {"type": "string", "default": "hello"}
            ),
        )

    def test_corrupt_json_falls_back_to_defaults(
        self, tmp_path: Path, defs: tuple[SettingDefinition, ...]
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "settings.json").write_text("{corrupt json!!!", encoding="utf-8")
        ctx = _make_ctx(tmp_path, settings_defs=defs)
        assert ctx.get_setting("count") == 10
        assert ctx.get_setting("label") == "hello"

    def test_missing_settings_file_uses_defaults(
        self, tmp_path: Path, defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=defs)
        assert ctx.get_setting("count") == 10
        assert ctx.get_setting("label") == "hello"

    def test_atomic_write_creates_file(
        self, tmp_path: Path, defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=defs)
        ctx.set_setting("count", 42)
        settings_path = tmp_path / "data" / "settings.json"
        assert settings_path.is_file()
        # The .tmp file should not linger
        assert not settings_path.with_suffix(".tmp").exists()

    def test_version_field_in_saved_json(
        self, tmp_path: Path, defs: tuple[SettingDefinition, ...]
    ) -> None:
        ctx = _make_ctx(tmp_path, settings_defs=defs, plugin_version="2.5.0")
        ctx.set_setting("count", 1)
        settings_path = tmp_path / "data" / "settings.json"
        with open(settings_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["_version"] == 1
        assert payload["_plugin_version"] == "2.5.0"

    def test_partial_settings_loads_valid_only(
        self, tmp_path: Path, defs: tuple[SettingDefinition, ...]
    ) -> None:
        """If only some settings exist on disk, the rest use defaults."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "settings.json").write_text(
            json.dumps({"count": 42}), encoding="utf-8"
        )
        ctx = _make_ctx(tmp_path, settings_defs=defs)
        assert ctx.get_setting("count") == 42
        assert ctx.get_setting("label") == "hello"

    def test_invalid_value_in_file_uses_default(
        self, tmp_path: Path, defs: tuple[SettingDefinition, ...]
    ) -> None:
        """A value that fails validation should fall back to the default."""
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "settings.json").write_text(
            json.dumps({"count": 999, "label": "world"}), encoding="utf-8"
        )
        ctx = _make_ctx(tmp_path, settings_defs=defs)
        assert ctx.get_setting("count") == 10  # invalid → default
        assert ctx.get_setting("label") == "world"  # valid → kept

    def test_extra_keys_in_file_ignored(
        self, tmp_path: Path, defs: tuple[SettingDefinition, ...]
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "settings.json").write_text(
            json.dumps({"count": 5, "label": "ok", "unknown_key": "whatever"}),
            encoding="utf-8",
        )
        ctx = _make_ctx(tmp_path, settings_defs=defs)
        assert ctx.get_setting("count") == 5
        assert ctx.get_setting("label") == "ok"

    def test_save_creates_parent_dirs(self, tmp_path: Path, defs: tuple[SettingDefinition, ...]) -> None:
        deep_dir = tmp_path / "deep" / "nested" / "path"
        ctx = PluginContext(
            plugin_id="demo",
            event_bus=EventBus(),
            music_library=MagicMock(),
            player_registry=MagicMock(),
            data_dir=deep_dir,
            settings_defs=defs,
        )
        ctx.set_setting("count", 7)
        assert (deep_dir / "settings.json").is_file()

    def test_no_settings_file_for_empty_defs(self, tmp_path: Path) -> None:
        """Plugins with no settings should not create a settings file."""
        ctx = _make_ctx(tmp_path, settings_defs=())
        # No file should be created
        assert not (tmp_path / "data" / "settings.json").exists()


# =============================================================================
# Phase A — TestCmdPluginsettings (extended)
# =============================================================================


class _FakeCommandCtx:
    def __init__(self, manager: PluginManager) -> None:
        self.plugin_manager = manager
        self.plugin_installer = None
        self.plugin_repository = None


async def _setup_manager_with_plugin(
    tmp_path: Path,
    plugin_name: str,
    plugin_toml: str,
) -> PluginManager:
    """Helper to create a manager with a plugin ready for handler tests."""
    shutil.rmtree(Path("data/plugins") / plugin_name, ignore_errors=True)
    _write_plugin(tmp_path, plugin_name, plugin_toml)

    manager = PluginManager(
        core_plugins_dir=tmp_path,
        community_plugins_dir=tmp_path / "__none__",
        state_file=tmp_path / "plugin_states.json",
    )
    await manager.discover()
    await manager.load_all()
    await manager.start_all(
        event_bus=EventBus(),
        music_library=MagicMock(),
        player_registry=MagicMock(),
    )
    return manager


SETTINGS_PLUGIN_TOML = """
[plugin]
name = "{name}"
version = "1.0.0"

[settings.enabled]
type = "bool"
default = true

[settings.volume]
type = "int"
default = 50
min = 0
max = 100

[settings.secret_key]
type = "string"
default = ""
secret = true

[settings.service]
type = "select"
options = ["spotify", "tidal", "qobuz"]
default = "spotify"
"""


class TestCmdPluginsettings:
    @pytest.mark.asyncio
    async def test_get_and_set(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_1"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        fetched = await cmd_pluginsettings(ctx, ["pluginsettings", "get", plugin_name])
        assert fetched["plugin_name"] == plugin_name
        assert fetched["values"]["enabled"] is True

        updated = await cmd_pluginsettings(
            ctx,
            ["pluginsettings", "set", plugin_name, "enabled:false", "secret_key:abcd1234"],
        )
        assert sorted(updated["updated"]) == ["enabled", "secret_key"]
        assert updated["values"]["enabled"] is False
        assert updated["values"]["secret_key"] != "abcd1234"

    @pytest.mark.asyncio
    async def test_getdef_subcommand(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_getdef"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(ctx, ["pluginsettings", "getdef", plugin_name])
        assert "definitions" in result
        assert len(result["definitions"]) == 4
        keys = [d["key"] for d in result["definitions"]]
        assert "enabled" in keys
        assert "volume" in keys
        assert "secret_key" in keys
        assert "service" in keys

    @pytest.mark.asyncio
    async def test_set_invalid_value_returns_error(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_invalid"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name, "volume:999"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_validation_error_for_select(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_select_err"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name, "service:deezer"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_setting_key_returns_error(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_unknown_key"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name, "nonexistent:value"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_plugin_returns_error(self, tmp_path: Path) -> None:
        manager = PluginManager(
            core_plugins_dir=tmp_path,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "plugin_states.json",
        )
        ctx = _FakeCommandCtx(manager)
        result = await cmd_pluginsettings(ctx, ["pluginsettings", "get", "missing"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_args_returns_usage_error(self, tmp_path: Path) -> None:
        manager = PluginManager(
            core_plugins_dir=tmp_path,
            community_plugins_dir=tmp_path / "__none__",
            state_file=tmp_path / "plugin_states.json",
        )
        ctx = _FakeCommandCtx(manager)
        result = await cmd_pluginsettings(ctx, ["pluginsettings"])
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_bad_action"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "delete", plugin_name]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_bool_conversion(self, tmp_path: Path) -> None:
        """String 'true'/'false' should be converted to bool."""
        plugin_name = "cmd_settings_bool_conv"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name, "enabled:false"]
        )
        assert "error" not in result
        assert result["values"]["enabled"] is False

        result2 = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name, "enabled:true"]
        )
        assert result2["values"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_set_int_conversion(self, tmp_path: Path) -> None:
        """String '42' should be converted to int for int-typed settings."""
        plugin_name = "cmd_settings_int_conv"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name, "volume:75"]
        )
        assert "error" not in result
        assert result["values"]["volume"] == 75

    @pytest.mark.asyncio
    async def test_set_no_change_returns_empty_updated(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_no_change"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name, "volume:50"]
        )
        assert result["updated"] == []

    @pytest.mark.asyncio
    async def test_set_missing_colon_returns_error(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_bad_arg"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name, "volume_no_value"]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_set_multiple_keys_at_once(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_multi"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx,
            [
                "pluginsettings",
                "set",
                plugin_name,
                "volume:75",
                "enabled:false",
                "service:tidal",
            ],
        )
        assert "error" not in result
        assert sorted(result["updated"]) == ["enabled", "service", "volume"]
        assert result["values"]["volume"] == 75
        assert result["values"]["enabled"] is False
        assert result["values"]["service"] == "tidal"

    @pytest.mark.asyncio
    async def test_set_missing_key_value_args(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_no_kv"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(
            ctx, ["pluginsettings", "set", plugin_name]
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_includes_definitions(self, tmp_path: Path) -> None:
        plugin_name = "cmd_settings_defs_in_get"
        manager = await _setup_manager_with_plugin(
            tmp_path,
            plugin_name,
            SETTINGS_PLUGIN_TOML.format(name=plugin_name),
        )
        ctx = _FakeCommandCtx(manager)

        result = await cmd_pluginsettings(ctx, ["pluginsettings", "get", plugin_name])
        assert "definitions" in result
        assert len(result["definitions"]) == 4
