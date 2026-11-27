"""Behavior tests for the optional Hermes Webapp listener."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "hermes_agent"
CONFIG = ADDON / "config.yaml"
RUN_SH = ADDON / "run.sh"
WEBAPP_LIB = ADDON / "webapp.sh"
DOCKERFILE = ADDON / "Dockerfile"
TRANSLATIONS = ADDON / "translations" / "en.yaml"
README = ROOT / "README.md"
CHANGELOG = ADDON / "CHANGELOG.md"
BASH = "/bin/bash" if sys.platform == "darwin" else "bash"

STUB_HERMES = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    printf '%s\\n' "$*" >>"$RECORD"
    # -p/--profile is the add-on's pin; the command itself starts after it.
    while [ "${1-}" = "-p" ] || [ "${1-}" = "--profile" ]; do shift 2; done
    if [ "$1" = "config" ] && [ "$2" = "get" ]; then
        case "$3" in
            webapp.enabled) printf '%s\\n' "${FAKE_ENABLED-}" ;;
            webapp.host) printf '%s\\n' "${FAKE_HOST-}" ;;
            plugins.disabled)
                printf '%s\\n' "${FAKE_DISABLED-}"
                exit "${FAKE_DISABLED_RC:-0}"
                ;;
            dashboard.basic_auth.secret) printf '%s\\n' "${FAKE_SECRET-}" ;;
            *) printf '\\n' ;;
        esac
        exit 0
    fi
    if [ "$1" = "webapp" ]; then
        printf 'WEBAPP_NODE_OPTIONS=%s\\n' "${NODE_OPTIONS-}" >>"$RECORD"
        printf 'WEBAPP_RAYON_THREADS=%s\\n' "${RAYON_NUM_THREADS-}" >>"$RECORD"
        printf 'WEBAPP_HERMES_HOME=%s\\n' "${HERMES_HOME-}" >>"$RECORD"
        [ -n "${FAKE_HELP-}" ] && printf '%s\\n' "$FAKE_HELP"
        exit "${FAKE_BUILD_RC:-0}"
    fi
    exit 0
    """
)

