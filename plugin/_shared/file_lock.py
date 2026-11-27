"""Fail-closed cross-platform exclusive file lock (Python 3.10+ stdlib)."""

from __future__ import annotations

import contextlib
import errno
import os
from pathlib import Path
import time
from typing import Iterator, Union

from .path_safety import ensure_safe_dir, is_symlink_or_junction

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]


@contextlib.contextmanager
def exclusive_file_lock(
    path: Union[str, Path],
    timeout_s: float = 5.0,
    poll_interval_s: float = 0.02,
) -> Iterator[None]:
    """Acquire an exclusive non-blocking file lock or fail closed with TimeoutError."""
    lock_str = os.fspath(path)
    if is_symlink_or_junction(lock_str):
        raise ValueError(f"Lock file must not be a symlink or junction: {lock_str}")
    parent_str = os.path.dirname(os.path.abspath(lock_str))
    if parent_str:
        ensure_safe_dir(parent_str)

    deadline = time.monotonic() + max(0.0, float(timeout_s))
    interval = max(0.001, float(poll_interval_s))

    with open(lock_str, "a+b") as lock_fh:
        fd = lock_fh.fileno()
        if os.name == "nt":
            if msvcrt is None:
                raise RuntimeError("msvcrt locking backend is unavailable on Windows")
            lock_fh.seek(0, os.SEEK_END)
            if lock_fh.tell() == 0:
                lock_fh.write(b"\x00")
                lock_fh.flush()

            while True:
                try:
                    lock_fh.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    now = time.monotonic()
                    if now >= deadline:
                        raise TimeoutError(
                            f"Timed out after {timeout_s}s acquiring file lock on {lock_str}: {exc}"
                        ) from exc
                    time.sleep(min(interval, max(0.0, deadline - now)))
            try:
                yield
            finally:
                try:
                    lock_fh.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            if fcntl is None:
                raise RuntimeError("fcntl locking backend is unavailable on POSIX")
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if not isinstance(exc, BlockingIOError) and exc.errno not in (
                        errno.EACCES,
                        errno.EAGAIN,
                        errno.EWOULDBLOCK,
                    ):
                        raise
                    now = time.monotonic()
                    if now >= deadline:
                        raise TimeoutError(
                            f"Timed out after {timeout_s}s acquiring file lock on {lock_str}: {exc}"
                        ) from exc
                    time.sleep(min(interval, max(0.0, deadline - now)))
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
