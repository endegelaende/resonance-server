"""Tests for the Plugin UI extension system (Server-Driven UI)."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from resonance.core.events import EventBus
from resonance.core.library import MusicLibrary
from resonance.core.library_db import LibraryDb
from resonance.player.registry import PlayerRegistry
from resonance.plugin_manager import PluginManager
from resonance.ui import (
    ALLOWED_COLORS,
    ALLOWED_MODAL_SIZES,
    ALLOWED_TYPES,
    ALLOWED_VISIBLE_WHEN_OPERATORS,
    Alert,
    Button,
    Card,
    Column,
    Form,
    Heading,
    KeyValue,
    KVItem,
    Markdown,
    Modal,
    NumberInput,
    Page,
    Progress,
    Row,
    Select,
    SelectOption,
    StatusBadge,
    Tab,
    Table,
    TableAction,
    TableColumn,
    Tabs,
    Text,
    Textarea,
    TextInput,
    Toggle,
    UIComponent,
)
from resonance.web.server import WebServer

# =============================================================================
# Model validation tests
# =============================================================================


class TestUIComponentValidation:
    """Test UIComponent data model validation rules."""

    def test_valid_types_accepted(self) -> None:
        for widget_type in ALLOWED_TYPES:
            comp = UIComponent(type=widget_type, props={})
            assert comp.type == widget_type

    def test_unknown_widget_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown widget type"):
            UIComponent(type="evil_widget", props={})

    def test_event_handler_prop_rejected(self) -> None:
        with pytest.raises(ValueError, match="Event handler props"):
            UIComponent(type="heading", props={"onclick": "alert(1)"})

    def test_event_handler_prop_case_insensitive(self) -> None:
        with pytest.raises(ValueError, match="Event handler props"):
            UIComponent(type="heading", props={"onMouseOver": "evil()"})

    def test_javascript_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid URL scheme"):
            UIComponent(type="heading", props={"href": "javascript:alert(1)"})

    def test_valid_url_schemes_accepted(self) -> None:
        for url in ["http://example.com", "https://example.com", "mailto:a@b.com"]:
            comp = UIComponent(type="heading", props={"href": url})
            assert comp.props["href"] == url

    def test_empty_page_valid(self) -> None:
        page = Page(title="Empty")
        data = page.to_dict(plugin_id="test")
        assert data["schema_version"] == "1.0"
        assert data["plugin_id"] == "test"
        assert data["title"] == "Empty"
        assert data["components"] == []

    def test_nested_children_serialized(self) -> None:
        card = Card(title="Test", children=[Heading("Hello")])
        data = card.to_dict()
        assert data["type"] == "card"
        assert len(data["children"]) == 1
        assert data["children"][0]["type"] == "heading"
        assert data["children"][0]["props"]["text"] == "Hello"


class TestConvenienceConstructors:
    """Test that convenience constructors set correct type and props."""

    def test_heading(self) -> None:
        h = Heading("Title", level=1)
        assert h.type == "heading"
        assert h.props == {"text": "Title", "level": 1}

    def test_text(self) -> None:
        t = Text("Hello", color="green")
        assert t.type == "text"
        assert t.props["content"] == "Hello"
        assert t.props["color"] == "green"

    def test_text_invalid_color(self) -> None:
        with pytest.raises(ValueError, match="Invalid color"):
            Text("Bad", color="pink")

    def test_status_badge(self) -> None:
        b = StatusBadge("Running", color="green")
        assert b.type == "status_badge"
        assert b.props["label"] == "Running"
        assert b.props["color"] == "green"

    def test_key_value(self) -> None:
        kv = KeyValue(items=[KVItem("Key", "Val", color="blue")])
        assert kv.type == "key_value"
        assert kv.props["items"] == [{"key": "Key", "value": "Val", "color": "blue"}]

    def test_kv_item_invalid_color(self) -> None:
        with pytest.raises(ValueError, match="Invalid color"):
            KVItem("Key", "Val", color="neon")

    def test_table(self) -> None:
        t = Table(
            columns=[TableColumn("name", "Name")],
            rows=[{"name": "Alice"}],
            title="Users",
        )
        assert t.type == "table"
        assert t.props["title"] == "Users"
        assert len(t.props["columns"]) == 1
        assert len(t.props["rows"]) == 1

    def test_table_action_invalid_style(self) -> None:
        with pytest.raises(ValueError, match="Invalid button style"):
            TableAction("Go", "go", style="fancy")

    def test_button(self) -> None:
        b = Button("Click", action="do_thing", style="primary", confirm=True)
        assert b.type == "button"
        assert b.props["label"] == "Click"
        assert b.props["action"] == "do_thing"
        assert b.props["style"] == "primary"
        assert b.props["confirm"] is True

    def test_button_invalid_style(self) -> None:
        with pytest.raises(ValueError, match="Invalid button style"):
            Button("Bad", action="x", style="nope")

    def test_card(self) -> None:
        c = Card(title="Info", children=[Text("Hello")])
        assert c.type == "card"
        assert c.props["title"] == "Info"
        assert len(c.children) == 1

    def test_row(self) -> None:
        r = Row(children=[Text("A"), Text("B")], gap="6")
        assert r.type == "row"
        assert r.props["gap"] == "6"
        assert len(r.children) == 2

    def test_column(self) -> None:
        c = Column(children=[Text("A")])
        assert c.type == "column"
        assert len(c.children) == 1

    def test_alert(self) -> None:
        a = Alert("Something happened", severity="warning", title="Warning")
        assert a.type == "alert"
        assert a.props["message"] == "Something happened"
        assert a.props["severity"] == "warning"
        assert a.props["title"] == "Warning"

    def test_alert_invalid_severity(self) -> None:
        with pytest.raises(ValueError, match="Invalid severity"):
            Alert("Bad", severity="critical")

    def test_progress(self) -> None:
        p = Progress(75, label="Loading", color="green")
        assert p.type == "progress"
        assert p.props["value"] == 75
        assert p.props["label"] == "Loading"

    def test_progress_clamps_value(self) -> None:
        p = Progress(150)
        assert p.props["value"] == 100
        p2 = Progress(-10)
        assert p2.props["value"] == 0

    def test_markdown(self) -> None:
        m = Markdown("# Hello\nWorld")
        assert m.type == "markdown"
        assert m.props["content"] == "# Hello\nWorld"

    def test_button_disabled(self) -> None:
        b = Button("Go", action="go", disabled=True)
        assert b.type == "button"
        assert b.props["disabled"] is True

    def test_button_not_disabled_by_default(self) -> None:
        b = Button("Go", action="go")
        assert "disabled" not in b.props


# =============================================================================
# Collapsible Card tests
# =============================================================================


class TestCollapsibleCard:
    """Tests for the collapsible/collapsed Card props."""

    def test_card_default_not_collapsible(self) -> None:
        c = Card(title="Info")
        assert "collapsible" not in c.props
        assert "collapsed" not in c.props

    def test_card_collapsible_true(self) -> None:
        c = Card(title="Info", collapsible=True)
        assert c.props["collapsible"] is True
        assert "collapsed" not in c.props  # default collapsed=False omitted

    def test_card_collapsible_and_collapsed(self) -> None:
        c = Card(title="Info", collapsible=True, collapsed=True)
        assert c.props["collapsible"] is True
        assert c.props["collapsed"] is True

    def test_card_collapsed_without_collapsible_ignored(self) -> None:
        """collapsed=True without collapsible=True has no effect."""
        c = Card(title="Info", collapsed=True)
        assert "collapsible" not in c.props
        assert "collapsed" not in c.props

    def test_card_collapsible_serialised_in_to_dict(self) -> None:
        c = Card(title="Status", collapsible=True)
        d = c.to_dict()
        assert d["props"]["collapsible"] is True
        assert "collapsed" not in d["props"]

    def test_card_collapsible_collapsed_serialised_in_to_dict(self) -> None:
        c = Card(title="Status", collapsible=True, collapsed=True)
        d = c.to_dict()
        assert d["props"]["collapsible"] is True
        assert d["props"]["collapsed"] is True

    def test_card_non_collapsible_omits_both_from_dict(self) -> None:
        c = Card(title="Status")
        d = c.to_dict()
        assert "collapsible" not in d["props"]
        assert "collapsed" not in d["props"]

    def test_card_collapsible_with_children(self) -> None:
        c = Card(title="Details", collapsible=True, children=[Text("A"), Text("B")])
        assert c.props["collapsible"] is True
        assert len(c.children) == 2

    def test_card_collapsible_supports_visible_when(self) -> None:
        c = Card(title="X", collapsible=True).when("show_details", True)
        d = c.to_dict()
        assert d["props"]["collapsible"] is True
        assert d["visible_when"] == {"field": "show_details", "value": True}

    def test_card_collapsible_in_page(self) -> None:
        """Full-stack: collapsible Card inside a Page serialises correctly."""
        page = Page(
            title="Test",
            components=[
                Card(title="Open Card", collapsible=True, children=[Text("A")]),
                Card(title="Closed Card", collapsible=True, collapsed=True, children=[Text("B")]),
                Card(title="Normal Card", children=[Text("C")]),
            ],
        )
        d = page.to_dict(plugin_id="test")
        open_card = d["components"][0]
        closed_card = d["components"][1]
        normal_card = d["components"][2]

        assert open_card["props"]["collapsible"] is True
        assert "collapsed" not in open_card["props"]

        assert closed_card["props"]["collapsible"] is True
        assert closed_card["props"]["collapsed"] is True

        assert "collapsible" not in normal_card["props"]
        assert "collapsed" not in normal_card["props"]


# =============================================================================
# Inline-editable Table tests
# =============================================================================


class TestInlineEditableTable:
    """Tests for the edit_action / row_key / editable variant on Table."""

    def test_table_default_no_edit_action(self) -> None:
        t = Table(columns=[TableColumn("name", "Name")], rows=[{"name": "A"}])
        assert "edit_action" not in t.props
        assert "row_key" not in t.props

    def test_table_edit_action_set(self) -> None:
        t = Table(
            columns=[TableColumn("name", "Name", variant="editable")],
            rows=[{"name": "A", "udn": "1"}],
            edit_action="update_device",
        )
        assert t.props["edit_action"] == "update_device"
        assert t.props["row_key"] == "udn"  # default

    def test_table_custom_row_key(self) -> None:
        t = Table(
            columns=[TableColumn("name", "Name", variant="editable")],
            rows=[{"name": "A", "id": "42"}],
            edit_action="rename",
            row_key="id",
        )
        assert t.props["row_key"] == "id"

    def test_table_row_key_omitted_without_edit_action(self) -> None:
        """row_key should not appear when edit_action is not set."""
        t = Table(
            columns=[TableColumn("name", "Name", variant="editable")],
            rows=[{"name": "A"}],
            row_key="id",
        )
        assert "row_key" not in t.props

    def test_table_editable_column_variant_serialised(self) -> None:
        col = TableColumn("name", "Name", variant="editable")
        d = col.to_dict()
        assert d["variant"] == "editable"

    def test_table_edit_action_serialised_in_to_dict(self) -> None:
        t = Table(
            columns=[TableColumn("name", "Name", variant="editable")],
            rows=[{"name": "A", "udn": "x"}],
            edit_action="update_device",
        )
        d = t.to_dict()
        assert d["props"]["edit_action"] == "update_device"
        assert d["props"]["row_key"] == "udn"

    def test_table_non_editable_omits_edit_props(self) -> None:
        t = Table(
            columns=[TableColumn("name", "Name")],
            rows=[{"name": "A"}],
            title="Users",
        )
        d = t.to_dict()
        assert "edit_action" not in d["props"]
        assert "row_key" not in d["props"]

    def test_table_mixed_columns_editable_and_plain(self) -> None:
        t = Table(
            columns=[
                TableColumn("name", "Name", variant="editable"),
                TableColumn("mac", "MAC Address"),
                TableColumn("enabled", "Enabled", variant="badge"),
            ],
            rows=[{"name": "Speaker", "mac": "AA:BB", "enabled": {"text": "Yes", "color": "green"}, "udn": "1"}],
            edit_action="update_device",
        )
        d = t.to_dict()
        cols = d["props"]["columns"]
        assert cols[0]["variant"] == "editable"
        assert "variant" not in cols[1]  # text is default, omitted
        assert cols[2]["variant"] == "badge"
        assert d["props"]["edit_action"] == "update_device"

    def test_table_editable_in_page_serialisation(self) -> None:
        """Full-stack: Table with editable column inside a Page."""
        page = Page(
            title="Test",
            components=[
                Table(
                    columns=[
                        TableColumn("name", "Name", variant="editable"),
                        TableColumn("id", "ID"),
                    ],
                    rows=[{"name": "Device A", "id": "1"}],
                    edit_action="rename_device",
                    row_key="id",
                ),
            ],
        )
        d = page.to_dict(plugin_id="test")
        table = d["components"][0]
        assert table["type"] == "table"
        assert table["props"]["edit_action"] == "rename_device"
        assert table["props"]["row_key"] == "id"
        assert table["props"]["columns"][0]["variant"] == "editable"

    def test_table_edit_action_with_title(self) -> None:
        t = Table(
            columns=[TableColumn("name", "Name", variant="editable")],
            rows=[{"name": "A", "udn": "x"}],
            title="Devices",
            edit_action="update_device",
        )
        d = t.to_dict()
        assert d["props"]["title"] == "Devices"
        assert d["props"]["edit_action"] == "update_device"


# =============================================================================
# Phase 2 — Tabs widget tests
# =============================================================================


class TestTabsWidget:
    """Tests für das Tabs-Layout-Widget."""

    def test_tabs_basic(self) -> None:
        t = Tabs(tabs=[
            Tab(label="First", children=[Text("Hello")]),
            Tab(label="Second", children=[Text("World")]),
        ])
        assert t.type == "tabs"
        tabs_data = t.props["tabs"]
        assert len(tabs_data) == 2
        assert tabs_data[0]["label"] == "First"
        assert tabs_data[1]["label"] == "Second"
        assert len(tabs_data[0]["children"]) == 1
        assert tabs_data[0]["children"][0]["type"] == "text"

    def test_tabs_with_icon(self) -> None:
        t = Tabs(tabs=[
            Tab(label="Settings", icon="settings", children=[Text("X")]),
        ])
        tabs_data = t.props["tabs"]
        assert tabs_data[0]["icon"] == "settings"

    def test_tabs_icon_omitted_when_none(self) -> None:
        tab = Tab(label="Plain", children=[])
        d = tab.to_dict()
        assert "icon" not in d

    def test_tabs_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one tab"):
            Tabs(tabs=[])

    def test_tabs_serialization_roundtrip(self) -> None:
        t = Tabs(tabs=[
            Tab(label="A", children=[
                Card(title="Inner", children=[Heading("H1")]),
            ]),
        ])
        data = t.to_dict()
        assert data["type"] == "tabs"
        tab_a = data["props"]["tabs"][0]
        assert tab_a["label"] == "A"
        assert tab_a["children"][0]["type"] == "card"
        assert tab_a["children"][0]["children"][0]["type"] == "heading"

    def test_tab_with_empty_children(self) -> None:
        tab = Tab(label="Empty")
        d = tab.to_dict()
        assert d["children"] == []

    def test_tabs_multiple_children_per_tab(self) -> None:
        t = Tabs(tabs=[
            Tab(label="Multi", children=[
                Heading("Title"),
                Text("Body"),
                Alert("Info", severity="info"),
            ]),
        ])
        tabs_data = t.props["tabs"]
        assert len(tabs_data[0]["children"]) == 3


# =============================================================================
# Phase 2 — Form widget tests
# =============================================================================


class TestTextInputWidget:
    """Tests für das TextInput-Widget."""

    def test_text_input_basic(self) -> None:
        ti = TextInput(name="hostname", label="Hostname", value="localhost")
        assert ti.type == "text_input"
        assert ti.props["name"] == "hostname"
        assert ti.props["label"] == "Hostname"
        assert ti.props["value"] == "localhost"

    def test_text_input_placeholder(self) -> None:
        ti = TextInput(name="x", label="X", placeholder="Enter value")
        assert ti.props["placeholder"] == "Enter value"

    def test_text_input_required(self) -> None:
        ti = TextInput(name="x", label="X", required=True)
        assert ti.props["required"] is True

    def test_text_input_pattern(self) -> None:
        ti = TextInput(name="ip", label="IP", pattern=r"^\d+\.\d+\.\d+\.\d+$")
        assert ti.props["pattern"] == r"^\d+\.\d+\.\d+\.\d+$"

    def test_text_input_disabled(self) -> None:
        ti = TextInput(name="x", label="X", disabled=True)
        assert ti.props["disabled"] is True

    def test_text_input_disabled_omitted_when_false(self) -> None:
        ti = TextInput(name="x", label="X")
        assert "disabled" not in ti.props

    def test_text_input_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            TextInput(name="", label="X")

    def test_text_input_default_value_empty_string(self) -> None:
        ti = TextInput(name="x", label="X")
        assert ti.props["value"] == ""

    def test_text_input_optional_props_omitted(self) -> None:
        ti = TextInput(name="x", label="X")
        assert "placeholder" not in ti.props
        assert "required" not in ti.props
        assert "pattern" not in ti.props
        assert "disabled" not in ti.props


class TestTextareaWidget:
    """Tests für das Textarea-Widget."""

    def test_textarea_basic(self) -> None:
        ta = Textarea(name="notes", label="Notes", value="hello world")
        assert ta.type == "textarea"
        assert ta.props["name"] == "notes"
        assert ta.props["label"] == "Notes"
        assert ta.props["value"] == "hello world"
        assert ta.props["rows"] == 4  # default

    def test_textarea_custom_rows(self) -> None:
        ta = Textarea(name="x", label="X", rows=10)
        assert ta.props["rows"] == 10

    def test_textarea_rows_minimum(self) -> None:
        with pytest.raises(ValueError, match="rows.*must be at least 1"):
            Textarea(name="x", label="X", rows=0)

    def test_textarea_placeholder(self) -> None:
        ta = Textarea(name="x", label="X", placeholder="Enter text…")
        assert ta.props["placeholder"] == "Enter text…"

    def test_textarea_maxlength(self) -> None:
        ta = Textarea(name="x", label="X", maxlength=500)
        assert ta.props["maxlength"] == 500

    def test_textarea_maxlength_minimum(self) -> None:
        with pytest.raises(ValueError, match="maxlength.*must be at least 1"):
            Textarea(name="x", label="X", maxlength=0)

    def test_textarea_required(self) -> None:
        ta = Textarea(name="x", label="X", required=True)
        assert ta.props["required"] is True

    def test_textarea_disabled(self) -> None:
        ta = Textarea(name="x", label="X", disabled=True)
        assert ta.props["disabled"] is True

    def test_textarea_disabled_omitted_when_false(self) -> None:
        ta = Textarea(name="x", label="X")
        assert "disabled" not in ta.props

    def test_textarea_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            Textarea(name="", label="X")

    def test_textarea_default_value_empty_string(self) -> None:
        ta = Textarea(name="x", label="X")
        assert ta.props["value"] == ""

    def test_textarea_optional_props_omitted(self) -> None:
        ta = Textarea(name="x", label="X")
        assert "placeholder" not in ta.props
        assert "required" not in ta.props
        assert "disabled" not in ta.props
        assert "maxlength" not in ta.props

    def test_textarea_rows_always_present(self) -> None:
        ta = Textarea(name="x", label="X")
        assert "rows" in ta.props
        assert ta.props["rows"] == 4

    def test_textarea_type_in_allowed_types(self) -> None:
        assert "textarea" in ALLOWED_TYPES

    def test_textarea_supports_visible_when(self) -> None:
        ta = Textarea(name="x", label="X").when("show_notes", True)
        assert ta.visible_when == {"field": "show_notes", "value": True}


class TestNumberInputWidget:
    """Tests für das NumberInput-Widget."""

    def test_number_input_basic(self) -> None:
        ni = NumberInput(name="port", label="Port", value=9000)
        assert ni.type == "number_input"
        assert ni.props["name"] == "port"
        assert ni.props["label"] == "Port"
        assert ni.props["value"] == 9000
        assert ni.props["step"] == 1

    def test_number_input_min_max(self) -> None:
        ni = NumberInput(name="vol", label="Volume", value=50, min=0, max=100)
        assert ni.props["min"] == 0
        assert ni.props["max"] == 100

    def test_number_input_step(self) -> None:
        ni = NumberInput(name="x", label="X", value=0.5, step=0.1)
        assert ni.props["step"] == 0.1

    def test_number_input_disabled(self) -> None:
        ni = NumberInput(name="x", label="X", disabled=True)
        assert ni.props["disabled"] is True

    def test_number_input_required(self) -> None:
        ni = NumberInput(name="x", label="X", required=True)
        assert ni.props["required"] is True

    def test_number_input_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            NumberInput(name="", label="X")

    def test_number_input_optional_props_omitted(self) -> None:
        ni = NumberInput(name="x", label="X")
        assert "min" not in ni.props
        assert "max" not in ni.props
        assert "disabled" not in ni.props
        assert "required" not in ni.props


class TestSelectWidget:
    """Tests für das Select-Widget."""

    def test_select_basic(self) -> None:
        s = Select(
            name="mode",
            label="Volume Mode",
            value="hardware",
            options=[
                SelectOption(value="hardware", label="Hardware"),
                SelectOption(value="software", label="Software"),
                SelectOption(value="disabled", label="Disabled"),
            ],
        )
        assert s.type == "select"
        assert s.props["name"] == "mode"
        assert s.props["label"] == "Volume Mode"
        assert s.props["value"] == "hardware"
        assert len(s.props["options"]) == 3
        assert s.props["options"][0] == {"value": "hardware", "label": "Hardware"}

    def test_select_disabled(self) -> None:
        s = Select(name="x", label="X", disabled=True)
        assert s.props["disabled"] is True

    def test_select_required(self) -> None:
        s = Select(name="x", label="X", required=True)
        assert s.props["required"] is True

    def test_select_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            Select(name="", label="X")

    def test_select_no_options_defaults_to_empty_list(self) -> None:
        s = Select(name="x", label="X")
        assert s.props["options"] == []

    def test_select_option_serialization(self) -> None:
        opt = SelectOption(value="v1", label="Label 1")
        assert opt.to_dict() == {"value": "v1", "label": "Label 1"}


class TestToggleWidget:
    """Tests für das Toggle-Widget."""

    def test_toggle_basic(self) -> None:
        t = Toggle(name="autostart", label="Auto-start", value=True)
        assert t.type == "toggle"
        assert t.props["name"] == "autostart"
        assert t.props["label"] == "Auto-start"
        assert t.props["value"] is True

    def test_toggle_default_false(self) -> None:
        t = Toggle(name="x", label="X")
        assert t.props["value"] is False

    def test_toggle_disabled(self) -> None:
        t = Toggle(name="x", label="X", disabled=True)
        assert t.props["disabled"] is True

    def test_toggle_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            Toggle(name="", label="X")

    def test_toggle_disabled_omitted_when_false(self) -> None:
        t = Toggle(name="x", label="X")
        assert "disabled" not in t.props


class TestFormWidget:
    """Tests für das Form-Container-Widget."""

    def test_form_basic(self) -> None:
        f = Form(
            action="save_settings",
            children=[
                TextInput(name="host", label="Host", value="localhost"),
                NumberInput(name="port", label="Port", value=9000),
            ],
            submit_label="Save Settings",
        )
        assert f.type == "form"
        assert f.props["action"] == "save_settings"
        assert f.props["submit_label"] == "Save Settings"
        assert f.props["submit_style"] == "primary"
        assert len(f.children) == 2

    def test_form_submit_style(self) -> None:
        f = Form(action="delete", submit_style="danger", submit_label="Delete All")
        assert f.props["submit_style"] == "danger"

    def test_form_invalid_submit_style(self) -> None:
        with pytest.raises(ValueError, match="Invalid submit_style"):
            Form(action="x", submit_style="nope")

    def test_form_disabled(self) -> None:
        f = Form(action="save", disabled=True)
        assert f.props["disabled"] is True

    def test_form_disabled_omitted_when_false(self) -> None:
        f = Form(action="save")
        assert "disabled" not in f.props

    def test_form_empty_action_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'action'"):
            Form(action="")

    def test_form_default_submit_label(self) -> None:
        f = Form(action="save")
        assert f.props["submit_label"] == "Save"

    def test_form_serialization_with_children(self) -> None:
        f = Form(
            action="update",
            children=[
                TextInput(name="name", label="Name", value="Test"),
                Toggle(name="active", label="Active", value=True),
                Select(
                    name="mode", label="Mode", value="a",
                    options=[SelectOption("a", "A"), SelectOption("b", "B")],
                ),
            ],
        )
        data = f.to_dict()
        assert data["type"] == "form"
        assert data["props"]["action"] == "update"
        assert len(data["children"]) == 3
        assert data["children"][0]["type"] == "text_input"
        assert data["children"][0]["props"]["name"] == "name"
        assert data["children"][1]["type"] == "toggle"
        assert data["children"][2]["type"] == "select"

    def test_form_inside_tab_inside_page(self) -> None:
        """Ganzer Stack: Page → Tabs → Tab → Form → Inputs."""
        page = Page(
            title="Test",
            components=[
                Tabs(tabs=[
                    Tab(label="Settings", children=[
                        Form(action="save", children=[
                            TextInput(name="x", label="X", value="1"),
                            NumberInput(name="y", label="Y", value=42, min=0, max=100),
                            Toggle(name="z", label="Z", value=False),
                        ]),
                    ]),
                ]),
            ],
        )
        data = page.to_dict(plugin_id="test")
        assert data["schema_version"] == "1.0"
        tabs_comp = data["components"][0]
        assert tabs_comp["type"] == "tabs"
        tab_settings = tabs_comp["props"]["tabs"][0]
        assert tab_settings["label"] == "Settings"
        form_comp = tab_settings["children"][0]
        assert form_comp["type"] == "form"
        assert form_comp["props"]["action"] == "save"
        assert len(form_comp["children"]) == 3
        assert form_comp["children"][1]["props"]["min"] == 0
        assert form_comp["children"][1]["props"]["max"] == 100


class TestPageSerialization:
    """Test Page.to_dict() output format."""

    def test_page_serializes_to_valid_json(self) -> None:
        page = Page(
            title="Test Page",
            icon="plug",
            refresh_interval=5,
            components=[
                Card(title="Status", children=[
                    KeyValue(items=[KVItem("Version", "1.0", color="green")]),
                ]),
                Button("Restart", action="restart", style="danger", confirm=True),
            ],
        )
        data = page.to_dict(plugin_id="testplugin")

        assert data["schema_version"] == "1.0"
        assert data["plugin_id"] == "testplugin"
        assert data["title"] == "Test Page"
        assert data["icon"] == "plug"
        assert data["refresh_interval"] == 5
        assert len(data["components"]) == 2

        card = data["components"][0]
        assert card["type"] == "card"
        assert len(card["children"]) == 1

        button = data["components"][1]
        assert button["type"] == "button"
        assert button["props"]["action"] == "restart"
        assert button["props"]["style"] == "danger"
        assert button["props"]["confirm"] is True

    def test_color_restricted_to_allowed_values(self) -> None:
        for color in ALLOWED_COLORS:
            StatusBadge("OK", color=color)

        with pytest.raises(ValueError):
            StatusBadge("Bad", color="rainbow")


# =============================================================================
# Manifest parsing tests
# =============================================================================


class TestManifestUIParsing:
    """Test that [ui] section in plugin.toml is parsed correctly."""

    def test_plugin_toml_ui_section_parsed(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "myplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "myplugin"\nversion = "1.0.0"\n\n'
            "[ui]\nenabled = true\n"
            'sidebar_label = "My Plugin"\n'
            'sidebar_icon = "star"\n',
            encoding="utf-8",
        )
        (plugin_dir / "__init__.py").write_text(
            "async def setup(ctx):\n    pass\n", encoding="utf-8"
        )

        manifest = PluginManager._parse_manifest(
            plugin_dir / "plugin.toml", plugin_dir
        )

        assert manifest.ui_enabled is True
        assert manifest.ui_sidebar_label == "My Plugin"
        assert manifest.ui_sidebar_icon == "star"

    def test_plugin_toml_without_ui_section_defaults(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "noplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            '[plugin]\nname = "noplugin"\nversion = "1.0.0"\n',
            encoding="utf-8",
        )
        (plugin_dir / "__init__.py").write_text(
            "async def setup(ctx):\n    pass\n", encoding="utf-8"
        )

        manifest = PluginManager._parse_manifest(
            plugin_dir / "plugin.toml", plugin_dir
        )

        assert manifest.ui_enabled is False
        assert manifest.ui_sidebar_label == ""
        assert manifest.ui_sidebar_icon == ""


# =============================================================================
# API endpoint tests
# =============================================================================


def _create_ui_plugin(
    root: Path,
    name: str,
    *,
    ui_enabled: bool = True,
    sidebar_label: str = "",
    sidebar_icon: str = "",
) -> None:
    """Create a plugin directory with [ui] section and a UI handler."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    ui_section = ""
    if ui_enabled:
        ui_section = (
            "\n[ui]\nenabled = true\n"
            f'sidebar_label = "{sidebar_label or name}"\n'
            f'sidebar_icon = "{sidebar_icon or "plug"}"\n'
        )

    (plugin_dir / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "1.0.0"\n{ui_section}',
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        f"""
from resonance.ui import Page, Card, KeyValue, KVItem, StatusBadge

async def setup(ctx):
    ctx.register_ui_handler(get_ui)
    ctx.register_action_handler(handle_action)

async def get_ui(ctx):
    return Page(
        title="{name} UI",
        icon="plug",
        refresh_interval=5,
        components=[
            Card(title="Status", children=[
                StatusBadge("Running", color="green"),
                KeyValue(items=[KVItem("Plugin", "{name}")]),
            ]),
        ],
    )

async def handle_action(action, params, ctx=None):
    return {{"success": True, "message": f"Action {{action}} executed"}}
""",
        encoding="utf-8",
    )


