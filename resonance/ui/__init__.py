"""
Server-Driven UI (SDUI) component models for plugin pages.

Plugins describe their UI as a tree of typed components. The Svelte frontend
has a generic renderer that maps each component ``type`` to a widget.

Usage inside a plugin::

    from resonance.ui import components as c

    async def get_ui(ctx):
        return c.Page(
            title="My Plugin",
            icon="plug",
            components=[
                c.Card(title="Status", children=[
                    c.KeyValue(items=[
                        c.KVItem("Version", "1.0", color="green"),
                    ]),
                ]),
                c.Button("Restart", action="restart", style="danger", confirm=True),
            ],
        )

Phase 2 additions — interactive form widgets::

    from resonance.ui import Tabs, Tab, Form, TextInput, NumberInput, Select, SelectOption, Toggle

    Tabs(tabs=[
        Tab(label="Settings", children=[
            Form(action="save_settings", submit_label="Save", children=[
                TextInput(name="interface", label="Interface", value="127.0.0.1"),
                NumberInput(name="port", label="Port", value=9000, min=1, max=65535),
                Select(name="mode", label="Volume Mode", value="hardware", options=[
                    SelectOption(value="hardware", label="Hardware"),
                    SelectOption(value="software", label="Software"),
                ]),
                Toggle(name="autostart", label="Auto-start", value=True),
            ]),
        ]),
        Tab(label="Devices", children=[...]),
    ])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

ALLOWED_TYPES = frozenset(
    {
        "heading",
        "text",
        "status_badge",
        "key_value",
        "table",
        "button",
        "card",
        "row",
        "column",
        "alert",
        "progress",
        "markdown",
        # Phase 2 — layout
        "tabs",
        # Phase 2 — form widgets
        "form",
        "text_input",
        "textarea",
        "number_input",
        "select",
        "toggle",
        # Phase 2.5 — modal
        "modal",
    }
)

ALLOWED_COLORS = frozenset({"green", "red", "yellow", "blue", "gray"})

ALLOWED_BUTTON_STYLES = frozenset({"primary", "secondary", "danger"})

ALLOWED_MODAL_SIZES = frozenset({"sm", "md", "lg", "xl"})

ALLOWED_URL_SCHEMES = frozenset({"http:", "https:", "mailto:"})

ALLOWED_ALERT_SEVERITIES = frozenset({"info", "warning", "error", "success"})

ALLOWED_VISIBLE_WHEN_OPERATORS = frozenset(
    {"eq", "ne", "gt", "lt", "gte", "lte", "in", "not_in"}
)

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_color(color: str | None) -> None:
    if color is not None and color not in ALLOWED_COLORS:
        raise ValueError(
            f"Invalid color '{color}'. Allowed: {sorted(ALLOWED_COLORS)}"
        )


def _validate_url(url: str) -> None:
    lower = url.lower().strip()
    if not any(lower.startswith(scheme) for scheme in ALLOWED_URL_SCHEMES):
        raise ValueError(
            f"Invalid URL scheme in '{url}'. Allowed: {sorted(ALLOWED_URL_SCHEMES)}"
        )


def _validate_no_event_handlers(props: dict[str, Any]) -> None:
    for key in props:
        if key.lower().startswith("on"):
            raise ValueError(
                f"Event handler props are not allowed: '{key}'"
            )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KVItem:
    """A single key-value pair for the ``key_value`` widget."""

    key: str
    value: str
    color: str | None = None

    def __post_init__(self) -> None:
        _validate_color(self.color)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "value": self.value}
        if self.color is not None:
            d["color"] = self.color
        return d


@dataclass(frozen=True)
class TableColumn:
    """Column definition for the ``table`` widget."""

    key: str
    label: str
    variant: str = "text"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "label": self.label}
        if self.variant != "text":
            d["variant"] = self.variant
        return d


@dataclass(frozen=True)
class TableAction:
    """An action button rendered inside a table ``actions`` column."""

    label: str
    action: str
    params: dict[str, Any] | None = None
    style: str = "secondary"
    confirm: bool = False

    def __post_init__(self) -> None:
        if self.style not in ALLOWED_BUTTON_STYLES:
            raise ValueError(
                f"Invalid button style '{self.style}'. "
                f"Allowed: {sorted(ALLOWED_BUTTON_STYLES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"label": self.label, "action": self.action}
        if self.params:
            d["params"] = self.params
        if self.style != "secondary":
            d["style"] = self.style
        if self.confirm:
            d["confirm"] = True
        return d


@dataclass(frozen=True)
class SelectOption:
    """A single option for the ``select`` widget."""

    value: str
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "label": self.label}


# ---------------------------------------------------------------------------
# UIComponent base
# ---------------------------------------------------------------------------


def _validate_visible_when(spec: dict[str, Any] | None) -> None:
    """Validate a ``visible_when`` condition spec.

    Must be ``{"field": "<name>", "value": <expected>}`` or
    ``{"field": "<name>", "value": <expected>, "operator": "<op>"}``
    where ``<op>`` is one of :data:`ALLOWED_VISIBLE_WHEN_OPERATORS`.
    When ``operator`` is omitted the frontend defaults to ``"eq"``.
    """
    if spec is None:
        return
    if not isinstance(spec, dict):
        raise ValueError(
            f"visible_when must be a dict, got {type(spec).__name__}"
        )
    if "field" not in spec:
        raise ValueError("visible_when requires a 'field' key")
    if "value" not in spec:
        raise ValueError("visible_when requires a 'value' key")
    if not isinstance(spec["field"], str) or not spec["field"]:
        raise ValueError("visible_when 'field' must be a non-empty string")
    op = spec.get("operator")
    if op is not None and op not in ALLOWED_VISIBLE_WHEN_OPERATORS:
        raise ValueError(
            f"Invalid visible_when operator '{op}'. "
            f"Allowed: {sorted(ALLOWED_VISIBLE_WHEN_OPERATORS)}"
        )


@dataclass
class UIComponent:
    """Base class for all UI components.

    Every component serialises to ``{"type": ..., "props": {...}, "children": [...]}``.

    ``visible_when`` is an optional condition that controls client-side
    visibility.  When set, the frontend checks the referenced form field
    and only renders the component when the field's current value matches
    the expected value.  Format: ``{"field": "<name>", "value": <expected>}``.
    This only works for components inside a ``Form`` — elsewhere the
    condition is ignored and the component is always shown.
    """

    type: str
    props: dict[str, Any] = field(default_factory=dict)
    children: list[UIComponent] | None = None
    fallback_text: str | None = None
    visible_when: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.type not in ALLOWED_TYPES:
            raise ValueError(
                f"Unknown widget type '{self.type}'. "
                f"Allowed: {sorted(ALLOWED_TYPES)}"
            )
        _validate_no_event_handlers(self.props)
        _validate_visible_when(self.visible_when)
        # Validate URL props
        for key in ("href", "src", "url"):
            val = self.props.get(key)
            if isinstance(val, str) and val:
                _validate_url(val)

    def when(
        self,
        field: str,
        value: Any,
        operator: str = "eq",
    ) -> "UIComponent":
        """Set a ``visible_when`` condition and return ``self`` for chaining.

        Inside a ``Form``, the frontend will only render this component when
        the sibling input identified by *field* satisfies the condition
        defined by *operator* and *value*.

        Supported operators: ``eq`` (default), ``ne``, ``gt``, ``lt``,
        ``gte``, ``lte``, ``in``, ``not_in``.

        Usage::

            Toggle(name="debug_enabled", label="Debug", value=False),
            Select(name="debug_category", label="Category", ...).when("debug_enabled", True),
            Alert(message="High port", severity="warning").when("port", 1024, operator="gt"),

        Outside a ``Form`` the condition is silently ignored and the component
        is always visible.
        """
        spec: dict[str, Any] = {"field": field, "value": value}
        if operator != "eq":
            spec["operator"] = operator
        _validate_visible_when(spec)
        # UIComponent is a dataclass — direct assignment works because
        # frozen=False (the default).
        object.__setattr__(self, "visible_when", spec)
        return self

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "props": dict(self.props)}
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        if self.fallback_text is not None:
            d["fallback_text"] = self.fallback_text
        if self.visible_when is not None:
            d["visible_when"] = self.visible_when
        return d


# ---------------------------------------------------------------------------
# Convenience constructors — plugins use these instead of raw UIComponent
# ---------------------------------------------------------------------------


class Heading(UIComponent):
    """Section heading."""

    def __init__(self, text: str, level: int = 2) -> None:
        super().__init__(type="heading", props={"text": text, "level": level})


class Text(UIComponent):
    """Paragraph text."""

    def __init__(
        self, content: str, color: str | None = None, size: str | None = None
    ) -> None:
        _validate_color(color)
        props: dict[str, Any] = {"content": content}
        if color is not None:
            props["color"] = color
        if size is not None:
            props["size"] = size
        super().__init__(type="text", props=props)


class StatusBadge(UIComponent):
    """Colored status indicator badge."""

    def __init__(
        self,
        label: str,
        status: str | None = None,
        color: str = "gray",
    ) -> None:
        _validate_color(color)
        super().__init__(
            type="status_badge",
            props={"label": label, "status": status or label, "color": color},
        )


class KeyValue(UIComponent):
    """Key-value pair list."""

    def __init__(self, items: list[KVItem]) -> None:
        super().__init__(
            type="key_value",
            props={"items": [item.to_dict() for item in items]},
        )


class Table(UIComponent):
    """Data table with optional action columns.

    When a column has ``variant="editable"`` the frontend renders an inline
    text input instead of plain text.  On blur or Enter the frontend dispatches
    *edit_action* with ``{<row_key>: <row[row_key]>, <col.key>: <new_value>}``.

    *row_key* identifies which column value uniquely identifies a row
    (default ``"udn"``).  *edit_action* is the plugin action name to call
    when an inline edit is committed.  Both are only serialised when
    *edit_action* is set.
    """

    def __init__(
        self,
        columns: list[TableColumn],
        rows: list[dict[str, Any]],
        title: str | None = None,
        edit_action: str | None = None,
        row_key: str = "udn",
    ) -> None:
        props: dict[str, Any] = {
            "columns": [col.to_dict() for col in columns],
            "rows": rows,
        }
        if title is not None:
            props["title"] = title
        if edit_action:
            props["edit_action"] = edit_action
            props["row_key"] = row_key
        super().__init__(type="table", props=props)


class Button(UIComponent):
    """Action button that triggers a POST to the plugin's action endpoint."""

    def __init__(
        self,
        label: str,
        action: str,
        params: dict[str, Any] | None = None,
        style: str = "secondary",
        confirm: bool = False,
        icon: str | None = None,
        disabled: bool = False,
    ) -> None:
        if style not in ALLOWED_BUTTON_STYLES:
            raise ValueError(
                f"Invalid button style '{style}'. "
                f"Allowed: {sorted(ALLOWED_BUTTON_STYLES)}"
            )
        props: dict[str, Any] = {"label": label, "action": action, "style": style}
        if params:
            props["params"] = params
        if confirm:
            props["confirm"] = True
        if icon:
            props["icon"] = icon
        if disabled:
            props["disabled"] = True
        super().__init__(type="button", props=props)


