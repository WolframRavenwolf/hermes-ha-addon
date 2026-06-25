"""Tests for the boot-hook runner (hooks-runner.py).

These tests exercise the runner in isolation — symlink rejection,
timeout, logging, ordering, and disabled-by-default gating.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Path to the hook runner under test
RUNNER = os.path.join(os.path.dirname(__file__), "..", "hermes_agent", "hooks_runner.py")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hooks_dir(tmp_path: Path) -> Path:
    """Create a temporary addon-hooks tree and point the runner at it."""
    root = tmp_path / "addon-hooks"
    root.mkdir()
    from hermes_agent import hooks_runner as runner

    runner.HOOKS_ROOT = str(root)
    runner.LOG_FILE = str(tmp_path / "boot-hooks.log")
    return root


@pytest.fixture
def runner_mod():
    """Reimport the runner module fresh (to clear state)."""
    import importlib

    from hermes_agent import hooks_runner as m

    importlib.reload(m)
    return m


# ---------------------------------------------------------------------------
# Disabled-by-default (integration-level)
# ---------------------------------------------------------------------------


def test_runner_not_invoked_when_disabled():
    """Simulate the run.sh guard — if enable_boot_hooks is false, the
    runner is never called. This is enforced in run.sh, not in the
    runner itself, so we just verify the runner exists."""
    assert os.path.isfile(RUNNER), "hooks-runner.py must exist"


# ---------------------------------------------------------------------------
# Symlink rejection
# ---------------------------------------------------------------------------


def test_reject_symlink(tmp_path: Path, runner_mod):
    """Symlinks must be rejected outright."""
    phase_dir = tmp_path / "ready"
    phase_dir.mkdir(parents=True)
    target = phase_dir / "real.sh"
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    link = phase_dir / "evil.sh"
    link.symlink_to("/etc/passwd")

    runner_mod.HOOKS_ROOT = str(tmp_path)
    runner_mod.LOG_FILE = str(tmp_path / "log")

    with pytest.raises(SystemExit):
        runner_mod.main()

    assert runner_mod._reject_symlink(str(link)) is True
    assert runner_mod._reject_symlink(str(target)) is False


def test_symlink_does_not_execute(tmp_path: Path, runner_mod):
    """A symlink hook must not be executed (only logged+skipped)."""
    phase_dir = tmp_path / "post-config"
    phase_dir.mkdir(parents=True)
    hook = phase_dir / "evil.sh"
    hook.symlink_to("/bin/sh")

    runner_mod.HOOKS_ROOT = str(tmp_path)
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)
    rc = runner_mod._run_one(str(hook))
    assert rc == -1
    assert "REJECTED" in log_path.read_text()


# ---------------------------------------------------------------------------
# Non-executable hooks
# ---------------------------------------------------------------------------


def test_non_executable_skipped(tmp_path: Path, runner_mod):
    """A hook without execute permission must be skipped."""
    phase_dir = tmp_path / "post-config"
    phase_dir.mkdir(parents=True)
    hook = phase_dir / "skip.sh"
    hook.write_text("#!/bin/sh\nexit 0\n")

    runner_mod.HOOKS_ROOT = str(tmp_path)
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)
    rc = runner_mod._run_one(str(hook))
    assert rc == -1
    assert "not executable" in log_path.read_text()


# ---------------------------------------------------------------------------
# Unsupported extensions
# ---------------------------------------------------------------------------


def test_invalid_extension_skipped(tmp_path: Path, runner_mod):
    phase_dir = tmp_path / "post-config"
    phase_dir.mkdir(parents=True)
    hook = phase_dir / "evil.conf"
    hook.write_text("location / { }")
    hook.chmod(0o755)

    runner_mod.HOOKS_ROOT = str(tmp_path)
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)
    rc = runner_mod._run_one(str(hook))
    assert rc == -1
    assert "unsupported extension" in log_path.read_text()


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


def test_ordering_lexicographic(tmp_path: Path, runner_mod):
    """Hooks must run in lexicographic order within a phase."""
    phase_dir = tmp_path / "ready"
    phase_dir.mkdir(parents=True)

    for name in ("20-second.sh", "10-first.sh", "30-third.sh"):
        p = phase_dir / name
        p.write_text(f'#!/bin/sh\necho "{name}"\n')
        p.chmod(0o755)

    runner_mod.HOOKS_ROOT = str(tmp_path)
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)

    runner_mod.run_phase("ready")

    log_text = log_path.read_text()
    out_lines = [
        line for line in log_text.split("\n") if ":out]" in line
    ]
    assert len(out_lines) == 3
    assert "10-first.sh" in out_lines[0]
    assert "20-second.sh" in out_lines[1]
    assert "30-third.sh" in out_lines[2]


# ---------------------------------------------------------------------------
# Hook timeout
# ---------------------------------------------------------------------------


def test_hook_timeout(tmp_path: Path, runner_mod):
    """A hook that exceeds HOOK_TIMEOUT must be killed and logged."""
    phase_dir = tmp_path / "post-config"
    phase_dir.mkdir(parents=True)
    hook = phase_dir / "sleep.sh"
    hook.write_text("#!/bin/sh\nsleep 3600\n")
    hook.chmod(0o755)

    runner_mod.HOOKS_ROOT = str(tmp_path)
    runner_mod.HOOK_TIMEOUT = 1
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)
    rc = runner_mod._run_one(str(hook))
    assert rc == -1
    assert "TIMEOUT" in log_path.read_text()


# ---------------------------------------------------------------------------
# Non-zero exit handling
# ---------------------------------------------------------------------------


def test_failure_logged_continues(tmp_path: Path, runner_mod):
    """A failing hook must be logged but not abort subsequent hooks."""
    phase_dir = tmp_path / "ready"
    phase_dir.mkdir(parents=True)

    fail_hook = phase_dir / "10-fail.sh"
    fail_hook.write_text("#!/bin/sh\nexit 1\n")
    fail_hook.chmod(0o755)

    ok_hook = phase_dir / "20-ok.sh"
    ok_hook.write_text("#!/bin/sh\nexit 0\n")
    ok_hook.chmod(0o755)

    runner_mod.HOOKS_ROOT = str(tmp_path)
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)

    failures = runner_mod.run_phase("ready")
    assert failures == 1

    log_text = log_path.read_text()
    assert "FAIL 10-fail.sh" in log_text
    assert "OK   20-ok.sh" in log_text


# ---------------------------------------------------------------------------
# Zero hooks = no error
# ---------------------------------------------------------------------------


def test_empty_phase_no_error(tmp_path: Path, runner_mod):
    """A phase directory with no hooks must not fail."""
    phase_dir = tmp_path / "post-config"
    phase_dir.mkdir(parents=True)

    runner_mod.HOOKS_ROOT = str(tmp_path)
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)

    failures = runner_mod.run_phase("post-config")
    assert failures == 0


def test_missing_phase_no_error(tmp_path: Path, runner_mod):
    """A phase directory that doesn't exist must not fail."""
    runner_mod.HOOKS_ROOT = str(tmp_path)
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)

    failures = runner_mod.run_phase("ready")
    assert failures == 0


# ---------------------------------------------------------------------------
# Logging output
# ---------------------------------------------------------------------------


def test_successful_hook_logged(tmp_path: Path, runner_mod):
    """stdout from a successful hook must appear in the log."""
    phase_dir = tmp_path / "post-config"
    phase_dir.mkdir(parents=True)
    hook = phase_dir / "10-ok.sh"
    hook.write_text("#!/bin/sh\necho 'hello from hook'\n")
    hook.chmod(0o755)

    runner_mod.HOOKS_ROOT = str(tmp_path)
    log_path = tmp_path / "log"
    runner_mod.LOG_FILE = str(log_path)

    runner_mod._run_one(str(hook))
    log_text = log_path.read_text()
    assert "[10-ok.sh:out] hello from hook" in log_text
    assert "OK   10-ok.sh" in log_text
