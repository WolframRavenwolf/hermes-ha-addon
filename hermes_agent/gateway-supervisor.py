#!/usr/bin/env python3
"""Own one Hermes gateway and every subprocess it creates."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

_POLL_SECONDS = 0.05
_DESCENDANT_SCAN_SECONDS = 2.0
_GATEWAY_GRACE_SECONDS = 5.0
_DESCENDANT_GRACE_SECONDS = 2.0
_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
_BOOTSTRAP_SIGNALS = {signal.SIGTERM, signal.SIGINT}
_stop_signal: int | None = None


def _request_stop(signum: int, _frame: object) -> None:
    global _stop_signal
    if _stop_signal is None:
        _stop_signal = signum


def _load_gateway_environment() -> tuple[dict[str, str], list[str]]:
    """Clean-reexec this PID, then recover the gateway env from an anonymous FD."""
    if len(sys.argv) >= 3 and sys.argv[1] == "--environment-fd":
        try:
            environment_fd = int(sys.argv[2])
            with os.fdopen(environment_fd, "r", encoding="utf-8") as stream:
                raw_environment = json.load(stream)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"invalid gateway environment handoff: {error}") from error
        if not isinstance(raw_environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw_environment.items()
        ):
            raise RuntimeError("invalid gateway environment handoff records")
        return raw_environment, sys.argv[3:]

    payload = tempfile.TemporaryFile(mode="w+b")
    payload.write(json.dumps(dict(os.environ), ensure_ascii=True).encode("utf-8"))
    payload.flush()
    payload.seek(0)
    environment_fd = payload.fileno()
    os.set_inheritable(environment_fd, True)
    clean_environment = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
        if key in os.environ
    }
    clean_argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--environment-fd",
        str(environment_fd),
        *sys.argv[1:],
    ]
    os.execve(sys.executable, clean_argv, clean_environment)
    raise AssertionError("os.execve returned unexpectedly")


def _set_linux_process_contract(expected_parent_pid: int) -> None:
    """Become a subreaper and die when the original slot supervisor disappears."""
    if sys.platform != "linux":
        return

    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    for option, value in (
        (_PR_SET_CHILD_SUBREAPER, 1),
        (_PR_SET_PDEATHSIG, signal.SIGTERM),
    ):
        if prctl(option, value, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
    if os.getppid() != expected_parent_pid:
        raise RuntimeError(
            "gateway supervisor parent changed before process contract was armed"
        )


def _publish_ready(ready_path: str) -> None:
    descriptor = os.open(
        ready_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _process_parents() -> dict[int, int]:
    """Return a PID-to-PPID snapshot on Linux and the macOS test host."""
    if sys.platform == "linux":
        parents: dict[int, int] = {}
        for stat_path in Path("/proc").glob("[0-9]*/stat"):
            try:
                pid = int(stat_path.parent.name)
                suffix = stat_path.read_text().rsplit(") ", 1)[1].split()
                if suffix[0] != "Z":
                    parents[pid] = int(suffix[1])
            except (IndexError, OSError, ValueError):
                continue
        return parents

    observer = subprocess.Popen(
        ["/bin/ps", "-axo", "pid=,ppid=,state="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    output, _ = observer.communicate()
    if observer.returncode != 0:
        raise subprocess.CalledProcessError(observer.returncode, observer.args)
    parents = {}
    for line in output.splitlines():
        try:
            pid_text, parent_text, state = line.split()
            if not state.startswith("Z"):
                parents[int(pid_text)] = int(parent_text)
        except ValueError:
            continue
    parents.pop(observer.pid, None)
    return parents


def _descendants(root_pid: int, parents: dict[int, int]) -> set[int]:
    children_by_parent: dict[int, list[int]] = {}
    for pid, parent_pid in parents.items():
        children_by_parent.setdefault(parent_pid, []).append(pid)

    found: set[int] = set()
    pending = list(children_by_parent.get(root_pid, ()))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children_by_parent.get(pid, ()))
    return found


def _signal_pids(pids: set[int], signum: int) -> None:
    for pid in sorted(pids, reverse=True):
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            continue
        except PermissionError as error:
            print(
                f"gateway-supervisor: cannot signal owned PID {pid}: {error}",
                file=sys.stderr,
                flush=True,
            )


def _reap_adopted_children() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _live_owned_pids(known: set[int]) -> set[int]:
    parents = _process_parents()
    owned = _descendants(os.getpid(), parents)
    if sys.platform != "linux":
        for pid in tuple(known):
            if pid in parents:
                owned.add(pid)
                owned.update(_descendants(pid, parents))
    owned.discard(os.getpid())
    return owned


def _cleanup_owned_descendants(known: set[int]) -> None:
    """Terminate and reap every process owned by this gateway slot."""
    owned = _live_owned_pids(known)
    _signal_pids(owned, signal.SIGTERM)
    deadline = time.monotonic() + _DESCENDANT_GRACE_SECONDS

    while time.monotonic() < deadline:
        _reap_adopted_children()
        owned = _live_owned_pids(known)
        if not owned:
            return
        _signal_pids(owned, signal.SIGTERM)
        time.sleep(_POLL_SECONDS)

    _signal_pids(_live_owned_pids(known), signal.SIGKILL)
    deadline = time.monotonic() + _DESCENDANT_GRACE_SECONDS
    while time.monotonic() < deadline:
        _reap_adopted_children()
        if not _live_owned_pids(known):
            return
        time.sleep(_POLL_SECONDS)

    survivors = sorted(_live_owned_pids(known))
    if survivors:
        raise RuntimeError(f"owned gateway descendants survived SIGKILL: {survivors}")


def _exit_code(returncode: int) -> int:
    return returncode if returncode >= 0 else 128 + abs(returncode)


def supervise(
    python_path: str,
    launcher: str,
    gateway_environment: dict[str, str],
) -> int:
    """Run one gateway and return only after all of its descendants are gone."""
    gateway = subprocess.Popen(
        [
            python_path,
            launcher,
            "gateway",
            "run",
            "--external-supervisor",
        ],
        close_fds=True,
        env=gateway_environment,
    )
    gateway_environment.clear()
    known: set[int] = {gateway.pid}
    stop_forwarded = False
    stop_deadline: float | None = None
    next_descendant_scan = 0.0
    descendant_scan_seconds = (
        _DESCENDANT_SCAN_SECONDS if sys.platform == "linux" else _POLL_SECONDS
    )

    while gateway.poll() is None:
        now = time.monotonic()
        if now >= next_descendant_scan:
            parents = _process_parents()
            known.update(_descendants(gateway.pid, parents))
            known.update(_descendants(os.getpid(), parents))
            known.discard(os.getpid())
            next_descendant_scan = now + descendant_scan_seconds

        if _stop_signal is not None and not stop_forwarded:
            try:
                os.kill(gateway.pid, _stop_signal)
            except ProcessLookupError:
                pass
            stop_forwarded = True
            stop_deadline = time.monotonic() + _GATEWAY_GRACE_SECONDS
        elif stop_deadline is not None and time.monotonic() >= stop_deadline:
            _signal_pids(_live_owned_pids(known), signal.SIGKILL)
            stop_deadline = None
        time.sleep(_POLL_SECONDS)

    returncode = gateway.wait()
    known.discard(gateway.pid)
    _cleanup_owned_descendants(known)
    print(
        f"gateway-supervisor: gateway exited with status {_exit_code(returncode)}; "
        "owned descendants empty",
        file=sys.stderr,
        flush=True,
    )
    return 0


def main() -> int:
    resumed = len(sys.argv) >= 3 and sys.argv[1] == "--environment-fd"
    for signum in _BOOTSTRAP_SIGNALS:
        signal.signal(signum, _request_stop)

    if not resumed:
        signal.pthread_sigmask(signal.SIG_BLOCK, _BOOTSTRAP_SIGNALS)
        initial_arguments = sys.argv[1:]
        if len(initial_arguments) != 3:
            print(
                "gateway-supervisor.py: expected LAUNCHER READY_PATH PARENT_PID",
                file=sys.stderr,
            )
            return 64
        try:
            expected_parent_pid = int(initial_arguments[2])
            if os.getpgrp() != os.getpid():
                os.setsid()
            _set_linux_process_contract(expected_parent_pid)
            _load_gateway_environment()
        except (OSError, RuntimeError, ValueError) as error:
            print(f"gateway-supervisor: {error}", file=sys.stderr, flush=True)
            return 70
        raise AssertionError("clean re-exec returned unexpectedly")

    try:
        gateway_environment, arguments = _load_gateway_environment()
        if len(arguments) != 3:
            raise RuntimeError(
                "expected LAUNCHER READY_PATH PARENT_PID after re-exec"
            )
        launcher, ready_path, parent_pid_text = arguments
        gateway_executable = str(Path(sys.executable).with_name("hermes-gateway"))
        expected_parent_pid = int(parent_pid_text)
        _set_linux_process_contract(expected_parent_pid)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, _BOOTSTRAP_SIGNALS)
        if _stop_signal is not None:
            return 0
        _publish_ready(ready_path)
        if _stop_signal is not None:
            return 0
        return supervise(gateway_executable, launcher, gateway_environment)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"gateway-supervisor: {error}", file=sys.stderr, flush=True)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
