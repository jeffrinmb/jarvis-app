"""A small cross-platform exclusive lock based on a lock file.

Used by the audit ledger and by per-session state, where several short-lived processes (for
example one hook process per tool call) may read-modify-write the same file at once.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockTimeoutError(Exception):
    """The lock could not be taken within the timeout."""


@contextmanager
def file_lock(lock_path: str | Path, timeout: float = 5.0) -> Iterator[None]:
    """Hold an exclusive lock on ``lock_path`` (created with O_EXCL, removed afterwards).

    If no writer is running but the file exists, the lock is stale and can be deleted by hand;
    the timeout error says so.
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError) as exc:
            # On Windows, creating a file that another process has just deleted (still
            # "delete pending") raises PermissionError instead of FileExistsError.
            if time.monotonic() > deadline:
                raise LockTimeoutError(
                    f"could not lock {lock_path} within {timeout}s ({type(exc).__name__}); "
                    "if no writer is running the lock is stale and can be deleted"
                ) from None
            time.sleep(0.02)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        _remove(lock_path)


def _remove(lock_path: Path) -> None:
    for attempt in range(50):
        try:
            os.unlink(lock_path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            # Windows: a virus scanner or another handle can briefly block deletion.
            if attempt == 49:
                raise
            time.sleep(0.02)
