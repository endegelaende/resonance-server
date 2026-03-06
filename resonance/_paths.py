"""
Central path resolution for Resonance.

This module provides canonical paths to bundled resources (core plugins,
static assets, web-ui build) that work **both** when running from a
source checkout *and* after ``pip install resonance-server``.

Strategy
--------
1. **Repo mode** — detected when ``<project_root>/pyproject.toml`` exists.
   All paths resolve relative to the repository root (two levels up from
   this file: ``resonance/_paths.py`` → ``resonance/`` → ``<root>``).

2. **Installed mode** — when there is no ``pyproject.toml`` next to the
   repo root, resources have been packed *inside* the wheel via
   ``[tool.hatch.build.targets.wheel.force-include]`` and live under
   ``resonance/_bundled/``.

Every other module that needs these paths should import from here
instead of hard-coding ``Path("plugins")`` or ``Path(__file__).parent…``.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package directory  (…/resonance/)
# ---------------------------------------------------------------------------
_PACKAGE_DIR: Path = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Repository root (only valid in repo/dev mode)
# ---------------------------------------------------------------------------
_REPO_ROOT: Path = _PACKAGE_DIR.parent

# ---------------------------------------------------------------------------
# Detect mode
# ---------------------------------------------------------------------------
_IS_REPO_MODE: bool = (_REPO_ROOT / "pyproject.toml").is_file()

if _IS_REPO_MODE:
    logger.debug("Path resolution: repo mode (root=%s)", _REPO_ROOT)
else:
    logger.debug("Path resolution: installed/bundled mode (package=%s)", _PACKAGE_DIR)


# ---------------------------------------------------------------------------
# Public path accessors
# ---------------------------------------------------------------------------

def core_plugins_dir() -> Path:
    """Return the directory containing built-in (core) plugins.

    Repo mode:   ``<repo>/plugins``
    Installed:   ``<package>/_bundled/plugins``
    """
    if _IS_REPO_MODE:
        return _REPO_ROOT / "plugins"
    return _PACKAGE_DIR / "_bundled" / "plugins"


def static_html_dir() -> Path:
    """Return the ``static/html`` directory (LMS-compatible assets).

    Repo mode:   ``<repo>/static/html``
    Installed:   ``<package>/_bundled/static/html``
    """
    if _IS_REPO_MODE:
        return _REPO_ROOT / "static" / "html"
    return _PACKAGE_DIR / "_bundled" / "static" / "html"


def webui_build_dir() -> Path:
    """Return the Svelte production build directory.

    Repo mode:   ``<repo>/web-ui/build``
    Installed:   ``<package>/_bundled/web-ui/build``
    """
    if _IS_REPO_MODE:
        return _REPO_ROOT / "web-ui" / "build"
    return _PACKAGE_DIR / "_bundled" / "web-ui" / "build"


def repo_root() -> Path | None:
    """Return the repository root, or *None* when running from an install."""
    if _IS_REPO_MODE:
        return _REPO_ROOT
    return None


def is_repo_mode() -> bool:
    """``True`` when running from a source checkout."""
    return _IS_REPO_MODE
