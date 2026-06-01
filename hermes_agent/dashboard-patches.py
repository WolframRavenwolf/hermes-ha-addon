#!/usr/bin/env python3
"""Apply Hermes dashboard compatibility patches for the Home Assistant add-on.

The add-on runs against a user-selected Hermes checkout, so the dashboard source
can be either modern and proxy-prefix aware, or older and root-path-only. Keep
startup tolerant: patch what we know how to patch, warn on upstream drift, and
never make dashboard patch drift stop the whole add-on.

Modern Hermes can derive its SPA base from X-Forwarded-Prefix, but Home
Assistant Ingress prefixes include a long random token and can exceed upstream's
current sanity limit. The add-on therefore also keeps an import.meta.url fallback
and a relative Vite asset base so HA users are not broken by upstream path
handling changes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


LEGACY_BASE_PATCH = (
    'export const BASE = new URL(/* @vite-ignore */ "..", import.meta.url)'
    '.pathname.replace(/\\/$/, ""); /* HA-ADDON-BASE-PATCHED */'
)
LEGACY_BASE_PATCH_WITHOUT_VITE_IGNORE = (
    'export const BASE = new URL("..", import.meta.url)'
    '.pathname.replace(/\\/$/, ""); /* HA-ADDON-BASE-PATCHED */'
)
LEGACY_BASE_PATCHES = (
    LEGACY_BASE_PATCH,
    LEGACY_BASE_PATCH_WITHOUT_VITE_IGNORE,
)
MODERN_BASE_FALLBACK_MARKER = "HA-ADDON-IMPORT-META-FALLBACK-PATCHED"
MODERN_BASE_FALLBACK_PATCH = (
    'const HERMES_IMPORT_META_BASE_PATH = new URL(/* @vite-ignore */ "..", import.meta.url)'
    '.pathname.replace(/\\/$/, ""); /* HA-ADDON-IMPORT-META-FALLBACK-PATCHED */\n'
    'export const HERMES_BASE_PATH = readBasePath() || HERMES_IMPORT_META_BASE_PATH;'
)
VITE_BASE_MARKER = "HA-ADDON-BASE-INJECTED"
PREFIX_LIMIT_MARKER = "HA-ADDON-PREFIX-LIMIT-PATCHED"
# HA Ingress prepends `/api/hassio_ingress/<32-char-token>` (~50 chars). Add a
# nested addon path like `/profile/<name>/dashboard` and the total easily
# exceeds upstream's default 64-char ceiling, causing the SPA serve handler to
# skip the asset URL rewrite and the browser to 404 on `/assets/...`.
PREFIX_LIMIT_RAISED = 256


def read(path: Path) -> str:
    return path.read_text() if path.is_file() else ""


def write_if_changed(path: Path, old_text: str, new_text: str) -> bool:
    if new_text == old_text:
        return False
    path.write_text(new_text)
    return True


def patch_modern_dashboard(api: Path, api_text: str) -> bool:
    """Make modern Hermes dashboard paths robust behind HA Ingress."""
    if not api.is_file():
        return False

    changed = False
    patched = api_text
    matched_patch = next((patch for patch in LEGACY_BASE_PATCHES if patch in patched), "")
    if matched_patch:
        patched = patched.replace(matched_patch, "const BASE = HERMES_BASE_PATH;")
        changed = True
        print("[run] Removed obsolete dashboard BASE source patch")

    if MODERN_BASE_FALLBACK_MARKER not in patched:
        patched, count = re.subn(
            r"^export const HERMES_BASE_PATH = readBasePath\(\);$",
            MODERN_BASE_FALLBACK_PATCH,
            patched,
            count=1,
            flags=re.MULTILINE,
        )
        if count:
            changed = True
            print("[run] Patched modern dashboard import.meta.url base fallback")
        else:
            print("[run] WARNING: api.ts HERMES_BASE_PATH pattern changed upstream - dashboard paths may need review")

    if write_if_changed(api, api_text, patched):
        return True
    return changed


def patch_legacy_api(api: Path, api_text: str) -> bool:
    if not api.is_file() or "HA-ADDON-BASE-PATCHED" in api_text:
        return False

    patched, count = re.subn(
        r"^const BASE = .*$",
        LEGACY_BASE_PATCH,
        api_text,
        count=1,
        flags=re.MULTILINE,
    )
    if not count:
        print("[run] WARNING: api.ts BASE pattern changed upstream - dashboard API paths may need review")
        return False

    if write_if_changed(api, api_text, patched):
        print("[run] Patched legacy dashboard API base path")
        return True
    return False


def patch_legacy_plugins(plugins: Path, plugins_text: str) -> bool:
    if not plugins.is_file() or "HA-ADDON-PLUGINS-PATCHED" in plugins_text:
        return False

    patched = plugins_text.replace(
        'import { api } from "@/lib/api";',
        'import { api, BASE } from "@/lib/api"; /* HA-ADDON-PLUGINS-PATCHED */',
        1,
    )
    patched = patched.replace("`/dashboard-plugins/", "`${BASE}/dashboard-plugins/")

    if patched == plugins_text:
        print("[run] WARNING: usePlugins.ts URL pattern changed upstream - dashboard plugins may 404")
        return False

    if write_if_changed(plugins, plugins_text, patched):
        print("[run] Patched legacy dashboard plugin asset paths")
        return True
    return False


def patch_legacy_router(main_tsx: Path, main_text: str) -> bool:
    if not main_tsx.is_file() or "HA-ADDON-ROUTER-BASENAME-PATCHED" in main_text:
        return False

    patched = main_text.replace(
        'import { BrowserRouter } from "react-router-dom";',
        'import { BrowserRouter } from "react-router-dom";\n'
        'import { BASE } from "@/lib/api"; /* HA-ADDON-ROUTER-BASENAME-PATCHED */',
        1,
    )
    patched = patched.replace("<BrowserRouter>", '<BrowserRouter basename={BASE || "/"}>', 1)

    if patched == main_text or 'basename={BASE || "/"}' not in patched:
        print("[run] WARNING: main.tsx BrowserRouter pattern changed upstream - dashboard links may 404 behind /dashboard/")
        return False

    if write_if_changed(main_tsx, main_text, patched):
        print("[run] Patched legacy dashboard router base path")
        return True
    return False


def ensure_vite_relative_base(vite: Path, vite_text: str) -> bool:
    """Force Vite to emit relative JS/CSS asset URLs for HA Ingress."""
    if not vite.is_file():
        return False
    if VITE_BASE_MARKER in vite_text and re.search(r'^\s*base:\s*"\./",', vite_text, flags=re.MULTILINE):
        return False

    cleaned = re.sub(
        r'^\s*/\* HA-ADDON-BASE-INJECTED \*/\s*\n\s*base:\s*["\'][^"\']*["\'],\s*\n',
        "",
        vite_text,
        flags=re.MULTILINE,
    )
    cleaned = re.sub(
        r'^\s*base:\s*["\'][^"\']*["\'],\s*\n',
        "",
        cleaned,
        flags=re.MULTILINE,
    )
    patched = cleaned.replace(
        "export default defineConfig({",
        'export default defineConfig({\n  /* HA-ADDON-BASE-INJECTED */\n  base: "./",\n',
        1,
    )

    if patched == vite_text:
        return False
    if patched == cleaned:
        print("[run] WARNING: vite.config.ts defineConfig pattern changed upstream - dashboard assets may need review")
        return False

    if write_if_changed(vite, vite_text, patched):
        print("[run] Patched dashboard Vite relative asset base")
        return True
    return False


def patch_prefix_length_limit(prefix_py: Path, text: str) -> bool:
    """Raise upstream's X-Forwarded-Prefix length ceiling for HA Ingress.

    Upstream rejects prefixes longer than 64 characters as a header-injection
    guard. HA Ingress' own token segment already eats most of that budget,
    leaving no room for nested addon routes (`/profile/<name>/dashboard`).
    """
    if not prefix_py.is_file():
        return False
    if PREFIX_LIMIT_MARKER in text:
        return False
    patched, count = re.subn(
        r"if len\(p\) > 64:",
        f"if len(p) > {PREFIX_LIMIT_RAISED}:  # {PREFIX_LIMIT_MARKER}",
        text,
        count=1,
    )
    if not count:
        print(
            "[run] WARNING: prefix.py length check pattern changed upstream - "
            "nested addon routes (/profile/...) may 404 on dashboard assets"
        )
        return False
    if write_if_changed(prefix_py, text, patched):
        print("[run] Patched dashboard prefix length limit for HA Ingress")
        return True
    return False


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: dashboard-patches.py SRC_DIR STATUS_FILE", file=sys.stderr)
        return 2

    src = Path(sys.argv[1])
    status_file = Path(sys.argv[2])
    api = src / "web/src/lib/api.ts"
    plugins = src / "web/src/plugins/usePlugins.ts"
    main_tsx = src / "web/src/main.tsx"
    vite = src / "web/vite.config.ts"
    prefix_py = src / "hermes_cli/dashboard_auth/prefix.py"

    changed = False
    api_text = read(api)
    modern_dashboard = "HERMES_BASE_PATH" in api_text and "__HERMES_BASE_PATH__" in api_text

    if modern_dashboard:
        changed |= patch_modern_dashboard(api, api_text)
    else:
        changed |= patch_legacy_api(api, api_text)
        changed |= patch_legacy_plugins(plugins, read(plugins))
        changed |= patch_legacy_router(main_tsx, read(main_tsx))
    changed |= ensure_vite_relative_base(vite, read(vite))
    changed |= patch_prefix_length_limit(prefix_py, read(prefix_py))

    status_file.write_text("changed" if changed else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
