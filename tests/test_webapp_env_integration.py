"""Regression: a profile .env must not take over the add-on's Webapp sign-in.

The maintainer asked for exactly this: reassert the option after the environment
layers, before the auth providers are discovered, and prove it with a conflicting
profile `.env`. This test uses the real Hermes env loader, the real
`plugins.dashboard_auth.basic` settings and provider, and the add-on's own state
helper — no stubs. It needs a Hermes checkout (``HERMES_CHECKOUT``) and the Hermes
interpreter (the provider needs httpx), so it skips in a plain add-on checkout.
"""

from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "hermes_agent" / "gateway-launcher.py"
HELPER = ROOT / "hermes_agent" / "webapp-auth-state.py"
CHECKOUT = os.environ.get("HERMES_CHECKOUT", "")
PLUGIN = Path(CHECKOUT) / "plugins" / "dashboard_auth" / "basic" / "__init__.py" if CHECKOUT else None

PASSWORD = "richtiges passwort"
HIJACK = "aus-der-datei"
AUTH_KEYS = (
    "HERMES_DASHBOARD_BASIC_AUTH_USERNAME",
    "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH",
    "HERMES_DASHBOARD_BASIC_AUTH_SECRET",
    "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD",
)
HANDOFF = {
    "HERMES_ADDON_API_HOST": "127.0.0.1",
    "HERMES_ADDON_API_PORT": "8642",
    "HERMES_ADDON_API_ENABLED": "false",
    "HERMES_ADDON_API_KEY": "",
    "HERMES_ADDON_PROFILE_HOME": "",
    "HERMES_ADDON_MULTIPLEX_PROFILES": "false",
    "HERMES_ADDON_GATEWAY_NO_SUPERVISE": "1",
    "HERMES_ADDON_SUPERVISED_CHILD": "1",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(PLUGIN is not None and PLUGIN.is_file(),
                     "set HERMES_CHECKOUT to a Hermes checkout to run the env integration")
class ProfileEnvIntegrationTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, CHECKOUT)
        from plugins.dashboard_auth import basic
        from hermes_cli import env_loader

        self.basic = basic
        self.env_loader = env_loader
        # _guard_env_loader patches the module-level loader; a leaked patch would
        # make the control test below pass for the wrong reason.
        self.original_loader = env_loader.load_hermes_dotenv
        self.addCleanup(setattr, env_loader, "load_hermes_dotenv", self.original_loader)
        self.launcher = load_module(LAUNCHER, "ha_launcher_env_integration")
        self.helper = load_module(HELPER, "ha_helper_env_integration")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.state = self.helper.auth_state(self.home, PASSWORD, basic, log=lambda text: None)
        (self.home / ".env").write_text(
            f"HERMES_DASHBOARD_BASIC_AUTH_PASSWORD={HIJACK}\n"
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH=scrypt$1$1$1$YWJj$YWJj\n"
            "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=einbrecher\n",
            encoding="utf-8",
        )

        self.add_on = {
            "HERMES_DASHBOARD_BASIC_AUTH_USERNAME": "hermes",
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH": self.state["password_hash"],
            "HERMES_DASHBOARD_BASIC_AUTH_SECRET": self.state["secret"],
        }
        self.saved = {key: os.environ.get(key) for key in (*AUTH_KEYS, *HANDOFF, "HERMES_HOME")}
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _load_profile_env(self):
        """Load the profile layers the way Hermes does at startup."""
        self.env_loader.load_hermes_dotenv(
            hermes_home=self.home, project_env=self.home / ".env.missing")

    def _prepare(self):
        for key in AUTH_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self.add_on)
        os.environ.update({key: (str(self.home) if key == "HERMES_ADDON_PROFILE_HOME" else value)
                           for key, value in HANDOFF.items()})
        os.environ["HERMES_HOME"] = str(self.home)
        protected = self.launcher._capture_protected_values()
        self.launcher._capture_auth_values(protected)
        return protected

    def test_the_profile_env_really_reaches_the_sign_in_settings(self):
        # Control: without the guard the .env supplies the credential. If this
        # ever stops being true, the guard test below would prove nothing.
        for key in AUTH_KEYS:
            os.environ.pop(key, None)
        os.environ["HERMES_HOME"] = str(self.home)

        self._load_profile_env()

        self.assertEqual(os.environ.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"), HIJACK)
        self.assertEqual(os.environ.get("HERMES_DASHBOARD_BASIC_AUTH_USERNAME"), "einbrecher")

    def test_guard_keeps_the_option_and_drops_the_env_password(self):
        protected = self._prepare()

        self.launcher._guard_env_loader(self.env_loader, protected)
        self._load_profile_env()  # a later layer load, as at runtime

        self.assertIsNone(os.environ.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD"))
        self.assertEqual(os.environ["HERMES_DASHBOARD_BASIC_AUTH_USERNAME"], "hermes")
        self.assertEqual(os.environ["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH"],
                         self.state["password_hash"])
        self.assertEqual(os.environ["HERMES_DASHBOARD_BASIC_AUTH_SECRET"], self.state["secret"])

    def test_provider_accepts_only_the_configured_password_after_the_guard(self):
        protected = self._prepare()
        self.launcher._guard_env_loader(self.env_loader, protected)
        self._load_profile_env()

        settings = self.basic._settings()
        self.assertEqual(settings["credential_id"], "")
        provider = self.basic.BasicAuthProvider(
            username=settings["username"],
            password_hash=settings["password_hash"],
            secret=settings["secret"],
            credential_id=settings["credential_id"],
        )
        session = provider.complete_password_login(username="hermes", password=PASSWORD)
        self.assertIsNotNone(provider.verify_session(access_token=session.access_token))
        with self.assertRaises(self.basic.InvalidCredentialsError):
            provider.complete_password_login(username="hermes", password=HIJACK)

    def test_secret_stays_a_decodable_32_byte_key(self):
        # _resolve_secret decodes base64 and keeps it only when it yields 16+ bytes;
        # a value that decodes to something else would silently become a text key.
        self.assertEqual(len(base64.b64decode(self.state["secret"])), 32)
        self.assertEqual(len(self.basic._resolve_secret({"secret": self.state["secret"]})), 32)

    def test_the_stale_marker_used_by_the_build_exists(self):
        # webapp.sh calls exactly these two names after a failed build; if either
        # disappeared, the only symptom would be a warning in webapp-build.log.
        from hermes_cli.config import get_project_root
        from hermes_cli.webapp import mark_webapp_stale

        self.assertTrue(callable(get_project_root))
        self.assertTrue(callable(mark_webapp_stale))


if __name__ == "__main__":
    unittest.main()
