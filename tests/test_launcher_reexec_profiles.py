"""Process-boundary regressions for the add-on's Hermes launchers.

The PM/bootstrap and Hermes CLI are local synthetic boundaries. The launcher
scripts, interpreter replacement via execv, and process environments are real.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
import venv


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "hermes_agent"

HANDOFF = {
    "HERMES_ADDON_API_HOST": ("API_SERVER_HOST", "127.0.0.1"),
    "HERMES_ADDON_API_PORT": ("API_SERVER_PORT", "8765"),
    "HERMES_ADDON_API_ENABLED": ("API_SERVER_ENABLED", "true"),
    "HERMES_ADDON_API_KEY": ("API_SERVER_KEY", "local-test-key"),
    "HERMES_ADDON_PROFILE_HOME": ("HERMES_HOME", ""),
    "HERMES_ADDON_MULTIPLEX_PROFILES": ("GATEWAY_MULTIPLEX_PROFILES", "false"),
    "HERMES_ADDON_GATEWAY_NO_SUPERVISE": ("HERMES_GATEWAY_NO_SUPERVISE", "1"),
    "HERMES_ADDON_SUPERVISED_CHILD": ("HERMES_S6_SUPERVISED_CHILD", "1"),
}


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


class LauncherProcessTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.fake = self.root / "fake"
        self.home = self.root / "operator"
        self.home.mkdir()
        self.fake.mkdir()
        managed = self.root / "managed"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(managed)
        self.selected = managed / "bin" / "python"
        site = next((managed / "lib").glob("python*/site-packages"))
        (site / "probe.pth").write_text(str(self.fake) + "\n", encoding="utf-8")
        _write(self.fake / "hermes_bootstrap.py", '''
            import json
            import os
            from pathlib import Path
            import sys

            with Path(os.environ["PROBE_EVENTS"]).open("a", encoding="utf-8") as log:
                log.write(json.dumps({
                    "phase": os.environ.get("PROBE_REEXEC", "initial"),
                    "executable": sys.executable,
                    "handoffs": {k: v for k, v in os.environ.items() if k.startswith("HERMES_ADDON_")},
                    "lazy_flag": os.environ.get("HERMES_DISABLE_LAZY_INSTALLS"),
                }) + "\\n")
            if not os.environ.get("PROBE_REEXEC") and os.environ.get("HERMES_DISABLE_LAZY_INSTALLS") != "1":
                os.environ["PROBE_REEXEC"] = "selected"
                # Match the pinned upstream relaunch_command's isolated run_path form.
                code = ("import sys, runpy; "
                        f"sys.path.insert(0, {os.environ['PYTHONPATH']!r}); "
                        f"sys.argv = {sys.argv!r}; "
                        f"runpy.run_path({str(Path(sys.argv[0]).absolute())!r}, run_name='__main__')")
                os.execv(os.environ["PROBE_SELECTED_PYTHON"], [
                    os.environ["PROBE_SELECTED_PYTHON"], "-I", "-c", code,
                ])
        ''')
        _write(self.fake / "hermes_constants.py", '''
            import os
            from pathlib import Path
            def get_default_hermes_root():
                native = Path.home() / ".hermes"
                value = Path(os.environ.get("HERMES_HOME", str(native)))
                try:
                    value.resolve().relative_to(native.resolve())
                    return native
                except ValueError:
                    return value.parent.parent if value.parent.name == "profiles" else value
        ''')
        _write(self.fake / "hermes_cli" / "__init__.py", "")
        _write(self.fake / "hermes_cli" / "env_loader.py", '''
            import os
            def load_hermes_dotenv(*args, **kwargs):
                os.environ["API_SERVER_PORT"] = "9999"
        ''')
        _write(self.fake / "hermes_cli" / "main.py", '''
            import json
            import os
            from pathlib import Path
            import sys
            from hermes_constants import get_default_hermes_root

            # Model the pinned upstream main.py import-time sticky selection.
            home = Path(os.environ["HERMES_HOME"])
            active = get_default_hermes_root() / "active_profile"
            if home.parent.name != "profiles" and active.exists() and active.is_file():
                os.environ["HERMES_HOME"] = str(Path(os.environ["PROBE_STICKY_HOME"]))
            def main():
                from hermes_cli import env_loader
                import subprocess
                proc = Path("/proc/self/cmdline")
                command = (proc.read_bytes().replace(b"\\0", b" ").decode().strip()
                           if proc.exists() else subprocess.check_output(
                               ["ps", "-ww", "-p", str(os.getpid()), "-o", "command="],
                               text=True, timeout=3).strip())
                env_loader.load_hermes_dotenv()
                if sys.argv[1:2] == ["gateway"]:
                    from gateway import config
                    config.load_gateway_config()
                print("PROBE_RESULT=" + json.dumps({
                    "executable": sys.executable,
                    "argv": sys.argv,
                    "os_argv": sys.orig_argv,
                    "os_command": command,
                    "home": os.environ["HERMES_HOME"],
                    "targets": {k: os.environ.get(k) for k in (
                        "API_SERVER_HOST", "API_SERVER_PORT", "API_SERVER_ENABLED",
                        "API_SERVER_KEY", "GATEWAY_MULTIPLEX_PROFILES",
                        "HERMES_GATEWAY_NO_SUPERVISE", "HERMES_S6_SUPERVISED_CHILD")},
                    "handoffs": {k: v for k, v in os.environ.items() if k.startswith("HERMES_ADDON_")},
                    "lazy_flag": os.environ.get("HERMES_DISABLE_LAZY_INSTALLS"),
                    "sticky_probe_visible_after_import": active.exists() and active.is_file(),
                }))
        ''')
        _write(self.fake / "gateway" / "__init__.py", "")
        _write(self.fake / "gateway" / "config.py", '''
            class Platform:
                API_SERVER = "api"
            class PlatformConfig:
                enabled = False
                extra = {}
            class GatewayConfig:
                platforms = {}
            def load_gateway_config():
                return GatewayConfig()
        ''')

    def run_launcher(self, script, home, *, operator_flag=None, dashboard_command=False):
        home.mkdir(parents=True, exist_ok=True)
        sticky_home = self.root / "sticky-other"
        sticky_home.mkdir(exist_ok=True)
        # Both native and assigned roots compete. Upstream may check either.
        for root in {self.home / ".hermes", home, home.parent.parent if home.parent.name == "profiles" else home}:
            root.mkdir(parents=True, exist_ok=True)
            (root / "active_profile").write_text("other\n", encoding="utf-8")
        events = self.root / "events.jsonl"
        events.unlink(missing_ok=True)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home), "HERMES_HOME": str(home),
            "TMPDIR": str(self.root), "XDG_CACHE_HOME": str(self.root),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(self.fake),
            "PROBE_EVENTS": str(events), "PROBE_SELECTED_PYTHON": str(self.selected),
            "PROBE_STICKY_HOME": str(sticky_home),
        }
        if operator_flag is not None:
            env["HERMES_DISABLE_LAZY_INSTALLS"] = operator_flag
        if script.name in ("gateway-launcher.py", "hermes"):
            env.update({key: str(home) if key == "HERMES_ADDON_PROFILE_HOME" else value
                        for key, (_, value) in HANDOFF.items()})
        args = [str(Path(sys.executable)), str(script)]
        args += ["gateway", "run"] if script.name in ("gateway-launcher.py", "hermes") else (["dashboard"] if dashboard_command else []) + ["--host", "127.0.0.1", "--port", "3100", "--skip-build", "--no-open"]
        completed = subprocess.run(args, env=env, cwd=self.root, capture_output=True, text=True, timeout=20, check=False)
        result_lines = [line.removeprefix("PROBE_RESULT=") for line in completed.stdout.splitlines() if line.startswith("PROBE_RESULT=")]
        result = json.loads(result_lines[-1]) if result_lines else None
        phases = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()] if events.exists() else []
        return completed, result, phases

    def test_run_dispatches_dashboard_via_shipped_python_wrapper(self):
        run = (ADDON / "run.sh").read_text(encoding="utf-8")
        body = run.split("start_dashboard_for_profile() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('exec "$VENV_DIR/bin/python" /usr/local/lib/hermes-dashboard-launcher.py dashboard', body)
        self.assertNotIn('dashboard_profile_args', body)
        self.assertIn('COPY dashboard-launcher.py /usr/local/lib/hermes-dashboard-launcher.py',
                      (ADDON / "Dockerfile").read_text(encoding="utf-8"))
        self.assertIn('COPY gateway-launcher.py /usr/local/lib/hermes-cli/hermes',
                      (ADDON / "Dockerfile").read_text(encoding="utf-8"))
        self.assertLess(run.index('"/usr/local/lib/hermes-cli/hermes"'),
                        run.index('"/usr/local/lib/hermes-gateway-launcher.py"'))

    def test_old_source_without_bootstrap_still_launches(self):
        _write(self.fake / "hermes_bootstrap.py", 'raise ModuleNotFoundError("No module named hermes_bootstrap", name="hermes_bootstrap")\n')
        for script in (ADDON / "gateway-launcher.py", ADDON / "dashboard-launcher.py"):
            with self.subTest(script=script.name):
                completed, result, phases = self.run_launcher(script, self.root / script.stem)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(result["home"], str(self.root / script.stem))
                self.assertEqual(phases, [])

    def test_gateway_reexec_preserves_all_handoffs_and_selects_managed_interpreter(self):
        home = self.root / "gateway-home"
        completed, result, phases = self.run_launcher(ADDON / "gateway-launcher.py", home)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual([p["phase"] for p in phases], ["initial", "selected", "selected"])
        self.assertEqual(Path(result["executable"]), self.selected)
        self.assertEqual(result["home"], str(home))
        self.assertEqual(result["targets"], {target: value if target != "HERMES_HOME" else str(home)
                                              for _, (target, value) in HANDOFF.items() if target != "HERMES_HOME"})
        self.assertEqual(result["handoffs"], {})
        self.assertEqual(phases[0]["handoffs"], phases[1]["handoffs"])
        self.assertEqual(phases[0]["handoffs"], phases[2]["handoffs"])
        self.assertEqual(set(phases[0]["handoffs"]), set(HANDOFF))
        self.assertTrue(result["sticky_probe_visible_after_import"])

    def test_gateway_preserves_operator_lazy_install_flag(self):
        completed, result, phases = self.run_launcher(ADDON / "gateway-launcher.py", self.root / "gateway-home", operator_flag="1")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(phases), 1)
        self.assertEqual(result["lazy_flag"], "1")

    def test_managed_gateway_reentry_keeps_a_real_discoverable_command(self):
        # The Docker image places these same launcher bytes at a CLI basename.
        script = self.root / "hermes-cli" / "hermes"
        _write(script, (ADDON / "gateway-launcher.py").read_text())
        completed, result, phases = self.run_launcher(script, self.root / "gateway-home")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("-c", result["os_argv"])
        self.assertIn(str(script), result["os_argv"])
        self.assertEqual(result["os_argv"][-2:], ["gateway", "run"])
        self.assertEqual(Path(result["executable"]), self.selected)
        self.assertTrue(all(phase["handoffs"] == phases[0]["handoffs"] for phase in phases))

    def test_dashboard_owns_an_isolated_listener_after_reexec(self):
        home = self.home / ".hermes" / "profiles" / "worker"
        completed, result, phases = self.run_launcher(
            ADDON / "dashboard-launcher.py", home, dashboard_command=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["argv"].count("--isolated"), 1)
        self.assertEqual(result["home"], str(home))

    def test_dashboard_command_is_idempotent_across_reexec(self):
        completed, result, phases = self.run_launcher(
            ADDON / "dashboard-launcher.py", self.root / "direct-home", dashboard_command=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual([p["phase"] for p in phases], ["initial", "selected"])
        self.assertEqual(result["argv"].count("dashboard"), 1)

    def test_dashboard_reexec_preserves_exact_assigned_home_in_every_layout(self):
        homes = {
            "primary": self.home / ".hermes",
            "named": self.home / ".hermes" / "profiles" / "worker",
            "custom": self.root / "custom",
            "flat": self.root / "flat-worker",
            "profile-shaped-primary": self.root / "other-root" / "profiles" / "finance",
            "nested": self.root / "other-root" / "profiles" / "team" / "worker",
        }
        for label, home in homes.items():
            with self.subTest(layout=label):
                completed, result, phases = self.run_launcher(ADDON / "dashboard-launcher.py", home)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual([p["phase"] for p in phases], ["initial", "selected"])
                self.assertEqual(Path(result["executable"]), self.selected)
                self.assertEqual(result["home"], str(home))
                self.assertEqual(result["argv"], [str((ADDON / "dashboard-launcher.py").resolve()), "dashboard", "--host", "127.0.0.1", "--port", "3100", "--skip-build", "--no-open", "--isolated"])
                self.assertEqual(result["handoffs"], {})
                self.assertTrue(result["sticky_probe_visible_after_import"])
