"""Source selection against local Git repositories; HTTPS is always stubbed."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "hermes_agent/source-channel.py"
SPEC = importlib.util.spec_from_file_location("addon_source_channel", SOURCE)


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True).stdout.strip()


class SourceChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.remote = self.base / "remote.git"
        self.work = self.base / "work"
        git(self.base, "init", "--bare", str(self.remote))
        git(self.base, "clone", str(self.remote), str(self.work))
        git(self.work, "config", "user.email", "test@example.invalid")
        git(self.work, "config", "user.name", "Test")
        git(self.work, "checkout", "-b", "main")
        (self.work / "file.txt").write_text("initial\n")
        git(self.work, "add", "file.txt")
        git(self.work, "commit", "-m", "first")
        self.release = git(self.work, "rev-parse", "HEAD")
        git(self.work, "tag", "v1.2.3")
        git(self.work, "push", "origin", "main", "v1.2.3")
        (self.work / "next.txt").write_text("new\n")
        git(self.work, "add", "next.txt")
        git(self.work, "commit", "-m", "second")
        self.newer = git(self.work, "rev-parse", "HEAD")
        git(self.work, "push", "origin", "main")
        self.source = self.base / "source"
        git(self.base, "clone", "--branch", "v1.2.3", str(self.remote), str(self.source))
        self.primary = self.base / "primary"
        self.secondary = self.base / "secondary"
        self.primary.mkdir()
        self.secondary.mkdir()
        self.events = []
        self.module = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(self.module)

    def record(self, home, channel):
        from hashlib import sha256
        import yaml
        key = sha256(str(self.source.resolve()).encode()).hexdigest()[:16]
        (home / "config.yaml").write_text(yaml.safe_dump({"update": {"installs": {key: {"channel": channel}}}}))

    def resolve(self, channel, git_cmd, cwd, forward_only=False):
        self.events.append((channel, forward_only))
        from types import SimpleNamespace
        if channel == "main":
            return SimpleNamespace(commit=None, branch="main", ahead=False)
        return SimpleNamespace(commit=self.release, branch=None, ahead=False)

    def invoke(self, mode="update"):
        error = io.StringIO()
        output = io.StringIO()
        with patch.object(sys, "argv", ["source-channel.py", mode, str(self.source),
                                        "--url", self.module.OFFICIAL, "--home", str(self.primary)]), \
             redirect_stderr(error), redirect_stdout(output):
            code = self.module.main()
        return code, error.getvalue(), output.getvalue()

    def shallow_source(self):
        shutil.rmtree(self.source)
        git(self.base, "clone", "--depth", "1", "--branch", "v1.2.3",
            self.remote.as_uri(), str(self.source))
        self.assertNotEqual(subprocess.run(["git", "cat-file", "-e", self.newer + "^{commit}"],
                                              cwd=self.source, capture_output=True).returncode, 0)

    def test_fetch_timeout_holds_usable_checkout_at_startup_boundary(self):
        self.shallow_source()
        (self.source / "local.txt").write_text("keep me\n")
        original = self.module.git
        calls = []

        def timed_out(source, *args):
            calls.append(args)
            if args[0] == "fetch":
                raise subprocess.TimeoutExpired(["git", *args], 90)
            return original(source, *args)

        from types import SimpleNamespace
        target = SimpleNamespace(commit=self.newer, branch=None, ahead=False)
        with patch.object(self.module, "native_target", return_value=target), \
             patch.object(self.module, "git", side_effect=timed_out):
            code, error, _ = self.invoke()
        self.assertEqual(code, 0, error)
        self.assertIn("WARNING: source update held", error)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)
        self.assertEqual((self.source / "file.txt").read_text(), "initial\n")
        self.assertEqual((self.source / "local.txt").read_text(), "keep me\n")
        self.assertTrue(any(args[0] == "fetch" for args in calls))

    def test_fetch_process_failure_holds_existing_checkout(self):
        from types import SimpleNamespace
        for channel in ("stable", "main"):
            with self.subTest(channel=channel):
                self.shallow_source()
                self.record(self.primary, channel)
                (self.source / "local.txt").write_text("keep me\n")
                original = self.module.git
                target = SimpleNamespace(
                    commit=self.newer if channel == "stable" else None,
                    branch="main" if channel == "main" else None, ahead=False)

                def failed_fetch(source, *args):
                    if args[0] == "fetch":
                        raise subprocess.CalledProcessError(128, ["git", *args])
                    return original(source, *args)

                with patch.object(self.module, "native_target", return_value=target), \
                     patch.object(self.module, "git", side_effect=failed_fetch):
                    code, error, _ = self.invoke()
                self.assertEqual(code, 0, error)
                self.assertIn("WARNING: source update held", error)
                self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)
                self.assertEqual((self.source / "local.txt").read_text(), "keep me\n")

    def test_wrapped_native_git_lookup_failure_holds_checkout(self):
        def offline(*args, **kwargs):
            raise ValueError("Release lookup failed") from subprocess.CalledProcessError(
                128, ["git", "ls-remote", "--tags", "origin"])
        with patch.object(self.module, "native_target", side_effect=offline):
            code, error, _ = self.invoke()
        self.assertEqual(code, 0, error)
        self.assertIn("WARNING: source update held", error)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)

    def test_wrapped_native_local_git_failure_remains_fatal(self):
        def broken(*args, **kwargs):
            raise ValueError("Local object invalid") from subprocess.CalledProcessError(
                128, ["git", "cat-file", "-e", self.release])
        with patch.object(self.module, "native_target", side_effect=broken):
            code, error, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertNotIn("update held", error)

    def test_absent_target_fetches_only_needed_commit_and_advances(self):
        self.shallow_source()
        from types import SimpleNamespace
        target = SimpleNamespace(commit=self.newer, branch=None, ahead=False)
        original = self.module.git
        fetches = []

        def observed_git(source, *args):
            if args[0] == "fetch":
                fetches.append(args)
            return original(source, *args)

        with patch.object(self.module, "native_target", return_value=target), \
             patch.object(self.module, "git", side_effect=observed_git):
            code, error, _ = self.invoke()
        self.assertEqual(code, 0, error)
        self.assertEqual(fetches, [("fetch", "--no-tags", "origin", self.newer)])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.newer)

    def test_wrapped_native_network_failure_holds_usable_checkout(self):
        def offline(*args, **kwargs):
            raise ValueError("No published stable release could be verified") from urllib.error.URLError("offline")

        with patch.object(self.module, "native_target", side_effect=offline):
            code, error, _ = self.invoke()
        self.assertEqual(code, 0, error)
        self.assertIn("WARNING: source update held", error)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)

    def test_native_local_git_timeout_remains_fatal(self):
        for command in (["git", "rev-parse", "HEAD"],
                        ["git", "merge-base", "--is-ancestor", self.release, self.newer]):
            with self.subTest(command=command):
                with patch.object(self.module, "native_target", side_effect=
                                  subprocess.TimeoutExpired(command, 10)):
                    code, error, _ = self.invoke()
                self.assertEqual(code, 1)
                self.assertNotIn("update held", error)
                self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)

    def test_nontransport_native_failure_still_fails_startup(self):
        def mismatch(*args, **kwargs):
            raise ValueError("No published stable release could be verified") from ValueError("tag SHA mismatch")
        with patch.object(self.module, "native_target", side_effect=mismatch):
            code, error, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("FATAL", error)
        self.assertNotIn("update held", error)

    def test_malformed_saved_channel_remains_fatal(self):
        self.record(self.primary, "stable!")
        code, error, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("Invalid saved update channel", error)
        self.assertNotIn("update held", error)

    def test_explicit_main_fetch_timeout_holds_unchanged_checkout(self):
        self.record(self.primary, "main")
        original = self.module.git

        def timed_out(source, *args):
            if args[0] == "fetch":
                raise subprocess.TimeoutExpired(["git", *args], 90)
            return original(source, *args)

        with patch.object(self.module, "native_target", side_effect=self.resolve), \
             patch.object(self.module, "git", side_effect=timed_out):
            code, error, _ = self.invoke()
        self.assertEqual(code, 0, error)
        self.assertIn("WARNING: source update held", error)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)

    def test_auto_update_off_skips_source_selector_at_installer_gate(self):
        import shlex
        from tests.test_python_runtime import installation_functions

        fixture = self.base / "offline-boot"
        fixture.mkdir()
        source = fixture / "source"
        git(self.base, "clone", "--branch", "v1.2.3", str(self.remote), str(source))
        (source / "node_modules/agent-browser").mkdir(parents=True)
        script = "\n".join((
            "set -euo pipefail",
            f"SRC_DIR={shlex.quote(str(source))}",
            f"VENV_DIR={shlex.quote(str(source / 'venv'))}",
            f"MARKER_FILE={shlex.quote(str(fixture / 'marker'))}",
            f"GIT_URL={shlex.quote(self.module.OFFICIAL)}",
            "GIT_REF=''", "GIT_TOKEN=''", "AUTO_UPDATE=false",
            f"PROFILE_HOMES=({shlex.quote(str(self.primary))})",
            installation_functions(),
            "required_python_version() { printf '3.11\\n'; }",
            "hermes_runtime_works() { return 0; }",
            "install_needed() { return 1; }",
            "install_hermes_core", "printf 'STARTUP_CONTINUED\\n'",
        )) + "\n"
        result = subprocess.run(["/bin/bash", "-c", script], cwd=fixture,
                                text=True, capture_output=True, timeout=20,
                                env=os.environ | {"HOME": str(self.base),
                                                  "HERMES_HOME": str(self.primary),
                                                  "TMPDIR": str(self.base),
                                                  "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STARTUP_CONTINUED", result.stdout)
        self.assertEqual(git(source, "rev-parse", "HEAD"), self.release)

    def test_legacy_tag_mismatch_still_fails_startup(self):
        with patch.object(self.module, "native_target", side_effect=ImportError("old checkout")), \
             patch.object(self.module, "published_release", side_effect=ValueError("tag SHA mismatch")):
            code, error, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("tag SHA mismatch", error)

    def test_no_usable_checkout_cannot_hold_network_failure(self):
        git(self.source, "checkout", "--orphan", "empty")
        git(self.source, "rm", "-rf", ".")
        def offline(*args, **kwargs):
            raise ValueError("offline") from urllib.error.URLError("offline")
        with patch.object(self.module, "native_target", side_effect=offline):
            code, error, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertNotIn("update held", error)

    def test_first_clone_network_failure_is_fatal(self):
        with patch.object(self.module, "published_release", side_effect=urllib.error.URLError("offline")):
            code, error, _ = self.invoke("clone")
        self.assertEqual(code, 1)
        self.assertIn("FATAL", error)

    def test_local_target_needs_no_fetch_and_updates_real_git(self):
        git(self.source, "fetch", "origin", "main")
        from types import SimpleNamespace
        target = SimpleNamespace(commit=self.newer, branch=None, ahead=False)
        original = self.module.git

        def no_fetch(source, *args):
            if args[0] == "fetch":
                self.fail("already verified local commit must not fetch")
            return original(source, *args)

        with patch.object(self.module, "native_target", return_value=target), \
             patch.object(self.module, "git", side_effect=no_fetch):
            code, error, _ = self.invoke()
        self.assertEqual(code, 0, error)
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.newer)

    def test_unknown_merge_timeout_remains_fatal(self):
        git(self.source, "fetch", "origin", "main")
        from types import SimpleNamespace
        target = SimpleNamespace(commit=self.newer, branch=None, ahead=False)
        original = self.module.git

        def merge_timeout(source, *args):
            if args[0] == "merge":
                raise subprocess.TimeoutExpired(["git", *args], 90)
            return original(source, *args)

        with patch.object(self.module, "native_target", return_value=target), \
             patch.object(self.module, "git", side_effect=merge_timeout):
            code, error, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertNotIn("update held", error)

    def test_implicit_stable_updates_once_and_keeps_local_change(self):
        (self.source / "local.txt").write_text("user change\n")
        with patch.object(self.module, "native_target", side_effect=self.resolve):
            self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)
        self.assertEqual((self.source / "local.txt").read_text(), "user change\n")
        self.assertEqual(self.events, [("stable", True)])

    def test_implicit_stable_never_downgrades_newer_checkout(self):
        git(self.source, "fetch", "origin", "main")
        git(self.source, "merge", "--ff-only", "FETCH_HEAD")
        with patch.object(self.module, "native_target", side_effect=self.resolve):
            self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.newer)
        self.assertEqual(self.events, [("stable", True)])

    def test_secondary_choice_is_honored_without_writing_primary(self):
        self.record(self.secondary, "main")
        with patch.object(self.module, "native_target", side_effect=self.resolve):
            self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.newer)
        self.assertEqual(self.events, [("main", False)])
        self.assertFalse((self.primary / "config.yaml").exists())

    def test_contradictory_choices_stop_before_fetch(self):
        self.record(self.primary, "stable")
        self.record(self.secondary, "main")
        before = git(self.source, "rev-parse", "HEAD")
        with patch.object(self.module, "native_target", side_effect=self.resolve):
            with self.assertRaisesRegex(ValueError, "Conflicting.*primary.*secondary"):
                self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), before)
        self.assertEqual(self.events, [])

    def test_missing_native_api_bootstraps_verified_stable_forward(self):
        with patch.object(self.module, "native_target", side_effect=ImportError("old checkout")), \
             patch.object(self.module, "published_release", return_value=("v1.2.4", self.newer)):
            self.module.update(self.source, [self.primary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.newer)

    def test_legacy_secondary_main_choice_is_preserved(self):
        self.record(self.secondary, "main")
        with patch.object(self.module, "native_target", side_effect=ImportError("old checkout")):
            self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.newer)
        self.assertFalse((self.primary / "config.yaml").exists())

    def test_legacy_canary_is_held_without_dropping_startup(self):
        self.record(self.secondary, "canary")
        with patch.object(self.module, "native_target", side_effect=ImportError("old checkout")):
            self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)

    def test_explicit_stable_may_select_older_release(self):
        git(self.source, "fetch", "origin", "main")
        git(self.source, "merge", "--ff-only", "FETCH_HEAD")
        self.record(self.secondary, "stable")
        with patch.object(self.module, "native_target", side_effect=self.resolve):
            self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)
        self.assertEqual(self.events, [("stable", False)])

    def test_native_canary_choice_is_used_and_failure_does_not_pull(self):
        self.record(self.secondary, "canary")
        with patch.object(self.module, "native_target", side_effect=ValueError("unpublished")):
            with self.assertRaisesRegex(ValueError, "unpublished"):
                self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)

    def test_new_release_refusing_to_overwrite_local_change(self):
        self.record(self.secondary, "main")
        (self.source / "next.txt").write_text("my local file\n")
        with patch.object(self.module, "native_target", side_effect=self.resolve):
            with self.assertRaises(subprocess.CalledProcessError):
                self.module.update(self.source, [self.primary, self.secondary])
        self.assertEqual(git(self.source, "rev-parse", "HEAD"), self.release)
        self.assertEqual((self.source / "next.txt").read_text(), "my local file\n")

    def test_published_release_rejects_prerelease_metadata(self):
        import io
        with patch.object(self.module.urllib.request, "urlopen", return_value=io.BytesIO(
                json.dumps({"tag_name": "v1.2.3", "draft": False, "prerelease": True}).encode())):
            with self.assertRaisesRegex(ValueError, "published"):
                self.module.published_release()

    def test_clone_commit_mismatch_leaves_no_unverified_checkout(self):
        import shlex
        from tests.test_python_runtime import installation_functions
        fixture = self.base / "boot"
        fixture.mkdir()
        selector = fixture / "source-channel.py"
        selector.write_text("print('v1.2.3\\t' + '0'*40)\n")
        script = fixture / "install.sh"
        script.write_text("\n".join((
            "set -euo pipefail",
            f"SRC_DIR={shlex.quote(str(fixture / 'source'))}",
            f"VENV_DIR={shlex.quote(str(fixture / 'source/venv'))}",
            f"MARKER_FILE={shlex.quote(str(fixture / 'marker'))}",
            f"GIT_URL={shlex.quote(str(self.remote))}",
            "GIT_REF=''", "GIT_TOKEN=''", "AUTO_UPDATE=false",
            f"PROFILE_HOMES=({shlex.quote(str(self.primary))})",
            installation_functions(),
            "install_hermes_core", "printf 'UNEXPECTED_SUCCESS\\n'",
        )) + "\n")
        result = subprocess.run(["/bin/bash", str(script)], cwd=fixture,
                                text=True, capture_output=True, timeout=20,
                                env=os.environ | {"HOME": str(self.base),
                                                  "TMPDIR": str(self.base),
                                                  "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cloned release differs", result.stderr)
        self.assertFalse((fixture / "source").exists(), "unverified clone must not become the next startup's source")

    def test_published_first_clone_is_verified_and_lookup_failure_fails_closed(self):
        with patch.object(self.module, "published_release", return_value=("v1.2.3", self.release)):
            self.assertEqual(self.module.clone_ref(self.module.OFFICIAL, "", self.source, [self.primary]), ("v1.2.3", self.release))
        with patch.object(self.module, "published_release", side_effect=ValueError("offline")):
            with self.assertRaisesRegex(ValueError, "offline"):
                self.module.clone_ref(self.module.OFFICIAL, "", self.source, [self.primary])
        self.assertEqual(self.module.clone_ref(self.module.OFFICIAL, "feature", self.source, [self.primary]), ("feature", ""))
        self.assertEqual(self.module.clone_ref("https://example.invalid/fork", "", self.source, [self.primary]), ("", ""))


if __name__ == "__main__":
    unittest.main()
