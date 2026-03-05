"""
Tests for raopbridge plugin SDUI integration (get_ui / handle_action).

Phase 2: Tests updated for Tabs layout, Form widgets, Device row-actions,
and new action handlers (save_settings, delete_device).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from resonance.ui import Page

# ---------------------------------------------------------------------------
# Load raopbridge module from community-repo via importlib
# ---------------------------------------------------------------------------

_RAOPBRIDGE_PKG_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "resonance-community-plugins-main"
    / "plugins"
    / "raopbridge"
)

# Ensure the raopbridge package directory is importable
_parent = str(_RAOPBRIDGE_PKG_DIR.parent)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import raopbridge as raopbridge_mod  # noqa: E402
from raopbridge.config import RaopCommonOptions, RaopDevice  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_settings(**overrides: Any) -> dict[str, Any]:
    """Return a realistic settings dict similar to RaopBridge.settings."""
    defaults = {
        "bin": "squeeze2raop-linux-x86_64",
        "interface": "127.0.0.1",
        "server": "127.0.0.1:9000",
        "active_at_startup": True,
        "config": "squeeze2raop.xml",
        "auto_save": True,
        "logging_enabled": True,
        "debug_enabled": False,
        "debug_category": "all",
        "debug_level": "info",
        "logging_file": "squeeze2raop.log",
        "pid_file": "squeeze2raop.pid",
    }
    defaults.update(overrides)
    return defaults


def _make_device(
    name: str = "Living Room",
    friendly_name: str = "LivingRoom-Speaker",
    mac: str = "aa:bb:cc:dd:ee:ff",
    udn: str = "AABBCCDDEE@Living Room._raop._tcp.local",
    enabled: bool = True,
) -> RaopDevice:
    return RaopDevice(
        udn=udn,
        name=name,
        friendly_name=friendly_name,
        mac=mac,
        enabled=enabled,
        common=RaopCommonOptions(),
    )


def _mock_bridge(
    is_active: bool = True,
    settings: dict[str, Any] | None = None,
    devices: list[RaopDevice] | None = None,
    common_options: RaopCommonOptions | None = None,
) -> MagicMock:
    """Create a mock RaopBridge with controllable state."""
    bridge = MagicMock()
    type(bridge).is_active = PropertyMock(return_value=is_active)
    type(bridge).settings = PropertyMock(
        return_value=settings or _make_settings()
    )
    type(bridge).data_dir = PropertyMock(return_value="/tmp/raopbridge")

    if devices is None:
        devices = [_make_device()]
    bridge.parse_devices = AsyncMock(return_value=devices)
    bridge.parse_common_options = AsyncMock(
        return_value=common_options if common_options is not None else RaopCommonOptions()
    )
    bridge.activate_bridge = AsyncMock()
    bridge.deactivate_bridge = MagicMock()
    bridge.remove_device = AsyncMock()
    bridge.save_device = AsyncMock()
    bridge.close = AsyncMock()

    return bridge


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.plugin_id = "raopbridge"
    return ctx


# ---------------------------------------------------------------------------
# Helpers to navigate the Tabs structure
# ---------------------------------------------------------------------------


def _get_page_dict(page: Page) -> dict[str, Any]:
    """Serialise a Page to dict."""
    return page.to_dict(plugin_id="raopbridge")


def _get_tabs(page_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the tabs list from the single Tabs component on the page."""
    assert len(page_dict["components"]) == 1
    tabs_comp = page_dict["components"][0]
    assert tabs_comp["type"] == "tabs"
    return tabs_comp["props"]["tabs"]


def _find_tab(tabs: list[dict], label: str) -> dict[str, Any]:
    """Find a tab by label."""
    for tab in tabs:
        if tab["label"] == label:
            return tab
    raise AssertionError(f"Tab '{label}' not found. Available: {[t['label'] for t in tabs]}")


def _find_component_by_type(children: list[dict], comp_type: str) -> dict[str, Any]:
    """Find the first component of a given type in a list of children."""
    for child in children:
        if child["type"] == comp_type:
            return child
    raise AssertionError(
        f"Component type '{comp_type}' not found. "
        f"Available: {[c['type'] for c in children]}"
    )


def _find_all_by_type(children: list[dict], comp_type: str) -> list[dict]:
    """Find all components of a given type (non-recursive)."""
    return [c for c in children if c["type"] == comp_type]


def _walk_components(components: list[dict]):
    """Yield every component dict in the tree (depth-first)."""
    for comp in components:
        yield comp
        if "children" in comp:
            yield from _walk_components(comp["children"])
        # Also walk into tabs children
        if comp.get("type") == "tabs":
            for tab in comp.get("props", {}).get("tabs", []):
                yield from _walk_components(tab.get("children", []))


# =============================================================================
# get_ui — basic page structure
# =============================================================================


