"""Run the installer/selector boundary with and without venv PyYAML."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest

from tests.test_python_runtime import installation_functions


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = "https://github.com/NousResearch/hermes-agent.git"


class SourceSelectorPythonTests(unittest.TestCase):
    def setUp(self):
        if importlib.util.find_spec("ruamel") is None:
            self.skipTest("requires ruamel.yaml to model the current Hermes venv")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.home = self.root / "profile"
        self.home.mkdir()
        self.events = self.root / "native.json"
        self.venv = self.source / "venv"
        (self.venv / "bin").mkdir(parents=True)
        (self.source / "node_modules/agent-browser").mkdir(parents=True)
        self.env = os.environ | {
            "HOME": str(self.root), "HERMES_HOME": str(self.home),
            "TMPDIR": str(self.root), "PYTHONDONTWRITEBYTECODE": "1",
            "TEST_NATIVE_EVENTS": str(self.events),
        }
        for args in (("init",), ("config", "user.name", "Test"),
                     ("config", "user.email", "test@example.invalid"),
                     ("commit", "--allow-empty", "-m", "fixture")):
            subprocess.run(["git", *args], cwd=self.source, env=self.env,
                           check=True, capture_output=True)
        package = self.source / "hermes_cli"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "update_channel.py").write_text(
            "def rides_default_channel(*args): return True\n")
        # Exercise real imports and selector calls; the pinned upstream seam is
        # separately probed with its actual modules, rather than this fixture.
        (package / "source_releases.py").write_text(textwrap.dedent("""\
            import json
            import os
            from pathlib import Path
            import subprocess
            from types import SimpleNamespace
            import urllib.error

            def resolve_source_target(channel, git_cmd, cwd, *, repository, forward_only):
                Path(os.environ['TEST_NATIVE_EVENTS']).write_text(json.dumps({
                    'channel': channel, 'forward_only': forward_only,
                    'repository': repository,
                }))
                failure = os.environ.get('TEST_NATIVE_FAILURE')
                if failure == 'network':
                    raise ValueError('lookup failed') from urllib.error.URLError('offline')
                if failure == 'integrity':
                    raise ValueError('tag SHA mismatch')
                head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cwd, text=True).strip()
                return SimpleNamespace(commit=head, branch=None, ahead=False)
            """))
        self.python = self.venv / "bin/python"
        self.python.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
            import importlib.abc
            import runpy
            import sys

            class MissingPyYAML(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == 'yaml' or fullname.startswith('yaml.'):
                        raise ModuleNotFoundError("No module named 'yaml'", name='yaml')

            sys.meta_path.insert(0, MissingPyYAML())
            args = sys.argv[1:]
            if args[0] == '-B':
                args.pop(0)
            sys.argv = args
            runpy.run_path(args[0], run_name='__main__')
            """))
        self.python.chmod(0o755)

    def record(self, channel, home=None):
        key = hashlib.sha256(str(self.source.resolve()).encode()).hexdigest()[:16]
        (home or self.home).joinpath("config.yaml").write_text(
            f'update:\n  installs:\n    "{key}":\n      channel: {channel}\n')

    def run_installer(self, url=OFFICIAL, ref="", failure="", homes=None):
        # The shipped Debian Python has python3-yaml. Use the test interpreter
        # as that dependency provider on non-Debian development hosts.
        functions = installation_functions().replace(
            "/usr/bin/python3", str(Path(sys.executable).absolute()))
        functions = functions.replace(
            'local selector="/usr/local/lib/hermes-source-channel.py"',
            f"local selector={shlex.quote(str(ROOT / 'hermes_agent/source-channel.py'))}")
        script = "\n".join((
            "set -euo pipefail",
            f"SRC_DIR={shlex.quote(str(self.source))}",
            f"VENV_DIR={shlex.quote(str(self.venv))}",
            f"GIT_URL={shlex.quote(url)}", f"GIT_REF={shlex.quote(ref)}",
            "GIT_TOKEN=''", "AUTO_UPDATE=true",
            "PROFILE_HOMES=(" + " ".join(shlex.quote(str(p)) for p in (homes or [self.home])) + ")",
            functions,
            "required_python_version() { printf '3.14\\n'; }",
            "hermes_runtime_works() { return 0; }",
            "install_needed() { return 1; }",
            # Legacy custom-ref/origin updates should take their existing Git
            # branch. Avoid network access and record that shell path.
            "git() { printf 'LEGACY_GIT %s\\n' \"$*\"; }",
            "install_hermes_core", "printf 'STARTUP_CONTINUED\\n'",
        ))
        return subprocess.run(["/bin/bash", "-c", script], cwd=self.root,
                              env=self.env | {"TEST_NATIVE_FAILURE": failure},
                              text=True, capture_output=True, timeout=20)

    def test_missing_venv_pyyaml_keeps_native_channel_selection(self):
        for channel in (None, "stable", "main", "pm-preview"):
            with self.subTest(channel=channel):
                if channel is not None:
                    self.record(channel)
                result = self.run_installer()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("STARTUP_CONTINUED", result.stdout)
                self.assertEqual(json.loads(self.events.read_text()), {
                    "channel": channel or "stable", "forward_only": channel is None,
                    "repository": "NousResearch/hermes-agent",
                })
                self.assertNotIn("legacy checkout", result.stderr)

    def test_yaml_and_channel_conflicts_remain_fatal(self):
        (self.home / "config.yaml").write_text("update: [unterminated\n")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL: source channel", result.stderr)
        self.assertFalse(self.events.exists())
        self.record("stable")
        other = self.root / "other-profile"
        other.mkdir()
        self.record("main", other)
        result = self.run_installer(homes=[self.home, other])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Conflicting saved update channels", result.stderr)
        self.assertFalse(self.events.exists())

    def test_transport_failure_is_held_but_integrity_failure_is_fatal(self):
        result = self.run_installer(failure="network")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: source update held", result.stderr)
        result = self.run_installer(failure="integrity")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FATAL: source channel: tag SHA mismatch", result.stderr)
        self.assertNotIn("update held", result.stderr)

    def test_custom_origin_and_explicit_ref_keep_legacy_updates(self):
        (self.home / "config.yaml").write_text("update: [unterminated\n")
        for url, ref in ((OFFICIAL, "custom-branch"), ("https://example.invalid/fork.git", "")):
            with self.subTest(url=url, ref=ref):
                result = self.run_installer(url=url, ref=ref)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("LEGACY_GIT pull --ff-only", result.stdout)
                self.assertFalse(self.events.exists())


if __name__ == "__main__":
    unittest.main()
