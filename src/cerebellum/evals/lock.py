"""One `cerebellum eval` at a time per CEREBELLUM_HOME. Concurrent evals would share one sandbox
payments API: they would interleave its fail modes, and the first to finish would stop it under
the other. The lock is an operating-system file lock, which the system releases when its process
exits however it exits, so a lock left by a killed eval never blocks the next one."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cerebellum.config import EVAL_LOCK_FILE
from cerebellum.errors import CerebellumError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class EvalBusy(CerebellumError):
    """Another eval holds the lock of this home."""


def _try_lock(fd: int) -> bool:
    try:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


class EvalLock:
    def __init__(self, home: Path):
        self.home = home
        self._fd: int | None = None

    def acquire(self) -> None:
        """Take the lock or raise EvalBusy at once (never waits)."""
        self.home.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.home / EVAL_LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        if not _try_lock(fd):
            os.close(fd)
            raise EvalBusy(
                f"another `cerebellum eval` is running in {self.home}; "
                "wait for it to finish, then run this one"
            )
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> EvalLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
