"""
OPML import / export for podcast subscriptions.

OPML (Outline Processor Markup Language) is the de-facto standard for
exchanging podcast subscription lists between apps.  Every major podcast
client — Apple Podcasts, Pocket Casts, Overcast, AntennaPod, gPodder —
supports OPML import and export.

This module provides:

* :func:`parse_opml` — parse an OPML XML string into subscription dicts
* :func:`generate_opml` — serialise subscriptions to a valid OPML 2.0 string
* :func:`import_opml_file` — read an OPML file from disk
* :func:`export_opml_file` — write subscriptions to an OPML file on disk

OPML 2.0 spec: http://opml.org/spec2.opml

Format notes
~~~~~~~~~~~~

Podcast OPML files use ``<outline>`` elements with these attributes:

* ``type="rss"`` — marks the outline as an RSS feed
* ``xmlUrl="…"`` — the RSS feed URL (required)
* ``text="…"`` — display name / title
* ``title="…"`` — alternative to *text* (some exporters use this)
* ``htmlUrl="…"`` — website link (optional)
* ``description="…"`` — show description (optional, non-standard but common)
* ``imageUrl="…"`` — cover art URL (optional, non-standard but common)

We are lenient on import (accept any outline with an ``xmlUrl``) and
strict on export (always emit valid OPML 2.0 with ``type="rss"``).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# Data types
# ============================================================================


@dataclass
class OPMLFeed:
    """A single feed entry parsed from or destined for OPML."""

    url: str
    """RSS feed URL (from ``xmlUrl``)."""

    name: str = ""
    """Display name (from ``text`` or ``title``)."""

    html_url: str = ""
    """Website link (from ``htmlUrl``)."""

    description: str = ""
    """Show description (non-standard but widely used)."""

    image_url: str = ""
    """Cover art URL (non-standard)."""

    categories: list[str] = field(default_factory=list)
    """Category breadcrumbs derived from parent ``<outline>`` nesting."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dict for JSON serialisation."""
        d: dict[str, Any] = {"url": self.url, "name": self.name}
        if self.html_url:
            d["html_url"] = self.html_url
        if self.description:
            d["description"] = self.description
        if self.image_url:
            d["image_url"] = self.image_url
        if self.categories:
            d["categories"] = self.categories
        return d


@dataclass
class OPMLDocument:
    """A parsed OPML document with metadata and feed entries."""

    title: str = ""
    """Document title from ``<head><title>``."""

    date_created: str = ""
    """Creation date from ``<head><dateCreated>``."""

    owner_name: str = ""
    """Owner name from ``<head><ownerName>``."""

    feeds: list[OPMLFeed] = field(default_factory=list)
    """All feed entries found in the document."""

    @property
    def feed_count(self) -> int:
        return len(self.feeds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "date_created": self.date_created,
            "owner_name": self.owner_name,
            "feed_count": self.feed_count,
            "feeds": [f.to_dict() for f in self.feeds],
        }


# ============================================================================
# Parsing (import)
# ============================================================================


def _collect_feeds(
    element: ET.Element,
    parent_categories: list[str] | None = None,
) -> list[OPMLFeed]:
    """Recursively collect feed outlines from an element tree.

    Handles nested ``<outline>`` groups (used by some exporters to
    organise feeds into folders / categories).
    """
    if parent_categories is None:
        parent_categories = []

    feeds: list[OPMLFeed] = []

    for outline in element.findall("outline"):
        xml_url = outline.get("xmlUrl", "").strip()

        if xml_url:
            # This is a feed outline
            name = (
                outline.get("text", "").strip()
                or outline.get("title", "").strip()
                or xml_url
            )
            feeds.append(OPMLFeed(
                url=xml_url,
                name=name,
                html_url=outline.get("htmlUrl", "").strip(),
                description=outline.get("description", "").strip(),
                image_url=outline.get("imageUrl", "").strip(),
                categories=list(parent_categories),
            ))
        else:
            # This is a folder / category — recurse into children
            folder_name = (
                outline.get("text", "").strip()
                or outline.get("title", "").strip()
            )
            child_categories = list(parent_categories)
            if folder_name:
                child_categories.append(folder_name)

            feeds.extend(_collect_feeds(outline, child_categories))

    return feeds


def parse_opml(xml_content: str) -> OPMLDocument:
    """Parse an OPML XML string into an :class:`OPMLDocument`.

    Lenient parsing:

    * Accepts any ``<outline>`` with a non-empty ``xmlUrl`` attribute,
      regardless of ``type``.
    * Handles nested outlines (folder hierarchies become category
      breadcrumbs on each feed).
    * Ignores malformed or non-feed outlines silently.

    Args:
        xml_content: Raw XML string of the OPML file.

    Returns:
        Parsed :class:`OPMLDocument`.

    Raises:
        ET.ParseError: If the XML is fundamentally malformed.
        ValueError: If no ``<body>`` element is found.
    """
    root = ET.fromstring(xml_content)

    # ── Head metadata ──────────────────────────────────────────
    head = root.find("head")
    title = ""
    date_created = ""
    owner_name = ""

    if head is not None:
        title_el = head.find("title")
        if title_el is not None and title_el.text:
            title = title_el.text.strip()

        date_el = head.find("dateCreated")
        if date_el is not None and date_el.text:
            date_created = date_el.text.strip()

        owner_el = head.find("ownerName")
        if owner_el is not None and owner_el.text:
            owner_name = owner_el.text.strip()

    # ── Body — collect feeds ───────────────────────────────────
    body = root.find("body")
    if body is None:
        raise ValueError("No <body> element found in OPML document")

    feeds = _collect_feeds(body)

    # Deduplicate by URL (keep first occurrence)
    seen_urls: set[str] = set()
    unique_feeds: list[OPMLFeed] = []
    for feed in feeds:
        normalised = feed.url.rstrip("/").lower()
        if normalised not in seen_urls:
            seen_urls.add(normalised)
            unique_feeds.append(feed)

    logger.info(
        "Parsed OPML: %d feeds (%d after dedup) from '%s'",
        len(feeds),
        len(unique_feeds),
        title or "(untitled)",
    )

    return OPMLDocument(
        title=title,
        date_created=date_created,
        owner_name=owner_name,
        feeds=unique_feeds,
    )


