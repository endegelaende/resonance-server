# Third-Party Notices

This file documents the licenses of third-party software used by Resonance.

Resonance itself is licensed under the **GNU General Public License v2.0** (GPL-2.0).
See [LICENSE](LICENSE) for the full text.

---

## Shipped Binaries (third_party/bin/)

These binaries are included in the repository for Windows. On Linux, users
install them separately (see [README.md](README.md#transcoding-tools-optional)).

| Binary | License | Source | Notes |
|--------|---------|--------|-------|
| **faad** | GPL-2.0 | [ralph-irving/faad2](https://github.com/ralph-irving/faad2) | LMS-patched version with seeking (-j/-e) and ALAC support |
| **flac** | BSD-like (Xiph.org) | [xiph.org/flac](https://xiph.org/flac/) | Reference FLAC encoder/decoder |
| **lame** | LGPL-2.0 | [lame.sourceforge.io](https://lame.sourceforge.io/) | MP3 encoder |
| **sox** | GPL-2.0 | [sox.sourceforge.net](https://sox.sourceforge.net/) | Sound eXchange — audio conversion tool |

### Squeezelite (third_party/squeezelite/)

| Binary | License | Source |
|--------|---------|--------|
| **squeezelite** | GPL-3.0 (with OpenSSL exception) | [ralph-irving/squeezelite](https://github.com/ralph-irving/squeezelite) |

See [third_party/squeezelite/LICENSE.txt](third_party/squeezelite/LICENSE.txt) for the full license text
including the DSD code BSD-2-Clause notice.

---

## Python Dependencies (pip)

### Runtime (required)

| Package | License | URL |
|---------|---------|-----|
| **mutagen** | GPL-2.0+ | https://github.com/quodlibet/mutagen |
| **aiosqlite** | MIT | https://github.com/omnilib/aiosqlite |
| **fastapi** | MIT | https://github.com/fastapi/fastapi |
| **uvicorn** | BSD-3-Clause | https://github.com/encode/uvicorn |

Transitive runtime dependencies:

| Package | License | URL |
|---------|---------|-----|
| starlette | BSD-3-Clause | https://github.com/encode/starlette |
| pydantic | MIT | https://github.com/pydantic/pydantic |
| pydantic-core | MIT | https://github.com/pydantic/pydantic-core |
| annotated-types | MIT | https://github.com/annotated-types/annotated-types |
| typing-extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection | MIT | https://github.com/pydantic/typing-inspection |
| annotated-doc | MIT | https://github.com/fastapi/annotated-doc |
| anyio | MIT | https://github.com/agronholm/anyio |
| click | BSD-3-Clause | https://github.com/pallets/click |
| h11 | MIT | https://github.com/python-hyper/h11 |
| idna | BSD-3-Clause | https://github.com/kjd/idna |
| sniffio | MIT/Apache-2.0 | https://github.com/python-trio/sniffio |

### Optional

| Package | License | URL | Purpose |
|---------|---------|-----|---------|
| blurhash-python | MIT | https://github.com/woltapp/blurhash | Blurred cover art placeholders |
| Pillow | HPND (Historical Permission Notice and Disclaimer) | https://github.com/python-pillow/Pillow | Image processing for BlurHash |

### Dev only (not shipped)

| Package | License | URL |
|---------|---------|-----|
| pytest | MIT | https://github.com/pytest-dev/pytest |
| pytest-asyncio | Apache-2.0 | https://github.com/pytest-dev/pytest-asyncio |
| pytest-cov | MIT | https://github.com/pytest-dev/pytest-cov |
| httpx | BSD-3-Clause | https://github.com/encode/httpx |
| mypy | MIT | https://github.com/python/mypy |
| ruff | MIT | https://github.com/astral-sh/ruff |

---

## Web UI Dependencies (npm)

The Web UI is built with Svelte 5, SvelteKit, and Tailwind CSS v4.
All production npm dependencies use permissive licenses.

### License summary (production only)

| License | Count |
|---------|-------|
| MIT | 74 |
| Apache-2.0 | 2 |
| BSD-3-Clause | 2 |
| ISC | 2 |
| BSD-2-Clause | 1 |
| MIT AND Zlib | 1 |

### Key production dependencies

| Package | License | URL |
|---------|---------|-----|
| svelte | MIT | https://github.com/sveltejs/svelte |
| @sveltejs/kit | MIT | https://github.com/sveltejs/kit |
| @sveltejs/adapter-static | MIT | https://github.com/sveltejs/kit |
| @tailwindcss/vite | MIT | https://github.com/tailwindlabs/tailwindcss |
| tailwindcss | MIT | https://github.com/tailwindlabs/tailwindcss |
| vite | MIT | https://github.com/vitejs/vite |
| lucide-svelte | ISC | https://github.com/lucide-icons/lucide |
| node-vibrant | MIT | https://github.com/Vibrant-Colors/node-vibrant |
| blurhash | MIT | https://github.com/woltapp/blurhash |
| clsx | MIT | https://github.com/lukeed/clsx |
| devalue | MIT | https://github.com/sveltejs/devalue |

For the complete list, run:

```bash
cd web-ui
npx license-checker --production --summary
```

---

## License Compatibility

Resonance is GPL-2.0. All dependencies are compatible:

- **MIT, BSD, ISC, Apache-2.0, PSF-2.0** — permissive licenses, compatible with GPL-2.0
- **LGPL-2.0** (lame) — compatible, used as separate executable (not linked)
- **GPL-2.0+** (mutagen, faad, sox) — same license family
- **GPL-3.0** (squeezelite) — separate executable, not linked into Resonance
- **MPL-2.0** (certifi, lightningcss) — compatible with GPL-2.0 per MPL Section 3.3

---

*Last updated: February 2026*