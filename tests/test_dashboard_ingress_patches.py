"""Regression tests for Home Assistant Ingress dashboard routing patches."""

from pathlib import Path
import errno
import os
import runpy
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = ROOT / "hermes_agent" / "dashboard-patches.py"
RUN_SH = ROOT / "hermes_agent" / "run.sh"
DOCKERFILE = ROOT / "hermes_agent" / "Dockerfile"
GATEWAY_CHILD = ROOT / "hermes_agent" / "gateway-child.sh"
GATEWAY_SUPERVISOR = ROOT / "hermes_agent" / "gateway-supervisor.py"
GATEWAY_LOGGER = ROOT / "hermes_agent" / "gateway-logger.py"
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
        self.assertIn('exec "$GATEWAY_CHILD"', run_sh)
        self.assertIn('local pid=$!', run_sh)
        self.assertIn('wait "$pid"', run_sh)
        self.assertIn("GATEWAY_LOGGER_PIDS=()", run_sh)
        self.assertIn("GATEWAY_LOG_PIPES=()", run_sh)
        self.assertIn('mkfifo -m 600 "$log_pipe"', run_sh)
        self.assertIn('/usr/bin/env -i PATH="/usr/bin:/bin" \\', run_sh)
        self.assertIn('"$VENV_DIR/bin/python" "$GATEWAY_LOGGER"', run_sh)
        self.assertNotIn('/usr/bin/tee', run_sh)
        self.assertIn('kill -TERM -- "-$pid"', run_sh)
        self.assertIn('cleanup_gateway_logger "$i"', run_sh)
        self.assertIn('start_gateway_signal_safe "$i"', run_sh)
        self.assertIn('logger_pid="${GATEWAY_LOGGER_PIDS[$i]:-}"', run_sh)
        self.assertIn('! kill -0 "$logger_pid"', run_sh)
        self.assertNotIn("pgrep -f", run_sh)
        self.assertNotIn('readlink "/proc/$candidate/cwd"', run_sh)
        self.assertNotIn("tee_pid", run_sh)
        dockerfile = DOCKERFILE.read_text()
        self.assertIn(
            "COPY gateway-child.sh /usr/local/lib/hermes-gateway-child.sh",
            dockerfile,
        )
        self.assertIn(
            "COPY gateway-supervisor.py /usr/local/lib/hermes-gateway-supervisor.py",
            dockerfile,
        )
        self.assertIn(
            "COPY gateway-logger.py /usr/local/lib/hermes-gateway-logger.py",
            dockerfile,
        )
        self.assertIn("/usr/local/lib/hermes-gateway-child.sh", dockerfile)
        self.assertIn("/usr/local/lib/hermes-gateway-supervisor.py", dockerfile)
        self.assertIn("/usr/local/lib/hermes-gateway-logger.py", dockerfile)

    def test_gateway_spawn_defers_shutdown_until_ownership_is_published(self) -> None:
        run_text = RUN_SH.read_text()
        helper_start = run_text.index("SHUTDOWN_PENDING=false")
        helper_end = run_text.index("\nshutdown() {", helper_start)
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "spawn-guard.sh"
            helper.write_text(run_text[helper_start:helper_end])
            for signal_point in ("logger", "gateway"):
                result = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "source \"$1\"; SIGNAL_POINT=\"$2\"; "
                        "start_gateway_for_profile() { "
                        "GATEWAY_LOGGER_PIDS[$1]=4321; "
                        "if [ \"$2\" = logger ]; then kill -TERM $$; fi; "
                        "/bin/sleep 60 >/dev/null 2>&1 & spawned_pid=$!; /bin/sleep 0.05; "
                        "if [ \"$2\" = gateway ]; then kill -TERM $$; fi; "
                        "GATEWAY_PIDS[$1]=$spawned_pid; }; "
                        "shutdown() { "
                        "test \"${GATEWAY_LOGGER_PIDS[0]:-}\" = 4321; "
                        "test \"${GATEWAY_PIDS[0]:-}\" = \"$spawned_pid\"; "
                        "kill \"$spawned_pid\"; wait \"$spawned_pid\" 2>/dev/null || true; "
                        "printf 'OWNERSHIP_PUBLISHED_%s\\n' \"$SIGNAL_POINT\"; exit 42; }; "
                        "start_gateway_signal_safe 0 \"$2\"",
                        "spawn-guard-test",
                        str(helper),
                        signal_point,
                    ],
                    timeout=5,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 42, result.stderr)
                self.assertIn(f"OWNERSHIP_PUBLISHED_{signal_point}", result.stdout)

    def test_gateway_supervisor_reaps_detached_child_before_exit(self) -> None:
        self.assertTrue(GATEWAY_SUPERVISOR.is_file())
        self.assertEqual(GATEWAY_SUPERVISOR.stat().st_mode & 0o777, 0o644)
        dockerfile = DOCKERFILE.read_text()
        self.assertIn(
            "COPY gateway-supervisor.py /usr/local/lib/hermes-gateway-supervisor.py",
            dockerfile,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launcher.py"
            detached_pid_file = root / "detached.pid"
            supervisor_ready = root / "supervisor.ready"
            launcher.write_text(
                "import os,subprocess,sys,time\n"
                "from pathlib import Path\n"
                "child = subprocess.Popen(\n"
                "    [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
                "    start_new_session=True,\n"
                ")\n"
                "Path(os.environ['DETACHED_PID_FILE']).write_text(str(child.pid))\n"
                "time.sleep(0.2)\n"
                "raise SystemExit(23)\n"
            )
            result = subprocess.run(
                [
                    str(GATEWAY_CHILD),
                    sys.executable,
                    str(GATEWAY_SUPERVISOR),
                    str(launcher),
                    str(supervisor_ready),
                    str(os.getpid()),
                ],
                env=os.environ | {"DETACHED_PID_FILE": str(detached_pid_file)},
                timeout=8,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("gateway exited with status 23", result.stderr)
            self.assertIn("owned descendants empty", result.stderr)
            self.assertTrue(detached_pid_file.exists())
            detached_pid = int(detached_pid_file.read_text())
            for _ in range(100):
                status = subprocess.run(
                    ["/bin/ps", "-o", "state=", "-p", str(detached_pid)],
                    check=False,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if not status or status.startswith("Z"):
                    break
                time.sleep(0.02)
            else:
                try:
                    os.kill(detached_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.fail(f"detached gateway child {detached_pid} survived supervisor exit")

    def test_gateway_supervisor_reexecs_without_private_handoffs(self) -> None:
        secret = "supervisor-must-not-retain-this-handoff"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launcher.py"
            observed = root / "observed"
            ready = root / "ready"
            supervisor_ready = root / "supervisor.ready"
            launcher.write_text(
                "import os,signal,sys,time\n"
                "from pathlib import Path\n"
                "if Path(os.environ['SUPERVISOR_READY']).read_text().strip() != str(os.getppid()):\n"
                "    raise SystemExit(93)\n"
                "Path(os.environ['OBSERVED']).write_text(\n"
                "    os.environ['HERMES_ADDON_API_KEY']\n"
                ")\n"
                "Path(os.environ['READY']).write_text(str(os.getpid()))\n"
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                "while True: time.sleep(0.05)\n"
            )
            process = subprocess.Popen(
                [
                    str(GATEWAY_CHILD),
                    sys.executable,
                    str(GATEWAY_SUPERVISOR),
                    str(launcher),
                    str(supervisor_ready),
                    str(os.getpid()),
                ],
                env=os.environ
                | {
                    "HERMES_ADDON_API_KEY": secret,
                    "OBSERVED": str(observed),
                    "READY": str(ready),
                    "SUPERVISOR_READY": str(supervisor_ready),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(100):
                    if ready.exists() and observed.exists():
                        break
                    time.sleep(0.02)
                self.assertEqual(observed.read_text(), secret)
                proc_environ = Path(f"/proc/{process.pid}/environ")
                if proc_environ.exists():
                    supervisor_environment = proc_environ.read_bytes()
                else:
                    supervisor_environment = subprocess.run(
                        [
                            "/bin/ps",
                            "eww",
                            "-p",
                            str(process.pid),
                            "-o",
                            "command=",
                        ],
                        check=True,
                        capture_output=True,
                    ).stdout
                self.assertNotIn(secret.encode(), supervisor_environment)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)

    @unittest.skipUnless(sys.platform == "linux", "requires Linux prctl")
    def test_gateway_supervisor_rejects_changed_parent_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launcher.py"
            marker = root / "gateway-started"
            ready = root / "supervisor.ready"
            launcher.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('started')\n"
            )

            result = subprocess.run(
                [
                    str(GATEWAY_CHILD),
                    sys.executable,
                    str(GATEWAY_SUPERVISOR),
                    str(launcher),
                    str(ready),
                    "0",
                ],
                timeout=5,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 70, result.stderr)
            self.assertIn("parent changed", result.stderr)
            self.assertFalse(ready.exists())
            self.assertFalse(marker.exists())

            logger_result = subprocess.run(
                [
                    sys.executable,
                    str(GATEWAY_LOGGER),
                    str(root / "gateway.log"),
                    str(root / "not-opened.fifo"),
                    "0",
                ],
                timeout=5,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(logger_result.returncode, 73, logger_result.stderr)
            self.assertIn("parent changed", logger_result.stderr)

    def test_gateway_logger_fails_fast_on_enospc_with_live_writer(self) -> None:
        self.assertTrue(GATEWAY_LOGGER.is_file())
        self.assertEqual(GATEWAY_LOGGER.stat().st_mode & 0o777, 0o644)
        namespace = runpy.run_path(str(GATEWAY_LOGGER), run_name="gateway_logger_test")
        copy_stream = namespace["_copy_stream"]
        function_globals = copy_stream.__globals__
        real_write_all = function_globals["_write_all"]

        read_fd, write_fd = os.pipe()
        with tempfile.TemporaryDirectory() as tmp:
            log_fd = os.open(Path(tmp) / "gateway.log", os.O_WRONLY | os.O_CREAT, 0o600)

            def fail_log_write(fd: int, data: bytes) -> None:
                if fd == log_fd:
                    raise OSError(errno.ENOSPC, "No space left on device")
                real_write_all(fd, data)

            function_globals["_write_all"] = fail_log_write
            try:
                os.write(write_fd, b"first-record\n")
                with self.assertRaises(OSError) as raised:
                    copy_stream(read_fd, log_fd, stdout_fd=None)
                self.assertEqual(raised.exception.errno, errno.ENOSPC)
                self.assertEqual(os.write(write_fd, b"writer-still-open\n"), 18)
            finally:
                function_globals["_write_all"] = real_write_all
                os.close(log_fd)
                os.close(read_fd)
                os.close(write_fd)

    def test_logger_failure_stops_gateway_tree_and_restarts_profile(self) -> None:
        run_text = RUN_SH.read_text()
        helper_start = run_text.index("gateway_group_alive() {")
        helper_end = run_text.index("\nshutdown() {", helper_start)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "supervisor.sh"
            helper.write_text(run_text[helper_start:helper_end])
            launcher = root / "gateway.py"
            launcher.write_text(
                "import os,signal,subprocess,sys,time\n"
                "from pathlib import Path\n"
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(42))\n"
                "fifo = open(os.environ['FIFO_PATH'], 'wb', buffering=0)\n"
                "child = subprocess.Popen(\n"
                "    [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
                "    start_new_session=True,\n"
                ")\n"
                "Path(os.environ['DETACHED_PID_FILE']).write_text(str(child.pid))\n"
                "time.sleep(1.0)\n"
                "Path(os.environ['READY_FILE']).write_text(str(os.getpid()))\n"
                "while True: time.sleep(0.1)\n"
            )
            fifo = root / "gateway.fifo"
            os.mkfifo(fifo, 0o600)
            bad_log = root / "missing" / "gateway.log"
            ready = root / "gateway.ready"
            supervisor_ready = root / "supervisor.ready"
            detached_pid_file = root / "detached.pid"
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    "source \"$1\"; "
                    "sleep() { /bin/sleep 0.001; }; "
                    "start_gateway_signal_safe() { printf 'PROFILE_RESTARTED_%s\\n' \"$1\"; }; "
                    "PROFILE_NAMES[0]=test; "
                    "/usr/bin/env -i PATH=/usr/bin:/bin \"$3\" \"$6\" \"$7\" \"$5\" \"$$\" & logger_pid=$!; "
                    "GATEWAY_LOGGER_PIDS[0]=$logger_pid; GATEWAY_LOG_PIPES[0]=\"$5\"; "
                    "FIFO_PATH=\"$5\" READY_FILE=\"$8\" DETACHED_PID_FILE=\"${10}\" \"$2\" \"$3\" \"$9\" \"$4\" \"${11}\" \"$$\" >/dev/null 2>&1 & gateway_pid=$!; "
                    "GATEWAY_PIDS[0]=$gateway_pid; "
                    "for n in $(seq 1 100); do [ -e \"$8\" ] && [ -e \"${10}\" ] && [ -e \"${11}\" ] && break; /bin/sleep 0.01; done; "
                    "test -e \"$8\"; test -e \"${10}\"; test -e \"${11}\"; "
                    "for n in $(seq 1 100); do ! kill -0 \"$logger_pid\" 2>/dev/null && break; /bin/sleep 0.01; done; "
                    "/bin/sleep 0.15; "
                    "supervise_gateway_profile 0; "
                    "if kill -0 \"$gateway_pid\" 2>/dev/null; then exit 91; fi; "
                    "if kill -0 -- \"-$gateway_pid\" 2>/dev/null; then exit 92; fi; "
                    "if [ -e \"$5\" ]; then exit 93; fi",
                    "logger-supervisor-test",
                    str(helper),
                    str(GATEWAY_CHILD),
                    sys.executable,
                    str(launcher),
                    str(fifo),
                    str(GATEWAY_LOGGER),
                    str(bad_log),
                    str(ready),
                    str(GATEWAY_SUPERVISOR),
                    str(detached_pid_file),
                    str(supervisor_ready),
                ],
                timeout=10,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Gateway logger exited (code: 74)", result.stdout)
            self.assertEqual(result.stdout.count("PROFILE_RESTARTED_0"), 1)
            detached_pid = int(detached_pid_file.read_text())
            state = subprocess.run(
                ["/bin/ps", "-o", "state=", "-p", str(detached_pid)],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)

    def test_unsafe_gateway_supervisor_exit_is_container_fatal(self) -> None:
        run_text = RUN_SH.read_text()
        helper_start = run_text.index("gateway_group_alive() {")
        helper_end = run_text.index("\nshutdown() {", helper_start)
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "supervisor.sh"
            helper.write_text(run_text[helper_start:helper_end])
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    "source \"$1\"; "
                    "cleanup_gateway_logger() { :; }; "
                    "start_gateway_signal_safe() { echo UNEXPECTED_RESTART; }; "
                    "PROFILE_NAMES[0]=test; "
                    "/bin/bash -c 'exit 70' & pid=$!; "
                    "/bin/sleep 0.05; GATEWAY_PIDS[0]=$pid; "
                    "supervise_gateway_profile 0",
                    "unsafe-supervisor-test",
                    str(helper),
                ],
                timeout=5,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 70, result.stderr)
            self.assertNotIn("UNEXPECTED_RESTART", result.stdout)
            self.assertIn("unsafe gateway supervisor exit: 70", result.stderr)

    def test_shutdown_cleans_logger_before_propagating_unsafe_status(self) -> None:
        run_text = RUN_SH.read_text()
        shutdown_start = run_text.index("shutdown() {")
        shutdown_end = run_text.index("\n# Register signal handler", shutdown_start)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "shutdown.sh"
            marker = root / "logger-cleaned"
            helper.write_text(run_text[shutdown_start:shutdown_end])
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    "source \"$1\"; MARKER=\"$2\"; "
                    "nginx() { :; }; desktop_backend_stop() { :; }; "
                    "stop_gateway_tree() { kill \"$1\" 2>/dev/null || true; return 70; }; "
                    "cleanup_gateway_logger() { : > \"$MARKER\"; }; "
                    "PROFILE_DIRS[0]=test; PROFILE_NAMES[0]=test; "
                    "/bin/sleep 60 & GATEWAY_PIDS[0]=$!; shutdown",
                    "unsafe-shutdown-test",
                    str(helper),
                    str(marker),
                ],
                timeout=5,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 70, result.stderr)
            self.assertTrue(marker.exists())
            self.assertIn("Shutdown complete", result.stdout)

    def test_gateway_child_preserves_pid_term_log_and_crash_status(self) -> None:
        self.assertTrue(GATEWAY_CHILD.is_file())
        child_text = GATEWAY_CHILD.read_text()
        supervisor_text = GATEWAY_SUPERVISOR.read_text()
        run_text = RUN_SH.read_text()
        self.assertIn('exec "$python_path" "$supervisor"', child_text)
        self.assertIn("_PR_SET_CHILD_SUBREAPER = 36", supervisor_text)
        self.assertIn("os.setsid()", supervisor_text)
        main_start = supervisor_text.index("def main() -> int:")
        initial_block = supervisor_text.index("signal.SIG_BLOCK", main_start)
        initial_contract = supervisor_text.index(
            "_set_linux_process_contract(expected_parent_pid)",
            initial_block,
        )
        clean_reexec = supervisor_text.index(
            "_load_gateway_environment()",
            initial_contract,
        )
        resumed_contract = supervisor_text.index(
            "_set_linux_process_contract(expected_parent_pid)",
            clean_reexec + 1,
        )
        unblock = supervisor_text.index("signal.SIG_UNBLOCK", resumed_contract)
        publish_ready = supervisor_text.index("_publish_ready(ready_path)", unblock)
        launch = supervisor_text.index(
            "return supervise(python_path, launcher, gateway_environment)",
            publish_ready,
        )
        self.assertLess(initial_block, initial_contract)
        self.assertLess(initial_contract, clean_reexec)
        self.assertLess(resumed_contract, unblock)
        self.assertLess(unblock, publish_ready)
        self.assertLess(publish_ready, launch)
        run_start = run_text.index("start_gateway_for_profile() {")
        readiness_failure = run_text.index(
            'if [ "$ready" != "true" ]; then',
            run_start,
        )
        startup_term = run_text.index('kill -TERM "$pid"', readiness_failure)
        startup_kill = run_text.index('kill -KILL "$pid"', startup_term)
        startup_wait = run_text.index('wait "$pid"', startup_kill)
        startup_cleanup = run_text.index(
            'cleanup_gateway_logger "$i"',
            startup_wait,
        )
        self.assertLess(startup_term, startup_kill)
        self.assertLess(startup_kill, startup_wait)
        self.assertLess(startup_wait, startup_cleanup)
        self.assertNotIn("tee", child_text)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_launcher = root / "launcher.py"
            fake_launcher.write_text(
                "import os, signal, subprocess, sys, time\n"
                "from pathlib import Path\n"
                "Path(os.environ['ARGS_FILE']).write_text(' '.join(sys.argv[1:]))\n"
                "if os.environ.get('FAKE_MODE') == 'crash':\n"
                "    print('crash-output', flush=True)\n"
                "    raise SystemExit(23)\n"
                "Path(os.environ['PID_FILE']).write_text(str(os.getpid()))\n"
                "Path(os.environ['PGID_FILE']).write_text(str(os.getpgrp()))\n"
                "if os.environ.get('SPAWN_DESCENDANT') == '1':\n"
                "    child = subprocess.Popen(\n"
                "        [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
                "        start_new_session=True,\n"
                "    )\n"
                "    Path(os.environ['CHILD_PID_FILE']).write_text(str(child.pid))\n"
                "if os.environ.get('IGNORE_TERM') == '1':\n"
                "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "else:\n"
                "    def stop(*_args):\n"
                "        print('term-output', flush=True)\n"
                "        raise SystemExit(42)\n"
                "    signal.signal(signal.SIGTERM, stop)\n"
                "print('running-output', flush=True)\n"
                "while True:\n"
                "    time.sleep(0.05)\n"
            )
            pid_file = root / "pid"
            pgid_file = root / "pgid"
            args_file = root / "args"
            child_pid_file = root / "child-pid"
            env = os.environ.copy()
            env.update(
                {
                    "PID_FILE": str(pid_file),
                    "PGID_FILE": str(pgid_file),
                    "ARGS_FILE": str(args_file),
                    "CHILD_PID_FILE": str(child_pid_file),
                }
            )

            def gateway_command(ready_path: Path) -> list[str]:
                return [
                    str(GATEWAY_CHILD),
                    sys.executable,
                    str(GATEWAY_SUPERVISOR),
                    str(fake_launcher),
                    str(ready_path),
                    str(os.getpid()),
                ]

            crashed = subprocess.run(
                gateway_command(root / "crash-supervisor.ready"),
                env=env | {"FAKE_MODE": "crash"},
                timeout=5,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 0)
            self.assertIn("crash-output", crashed.stdout)
            self.assertIn("gateway exited with status 23", crashed.stderr)
            self.assertIn("owned descendants empty", crashed.stderr)

            process = subprocess.Popen(
                gateway_command(root / "running-supervisor.ready"),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for _ in range(100):
                if pid_file.exists() and pgid_file.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(pid_file.exists())
            self.assertNotEqual(int(pid_file.read_text().strip()), process.pid)
            self.assertEqual(int(pgid_file.read_text().strip()), process.pid)
            os.killpg(process.pid, signal.SIGTERM)
            output, _ = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertIn("running-output", output)
            self.assertIn("term-output", output)
            self.assertIn("gateway exited with status 42", output)
            self.assertIn("owned descendants empty", output)
            self.assertEqual(args_file.read_text().strip(), "gateway run")

            pid_file.unlink()
            pgid_file.unlink()
            stubborn = subprocess.Popen(
                gateway_command(root / "stubborn-supervisor.ready"),
                env=env | {"IGNORE_TERM": "1"},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            for _ in range(100):
                if pid_file.exists() and pgid_file.exists():
                    break
                time.sleep(0.02)
            os.killpg(stubborn.pid, signal.SIGTERM)
            time.sleep(0.1)
            self.assertIsNone(stubborn.poll())
            os.killpg(stubborn.pid, signal.SIGKILL)
            self.assertEqual(stubborn.wait(timeout=5), -signal.SIGKILL)
            with self.assertRaises(ProcessLookupError):
                os.killpg(stubborn.pid, 0)

            def start_logger(
                fifo: Path,
                log: Path,
                *,
                logger_env=None,
                stdout=subprocess.PIPE,
            ):
                return subprocess.Popen(
                    [
                        "/usr/bin/env",
                        "-i",
                        "PATH=/usr/bin:/bin",
                        sys.executable,
                        str(GATEWAY_LOGGER),
                        str(log),
                        str(fifo),
                        str(os.getpid()),
                    ],
                    env=logger_env,
                    stdout=stdout,
                    stderr=subprocess.STDOUT,
                )

            normal_fifo = root / "normal.fifo"
            os.mkfifo(normal_fifo, 0o600)
            normal_log = root / "normal.log"
            logger = start_logger(normal_fifo, normal_log)
            payload = b"first-line\nsecond-line\n"
            writer = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[1]).open('wb', buffering=0).write(sys.argv[2].encode())",
                    str(normal_fifo),
                    payload.decode(),
                ],
                timeout=5,
                check=False,
            )
            self.assertEqual(writer.returncode, 0)
            logger_output, _ = logger.communicate(timeout=5)
            self.assertEqual(logger.returncode, 0)
            self.assertEqual(logger_output, payload)
            self.assertEqual(normal_log.read_bytes(), payload)

            held_fifo = root / "held.fifo"
            os.mkfifo(held_fifo, 0o600)
            held_log = root / "held.log"
            secret = "logger-must-not-inherit-this-secret"
            held_logger = start_logger(
                held_fifo,
                held_log,
                logger_env=os.environ | {"HERMES_ADDON_API_KEY": secret},
                stdout=subprocess.DEVNULL,
            )

            def process_environment(pid: int) -> bytes:
                proc_environ = Path(f"/proc/{pid}/environ")
                if proc_environ.exists():
                    return proc_environ.read_bytes()
                return subprocess.run(
                    ["/bin/ps", "eww", "-p", str(pid), "-o", "command="],
                    check=True,
                    capture_output=True,
                ).stdout

            logger_environment = b""
            for _ in range(100):
                logger_environment = process_environment(held_logger.pid)
                if secret.encode() not in logger_environment:
                    break
                time.sleep(0.02)
            self.assertIsNone(held_logger.poll())
            self.assertNotIn(secret.encode(), logger_environment)
            self.assertFalse(held_log.exists())

            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import sys,time; f=open(sys.argv[1],'wb',buffering=0); "
                    "f.write(b'held-open\\n'); time.sleep(60)",
                    str(held_fifo),
                ]
            )
            for _ in range(100):
                if held_log.exists() and b"held-open" in held_log.read_bytes():
                    break
                time.sleep(0.02)
            self.assertIn(b"held-open", held_log.read_bytes())
            logger_environment = process_environment(held_logger.pid)
            self.assertNotIn(secret.encode(), logger_environment)

            self.assertIsNone(holder.poll())
            holder.terminate()
            holder.wait(timeout=5)
            held_logger.wait(timeout=5)
            self.assertEqual(held_logger.returncode, 0)
            held_fifo.unlink()
            self.assertFalse(held_fifo.exists())

            failed_fifo = root / "failed.fifo"
            os.mkfifo(failed_fifo, 0o600)
            failed_logger = start_logger(
                failed_fifo,
                root / "missing" / "gateway.log",
                stdout=subprocess.DEVNULL,
            )
            failed_writer = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; open(sys.argv[1],'wb',buffering=0).write(b'x')",
                    str(failed_fifo),
                ],
                timeout=5,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            failed_logger.wait(timeout=5)
            self.assertNotEqual(failed_logger.returncode, 0)
            self.assertIsNotNone(failed_writer.returncode)

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