STUB_PYTHON = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    printf 'python %s\\n' "$*" >>"$RECORD"
    if [ "${STUB_READ_STDIN:-0}" = "1" ]; then
        cat >"$RECORD.stdin"
    fi
    [ -n "${STUB_HASH-}" ] && printf '%s' "$STUB_HASH"
    exit "${STUB_PYTHON_RC:-0}"
    """
)


class ManifestTests(unittest.TestCase):
    def test_feature_is_opt_in_and_port_is_unmapped_by_default(self) -> None:
        manifest = yaml.safe_load(CONFIG.read_text())

        self.assertIs(manifest["options"]["enable_webapp"], False)
        self.assertEqual(manifest["schema"]["enable_webapp"], "bool")
        self.assertIn("9120/tcp", manifest["ports"])
        self.assertIsNone(manifest["ports"]["9120/tcp"])
        self.assertIn("Webapp", manifest["ports_description"]["9120/tcp"])
        self.assertRegex(manifest["ports_description"]["9120/tcp"], r"(?i)opt-in")
        self.assertRegex(manifest["ports_description"]["9120/tcp"], r"(?i)LAN|VPN|Tailscale")

    def test_container_port_is_fixed_like_the_desktop_backend(self) -> None:
        manifest = yaml.safe_load(CONFIG.read_text())

        # A configured container port would silently disagree with the static
        # Home Assistant port mapping, so the option does not exist.
        self.assertNotIn("webapp_port", manifest["options"])
        self.assertNotIn("webapp_port", manifest["schema"])

    def test_bundle_build_dependencies_stay_out_of_backups(self) -> None:
        manifest = yaml.safe_load(CONFIG.read_text())

        self.assertIn(".hermes/hermes-agent/.build", manifest["backup_exclude"])
        self.assertNotIn(".hermes/hermes-agent/apps/desktop/dist-webapp", manifest["backup_exclude"])

    def test_container_ships_the_runtime_library(self) -> None:
        self.assertIn(
            "COPY webapp.sh /usr/local/lib/hermes-webapp.sh",
            DOCKERFILE.read_text(),
        )

    def test_container_ships_the_sign_in_state_helper(self) -> None:
        # Without this line the entrypoint would call a missing script and the
        # Webapp would never get a sign-in state.
        dockerfile = DOCKERFILE.read_text()
        self.assertIn(
            "COPY webapp-auth-state.py /usr/local/bin/hermes-webapp-auth-state",
            dockerfile,
        )
        self.assertTrue((ADDON / "webapp-auth-state.py").is_file())

    def test_user_facing_text_states_opt_in_risk_and_trusted_network_boundary(self) -> None:
        for text in (TRANSLATIONS.read_text(), README.read_text()):
            self.assertIn("enable_webapp", text)
            self.assertIn("9120", text)
            self.assertRegex(text, r"(?i)LAN|VPN|Tailscale")
            self.assertRegex(text, r"(?i)risk|powerful|full agent control")
        changelog = CHANGELOG.read_text()
        self.assertIn("enable_webapp", changelog)
        self.assertIn("opt-in", changelog)
        self.assertIn("#69", changelog)


class WebappLibraryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.venv = self.root / "venv"
        (self.venv / "bin").mkdir(parents=True)
        hermes = self.venv / "bin" / "hermes"
        hermes.write_text(STUB_HERMES)
        hermes.chmod(0o755)
        python = self.venv / "bin" / "python"
        python.write_text(STUB_PYTHON)
        python.chmod(0o755)
        self.home = self.root / "config"
        (self.home / "logs").mkdir(parents=True)
        # The sign-in helper refuses to guess: it needs a checkout with the plugin.
        self.checkout = self.root / "checkout"
        (self.checkout / "plugins" / "dashboard_auth").mkdir(parents=True)
        self.record = self.root / "record.txt"
        self.record.touch()

    def run_lib(self, body: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        merged.update(
            {
                "VENV_DIR": str(self.venv),
                "SRC_DIR": str(self.checkout),
                "RECORD": str(self.record),
                "BASE_PATH": "/usr/bin:/bin",
                "ACCESS_PASSWORD": "",
            }
        )
        if env:
            merged.update(env)
        script = (
            f"set -euo pipefail\nsource {shlex.quote(str(WEBAPP_LIB))}\n"
            + textwrap.dedent(body)
        )
        return subprocess.run(
            [BASH, "-c", script],
            check=False,
            text=True,
            capture_output=True,
            env=merged,
        )

    def raw_calls(self) -> list[str]:
        return [line for line in self.record.read_text().splitlines() if line]

    def calls(self) -> list[str]:
        """Recorded CLI calls without the add-on's `-p default` pin."""
        return [line.removeprefix("-p default ") for line in self.raw_calls()]


