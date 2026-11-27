"""Contract test against the real Hermes provider: sessions must survive a restart.

This is the proof the maintainer asked for ("a token minted before a
re-initialization still verifies afterwards and is rejected after a password
change"). It needs a Hermes checkout, so it skips without ``HERMES_CHECKOUT``:
the add-on repository does not ship Hermes itself, and a stub of the provider
would only re-assert our own assumptions.
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
HELPER = ROOT / "hermes_agent" / "webapp-auth-state.py"
CHECKOUT = os.environ.get("HERMES_CHECKOUT", "")
PLUGIN = Path(CHECKOUT) / "plugins" / "dashboard_auth" / "basic" / "__init__.py" if CHECKOUT else None


def load_helper():
    spec = importlib.util.spec_from_file_location("ha_webapp_auth_state_contract", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def import_basic():
    if CHECKOUT not in sys.path:
        sys.path.insert(0, CHECKOUT)
    from plugins.dashboard_auth import basic
    return basic


@unittest.skipUnless(PLUGIN is not None and PLUGIN.is_file(),
                     "set HERMES_CHECKOUT to a Hermes checkout to run the provider contract")
class SessionContractTests(unittest.TestCase):
    def setUp(self):
        self.basic = import_basic()
        self.helper = load_helper()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def state(self, password):
        return self.helper.auth_state(self.home, password, self.basic, log=lambda text: None)

    def provider(self, state):
        return self.basic.BasicAuthProvider(
            username="hermes",
            password_hash=state["password_hash"],
            secret=base64.b64decode(state["secret"]),
        )

    def token(self, password):
        session = self.provider(self.state(password)).complete_password_login(
            username="hermes", password=password)
        value = getattr(session, "access_token", None) or getattr(session, "token", None)
        self.assertTrue(value, f"the provider returned no token on {type(session).__name__}")
        return value

    def test_token_minted_before_a_reinitialization_still_verifies(self):
        before = self.state("pw-1")
        token = self.token("pw-1")

        after = self.state("pw-1")

        # Same add-on start semantics: the state is re-read, not re-created.
        self.assertEqual(before, after)
        verified = self.provider(after).verify_session(access_token=token)
        self.assertIsNotNone(verified, "a restart ended the session (hash or secret rotated)")

    def test_token_is_rejected_after_a_password_change(self):
        before = self.state("pw-1")
        token = self.token("pw-1")
        # Positive control: without it an empty token would pass the rejection below.
        self.assertIsNotNone(self.provider(before).verify_session(access_token=token))

        changed = self.state("pw-2")

        self.assertIsNone(self.provider(changed).verify_session(access_token=token))

    def test_settings_bind_the_session_to_the_hash_when_no_env_password_exists(self):
        state = self.state("pw-1")
        environment = {
            "HERMES_DASHBOARD_BASIC_AUTH_USERNAME": "hermes",
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH": state["password_hash"],
            "HERMES_DASHBOARD_BASIC_AUTH_SECRET": state["secret"],
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            os.environ.pop("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", None)
            settings = self.basic._settings()

        # Empty credential_id = the session binds to the stable stored hash; a
        # plaintext from the environment would bind it to a per-start salt.
        self.assertEqual(settings["credential_id"], "")
        self.assertEqual(settings["password_hash"], state["password_hash"])
        self.assertEqual(len(settings["secret"]), 32)


if __name__ == "__main__":
    unittest.main()
