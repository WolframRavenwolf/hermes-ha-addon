"""Behavior tests for the optional Hermes Desktop remote backend."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "hermes_agent"
CONFIG = ADDON / "config.yaml"
RUN_SH = ADDON / "run.sh"
DESKTOP_LIB = ADDON / "desktop-backend.sh"
DESKTOP_LAUNCHER = ADDON / "desktop-backend-launcher.py"
DOCKERFILE = ADDON / "Dockerfile"
TRANSLATIONS = ADDON / "translations" / "en.yaml"
README = ROOT / "README.md"
CHANGELOG = ADDON / "CHANGELOG.md"
BASH = "/bin/bash" if sys.platform == "darwin" else "bash"


def run_bash(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [BASH, "-c", script],
        check=False,
        text=True,
        capture_output=True,
        env=merged,
    )


class DesktopBackendConfigurationTests(unittest.TestCase):
    def test_feature_is_opt_in_and_host_port_is_unmapped_by_default(self) -> None:
        config = CONFIG.read_text()

        self.assertIn("enable_desktop_backend: false", config)
        self.assertIn("enable_desktop_backend: \"bool\"", config)
        self.assertIn("9119/tcp: null", config)
        self.assertIn("9119/tcp:", config)
        self.assertNotIn("desktop_backend_port:", config)

    def test_container_ships_the_runtime_library_and_launcher(self) -> None:
        dockerfile = DOCKERFILE.read_text()

        self.assertIn(
            "COPY desktop-backend.sh /usr/local/lib/hermes-desktop-backend.sh",
            dockerfile,
        )
        self.assertIn(
            "COPY desktop-backend-launcher.py /usr/local/bin/hermes-desktop-backend",
            dockerfile,
        )

    def test_user_facing_text_states_opt_in_risk_and_trusted_network_boundary(self) -> None:
        translations = TRANSLATIONS.read_text()
        readme = README.read_text()
        changelog = CHANGELOG.read_text()

        for text in (translations, readme):
            self.assertIn("enable_desktop_backend", text)
            self.assertIn("9119", text)
            self.assertRegex(text, r"(?i)LAN|VPN|Tailscale")
            self.assertRegex(text, r"(?i)risk|powerful|full agent control")
        self.assertIn("Hermes Desktop", changelog)
        self.assertIn("opt-in", changelog)


class DesktopBackendLauncherTests(unittest.TestCase):
    def test_machine_root_pin_and_exact_password_hash_win_after_env_loading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "hermes_cli"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "main.py").write_text(
                textwrap.dedent(
                    """
                    import json
                    import os
                    import sys
                    from pathlib import Path

                    argv_at_import = list(sys.argv)
                    if sys.argv[1:3] == ["-p", "default"]:
                        sys.argv = [sys.argv[0], *sys.argv[3:]]

                    # Simulate Hermes loading colliding machine-root env layers.
                    os.environ["HERMES_DASHBOARD_BASIC_AUTH_USERNAME"] = "wrong-user"
                    os.environ["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"] = "wrong-password"
                    os.environ["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH"] = "wrong-hash"
                    os.environ["HERMES_DASHBOARD_BASIC_AUTH_SECRET"] = "wrong-secret"

                    def main():
                        payload = {
                            "argv_at_import": argv_at_import,
                            "argv": sys.argv,
                            "username": os.environ.get("HERMES_DASHBOARD_BASIC_AUTH_USERNAME"),
                            "password_present": "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD" in os.environ,
                            "password_hash": os.environ.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH"),
                            "secret_present": "HERMES_DASHBOARD_BASIC_AUTH_SECRET" in os.environ,
                        }
                        Path(os.environ["RESULT_FILE"]).write_text(json.dumps(payload))
                        return 0
                    """
                )
            )
            basic = root / "plugins" / "dashboard_auth" / "basic"
            basic.mkdir(parents=True)
            (root / "plugins" / "__init__.py").write_text("")
            (root / "plugins" / "dashboard_auth" / "__init__.py").write_text("")
            (basic / "__init__.py").write_text(
                "def hash_password(password):\n"
                "    return 'fake$' + password.encode('utf-8').hex()\n"
            )
            result_file = root / "result.json"
            env = os.environ.copy()
            env.update(
                {
                    "PYTHONPATH": str(root),
                    "RESULT_FILE": str(result_file),
                }
            )
            password = " correct horse battery staple "

            result = subprocess.run(
                [
                    sys.executable,
                    str(DESKTOP_LAUNCHER),
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9119",
                    "--skip-build",
                ],
                input=password + "\n",
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result_file.read_text())
            self.assertEqual(
                payload["argv_at_import"],
                [
                    "hermes",
                    "-p",
                    "default",
                    "serve",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9119",
                    "--skip-build",
                ],
            )
            self.assertEqual(
                payload["argv"],
                [
                    "hermes",
                    "serve",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9119",
                    "--skip-build",
                ],
            )
            self.assertEqual(payload["username"], "hermes")
            self.assertFalse(payload["password_present"])
            self.assertEqual(payload["password_hash"], "fake$" + password.encode().hex())
            self.assertFalse(payload["secret_present"])
            self.assertNotIn(password, " ".join(payload["argv_at_import"]))


class DesktopBackendShellTests(unittest.TestCase):
    def test_disabled_feature_needs_no_password_and_starts_nothing(self) -> None:
        script = textwrap.dedent(
            f"""
            set -euo pipefail
            source {DESKTOP_LIB!s}
            ENABLE_DESKTOP_BACKEND=false
            ACCESS_PASSWORD=""
            DESKTOP_BACKEND_PID=""
            desktop_backend_validate_options
            desktop_backend_start
            test -z "$DESKTOP_BACKEND_PID"
            """
        )

        result = run_bash(script)

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_enabled_feature_requires_access_password(self) -> None:
        script = textwrap.dedent(
            f"""
            set -euo pipefail
            source {DESKTOP_LIB!s}
            ENABLE_DESKTOP_BACKEND=true
            ACCESS_PASSWORD=""
            desktop_backend_validate_options
            """
        )

        result = run_bash(script)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("access_password", result.stderr)

    def test_enabled_feature_rejects_whitespace_only_password(self) -> None:
        script = textwrap.dedent(
            f"""
            set -euo pipefail
            source {DESKTOP_LIB!s}
            ENABLE_DESKTOP_BACKEND=true
            ACCESS_PASSWORD=$' \\t '
            desktop_backend_validate_options
            """
        )

        result = run_bash(script)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("access_password", result.stderr)

    def test_enabled_runtime_rejects_serve_without_skip_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv_bin = root / "venv" / "bin"
            venv_bin.mkdir(parents=True)
            fake_python = venv_bin / "python"
            fake_python.write_text("#!/bin/bash\nexit 0\n")
            fake_python.chmod(0o755)
            fake_hermes = venv_bin / "hermes"
            fake_hermes.write_text(
                "#!/bin/bash\nprintf '%s\\n' 'usage: hermes serve --host HOST --port PORT'\n"
            )
            fake_hermes.chmod(0o755)
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                source {DESKTOP_LIB!s}
                ENABLE_DESKTOP_BACKEND=true
                ACCESS_PASSWORD=password
                VENV_DIR={root / 'venv'!s}
                DESKTOP_BACKEND_LAUNCHER={DESKTOP_LAUNCHER!s}
                desktop_backend_validate_runtime
                """
            )

            result = run_bash(script)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--skip-build", result.stderr)

    def test_start_uses_official_serve_contract_and_password_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv_bin = root / "venv" / "bin"
            venv_bin.mkdir(parents=True)
            fake_python = venv_bin / "python"
            fake_python.write_text(
                textwrap.dedent(
                    """#!/bin/bash
                    set -euo pipefail
                    printf '%s\n' "$@" > "$CAPTURE_ARGS"
                    cat > "$CAPTURE_STDIN"
                    trap 'exit 0' TERM INT
                    while :; do sleep 1; done
                    """
                )
            )
            fake_python.chmod(0o755)
            fake_hermes = venv_bin / "hermes"
            fake_hermes.write_text("#!/bin/bash\nprintf '%s\\n' '--skip-build'\n")
            fake_hermes.chmod(0o755)
            primary_home = root / "profile"
            primary_home.mkdir()
            capture_args = root / "args"
            capture_stdin = root / "stdin"
            env = {
                "CAPTURE_ARGS": str(capture_args),
                "CAPTURE_STDIN": str(capture_stdin),
            }
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                source {DESKTOP_LIB!s}
                ENABLE_DESKTOP_BACKEND=true
                ACCESS_PASSWORD='a password with spaces'
                PRIMARY_HOME={primary_home!s}
                VENV_DIR={root / 'venv'!s}
                BASE_PATH=/usr/bin:/bin
                DESKTOP_BACKEND_PORT=9119
                DESKTOP_BACKEND_LAUNCHER={DESKTOP_LAUNCHER!s}
                DESKTOP_BACKEND_PID=""
                trap desktop_backend_stop EXIT
                desktop_backend_validate_options
                desktop_backend_validate_runtime
                desktop_backend_start
                sleep 0.2
                test -n "$DESKTOP_BACKEND_PID"
                kill -0 "$DESKTOP_BACKEND_PID"
                desktop_backend_stop
                """
            )

            result = run_bash(script, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            args = capture_args.read_text().splitlines()
            self.assertEqual(
                args,
                [
                    str(DESKTOP_LAUNCHER),
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9119",
                    "--skip-build",
                ],
            )
            self.assertEqual(capture_stdin.read_text(), "a password with spaces\n")
            self.assertNotIn("a password with spaces", " ".join(args))
            self.assertIn("trusted LAN/VPN/Tailscale", result.stdout)

    def test_supervisor_restarts_a_crashed_backend_and_stop_terminates_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv_bin = root / "venv" / "bin"
            venv_bin.mkdir(parents=True)
            fake_python = venv_bin / "python"
            fake_python.write_text(
                textwrap.dedent(
                    """#!/bin/bash
                    set -euo pipefail
                    count=0
                    [ ! -f "$COUNT_FILE" ] || count=$(cat "$COUNT_FILE")
                    count=$((count + 1))
                    printf '%s' "$count" > "$COUNT_FILE"
                    cat >/dev/null
                    if [ "$count" -eq 1 ]; then exit 7; fi
                    trap 'exit 0' TERM INT
                    while :; do sleep 1; done
                    """
                )
            )
            fake_python.chmod(0o755)
            fake_hermes = venv_bin / "hermes"
            fake_hermes.write_text("#!/bin/bash\nprintf '%s\\n' '--skip-build'\n")
            fake_hermes.chmod(0o755)
            primary_home = root / "profile"
            primary_home.mkdir()
            count_file = root / "count"
            env = {"COUNT_FILE": str(count_file)}
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                source {DESKTOP_LIB!s}
                ENABLE_DESKTOP_BACKEND=true
                ACCESS_PASSWORD=restart-probe-password
                PRIMARY_HOME={primary_home!s}
                VENV_DIR={root / 'venv'!s}
                BASE_PATH=/usr/bin:/bin
                DESKTOP_BACKEND_LAUNCHER={DESKTOP_LAUNCHER!s}
                DESKTOP_BACKEND_STOP_TIMEOUT=3
                DESKTOP_BACKEND_PID=""
                trap desktop_backend_stop EXIT
                desktop_backend_validate_options
                desktop_backend_validate_runtime
                desktop_backend_start
                for _ in $(seq 1 50); do
                    if ! kill -0 "$DESKTOP_BACKEND_PID" 2>/dev/null; then break; fi
                    sleep 0.02
                done
                ! kill -0 "$DESKTOP_BACKEND_PID" 2>/dev/null
                desktop_backend_supervise
                for _ in $(seq 1 50); do
                    if [ "$(cat {count_file!s} 2>/dev/null || true)" = "2" ]; then break; fi
                    sleep 0.02
                done
                test "$(cat {count_file!s})" = "2"
                kill -0 "$DESKTOP_BACKEND_PID"
                desktop_backend_stop
                test -z "$DESKTOP_BACKEND_PID"
                """
            )

            result = run_bash(script, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Exited (code: 7); restarting", result.stdout)
            self.assertEqual(count_file.read_text(), "2")

    def test_sigterm_during_started_backend_runs_shutdown_and_stops_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv_bin = root / "venv" / "bin"
            venv_bin.mkdir(parents=True)
            fake_python = venv_bin / "python"
            fake_python.write_text(
                textwrap.dedent(
                    """#!/bin/bash
                    set -euo pipefail
                    cat >/dev/null
                    trap 'exit 0' TERM INT
                    while :; do sleep 1; done
                    """
                )
            )
            fake_python.chmod(0o755)
            fake_hermes = venv_bin / "hermes"
            fake_hermes.write_text("#!/bin/bash\nprintf '%s\\n' '--skip-build'\n")
            fake_hermes.chmod(0o755)
            primary_home = root / "profile"
            primary_home.mkdir()
            marker = root / "shutdown-marker"
            env = {"SIGNAL_MARKER": str(marker)}
            script = textwrap.dedent(
                f"""
                set -euo pipefail
                source {DESKTOP_LIB!s}
                ENABLE_DESKTOP_BACKEND=true
                ACCESS_PASSWORD=signal-probe-password
                PRIMARY_HOME={primary_home!s}
                VENV_DIR={root / 'venv'!s}
                BASE_PATH=/usr/bin:/bin
                DESKTOP_BACKEND_LAUNCHER={DESKTOP_LAUNCHER!s}
                DESKTOP_BACKEND_STOP_TIMEOUT=3
                DESKTOP_BACKEND_PID=""
                trap desktop_backend_stop EXIT
                shutdown() {{
                    desktop_backend_stop
                    printf 'handled' > "$SIGNAL_MARKER"
                    exit 0
                }}
                trap shutdown SIGTERM SIGINT
                desktop_backend_validate_options
                desktop_backend_validate_runtime
                desktop_backend_start
                kill -0 "$DESKTOP_BACKEND_PID"
                kill -TERM $$
                exit 99
                """
            )

            result = run_bash(script, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(), "handled")
            self.assertIn("[desktop-backend] Stopped", result.stdout)

    def test_run_script_integrates_validation_start_stop_and_supervision(self) -> None:
        run_sh = RUN_SH.read_text()

        self.assertIn("ENABLE_DESKTOP_BACKEND=$(opt_bool enable_desktop_backend)", run_sh)
        self.assertIn("source \"$DESKTOP_BACKEND_LIB\"", run_sh)
        self.assertIn("desktop_backend_validate_options", run_sh)
        self.assertIn("desktop_backend_validate_runtime", run_sh)
        self.assertIn("desktop_backend_start", run_sh)
        self.assertIn("desktop_backend_stop", run_sh)
        self.assertIn("desktop_backend_supervise", run_sh)
        self.assertLess(run_sh.index("shutdown() {"), run_sh.index("trap shutdown SIGTERM SIGINT"))
        self.assertLess(run_sh.index("trap shutdown SIGTERM SIGINT"), run_sh.index("desktop_backend_start"))
        self.assertLess(run_sh.index("desktop_backend_stop"), run_sh.index("Gateway stopped"))


if __name__ == "__main__":
    unittest.main()
