#!/usr/bin/env python3
"""Start the official Hermes Desktop backend with add-on-owned Basic auth."""

from __future__ import annotations

from importlib import import_module
import os
import sys


_BASIC_AUTH_ENV_KEYS = (
    "HERMES_DASHBOARD_BASIC_AUTH_USERNAME",
    "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD",
    "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH",
    "HERMES_DASHBOARD_BASIC_AUTH_SECRET",
    "HERMES_DASHBOARD_BASIC_AUTH_TTL_SECONDS",
)


def _read_password() -> str:
    password = sys.stdin.read()
    # run.sh supplies the option through a Bash here-string, which appends one
    # newline. Remove only that transport newline so spaces remain untouched.
    if password.endswith("\n"):
        password = password[:-1]
    if not password:
        raise SystemExit(
            "[desktop-backend] FATAL: no access password received on stdin"
        )
    return password


def main() -> int:
    serve_args = sys.argv[1:]
    # Pin the official machine-level backend before Hermes' import-time profile
    # selection. This avoids a later named-profile re-exec that would bypass
    # this launcher and reload colliding machine-root environment layers.
    #
    # argv[0] must be a real file path, not the bare string "hermes": on a PM
    # (package-manager) dependency desync, Hermes self-relaunches into the
    # managed "store" interpreter via runpy.run_path(Path(sys.argv[0])),
    # which resolves a bare word against the process cwd and crashes with
    # FileNotFoundError. The prefix insert must also be idempotent: the
    # self-relaunch re-runs this exact script from the top with the
    # already-rewritten argv, so a naive unconditional prepend would
    # double-insert "-p default serve" on the second pass.
    if serve_args[:3] != ["-p", "default", "serve"]:
        serve_args = ["-p", "default", "serve", *serve_args]
    sys.argv = [os.path.abspath(__file__), *serve_args]

    # Import first: Hermes resolves the machine HERMES_HOME and loads profile,
    # project, external-secret and managed environment layers at module import
    # time. The explicit default profile is stripped from sys.argv during this
    # import, leaving the normal `hermes serve ...` dispatch contract.
    from hermes_cli import main as hermes_main

    # Read stdin only after the import above: a PM self-relaunch replaces this
    # process via os.execv, which preserves file descriptors including stdin.
    # Reading the password before the relaunch would consume it in the
    # process that gets replaced, leaving the relaunched process to hit EOF.
    password = _read_password()

    hash_password = import_module(
        "plugins.dashboard_auth.basic"
    ).hash_password

    try:
        password_hash = hash_password(password)
    except Exception as exc:
        raise SystemExit(
            "[desktop-backend] FATAL: could not hash the access password "
            f"({type(exc).__name__})"
        ) from exc

    # The add-on option is authoritative for this opt-in listener. Remove any
    # colliding Hermes-loaded auth settings, then configure the normal Basic
    # auth plugin with an exact hash before hermes_cli.main() performs plugin
    # discovery. Plaintext never enters the post-import process environment.
    for key in _BASIC_AUTH_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["HERMES_DASHBOARD_BASIC_AUTH_USERNAME"] = "hermes"
    os.environ["HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH"] = password_hash
    del password, password_hash

    result = hermes_main.main()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