class Card(UIComponent):
    """Grouped content with optional title.

    When *collapsible* is ``True`` the frontend renders a toggle arrow next to
    the title so the user can expand/collapse the card body.  *collapsed*
    controls the initial state (default ``False`` = expanded).  Both flags are
    only emitted when *collapsible* is ``True`` to keep payloads compact.
    """

    def __init__(
        self,
        title: str = "",
        children: list[UIComponent] | None = None,
        collapsible: bool = False,
        collapsed: bool = False,
    ) -> None:
        props: dict[str, Any] = {}
        if title:
            props["title"] = title
        if collapsible:
            props["collapsible"] = True
            if collapsed:
                props["collapsed"] = True
        super().__init__(
            type="card",
            props=props,
            children=children or [],
        )


class Row(UIComponent):
    """Horizontal flex layout."""

    def __init__(
        self,
        children: list[UIComponent] | None = None,
        gap: str = "4",
        justify: str | None = None,
        align: str | None = None,
    ) -> None:
        props: dict[str, Any] = {"gap": gap}
        if justify:
            props["justify"] = justify
        if align:
            props["align"] = align
        super().__init__(type="row", props=props, children=children or [])


class Column(UIComponent):
    """Vertical flex layout."""

    def __init__(
        self,
        children: list[UIComponent] | None = None,
        gap: str = "4",
    ) -> None:
        super().__init__(
            type="column", props={"gap": gap}, children=children or []
        )


