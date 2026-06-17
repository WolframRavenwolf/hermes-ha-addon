"""Regression tests for Home Assistant Ingress dashboard routing patches."""

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = ROOT / "hermes_agent" / "dashboard-patches.py"
RUN_SH = ROOT / "hermes_agent" / "run.sh"
NGINX_RENDER_LIB = ROOT / "hermes_agent" / "nginx-render.sh"
NGINX_TEMPLATE = ROOT / "hermes_agent" / "nginx.conf.tpl"
NGINX_PORTS_TEMPLATE = ROOT / "hermes_agent" / "nginx-ports.conf.tpl"
ADDON_CONFIG = ROOT / "hermes_agent" / "config.yaml"
LANDING_TEMPLATE = ROOT / "hermes_agent" / "landing.html.tpl"


def run_dashboard_patches(src: Path, status_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PATCH_SCRIPT), str(src), str(status_file)],
        check=False,
        text=True,
        capture_output=True,
    )


def render_nginx_fragment(*, server_kind: str, profiles=None, flags=None) -> str:
    """Source the nginx-render lib with a mock profile setup and capture a fragment."""
    if profiles is None:
        profiles = [(".hermes", "hermes", ""), ("amy", "amy", "/profile/amy")]
    flags = flags or {}
    enable_terminal = flags.get("enable_terminal", "true")
    enable_api = flags.get("enable_api", "true")
    enable_dashboard = flags.get("enable_dashboard", "true")
    dashboard_available = flags.get("dashboard_available", "true")

    dirs = " ".join(f'"{d}"' for d, _, _ in profiles)
    names = " ".join(f'"{n}"' for _, n, _ in profiles)
    prefixes = " ".join(f'"{p}"' for _, _, p in profiles)
    n = len(profiles)
    ports_api = " ".join(str(8642 + i) for i in range(n))
    ports_th = " ".join(str(49269 + i) for i in range(n))
    ports_tt = " ".join(str(49369 + i) for i in range(n))
    ports_dash = " ".join(str(49469 + i) for i in range(n))
    tokens = " ".join(f'"TOK{i}"' for i in range(n))

    script = textwrap.dedent(f"""
        set -euo pipefail
        source "{NGINX_RENDER_LIB}"
        PROFILE_DIRS=({dirs})
        PROFILE_NAMES=({names})
        PROFILE_PATH_PREFIX=({prefixes})
        API_PORTS=({ports_api})
        TTYD_HERMES_PORTS=({ports_th})
        TTYD_TERMINAL_PORTS=({ports_tt})
        DASHBOARD_PORTS=({ports_dash})
        DASHBOARD_TOKENS=({tokens})
        DASHBOARD_AVAILABLE="{dashboard_available}"
        ENABLE_TERMINAL="{enable_terminal}"
        ENABLE_API="{enable_api}"
        ENABLE_DASHBOARD="{enable_dashboard}"
        emit_profile_locations {server_kind}
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def render_nginx_section(section: str, *, profiles=None) -> str:
    """Render upstreams / dashboard_maps / token_maps from the lib."""
    if profiles is None:
        profiles = [(".hermes", "hermes", ""), ("amy", "amy", "/profile/amy")]
    dirs = " ".join(f'"{d}"' for d, _, _ in profiles)
    names = " ".join(f'"{n}"' for _, n, _ in profiles)
    prefixes = " ".join(f'"{p}"' for _, _, p in profiles)
    n = len(profiles)
    ports_api = " ".join(str(8642 + i) for i in range(n))
    ports_th = " ".join(str(49269 + i) for i in range(n))
    ports_tt = " ".join(str(49369 + i) for i in range(n))
    ports_dash = " ".join(str(49469 + i) for i in range(n))
    tokens = " ".join(f'"TOK{i}"' for i in range(n))

    script = textwrap.dedent(f"""
        set -euo pipefail
        source "{NGINX_RENDER_LIB}"
        PROFILE_DIRS=({dirs})
        PROFILE_NAMES=({names})
        PROFILE_PATH_PREFIX=({prefixes})
        API_PORTS=({ports_api})
        TTYD_HERMES_PORTS=({ports_th})
        TTYD_TERMINAL_PORTS=({ports_tt})
        DASHBOARD_PORTS=({ports_dash})
        DASHBOARD_TOKENS=({tokens})
        emit_{section}
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def write_modern_dashboard_fixture(src: Path, vite_text: str = "export default defineConfig({});\n") -> None:
    """Create a minimal current-upstream-shaped dashboard source tree."""
    (src / "web/src/lib").mkdir(parents=True)
    (src / "web/src/plugins").mkdir(parents=True)
    (src / "web").mkdir(exist_ok=True)
    (src / "web/src/lib/api.ts").write_text(
        "function readBasePath(): string {\n"
        "  const raw = window.__HERMES_BASE_PATH__ ?? \"\";\n"
        "  return raw;\n"
        "}\n"
        "export const HERMES_BASE_PATH = readBasePath();\n"
        "const BASE = HERMES_BASE_PATH;\n"
        "declare global { interface Window { __HERMES_BASE_PATH__?: string; } }\n"
    )
    (src / "web/src/plugins/usePlugins.ts").write_text(
        'import { api, HERMES_BASE_PATH } from "@/lib/api";\n'
        "const baseUrl = `${HERMES_BASE_PATH}/dashboard-plugins/x.js`;\n"
    )
    (src / "web/src/main.tsx").write_text(
        'import { HERMES_BASE_PATH } from "./lib/api";\n'
        "<BrowserRouter basename={HERMES_BASE_PATH || undefined}>\n"
    )
    (src / "web/vite.config.ts").write_text(vite_text)


class DashboardIngressPatchTests(unittest.TestCase):
    def test_dashboard_router_uses_runtime_base_as_basename(self) -> None:
        """React Router must generate /dashboard/* links behind HA Ingress.

        The add-on serves the SPA below /dashboard/. API/assets were already
        base-aware, but BrowserRouter without a basename still emitted top-level
        links like /logs. Those work for in-app navigation but 404 on frame
        reload because nginx only proxies dashboard traffic below /dashboard/.
        """
        patch_script = PATCH_SCRIPT.read_text()

        self.assertIn("HA-ADDON-ROUTER-BASENAME-PATCHED", patch_script)
        self.assertIn('import { BASE } from "@/lib/api";', patch_script)
        self.assertIn('basename={BASE || "/"}', patch_script)

    def test_nginx_keeps_dashboard_deep_links_under_dashboard_prefix(self) -> None:
        """Direct /dashboard/<route> reloads must keep proxying to the SPA."""
        rendered = render_nginx_fragment(server_kind="ingress")

        self.assertIn("location = /dashboard { return 302 /dashboard/; }", rendered)
        self.assertIn("location /dashboard/api/", rendered)
        self.assertIn("location /dashboard/", rendered)
        self.assertIn("proxy_pass http://hermes_dashboard_0/;", rendered)

    def test_direct_port_dashboard_api_accepts_spa_session_token_sources(self) -> None:
        """Direct-port WebSockets must survive Basic auth plus ?token= auth."""
        token_maps = render_nginx_section("token_maps")

        self.assertIn("$http_x_hermes_session_token", token_maps)
        self.assertIn("$http_authorization", token_maps)
        self.assertIn("$arg_token", token_maps)
        self.assertIn("$dashboard_token_ok_0", token_maps)
        self.assertIn("~^TOK0\\|", token_maps)
        self.assertIn("~^\\|Bearer\\ TOK0\\|", token_maps)
        self.assertIn("~\\|TOK0$", token_maps)
        # Per-profile maps are emitted, one per profile.
        self.assertIn("$dashboard_token_ok_1", token_maps)

    def test_addon_enables_ingress_stream_for_websockets(self) -> None:
        """Home Assistant Ingress must stream WebSocket traffic to the add-on."""
        addon_config = ADDON_CONFIG.read_text()

        self.assertIn("ingress: true", addon_config)
        self.assertIn("ingress_port:", addon_config)
        self.assertIn("ingress_stream: true", addon_config)

    def test_nginx_sets_map_hash_bucket_size_before_dashboard_maps(self) -> None:
        """nginx rejects map_hash_bucket_size after any map block has been parsed."""
        nginx_conf = NGINX_TEMPLATE.read_text()
        nginx_ports = NGINX_PORTS_TEMPLATE.read_text()

        # The template's static prelude sets the hash size; per-profile map blocks
        # are inserted at the %%DASHBOARD_MAPS%% marker that follows it.
        self.assertIn("map_hash_bucket_size 128;", nginx_conf)
        self.assertLess(
            nginx_conf.index("map_hash_bucket_size 128;"),
            nginx_conf.index("%%DASHBOARD_MAPS%%"),
        )
        self.assertNotIn("map_hash_bucket_size", nginx_ports)

    def test_nginx_dashboard_api_locations_support_websocket_upgrades(self) -> None:
        """Dashboard chat WebSockets share /dashboard/api/ with REST calls."""
        nginx_conf = NGINX_TEMPLATE.read_text()

        self.assertIn("map $http_upgrade $connection_upgrade", nginx_conf)
        self.assertLess(
            nginx_conf.index("map $http_upgrade $connection_upgrade"),
            nginx_conf.index("%%DASHBOARD_MAPS%%"),
        )

        for server_kind in ("ingress", "http", "https"):
            rendered = render_nginx_fragment(server_kind=server_kind)
            for prefix in ("", "/profile/amy"):
                start = rendered.index(f"location {prefix}/dashboard/api/")
                end = rendered.index(f"location {prefix}/dashboard/", start + 1)
                block = rendered[start:end]
                self.assertIn("proxy_set_header Upgrade $http_upgrade;", block)
                self.assertIn("proxy_set_header Connection $connection_upgrade;", block)
                self.assertIn('proxy_set_header Origin "http://127.0.0.1";', block)

    def test_nginx_forwards_dashboard_prefix_to_modern_hermes(self) -> None:
        """Modern Hermes reads X-Forwarded-Prefix to set SPA base paths."""
        dashboard_maps = render_nginx_section("dashboard_maps")
        ingress_fragment = render_nginx_fragment(server_kind="ingress")
        http_fragment = render_nginx_fragment(server_kind="http")
        https_fragment = render_nginx_fragment(server_kind="https")

        # Primary keeps the legacy "/dashboard" suffix; non-primary uses its prefix.
        self.assertIn("map $http_x_forwarded_prefix $dashboard_proxy_prefix_0", dashboard_maps)
        self.assertIn("map $http_x_ingress_path $dashboard_forwarded_prefix_0", dashboard_maps)
        self.assertIn('default "$http_x_ingress_path/dashboard";', dashboard_maps)
        self.assertIn('default "$http_x_ingress_path/profile/amy/dashboard";', dashboard_maps)

        # Ingress: 2 dashboard locations × 2 profiles = 4 X-Forwarded-Prefix headers.
        self.assertEqual(ingress_fragment.count("proxy_set_header X-Forwarded-Prefix"), 4)
        # Direct HTTP/HTTPS: 3 dashboard locations (status, api/, /) × 2 profiles = 6.
        self.assertEqual(http_fragment.count("proxy_set_header X-Forwarded-Prefix"), 6)
        self.assertEqual(https_fragment.count("proxy_set_header X-Forwarded-Prefix"), 6)

    def test_nginx_non_primary_profile_uses_path_prefix(self) -> None:
        """Non-primary profiles must be reachable under /profile/<name>/..."""
        rendered = render_nginx_fragment(server_kind="ingress")

        self.assertIn("location /profile/amy/hermes/", rendered)
        self.assertIn("location /profile/amy/terminal/", rendered)
        self.assertIn("location /profile/amy/v1/", rendered)
        self.assertIn("location /profile/amy/dashboard/", rendered)
        # Each non-primary location proxies to its own per-profile upstream.
        self.assertIn("proxy_pass http://hermes_dashboard_1/;", rendered)
        self.assertIn("proxy_pass http://hermes_api_1/v1/;", rendered)

    def test_nginx_upstreams_are_per_profile(self) -> None:
        upstreams = render_nginx_section("upstreams")

        self.assertIn("upstream hermes_api_0", upstreams)
        self.assertIn("upstream hermes_api_1", upstreams)
        self.assertIn("server 127.0.0.1:8642", upstreams)
        self.assertIn("server 127.0.0.1:8643", upstreams)

    def test_run_script_delegates_dashboard_patches_to_helper(self) -> None:
        """The startup path should not contain fragile multi-expression sed edits."""
        run_sh = RUN_SH.read_text()

        self.assertIn("hermes-dashboard-patches", run_sh)
        self.assertIn('/usr/local/bin/hermes-dashboard-patches "$SRC_DIR" "$status_file"', run_sh)
        self.assertNotIn('hermes-dashboard-patches "$src_dir"', run_sh)
        self.assertNotIn("python /usr/local/bin/hermes-dashboard-patches", run_sh)
        self.assertNotIn("BASE ||", run_sh)
        self.assertNotIn("HA-ADDON-ROUTER-BASENAME-PATCHED", run_sh)

    def test_run_script_preserves_profiles_base_default_when_option_missing(self) -> None:
        """Upgrades from older options.json should still get the documented default."""
        run_sh = RUN_SH.read_text()

        self.assertIn('has("profiles_base")', run_sh)
        self.assertIn('else ".hermes/profiles"', run_sh)
        self.assertNotIn("PROFILES_BASE=$(opt profiles_base)", run_sh)

    def test_run_script_keeps_gateway_in_foreground_under_ha_s6(self) -> None:
        """The add-on wrapper, not upstream Hermes' s6 manager, supervises the gateway."""
        run_sh = RUN_SH.read_text()

        self.assertGreaterEqual(run_sh.count("export HERMES_GATEWAY_NO_SUPERVISE=1"), 2)
        # The gateway is started inside a subshell whose stdout pipes through tee
        # to a per-profile log file. The pipe lives inside `(...)` and the whole
        # group is backgrounded with `&`.
        self.assertIn('"$VENV_DIR/bin/hermes" gateway run 2>&1 | tee -a "$home/logs/gateway.log"', run_sh)

    def test_install_marker_submodule_scan_tolerates_empty_matches(self) -> None:
        """The marker calculation must not call basename with no operands."""
        run_sh = RUN_SH.read_text()

        self.assertIn('find "$SRC_DIR" -mindepth 2 -maxdepth 2 -name pyproject.toml', run_sh)
        self.assertNotIn("xargs -n1 basename", run_sh)

    def test_modern_dashboard_adds_import_meta_fallback_and_relative_vite_base(self) -> None:
        """Modern Hermes still needs add-on-controlled paths behind long HA Ingress tokens."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            write_modern_dashboard_fixture(src)
            status = src / "status"

            result = run_dashboard_patches(src, status)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.read_text(), "changed")
            api_text = (src / "web/src/lib/api.ts").read_text()
            self.assertIn("HA-ADDON-IMPORT-META-FALLBACK-PATCHED", api_text)
            self.assertIn(
                "export const HERMES_BASE_PATH = readBasePath() || HERMES_IMPORT_META_BASE_PATH;",
                api_text,
            )
            vite_text = (src / "web/vite.config.ts").read_text()
            self.assertIn("HA-ADDON-BASE-INJECTED", vite_text)
            self.assertIn('base: "./"', vite_text)

            second_status = src / "status2"
            second_result = run_dashboard_patches(src, second_status)

            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            self.assertEqual(second_status.read_text(), "")

    def test_modern_dashboard_replaces_wrong_vite_base_without_duplicate(self) -> None:
        """An absolute Vite base must be replaced, not duplicated."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            write_modern_dashboard_fixture(
                src,
                "export default defineConfig({\n"
                "  base: \"/\",\n"
                "  plugins: [],\n"
                "});\n",
            )
            status = src / "status"

            result = run_dashboard_patches(src, status)

            self.assertEqual(result.returncode, 0, result.stderr)
            vite_text = (src / "web/vite.config.ts").read_text()
            self.assertEqual(vite_text.count("base:"), 1)
            self.assertIn('base: "./"', vite_text)
            self.assertNotIn('base: "/"', vite_text)

    def test_modern_dashboard_repairs_obsolete_legacy_base_patch(self) -> None:
        """A failed previous start may have patched api.ts before dying later."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / "web/src/lib").mkdir(parents=True)
            (src / "web/src/plugins").mkdir(parents=True)
            (src / "web").mkdir(exist_ok=True)
            (src / "web/src/lib/api.ts").write_text(
                'export const HERMES_BASE_PATH = readBasePath();\n'
                'export const BASE = new URL(/* @vite-ignore */ "..", import.meta.url)'
                '.pathname.replace(/\\/$/, ""); /* HA-ADDON-BASE-PATCHED */\n'
                "declare global { interface Window { __HERMES_BASE_PATH__?: string; } }\n"
            )
            status = src / "status"

            result = run_dashboard_patches(src, status)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.read_text(), "changed")
            self.assertIn("Removed obsolete dashboard BASE source patch", result.stdout)
            api_text = (src / "web/src/lib/api.ts").read_text()
            self.assertIn("const BASE = HERMES_BASE_PATH;", api_text)
            self.assertIn("HA-ADDON-IMPORT-META-FALLBACK-PATCHED", api_text)

    def test_modern_dashboard_repairs_pre_vite_ignore_base_patch(self) -> None:
        """Older v1.0.4 starts used the same marker without @vite-ignore."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / "web/src/lib").mkdir(parents=True)
            (src / "web/src/lib/api.ts").write_text(
                'export const HERMES_BASE_PATH = readBasePath();\n'
                'export const BASE = new URL("..", import.meta.url)'
                '.pathname.replace(/\\/$/, ""); /* HA-ADDON-BASE-PATCHED */\n'
                "declare global { interface Window { __HERMES_BASE_PATH__?: string; } }\n"
            )
            status = src / "status"

            result = run_dashboard_patches(src, status)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.read_text(), "changed")
            self.assertIn("Removed obsolete dashboard BASE source patch", result.stdout)
            api_text = (src / "web/src/lib/api.ts").read_text()
            self.assertIn("const BASE = HERMES_BASE_PATH;", api_text)
            self.assertIn("HA-ADDON-IMPORT-META-FALLBACK-PATCHED", api_text)

    def test_legacy_dashboard_sources_are_patched_without_sed_delimiter_bug(self) -> None:
        """Legacy root-only dashboard sources still get the compatibility patches."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / "web/src/lib").mkdir(parents=True)
            (src / "web/src/plugins").mkdir(parents=True)
            (src / "web").mkdir(exist_ok=True)
            (src / "web/src/lib/api.ts").write_text('const BASE = "";\n')
            (src / "web/src/plugins/usePlugins.ts").write_text(
                'import { api } from "@/lib/api";\n'
                "const baseUrl = `/dashboard-plugins/${manifest.name}/${manifest.entry}`;\n"
            )
            (src / "web/src/main.tsx").write_text(
                'import { BrowserRouter } from "react-router-dom";\n'
                "<BrowserRouter>\n"
            )
            (src / "web/vite.config.ts").write_text(
                "export default defineConfig({\n"
                "  plugins: [],\n"
                "});\n"
            )
            status = src / "status"

            result = run_dashboard_patches(src, status)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.read_text(), "changed")
            self.assertIn("HA-ADDON-BASE-PATCHED", (src / "web/src/lib/api.ts").read_text())
            self.assertIn("`${BASE}/dashboard-plugins/", (src / "web/src/plugins/usePlugins.ts").read_text())
            self.assertIn('basename={BASE || "/"}', (src / "web/src/main.tsx").read_text())
            self.assertIn('base: "./"', (src / "web/vite.config.ts").read_text())

    def test_prefix_length_limit_raised_for_ha_ingress(self) -> None:
        """Upstream's normalise_prefix rejects > 64-char prefixes, which kills
        nested addon routes (HA Ingress token + /profile/<name>/dashboard).
        Patch must bump the limit."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            write_modern_dashboard_fixture(src)
            prefix_py = src / "hermes_cli/dashboard_auth/prefix.py"
            prefix_py.parent.mkdir(parents=True)
            prefix_py.write_text(
                "def normalise_prefix(raw):\n"
                '    p = "/" + (raw or "").strip("/")\n'
                '    if len(p) > 64:\n'
                '        return ""\n'
                "    return p\n"
            )
            status = src / "status"

            result = run_dashboard_patches(src, status)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(status.read_text(), "changed")
            patched = prefix_py.read_text()
            self.assertIn("HA-ADDON-PREFIX-LIMIT-PATCHED", patched)
            self.assertNotIn("if len(p) > 64:", patched)
            # New limit must accommodate HA Ingress token (~50) +
            # `/profile/<name>/dashboard` (up to ~50) comfortably.
            self.assertIn("> 256:", patched)

            # Idempotent on second run.
            second_status = src / "status2"
            second_result = run_dashboard_patches(src, second_status)
            self.assertEqual(second_result.returncode, 0, second_result.stderr)
            # Re-running with no other changes leaves status empty.
            self.assertEqual(second_status.read_text(), "")

    def test_run_script_rebuilds_any_dashboard_with_absolute_index_assets(self) -> None:
        """Absolute Vite index assets are stale for HA Ingress, modern or legacy."""
        run_sh = RUN_SH.read_text()

        self.assertIn("grep -Eq", run_sh)
        self.assertIn("(src|href)=\"/assets/", run_sh)
        self.assertNotIn("! grep -q 'HERMES_BASE_PATH'", run_sh)

    def test_landing_page_api_health_is_not_gateway_health(self) -> None:
        """Disabled API must not be polled or shown as a broken Gateway."""
        run_sh = RUN_SH.read_text()
        landing = LANDING_TEMPLATE.read_text()

        self.assertIn('SHOW_API="false"', run_sh)
        self.assertIn('SHOW_API="true"', run_sh)
        self.assertIn('s|%%SHOW_API%%|${SHOW_API}|g', run_sh)
        self.assertIn('id="statusApi"', landing)
        self.assertIn('var showApi = %%SHOW_API%%;', landing)
        # API health now lives behind a profile-aware prefix builder.
        show_api_start = landing.index("if (showApi) {")
        api_fetch = landing.index("/v1/health", show_api_start)
        api_else = landing.index("} else {", show_api_start)
        self.assertLess(show_api_start, api_fetch)
        self.assertLess(api_fetch, api_else)
        self.assertNotIn('statusGateway', landing)
        self.assertNotIn('Gateway</span>', landing)


if __name__ == "__main__":
    unittest.main()