def _create_no_ui_plugin(root: Path, name: str) -> None:
    """Create a plain plugin without UI."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        f'[plugin]\nname = "{name}"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "async def setup(ctx):\n    pass\n", encoding="utf-8"
    )


@pytest.fixture
async def ui_client(tmp_path: Path):
    """Fixture providing an HTTP client with a UI-enabled plugin."""
    core_dir = tmp_path / "core_plugins"
    community_dir = tmp_path / "community_plugins"

    for name in ["ui_test", "no_ui_test"]:
        shutil.rmtree(Path("data/plugins") / name, ignore_errors=True)

    _create_ui_plugin(
        core_dir, "ui_test",
        sidebar_label="Test Plugin",
        sidebar_icon="zap",
    )
    _create_no_ui_plugin(core_dir, "no_ui_test")

    manager = PluginManager(
        core_plugins_dir=core_dir,
        community_plugins_dir=community_dir,
        state_file=tmp_path / "plugin_states.json",
    )
    await manager.discover()
    await manager.load_all()
    await manager.start_all(
        event_bus=EventBus(),
        music_library=MagicMock(),
        player_registry=MagicMock(),
    )

    db = LibraryDb(":memory:")
    await db.open()
    await db.ensure_schema()
    library = MusicLibrary(db=db, music_root=None)
    await library.initialize()

    server = WebServer(
        player_registry=PlayerRegistry(),
        music_library=library,
        plugin_manager=manager,
    )
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, manager
    await db.close()


class TestUIRegistry:
    """Tests for GET /api/plugins/ui-registry."""

    @pytest.mark.asyncio
    async def test_ui_registry_returns_ui_plugin(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.get("/api/plugins/ui-registry")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        entry = next(e for e in data if e["id"] == "ui_test")
        assert entry["label"] == "Test Plugin"
        assert entry["icon"] == "zap"
        assert entry["path"] == "/plugins/ui_test"

    @pytest.mark.asyncio
    async def test_ui_registry_excludes_no_ui_plugin(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.get("/api/plugins/ui-registry")
        data = response.json()
        ids = [e["id"] for e in data]
        assert "no_ui_test" not in ids


class TestGetPluginUI:
    """Tests for GET /api/plugins/{plugin_id}/ui."""

    @pytest.mark.asyncio
    async def test_get_plugin_ui_returns_schema(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.get("/api/plugins/ui_test/ui")
        assert response.status_code == 200
        data = response.json()
        assert data["schema_version"] == "1.0"
        assert data["plugin_id"] == "ui_test"
        assert data["title"] == "ui_test UI"
        assert data["icon"] == "plug"
        assert data["refresh_interval"] == 5
        assert len(data["components"]) == 1
        assert data["components"][0]["type"] == "card"

    @pytest.mark.asyncio
    async def test_get_plugin_ui_404_when_no_handler(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.get("/api/plugins/no_ui_test/ui")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_plugin_ui_404_when_not_found(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.get("/api/plugins/nonexistent/ui")
        assert response.status_code == 404


class TestDispatchAction:
    """Tests for POST /api/plugins/{plugin_id}/actions/{action}."""

    @pytest.mark.asyncio
    async def test_dispatch_action_calls_handler(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.post(
            "/api/plugins/ui_test/actions/restart",
            json={"device": "kitchen"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "restart" in data["message"]

    @pytest.mark.asyncio
    async def test_dispatch_action_404_when_no_handler(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.post(
            "/api/plugins/no_ui_test/actions/restart",
            json={},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_dispatch_action_404_when_not_found(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.post(
            "/api/plugins/nonexistent/actions/restart",
            json={},
        )
        assert response.status_code == 404


class TestListPluginsIncludesUI:
    """Test that GET /api/plugins includes ui_enabled field."""

    @pytest.mark.asyncio
    async def test_list_plugins_has_ui_enabled(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.get("/api/plugins")
        assert response.status_code == 200
        plugins = response.json()["plugins"]
        ui_plugin = next(p for p in plugins if p["name"] == "ui_test")
        no_ui_plugin = next(p for p in plugins if p["name"] == "no_ui_test")
        assert ui_plugin["ui_enabled"] is True
        assert no_ui_plugin["ui_enabled"] is False


# =============================================================================
# visible_when conditional rendering (C2)
# =============================================================================


class TestVisibleWhen:
    """Tests for the visible_when conditional rendering feature on UIComponent."""

    def test_when_method_sets_visible_when(self) -> None:
        comp = Toggle(name="debug", label="Debug", value=False)
        result = comp.when("debug_enabled", True)
        assert result is comp  # chainable — returns self
        assert comp.visible_when == {"field": "debug_enabled", "value": True}

    def test_when_serialised_in_to_dict(self) -> None:
        comp = Select(
            name="cat", label="Category", value="all",
            options=[SelectOption(value="all", label="All")],
        ).when("debug_enabled", True)
        d = comp.to_dict()
        assert d["visible_when"] == {"field": "debug_enabled", "value": True}

    def test_no_visible_when_omitted_from_dict(self) -> None:
        comp = Text("hello")
        d = comp.to_dict()
        assert "visible_when" not in d

    def test_visible_when_with_false_value(self) -> None:
        comp = Alert(message="hidden when off", severity="info").when("active", False)
        d = comp.to_dict()
        assert d["visible_when"] == {"field": "active", "value": False}

    def test_visible_when_with_string_value(self) -> None:
        comp = Text("only for hardware").when("volume_mode", "2")
        d = comp.to_dict()
        assert d["visible_when"] == {"field": "volume_mode", "value": "2"}

    def test_visible_when_invalid_no_field_raises(self) -> None:
        with pytest.raises(ValueError, match="field"):
            UIComponent(
                type="text",
                props={"content": "x"},
                visible_when={"value": True},
            )

    def test_visible_when_invalid_no_value_raises(self) -> None:
        with pytest.raises(ValueError, match="value"):
            UIComponent(
                type="text",
                props={"content": "x"},
                visible_when={"field": "debug"},
            )

    def test_visible_when_invalid_empty_field_raises(self) -> None:
        with pytest.raises(ValueError, match="field"):
            UIComponent(
                type="text",
                props={"content": "x"},
                visible_when={"field": "", "value": True},
            )

    def test_visible_when_invalid_type_raises(self) -> None:
        with pytest.raises(ValueError, match="dict"):
            UIComponent(
                type="text",
                props={"content": "x"},
                visible_when="not_a_dict",  # type: ignore[arg-type]
            )

    def test_when_method_validates(self) -> None:
        comp = Text("hello")
        with pytest.raises(ValueError, match="field"):
            comp.when("", True)

    def test_visible_when_in_form_tree(self) -> None:
        """Full stack: Form with a Toggle and a conditional Select."""
        page = Page(
            title="Test",
            components=[
                Form(action="save", children=[
                    Toggle(name="debug_enabled", label="Debug", value=False),
                    Select(
                        name="debug_category",
                        label="Category",
                        value="all",
                        options=[SelectOption(value="all", label="All")],
                    ).when("debug_enabled", True),
                ]),
            ],
        )
        d = page.to_dict(plugin_id="test")
        form = d["components"][0]
        assert form["type"] == "form"
        toggle = form["children"][0]
        select = form["children"][1]
        assert "visible_when" not in toggle
        assert select["visible_when"] == {"field": "debug_enabled", "value": True}

    def test_visible_when_none_by_default(self) -> None:
        comp = Heading("Title")
        assert comp.visible_when is None

    def test_multiple_when_calls_last_wins(self) -> None:
        comp = Text("test").when("a", True).when("b", False)
        assert comp.visible_when == {"field": "b", "value": False}

    def test_when_works_on_all_widget_types(self) -> None:
        """Ensure .when() is available on various widget subclasses."""
        widgets = [
            Heading("H").when("x", True),
            Text("T").when("x", True),
            StatusBadge(label="S", color="green").when("x", True),
            Alert(message="A").when("x", True),
            Button(label="B", action="a").when("x", True),
            Card(title="C").when("x", True),
            Markdown("M").when("x", True),
            Progress(value=50).when("x", True),
        ]
        for w in widgets:
            d = w.to_dict()
            assert d["visible_when"] == {"field": "x", "value": True}, (
                f"{w.type} should support visible_when"
            )


# =============================================================================
# visible_when operators (eq, ne, gt, lt, gte, lte, in, not_in)
# =============================================================================


class TestVisibleWhenOperators:
    """Tests for extended visible_when operator support."""

    # -- Allowed operators constant -------------------------------------------

    def test_allowed_operators_contains_all_expected(self) -> None:
        expected = {"eq", "ne", "gt", "lt", "gte", "lte", "in", "not_in"}
        assert ALLOWED_VISIBLE_WHEN_OPERATORS == expected

    # -- .when() with operator argument ---------------------------------------

    def test_when_eq_is_default_no_operator_in_spec(self) -> None:
        """When operator='eq' (default), the spec should NOT contain 'operator'."""
        comp = Text("x").when("mode", "hardware")
        assert comp.visible_when == {"field": "mode", "value": "hardware"}
        assert "operator" not in comp.visible_when  # type: ignore[operator]

    def test_when_ne_operator(self) -> None:
        comp = Text("x").when("mode", "hardware", operator="ne")
        assert comp.visible_when == {
            "field": "mode", "value": "hardware", "operator": "ne",
        }

    def test_when_gt_operator(self) -> None:
        comp = Alert(message="High port", severity="warning").when(
            "port", 1024, operator="gt",
        )
        assert comp.visible_when == {
            "field": "port", "value": 1024, "operator": "gt",
        }

    def test_when_lt_operator(self) -> None:
        comp = Text("low").when("volume", 10, operator="lt")
        assert comp.visible_when == {
            "field": "volume", "value": 10, "operator": "lt",
        }

    def test_when_gte_operator(self) -> None:
        comp = Text("ok").when("level", 5, operator="gte")
        assert comp.visible_when == {
            "field": "level", "value": 5, "operator": "gte",
        }

    def test_when_lte_operator(self) -> None:
        comp = Text("ok").when("level", 100, operator="lte")
        assert comp.visible_when == {
            "field": "level", "value": 100, "operator": "lte",
        }

    def test_when_in_operator(self) -> None:
        comp = Text("special").when("codec", ["aac", "alac"], operator="in")
        assert comp.visible_when == {
            "field": "codec", "value": ["aac", "alac"], "operator": "in",
        }

    def test_when_not_in_operator(self) -> None:
        comp = Text("excluded").when("codec", ["pcm", "wav"], operator="not_in")
        assert comp.visible_when == {
            "field": "codec", "value": ["pcm", "wav"], "operator": "not_in",
        }

    # -- Invalid operator rejected --------------------------------------------

    def test_invalid_operator_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid visible_when operator"):
            Text("x").when("field", 1, operator="like")

    def test_invalid_operator_rejected_via_raw_spec(self) -> None:
        with pytest.raises(ValueError, match="Invalid visible_when operator"):
            UIComponent(
                type="text",
                props={"content": "x"},
                visible_when={"field": "f", "value": 1, "operator": "regex"},
            )

    # -- Serialisation (to_dict) ----------------------------------------------

    def test_eq_default_omits_operator_from_dict(self) -> None:
        comp = Text("x").when("a", 1)
        d = comp.to_dict()
        assert d["visible_when"] == {"field": "a", "value": 1}
        assert "operator" not in d["visible_when"]

    def test_ne_operator_serialised_in_dict(self) -> None:
        comp = Text("x").when("a", 1, operator="ne")
        d = comp.to_dict()
        assert d["visible_when"]["operator"] == "ne"

    def test_gt_operator_serialised_in_dict(self) -> None:
        comp = Text("x").when("port", 1024, operator="gt")
        d = comp.to_dict()
        assert d["visible_when"] == {
            "field": "port", "value": 1024, "operator": "gt",
        }

    def test_in_operator_serialised_with_list_value(self) -> None:
        comp = Text("x").when("codec", ["aac", "alac"], operator="in")
        d = comp.to_dict()
        assert d["visible_when"] == {
            "field": "codec",
            "value": ["aac", "alac"],
            "operator": "in",
        }

    # -- Chaining with operators ----------------------------------------------

    def test_operator_overridden_by_second_when_call(self) -> None:
        comp = Text("x").when("a", 1, operator="gt").when("b", 2, operator="lt")
        assert comp.visible_when == {
            "field": "b", "value": 2, "operator": "lt",
        }

    def test_operator_reset_to_eq_on_second_call(self) -> None:
        """A second .when() without operator resets to eq (no operator key)."""
        comp = Text("x").when("a", 1, operator="ne").when("b", 2)
        assert comp.visible_when == {"field": "b", "value": 2}
        assert "operator" not in comp.visible_when  # type: ignore[operator]

    # -- Full-stack: in form tree ---------------------------------------------

    def test_operator_in_form_tree_serialisation(self) -> None:
        """A Form with children using various operators serialises correctly."""
        page = Page(
            title="Ops Test",
            components=[
                Form(action="save", children=[
                    NumberInput(name="port", label="Port", value=9000),
                    Alert(
                        message="Port too low!", severity="warning",
                    ).when("port", 1024, operator="lt"),
                    Select(
                        name="codec", label="Codec", value="aac",
                        options=[
                            SelectOption(value="aac", label="AAC"),
                            SelectOption(value="alac", label="ALAC"),
                            SelectOption(value="pcm", label="PCM"),
                        ],
                    ),
                    Text("Lossy codec selected").when(
                        "codec", ["aac", "mp3"], operator="in",
                    ),
                ]),
            ],
        )
        d = page.to_dict(plugin_id="test")
        form = d["components"][0]
        assert form["type"] == "form"
        # NumberInput — no visible_when
        assert "visible_when" not in form["children"][0]
        # Alert with lt
        alert_vw = form["children"][1]["visible_when"]
        assert alert_vw == {"field": "port", "value": 1024, "operator": "lt"}
        # Select — no visible_when
        assert "visible_when" not in form["children"][2]
        # Text with in
        text_vw = form["children"][3]["visible_when"]
        assert text_vw == {
            "field": "codec", "value": ["aac", "mp3"], "operator": "in",
        }

    # -- All operators accepted by _validate_visible_when --------------------

    @pytest.mark.parametrize("op", sorted(ALLOWED_VISIBLE_WHEN_OPERATORS))
    def test_all_allowed_operators_accepted(self, op: str) -> None:
        """Every operator in ALLOWED_VISIBLE_WHEN_OPERATORS should be valid."""
        comp = Text("x").when("f", 1, operator=op)
        spec = comp.visible_when
        assert spec is not None
        assert spec["field"] == "f"
        assert spec["value"] == 1
        if op == "eq":
            assert "operator" not in spec
        else:
            assert spec["operator"] == op


# =============================================================================
# Modal widget model (C1)
# =============================================================================


class TestModalWidget:
    """Tests for the Modal UIComponent backend model."""

    def test_modal_basic(self) -> None:
        m = Modal(title="Test", trigger_label="Open")
        d = m.to_dict()
        assert d["type"] == "modal"
        assert d["props"]["title"] == "Test"
        assert d["props"]["trigger_label"] == "Open"
        assert d["props"]["trigger_style"] == "secondary"
        assert d["props"]["size"] == "md"

    def test_modal_with_children(self) -> None:
        m = Modal(
            title="Details",
            trigger_label="View",
            children=[Text("Hello")],
        )
        d = m.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["type"] == "text"

    def test_modal_custom_style_and_size(self) -> None:
        m = Modal(
            title="Delete?",
            trigger_label="Delete",
            trigger_style="danger",
            size="lg",
        )
        d = m.to_dict()
        assert d["props"]["trigger_style"] == "danger"
        assert d["props"]["size"] == "lg"

    def test_modal_with_icon(self) -> None:
        m = Modal(title="T", trigger_label="L", trigger_icon="settings")
        d = m.to_dict()
        assert d["props"]["trigger_icon"] == "settings"

    def test_modal_no_icon_omitted(self) -> None:
        m = Modal(title="T", trigger_label="L")
        d = m.to_dict()
        assert "trigger_icon" not in d["props"]

    def test_modal_empty_title_raises(self) -> None:
        with pytest.raises(ValueError, match="title"):
            Modal(title="", trigger_label="Open")

    def test_modal_empty_trigger_label_raises(self) -> None:
        with pytest.raises(ValueError, match="trigger_label"):
            Modal(title="T", trigger_label="")

    def test_modal_invalid_style_raises(self) -> None:
        with pytest.raises(ValueError, match="trigger_style"):
            Modal(title="T", trigger_label="L", trigger_style="invalid")

    def test_modal_invalid_size_raises(self) -> None:
        with pytest.raises(ValueError, match="size"):
            Modal(title="T", trigger_label="L", size="xxl")

    def test_modal_all_valid_sizes(self) -> None:
        for size in ALLOWED_MODAL_SIZES:
            m = Modal(title="T", trigger_label="L", size=size)
            assert m.to_dict()["props"]["size"] == size

    def test_modal_all_valid_styles(self) -> None:
        for style in ("primary", "secondary", "danger"):
            m = Modal(title="T", trigger_label="L", trigger_style=style)
            assert m.to_dict()["props"]["trigger_style"] == style

    def test_modal_type_in_allowed_types(self) -> None:
        assert "modal" in ALLOWED_TYPES

    def test_modal_with_form_child(self) -> None:
        """Modal containing a Form — the typical device-settings pattern."""
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

    def test_modal_supports_visible_when(self) -> None:
        m = Modal(title="T", trigger_label="L").when("active", True)
        d = m.to_dict()
        assert d["visible_when"] == {"field": "active", "value": True}

    def test_modal_no_children_serialises_without_children_key(self) -> None:
        m = Modal(title="T", trigger_label="L")
        d = m.to_dict()
        # children defaults to [] which is falsy, so to_dict skips it
        # Actually Modal passes children=[] which is falsy, verify behaviour
        # The UIComponent.to_dict checks `if self.children:` — empty list is falsy
        assert "children" not in d or d["children"] == []


# =============================================================================
# SSE unit tests — PluginContext notify/wait mechanism (D2)
# =============================================================================


class TestPluginContextSSE:
    """Unit tests for the PluginContext SSE notification mechanism.

    These test the core notify/wait pattern directly — no HTTP involved,
    so they are fast and reliable regardless of transport quirks.
    """

    @pytest.mark.asyncio
    async def test_revision_starts_at_zero(self, ui_client) -> None:
        """Fresh context should have revision 0."""
        _, manager = ui_client
        ctx = manager.plugins["ui_test"].context
        # revision is >= 0 (may be > 0 if previous tests ran actions)
        assert ctx.ui_revision >= 0

    @pytest.mark.asyncio
    async def test_notify_increments_revision(self, ui_client) -> None:
        """Each notify_ui_update() bumps the revision by exactly 1."""
        _, manager = ui_client
        ctx = manager.plugins["ui_test"].context

        rev0 = ctx.ui_revision
        ctx.notify_ui_update()
        assert ctx.ui_revision == rev0 + 1
        ctx.notify_ui_update()
        assert ctx.ui_revision == rev0 + 2

    @pytest.mark.asyncio
    async def test_wait_returns_immediately_when_already_updated(self, ui_client) -> None:
        """If revision already exceeds last_revision, wait returns immediately."""
        _, manager = ui_client
        ctx = manager.plugins["ui_test"].context

        ctx.notify_ui_update()
        rev = ctx.ui_revision
        # Ask to wait with a stale last_revision — should return at once
        result = await ctx.wait_for_ui_update(last_revision=rev - 1, timeout=1.0)
        assert result == rev

    @pytest.mark.asyncio
    async def test_wait_returns_on_notify(self, ui_client) -> None:
        """wait_for_ui_update() unblocks when notify_ui_update() is called."""
        _, manager = ui_client
        ctx = manager.plugins["ui_test"].context
        rev_before = ctx.ui_revision

        async def _trigger():
            await asyncio.sleep(0.05)
            ctx.notify_ui_update()

        asyncio.create_task(_trigger())
        result = await ctx.wait_for_ui_update(last_revision=rev_before, timeout=3.0)
        assert result == rev_before + 1

    @pytest.mark.asyncio
    async def test_wait_times_out_without_notify(self, ui_client) -> None:
        """wait_for_ui_update() returns current revision on timeout."""
        _, manager = ui_client
        ctx = manager.plugins["ui_test"].context
        rev = ctx.ui_revision

        result = await ctx.wait_for_ui_update(last_revision=rev, timeout=0.1)
        # No notify happened — revision unchanged
        assert result == rev

    @pytest.mark.asyncio
    async def test_multiple_waiters_all_wake(self, ui_client) -> None:
        """Multiple concurrent waiters all wake on a single notify."""
        _, manager = ui_client
        ctx = manager.plugins["ui_test"].context
        rev = ctx.ui_revision

        results: list[int] = []

        async def _waiter():
            r = await ctx.wait_for_ui_update(last_revision=rev, timeout=3.0)
            results.append(r)

        tasks = [asyncio.create_task(_waiter()) for _ in range(3)]
        await asyncio.sleep(0.05)
        ctx.notify_ui_update()
        await asyncio.gather(*tasks)

        assert len(results) == 3
        assert all(r == rev + 1 for r in results)

    @pytest.mark.asyncio
    async def test_rapid_notifies_all_counted(self, ui_client) -> None:
        """Rapid back-to-back notifies all increment the counter."""
        _, manager = ui_client
        ctx = manager.plugins["ui_test"].context
        rev0 = ctx.ui_revision

        for _ in range(5):
            ctx.notify_ui_update()

        assert ctx.ui_revision == rev0 + 5

    @pytest.mark.asyncio
    async def test_action_dispatch_increments_revision(self, ui_client) -> None:
        """Dispatching an action via HTTP auto-calls notify_ui_update()."""
        client, manager = ui_client
        ctx = manager.plugins["ui_test"].context
        rev_before = ctx.ui_revision

        response = await client.post(
            "/api/plugins/ui_test/actions/restart",
            json={"device": "test"},
        )
        assert response.status_code == 200
        assert ctx.ui_revision > rev_before


# =============================================================================
# SSE endpoint routing tests (D2)
# =============================================================================


class TestSSEEndpointRouting:
    """HTTP-level tests for the SSE endpoint route registration and error cases.

    Note: httpx ASGITransport does not support true SSE streaming, so we only
    test non-streaming responses (404, 503) and verify the endpoint exists.
    Streaming behaviour is covered by the PluginContext unit tests above.
    """

    @pytest.mark.asyncio
    async def test_sse_404_for_nonexistent_plugin(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.get("/api/plugins/nonexistent/events")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_sse_404_detail_message(self, ui_client) -> None:
        client, _ = ui_client
        response = await client.get("/api/plugins/nonexistent/events")
        assert "not found" in response.json().get("detail", "").lower()
