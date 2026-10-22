"""Exercise the real installer shell with offline Python/uv fixtures."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASH = "/bin/bash" if sys.platform == "darwin" else "bash"


def installation_functions():
    script = (ROOT / "hermes_agent/run.sh").read_text()
    return script[script.index("compute_marker() {"):script.index("\ninstall_hermes_core\n")]


class PythonRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.venv = self.source / "venv"
        self.marker = self.root / "install-marker"
        self.events = self.root / "events.jsonl"
        # HOME and HERMES_HOME are distinct, mirroring the container layout
        # (/config is the shell home; /config/.hermes is the add-on-owned
        # primary profile home, PROFILE_HOMES[0]). Keeping them apart surfaces
        # any _HERMES_HOME_/native-home resolution regressions in the probe.
        self.shell_home = self.root / "config"
        self.home = self.shell_home / ".hermes"
        self.shell_home.mkdir(parents=True)
        self.home.mkdir()
        (self.home / "config.yaml").write_text("keep user config\n")
        (self.source / ".git").mkdir(parents=True)
        (self.source / "node_modules/agent-browser").mkdir(parents=True)
        for name in ("mini-swe-agent", "tinker-atropos"):
            (self.source / name).mkdir()
            (self.source / name / "pyproject.toml").write_text("# retained source\n")
        package = self.source / "hermes_cli"
        package.mkdir()
        (package / "__init__.py").write_text("")
        # These are real imports. Config fails if the installer probes before
        # installation; its config import writes a canary into the probe's home
        # (now the persistent install home, not a scratch dir).
        # This main emulates upstream Hermes's sticky-profile re-homing: at
        # import it checks for an active_profile and, if found, re-binds to
        # that profile's home. The probe masks active_profile (Path.is_file
        # returns False), so a healthy primary venv is kept; unmasked, the
        # redirect lands on a profile with no healthy runtime and fails.
        (package / "main.py").write_text(textwrap.dedent("""\
            import os
            from pathlib import Path
            sticky = Path(os.environ['HERMES_HOME']) / 'active_profile'
            if sticky.is_file():
                redirected = (Path(os.environ['HERMES_HOME']) / 'active_profile').read_text().strip()
                # Follow the sticky profile like upstream: resolve its config.
                # A redirected profile has no healthy runtime here, so the
                # probe must fail if the mask is missing.
                from config import state  # noqa: F401
                raise ImportError('redirected to profile: ' + redirected)
            from . import config
            """))
        (package / "config.py").write_text(textwrap.dedent("""\
            import json
            import os
            from pathlib import Path
            state = json.loads((Path(os.environ['TEST_VENV']) / 'state.json').read_text())
            if not state.get('healthy'):
                raise ImportError('missing runtime dependency')
            with open(os.environ['TEST_EVENTS'], 'a') as stream:
                stream.write(json.dumps(['import-config']) + '\\n')
            Path(os.environ['HERMES_HOME'], 'probe-touched').write_text('probe')
            """))
        self.fake_python = self.root / "fake-python"
        self.fake_python.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
            import json
            import os
            import sys
            from pathlib import Path
            state = json.loads((Path(__file__).parent.parent / 'state.json').read_text())
            if state.get('broken'):
                sys.exit(19)
            # Emulate the selected interpreter's version, but execute the actual
            # production -c/- code, including imports, on our installed Python.
            sys.version_info = tuple(int(v) for v in state['version'].split('.')) + (0, 0)
            args = sys.argv[1:]
            if args and args[0] == '-B':
                args.pop(0)
            if args[0] == '-c':
                code = args[1]
                sys.argv = ['-c'] + args[2:]
            elif args[0] == '-':
                code = sys.stdin.read()
                sys.argv = args
            else:
                sys.exit('unexpected Python arguments: ' + repr(args))
            sys.path.insert(0, os.getcwd())
            exec(compile(code, '<runtime-probe>', 'exec'))
            """))
        self.fake_python.chmod(0o755)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        uv = self.bin / "uv"
        uv.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
            import json
            import os
            import shutil
            import sys
            from pathlib import Path
            args = sys.argv[1:]
            with open(os.environ['TEST_EVENTS'], 'a') as stream:
                stream.write(json.dumps(args) + '\\n')
            venv = Path(os.environ['TEST_VENV'])
            failure = os.environ.get('TEST_FAILURE', '')
            if args[0] == 'venv':
                (venv / 'bin').mkdir(parents=True)
                (venv / 'partial').write_text('new environment')
                if failure == 'venv':
                    sys.exit(20)
                version = args[args.index('--python') + 1]
                state = {'version': version, 'healthy': False}
                (venv / 'state.json').write_text(json.dumps(state))
                shutil.copy(os.environ['TEST_PYTHON'], venv / 'bin/python')
                (venv / 'bin/activate').write_text('')
            elif args[:2] == ['pip', 'install']:
                target = args[args.index('-e') + 1]
                if failure == 'pip' or (failure in ('mini-swe-agent', 'tinker-atropos') and target.endswith(failure)):
                    sys.exit(21)
                state = json.loads((venv / 'state.json').read_text())
                state['healthy'] = failure != 'import'
                (venv / 'state.json').write_text(json.dumps(state))
                entrypoint = venv / 'bin/hermes'
                entrypoint.write_text('#!/bin/sh\\nexit 0\\n')
                entrypoint.chmod(0o755)
            else:
                sys.exit('unexpected uv arguments: ' + repr(args))
            """))
        uv.chmod(0o755)
        self.env = os.environ | {
            "HOME": str(self.shell_home), "HERMES_HOME": str(self.home),
            "TMPDIR": str(self.root), "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "TEST_VENV": str(self.venv), "TEST_EVENTS": str(self.events),
            "TEST_PYTHON": str(self.fake_python),
        }

    def existing_venv(self, version="3.11.2", healthy=True, broken=False):
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "bin/python").write_bytes(self.fake_python.read_bytes())
        (self.venv / "bin/python").chmod(0o755)
        (self.venv / "bin/activate").write_text("")
        (self.venv / "bin/hermes").write_text("#!/bin/sh\nexit 0\n")
        (self.venv / "bin/hermes").chmod(0o755)
        (self.venv / "state.json").write_text(json.dumps({
            "version": version, "healthy": healthy, "broken": broken,
        }))
        (self.venv / "old-canary").write_text("retain old environment\n")
        self.marker.write_text("same-marker\n")

    def run_installer(self, pin: str | None = "3.14\n", failure="", marker="same-marker"):
        if pin is not None:
            (self.source / ".python-version").write_text(pin)
        script = textwrap.dedent(f"""\
            set -euo pipefail
            SRC_DIR={shlex.quote(str(self.source))}
            VENV_DIR={shlex.quote(str(self.venv))}
            MARKER_FILE={shlex.quote(str(self.marker))}
            GIT_URL=https://example.invalid/hermes.git
            GIT_REF=test-ref
            GIT_TOKEN=''
            AUTO_UPDATE=false
            PROFILE_HOMES=({shlex.quote(str(self.home))})
            {installation_functions()}
            compute_marker() {{ printf '%s\\n' {shlex.quote(marker)}; }}
            install_hermes_core
            printf 'STARTUP_CONTINUED\\n'
            """)
        return subprocess.run(
            [BASH, "-c", script], cwd=self.root,
            env=self.env | {"TEST_FAILURE": failure},
            text=True, capture_output=True, timeout=20, check=False,
        )

    def calls(self):
        return [json.loads(line) for line in self.events.read_text().splitlines()] if self.events.exists() else []

    def assert_retained_data(self):
        self.assertEqual((self.home / "config.yaml").read_text(), "keep user config\n")
        self.assertFalse(list(self.source.rglob("__pycache__")))
        self.assertEqual((self.source / "mini-swe-agent/pyproject.toml").read_text(), "# retained source\n")

    def test_checkout_pin_selects_python_and_imports_before_marker(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        creation = next(call for call in self.calls() if call[0] == "venv")
        self.assertEqual(creation[creation.index("--python") + 1], "3.14")
        self.assertIn(["import-config"], self.calls())
        self.assertEqual(self.marker.read_text(), "same-marker\n")
        self.assert_retained_data()

    def test_missing_pin_preserves_legacy_python_3_11(self):
        result = self.run_installer(pin=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        creation = next(call for call in self.calls() if call[0] == "venv")
        self.assertEqual(creation[creation.index("--python") + 1], "3.11")

    def test_matching_marker_cannot_hide_wrong_python(self):
        self.existing_venv()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call[0] == "venv" for call in self.calls()))
        self.assertTrue(any(call[:2] == ["pip", "install"] for call in self.calls()))
        self.assertFalse((self.venv / "old-canary").exists())

    def test_patch_pin_requires_matching_patch(self):
        self.existing_venv(version="3.14.0")
        result = self.run_installer(pin="3.14.1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        creation = next((call for call in self.calls() if call[0] == "venv"), [])
        self.assertIn("3.14.1", creation)

    def test_sticky_active_profile_does_not_redirect_the_probe(self):
        self.existing_venv(version="3.14.2")
        # A leftover sticky selection (interactive `hermes profile use`) must
        # not re-home the add-on-managed probe: the probe masks active_profile,
        # so it still validates the persistent primary home and keeps the
        # healthy venv instead of being redirected to a broken profile.
        (self.home / "active_profile").write_text("coder-home\n")
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[0] in ("venv", "pip") for call in self.calls()))
        self.assertTrue((self.venv / "old-canary").exists())
        self.assertTrue((self.home / "probe-touched").exists())
        self.assert_retained_data()

    def test_matching_healthy_venv_is_kept(self):
        self.existing_venv(version="3.14.2")
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[0] in ("venv", "pip") for call in self.calls()))
        self.assertTrue((self.venv / "old-canary").exists())
        # The import probe now runs against the persistent install home (not an
        # empty scratch dir it would have to scaffold a ~400MB runtime into), so
        # its config canary lands in the real home and the healthy venv (and its
        # installed runtime) is reused instead of rebuilt on every startup.
        self.assertTrue((self.home / "probe-touched").exists())
        self.assert_retained_data()

    def test_changed_marker_updates_healthy_venv_without_discarding_user_packages(self):
        self.existing_venv(version="3.14.2")
        result = self.run_installer(marker="new-marker")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(call[0] == "venv" for call in self.calls()))
        self.assertTrue(any(call[:2] == ["pip", "install"] for call in self.calls()))
        self.assertTrue((self.venv / "old-canary").exists())
        self.assertEqual(self.marker.read_text(), "new-marker\n")
        self.assert_retained_data()

    def test_broken_interpreter_rebuilds_despite_matching_marker(self):
        self.existing_venv(version="3.14.2", broken=True)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call[0] == "venv" for call in self.calls()))

    def test_broken_import_rebuilds_despite_matching_marker(self):
        self.existing_venv(version="3.14.2", healthy=False)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(call[0] == "venv" for call in self.calls()))
        self.assertIn(["import-config"], self.calls())

    def test_invalid_pins_fail_closed_without_touching_venv(self):
        self.existing_venv()
        for pin in ("", "--system", "/usr/bin/python3", "pypy3.14", "3.14\n3.11", "3.14;true", "3", "4.1", " 3.14"):
            with self.subTest(pin=pin):
                result = self.run_installer(pin=pin)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FATAL", result.stderr + result.stdout)
                self.assertNotIn("STARTUP_CONTINUED", result.stdout)
                self.assertEqual(self.calls(), [])
                self.assertTrue((self.venv / "old-canary").exists())
                self.assertEqual(self.marker.read_text(), "same-marker\n")

    def test_failed_rebuild_restores_existing_venv_and_marker(self):
        self.existing_venv()
        original = (self.venv / "state.json").read_bytes()
        for failure in ("venv", "pip", "mini-swe-agent", "tinker-atropos", "import"):
            with self.subTest(failure=failure):
                result = self.run_installer(failure=failure, marker="new-marker")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FATAL", result.stderr + result.stdout)
                self.assertNotIn("STARTUP_CONTINUED", result.stdout)
                self.assertEqual(self.marker.read_text(), "same-marker\n")
                self.assertEqual((self.venv / "state.json").read_bytes(), original)
                self.assertTrue((self.venv / "old-canary").exists())
                self.assertFalse((self.venv / "partial").exists())
                self.assert_retained_data()

    def test_successful_pip_but_failed_import_never_writes_marker(self):
        result = self.run_installer(failure="import")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertNotIn("STARTUP_CONTINUED", result.stdout)


if __name__ == "__main__":
    unittest.main()
