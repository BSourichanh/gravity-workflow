"""Shared Python 3.10+ stdlib-only primitives for the Gravity ecosystem."""

from __future__ import annotations

from .atomic_io import (
    append_jsonl_atomic,
    is_lock_oserror,
    write_bytes_atomic,
    write_json_atomic,
    write_text_atomic,
)
from .file_lock import exclusive_file_lock
from .hook_runtime import (
    configure_utf8_stdio,
    emit_handoff_markdown,
    emit_json,
    emit_ok,
    emit_passthrough,
    emit_user_markdown,
    first_not_none,
    first_str_arg,
    is_initial_invocation,
    normalize_command,
    read_stdin_json,
    read_stdin_text,
    resolve_brain_dir,
    resolve_conversation_id,
    resolve_workspace_root,
)
from .path_safety import (
    ensure_no_symlink_or_junction_under,
    ensure_safe_dir,
    is_symlink_or_junction,
    is_within_root,
    safe_home_dir,
    unlink_or_rmdir_link,
    validate_rel_member_path,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "append_jsonl_atomic",
    "configure_utf8_stdio",
    "emit_handoff_markdown",
    "emit_json",
    "emit_ok",
    "emit_passthrough",
    "emit_user_markdown",
    "ensure_no_symlink_or_junction_under",
    "ensure_safe_dir",
    "exclusive_file_lock",
    "first_not_none",
    "first_str_arg",
    "is_initial_invocation",
    "is_lock_oserror",
    "is_symlink_or_junction",
    "is_within_root",
    "normalize_command",
    "read_stdin_json",
    "read_stdin_text",
    "resolve_brain_dir",
    "resolve_conversation_id",
    "resolve_workspace_root",
    "safe_home_dir",
    "unlink_or_rmdir_link",
    "validate_rel_member_path",
    "write_bytes_atomic",
    "write_json_atomic",
    "write_text_atomic",
]
