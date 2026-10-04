"""The gateway launcher must hide a sticky ``active_profile`` without poisoning
``hermes_constants.get_default_hermes_root``.

Replacing that helper with a ``/dev/null`` sentinel while ``hermes_cli.main``
imports leaked into every module binding it at import time (``pm.environments``
does), so ``dependency_home_root()`` resolved to ``/dev/null/installs/...`` and
the source-update dependency completion failed on every boot.
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "hermes_agent" / "gateway-launcher.py"


def load_launcher():
    spec = importlib.util.spec_from_file_location("ha_gateway_launcher", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StickyProfileTests(unittest.TestCase):
    def setUp(self):
        self.launcher = load_launcher()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "hermes-home"
        self.root.mkdir()
        self.sticky = self.root / "active_profile"
        self.sticky.write_text("coder\n", encoding="utf-8")
        (self.root / "config.yaml").write_text("keep\n", encoding="utf-8")
        self._set_env("HERMES_HOME", str(self.root))

    def _set_env(self, name, value):
        missing = object()
        previous = os.environ.get(name, missing)

        def restore():
            if previous is missing:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

        self.addCleanup(restore)
        os.environ[name] = value

    def _stub_constants(self):
        """A stand-in for hermes_constants whose helper points at our root."""

        def get_default_hermes_root(**kwargs):
            return self.root

        stub = types.ModuleType("hermes_constants")
        stub.get_default_hermes_root = get_default_hermes_root
        previous = sys.modules.get("hermes_constants")

        def restore():
            if previous is None:
                sys.modules.pop("hermes_constants", None)
            else:
                sys.modules["hermes_constants"] = previous

        self.addCleanup(restore)
        sys.modules["hermes_constants"] = stub
        return stub, get_default_hermes_root

    def test_sticky_profile_masked_without_replacing_root_helper(self):
        stub, real = self._stub_constants()
        observed = {}

        def fake_import(name):
            observed["name"] = name
            observed["root_helper"] = stub.get_default_hermes_root
            observed["sticky_exists"] = self.sticky.exists()
            observed["sticky_is_file"] = self.sticky.is_file()
            observed["benign_exists"] = (self.root / "config.yaml").exists()
            return types.SimpleNamespace(main=lambda: None)

        before_exists = Path.exists
        before_is_file = Path.is_file

        main = self.launcher._import_fixed_profile_main(import_module=fake_import)

        self.assertEqual(observed["name"], "hermes_cli.main")
        self.assertIs(observed["root_helper"], real)
        self.assertFalse(observed["sticky_exists"])
        self.assertFalse(observed["sticky_is_file"])
        self.assertTrue(observed["benign_exists"])
        self.assertIs(Path.exists, before_exists)
        self.assertIs(Path.is_file, before_is_file)
        self.assertTrue(callable(main))

    def test_module_level_binding_survives_the_import_window(self):
        """Mirrors ``pm.environments``: ``from hermes_constants import ...`` binds once."""
        stub, real = self._stub_constants()
        bound = {}

        def fake_import(name):
            # What a module-level `from hermes_constants import
            # get_default_hermes_root` captures during the import window.
            bound["helper"] = stub.get_default_hermes_root
            return types.SimpleNamespace(main=lambda: None)

        self.launcher._import_fixed_profile_main(import_module=fake_import)

        self.assertIs(bound["helper"], real)
        self.assertEqual(bound["helper"](), self.root)
        self.assertNotEqual(bound["helper"]() / "installs", Path("/dev/null/installs"))

    def test_masking_is_restored_after_a_failed_import(self):
        self._stub_constants()

        def exploding_import(name):
            raise ImportError("boom")

        before_exists = Path.exists
        before_is_file = Path.is_file
        with self.assertRaises(ImportError):
            self.launcher._import_fixed_profile_main(import_module=exploding_import)
        self.assertIs(Path.exists, before_exists)
        self.assertIs(Path.is_file, before_is_file)

    def test_candidate_paths_cover_default_root_and_hermes_home(self):
        self._stub_constants()
        paths = self.launcher._sticky_active_profile_paths()
        self.assertIn(self.root / "active_profile", paths)
        self.assertIn(Path.home() / ".hermes" / "active_profile", paths)


if __name__ == "__main__":
    unittest.main()
