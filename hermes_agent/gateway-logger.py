#!/usr/bin/env python3
"""Duplicate gateway output to the container log and a durable file."""

import ctypes
import errno
import os
import signal
import sys
from typing import Optional

_CHUNK_SIZE = 64 * 1024
_EX_USAGE = 64
_EX_CANTCREAT = 73
_EX_IOERR = 74
_PR_SET_PDEATHSIG = 1


def _arm_parent_death_signal(expected_parent_pid: int) -> None:
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
    if prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != expected_parent_pid:
        raise OSError(errno.ESRCH, "gateway logger parent changed before startup")


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte or raise the underlying output error."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "zero-length write")
        view = view[written:]


def _copy_stream(
    source_fd: int,
    log_fd: int,
    *,
    stdout_fd: Optional[int] = 1,
) -> None:
    """Copy FIFO bytes, treating the durable log as the authoritative sink."""
    console_fd = stdout_fd
    while True:
        data = os.read(source_fd, _CHUNK_SIZE)
        if not data:
            return

        # Fail immediately if the durable log cannot accept the complete record.
        _write_all(log_fd, data)

        # A broken container-log consumer must not disable the durable log.
        if console_fd is not None:
            try:
                _write_all(console_fd, data)
            except OSError:
                console_fd = None


def _diagnose(message: str, exc: OSError) -> None:
    errno_value = exc.errno if exc.errno is not None else "unknown"
    print(f"[gateway-logger] {message} (errno {errno_value})", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "gateway-logger.py: expected LOG_FILE FIFO PARENT_PID",
            file=sys.stderr,
        )
        return _EX_USAGE

    log_path, fifo_path, parent_pid_text = argv[1:]
    try:
        expected_parent_pid = int(parent_pid_text)
        _arm_parent_death_signal(expected_parent_pid)
    except (OSError, ValueError) as exc:
        if isinstance(exc, OSError):
            _diagnose("parent-death contract failed", exc)
        else:
            print("[gateway-logger] invalid parent PID", file=sys.stderr)
        return _EX_CANTCREAT
    try:
        # This blocking open happens only after run.sh has execed us through env -i.
        source_fd = os.open(fifo_path, os.O_RDONLY)
    except OSError as exc:
        _diagnose("FIFO open failed", exc)
        return _EX_CANTCREAT

    try:
        try:
            log_fd = os.open(
                log_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
        except OSError as exc:
            _diagnose("log sink open failed", exc)
            return _EX_IOERR

        try:
            _copy_stream(source_fd, log_fd)
        except OSError as exc:
            _diagnose("log sink write failed", exc)
            return _EX_IOERR
        finally:
            os.close(log_fd)
    finally:
        os.close(source_fd)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