class Alert(UIComponent):
    """Info/warning/error/success banner."""

    def __init__(
        self,
        message: str,
        severity: str = "info",
        title: str | None = None,
    ) -> None:
        if severity not in ALLOWED_ALERT_SEVERITIES:
            raise ValueError(
                f"Invalid severity '{severity}'. "
                f"Allowed: {sorted(ALLOWED_ALERT_SEVERITIES)}"
            )
        props: dict[str, Any] = {"message": message, "severity": severity}
        if title:
            props["title"] = title
        super().__init__(type="alert", props=props)


class Progress(UIComponent):
    """Progress bar (0–100)."""

    def __init__(
        self,
        value: int | float,
        label: str | None = None,
        color: str | None = None,
    ) -> None:
        _validate_color(color)
        props: dict[str, Any] = {"value": max(0, min(100, value))}
        if label:
            props["label"] = label
        if color:
            props["color"] = color
        super().__init__(type="progress", props=props)


class Markdown(UIComponent):
    """Rendered markdown text (sanitised by frontend)."""

    def __init__(self, content: str) -> None:
        super().__init__(type="markdown", props={"content": content})


# ---------------------------------------------------------------------------
# Phase 2 — Tab layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tab:
    """A single tab inside a ``Tabs`` widget.

    ``label`` is displayed on the tab button.
    ``children`` are the components rendered when this tab is active.
    ``icon`` is an optional icon name shown next to the label.
    """

    label: str
    children: list[UIComponent] = field(default_factory=list)
    icon: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "label": self.label,
            "children": [c.to_dict() for c in self.children],
        }
        if self.icon:
            d["icon"] = self.icon
        return d


