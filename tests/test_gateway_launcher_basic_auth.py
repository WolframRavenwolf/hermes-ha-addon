"""The add-on's Webapp sign-in settings must survive Hermes' environment layers.

`plugins.dashboard_auth.basic._settings()` prefers an env plaintext password over
the configured hash, and the in-memory hash it builds from it is salted afresh —
which ends every session on a restart. A profile `.env` must therefore not be
able to override the add-on option, exactly like the API handoff values.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "hermes_agent" / "gateway-launcher.py"

AUTH_PASSWORD = "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"
AUTH_HASH = "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH"
AUTH_SECRET = "HERMES_DASHBOARD_BASIC_AUTH_SECRET"
AUTH_USER = "HERMES_DASHBOARD_BASIC_AUTH_USERNAME"

API_PROTECTED = {
    "API_SERVER_ENABLED": "false",
    "API_SERVER_HOST": "127.0.0.1",
    "API_SERVER_PORT": "8642",
    "API_SERVER_KEY": "key",
}


def load_launcher():
    spec = importlib.util.spec_from_file_location("ha_gateway_launcher_auth", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BasicAuthEnvTests(unittest.TestCase):
    def setUp(self):
        self.launcher = load_launcher()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        package = self.home / "pkg"
        package.mkdir()
        self.env_loader_file = package / "env_loader.py"
        self.env_loader_file.write_text("", encoding="utf-8")

        self.missing = object()
        # The guard writes the API handoff values into os.environ as well; a leaked
        # value would follow into every later test of this process.
        self.previous = {
            key: os.environ.get(key, self.missing)
            for key in (AUTH_PASSWORD, AUTH_HASH, AUTH_SECRET, AUTH_USER, *API_PROTECTED)
        }
        self.addCleanup(self._restore)
        for key in self.previous:
            os.environ.pop(key, None)

    def _restore(self):
        for key, value in self.previous.items():
            if value is self.missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _env_loader(self, layer: dict[str, str]) -> types.SimpleNamespace:
        def load_hermes_dotenv(*args, **kwargs):
            os.environ.update(layer)
            return None

        return types.SimpleNamespace(
            __file__=str(self.env_loader_file),
            load_hermes_dotenv=load_hermes_dotenv,
        )

    def _add_on_values(self):
        os.environ[AUTH_USER] = "hermes"
        os.environ[AUTH_HASH] = "scrypt$16384$8$1$c2FsdA==$ZGlnZXN0"
        os.environ[AUTH_SECRET] = "c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0LXNlY3I="

    def test_capture_removes_and_keeps_the_add_on_values(self) -> None:
        self._add_on_values()
        protected = dict(API_PROTECTED)

        self.launcher._capture_auth_values(protected)

        for key in (AUTH_USER, AUTH_HASH, AUTH_SECRET):
            self.assertNotIn(key, os.environ)
            self.assertIn(key, protected)

    def test_capture_without_webapp_values_keeps_handoff_intact(self) -> None:
        protected = dict(API_PROTECTED)

        self.launcher._capture_auth_values(protected)

        self.assertEqual(protected, API_PROTECTED)

    def test_env_layer_cannot_override_hash_or_smuggle_a_plaintext_password(self) -> None:
        self._add_on_values()
        expect_hash = os.environ[AUTH_HASH]
        expect_secret = os.environ[AUTH_SECRET]
        protected = dict(API_PROTECTED)
        self.launcher._capture_auth_values(protected)

        env_loader = self._env_loader({
            AUTH_PASSWORD: "leaked-from-profile-dotenv",
            AUTH_HASH: "scrypt$1$1$1$YXR0YWNr$YXR0YWNr",
            AUTH_USER: "intruder",
        })
        self.launcher._guard_env_loader(env_loader, protected)

        self.assertIsNone(os.environ.get(AUTH_PASSWORD))
        self.assertEqual(os.environ[AUTH_HASH], expect_hash)
        self.assertEqual(os.environ[AUTH_SECRET], expect_secret)
        self.assertEqual(os.environ[AUTH_USER], "hermes")

    def test_later_env_loads_are_guarded_too(self) -> None:
        self._add_on_values()
        expect_hash = os.environ[AUTH_HASH]
        protected = dict(API_PROTECTED)
        self.launcher._capture_auth_values(protected)
        env_loader = self._env_loader({AUTH_PASSWORD: "second-layer-password"})
        self.launcher._guard_env_loader(env_loader, protected)

        # The patched loader is what Hermes calls for profile/project layers.
        env_loader.load_hermes_dotenv()

        self.assertIsNone(os.environ.get(AUTH_PASSWORD))
        self.assertEqual(os.environ[AUTH_HASH], expect_hash)

    def test_gateway_config_boundary_reasserts_before_discovery(self) -> None:
        # A config reader inside load_gateway_config could set the env password
        # again; the boundary reassert keeps the add-on values last.
        self._add_on_values()
        expect_hash = os.environ[AUTH_HASH]
        protected = dict(API_PROTECTED)
        self.launcher._capture_auth_values(protected)

        platform = types.SimpleNamespace(API_SERVER="api_server", PlatformConfig=lambda: None)

        class FakeConfig:
            platforms: dict = {}
            multiplex_profiles = True

        def load_gateway_config(*args, **kwargs):
            os.environ[AUTH_PASSWORD] = "late-config-reader"
            os.environ[AUTH_HASH] = "scrypt$late"
            return FakeConfig()

        gateway_config = types.SimpleNamespace(
            load_gateway_config=load_gateway_config,
            Platform=platform,
            PlatformConfig=FakeConfig,
        )
        self.launcher._guard_gateway_config(gateway_config, protected)

        gateway_config.load_gateway_config()

        self.assertIsNone(os.environ.get(AUTH_PASSWORD))
        self.assertEqual(os.environ[AUTH_HASH], expect_hash)


if __name__ == "__main__":
    unittest.main()
