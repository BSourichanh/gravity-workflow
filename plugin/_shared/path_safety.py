"""Cross-platform path safety, symlink/junction detection, and traversal guards (Python 3.10+ stdlib)."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import stat
from typing import Optional, Union

_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
_IO_REPARSE_TAG_MOUNT_POINT = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
_IO_REPARSE_TAG_SYMLINK = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _win_get_file_attributes_reparse(path: Union[str, Path]) -> bool:
    """Check Windows FILE_ATTRIBUTE_REPARSE_POINT via kernel32.GetFileAttributesW."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    kernel32 = getattr(windll, "kernel32", None)
    get_attrs = getattr(kernel32, "GetFileAttributesW", None)
    if not callable(get_attrs):
        return False
    try:
        attrs = int(get_attrs(os.fspath(path)))
    except (OSError, TypeError, ValueError):
        return False
    if attrs == _INVALID_FILE_ATTRIBUTES or attrs < 0:
        return False
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def is_symlink_or_junction(path: Union[str, Path]) -> bool:
    """Return True if path is a symlink or a Windows NTFS junction (Python 3.10+)."""
    raw = os.fspath(path)
    try:
        if os.path.islink(raw):
            return True
    except OSError:
        pass

    if isinstance(path, Path):
        is_junc = getattr(path, "is_junction", None)
        if callable(is_junc):
            try:
                if is_junc():
                    return True
            except OSError:
                pass

    os_is_junc = getattr(os.path, "isjunction", None)
    if callable(os_is_junc):
        try:
            if os_is_junc(raw):
                return True
        except OSError:
            pass

    if os.name == "nt":
        file_attrs = 0
        reparse_tag = 0
        try:
            st = os.lstat(raw)
            file_attrs = int(getattr(st, "st_file_attributes", 0) or 0)
            reparse_tag = int(getattr(st, "st_reparse_tag", 0) or 0)
        except OSError:
            st = None

        if reparse_tag in (_IO_REPARSE_TAG_MOUNT_POINT, _IO_REPARSE_TAG_SYMLINK):
            return True
        if (file_attrs & _FILE_ATTRIBUTE_REPARSE_POINT) and reparse_tag == 0:
            return True
        if (st is None or file_attrs == 0) and _win_get_file_attributes_reparse(raw):
            return True
        try:
            os.readlink(raw)
            return True
        except OSError:
            pass

    return False


def unlink_or_rmdir_link(path: Union[str, Path]) -> None:
    """Remove a symlink or Windows junction without following its target."""
    p = Path(path)
    try:
        p.unlink()
    except OSError:
        p.rmdir()


def is_within_root(root: Union[str, Path], target: Union[str, Path]) -> bool:
    """Return True when target resolves inside (or equal to) root."""
    try:
        root_res = Path(os.path.abspath(os.fspath(root))).resolve()
        target_res = Path(os.path.abspath(os.fspath(target))).resolve()
        return target_res.is_relative_to(root_res)
    except (OSError, ValueError):
        return False


def ensure_no_symlink_or_junction_under(
    root: Union[str, Path],
    target: Union[str, Path],
) -> Path:
    """Verify root, target, and all intermediate segments contain no symlink or junction."""
    root_abs = Path(os.path.abspath(os.fspath(root)))
    target_abs = Path(os.path.abspath(os.fspath(target)))

    if is_symlink_or_junction(root_abs):
        raise ValueError(f"Root path must not be a symlink or junction: {root_abs}")

    rel = target_abs.relative_to(root_abs)
    cur = root_abs
    for part in rel.parts:
        cur = cur / part
        if is_symlink_or_junction(cur):
            raise ValueError(f"Refusing symlink or junction path component: {cur}")

    if not is_within_root(root_abs, target_abs):
        raise ValueError(f"Target path escapes root ({target_abs} not in {root_abs})")
    return target_abs


def ensure_safe_dir(
    path: Union[str, Path],
    *,
    allow_existing_symlink: bool = False,
    root: Optional[Union[str, Path]] = None,
) -> Path:
    """Ensure directory exists safely without traversing symlinks/junctions by default."""
    target_str = os.path.abspath(os.fspath(path))
    if root is not None:
        if not allow_existing_symlink:
            ensure_no_symlink_or_junction_under(root, target_str)
        elif not is_within_root(root, target_str):
            raise ValueError(f"Directory escapes root ({target_str} not in {root})")
    elif not allow_existing_symlink and is_symlink_or_junction(target_str):
        raise ValueError(f"Refusing symlink or junction directory: {target_str}")

    if os.path.exists(target_str) and not os.path.isdir(target_str):
        raise NotADirectoryError(f"Expected directory but found non-directory: {target_str}")

    os.makedirs(target_str, exist_ok=True)
    if not allow_existing_symlink and is_symlink_or_junction(target_str):
        raise ValueError(f"Refusing symlink or junction directory: {target_str}")
    return Path(target_str)


def validate_rel_member_path(
    member: str,
    *,
    label: str = "member path",
    allow_dir: bool = False,
) -> str:
    """Validate and normalize a relative archive/topic member path (rejects traversal/absolute/NUL)."""
    if not isinstance(member, str) or not member:
        raise ValueError(f"Invalid empty path in {label}.")
    if "\x00" in member:
        raise ValueError(f"NUL byte rejected in {label}: {member!r}")
    if "\\" in member:
        raise ValueError(f"Backslash rejected in {label}: {member!r}")
    if member.startswith("/") or _WINDOWS_DRIVE_RE.match(member):
        raise ValueError(f"Absolute or drive-qualified path rejected in {label}: {member!r}")

    is_dir = member.endswith("/")
    trimmed = member.rstrip("/") if (allow_dir and is_dir) else member
    if not trimmed:
        raise ValueError(f"Empty path segment rejected in {label}: {member!r}")

    parts = trimmed.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise ValueError(f"Unsafe path traversal segment rejected in {label}: {member!r}")
    return trimmed + ("/" if (allow_dir and is_dir) else "")


def safe_home_dir() -> Path:
    """Return user home directory, falling back to USERPROFILE/HOME if Path.home() fails."""
    try:
        return Path.home()
    except RuntimeError:
        for env_key in ("USERPROFILE", "HOME"):
            val = os.environ.get(env_key, "").strip()
            if val:
                return Path(val)
        drive = os.environ.get("HOMEDRIVE", "").strip()
        home_path = os.environ.get("HOMEPATH", "").strip()
        if drive and home_path:
            return Path(f"{drive}{home_path}")
        raise
