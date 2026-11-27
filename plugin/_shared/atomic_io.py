"""Atomic UTF-8/LF file and JSON/JSONL I/O primitives with lock retries (Python 3.10+ stdlib)."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Optional, Union

from .file_lock import exclusive_file_lock
from .path_safety import ensure_safe_dir, is_symlink_or_junction


def is_lock_oserror(exc: BaseException) -> bool:
    """Return True if exc represents a transient Windows/POSIX file lock error."""
    if isinstance(exc, PermissionError):
        return True
    if not isinstance(exc, OSError):
        return False
    if getattr(exc, "winerror", None) in (5, 32, 33):
        return True
    return exc.errno in (errno.EACCES, errno.EPERM, errno.EBUSY)


def write_bytes_atomic(
    path: Union[str, Path],
    data: bytes,
    *,
    retries: int = 5,
    base_delay_s: float = 0.02,
    reject_symlink: bool = True,
) -> Path:
    """Atomically write bytes via same-directory temp file, fsync, and bounded replace retries."""
    target = Path(path)
    if reject_symlink and is_symlink_or_junction(target):
        raise ValueError(f"Refusing atomic write to symlink or junction: {target}")
    ensure_safe_dir(target.parent, allow_existing_symlink=not reject_symlink)

    max_attempts = max(1, int(retries))
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        for attempt in range(max_attempts):
            try:
                os.replace(str(tmp_path), str(target))
                tmp_path = None
                break
            except OSError as exc:
                if is_lock_oserror(exc) and attempt < max_attempts - 1:
                    time.sleep(max(0.0, float(base_delay_s)) * (2**attempt))
                    continue
                raise
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return target


def write_text_atomic(
    path: Union[str, Path],
    text: str,
    *,
    encoding: str = "utf-8",
    retries: int = 5,
    base_delay_s: float = 0.02,
    reject_symlink: bool = True,
) -> Path:
    """Atomically write LF-normalized text as UTF-8 without BOM."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return write_bytes_atomic(
        path,
        normalized.encode(encoding),
        retries=retries,
        base_delay_s=base_delay_s,
        reject_symlink=reject_symlink,
    )


def write_json_atomic(
    path: Union[str, Path],
    payload: Any,
    *,
    indent: Optional[int] = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    retries: int = 5,
    base_delay_s: float = 0.02,
    reject_symlink: bool = True,
) -> Path:
    """Atomically write a JSON payload with trailing LF."""
    serialized = json.dumps(
        payload,
        indent=indent,
        sort_keys=sort_keys,
        ensure_ascii=ensure_ascii,
    ) + "\n"
    return write_text_atomic(
        path,
        serialized,
        encoding="utf-8",
        retries=retries,
        base_delay_s=base_delay_s,
        reject_symlink=reject_symlink,
    )


def append_jsonl_atomic(
    path: Union[str, Path],
    record: Any,
    *,
    lock_path: Optional[Union[str, Path]] = None,
    timeout_s: float = 5.0,
    ensure_ascii: bool = False,
    reject_symlink: bool = True,
) -> Path:
    """Append a single JSONL record under an exclusive file lock with fsync."""
    target = Path(path)
    if reject_symlink and is_symlink_or_junction(target):
        raise ValueError(f"Refusing JSONL append to symlink or junction: {target}")
    ensure_safe_dir(target.parent, allow_existing_symlink=not reject_symlink)

    resolved_lock = Path(lock_path) if lock_path is not None else target.with_suffix(".lock")
    line = (
        json.dumps(record, ensure_ascii=ensure_ascii) + "\n"
    ).replace("\r\n", "\n").replace("\r", "\n")

    with exclusive_file_lock(resolved_lock, timeout_s=timeout_s):
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return target