class Tabs(UIComponent):
    """Tab-based navigation — purely client-side (no backend roundtrip on switch).

    Each ``Tab`` has a label and a list of child components.
    The frontend renders tab buttons and shows only the active tab's children.
    """

    def __init__(self, tabs: list[Tab]) -> None:
        if not tabs:
            raise ValueError("Tabs widget requires at least one tab")
        super().__init__(
            type="tabs",
            props={"tabs": [t.to_dict() for t in tabs]},
        )


# ---------------------------------------------------------------------------
# Phase 2 — Form widgets
# ---------------------------------------------------------------------------


class TextInput(UIComponent):
    """Single-line text input field.

    ``name`` identifies the field value in the form submission params.
    ``required`` marks the field as mandatory (frontend + backend validation).
    ``pattern`` is an optional regex validated on the frontend.
    ``disabled`` prevents editing (e.g. while bridge is active).
    ``help_text`` is an optional hint shown below the input field.
    """

    def __init__(
        self,
        name: str,
        label: str,
        value: str = "",
        placeholder: str = "",
        required: bool = False,
        pattern: str | None = None,
        disabled: bool = False,
        help_text: str | None = None,
    ) -> None:
        if not name:
            raise ValueError("TextInput requires a non-empty 'name'")
        props: dict[str, Any] = {
            "name": name,
            "label": label,
            "value": value,
        }
        if placeholder:
            props["placeholder"] = placeholder
        if required:
            props["required"] = True
        if pattern is not None:
            props["pattern"] = pattern
        if disabled:
            props["disabled"] = True
        if help_text:
            props["help_text"] = help_text
        super().__init__(type="text_input", props=props)


