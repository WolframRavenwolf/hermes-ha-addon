#!/usr/bin/env python3
"""Keep the Webapp Basic-auth state: the verified password hash and secret.

The add-on option ``access_password`` is authoritative for the Webapp listener.
Hermes binds a session to ``credential_id or password_hash``, so the hash has to
survive an add-on restart: a hash salted afresh on every start ends every
session. This keeps the verified hash together with the signing secret and
rewrites both only when the configured password no longer verifies, which is
also what ends sessions after a password change. The plaintext password never
reaches the file or the process arguments.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Callable

STATE_FILENAME = ".ha-webapp-auth"


def _log(text: str) -> None:
    print(text, file=sys.stderr)


def _read(path: Path) -> dict[str, str]:
    try:
        with path.open(encoding="utf-8") as stream:
            stored = json.load(stream)
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    password_hash = stored.get("password_hash")
    secret = stored.get("secret")
    if not (isinstance(password_hash, str) and password_hash
            and isinstance(secret, str) and secret):
        return {}
    return {"password_hash": password_hash, "secret": secret}


def _write(path: Path, state: dict[str, str]) -> None:
    temp = path.with_name(path.name + ".new")
    try:
        temp.unlink()
    except FileNotFoundError:
        pass
    # O_EXCL so a stray temp file cannot keep a wider mode; O_NOFOLLOW so the
    # path cannot be a symlink out of the profile home; fsync so a power cut
    # cannot leave an empty state file behind (which would end all sessions).
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _verifies(verify: Any, password: str, password_hash: str, log: Callable[[str], None],
              what: str) -> bool:
    """Verify without letting a damaged hash break startup."""
    try:
        return bool(verify(password, password_hash))
    except Exception as exc:  # a corrupt hash must not stop the add-on
        log(f"[webapp] WARNING: {what} sign-in hash is unusable "
            f"({exc.__class__.__name__})")
        return False


def auth_state(
    home: Path,
    password: str,
    basic: Any,
    log: Callable[[str], None] = _log,
) -> dict[str, str]:
    """Return the hash and secret to publish, reusing them while they verify."""
    path = home / STATE_FILENAME
    state = _read(path)
    if state:
        verify = getattr(basic, "_verify_password", None)
        if verify is None:
            # Never rotate silently: a new hash ends every session and would hide
            # the incompatibility instead of reporting it.
            log("[webapp] WARNING: dashboard_auth.basic offers no _verify_password; "
                "rotating the stored sign-in state instead of reusing it")
            state = {}
        elif not _verifies(verify, password, state["password_hash"], log, "stored"):
            state = {}
        else:
            # A restored file may carry wider permissions; the state is a credential.
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    if not state:
        password_hash = basic.hash_password(password)
        verify = getattr(basic, "_verify_password", None)
        if verify is not None and not _verifies(verify, password, password_hash, log, "fresh"):
            # An incompatible provider would rotate on every start and end all
            # sessions silently; refuse loudly instead.
            log("[webapp] FATAL: a freshly hashed password does not verify; "
                "the dashboard-auth provider is incompatible with this state helper")
            raise SystemExit(
                "[webapp] FATAL: a freshly hashed password does not verify; "
                "refusing to publish an unusable sign-in state")
        state = {
            "password_hash": password_hash,
            # Standard base64 of exactly 32 bytes: basic._resolve_secret decodes it
            # and keeps it as the signing key, so the encoding must stay decodable.
            "secret": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        }
        _write(path, state)
        log(f"[webapp] Wrote the Basic-auth state to {path} (0600)")
    return state


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: hermes-webapp-auth-state <hermes-home>", file=sys.stderr)
        return 2
    output = sys.stdout
    # Anything the plugin prints while importing must not shift the two output
    # lines the caller parses.
    sys.stdout = sys.stderr
    try:
        from plugins.dashboard_auth import basic
    finally:
        sys.stdout = output

    # Bytes, not text: universal-newline translation would break the promise that
    # nginx and the provider hash exactly the configured bytes.
    password = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
    state = auth_state(Path(argv[1]), password, basic)
    sys.stdout.write(state["password_hash"] + "\n" + state["secret"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
