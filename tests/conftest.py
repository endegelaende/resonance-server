"""
Shared pytest configuration and markers for optional dependencies.

Custom markers
--------------
- ``@pytest.mark.requires_pil``   – test needs Pillow (PIL) installed
- ``@pytest.mark.requires_tools`` – test needs external audio tools (flac, lame, sox, faad)

Usage examples::

    # Skip a single test when Pillow is missing:
    @pytest.mark.requires_pil
    def test_something_with_pil():
        ...

    # Run only tests that don't need optional deps:
    pytest -m "not requires_pil and not requires_tools"

    # Run everything (will auto-skip if deps are missing):
    pytest
"""

from __future__ import annotations

import shutil

import pytest

# ---------------------------------------------------------------------------
# Detect optional dependencies once at import time
# ---------------------------------------------------------------------------

def _pil_available() -> bool:
    """Return True if Pillow is importable."""
    try:
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _tool_available(name: str) -> bool:
    """Return True if *name* is found on ``$PATH`` or in ``third_party/bin/``."""
    if shutil.which(name) is not None:
        return True
    # Also check the bundled third_party/bin directory (Windows ships binaries there)
    from pathlib import Path
    third_party = Path(__file__).resolve().parent.parent / "third_party" / "bin"
    if third_party.is_dir():
        for candidate in third_party.iterdir():
            if candidate.stem == name:
                return True
    return False


HAS_PIL = _pil_available()
HAS_TOOLS = all(_tool_available(t) for t in ("flac", "lame", "sox"))


# ---------------------------------------------------------------------------
# Register markers so ``pytest --strict-markers`` doesn't complain
# ---------------------------------------------------------------------------

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_pil: mark test as requiring Pillow (auto-skipped if unavailable)",
    )
    config.addinivalue_line(
        "markers",
        "requires_tools: mark test as requiring external audio tools — flac, lame, sox "
        "(auto-skipped if unavailable)",
    )


# ---------------------------------------------------------------------------
# Auto-skip collection hook
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Automatically skip tests whose optional dependencies are missing."""

    skip_pil = pytest.mark.skip(reason="Pillow (PIL) not installed")
    skip_tools = pytest.mark.skip(
        reason="External audio tools (flac/lame/sox) not found on PATH",
    )

    for item in items:
        if "requires_pil" in item.keywords and not HAS_PIL:
            item.add_marker(skip_pil)
        if "requires_tools" in item.keywords and not HAS_TOOLS:
            item.add_marker(skip_tools)