class Textarea(UIComponent):
    """Multi-line text input field.

    ``name`` identifies the field value in the form submission params.
    ``rows`` sets the visible height in text rows (defaults to 4).
    ``maxlength`` is an optional maximum character count.
    ``required`` marks the field as mandatory (frontend validation).
    ``disabled`` prevents editing.
    ``help_text`` is an optional hint shown below the input field.
    """

    def __init__(
        self,
        name: str,
        label: str,
        value: str = "",
        placeholder: str = "",
        rows: int = 4,
        maxlength: int | None = None,
        required: bool = False,
        disabled: bool = False,
        help_text: str | None = None,
    ) -> None:
        if not name:
            raise ValueError("Textarea requires a non-empty 'name'")
        if rows < 1:
            raise ValueError("Textarea 'rows' must be at least 1")
        props: dict[str, Any] = {
            "name": name,
            "label": label,
            "value": value,
            "rows": rows,
        }
        if placeholder:
            props["placeholder"] = placeholder
        if maxlength is not None:
            if maxlength < 1:
                raise ValueError("Textarea 'maxlength' must be at least 1")
            props["maxlength"] = maxlength
        if required:
            props["required"] = True
        if disabled:
            props["disabled"] = True
        if help_text:
            props["help_text"] = help_text
        super().__init__(type="textarea", props=props)


class NumberInput(UIComponent):
    """Numeric input field with optional min/max/step constraints.

    ``name`` identifies the field value in the form submission params.
    ``min`` / ``max`` define allowed range (validated on frontend + backend).
    ``step`` controls the increment (defaults to 1).
    ``disabled`` prevents editing.
    ``help_text`` is an optional hint shown below the input field.
    """

    def __init__(
        self,
        name: str,
        label: str,
        value: int | float = 0,
        min: int | float | None = None,
        max: int | float | None = None,
        step: int | float = 1,
        required: bool = False,
        disabled: bool = False,
        help_text: str | None = None,
    ) -> None:
        if not name:
            raise ValueError("NumberInput requires a non-empty 'name'")
        props: dict[str, Any] = {
            "name": name,
            "label": label,
            "value": value,
            "step": step,
        }
        if min is not None:
            props["min"] = min
        if max is not None:
            props["max"] = max
        if required:
            props["required"] = True
        if disabled:
            props["disabled"] = True
        if help_text:
            props["help_text"] = help_text
        super().__init__(type="number_input", props=props)


class Select(UIComponent):
    """Dropdown selection field.

    ``name`` identifies the field value in the form submission params.
    ``options`` is a list of ``SelectOption`` (value + label).
    ``disabled`` prevents editing.
    ``help_text`` is an optional hint shown below the input field.
    """

    def __init__(
        self,
        name: str,
        label: str,
        value: str = "",
        options: list[SelectOption] | None = None,
        required: bool = False,
        disabled: bool = False,
        help_text: str | None = None,
    ) -> None:
        if not name:
            raise ValueError("Select requires a non-empty 'name'")
        props: dict[str, Any] = {
            "name": name,
            "label": label,
            "value": value,
            "options": [o.to_dict() for o in (options or [])],
        }
        if required:
            props["required"] = True
        if disabled:
            props["disabled"] = True
        if help_text:
            props["help_text"] = help_text
        super().__init__(type="select", props=props)


class Toggle(UIComponent):
    """Boolean toggle switch.

    ``name`` identifies the field value in the form submission params.
    ``disabled`` prevents toggling.
    ``help_text`` is an optional hint shown below the toggle.
    """

    def __init__(
        self,
        name: str,
        label: str,
        value: bool = False,
        disabled: bool = False,
        help_text: str | None = None,
    ) -> None:
        if not name:
            raise ValueError("Toggle requires a non-empty 'name'")
        props: dict[str, Any] = {
            "name": name,
            "label": label,
            "value": value,
        }
        if disabled:
            props["disabled"] = True
        if help_text:
            props["help_text"] = help_text
        super().__init__(type="toggle", props=props)