class OptionValidationTests(WebappLibraryTestCase):
    def test_disabled_option_needs_no_credentials(self) -> None:
        result = self.run_lib("ENABLE_WEBAPP=false\nwebapp_validate_options\n")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_enabled_option_requires_a_non_blank_password(self) -> None:
        for password in ("", "   "):
            with self.subTest(password=password):
                result = self.run_lib(
                    "ENABLE_WEBAPP=true\nwebapp_validate_options\n",
                    env={"ACCESS_PASSWORD": password},
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn("requires access_password", result.stderr)

    def test_enabled_option_accepts_a_password(self) -> None:
        result = self.run_lib(
            "ENABLE_WEBAPP=true\nwebapp_validate_options\n",
            env={"ACCESS_PASSWORD": "correct horse battery staple"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_requires_a_webapp_build_only_command(self) -> None:
        ok = self.run_lib(
            "ENABLE_WEBAPP=true\nwebapp_validate_runtime\n",
            env={"FAKE_HELP": "usage: hermes webapp [--build-only]"},
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)

        missing = self.run_lib(
            "ENABLE_WEBAPP=true\nwebapp_validate_runtime\n",
            env={"FAKE_HELP": "usage: hermes webapp"},
        )
        self.assertEqual(missing.returncode, 1)
        self.assertIn("--build-only", missing.stderr)


class ConfigureTests(WebappLibraryTestCase):
    def test_enabled_writes_listener_settings_before_the_gateway_starts(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_configure {shlex.quote(str(self.home))}\n',
            env={"FAKE_SECRET": "existing-secret"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                "config set --force webapp.enabled true",
                "config set --force webapp.host 0.0.0.0",
                "config set --force webapp.port 9120",
            ],
        )
        # The signing secret is a credential: it must not land in config.yaml.
        self.assertNotIn("dashboard.basic_auth", "\n".join(self.calls()))

    def test_auth_state_passes_the_password_on_stdin_and_returns_both_lines(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_auth_state {shlex.quote(str(self.home))}\n',
            env={
                "ACCESS_PASSWORD": "first-password",
                "STUB_READ_STDIN": "1",
                "STUB_HASH": "scrypt$16384$8$1$c2FsdA==$ZGlnZXN0\nc2VjcmV0LXNlY3JldA==",
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2, result.stdout)
        self.assertTrue(lines[0].startswith("scrypt$"))
        self.assertEqual(lines[1], "c2VjcmV0LXNlY3JldA==")
        # The password travels on stdin, never as an argument (ps, /proc/cmdline).
        stdin_seen = Path(str(self.record) + ".stdin").read_text()
        self.assertEqual(stdin_seen, "first-password")
        self.assertNotIn("first-password", " ".join(self.raw_calls()))

    def test_config_writes_never_carry_the_signing_secret(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_configure {shlex.quote(str(self.home))}\n',
            env={"FAKE_SECRET": "existing-secret"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("existing-secret", " ".join(self.raw_calls()))
        self.assertNotIn("dashboard.basic_auth", " ".join(self.raw_calls()))

    def test_disabling_skips_the_cli_when_the_profile_never_had_a_block(self) -> None:
        skipped = self.run_lib(
            f'ENABLE_WEBAPP=false\nwebapp_configure {shlex.quote(str(self.home))}\n',
        )

        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        # No CLI start on boot for users who never enabled the Webapp.
        self.assertEqual(self.calls(), [])

    def test_disabling_clears_only_a_block_that_is_switched_on(self) -> None:
        (self.home / "config.yaml").write_text("webapp:\n  enabled: true\n")
        off_unset = self.run_lib(
            f'ENABLE_WEBAPP=false\nwebapp_configure {shlex.quote(str(self.home))}\n',
            env={"FAKE_ENABLED": "False"},
        )
        self.assertEqual(off_unset.returncode, 0, off_unset.stderr)
        self.assertEqual(self.calls(), ["config get webapp.enabled --raw"])

        self.record.write_text("")
        off_was_on = self.run_lib(
            f'ENABLE_WEBAPP=false\nwebapp_configure {shlex.quote(str(self.home))}\n',
            env={"FAKE_ENABLED": "True"},
        )
        self.assertEqual(off_was_on.returncode, 0, off_was_on.stderr)
        self.assertEqual(
            self.calls(),
            ["config get webapp.enabled --raw", "config set --force webapp.enabled false"],
        )

    def test_disabled_auth_plugin_is_reported(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_warn_if_basic_auth_disabled {shlex.quote(str(self.home))}\n',
            env={"FAKE_DISABLED": '["basic"]'},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plugins enable basic", result.stderr)

        self.record.write_text("")
        clean = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_warn_if_basic_auth_disabled {shlex.quote(str(self.home))}\n',
            env={"FAKE_DISABLED": "[]"},
        )
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertNotIn("plugins enable basic", clean.stderr)

    def test_unset_plugin_list_does_not_abort_startup(self) -> None:
        # `hermes config get plugins.disabled` exits 1 when the key was never
        # written; the entrypoint runs with `set -euo pipefail`.
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_warn_if_basic_auth_disabled {shlex.quote(str(self.home))}\n',
            env={"FAKE_DISABLED": "", "FAKE_DISABLED_RC": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("plugins enable basic", result.stderr)

    def test_cli_is_pinned_away_from_a_sticky_interactive_profile(self) -> None:
        # HERMES_HOME at a machine root follows `hermes profile use`; the pin
        # resolves to that same root. A configured Webapp block is what makes
        # the entrypoint consult the CLI at all.
        (self.home / "config.yaml").write_text("webapp:\n  enabled: false\n")
        self.run_lib(
            f'ENABLE_WEBAPP=false\nwebapp_configure {shlex.quote(str(self.home))}\n',
        )
        self.assertTrue(all(call.startswith("-p default ") for call in self.raw_calls()))

        profile_home = self.root / "profiles" / "amy"
        (profile_home / "logs").mkdir(parents=True)
        (profile_home / "config.yaml").write_text("webapp:\n  enabled: false\n")
        self.record.write_text("")
        self.run_lib(
            f'ENABLE_WEBAPP=false\nwebapp_configure {shlex.quote(str(profile_home))}\n',
        )
        self.assertTrue(self.raw_calls())
        self.assertFalse(any(call.startswith("-p ") for call in self.raw_calls()))

    def test_password_reaches_the_helper_byte_exact_on_stdin(self) -> None:
        # The provider strips surrounding whitespace; nginx does not. The helper
        # must see the configured bytes, and never through argv (ps).
        password = "  padded password  "
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_auth_state {shlex.quote(str(self.home))}\n',
            env={"ACCESS_PASSWORD": password, "STUB_HASH": "scrypt$stub$hash\nstub-secret",
                 "STUB_READ_STDIN": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "scrypt$stub$hash\nstub-secret")
        self.assertEqual((self.root / "record.txt.stdin").read_text(), password)
        self.assertNotIn(password, "\n".join(self.raw_calls()))


class BuildTests(WebappLibraryTestCase):
    def test_build_runs_in_the_background_with_bounded_resources(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_start_build {shlex.quote(str(self.home))}\nwait\n',
            env={"NODE_OPTIONS": "", "RAYON_NUM_THREADS": ""},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("webapp --build-only", calls)
        joined = "\n".join(calls)
        self.assertIn(f"WEBAPP_HERMES_HOME={self.home}", joined)
        self.assertIn("WEBAPP_NODE_OPTIONS=--max-old-space-size=2048", joined)
        self.assertIn("WEBAPP_RAYON_THREADS=2", joined)
        self.assertTrue((self.home / "logs" / "webapp-build.log").exists())

    def test_existing_limits_win(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_start_build {shlex.quote(str(self.home))}\nwait\n',
            env={"NODE_OPTIONS": "--max-old-space-size=1024", "RAYON_NUM_THREADS": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        joined = "\n".join(self.calls())
        # A second V8 flag would not win: later flags override earlier ones.
        self.assertIn("WEBAPP_NODE_OPTIONS=--max-old-space-size=1024", joined)
        self.assertNotIn("--max-old-space-size=2048", joined)
        self.assertIn("WEBAPP_RAYON_THREADS=1", joined)

    def test_unrelated_node_options_are_kept(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_start_build {shlex.quote(str(self.home))}\nwait\n',
            env={"NODE_OPTIONS": "--no-warnings", "RAYON_NUM_THREADS": ""},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        joined = "\n".join(self.calls())
        self.assertIn("WEBAPP_NODE_OPTIONS=--no-warnings --max-old-space-size=2048", joined)
        self.assertIn("WEBAPP_RAYON_THREADS=2", joined)

    def test_failed_build_marks_the_bundle_stale(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_start_build {shlex.quote(str(self.home))}\nwait\n',
            env={"FAKE_BUILD_RC": "1"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        # The -c payload spans lines, so count the launches and read the text.
        python_calls = [call for call in self.raw_calls() if call.startswith("python -c ")]
        self.assertEqual(len(python_calls), 1)
        self.assertIn("mark_webapp_stale", self.record.read_text())

        self.record.write_text("")
        ok = self.run_lib(
            f'ENABLE_WEBAPP=true\nwebapp_start_build {shlex.quote(str(self.home))}\nwait\n',
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertFalse([call for call in self.raw_calls() if call.startswith("python -c ")])

    def test_disabled_option_starts_no_build(self) -> None:
        result = self.run_lib(
            f'ENABLE_WEBAPP=false\nwebapp_start_build {shlex.quote(str(self.home))}\nwait\n',
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])


class BuildHardeningTests(unittest.TestCase):
    """The build runs unattended, so its isolation is asserted on the script."""

    def setUp(self) -> None:
        self.lib = WEBAPP_LIB.read_text()

    def test_build_is_niced_and_deadline_bounded(self) -> None:
        self.assertIn("nice -n 10 timeout -k 30", self.lib)
        self.assertIn('WEBAPP_BUILD_TIMEOUT:-3600', self.lib)

    def test_build_drops_the_addon_credentials(self) -> None:
        self.assertIn(
            "unset ACCESS_PASSWORD WEBAPP_PASSWORD_HASH WEBAPP_AUTH_SECRET HERMES_ADDON_API_KEY",
            self.lib,
        )

    def test_hash_helper_imports_from_the_checkout(self) -> None:
        self.assertIn("PYTHONPATH=", self.lib)
        self.assertIn('local root="${SRC_DIR:-$home}"', self.lib)


class RunScriptWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = RUN_SH.read_text()

    def test_option_is_read_from_options_json(self) -> None:
        self.assertIn("ENABLE_WEBAPP=$(opt_bool enable_webapp)", self.script)

    def test_library_is_sourced_and_validated(self) -> None:
        self.assertIn('"/usr/local/lib/hermes-webapp.sh"', self.script)
        self.assertIn('source "$WEBAPP_LIB"', self.script)
        self.assertIn("webapp_validate_options || exit 1", self.script)
        self.assertIn("webapp_validate_runtime || exit 1", self.script)

    def test_configuration_is_written_before_the_first_gateway(self) -> None:
        configure = self.script.index('webapp_configure "${PROFILE_HOMES[0]}"')
        first_gateway = self.script.index('for i in "${!PROFILE_DIRS[@]}"; do\n    start_gateway_signal_safe')

        self.assertLess(configure, first_gateway)

    def test_credentials_reach_only_the_primary_gateway_environment(self) -> None:
        start = self.script.index("start_gateway_for_profile() {")
        end = self.script.index("\ninstall_start_hermes_wrapper() {", start)
        body = self.script[start:end]

        self.assertIn('if [ "$i" = "0" ] && [ "$ENABLE_WEBAPP" = "true" ]; then', body)
        self.assertIn('export HERMES_DASHBOARD_BASIC_AUTH_USERNAME="hermes"', body)
        self.assertIn(
            'export HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH="$WEBAPP_PASSWORD_HASH"', body
        )
        self.assertNotIn("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=", self.script)
        self.assertNotIn("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH", self.script[:start])

    def test_credentials_are_prepared_once_before_the_gateways(self) -> None:
        prepared = self.script.index('WEBAPP_AUTH_STATE=$(webapp_auth_state "${PROFILE_HOMES[0]}"')
        first_gateway = self.script.index('for i in "${!PROFILE_DIRS[@]}"; do\n    start_gateway_signal_safe')

        self.assertLess(prepared, first_gateway)
        self.assertIn('case "$WEBAPP_PASSWORD_HASH" in', self.script)
        self.assertIn("scrypt", self.script)
        self.assertIn('export HERMES_DASHBOARD_BASIC_AUTH_SECRET="$WEBAPP_AUTH_SECRET"', self.script)

    def test_former_primary_profiles_are_cleared(self) -> None:
        start = self.script.index('webapp_warn_if_basic_auth_disabled "${PROFILE_HOMES[0]}"')
        end = self.script.index("install_start_hermes_wrapper", start)
        block = self.script[start:end]

        self.assertIn('if [ "${#PROFILE_DIRS[@]}" -gt 1 ]', block)
        self.assertIn("ENABLE_WEBAPP=false; webapp_configure", block)

    def test_a_running_build_is_stopped_on_shutdown(self) -> None:
        shutdown = self.script.index("\nshutdown() {")
        body = self.script[shutdown:self.script.index("trap shutdown SIGTERM SIGINT", shutdown)]

        self.assertIn('kill -TERM "$WEBAPP_BUILD_PID"', body)

    def test_build_starts_after_all_services(self) -> None:
        build = self.script.index('webapp_start_build "${PROFILE_HOMES[0]}"')
        services = self.script.index('echo "[run] All services started"')

        self.assertLess(build, services)


if __name__ == "__main__":
    unittest.main()
