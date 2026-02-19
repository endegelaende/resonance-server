# Plugin Repository Guide

How to publish Resonance community plugins so they can be installed from the
built-in repository flow (`/api/plugins/repository`, `/api/plugins/install-from-repo`).

---

## 1) Overview

Resonance reads a repository index JSON file and shows available plugins in the
Plugins view. Installation downloads a ZIP and verifies its SHA256 hash before
extracting into `data/installed_plugins/<plugin_name>/`.

Current default index URL:

```text
https://resonance-plugins.github.io/repository/index.json
```

---

## 2) Plugin Package Requirements

Your ZIP must contain a valid plugin with:

- `plugin.toml` with `[plugin].name` and `[plugin].version`
- `__init__.py` with `async def setup(ctx)` (required)
- optional extra modules/assets

Accepted ZIP layouts:

```text
myplugin.zip
├── plugin.toml
├── __init__.py
└── ...
```

or

```text
myplugin-1.2.0.zip
└── myplugin/
    ├── plugin.toml
    ├── __init__.py
    └── ...
```

The installer detects and strips one common top-level folder automatically.

---

## 3) Build ZIP and SHA256

PowerShell example:

```powershell
Compress-Archive -Path .\plugins\myplugin\* -DestinationPath .\dist\myplugin-1.2.0.zip -Force
Get-FileHash .\dist\myplugin-1.2.0.zip -Algorithm SHA256
```

Use the lowercase SHA256 hex string in the repository entry.

---

## 4) Repository Index Format

Path: `repository/index.json`

```json
{
  "repository_version": 1,
  "name": "Resonance Community Plugins",
  "updated": "2026-02-19T12:00:00Z",
  "plugins": [
    {
      "name": "myplugin",
      "version": "1.2.0",
      "description": "Example plugin",
      "author": "Your Name",
      "category": "tools",
      "icon": "https://example.com/myplugin/icon.png",
      "min_resonance_version": "0.1.0",
      "url": "https://example.com/releases/myplugin-1.2.0.zip",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "homepage": "https://github.com/your-org/myplugin",
      "changelog": "https://github.com/your-org/myplugin/releases/tag/v1.2.0",
      "tags": ["utility", "example"]
    }
  ]
}
```

Recognized fields (used by Resonance):

- required in practice: `name`, `version`, `url`, `sha256`
- optional: `description`, `author`, `category`, `icon`, `min_resonance_version`,
  `homepage`, `changelog`, `tags`

If two repositories provide the same `name`, Resonance keeps the higher
`version`.

---

## 5) Hosting

Host both:

- `repository/index.json`
- plugin ZIP files referenced by `url`

Common setup:

- GitHub repository + GitHub Pages for static hosting
- releases/artifacts for ZIP files

Requirements:

- URLs must be publicly reachable by the Resonance server
- `url` should be stable and versioned (e.g. include `v1.2.0`)

---

## 6) Verify with Resonance

1. Refresh repository:
   - `GET /api/plugins/repository?force_refresh=true`
2. Install plugin:
   - `POST /api/plugins/install-from-repo` with `{ "name": "myplugin" }`
3. Check installed list:
   - `GET /api/plugins`

If installation fails, verify:

- SHA256 matches the exact ZIP bytes served by `url`
- ZIP contains valid `plugin.toml` and `__init__.py`
- plugin name in manifest is correct

---

## 7) Updates

To publish an update:

1. Bump plugin `version` in `plugin.toml`.
2. Build new ZIP.
3. Recompute SHA256.
4. Update repository entry (`version`, `url`, `sha256`, `changelog`).
5. Update top-level `updated` timestamp.

Resonance marks update availability by comparing `version` from repository with
installed plugin versions.

---

## 8) Security Checklist

- Always publish SHA256 for every ZIP.
- Never reuse a version string for different ZIP contents.
- Prefer immutable release URLs.
- Keep plugin dependencies explicit and minimal.
- Treat plugin code as trusted server-side code (same process as Resonance).