class TestGetUI:
    """Tests for the raopbridge get_ui() SDUI page builder."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        """Ensure we always reset _raop_bridge after each test."""
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    @pytest.mark.asyncio
    async def test_returns_page_object(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        assert isinstance(page, Page)

    @pytest.mark.asyncio
    async def test_page_title_and_icon(self):
        raopbridge_mod._raop_bridge = _mock_bridge()
        page = await raopbridge_mod.get_ui(_make_ctx())
        assert page.title == "AirPlay Bridge"
        assert page.icon == "cast"

    @pytest.mark.asyncio
    async def test_page_has_refresh_interval(self):
        raopbridge_mod._raop_bridge = _mock_bridge()
        page = await raopbridge_mod.get_ui(_make_ctx())
        assert page.refresh_interval > 0

    @pytest.mark.asyncio
    async def test_serialises_to_valid_dict(self):
        raopbridge_mod._raop_bridge = _mock_bridge()
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        assert d["schema_version"] == "1.0"
        assert d["plugin_id"] == "raopbridge"
        assert d["title"] == "AirPlay Bridge"
        assert isinstance(d["components"], list)
        assert len(d["components"]) > 0


# =============================================================================
# Tabs structure
# =============================================================================


class TestTabsStructure:
    """Verify the page uses a Tabs layout with the expected tabs."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    @pytest.mark.asyncio
    async def test_page_has_single_tabs_component(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        assert len(d["components"]) == 1
        assert d["components"][0]["type"] == "tabs"

    @pytest.mark.asyncio
    async def test_tabs_has_five_tabs(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        assert len(tabs) == 6

    @pytest.mark.asyncio
    async def test_tab_labels(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        labels = [t["label"] for t in tabs]
        assert labels == ["Status", "Devices", "Settings", "Advanced", "Logs", "About"]

    @pytest.mark.asyncio
    async def test_tabs_have_icons(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        for tab in tabs:
            assert "icon" in tab, f"Tab '{tab['label']}' should have an icon"
            assert tab["icon"], f"Tab '{tab['label']}' icon should not be empty"

    @pytest.mark.asyncio
    async def test_inactive_bridge_also_has_tabs(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        assert d["components"][0]["type"] == "tabs"
        tabs = _get_tabs(d)
        assert len(tabs) == 6

    @pytest.mark.asyncio
    async def test_no_bridge_has_no_tabs(self):
        """Uninitialised bridge shows error alert + log card, not tabs."""
        raopbridge_mod._raop_bridge = None
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        assert len(d["components"]) == 2
        assert d["components"][0]["type"] == "alert"
        assert d["components"][0]["props"]["severity"] == "error"
        assert d["components"][1]["type"] == "card"


# =============================================================================
# Status tab
# =============================================================================


class TestStatusTab:
    """Tests for the Status tab content."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _status_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "Status")["children"]

    @pytest.mark.asyncio
    async def test_active_bridge_shows_green_status(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._status_children(d)
        status_card = _find_component_by_type(children, "card")
        assert status_card["props"]["title"] == "Bridge Status"
        # Find the status_badge inside the card
        badge = status_card["children"][0]["children"][0]
        assert badge["type"] == "status_badge"
        assert badge["props"]["color"] == "green"
        assert badge["props"]["label"] == "Active"

    @pytest.mark.asyncio
    async def test_inactive_bridge_shows_red_status(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._status_children(d)
        status_card = _find_component_by_type(children, "card")
        badge = status_card["children"][0]["children"][0]
        assert badge["props"]["color"] == "red"
        assert badge["props"]["label"] == "Inactive"

    @pytest.mark.asyncio
    async def test_active_bridge_shows_deactivate_and_restart(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._status_children(d)
        controls = _find_component_by_type(children, "row")
        button_actions = [
            child["props"]["action"] for child in controls["children"]
        ]
        assert "deactivate" in button_actions
        assert "restart" in button_actions

    @pytest.mark.asyncio
    async def test_inactive_bridge_shows_activate_button(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._status_children(d)
        controls = _find_component_by_type(children, "row")
        button_actions = [
            child["props"]["action"] for child in controls["children"]
        ]
        assert "activate" in button_actions
        assert "deactivate" not in button_actions
        assert "restart" not in button_actions

    @pytest.mark.asyncio
    async def test_status_card_key_value_items(self):
        settings = _make_settings(
            bin="squeeze2raop-win64",
            interface="192.168.1.10",
            server="192.168.1.1:9000",
            active_at_startup=False,
        )
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, settings=settings
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._status_children(d)
        status_card = _find_component_by_type(children, "card")
        kv = status_card["children"][1]
        assert kv["type"] == "key_value"
        items = {item["key"]: item["value"] for item in kv["props"]["items"]}
        assert items["Binary"] == "squeeze2raop-win64"
        assert items["Interface"] == "192.168.1.10"
        assert items["Server"] == "192.168.1.1:9000"
        assert items["Auto-start"] == "No"

    @pytest.mark.asyncio
    async def test_status_tab_has_card_and_row(self):
        """Status tab always contains a status card and a controls row."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._status_children(d)
        types = [c["type"] for c in children]
        assert "card" in types
        assert "row" in types


# =============================================================================
# Devices tab
# =============================================================================


class TestDevicesTab:
    """Tests for the Devices tab content."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _devices_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "Devices")["children"]

    @pytest.mark.asyncio
    async def test_active_bridge_shows_devices_table(self):
        devices = [
            _make_device(name="Kitchen", mac="11:22:33:44:55:66"),
            _make_device(name="Bedroom", mac="aa:bb:cc:dd:ee:ff"),
        ]
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=devices
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        assert table["props"]["title"] == "Detected AirPlay Devices"
        assert len(table["props"]["rows"]) == 2
        assert table["props"]["rows"][0]["name"] == "Kitchen"
        assert table["props"]["rows"][1]["name"] == "Bedroom"

    @pytest.mark.asyncio
    async def test_active_bridge_no_devices_shows_info_alert(self):
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=[]
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        alert = _find_component_by_type(children, "alert")
        assert alert["props"]["severity"] == "info"

    @pytest.mark.asyncio
    async def test_active_bridge_device_parse_error_shows_warning(self):
        bridge = _mock_bridge(is_active=True)
        bridge.parse_devices = AsyncMock(
            side_effect=RuntimeError("XML parse failed")
        )
        raopbridge_mod._raop_bridge = bridge
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        alert = _find_component_by_type(children, "alert")
        assert alert["props"]["severity"] == "warning"
        assert "XML parse failed" in alert["props"]["message"]

    @pytest.mark.asyncio
    async def test_inactive_bridge_shows_info_alert(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        alert = _find_component_by_type(children, "alert")
        assert alert["props"]["severity"] == "info"
        # No table should be present
        table_comps = _find_all_by_type(children, "table")
        assert len(table_comps) == 0

    @pytest.mark.asyncio
    async def test_device_row_has_correct_shape(self):
        device = _make_device(
            name="Bath Speaker",
            friendly_name="Bath-Speaker-12345",
            mac="de:ad:be:ef:00:01",
            udn="DEADBEEF@Bath._raop._tcp.local",
            enabled=False,
        )
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=[device]
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        row = table["props"]["rows"][0]
        assert row["name"] == "Bath Speaker"
        assert row["friendly_name"] == "Bath-Speaker-12345"
        assert row["mac"] == "de:ad:be:ef:00:01"
        assert row["udn"] == "DEADBEEF@Bath._raop._tcp.local"

    @pytest.mark.asyncio
    async def test_device_enabled_shown_as_badge(self):
        """Enabled field should be a badge dict with text and color."""
        device = _make_device(enabled=True)
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=[device]
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        row = table["props"]["rows"][0]
        assert isinstance(row["enabled"], dict)
        assert row["enabled"]["text"] == "Yes"
        assert row["enabled"]["color"] == "green"

    @pytest.mark.asyncio
    async def test_device_disabled_shown_as_red_badge(self):
        device = _make_device(enabled=False)
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=[device]
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        row = table["props"]["rows"][0]
        assert row["enabled"]["text"] == "No"
        assert row["enabled"]["color"] == "red"

    @pytest.mark.asyncio
    async def test_device_table_has_enabled_badge_column(self):
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=[_make_device()]
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        columns = table["props"]["columns"]
        enabled_col = next(c for c in columns if c["key"] == "enabled")
        assert enabled_col["variant"] == "badge"

    @pytest.mark.asyncio
    async def test_device_table_has_actions_column(self):
        """Table should have an actions column."""
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=[_make_device()]
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        columns = table["props"]["columns"]
        action_cols = [c for c in columns if c.get("variant") == "actions"]
        assert len(action_cols) == 1

    @pytest.mark.asyncio
    async def test_device_row_has_delete_action(self):
        """Each device row should have a delete action."""
        device = _make_device(udn="test-udn-123")
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=[device]
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        row = table["props"]["rows"][0]
        actions = row.get("actions", [])
        assert len(actions) >= 1
        delete_action = next(
            (a for a in actions if a["action"] == "delete_device"), None
        )
        assert delete_action is not None
        assert delete_action["params"]["udn"] == "test-udn-123"
        assert delete_action["style"] == "danger"
        assert delete_action["confirm"] is True

    @pytest.mark.asyncio
    async def test_table_columns_match_row_keys(self):
        """Table column keys should all be present in every row."""
        devices = [
            _make_device(),
            _make_device(name="Second", mac="00:11:22:33:44:55"),
        ]
        raopbridge_mod._raop_bridge = _mock_bridge(
            is_active=True, devices=devices
        )
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        col_keys = {col["key"] for col in table["props"]["columns"]}
        for row in table["props"]["rows"]:
            for key in col_keys:
                assert key in row, f"Row missing column key '{key}': {row}"


# =============================================================================
# Settings tab
# =============================================================================


class TestSettingsTab:
    """Tests for the Settings tab content with editable forms."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _settings_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "Settings")["children"]

    @pytest.mark.asyncio
    async def test_settings_tab_has_form(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        assert form["props"]["action"] == "save_settings"

    @pytest.mark.asyncio
    async def test_settings_form_has_submit_label(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        assert form["props"]["submit_label"] == "Save Settings"

    @pytest.mark.asyncio
    async def test_settings_form_disabled_when_active(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        assert form["props"].get("disabled") is True

    @pytest.mark.asyncio
    async def test_settings_form_not_disabled_when_inactive(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        assert "disabled" not in form["props"]

    @pytest.mark.asyncio
    async def test_settings_form_has_bin_select(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        with patch("raopbridge.bridge.define_valid_bin", return_value=["bin-a", "bin-b"]):
            page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        form_children = form["children"]
        select_comps = [c for c in form_children if c["type"] == "select"]
        bin_select = next((s for s in select_comps if s["props"]["name"] == "bin"), None)
        assert bin_select is not None
        option_values = [o["value"] for o in bin_select["props"]["options"]]
        assert "bin-a" in option_values
        assert "bin-b" in option_values

    @pytest.mark.asyncio
    async def test_settings_form_has_interface_input(self):
        settings = _make_settings(interface="192.168.1.50")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False, settings=settings)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        form_children = form["children"]
        iface_input = next(
            (c for c in form_children if c["type"] == "text_input" and c["props"]["name"] == "interface"),
            None,
        )
        assert iface_input is not None
        assert iface_input["props"]["value"] == "192.168.1.50"

    @pytest.mark.asyncio
    async def test_settings_form_has_server_input(self):
        settings = _make_settings(server="10.0.0.1:9000")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False, settings=settings)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        form_children = form["children"]
        server_input = next(
            (c for c in form_children if c["type"] == "text_input" and c["props"]["name"] == "server"),
            None,
        )
        assert server_input is not None
        assert server_input["props"]["value"] == "10.0.0.1:9000"

    @pytest.mark.asyncio
    async def test_settings_form_has_autostart_toggle(self):
        settings = _make_settings(active_at_startup=True)
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False, settings=settings)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        form_children = form["children"]
        toggle = next(
            (c for c in form_children if c["type"] == "toggle" and c["props"]["name"] == "active_at_startup"),
            None,
        )
        assert toggle is not None
        assert toggle["props"]["value"] is True

    @pytest.mark.asyncio
    async def test_settings_form_has_logging_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        form_children = form["children"]
        toggle = next(
            (c for c in form_children if c["type"] == "toggle" and c["props"]["name"] == "logging_enabled"),
            None,
        )
        assert toggle is not None

    @pytest.mark.asyncio
    async def test_settings_form_has_debug_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        form_children = form["children"]
        toggle = next(
            (c for c in form_children if c["type"] == "toggle" and c["props"]["name"] == "debug_enabled"),
            None,
        )
        assert toggle is not None

    @pytest.mark.asyncio
    async def test_settings_form_has_auto_save_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        form_children = form["children"]
        toggle = next(
            (c for c in form_children if c["type"] == "toggle" and c["props"]["name"] == "auto_save"),
            None,
        )
        assert toggle is not None

    @pytest.mark.asyncio
    async def test_settings_tab_has_config_info_card(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        card = _find_component_by_type(children, "card")
        assert card["props"]["title"] == "Configuration Info"

    @pytest.mark.asyncio
    async def test_settings_form_has_debug_category_select(self):
        """Debug Category select should be in the settings form."""
        settings = _make_settings(debug_category="slimproto")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False, settings=settings)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        cat_select = next(
            c for c in form["children"]
            if c["type"] == "select" and c["props"]["name"] == "debug_category"
        )
        assert cat_select["props"]["value"] == "slimproto"
        option_values = [o["value"] for o in cat_select["props"]["options"]]
        assert "all" in option_values
        assert "slimproto" in option_values
        assert "raop" in option_values

    @pytest.mark.asyncio
    async def test_settings_form_has_debug_level_select(self):
        """Debug Level select should be in the settings form."""
        settings = _make_settings(debug_level="debug")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False, settings=settings)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        lvl_select = next(
            c for c in form["children"]
            if c["type"] == "select" and c["props"]["name"] == "debug_level"
        )
        assert lvl_select["props"]["value"] == "debug"
        option_values = [o["value"] for o in lvl_select["props"]["options"]]
        assert "sdebug" in option_values
        assert "info" in option_values
        assert "error" in option_values

    @pytest.mark.asyncio
    async def test_debug_category_has_visible_when(self):
        """Debug Category select should only be visible when debug_enabled is True."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        cat_select = next(
            c for c in form["children"]
            if c["type"] == "select" and c["props"]["name"] == "debug_category"
        )
        assert "visible_when" in cat_select
        assert cat_select["visible_when"]["field"] == "debug_enabled"
        assert cat_select["visible_when"]["value"] is True

    @pytest.mark.asyncio
    async def test_debug_level_has_visible_when(self):
        """Debug Level select should only be visible when debug_enabled is True."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        lvl_select = next(
            c for c in form["children"]
            if c["type"] == "select" and c["props"]["name"] == "debug_level"
        )
        assert "visible_when" in lvl_select
        assert lvl_select["visible_when"]["field"] == "debug_enabled"
        assert lvl_select["visible_when"]["value"] is True

    @pytest.mark.asyncio
    async def test_non_debug_widgets_have_no_visible_when(self):
        """Non-debug form widgets should NOT have visible_when."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        non_debug_names = {"bin", "interface", "server", "active_at_startup",
                           "auto_save", "logging_enabled", "debug_enabled"}
        for child in form["children"]:
            name = child["props"].get("name")
            if name in non_debug_names:
                assert "visible_when" not in child, (
                    f"Widget '{name}' should not have visible_when"
                )

    @pytest.mark.asyncio
    async def test_settings_config_info_shows_readonly_fields(self):
        """Config info card should show PID File, Config File, Logging File."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        card = _find_component_by_type(children, "card")
        kv = card["children"][0]
        assert kv["type"] == "key_value"
        keys = [item["key"] for item in kv["props"]["items"]]
        assert "Config File" in keys
        assert "PID File" in keys
        assert "Logging File" in keys

    @pytest.mark.asyncio
    async def test_active_bridge_settings_tab_shows_info_alert(self):
        """When bridge is active, settings tab should show an info alert."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        alert = _find_component_by_type(children, "alert")
        assert alert["props"]["severity"] == "info"

    @pytest.mark.asyncio
    async def test_bin_select_includes_current_value(self):
        """Even if current bin is not in define_valid_bin(), it should appear in options."""
        settings = _make_settings(bin="custom-binary")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False, settings=settings)
        with patch("raopbridge.bridge.define_valid_bin", return_value=["bin-a"]):
            page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        bin_select = next(
            c for c in form["children"]
            if c["type"] == "select" and c["props"]["name"] == "bin"
        )
        option_values = [o["value"] for o in bin_select["props"]["options"]]
        assert "custom-binary" in option_values
        assert "bin-a" in option_values

    @pytest.mark.asyncio
    async def test_form_inputs_disabled_when_bridge_active(self):
        """Input widgets inside the form should be disabled when bridge is active.

        Note: bin, interface, server selects/inputs have explicit disabled=True.
        Debug category/level selects do NOT set disabled (they are always editable),
        so the form-level disabled prop handles them via the frontend formContext.
        """
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._settings_children(d)
        form = _find_component_by_type(children, "form")
        assert form["props"].get("disabled") is True
        # Explicitly disabled widgets (bin, interface, server)
        explicitly_disabled = {"bin", "interface", "server"}
        for child in form["children"]:
            if child["type"] in ("text_input", "select"):
                name = child["props"]["name"]
                if name in explicitly_disabled:
                    assert child["props"].get("disabled") is True, (
                        f"{child['type']} '{name}' should be disabled"
                    )


# =============================================================================
# handle_action — existing actions
# =============================================================================


class TestHandleAction:
    """Tests for the raopbridge handle_action() SDUI dispatcher."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    @pytest.mark.asyncio
    async def test_activate_calls_bridge(self):
        bridge = _mock_bridge(is_active=False)
        raopbridge_mod._raop_bridge = bridge
        with patch.object(
            raopbridge_mod,
            "_activate",
            new_callable=AsyncMock,
            return_value={"result": True},
        ) as mock_activate:
            result = await raopbridge_mod.handle_action("activate", {})
            mock_activate.assert_awaited_once()
            assert result == {"result": True}

    @pytest.mark.asyncio
    async def test_deactivate_calls_bridge(self):
        bridge = _mock_bridge(is_active=True)
        raopbridge_mod._raop_bridge = bridge
        with patch.object(
            raopbridge_mod,
            "_deactivate",
            new_callable=AsyncMock,
            return_value={"result": True},
        ) as mock_deactivate:
            result = await raopbridge_mod.handle_action("deactivate", {})
            mock_deactivate.assert_awaited_once()
            assert result == {"result": True}

    @pytest.mark.asyncio
    async def test_restart_calls_bridge(self):
        bridge = _mock_bridge(is_active=True)
        raopbridge_mod._raop_bridge = bridge
        with patch.object(
            raopbridge_mod,
            "_restart",
            new_callable=AsyncMock,
            return_value={"active": True},
        ) as mock_restart:
            result = await raopbridge_mod.handle_action("restart", {})
            mock_restart.assert_awaited_once()
            assert result == {"active": True}

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self):
        raopbridge_mod._raop_bridge = _mock_bridge()
        result = await raopbridge_mod.handle_action("explode", {})
        assert "error" in result
        assert "explode" in result["error"]

    @pytest.mark.asyncio
    async def test_no_bridge_returns_error(self):
        raopbridge_mod._raop_bridge = None
        result = await raopbridge_mod.handle_action("activate", {})
        assert "error" in result
        assert "not initialised" in result["error"]

    @pytest.mark.asyncio
    async def test_action_params_forwarded(self):
        """Ensure params dict is received (even if current actions don't use it)."""
        bridge = _mock_bridge(is_active=True)
        raopbridge_mod._raop_bridge = bridge
        with patch.object(
            raopbridge_mod,
            "_activate",
            new_callable=AsyncMock,
            return_value={"result": True},
        ):
            result = await raopbridge_mod.handle_action(
                "activate", {"extra": "data"}
            )
            assert result == {"result": True}


# =============================================================================
# handle_action — Phase 2 new actions
# =============================================================================


class TestHandleActionPhase2:
    """Tests for new Phase 2 actions: save_settings, delete_device."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    # -- save_settings -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_save_settings_dispatches(self):
        bridge = _mock_bridge(is_active=False)
        raopbridge_mod._raop_bridge = bridge
        with patch.object(
            raopbridge_mod,
            "_do_save_settings",
            return_value={"result": True},
        ) as mock_save:
            result = await raopbridge_mod.handle_action(
                "save_settings",
                {"interface": "10.0.0.1", "active_at_startup": True},
            )
            mock_save.assert_called_once()
            assert result.get("success") is True

    @pytest.mark.asyncio
    async def test_save_settings_empty_params_returns_error(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        result = await raopbridge_mod.handle_action("save_settings", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_save_settings_reports_save_errors(self):
        bridge = _mock_bridge(is_active=False)
        raopbridge_mod._raop_bridge = bridge
        with patch.object(
            raopbridge_mod,
            "_do_save_settings",
            return_value={"errors": "Invalid setting name: 'bogus'"},
        ):
            result = await raopbridge_mod.handle_action(
                "save_settings", {"bogus": "value"}
            )
            assert "error" in result

    @pytest.mark.asyncio
    async def test_save_settings_returns_success_message(self):
        bridge = _mock_bridge(is_active=False)
        raopbridge_mod._raop_bridge = bridge
        with patch.object(
            raopbridge_mod,
            "_do_save_settings",
            return_value={"result": True},
        ):
            result = await raopbridge_mod.handle_action(
                "save_settings", {"interface": "192.168.1.1"}
            )
            assert result.get("success") is True
            assert "message" in result

    # -- delete_device -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_delete_device_calls_remove(self):
        bridge = _mock_bridge(is_active=True)
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "delete_device", {"udn": "test-udn-123"}
        )
        bridge.remove_device.assert_awaited_once_with("test-udn-123")
        assert result.get("success") is True
        assert "message" in result

    @pytest.mark.asyncio
    async def test_delete_device_no_udn_returns_error(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        result = await raopbridge_mod.handle_action("delete_device", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_device_exception_returns_error(self):
        bridge = _mock_bridge(is_active=True)
        bridge.remove_device = AsyncMock(
            side_effect=RuntimeError("Device not found")
        )
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "delete_device", {"udn": "bad-udn"}
        )
        assert "error" in result
        assert "Device not found" in result["error"]


# =============================================================================
# plugin.toml integration
# =============================================================================


class TestPluginToml:
    """Verify the plugin.toml has the required [ui] section."""

    def test_toml_has_ui_section(self):
        import tomllib

        toml_path = _RAOPBRIDGE_PKG_DIR / "plugin.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        assert "ui" in data, "plugin.toml must have a [ui] section"
        ui = data["ui"]
        assert ui["enabled"] is True
        assert ui["sidebar_label"] == "AirPlay"
        assert ui["sidebar_icon"] == "cast"

    def test_toml_has_plugin_section(self):
        import tomllib

        toml_path = _RAOPBRIDGE_PKG_DIR / "plugin.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        assert "plugin" in data
        assert data["plugin"]["name"] == "raopbridge"


# =============================================================================
# Component tree structural sanity checks
# =============================================================================


class TestPageStructure:
    """High-level structural assertions about the generated page tree."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    @pytest.mark.asyncio
    async def test_all_components_have_type_and_props(self):
        """Every component in the tree must have 'type' and 'props'."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)

        for comp in _walk_components(d["components"]):
            assert "type" in comp, f"Component missing 'type': {comp}"
            assert "props" in comp, f"Component missing 'props': {comp}"

    @pytest.mark.asyncio
    async def test_button_styles_are_valid(self):
        """All buttons in the page must use valid style values."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        valid_styles = {"primary", "secondary", "danger"}

        for comp in _walk_components(d["components"]):
            if comp["type"] == "button":
                style = comp["props"].get("style", "secondary")
                assert style in valid_styles, f"Invalid button style '{style}'"

    @pytest.mark.asyncio
    async def test_status_badge_colors_are_valid(self):
        """All status badges must use valid color values."""
        for active in (True, False):
            raopbridge_mod._raop_bridge = _mock_bridge(is_active=active)
            page = await raopbridge_mod.get_ui(_make_ctx())
            d = _get_page_dict(page)
            valid_colors = {"green", "red", "yellow", "blue", "gray"}

            for comp in _walk_components(d["components"]):
                if comp["type"] == "status_badge":
                    color = comp["props"].get("color", "gray")
                    assert color in valid_colors, f"Invalid badge color '{color}'"

    @pytest.mark.asyncio
    async def test_form_widgets_have_name_prop(self):
        """All form input widgets must have a 'name' prop."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        input_types = {"text_input", "number_input", "select", "toggle"}

        for comp in _walk_components(d["components"]):
            if comp["type"] in input_types:
                assert "name" in comp["props"], (
                    f"{comp['type']} missing 'name' prop"
                )
                assert comp["props"]["name"], (
                    f"{comp['type']} has empty 'name' prop"
                )

    @pytest.mark.asyncio
    async def test_form_widgets_have_label_prop(self):
        """All form input widgets must have a 'label' prop."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        input_types = {"text_input", "number_input", "select", "toggle"}

        for comp in _walk_components(d["components"]):
            if comp["type"] in input_types:
                assert "label" in comp["props"], (
                    f"{comp['type']} '{comp['props'].get('name')}' missing 'label'"
                )

    @pytest.mark.asyncio
    async def test_select_widgets_have_options(self):
        """All select widgets must have an 'options' list."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        with patch("raopbridge.bridge.define_valid_bin", return_value=["bin-a"]):
            page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)

        for comp in _walk_components(d["components"]):
            if comp["type"] == "select":
                assert "options" in comp["props"]
                assert isinstance(comp["props"]["options"], list)
                for opt in comp["props"]["options"]:
                    assert "value" in opt
                    assert "label" in opt

    @pytest.mark.asyncio
    async def test_uninitialised_page_is_simple_error(self):
        """No bridge: error alert + log card, no tabs."""
        raopbridge_mod._raop_bridge = None
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        assert len(d["components"]) == 2
        assert d["components"][0]["type"] == "alert"
        assert d["components"][0]["props"]["severity"] == "error"
        assert d["components"][1]["type"] == "card"

    @pytest.mark.asyncio
    async def test_all_tab_children_are_non_empty(self):
        """Each tab should have at least one child component."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        for tab in tabs:
            assert len(tab["children"]) > 0, (
                f"Tab '{tab['label']}' has no children"
            )

    @pytest.mark.asyncio
    async def test_modal_widgets_have_required_props(self):
        """All modal widgets must have title and trigger_label props."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)

        for comp in _walk_components(d["components"]):
            if comp["type"] == "modal":
                assert "title" in comp["props"], "Modal missing 'title'"
                assert comp["props"]["title"], "Modal has empty 'title'"
                assert "trigger_label" in comp["props"], "Modal missing 'trigger_label'"
                assert comp["props"]["trigger_label"], "Modal has empty 'trigger_label'"
                assert comp["props"].get("size", "md") in {"sm", "md", "lg", "xl"}


# =============================================================================
# About tab (A1)
# =============================================================================


class TestAboutTab:
    """Tests for the About tab content."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _about_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "About")["children"]

    @pytest.mark.asyncio
    async def test_about_tab_exists(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        labels = [t["label"] for t in tabs]
        assert "About" in labels

    @pytest.mark.asyncio
    async def test_about_tab_has_icon(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        about = _find_tab(tabs, "About")
        assert about["icon"] == "info"

    @pytest.mark.asyncio
    async def test_about_tab_has_card_with_markdown(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._about_children(d)
        card = _find_component_by_type(children, "card")
        assert card["props"]["title"] == "About"
        md = _find_component_by_type(card["children"], "markdown")
        content = md["props"]["content"]
        assert "AirPlay Bridge" in content
        assert "squeeze2raop" in content

    @pytest.mark.asyncio
    async def test_about_tab_markdown_has_links(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._about_children(d)
        card = _find_component_by_type(children, "card")
        md = _find_component_by_type(card["children"], "markdown")
        content = md["props"]["content"]
        assert "philippe44" in content
        assert "https://github.com" in content

    @pytest.mark.asyncio
    async def test_about_tab_present_when_inactive(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        labels = [t["label"] for t in tabs]
        assert "About" in labels


# =============================================================================
# Advanced tab (B3)
# =============================================================================


class TestAdvancedTab:
    """Tests for the Advanced tab — common_options from bridge XML config."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _advanced_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "Advanced")["children"]

    @pytest.mark.asyncio
    async def test_advanced_tab_exists(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        labels = [t["label"] for t in tabs]
        assert "Advanced" in labels

    @pytest.mark.asyncio
    async def test_advanced_tab_has_icon(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        adv = _find_tab(tabs, "Advanced")
        assert adv["icon"] == "sliders-horizontal"

    @pytest.mark.asyncio
    async def test_advanced_inactive_shows_alert(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        alert = _find_component_by_type(children, "alert")
        assert alert["props"]["severity"] == "info"
        assert "Activate" in alert["props"]["message"]

    @pytest.mark.asyncio
    async def test_advanced_active_shows_card_with_kv(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        card = _find_component_by_type(children, "card")
        assert "Common Options" in card["props"]["title"]
        kv = _find_component_by_type(card["children"], "key_value")
        keys = [item["key"] for item in kv["props"]["items"]]
        assert "Stream Buffer Size" in keys
        assert "Sample Rate" in keys
        assert "Codecs" in keys
        assert "Volume Mode" in keys

    @pytest.mark.asyncio
    async def test_advanced_shows_all_common_option_fields(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        card = _find_component_by_type(children, "card")
        kv = _find_component_by_type(card["children"], "key_value")
        keys = [item["key"] for item in kv["props"]["items"]]
        expected_keys = [
            "Stream Buffer Size", "Output Size", "Enabled", "Codecs",
            "Sample Rate", "Volume Mode", "Volume Feedback",
            "Mute on Pause", "Send Metadata", "Send Cover Art",
            "Auto Play", "Idle Timeout", "Remove Timeout",
            "ALAC Encode", "Encryption", "Read Ahead", "Server",
        ]
        for key in expected_keys:
            assert key in keys, f"Advanced tab missing key: {key}"

    @pytest.mark.asyncio
    async def test_advanced_volume_mode_shows_label(self):
        """Volume mode should display a human-readable label, not raw int."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        card = _find_component_by_type(children, "card")
        kv = _find_component_by_type(card["children"], "key_value")
        vm_item = next(i for i in kv["props"]["items"] if i["key"] == "Volume Mode")
        assert vm_item["value"] in ("Hardware", "Software", "Ignored")

    @pytest.mark.asyncio
    async def test_advanced_booleans_show_yes_no(self):
        """Boolean fields should display 'Yes' or 'No', not True/False."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        card = _find_component_by_type(children, "card")
        kv = _find_component_by_type(card["children"], "key_value")
        enabled_item = next(i for i in kv["props"]["items"] if i["key"] == "Enabled")
        assert enabled_item["value"] in ("Yes", "No")

    @pytest.mark.asyncio
    async def test_advanced_card_has_description_text(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        card = _find_component_by_type(children, "card")
        text = _find_component_by_type(card["children"], "text")
        assert "read-only" in text["props"]["content"].lower() or "read" in text["props"]["content"].lower()

    @pytest.mark.asyncio
    async def test_advanced_parse_error_shows_warning(self):
        bridge = _mock_bridge(is_active=True)
        bridge.parse_common_options = AsyncMock(side_effect=Exception("XML broken"))
        raopbridge_mod._raop_bridge = bridge
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        alert = _find_component_by_type(children, "alert")
        assert alert["props"]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_advanced_no_config_shows_info(self):
        bridge = _mock_bridge(is_active=True)
        bridge.parse_common_options = AsyncMock(return_value=None)
        raopbridge_mod._raop_bridge = bridge
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        alert = _find_component_by_type(children, "alert")
        assert alert["props"]["severity"] == "info"


# =============================================================================
# Device toggle action (A5/A6)
# =============================================================================


class TestToggleDeviceAction:
    """Tests for the toggle_device action handler."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    @pytest.mark.asyncio
    async def test_toggle_device_enables(self):
        device = _make_device(enabled=False)
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "toggle_device", {"udn": device.udn, "enabled": True}
        )
        assert result["success"] is True
        assert "enabled" in result["message"]
        bridge.save_device.assert_awaited_once()
        saved = bridge.save_device.call_args[0][0]
        assert saved.enabled is True

    @pytest.mark.asyncio
    async def test_toggle_device_disables(self):
        device = _make_device(enabled=True)
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "toggle_device", {"udn": device.udn, "enabled": False}
        )
        assert result["success"] is True
        assert "disabled" in result["message"]
        saved = bridge.save_device.call_args[0][0]
        assert saved.enabled is False

    @pytest.mark.asyncio
    async def test_toggle_device_no_udn(self):
        raopbridge_mod._raop_bridge = _mock_bridge()
        result = await raopbridge_mod.handle_action("toggle_device", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_toggle_device_no_enabled_value(self):
        raopbridge_mod._raop_bridge = _mock_bridge()
        result = await raopbridge_mod.handle_action(
            "toggle_device", {"udn": "some-udn"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_toggle_device_not_found(self):
        bridge = _mock_bridge(is_active=True, devices=[])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "toggle_device", {"udn": "nonexistent", "enabled": True}
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_toggle_device_exception(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        bridge.save_device = AsyncMock(side_effect=Exception("disk full"))
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "toggle_device", {"udn": device.udn, "enabled": False}
        )
        assert "error" in result


# =============================================================================
# Device table row-actions (A5 — UI structure)
# =============================================================================


class TestDeviceRowActions:
    """Tests that device table rows have the correct toggle and delete actions."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _device_rows(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        devices_tab = _find_tab(tabs, "Devices")
        table = _find_component_by_type(devices_tab["children"], "table")
        return table["props"]["rows"]

    @pytest.mark.asyncio
    async def test_enabled_device_has_disable_action(self):
        device = _make_device(enabled=True)
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=[device])
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        rows = self._device_rows(d)
        actions = rows[0]["actions"]
        toggle_action = next(a for a in actions if a["action"] == "toggle_device")
        assert toggle_action["label"] == "Disable"
        assert toggle_action["params"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_disabled_device_has_enable_action(self):
        device = _make_device(enabled=False)
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=[device])
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        rows = self._device_rows(d)
        actions = rows[0]["actions"]
        toggle_action = next(a for a in actions if a["action"] == "toggle_device")
        assert toggle_action["label"] == "Enable"
        assert toggle_action["params"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_device_row_has_both_toggle_and_delete(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        rows = self._device_rows(d)
        actions = rows[0]["actions"]
        action_names = [a["action"] for a in actions]
        assert "toggle_device" in action_names
        assert "delete_device" in action_names


# =============================================================================
# Device settings modal (B1/B2)
# =============================================================================


class TestDeviceSettingsModal:
    """Tests for per-device settings modals in the Devices tab."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _devices_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "Devices")["children"]

    @pytest.mark.asyncio
    async def test_active_bridge_has_device_modals(self):
        devices = [_make_device(name="Speaker A"), _make_device(name="Speaker B", udn="BBB")]
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=devices)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modals = _find_all_by_type(children, "modal")
        assert len(modals) == 2

    @pytest.mark.asyncio
    async def test_device_modal_has_correct_title(self):
        device = _make_device(name="Living Room")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=[device])
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modal = _find_component_by_type(children, "modal")
        assert "Living Room" in modal["props"]["title"]

    @pytest.mark.asyncio
    async def test_device_modal_trigger_label_includes_name(self):
        device = _make_device(name="Kitchen")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=[device])
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modal = _find_component_by_type(children, "modal")
        assert "Kitchen" in modal["props"]["trigger_label"]

    @pytest.mark.asyncio
    async def test_device_modal_contains_form(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modal = _find_component_by_type(children, "modal")
        form = _find_component_by_type(modal["children"], "form")
        assert form["props"]["action"] == "update_device"

    @pytest.mark.asyncio
    async def test_device_modal_form_has_name_input(self):
        device = _make_device(name="Bedroom")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=[device])
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modal = _find_component_by_type(children, "modal")
        form = _find_component_by_type(modal["children"], "form")
        # Name input is now inside a Tabs > Tab ("General") structure
        name_input = next(
            c for c in _walk_components(form["children"])
            if c["type"] == "text_input" and c["props"].get("name") == "name"
        )
        assert name_input["props"]["value"] == "Bedroom"
        assert name_input["props"].get("required") is True

    @pytest.mark.asyncio
    async def test_device_modal_form_has_udn_readonly(self):
        device = _make_device()
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=[device])
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modal = _find_component_by_type(children, "modal")
        form = _find_component_by_type(modal["children"], "form")
        udn_input = next(
            c for c in form["children"]
            if c["type"] == "text_input" and c["props"]["name"] == "udn"
        )
        assert udn_input["props"].get("disabled") is True

    @pytest.mark.asyncio
    async def test_device_modal_form_has_volume_mode_select(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modal = _find_component_by_type(children, "modal")
        form = _find_component_by_type(modal["children"], "form")
        # Volume mode select is now inside a Tabs > Tab ("General") structure
        vol_select = next(
            c for c in _walk_components(form["children"])
            if c["type"] == "select" and c["props"].get("name") == "volume_mode"
        )
        option_values = [o["value"] for o in vol_select["props"]["options"]]
        assert "0" in option_values
        assert "1" in option_values
        assert "2" in option_values

    @pytest.mark.asyncio
    async def test_device_modal_form_has_device_info_kv(self):
        device = _make_device(mac="11:22:33:44:55:66")
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=[device])
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modal = _find_component_by_type(children, "modal")
        form = _find_component_by_type(modal["children"], "form")
        # KeyValue is now inside a Tabs > Tab ("General") structure
        kv = next(
            c for c in _walk_components(form["children"])
            if c["type"] == "key_value"
        )
        keys = [item["key"] for item in kv["props"]["items"]]
        assert "Friendly Name" in keys
        assert "MAC Address" in keys
        assert "Enabled" in keys

    @pytest.mark.asyncio
    async def test_no_modals_when_no_devices(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=[])
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modals = _find_all_by_type(children, "modal")
        assert len(modals) == 0

    @pytest.mark.asyncio
    async def test_no_modals_when_inactive(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=False)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        modals = _find_all_by_type(children, "modal")
        assert len(modals) == 0


# =============================================================================
# update_device action (B4)
# =============================================================================


class TestUpdateDeviceAction:
    """Tests for the update_device action handler."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    @pytest.mark.asyncio
    async def test_update_device_renames(self):
        device = _make_device(name="Old Name")
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "name": "New Name"}
        )
        assert result["success"] is True
        bridge.save_device.assert_awaited_once()
        saved = bridge.save_device.call_args[0][0]
        assert saved.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_device_changes_volume_mode(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "volume_mode": "1"}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.volume_mode == 1

    @pytest.mark.asyncio
    async def test_update_device_rename_and_volume_together(self):
        device = _make_device(name="Old")
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "name": "New", "volume_mode": "0"}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.name == "New"
        assert saved.common.volume_mode == 0

    @pytest.mark.asyncio
    async def test_update_device_no_udn(self):
        raopbridge_mod._raop_bridge = _mock_bridge()
        result = await raopbridge_mod.handle_action("update_device", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_device_not_found(self):
        bridge = _mock_bridge(is_active=True, devices=[])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": "nonexistent", "name": "X"}
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_update_device_invalid_volume_mode(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "volume_mode": "not_a_number"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_device_preserves_other_fields(self):
        """Updating name should not change mac, udn, friendly_name, enabled."""
        device = _make_device(
            name="Old", mac="aa:bb:cc:dd:ee:ff",
            friendly_name="FriendlyOld", enabled=True,
        )
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "name": "New"}
        )
        saved = bridge.save_device.call_args[0][0]
        assert saved.udn == device.udn
        assert saved.friendly_name == "FriendlyOld"
        assert saved.mac == "aa:bb:cc:dd:ee:ff"
        assert saved.enabled is True

    @pytest.mark.asyncio
    async def test_update_device_exception(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        bridge.save_device = AsyncMock(side_effect=Exception("write error"))
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "name": "New"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_device_message_contains_new_name(self):
        device = _make_device(name="Old")
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "name": "Küche"}
        )
        assert "Küche" in result["message"]

    # -- D1: Per-device advanced override tests ----------------------------

    @pytest.mark.asyncio
    async def test_update_device_changes_sample_rate(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "sample_rate": "44100"}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.sample_rate == 44100

    @pytest.mark.asyncio
    async def test_update_device_changes_idle_timeout(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "idle_timeout": "60"}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.idle_timeout == 60

    @pytest.mark.asyncio
    async def test_update_device_invalid_sample_rate(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "sample_rate": "not_int"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_device_changes_codecs(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "codecs": "aac, flc, mp3"}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.codecs == ["aac", "flc", "mp3"]

    @pytest.mark.asyncio
    async def test_update_device_codecs_as_list(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "codecs": ["wav", "pcm"]}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.codecs == ["wav", "pcm"]

    @pytest.mark.asyncio
    async def test_update_device_toggle_resample(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "resample": False}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.resample is False

    @pytest.mark.asyncio
    async def test_update_device_toggle_alac_encode(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "alac_encode": True}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.alac_encode is True

    @pytest.mark.asyncio
    async def test_update_device_toggle_encryption(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "encryption": True}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.encryption is True

    @pytest.mark.asyncio
    async def test_update_device_toggle_send_metadata(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "send_metadata": False}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.send_metadata is False

    @pytest.mark.asyncio
    async def test_update_device_toggle_send_coverart(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "send_coverart": False}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.send_coverart is False

    @pytest.mark.asyncio
    async def test_update_device_toggle_mute_on_pause(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "mute_on_pause": False}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.mute_on_pause is False

    @pytest.mark.asyncio
    async def test_update_device_toggle_auto_play(self):
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "auto_play": True}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.auto_play is True

    @pytest.mark.asyncio
    async def test_update_device_bool_from_string(self):
        """Boolean fields submitted as strings should be parsed correctly."""
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "resample": "false"}
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.resample is False

    @pytest.mark.asyncio
    async def test_update_device_multiple_advanced_overrides(self):
        """Multiple advanced fields can be updated in a single action."""
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        result = await raopbridge_mod.handle_action(
            "update_device",
            {
                "udn": device.udn,
                "name": "Updated Speaker",
                "volume_mode": "1",
                "sample_rate": "48000",
                "codecs": "aac, flc",
                "resample": False,
                "send_metadata": False,
                "idle_timeout": "120",
            },
        )
        assert result["success"] is True
        saved = bridge.save_device.call_args[0][0]
        assert saved.name == "Updated Speaker"
        assert saved.common.volume_mode == 1
        assert saved.common.sample_rate == 48000
        assert saved.common.codecs == ["aac", "flc"]
        assert saved.common.resample is False
        assert saved.common.send_metadata is False
        assert saved.common.idle_timeout == 120

    @pytest.mark.asyncio
    async def test_update_device_advanced_preserves_unchanged(self):
        """Fields not included in params should keep their original values."""
        device = _make_device()
        bridge = _mock_bridge(is_active=True, devices=[device])
        raopbridge_mod._raop_bridge = bridge
        original_common = device.common
        # Only update sample_rate, everything else should stay the same
        await raopbridge_mod.handle_action(
            "update_device", {"udn": device.udn, "sample_rate": "44100"}
        )
        saved = bridge.save_device.call_args[0][0]
        assert saved.common.sample_rate == 44100
        assert saved.common.resample == original_common.resample
        assert saved.common.codecs == original_common.codecs
        assert saved.common.send_metadata == original_common.send_metadata
        assert saved.common.volume_mode == original_common.volume_mode
        assert saved.common.idle_timeout == original_common.idle_timeout


# =============================================================================
# Device modal Advanced/Audio/Behaviour tabs (D1 UI)
# =============================================================================


class TestDeviceModalAdvancedTabs:
    """Tests for per-device advanced override tabs inside the device modal."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _get_modal_form_children(self, page_dict: dict) -> list[dict]:
        """Get all children from the first device modal's form (recursively)."""
        tabs = _get_tabs(page_dict)
        children = _find_tab(tabs, "Devices")["children"]
        modal = _find_component_by_type(children, "modal")
        form = _find_component_by_type(modal["children"], "form")
        return list(_walk_components(form["children"]))

    def _get_modal_inner_tabs(self, page_dict: dict) -> list[dict]:
        """Get the Tabs component inside the device modal form."""
        tabs = _get_tabs(page_dict)
        children = _find_tab(tabs, "Devices")["children"]
        modal = _find_component_by_type(children, "modal")
        form = _find_component_by_type(modal["children"], "form")
        tabs_comp = _find_component_by_type(form["children"], "tabs")
        return tabs_comp["props"]["tabs"]

    @pytest.mark.asyncio
    async def test_modal_has_three_tabs(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        labels = [t["label"] for t in inner_tabs]
        assert "General" in labels
        assert "Audio" in labels
        assert "Behaviour" in labels

    @pytest.mark.asyncio
    async def test_audio_tab_has_sample_rate_select(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        audio_tab = next(t for t in inner_tabs if t["label"] == "Audio")
        all_children = list(_walk_components(audio_tab["children"]))
        sample_rate = next(
            c for c in all_children
            if c["type"] == "select" and c["props"].get("name") == "sample_rate"
        )
        assert sample_rate is not None
        option_values = [o["value"] for o in sample_rate["props"]["options"]]
        assert "44100" in option_values
        assert "96000" in option_values

    @pytest.mark.asyncio
    async def test_audio_tab_has_codecs_input(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        audio_tab = next(t for t in inner_tabs if t["label"] == "Audio")
        all_children = list(_walk_components(audio_tab["children"]))
        codecs_input = next(
            c for c in all_children
            if c["type"] == "text_input" and c["props"].get("name") == "codecs"
        )
        assert codecs_input is not None
        # Should contain comma-separated codec string
        assert "," in codecs_input["props"]["value"]

    @pytest.mark.asyncio
    async def test_audio_tab_has_resample_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        audio_tab = next(t for t in inner_tabs if t["label"] == "Audio")
        all_children = list(_walk_components(audio_tab["children"]))
        resample = next(
            c for c in all_children
            if c["type"] == "toggle" and c["props"].get("name") == "resample"
        )
        assert resample is not None

    @pytest.mark.asyncio
    async def test_audio_tab_has_alac_encode_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        audio_tab = next(t for t in inner_tabs if t["label"] == "Audio")
        all_children = list(_walk_components(audio_tab["children"]))
        alac = next(
            c for c in all_children
            if c["type"] == "toggle" and c["props"].get("name") == "alac_encode"
        )
        assert alac is not None

    @pytest.mark.asyncio
    async def test_audio_tab_has_encryption_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        audio_tab = next(t for t in inner_tabs if t["label"] == "Audio")
        all_children = list(_walk_components(audio_tab["children"]))
        enc = next(
            c for c in all_children
            if c["type"] == "toggle" and c["props"].get("name") == "encryption"
        )
        assert enc is not None

    @pytest.mark.asyncio
    async def test_behaviour_tab_has_send_metadata_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        behaviour_tab = next(t for t in inner_tabs if t["label"] == "Behaviour")
        all_children = list(_walk_components(behaviour_tab["children"]))
        meta = next(
            c for c in all_children
            if c["type"] == "toggle" and c["props"].get("name") == "send_metadata"
        )
        assert meta is not None

    @pytest.mark.asyncio
    async def test_behaviour_tab_has_send_coverart_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        behaviour_tab = next(t for t in inner_tabs if t["label"] == "Behaviour")
        all_children = list(_walk_components(behaviour_tab["children"]))
        coverart = next(
            c for c in all_children
            if c["type"] == "toggle" and c["props"].get("name") == "send_coverart"
        )
        assert coverart is not None

    @pytest.mark.asyncio
    async def test_behaviour_tab_has_mute_on_pause_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        behaviour_tab = next(t for t in inner_tabs if t["label"] == "Behaviour")
        all_children = list(_walk_components(behaviour_tab["children"]))
        mute = next(
            c for c in all_children
            if c["type"] == "toggle" and c["props"].get("name") == "mute_on_pause"
        )
        assert mute is not None

    @pytest.mark.asyncio
    async def test_behaviour_tab_has_auto_play_toggle(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        behaviour_tab = next(t for t in inner_tabs if t["label"] == "Behaviour")
        all_children = list(_walk_components(behaviour_tab["children"]))
        auto = next(
            c for c in all_children
            if c["type"] == "toggle" and c["props"].get("name") == "auto_play"
        )
        assert auto is not None

    @pytest.mark.asyncio
    async def test_behaviour_tab_has_idle_timeout_input(self):
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        inner_tabs = self._get_modal_inner_tabs(d)
        behaviour_tab = next(t for t in inner_tabs if t["label"] == "Behaviour")
        all_children = list(_walk_components(behaviour_tab["children"]))
        timeout = next(
            c for c in all_children
            if c["type"] == "number_input" and c["props"].get("name") == "idle_timeout"
        )
        assert timeout is not None
        assert timeout["props"].get("min") == 0
        assert timeout["props"].get("max") == 3600

    @pytest.mark.asyncio
    async def test_modal_size_is_lg(self):
        """Modal should be 'lg' to accommodate the tabbed layout."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        children = _find_tab(tabs, "Devices")["children"]
        modal = _find_component_by_type(children, "modal")
        assert modal["props"]["size"] == "lg"


# =============================================================================
# Modal widget backend model (C1)
# =============================================================================


class TestModalWidgetModel:
    """Tests for the Modal UIComponent backend model."""

    def test_modal_basic(self):
        from resonance.ui import Modal
        m = Modal(title="Test", trigger_label="Open")
        d = m.to_dict()
        assert d["type"] == "modal"
        assert d["props"]["title"] == "Test"
        assert d["props"]["trigger_label"] == "Open"
        assert d["props"]["trigger_style"] == "secondary"
        assert d["props"]["size"] == "md"

    def test_modal_with_children(self):
        from resonance.ui import Modal, Text
        m = Modal(
            title="Details",
            trigger_label="View",
            children=[Text("Hello")],
        )
        d = m.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["type"] == "text"

    def test_modal_custom_style_and_size(self):
        from resonance.ui import Modal
        m = Modal(
            title="Delete?",
            trigger_label="Delete",
            trigger_style="danger",
            size="lg",
        )
        d = m.to_dict()
        assert d["props"]["trigger_style"] == "danger"
        assert d["props"]["size"] == "lg"

    def test_modal_with_icon(self):
        from resonance.ui import Modal
        m = Modal(title="T", trigger_label="L", trigger_icon="settings")
        d = m.to_dict()
        assert d["props"]["trigger_icon"] == "settings"

    def test_modal_no_icon_omitted(self):
        from resonance.ui import Modal
        m = Modal(title="T", trigger_label="L")
        d = m.to_dict()
        assert "trigger_icon" not in d["props"]

    def test_modal_empty_title_raises(self):
        from resonance.ui import Modal
        with pytest.raises(ValueError, match="title"):
            Modal(title="", trigger_label="Open")

    def test_modal_empty_trigger_label_raises(self):
        from resonance.ui import Modal
        with pytest.raises(ValueError, match="trigger_label"):
            Modal(title="T", trigger_label="")

    def test_modal_invalid_style_raises(self):
        from resonance.ui import Modal
        with pytest.raises(ValueError, match="trigger_style"):
            Modal(title="T", trigger_label="L", trigger_style="invalid")

    def test_modal_invalid_size_raises(self):
        from resonance.ui import Modal
        with pytest.raises(ValueError, match="size"):
            Modal(title="T", trigger_label="L", size="xxl")

    def test_modal_all_valid_sizes(self):
        from resonance.ui import Modal
        for size in ("sm", "md", "lg", "xl"):
            m = Modal(title="T", trigger_label="L", size=size)
            assert m.to_dict()["props"]["size"] == size

    def test_modal_all_valid_styles(self):
        from resonance.ui import Modal
        for style in ("primary", "secondary", "danger"):
            m = Modal(title="T", trigger_label="L", trigger_style=style)
            assert m.to_dict()["props"]["trigger_style"] == style

    def test_modal_type_in_allowed_types(self):
        from resonance.ui import ALLOWED_TYPES
        assert "modal" in ALLOWED_TYPES

    def test_modal_with_form_child(self):
        """Modal containing a Form — the typical device-settings pattern."""
        from resonance.ui import Form, Modal, Select, SelectOption, TextInput
        m = Modal(
            title="Device Settings",
            trigger_label="Edit",
            size="md",
            children=[
                Form(
                    action="update_device",
                    submit_label="Save",
                    children=[
                        TextInput(name="name", label="Name", value="Speaker"),
                        Select(
                            name="volume_mode",
                            label="Volume",
                            value="2",
                            options=[
                                SelectOption(value="0", label="Ignored"),
                                SelectOption(value="1", label="Software"),
                                SelectOption(value="2", label="Hardware"),
                            ],
                        ),
                    ],
                ),
            ],
        )
        d = m.to_dict()
        assert d["type"] == "modal"
        form = d["children"][0]
        assert form["type"] == "form"
        assert form["props"]["action"] == "update_device"
        assert len(form["children"]) == 2


# =============================================================================
# UX Polish — Collapsible Cards, Inline-Editable Table
# =============================================================================


class TestUXPolish:
    """Tests for UX Polish features: collapsible cards, inline-editable table columns."""

    @pytest.fixture(autouse=True)
    def _patch_bridge(self):
        original = raopbridge_mod._raop_bridge
        yield
        raopbridge_mod._raop_bridge = original

    def _devices_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "Devices")["children"]

    def _advanced_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "Advanced")["children"]

    def _about_children(self, page_dict: dict) -> list[dict]:
        tabs = _get_tabs(page_dict)
        return _find_tab(tabs, "About")["children"]

    # -- Inline-Editable Device Name Column -----------------------------------

    @pytest.mark.asyncio
    async def test_device_table_has_edit_action(self):
        """Device table should have edit_action='update_device' for inline rename."""
        devices = [_make_device(name="Kitchen")]
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=devices)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        assert table["props"]["edit_action"] == "update_device"

    @pytest.mark.asyncio
    async def test_device_table_has_row_key_udn(self):
        """Device table row_key should be 'udn' to identify devices."""
        devices = [_make_device(name="Kitchen")]
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=devices)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        assert table["props"]["row_key"] == "udn"

    @pytest.mark.asyncio
    async def test_device_name_column_is_editable(self):
        """The 'name' column should have variant='editable'."""
        devices = [_make_device(name="Kitchen")]
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=devices)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        name_col = next(
            c for c in table["props"]["columns"] if c["key"] == "name"
        )
        assert name_col["variant"] == "editable"

    @pytest.mark.asyncio
    async def test_device_non_name_columns_not_editable(self):
        """Other columns (friendly_name, mac) should NOT be editable."""
        devices = [_make_device(name="Kitchen")]
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=devices)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        for col in table["props"]["columns"]:
            if col["key"] in ("friendly_name", "mac"):
                assert col.get("variant", "text") == "text", (
                    f"Column '{col['key']}' should not be editable"
                )

    @pytest.mark.asyncio
    async def test_device_rows_have_udn_for_row_key(self):
        """Each device row must include the udn field used as row_key."""
        devices = [
            _make_device(name="A", udn="udn-a"),
            _make_device(name="B", udn="udn-b"),
        ]
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=devices)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        for row in table["props"]["rows"]:
            assert "udn" in row, "Each row must contain the 'udn' key"

    @pytest.mark.asyncio
    async def test_inline_rename_dispatches_update_device(self):
        """Inline rename should use 'update_device' which already handles name changes."""
        devices = [_make_device(name="Old Name", udn="udn-1")]
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True, devices=devices)
        # The existing _handle_update_device already supports renaming via
        # params={"udn": ..., "name": ...}.  We just verify the table edit_action
        # matches the same action so the existing handler is reused.
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._devices_children(d)
        table = _find_component_by_type(children, "table")
        assert table["props"]["edit_action"] == "update_device"
        # The handler is the same one used by the modal form
        result = await raopbridge_mod.handle_action(
            "update_device", {"udn": "udn-1", "name": "New Name"}
        )
        assert result.get("success") is True

    # -- Collapsible Advanced Card --------------------------------------------

    @pytest.mark.asyncio
    async def test_advanced_card_is_collapsible(self):
        """Advanced tab common options card should be collapsible."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        card = _find_component_by_type(children, "card")
        assert card["props"].get("collapsible") is True

    @pytest.mark.asyncio
    async def test_advanced_card_starts_collapsed(self):
        """Advanced common options card should start collapsed by default."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._advanced_children(d)
        card = _find_component_by_type(children, "card")
        assert card["props"].get("collapsed") is True

    # -- Collapsible About Card -----------------------------------------------

    @pytest.mark.asyncio
    async def test_about_card_is_collapsible(self):
        """About tab card should be collapsible."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._about_children(d)
        card = _find_component_by_type(children, "card")
        assert card["props"].get("collapsible") is True

    @pytest.mark.asyncio
    async def test_about_card_starts_expanded(self):
        """About tab card should start expanded (not collapsed)."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        children = self._about_children(d)
        card = _find_component_by_type(children, "card")
        assert card["props"].get("collapsed") is not True

    # -- Status Card is NOT collapsible (important info) ----------------------

    @pytest.mark.asyncio
    async def test_status_card_not_collapsible(self):
        """Status tab card should NOT be collapsible — it shows critical info."""
        raopbridge_mod._raop_bridge = _mock_bridge(is_active=True)
        page = await raopbridge_mod.get_ui(_make_ctx())
        d = _get_page_dict(page)
        tabs = _get_tabs(d)
        status_children = _find_tab(tabs, "Status")["children"]
        card = _find_component_by_type(status_children, "card")
        assert card["props"].get("collapsible") is not True