def parse_opml_safe(xml_content: str) -> OPMLDocument | None:
    """Like :func:`parse_opml` but returns ``None`` on any error."""
    try:
        return parse_opml(xml_content)
    except Exception as exc:
        logger.warning("Failed to parse OPML: %s", exc)
        return None


# ============================================================================
# Generation (export)
# ============================================================================


def generate_opml(
    feeds: list[dict[str, Any]],
    *,
    title: str = "Resonance Podcast Subscriptions",
    owner_name: str = "Resonance",
) -> str:
    """Generate a valid OPML 2.0 XML string from a list of feed dicts.

    Each dict should have at least ``"url"`` and ``"name"`` keys.
    Optional keys: ``"image"``, ``"author"``, ``"description"``,
    ``"html_url"``.

    Args:
        feeds: List of feed dicts (typically from ``PodcastStore.subscriptions``).
        title: Document title for the ``<head>`` section.
        owner_name: Owner name for the ``<head>`` section.

    Returns:
        UTF-8 XML string with ``<?xml …?>`` declaration.
    """
    opml = ET.Element("opml", version="2.0")

    # ── Head ───────────────────────────────────────────────────
    head = ET.SubElement(opml, "head")

    title_el = ET.SubElement(head, "title")
    title_el.text = title

    date_el = ET.SubElement(head, "dateCreated")
    date_el.text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    if owner_name:
        owner_el = ET.SubElement(head, "ownerName")
        owner_el.text = owner_name

    # ── Body ───────────────────────────────────────────────────
    body = ET.SubElement(opml, "body")

    for feed in feeds:
        url = feed.get("url", "")
        if not url:
            continue

        name = feed.get("name", "") or feed.get("title", "") or url

        attrib: dict[str, str] = {
            "text": name,
            "title": name,
            "type": "rss",
            "xmlUrl": url,
        }

        # Optional attributes
        html_url = feed.get("html_url", "") or feed.get("link", "")
        if html_url:
            attrib["htmlUrl"] = html_url

        description = feed.get("description", "")
        if description:
            attrib["description"] = description[:500]

        image_url = feed.get("image", "") or feed.get("image_url", "")
        if image_url:
            attrib["imageUrl"] = image_url

        ET.SubElement(body, "outline", attrib)

    # ── Serialise ──────────────────────────────────────────────
    ET.indent(opml, space="  ")

    xml_decl = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_body = ET.tostring(opml, encoding="unicode", xml_declaration=False)

    return xml_decl + xml_body + "\n"


# ============================================================================
# File I/O helpers
# ============================================================================


def import_opml_file(path: str | Path) -> OPMLDocument:
    """Read and parse an OPML file from disk.

    Args:
        path: Path to the OPML file.

    Returns:
        Parsed :class:`OPMLDocument`.

    Raises:
        FileNotFoundError: If the file does not exist.
        ET.ParseError: If the XML is malformed.
        ValueError: If the OPML structure is invalid.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"OPML file not found: {file_path}")

    # Try UTF-8 first, then fall back to latin-1 (some old OPML files)
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            content = file_path.read_text(encoding=encoding)
            return parse_opml(content)
        except UnicodeDecodeError:
            continue

    raise ValueError(f"Cannot decode OPML file: {file_path}")


def export_opml_file(
    path: str | Path,
    feeds: list[dict[str, Any]],
    *,
    title: str = "Resonance Podcast Subscriptions",
    owner_name: str = "Resonance",
) -> Path:
    """Write subscriptions to an OPML file on disk.

    Creates parent directories if they don't exist.  Uses atomic write
    (write-to-temp → rename) to prevent corruption.

    Args:
        path: Destination file path.
        feeds: List of feed dicts.
        title: Document title.
        owner_name: Owner name.

    Returns:
        The resolved :class:`Path` that was written.
    """
    import os
    import tempfile

    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    xml_content = generate_opml(
        feeds, title=title, owner_name=owner_name,
    )

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        suffix=".tmp",
        prefix="opml_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(xml_content)
    except BaseException:
        os.unlink(tmp_path)
        raise

    # On Windows, rename fails if target exists — remove first
    if file_path.exists():
        file_path.unlink()
    os.rename(tmp_path, file_path)

    logger.info("Exported %d subscriptions to %s", len(feeds), file_path)
    return file_path
