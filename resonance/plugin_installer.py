"""Community plugin installer."""

from __future__ import annotations

import hashlib
import io
import logging
import shutil
import tomllib
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


class PluginInstaller:
    """Install and uninstall community plugins under data/installed_plugins."""

    def __init__(self, install_dir: Path | None = None) -> None:
        self.install_dir = install_dir or Path("data/installed_plugins")

    def install_from_zip(self, zip_data: bytes, expected_sha256: str) -> str:
        """Install a plugin from zip bytes after SHA256 verification."""
        actual_sha256 = hashlib.sha256(zip_data).hexdigest()
        if actual_sha256 != expected_sha256.lower():
            raise ValueError(
                f"SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            )

        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_data))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid zip file: {exc}") from exc

        plugin_toml_path = self._find_plugin_toml(zf)
        if plugin_toml_path is None:
            raise ValueError("Zip file does not contain a plugin.toml")

        plugin_name = self._extract_plugin_name(zf, plugin_toml_path)
        target_dir = self.install_dir / plugin_name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        prefix = self._detect_prefix(zf, plugin_toml_path)
        target_root = target_dir.resolve()

        for member in zf.infolist():
            if member.is_dir():
                continue
            rel_path = member.filename.replace("\\", "/")
            if prefix and rel_path.startswith(prefix):
                rel_path = rel_path[len(prefix):]
            if not rel_path:
                continue

            dest = (target_dir / Path(rel_path)).resolve()
            # Zip-slip guard: extracted file must stay inside plugin dir.
            if target_root not in dest.parents and dest != target_root:
                raise ValueError(f"Unsafe zip path: {member.filename}")

            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as dst:
                dst.write(src.read())

        logger.info("Installed plugin '%s' to %s", plugin_name, target_dir)
        return plugin_name

    def uninstall(self, plugin_name: str) -> bool:
        """Remove an installed community plugin directory."""
        target_dir = self.install_dir / plugin_name
        if not target_dir.is_dir():
            return False
        shutil.rmtree(target_dir)
        logger.info("Uninstalled plugin '%s' from %s", plugin_name, target_dir)
        return True

    def is_installed(self, plugin_name: str) -> bool:
        return (self.install_dir / plugin_name / "plugin.toml").is_file()

    def list_installed(self) -> list[str]:
        if not self.install_dir.is_dir():
            return []
        return [
            directory.name
            for directory in sorted(self.install_dir.iterdir())
            if directory.is_dir() and (directory / "plugin.toml").is_file()
        ]

    def _find_plugin_toml(self, zf: zipfile.ZipFile) -> str | None:
        candidates = [name for name in zf.namelist() if name.endswith("plugin.toml")]
        if not candidates:
            return None
        candidates.sort(key=lambda value: (value.count("/"), value))
        return candidates[0]

    def _detect_prefix(self, zf: zipfile.ZipFile, toml_path: str) -> str:
        _ = zf
        parts = toml_path.rsplit("/", 1)
        if len(parts) == 2:
            return parts[0] + "/"
        return ""

    def _extract_plugin_name(self, zf: zipfile.ZipFile, toml_path: str) -> str:
        try:
            toml_data = zf.read(toml_path)
            parsed = tomllib.loads(toml_data.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Failed to parse plugin.toml: {exc}") from exc

        plugin_name = parsed.get("plugin", {}).get("name")
        if not plugin_name:
            raise ValueError("plugin.toml is missing [plugin].name")
        return str(plugin_name)