class Form(UIComponent):
    """Container that groups input widgets and submits their values as an action.

    On submit, all child input widget values (identified by ``name``) are
    collected into a ``params`` dict and sent to
    ``POST /api/plugins/{id}/actions/{action}``.

    ``submit_label`` is the text on the submit button (default: "Save").
    ``submit_style`` controls the button style (primary/secondary/danger).
    ``disabled`` disables the entire form (all inputs + submit button).
    """

    def __init__(
        self,
        action: str,
        children: list[UIComponent] | None = None,
        submit_label: str = "Save",
        submit_style: str = "primary",
        disabled: bool = False,
    ) -> None:
        if not action:
            raise ValueError("Form requires a non-empty 'action'")
        if submit_style not in ALLOWED_BUTTON_STYLES:
            raise ValueError(
                f"Invalid submit_style '{submit_style}'. "
                f"Allowed: {sorted(ALLOWED_BUTTON_STYLES)}"
            )
        props: dict[str, Any] = {
            "action": action,
            "submit_label": submit_label,
            "submit_style": submit_style,
        }
        if disabled:
            props["disabled"] = True
        super().__init__(
            type="form",
            props=props,
            children=children or [],
        )


# ---------------------------------------------------------------------------
# Phase 2.5 — Modal / Dialog
# ---------------------------------------------------------------------------


class Modal(UIComponent):
    """Modal dialog overlay triggered by a button.

    The modal renders a trigger button in the page. When clicked, a dialog
    overlay appears containing the ``children`` components.

    ``title`` is shown in the modal header.
    ``trigger_label`` is the text on the button that opens the modal.
    ``trigger_style`` controls the trigger button appearance (primary/secondary/danger).
    ``trigger_icon`` is an optional icon name for the trigger button.
    ``size`` controls the modal width: sm (384px), md (512px), lg (672px), xl (896px).
    ``children`` are rendered inside the modal body.
    """

    def __init__(
        self,
        title: str,
        trigger_label: str,
        children: list[UIComponent] | None = None,
        trigger_style: str = "secondary",
        trigger_icon: str | None = None,
        size: str = "md",
    ) -> None:
        if not title:
            raise ValueError("Modal requires a non-empty 'title'")
        if not trigger_label:
            raise ValueError("Modal requires a non-empty 'trigger_label'")
        if trigger_style not in ALLOWED_BUTTON_STYLES:
            raise ValueError(
                f"Invalid trigger_style '{trigger_style}'. "
                f"Allowed: {sorted(ALLOWED_BUTTON_STYLES)}"
            )
        if size not in ALLOWED_MODAL_SIZES:
            raise ValueError(
                f"Invalid modal size '{size}'. "
                f"Allowed: {sorted(ALLOWED_MODAL_SIZES)}"
            )
        props: dict[str, Any] = {
            "title": title,
            "trigger_label": trigger_label,
            "trigger_style": trigger_style,
            "size": size,
        }
        if trigger_icon:
            props["trigger_icon"] = trigger_icon
        super().__init__(
            type="modal",
            props=props,
            children=children or [],
        )


# ---------------------------------------------------------------------------
# Page envelope — wraps a list of components
# ---------------------------------------------------------------------------


@dataclass
class Page:
    """Top-level page returned by a plugin's ``get_ui()`` handler.

    Serialises to the JSON envelope the frontend expects.
    """

    title: str
    components: list[UIComponent] = field(default_factory=list)
    icon: str | None = None
    refresh_interval: int = 0

    def to_dict(self, plugin_id: str = "") -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "plugin_id": plugin_id,
            "title": self.title,
            "icon": self.icon or "",
            "refresh_interval": self.refresh_interval,
            "components": [c.to_dict() for c in self.components],
        }
