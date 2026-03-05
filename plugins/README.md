# Core Plugins

Built-in plugins that ship with Resonance. Each plugin lives in its own
directory with a `plugin.toml` manifest and an `__init__.py` entry point.

---

## Included Plugins

| Plugin                            | Category | Description                                                                    |
| --------------------------------- | -------- | ------------------------------------------------------------------------------ |
| [`example`](example/)             | tools    | Minimal template — start here when creating a new plugin                       |
| [`favorites`](favorites/)         | music    | Manage favorite tracks, albums, and artists (JSON-RPC + Jive menus)            |
| [`nowplaying`](nowplaying/)       | music    | Track play history and statistics — companion code for the Plugin Tutorial     |
| [`radio`](radio/)                 | radio    | Internet radio via radio-browser.info (~40 000 stations, ContentProvider)      |
| [`podcast`](podcast/)             | podcast  | Podcast subscriptions, RSS feeds, resume playback, OPML import/export          |

## Community Plugins

Community-contributed plugins live in a separate repository:
→ [resonance-community-plugins](https://github.com/endegelaende/resonance-community-plugins)

Notable community plugins:

| Plugin                                                                                       | Description                                              |
| -------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| [raopbridge](https://github.com/endegelaende/resonance-community-plugins/tree/main/plugins/raopbridge) | AirPlay bridge with full SDUI web interface |

---

## Creating Your Own Plugin

1. Copy `example/` to a new directory (e.g. `plugins/my-plugin/`)
2. Edit `plugin.toml` with your plugin's metadata
3. Implement `setup(ctx)` in `__init__.py`
4. Restart the server — your plugin loads automatically

### Documentation

| Document                                                     | Content                                  |
| ------------------------------------------------------------ | ---------------------------------------- |
| [Plugin Tutorial](../docs/PLUGIN_TUTORIAL.md)                | Step-by-step guide (build from scratch)  |
| [Plugin API Reference](../docs/PLUGIN_API.md)                | Complete API docs (incl. §19 SDUI)       |
| [Plugin System Overview](../docs/PLUGINS.md)                 | Non-technical overview for all audiences  |

### Minimal `plugin.toml`

```toml
[plugin]
name = "my-plugin"
version = "0.1.0"
description = "What my plugin does"
author = "Your Name"
min_resonance_version = "0.1.0"
category = "tools"
```

### Minimal `__init__.py`

```python
import logging

logger = logging.getLogger(__name__)

async def setup(ctx):
    logger.info("%s loaded", ctx.plugin_name)
```

---

## Plugin Anatomy

```
plugins/<name>/
├── plugin.toml          # Manifest (name, version, category, settings, UI)
├── __init__.py          # Entry point: setup(), teardown(), get_ui(), handle_action()
├── store.py             # Data persistence (optional)
└── ...                  # Additional modules as needed
```

## Rules

- **Names:** lowercase, hyphen-separated (`my-cool-plugin`)
- **Versioning:** SemVer (`MAJOR.MINOR.PATCH`)
- **Logging:** `logging.getLogger(__name__)` — never `print()`
- **Settings:** Use `SettingDefinition` in `plugin.toml`, no hardcoded values
- **Security:** No credentials in plain text, use `masked = true` for secrets
- **Cleanup:** `teardown()` must release all resources
