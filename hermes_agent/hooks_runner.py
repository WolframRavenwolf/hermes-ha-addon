#!/usr/bin/env python3
"""
Hermes HA Addon Boot Hook Runner

Executes script hooks from /config/.hermes/addon-hooks/<phase>/ at
well-defined points in the addon boot lifecycle. Hooks are opt-in
(enable_boot_hooks: false by default) and run as isolated subprocesses
with timeout, symlink rejection, and structured logging.

Phases:
    post-config  — after profile scaffolding and env, before services
    ready        — after all services, tokens, and nginx reload
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time

HOOKS_ROOT = "/config/.hermes/addon-hooks"
HOOK_TIMEOUT = 30  # seconds per hook
LOG_FILE = os.path.join(
    os.environ.get("HERMES_HOME", "/config/.hermes"), "logs", "boot-hooks.log"
)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
VALID_EXTENSIONS = frozenset({".sh", ".py"})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hooks] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("boot-hooks")


def _reject_symlink(path: str) -> bool:
    """Reject symlinks to prevent path-traversal escapes."""
    if os.path.islink(path):
        log.error("REJECTED: symlink %s", path)
        return True
    return False


def _run_one(hook_path: str) -> int:
    """Execute a single hook. Returns exit code, or -1 on rejection/timeout."""
    name = os.path.basename(hook_path)

    if _reject_symlink(hook_path):
        return -1

    ext = os.path.splitext(hook_path)[1].lower()
    if ext not in VALID_EXTENSIONS:
        log.warning("SKIPPED %s: unsupported extension '%s'", name, ext)
        return -1

    if not os.access(hook_path, os.X_OK):
        log.warning("SKIPPED %s: not executable", name)
        return -1

    log.info("RUNNING %s ...", name)
    start = time.time()
    try:
        result = subprocess.run(
            [hook_path],
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        log.error("TIMEOUT %s (%.1fs > %ds)", name, elapsed, HOOK_TIMEOUT)
        return -1
    except OSError as e:
        log.error("OSERROR %s: %s", name, e)
        return -1

    elapsed = time.time() - start
    for line in result.stdout.strip().split("\n"):
        if line:
            log.info("[%s:out] %s", name, line)
    for line in result.stderr.strip().split("\n"):
        if line:
            log.warning("[%s:err] %s", name, line)

    if result.returncode == 0:
        log.info("OK   %s (%.1fs)", name, elapsed)
    else:
        log.error("FAIL %s (exit=%d, %.1fs)", name, result.returncode, elapsed)
    return result.returncode


def run_phase(phase: str) -> int:
    """Run all hooks in *phase*. Returns failure count."""
    phase_dir = os.path.join(HOOKS_ROOT, phase)
    if not os.path.isdir(phase_dir):
        log.info("Phase %s: directory not found, skipping", phase)
        return 0

    entries = sorted(
        e
        for e in os.listdir(phase_dir)
        if not e.startswith(".") and os.path.isfile(os.path.join(phase_dir, e))
    )
    if not entries:
        log.info("Phase %s: no hooks", phase)
        return 0

    log.info("=== Phase %s (%d hooks) ====", phase, len(entries))
    failures = 0
    for name in entries:
        rc = _run_one(os.path.join(phase_dir, name))
        if rc != 0:
            failures += 1
    log.info("=== Phase %s complete (%d failures) ====", phase, failures)
    return failures


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "run":
        print(f"Usage: {sys.argv[0]} run <phase>", file=sys.stderr)
        print(f"Phases: post-config, ready", file=sys.stderr)
        sys.exit(1)
    phase = sys.argv[2]
    if phase not in ("post-config", "ready"):
        print(f"Unknown phase: {phase}", file=sys.stderr)
        sys.exit(1)
    run_phase(phase)
    # Always exit 0; hook failures are logged but don't abort the boot.
    sys.exit(0)


if __name__ == "__main__":
    main()
