"""Regression tests for add-on-owned Hermes API server credentials."""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "hermes_agent"
API_SERVER_LIB = ADDON / "api-server.sh"
PROFILE_INIT_LIB = ADDON / "profile-init.sh"
RUN_SCRIPT = ADDON / "run.sh"
GATEWAY_LAUNCHER = ADDON / "gateway-launcher.py"
DOCKERFILE = ADDON / "Dockerfile"
CONFIG = ADDON / "config.yaml"
TRANSLATION = ADDON / "translations" / "en.yaml"
CHANGELOG = ADDON / "CHANGELOG.md"
README = ROOT / "README.md"

# Home Assistant add-on scripts must remain compatible with macOS' Bash 3.2.
BASH = "/bin/bash" if sys.platform == "darwin" and os.path.exists("/bin/bash") else "bash"

PLACEHOLDERS = (
    "*",
    "**",
    "***",
    "changeme",
    "your_api_key",
    "your_api_key_here",
    "your-api-key",
    "placeholder",
    "example",
    "dummy",
    "null",
    "none",
)


def _mixed_case(value):
    return "".join(
        character.upper() if index % 2 == 0 else character.lower()
        for index, character in enumerate(value)
    )


def _run_validation(enable_api, access_password=None):
    assignment = (
        f"export ACCESS_PASSWORD={shlex.quote(access_password)}"
        if access_password is not None
        else "unset ACCESS_PASSWORD"
    )
    script = textwrap.dedent(
        f"""
        set -uo pipefail
        export ENABLE_API={shlex.quote(enable_api)}
        {assignment}
        source {shlex.quote(str(API_SERVER_LIB))}
        if api_server_validate_options; then
            if [ "${{ACCESS_PASSWORD+x}}" = x ]; then
                printf 'VALUE=%s\\n' "$ACCESS_PASSWORD"
            else
                printf 'VALUE=__UNSET__\\n'
            fi
        else
            exit $?
        fi
        """
    )
    return subprocess.run(
        [BASH, "-c", script],
        text=True,
        capture_output=True,
        check=False,
    )


def _read_access_password_from_json(value):
    with tempfile.TemporaryDirectory() as tmp:
        options_path = Path(tmp) / "options.json"
        options_path.write_text(json.dumps({"access_password": value}))
        script = textwrap.dedent(
            f"""
            set -euo pipefail
            source {shlex.quote(str(API_SERVER_LIB))}
            api_server_read_json_string \
                {shlex.quote(str(options_path))} access_password "" READ_VALUE
            printf '%s' "$READ_VALUE"
            """
        )
        return subprocess.run(
            [BASH, "-c", script],
            capture_output=True,
            check=False,
        )


def _validate_env_records(options):
    with tempfile.TemporaryDirectory() as tmp:
        options_path = Path(tmp) / "options.json"
        options_path.write_text(json.dumps(options))
        script = textwrap.dedent(
            f"""
            set -uo pipefail
            source {shlex.quote(str(API_SERVER_LIB))}
            api_server_validate_env_records {shlex.quote(str(options_path))}
            """
        )
        return subprocess.run(
            [BASH, "-c", script],
            text=True,
            capture_output=True,
            check=False,
        )


