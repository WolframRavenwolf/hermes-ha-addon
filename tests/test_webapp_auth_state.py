"""The Webapp sign-in state must survive restarts and end with the password.

Since Hermes v0.23.2 a session is bound to `credential_id or password_hash`, so
a hash that is salted afresh on every start ends every session whenever the
add-on restarts. The helper therefore reuses the stored hash and secret while
the configured password still verifies, and replaces both when it does not.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "hermes_agent" / "webapp-auth-state.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("ha_webapp_auth_state", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBasic:
    """Stands in for plugins.dashboard_auth.basic with a salted scrypt-like hash."""

    def __init__(self, verify: bool = True) -> None:
        self.salts: list[str] = []
        self.verify_available = verify

    @staticmethod
    def _digest(salt: str, password: str) -> str:
        return hashlib.sha256((salt + password).encode()).hexdigest()

    def hash_password(self, password: str) -> str:
        salt = secrets.token_hex(8)
        self.salts.append(salt)
        # Like scrypt: the plaintext never appears in the stored hash.
        return f"scrypt$16384$8$1${salt}${self._digest(salt, password)}"

    def _verify_password(self, password: str, encoded: str) -> bool:
        parts = encoded.split("$")
        return len(parts) == 6 and parts[5] == self._digest(parts[4], password)


def fake_verify_removed(basic: FakeBasic) -> FakeBasic:
    # Stands in for a Hermes build whose provider has no verifier.
    basic._verify_password = None
    return basic


class AuthStateTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = self.home / ".ha-webapp-auth"
        self.messages: list[str] = []

    def log(self, text: str) -> None:
        self.messages.append(text)

    def state(self, password: str, basic: FakeBasic | None = None) -> dict[str, str]:
        return self.helper.auth_state(
            self.home, password, basic or FakeBasic(), log=self.log)

    def test_writes_private_state_without_the_plaintext(self) -> None:
        state = self.state("correct horse battery staple")

        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored, state)
        self.assertEqual(set(stored), {"password_hash", "secret"})
        self.assertNotIn("correct horse battery staple", self.path.read_text())
        # Standard base64 of 32 bytes: _resolve_secret decodes it to the signing key.
        import base64
        self.assertEqual(len(base64.b64decode(stored["secret"])), 32)
        self.assertFalse((self.home / ".ha-webapp-auth.new").exists())

    def test_reuses_the_hash_and_secret_while_the_password_verifies(self) -> None:
        first = self.state("first-password")
        writes = list(self.messages)
        second = self.state("first-password")

        self.assertEqual(first, second)
        self.assertEqual(self.messages, writes)

    def test_replaces_both_when_the_password_changes(self) -> None:
        first = self.state("first-password")
        second = self.state("second-password")

        self.assertNotEqual(first["password_hash"], second["password_hash"])
        self.assertNotEqual(first["secret"], second["secret"])
        text = self.path.read_text()
        self.assertNotIn(first["password_hash"], text)
        self.assertNotIn(first["secret"], text)

    def test_retired_fingerprint_file_is_replaced_without_failing(self) -> None:
        self.path.write_text("fingerprint=deadbeef\nsecret=old-plain-secret\n")
        os.chmod(self.path, 0o644)

        state = self.state("first-password")

        self.assertTrue(state["password_hash"].startswith("scrypt$"))
        self.assertNotEqual(state["secret"], "old-plain-secret")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("fingerprint", self.path.read_text())

    def test_missing_verify_helper_rotates_visibly_instead_of_silently(self) -> None:
        first = self.state("first-password")
        basic = fake_verify_removed(FakeBasic())

        second = self.state("first-password", basic)

        self.assertNotEqual(first["password_hash"], second["password_hash"])
        self.assertTrue(any("no _verify_password" in text for text in self.messages))

    def test_unexpected_json_shape_is_rebuilt(self) -> None:
        self.path.write_text('["not", "a", "state"]')

        state = self.state("first-password")

        self.assertTrue(state["password_hash"].startswith("scrypt$"))
        self.assertIn("password_hash", json.loads(self.path.read_text()))

    def test_damaged_stored_hash_rotates_instead_of_stopping(self) -> None:
        # The file holds a hash the provider cannot parse; the fresh hash it
        # writes instead must verify normally.
        self.path.write_text(json.dumps({"password_hash": "kaputt", "secret": "x"}))
        basic = FakeBasic()
        original = basic._verify_password

        def verify(password, encoded):
            if encoded == "kaputt":
                raise ValueError("bad hash")
            return original(password, encoded)

        basic._verify_password = verify

        state = self.state("first-password", basic)

        self.assertTrue(state["password_hash"].startswith("scrypt$"))
        self.assertTrue(any("unusable" in text for text in self.messages))

    def test_fresh_hash_that_does_not_verify_is_fatal(self) -> None:
        basic = FakeBasic()
        basic._verify_password = lambda password, encoded: False

        with self.assertRaises(SystemExit):
            self.state("first-password", basic)

        self.assertFalse(self.path.exists())
        self.assertTrue(any("fresh" in text for text in self.messages))

    def test_reuse_tightens_loose_permissions(self) -> None:
        first = self.state("first-password")
        os.chmod(self.path, 0o644)

        second = self.state("first-password")

        self.assertEqual(first, second)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_helper_writes_exactly_two_lines_even_if_the_plugin_prints(self) -> None:
        # A plugin that prints on import must not shift the two lines the caller
        # parses (hash, then secret).
        checkout = self.home / "checkout"
        package = checkout / "plugins" / "dashboard_auth" / "basic"
        package.mkdir(parents=True)
        (checkout / "plugins" / "__init__.py").write_text("")
        (checkout / "plugins" / "dashboard_auth" / "__init__.py").write_text("")
        (package / "__init__.py").write_text(
            "import hashlib, secrets\n"
            "print('noise from the plugin import')\n"
            "def hash_password(password):\n"
            "    salt = secrets.token_hex(8)\n"
            "    return 'scrypt$x$' + salt + '$' + hashlib.sha256((salt + password).encode()).hexdigest()\n"
            "def _verify_password(password, encoded):\n"
            "    parts = encoded.split('$')\n"
            "    return hashlib.sha256((parts[2] + password).encode()).hexdigest() == parts[3]\n",
        )
        import subprocess
        result = subprocess.run(
            [sys.executable, str(HELPER), str(self.home)],
            input="first-password", capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(checkout)},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2, result.stdout)
        self.assertTrue(lines[0].startswith("scrypt$"))
        self.assertIn("noise from the plugin import", result.stderr)


if __name__ == "__main__":
    unittest.main()
