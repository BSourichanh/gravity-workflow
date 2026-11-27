"""Common Antigravity hook runtime helpers (Python 3.10+ stdlib)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Optional, Sequence, Union

_STRIP_META_RE = re.compile(
    r"<ADDITIONAL_METADATA>.*?(?:</ADDITIONAL_METADATA>|$)"
    r"|<USER_SETTINGS_CHANGE>.*?(?:</USER_SETTINGS_CHANGE>|$)"
    r"|</?USER_REQUEST>",
    re.DOTALL,
)


def configure_utf8_stdio() -> None:
    """Force UTF-8 encoding with replacement error handling on stdin/stdout/stderr."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def read_stdin_text(timeout_s: float = 1.0) -> str:
    """Safely read piped stdin within timeout_s without blocking on interactive TTYs."""
    configure_utf8_stdio()
    if sys.stdin is None:
        return ""
    try:
        if sys.stdin.isatty():
            return ""
    except Exception:
        pass

    buf: list[str] = []

    def _reader() -> None:
        try:
            buf.append(sys.stdin.read())
        except Exception:
            pass

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    thread.join(timeout=max(0.0, float(timeout_s)))
    return buf[0].strip() if buf else ""


def read_stdin_json(timeout_s: float = 1.0) -> dict[str, Any]:
    """Read stdin and parse a JSON object; return {} on timeout, empty input, or invalid JSON."""
    raw = read_stdin_text(timeout_s=timeout_s)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def emit_json(payload: dict[str, Any]) -> None:
    """Write a single-line ASCII-safe JSON object to stdout and flush."""
    configure_utf8_stdio()
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def emit_passthrough() -> None:
    """Emit empty JSON object `{}` so execution proceeds without injection."""
    emit_json({})


def emit_ok() -> None:
    """Alias for emit_passthrough() matching PostToolUse/Stop hook contract."""
    emit_passthrough()


def emit_user_markdown(markdown_text: str, *, system_message: str = "") -> None:
    """Display user-facing Markdown directly in chat and terminate the LLM turn."""
    emit_json(
        {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": system_message,
                        "metadata": {"user_markdown": markdown_text},
                    }
                }
            ],
            "terminationBehavior": "terminate",
        }
    )


def emit_handoff_markdown(markdown_text: str, *, system_message: str = "") -> None:
    """Display inline Markdown and let the agent continue to invoke tools."""
    emit_json(
        {
            "injectSteps": [
                {
                    "systemMessage": {
                        "systemMessage": system_message,
                        "metadata": {"user_markdown": markdown_text},
                    }
                }
            ],
        }
    )


def first_not_none(*values: Any) -> Any:
    """Return the first argument that is not None."""
    for val in values:
        if val is not None:
            return val
    return None


def first_str_arg(data: Any, *keys: Union[str, Sequence[str]]) -> str:
    """Return the first stripped non-empty string value found among keys."""
    if not isinstance(data, dict):
        return ""
    flat_keys: list[str] = []
    for item in keys:
        if isinstance(item, str):
            flat_keys.append(item)
        elif isinstance(item, (list, tuple)):
            flat_keys.extend(k for k in item if isinstance(k, str))
    for key in flat_keys:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def normalize_command(cmd: str) -> str:
    """Strip Antigravity metadata tags, collapse whitespace, and lowercase."""
    if not isinstance(cmd, str):
        return ""
    cleaned = _STRIP_META_RE.sub("", cmd)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def is_initial_invocation(data: Any) -> bool:
    """Return True when hook payload represents the initial user turn (invocationNum is None or 0)."""
    if not isinstance(data, dict):
        return False
    inv_num = first_not_none(data.get("invocationNum"), data.get("invocation_num"))
    if inv_num is None:
        return True
    if isinstance(inv_num, bool):
        return False
    return inv_num == 0


def resolve_workspace_root(
    data: Optional[dict[str, Any]],
    *,
    anchor_file: Optional[str] = None,
    fallback_cwd: bool = False,
    env_var: Optional[str] = None,
) -> Optional[Path]:
    """Resolve workspace root from hook payload (cwd/workspacePaths), env_var, or optional CWD."""
    _ = anchor_file
    if isinstance(data, dict):
        cwd = data.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return Path(cwd.strip()).expanduser()
        ws_paths = data.get("workspacePaths")
        if isinstance(ws_paths, list) and ws_paths:
            first = ws_paths[0]
            if isinstance(first, str) and first.strip():
                return Path(first.strip()).expanduser()

    target_env = env_var if env_var else "GRAVITY_WORKSPACE"
    env_ws = os.environ.get(target_env, "").strip()
    if env_ws:
        return Path(env_ws).expanduser()
    if fallback_cwd:
        return Path.cwd()
    return None


def resolve_brain_dir(data: Optional[dict[str, Any]]) -> Optional[Path]:
    """Infer brain directory from transcriptPath/transcript_path or ANTIGRAVITY_BRAIN_DIR."""
    if isinstance(data, dict):
        tpath_raw = first_not_none(data.get("transcriptPath"), data.get("transcript_path"))
        if isinstance(tpath_raw, str) and tpath_raw.strip():
            tp = Path(tpath_raw.strip()).expanduser()
            if (
                len(tp.parents) >= 4
                and tp.parent.name == "logs"
                and tp.parent.parent.name == ".system_generated"
            ):
                return tp.parents[3]
    env_brain = os.environ.get("ANTIGRAVITY_BRAIN_DIR", "").strip()
    if env_brain:
        return Path(env_brain).expanduser()
    return None


def resolve_conversation_id(data: Optional[dict[str, Any]]) -> Optional[str]:
    """Resolve conversation UUID from hook payload or transcriptPath/transcript_path."""
    if isinstance(data, dict):
        cid = first_not_none(
            data.get("conversationId"),
            data.get("sessionId"),
            data.get("conversation_id"),
        )
        if isinstance(cid, str) and cid.strip():
            return cid.strip()
        tpath_raw = first_not_none(data.get("transcriptPath"), data.get("transcript_path"))
        if isinstance(tpath_raw, str) and tpath_raw.strip():
            tp = Path(tpath_raw.strip()).expanduser()
            if (
                len(tp.parents) >= 3
                and tp.parent.name == "logs"
                and tp.parent.parent.name == ".system_generated"
            ):
                return tp.parents[2].name
    return None