def _run_reserved_env_merge(profile_index):
    password = '0123456789ab #$"`!'
    reserved = (
        "API_SERVER_HOST",
        "API_SERVER_PORT",
        "API_SERVER_ENABLED",
        "API_SERVER_KEY",
        "GATEWAY_MULTIPLEX_PROFILES",
        "HERMES_S6_SUPERVISED_CHILD",
        "HERMES_HOME",
        "HERMES_GATEWAY_NO_SUPERVISE",
    )
    top_values = {
        "API_SERVER_HOST": "0.0.0.0",
        "API_SERVER_PORT": "9999",
        "API_SERVER_ENABLED": "false",
        "API_SERVER_KEY": "top-level-key",
        "GATEWAY_MULTIPLEX_PROFILES": "true",
        "HERMES_S6_SUPERVISED_CHILD": "false",
        "HERMES_HOME": "/wrong/top-level/profile",
        "HERMES_GATEWAY_NO_SUPERVISE": "0",
    }
    profile_values = {
        "API_SERVER_HOST": "192.0.2.10",
        "API_SERVER_PORT": "7777",
        "API_SERVER_ENABLED": "false",
        "API_SERVER_KEY": "profile-key",
        "GATEWAY_MULTIPLEX_PROFILES": "true",
        "HERMES_S6_SUPERVISED_CHILD": "false",
        "HERMES_HOME": "/wrong/per-profile/home",
        "HERMES_GATEWAY_NO_SUPERVISE": "0",
    }
    options = {
        "profiles": ["primary", "worker"],
        "env_vars": [
            {"name": name, "value": top_values[name]} for name in reserved
        ],
        "profile_env_vars": [
            {"profile": "worker", "name": name, "value": profile_values[name]}
            for name in reserved
        ],
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "home"
        home.mkdir()
        for profile in options["profiles"]:
            profile_home = home / profile
            profile_home.mkdir()
            (profile_home / ".env").write_text(
                "API_SERVER_HOST=old-owned\n"
                "export API_SERVER_HOST=0.0.0.0\n"
                "API_SERVER_PORT=old-owned\n"
                "export API_SERVER_PORT=9999\n"
                "API_SERVER_ENABLED=old-owned\n"
                "export API_SERVER_ENABLED=false\n"
                "API_SERVER_KEY=old-owned\n"
                "export API_SERVER_KEY=stale-known-key\n"
                "GATEWAY_MULTIPLEX_PROFILES=true\n"
                "HERMES_S6_SUPERVISED_CHILD=false\n"
                "HERMES_HOME=/wrong/stale/home\n"
                "HERMES_GATEWAY_NO_SUPERVISE=0\n"
            )
        options_path = tmp_path / "options.json"
        options_path.write_text(json.dumps(options))

        script = textwrap.dedent(
            f"""
            set -euo pipefail
            export HOME={shlex.quote(str(home))}
            export OPTIONS_FILE={shlex.quote(str(options_path))}
            export HERMES_HOME_DIR=""
            export PROFILES_BASE=""
            export ENABLE_API=true
            export ACCESS_PASSWORD={shlex.quote(password)}
            source {shlex.quote(str(PROFILE_INIT_LIB))}
            resolve_profiles
            apply_env_vars_for_profile {profile_index}
            printf '%s\\n' '---ENV---'
            cat "${{PROFILE_HOMES[{profile_index}]}}/.env"
            printf '%s\\n' '---PARSED-KEY---'
            set -a
            source "${{PROFILE_HOMES[{profile_index}]}}/.env"
            set +a
            printf '%s|%s|%s|%s' \
                "$API_SERVER_HOST" "$API_SERVER_PORT" \
                "$API_SERVER_ENABLED" "$API_SERVER_KEY"
            """
        )
        result = subprocess.run(
            [BASH, "-c", script],
            text=True,
            capture_output=True,
            check=False,
        )

    if result.returncode != 0:
        raise AssertionError(f"profile env merge failed: {result.stderr}")
    stdout_log, separator, serialized = result.stdout.partition("---ENV---\n")
    if not separator:
        raise AssertionError(f"profile env output missing separator: {result.stdout}")
    env_text, parsed_separator, parsed_text = serialized.partition("---PARSED-KEY---\n")
    if not parsed_separator:
        raise AssertionError(f"profile env parse output missing separator: {result.stdout}")
    env = {}
    for line in env_text.splitlines():
        key, found, value = line.partition("=")
        if found:
            env[key] = value
    return env, stdout_log + result.stderr, password, parsed_text.split("|"), reserved


class ApiCredentialValidationTests(unittest.TestCase):
    def test_json_reader_preserves_trailing_line_breaks_for_validation(self):
        for value in ("1234567890123456\n", "1234567890123456\r\n"):
            with self.subTest(value=repr(value)):
                result = _read_access_password_from_json(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, value.encode())

    def test_json_reader_rejects_null_before_shell_assignment(self):
        result = _read_access_password_from_json("1234567890123456\x00")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_disabled_api_accepts_and_preserves_any_password(self):
        for password in (None, "", "short", "changeme", "   \t  "):
            with self.subTest(password=password):
                result = _run_validation("false", password)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = "__UNSET__" if password is None else password
                self.assertEqual(result.stdout, f"VALUE={expected}\n")

    def test_enabled_api_rejects_missing_password(self):
        result = _run_validation("true", None)
        self.assertNotEqual(result.returncode, 0)

    def test_enabled_api_rejects_whitespace_only_password(self):
        result = _run_validation("true", "   \t  ")
        self.assertNotEqual(result.returncode, 0)

    def test_enabled_api_rejects_fifteen_character_password(self):
        result = _run_validation("true", "123456789012345")
        self.assertNotEqual(result.returncode, 0)

    def test_enabled_api_accepts_exactly_sixteen_characters(self):
        password = "1234567890123456"
        result = _run_validation("true", password)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"VALUE={password}\n")

    def test_enabled_api_accepts_long_non_placeholder_password(self):
        password = "correct-horse-battery-staple"
        result = _run_validation("true", password)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"VALUE={password}\n")

    def test_enabled_api_normalizes_surrounding_whitespace_for_all_services(self):
        result = _run_validation("true", " \t1234567890123456  ")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "VALUE=1234567890123456\n")

    def test_enabled_api_matches_python_strip_for_unicode_whitespace(self):
        for whitespace in ("\u00a0", "\u2003", "\u0085", "\u001c"):
            with self.subTest(codepoint=f"U+{ord(whitespace):04X}", kind="placeholder"):
                result = _run_validation(
                    "true",
                    f"{whitespace}your_api_key_here{whitespace}",
                )
                self.assertNotEqual(result.returncode, 0)

            with self.subTest(codepoint=f"U+{ord(whitespace):04X}", kind="valid"):
                result = _run_validation(
                    "true",
                    f"{whitespace}1234567890123456{whitespace}",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "VALUE=1234567890123456\n")

    def test_enabled_api_accepts_complex_roundtrip_safe_ascii(self):
        password = '0123456789ab #$"`!'
        result = _run_validation("true", password)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"VALUE={password}\n")

    def test_enabled_api_rejects_nonroundtrip_credential_characters(self):
        for password in (
            "0123456789ab'cde",
            "0123456789ab\\cde",
            "0123456789abcde${UNSET}",
            "0123456789abcdeé",
            "0123456789ab\tcde",
        ):
            with self.subTest(password=repr(password)):
                result = _run_validation("true", password)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(password, result.stdout + result.stderr)

    def test_access_password_rejects_line_breaks_before_dotenv_serialization(self):
        for enable_api in ("false", "true"):
            for separator in ("\n", "\r", "\r\n"):
                supplied = f"1234567890123456{separator}API_SERVER_ENABLED=true"
                with self.subTest(enable_api=enable_api, separator=repr(separator)):
                    result = _run_validation(enable_api, supplied)
                    combined = result.stdout + result.stderr
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("line break", result.stderr.lower())
                    self.assertNotIn(supplied, combined)

    def test_enabled_api_rejects_all_placeholders_case_insensitively(self):
        variants = []
        for placeholder in PLACEHOLDERS:
            variants.extend((placeholder, placeholder.upper(), _mixed_case(placeholder)))
        for password in variants:
            with self.subTest(password=password):
                result = _run_validation("true", f"  {password}  ")
                self.assertNotEqual(result.returncode, 0)

    def test_invalid_password_error_is_actionable_without_secret_leakage(self):
        supplied_secret = "private-value"
        result = _run_validation("true", supplied_secret)
        combined = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("enable_api", result.stderr)
        self.assertIn("access_password", result.stderr)
        self.assertIn("16", result.stderr)
        self.assertIn("placeholder", result.stderr.lower())
        self.assertNotIn(supplied_secret, combined)


class StartupContractTests(unittest.TestCase):
    def test_run_sources_dedicated_library_with_container_and_local_fallbacks(self):
        run = RUN_SCRIPT.read_text()
        library = API_SERVER_LIB.read_text()
        self.assertIn('"/usr/local/lib/hermes-api-server.sh"', run)
        self.assertIn('/api-server.sh"', run)
        self.assertIn('source "$API_SERVER_LIB"', run)
        self.assertIn("api_server_validate_options || exit 1", run)
        self.assertEqual(library.count("/usr/bin/python3 -B"), 2)
        self.assertIn("HERMES_ADDON_API_HOST", run)
        self.assertIn("HERMES_ADDON_PROFILE_HOME", run)
        self.assertIn("HERMES_ADDON_MULTIPLEX_PROFILES", run)
        self.assertIn("HERMES_ADDON_GATEWAY_NO_SUPERVISE", run)
        self.assertIn("HERMES_ADDON_SUPERVISED_CHILD", run)
        self.assertIn("gateway-launcher.py", run)
        self.assertIn('export HERMES_S6_SUPERVISED_CHILD="1"', run)

    def test_gateway_launcher_reasserts_addon_api_values_after_every_env_load(self):
        self.assertTrue(GATEWAY_LAUNCHER.is_file())
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "hermes_gateway_launcher_test",
            GATEWAY_LAUNCHER,
        )
        assert spec is not None and spec.loader is not None
        launcher = importlib.util.module_from_spec(spec)
        targets = {
            "API_SERVER_HOST": "127.0.0.1",
            "API_SERVER_PORT": "8642",
            "API_SERVER_ENABLED": "true",
            "API_SERVER_KEY": "documented-key",
            "HERMES_HOME": "/config/.hermes",
            "GATEWAY_MULTIPLEX_PROFILES": "false",
            "HERMES_GATEWAY_NO_SUPERVISE": "1",
            "HERMES_S6_SUPERVISED_CHILD": "1",
        }
        handoff = {
            "HERMES_ADDON_API_HOST": targets["API_SERVER_HOST"],
            "HERMES_ADDON_API_PORT": targets["API_SERVER_PORT"],
            "HERMES_ADDON_API_ENABLED": targets["API_SERVER_ENABLED"],
            "HERMES_ADDON_API_KEY": targets["API_SERVER_KEY"],
            "HERMES_ADDON_PROFILE_HOME": targets["HERMES_HOME"],
            "HERMES_ADDON_MULTIPLEX_PROFILES": targets[
                "GATEWAY_MULTIPLEX_PROFILES"
            ],
            "HERMES_ADDON_GATEWAY_NO_SUPERVISE": targets[
                "HERMES_GATEWAY_NO_SUPERVISE"
            ],
            "HERMES_ADDON_SUPERVISED_CHILD": targets[
                "HERMES_S6_SUPERVISED_CHILD"
            ],
        }
        env_loader = types.ModuleType("hermes_cli.env_loader")
        env_loader.__file__ = "/opt/hermes/hermes_cli/env_loader.py"
        load_calls = []

        def load_dotenv(*args, **kwargs):
            load_calls.append((args, kwargs))
            for name in targets:
                os.environ[name] = "later-source-value"
            os.environ["PROVIDER_API_KEY"] = f"provider-key-{len(load_calls)}"
            return [Path("profile.env")]

        setattr(env_loader, "load_hermes_dotenv", load_dotenv)
        package = types.ModuleType("hermes_cli")
        package.__path__ = []
        setattr(package, "env_loader", env_loader)
        main = types.ModuleType("hermes_cli.main")

        def hermes_main():
            self.assertEqual(
                {name: os.environ.get(name) for name in targets},
                targets,
            )
            self.assertEqual(os.environ.get("PROVIDER_API_KEY"), "provider-key-1")
            for name in targets:
                os.environ[name] = "runtime-drift"
            os.environ["PROVIDER_API_KEY"] = "stale-provider-key"
            self.assertEqual(
                getattr(env_loader, "load_hermes_dotenv")(),
                [Path("profile.env")],
            )
            self.assertEqual(
                {name: os.environ.get(name) for name in targets},
                targets,
            )
            self.assertEqual(os.environ.get("PROVIDER_API_KEY"), "provider-key-2")

        setattr(main, "main", hermes_main)
        gateway_package = types.ModuleType("gateway")
        gateway_package.__path__ = []
        gateway_config = types.ModuleType("gateway.config")
        gateway_api_platform = object()
        setattr(
            gateway_config,
            "Platform",
            types.SimpleNamespace(API_SERVER=gateway_api_platform),
        )
        setattr(
            gateway_config,
            "PlatformConfig",
            lambda: types.SimpleNamespace(enabled=False, extra={}),
        )
        setattr(
            gateway_config,
            "load_gateway_config",
            lambda: types.SimpleNamespace(platforms={}),
        )
        setattr(gateway_package, "config", gateway_config)
        hermes_constants = types.ModuleType("hermes_constants")
        setattr(
            hermes_constants,
            "get_default_hermes_root",
            lambda: Path("/real/hermes/root"),
        )
        modules = {
            "hermes_cli": package,
            "hermes_cli.env_loader": env_loader,
            "hermes_cli.main": main,
            "gateway": gateway_package,
            "gateway.config": gateway_config,
            "hermes_constants": hermes_constants,
        }
        with mock.patch.dict(sys.modules, modules), mock.patch.dict(
            os.environ,
            handoff,
            clear=False,
        ):
            spec.loader.exec_module(launcher)
            launcher.main()
            self.assertEqual(
                load_calls,
                [
                    ((), {"project_env": Path("/opt/hermes/.env")}),
                    ((), {}),
                ],
            )
            for source_name in handoff:
                self.assertNotIn(source_name, os.environ)

    def test_gateway_launcher_masks_sticky_profile_without_root_helper(self):
        self.assertTrue(GATEWAY_LAUNCHER.is_file())
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "hermes_gateway_launcher_legacy_test",
            GATEWAY_LAUNCHER,
        )
        assert spec is not None and spec.loader is not None
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)

        hermes_constants = types.ModuleType("hermes_constants")
        fake_main = types.SimpleNamespace(main=lambda: None)
        sticky = Path.home() / ".hermes" / "active_profile"
        real_exists = Path.exists
        imports = []

        def sentinel_exists(path):
            if path == sticky:
                return True
            return real_exists(path)

        def import_module(name):
            imports.append(name)
            self.assertFalse(sticky.exists())
            return fake_main

        with mock.patch.dict(sys.modules, {"hermes_constants": hermes_constants}), mock.patch.object(
            Path,
            "exists",
            sentinel_exists,
        ):
            selected = launcher._import_fixed_profile_main(import_module)
            self.assertTrue(sticky.exists())

        self.assertIs(selected, fake_main.main)
        self.assertEqual(imports, ["hermes_cli.main"])

    def test_gateway_launcher_strips_external_supervisor_for_legacy_cli(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "hermes_gateway_launcher_legacy_flag_test",
            GATEWAY_LAUNCHER,
        )
        assert spec is not None and spec.loader is not None
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)

        def import_module(name):
            raise ModuleNotFoundError(name="hermes_cli.subcommands")

        argv = [
            "gateway-launcher.py",
            "gateway",
            "run",
            "--external-supervisor",
        ]
        with mock.patch.object(sys, "argv", argv):
            launcher._remove_unsupported_external_supervisor(import_module)
            self.assertEqual(
                sys.argv,
                ["gateway-launcher.py", "gateway", "run"],
            )

    def test_gateway_launcher_preserves_external_supervisor_for_modern_cli(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "hermes_gateway_launcher_modern_flag_test",
            GATEWAY_LAUNCHER,
        )
        assert spec is not None and spec.loader is not None
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        parser_module = types.ModuleType("hermes_cli.subcommands.gateway")

        def build_gateway_parser():
            return "--external-supervisor"

        setattr(parser_module, "build_gateway_parser", build_gateway_parser)
        argv = [
            "gateway-launcher.py",
            "gateway",
            "run",
            "--external-supervisor",
        ]
        with mock.patch.object(sys, "argv", argv):
            launcher._remove_unsupported_external_supervisor(
                lambda name: parser_module
            )
            self.assertEqual(
                sys.argv,
                [
                    "gateway-launcher.py",
                    "gateway",
                    "run",
                    "--external-supervisor",
                ],
            )

    def test_gateway_config_guard_enforces_enabled_and_disabled_values(self):
        self.assertTrue(GATEWAY_LAUNCHER.is_file())
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "hermes_gateway_config_guard_test",
            GATEWAY_LAUNCHER,
        )
        assert spec is not None and spec.loader is not None
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        api_platform = object()

        def load_with(protected):
            stale_platform = types.SimpleNamespace(
                enabled=True,
                extra={"host": "0.0.0.0", "port": 9999, "key": "stale-key"},
            )
            config = types.SimpleNamespace(
                platforms={api_platform: stale_platform},
                multiplex_profiles=True,
            )
            gateway_config = types.SimpleNamespace(
                Platform=types.SimpleNamespace(API_SERVER=api_platform),
                PlatformConfig=lambda: types.SimpleNamespace(enabled=False, extra={}),
                load_gateway_config=lambda: config,
            )
            launcher._guard_gateway_config(gateway_config, protected)
            return gateway_config.load_gateway_config()

        disabled = load_with(
            {
                "API_SERVER_HOST": "127.0.0.1",
                "API_SERVER_PORT": "8642",
                "API_SERVER_ENABLED": "false",
                "API_SERVER_KEY": "",
            }
        )
        self.assertNotIn(api_platform, disabled.platforms)
        self.assertFalse(disabled.multiplex_profiles)

        enabled = load_with(
            {
                "API_SERVER_HOST": "127.0.0.1",
                "API_SERVER_PORT": "8642",
                "API_SERVER_ENABLED": "true",
                "API_SERVER_KEY": "documented-key",
            }
        )
        platform = enabled.platforms[api_platform]
        self.assertFalse(enabled.multiplex_profiles)
        self.assertTrue(platform.enabled)
        self.assertEqual(
            platform.extra,
            {"host": "127.0.0.1", "port": 8642, "key": "documented-key"},
        )

    def test_fixed_profile_import_masks_sticky_root_and_restores_helper(self):
        self.assertTrue(GATEWAY_LAUNCHER.is_file())
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "hermes_fixed_profile_import_test",
            GATEWAY_LAUNCHER,
        )
        assert spec is not None and spec.loader is not None
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)

        constants = types.ModuleType("hermes_constants")

        def original_root():
            return Path("/real/hermes/root")

        setattr(constants, "get_default_hermes_root", original_root)
        imported_main = types.SimpleNamespace(main=lambda: None)
        observed = {}

        def import_module(name):
            self.assertEqual(name, "hermes_cli.main")
            observed["root"] = constants.get_default_hermes_root()
            return imported_main

        with mock.patch.dict(sys.modules, {"hermes_constants": constants}):
            result = launcher._import_fixed_profile_main(import_module)

        self.assertIs(result, imported_main.main)
        self.assertEqual(observed["root"], Path(os.devnull))
        self.assertIs(constants.get_default_hermes_root, original_root)

    def test_api_validation_precedes_nginx_install_and_service_startup(self):
        run = RUN_SCRIPT.read_text()
        self.assertIn("api_server_read_json_string", run)
        self.assertIn("api_server_validate_env_records", run)
        self.assertIn("api_server_validate_options || exit 1", run)
        read_password = run.index("api_server_read_json_string")
        env_validation = run.index("api_server_validate_env_records")
        validation = run.index("api_server_validate_options || exit 1")
        self.assertLess(read_password, validation)
        self.assertLess(env_validation, validation)
        self.assertLess(validation, run.index("\nnginx\n"))
        self.assertLess(validation, run.index("\ninstall_hermes_core\n"))
        self.assertLess(validation, run.index('start_gateway_signal_safe "$i"'))

    def test_dockerfile_ships_api_validation_library(self):
        dockerfile = DOCKERFILE.read_text()
        self.assertIn(
            "COPY api-server.sh /usr/local/lib/hermes-api-server.sh",
            dockerfile,
        )
        self.assertIn(
            "COPY gateway-launcher.py /usr/local/lib/hermes-gateway-launcher.py",
            dockerfile,
        )

    def test_api_library_is_bash_3_2_syntax_compatible(self):
        self.assertTrue(API_SERVER_LIB.is_file(), "api-server.sh must exist")
        result = subprocess.run(
            [BASH, "-n", str(API_SERVER_LIB)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_env_record_validation_rejects_multiline_and_export_aliases(self):
        invalid_options = (
            {"env_vars": [{"name": "export API_SERVER_KEY", "value": "known-key"}]},
            {"env_vars": [{"name": "SAFE_NAME", "value": "ok\nAPI_SERVER_PORT=9"}]},
            {
                "profile_env_vars": [
                    {
                        "profile": "worker",
                        "name": "SAFE_NAME",
                        "value": "ok\rAPI_SERVER_ENABLED=true",
                    }
                ]
            },
            {
                "profiles": ["worker"],
                "profile_env_vars": [
                    {"profile": 7, "name": "SAFE_NAME", "value": "value"}
                ],
            },
            {
                "profiles": ["worker"],
                "profile_env_vars": [
                    {"profile": "other", "name": "SAFE_NAME", "value": "value"}
                ],
            },
        )
        for options in invalid_options:
            with self.subTest(options=options):
                result = _validate_env_records(options)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.returncode, 127)
                self.assertIn("environment variable", result.stderr.lower())
                self.assertNotIn("known-key", result.stdout + result.stderr)

    def test_env_record_validation_accepts_structured_single_line_values(self):
        result = _validate_env_records(
            {
                "profiles": ["worker"],
                "env_vars": [{"name": "OPENROUTER_API_KEY", "value": "key=value"}],
                "profile_env_vars": [
                    {"profile": "worker", "name": "SAFE_NAME", "value": "value"}
                ],
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class ReservedApiVariableTests(unittest.TestCase):
    def _assert_addon_owned_values(self, profile_index):
        env, stderr, password, parsed_values, reserved = _run_reserved_env_merge(
            profile_index
        )
        expected_port = "8642" if profile_index == 0 else "8643"
        self.assertEqual(env.get("API_SERVER_HOST"), "127.0.0.1")
        self.assertEqual(env.get("API_SERVER_PORT"), expected_port)
        self.assertEqual(env.get("API_SERVER_ENABLED"), "true")
        self.assertEqual(env.get("API_SERVER_KEY"), f"'{password}'")
        self.assertEqual(
            parsed_values,
            ["127.0.0.1", expected_port, "true", password],
        )
        self.assertFalse(any(key.startswith("export API_SERVER_") for key in env))
        self.assertEqual(env.get("GATEWAY_MULTIPLEX_PROFILES"), "false")
        self.assertNotIn("HERMES_S6_SUPERVISED_CHILD", env)
        self.assertNotIn("HERMES_HOME", env)
        self.assertNotIn("HERMES_GATEWAY_NO_SUPERVISE", env)
        for name in reserved:
            self.assertIn(f"skipping top-level env '{name}'", stderr)
        return stderr, reserved

    def test_all_api_variables_are_reserved_from_top_level_overrides(self):
        self._assert_addon_owned_values(0)

    def test_all_api_variables_are_reserved_from_per_profile_overrides(self):
        stderr, reserved = self._assert_addon_owned_values(1)
        for name in reserved:
            self.assertIn(f"skipping per-profile env '{name}'", stderr)


class PublicationMetadataTests(unittest.TestCase):
    def test_addon_version_is_1_3_2(self):
        config = CONFIG.read_text()
        match = re.search(r'^version:\s*["\']?([^"\'\s]+)', config, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1) if match else None, "1.3.2")

    def test_translation_describes_api_password_policy(self):
        translation = TRANSLATION.read_text().lower()
        enable_api = translation.split("  enable_api:", 1)[1].split(
            "  enable_desktop_backend:", 1
        )[0]
        access_password = translation.split("  access_password:", 1)[1].split(
            "  env_vars:", 1
        )[0]
        for option, text in (
            ("enable_api", enable_api),
            ("access_password", access_password),
        ):
            with self.subTest(option=option):
                self.assertIn("16", text)
                self.assertIn("placeholder", text)
                self.assertIn("surrounding whitespace is ignored", text)
                self.assertIn("line breaks are rejected", text)
                self.assertIn("printable ascii", text)
                self.assertIn("single-quote", text)
                self.assertIn("backslash", text)
                self.assertIn("interpolation", text)

    def test_readme_describes_api_password_policy(self):
        readme = README.read_text().lower()
        api_section = readme.split("### openai-compatible api", 1)[1].split(
            "### ports", 1
        )[0]
        self.assertIn("at least 16 printable ascii characters", api_section)
        self.assertIn("placeholder", api_section)
        self.assertIn("surrounding whitespace is ignored", api_section)
        self.assertIn("line breaks are rejected", api_section)
        self.assertIn("single-quote", api_section)
        self.assertIn("backslash", api_section)
        self.assertIn("interpolation", api_section)

    def test_readme_distinguishes_api_health_from_nginx_liveness(self):
        readme = README.read_text()
        api_section = readme.split("### OpenAI-compatible API", 1)[1].split(
            "### Ports", 1
        )[0]
        self.assertIn("`https://homeassistant.local:8443/v1/health`", api_section)
        self.assertIn("Public Hermes API liveness check", api_section)
        self.assertIn(
            "`/health` is nginx's unauthenticated root liveness response",
            api_section,
        )
        self.assertIn("`/v1/health` is also unauthenticated", api_section)
        self.assertIn("`/v1/models`", api_section)
        self.assertNotIn("Authenticated Hermes API health check", api_section)

    def test_security_model_names_the_public_health_exception(self):
        security_model = README.read_text().split("## Security Model", 1)[1]
        self.assertIn("except `/v1/health`", security_model)
        self.assertIn("standard Authorization header", security_model)
        self.assertIn("`/v1/health` is public liveness and sends no credential", security_model)
        self.assertIn("use `/v1/models` to verify authentication", security_model)
        self.assertIn("Gateway configuration authority", security_model)
        self.assertIn("after every load", security_model)
        self.assertIn("final gateway-config boundary", security_model)
        self.assertIn("disables Hermes profile multiplexing", security_model)
        self.assertIn("sticky interactive `active_profile`", security_model)
        self.assertIn("independently of the installed Hermes revision", security_model)
        api_line = next(
            line
            for line in security_model.splitlines()
            if line.startswith("- **OpenAI-compatible API**")
        )
        self.assertTrue(api_line.endswith("."))

    def test_release_changelog_records_actual_verification(self):
        changelog = CHANGELOG.read_text()
        unreleased = changelog.split("## [Unreleased]", 1)[1].split(
            "\n## [", 1
        )[0]
        release = changelog.split("## [1.3.2] - 2026-08-27", 1)[1].split(
            "\n## [", 1
        )[0]
        self.assertEqual(unreleased.strip(), "")
        self.assertIn("### Changed", release)
        self.assertIn("### Fixed", release)
        self.assertIn("### Verified", release)
        self.assertIn("backup size", release)
        self.assertIn("`hermes-gateway`", release)
        self.assertIn("external supervisor", release)
        self.assertIn("122 tests OK, 2 skipped", release)
        self.assertIn("older pinned Hermes revisions", release)
        self.assertIn("Home Assistant", release)
        self.assertNotIn("Pending final publication verification", release)


if __name__ == "__main__":
    unittest.main()
