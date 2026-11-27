"""Incremental JSONL transcript reader, shell exit extraction, and worktree normalization (Python 3.10+ stdlib)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import posixpath
import re
from typing import Any, Callable, Iterable, Optional, Union
from urllib.parse import unquote, urlparse

from .path_safety import is_symlink_or_junction

KNOWN_GRAVITY_REPOS: tuple[str, ...] = (
    "gravity-architect", "gravity-workflow", "gravity-metrics", "gravity-workshop", "gravity",
)

_SHELL_EXIT_RE = re.compile(r"^Command exited with code (-?\d+)\b", re.MULTILINE)
_WT_GRAVITY_RE = re.compile(r"(?:^|/)gravity-worktrees/([^/]+)(?:/|$)", re.IGNORECASE)
_WT_GEMINI_RE = re.compile(r"(?:^|/)\.gemini/antigravity/worktrees/([^/]+)(?:/|$)", re.IGNORECASE)
_WT_DOT_RE = re.compile(r"(?:^|/)\.worktrees/([^/]+)(?:/|$)", re.IGNORECASE)
_WORKER_SUFFIX_RE = re.compile(r"-w\d+$", re.IGNORECASE)
_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:/")
_CONTAINER_DIRS = frozenset({"github", "repos", "repo", "projects", "project", "workspaces", "workspace", "work"})
_USER_PROMPT_META_RE = re.compile(
    r"<ADDITIONAL_METADATA>.*?(?:</ADDITIONAL_METADATA>|$)"
    r"|<USER_SETTINGS_CHANGE>.*?(?:</USER_SETTINGS_CHANGE>|$)"
    r"|</?USER_REQUEST>",
    re.DOTALL | re.IGNORECASE,
)


def parse_iso_ts(raw: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into an aware UTC datetime, or return None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def extract_shell_nonzero_code(content: Any) -> Optional[int]:
    """Return nonzero shell exit code from a GENERIC step header, ignoring output/preview bodies."""
    if not isinstance(content, str) or "Command exited with code " not in content:
        return None
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    header = normalized.split("\nCommand output:", 1)[0].split("\nShowing lines ", 1)[0]
    if header.lstrip().startswith("Showing lines "):
        return None
    for match in _SHELL_EXIT_RE.finditer(header):
        code = int(match.group(1))
        if code != 0:
            return code
    return None


def strip_file_uri(raw: Any) -> str:
    """Convert a file:// URI or quoted path string into a plain filesystem path."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip().strip("\"'")
    if text.lower().startswith("file:"):
        path = unquote(urlparse(text).path or "")
        if len(path) >= 3 and path[0] == "/" and path[2] == ":" and path[1].isalpha():
            path = path[1:]
        return path
    return text


def is_worktree_path(raw_path: Any) -> bool:
    """Return True when raw_path points inside an Antigravity or Gravity worktree directory."""
    cleaned = strip_file_uri(raw_path)
    if not cleaned:
        return False
    low_raw = cleaned.lower()
    norm_low = ("/" + cleaned.replace("\\", "/").strip("/") + "/").lower()
    return (
        "/.gemini/antigravity/worktrees/" in norm_low
        or "/gravity-worktrees/" in norm_low
        or "/.worktrees/" in norm_low
        or "/worktrees/" in norm_low
        or "\\worktrees\\" in (low_raw + "\\")
    )


def _normalize_wt_segment(seg: str, known_repos: set[str], *, generic_suffix: bool) -> str:
    clean = seg.strip()
    if not clean:
        return ""
    for repo in sorted(known_repos, key=lambda r: (-len(r), r)):
        if clean == repo or clean.startswith(repo + "-"):
            return repo
    stripped = _WORKER_SUFFIX_RE.sub("", clean)
    if stripped != clean and stripped:
        return stripped
    if generic_suffix and "-" in clean:
        base, _tail = clean.rsplit("-", 1)
        if base:
            return base
    return clean


def discover_known_repos(
    root_workspace: str = "",
    root_workspace_path: Optional[Union[os.PathLike[str], str]] = None,
) -> set[str]:
    """Build the set of known repository names from Gravity defaults, projects.json, and sibling dirs."""
    repos: set[str] = set(KNOWN_GRAVITY_REPOS)
    if root_workspace and root_workspace != "root":
        repos.add(root_workspace)
    if root_workspace_path is None:
        return repos
    rpath = os.path.abspath(os.fspath(root_workspace_path))
    base_name = os.path.basename(rpath.rstrip("/\\"))
    if base_name:
        repos.add(base_name)
    proj_file = os.path.join(rpath, "projects.json")
    if os.path.isfile(proj_file) and not is_symlink_or_junction(proj_file):
        try:
            with open(proj_file, "r", encoding="utf-8", errors="replace") as fh:
                pdata = json.load(fh)
            items = pdata.get("projects") if isinstance(pdata, dict) else None
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or item.get("id") or "").strip()
                    if name:
                        repos.add(name)
                    seg = posixpath.basename(str(item.get("path") or "").strip().replace("\\", "/").rstrip("/"))
                    if seg:
                        repos.add(seg)
            elif isinstance(items, dict):
                repos.update(str(k).strip() for k in items if str(k).strip())
        except (OSError, ValueError):
            pass
    parent = os.path.dirname(rpath.rstrip("/\\"))
    if parent and os.path.isdir(parent) and not is_symlink_or_junction(parent):
        try:
            for entry in os.scandir(parent):
                if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                    repos.add(entry.name)
        except OSError:
            pass
    return repos


def normalize_worktree_path(
    raw_path: str,
    *,
    root_workspace: str = "",
    root_workspace_path: Optional[Union[os.PathLike[str], str]] = None,
    known_repos: Optional[Iterable[str]] = None,
    is_workspace_uri: bool = False,
) -> str:
    """Resolve a path or file:// URI (including worktrees) to its canonical repository name."""
    cleaned = strip_file_uri(raw_path)
    norm = cleaned.replace("\\", "/").strip()
    if not norm:
        return ""
    repos = (
        {str(r).strip() for r in known_repos if str(r).strip()}
        if known_repos is not None
        else discover_known_repos(root_workspace, root_workspace_path)
    )
    for pattern, generic in ((_WT_GRAVITY_RE, False), (_WT_GEMINI_RE, True), (_WT_DOT_RE, True)):
        match = pattern.search(norm)
        if match:
            return _normalize_wt_segment(match.group(1), repos, generic_suffix=generic)
    low_norm = norm.lower()
    if "/.gemini/antigravity/brain/" in low_norm or "/.system_generated/" in low_norm:
        return ""
    is_abs = norm.startswith("/") or norm.startswith("~/") or bool(_WINDOWS_ABS_RE.match(norm))
    if not is_abs:
        if norm.startswith("./"):
            norm = norm[2:]
        if not norm.startswith("../"):
            if is_workspace_uri and "/" not in norm:
                return norm
            return root_workspace if (root_workspace and root_workspace != "root") else ""
        if root_workspace_path is not None:
            root_posix = os.fspath(root_workspace_path).replace("\\", "/").rstrip("/")
            return normalize_worktree_path(
                posixpath.normpath(f"{root_posix}/{norm}"),
                root_workspace=root_workspace,
                root_workspace_path=root_workspace_path,
                known_repos=repos,
                is_workspace_uri=is_workspace_uri,
            )
        while norm.startswith("../"):
            norm = norm[3:]
        seg = norm.split("/", 1)[0].strip()
        return seg if seg and seg not in (".", "..") else ""
    if is_workspace_uri:
        return norm.rstrip("/").rsplit("/", 1)[-1].strip()
    if root_workspace_path is not None:
        root_posix = os.fspath(root_workspace_path).replace("\\", "/").rstrip("/")
        parent_posix = posixpath.dirname(root_posix).rstrip("/")
        if norm == root_posix or norm.startswith(root_posix + "/"):
            return root_workspace or posixpath.basename(root_posix)
        if parent_posix and parent_posix != "/" and norm.startswith(parent_posix + "/"):
            first_seg = norm[len(parent_posix) + 1 :].split("/", 1)[0].strip()
            if first_seg and not first_seg.startswith("."):
                return first_seg
    parts = [part for part in norm.split("/") if part]
    for seg in reversed(parts):
        if seg in repos:
            return seg
    try:
        cand = os.path.expanduser(cleaned)
        if not is_symlink_or_junction(cand):
            probe = cand if os.path.isdir(cand) else os.path.dirname(cand)
            for _ in range(6):
                if probe and os.path.isdir(probe) and not is_symlink_or_junction(probe):
                    if os.path.exists(os.path.join(probe, ".git")):
                        return os.path.basename(probe.rstrip("/\\"))
                parent = os.path.dirname(probe.rstrip("/\\")) if probe else ""
                if not parent or parent == probe:
                    break
                probe = parent
    except OSError:
        pass
    for idx, seg in enumerate(parts[:-1]):
        if seg.lower() in _CONTAINER_DIRS or re.match(r"^tmp[a-z0-9_]{4,}$", seg, re.IGNORECASE):
            nxt = parts[idx + 1]
            if nxt and not nxt.startswith("."):
                return nxt
    return ""


def read_jsonl_incremental(
    path: Union[str, os.PathLike[str]],
    cache: dict[str, dict[str, Any]],
    make_state: Callable[[], Any],
    apply_line: Callable[[Any, dict[str, Any]], None],
    *,
    max_cache_entries: int = 256,
    stats: Optional[dict[str, int]] = None,
) -> Any:
    """Incrementally read a JSONL file keyed by (st_ino, st_mtime_ns, st_size), rejecting symlinks/junctions."""
    raw_path = os.fspath(path)
    key = os.path.abspath(raw_path)

    if is_symlink_or_junction(raw_path):
        cache.pop(key, None)
        return make_state()

    try:
        if not os.path.isfile(raw_path):
            cache.pop(key, None)
            return make_state()
        st = os.lstat(raw_path)
    except OSError:
        cache.pop(key, None)
        return make_state()

    cached = cache.get(key)
    if (
        cached is not None
        and cached.get("inode") == st.st_ino
        and cached.get("mtime_ns") == st.st_mtime_ns
        and cached.get("size") == st.st_size
    ):
        if stats is not None:
            stats["cache_hits"] = stats.get("cache_hits", 0) + 1
        return cached["state"]

    if cached is not None and cached.get("inode") == st.st_ino and st.st_size >= cached.get("offset", 0):
        offset = int(cached["offset"])
        partial = bytes(cached.get("partial") or b"")
        state = cached["state"]
        is_inc = True
    else:
        offset = 0
        partial = b""
        state = make_state()
        is_inc = False

    try:
        with open(raw_path, "rb") as fh:
            if offset > 0:
                fh.seek(offset)
            chunk = fh.read()
    except OSError:
        return state

    if stats is not None:
        stats["bytes_read"] = stats.get("bytes_read", 0) + len(chunk)
        stat_key = "incremental_reads" if is_inc else "full_reads"
        stats[stat_key] = stats.get(stat_key, 0) + 1

    lines = (partial + chunk).splitlines(keepends=True)
    new_partial = lines.pop() if lines and not lines[-1].endswith((b"\n", b"\r")) else b""

    for raw_line in lines:
        txt = raw_line.decode("utf-8", errors="replace").lstrip("\ufeff").strip()
        if not txt:
            continue
        try:
            obj = json.loads(txt)
        except ValueError:
            continue
        if isinstance(obj, dict):
            apply_line(state, obj)

    limit = max(0, int(max_cache_entries))
    if limit > 0:
        if key not in cache:
            while len(cache) >= limit:
                cache.pop(next(iter(cache)), None)
        cache[key] = {
            "inode": st.st_ino,
            "mtime_ns": st.st_mtime_ns,
            "size": st.st_size,
            "offset": offset + len(chunk),
            "partial": new_partial,
            "state": state,
        }
    return state


def parse_transcript_steps(
    path: Optional[Union[str, os.PathLike[str]]],
    *,
    start_line_idx: int = 0,
    end_line_idx: Optional[int] = None,
    cache: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Parse transcript.jsonl dict records safely, rejecting symlinks/junctions and corrupted lines."""
    if path is None:
        return []
    raw_path = os.path.expanduser(os.fspath(path))
    if is_symlink_or_junction(raw_path) or not os.path.isfile(raw_path):
        return []

    if cache is not None and start_line_idx == 0 and end_line_idx is None:
        steps = read_jsonl_incremental(
            raw_path,
            cache,
            list,
            lambda acc, obj: acc.append(obj),
        )
        return list(steps)

    entries: list[dict[str, Any]] = []
    try:
        with open(raw_path, "r", encoding="utf-8", errors="replace") as fh:
            for line_idx, raw_line in enumerate(fh):
                if line_idx < start_line_idx:
                    continue
                if end_line_idx is not None and line_idx >= end_line_idx:
                    break
                line = raw_line.lstrip("\ufeff").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    entries.append(obj)
    except OSError:
        return []
    return entries


def normalize_user_prompt(text: Optional[str]) -> str:
    """Strip Antigravity wrapper tags from a user prompt and collapse whitespace."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", _USER_PROMPT_META_RE.sub("", text)).strip()


def _valid_step_index(obj: dict[str, Any]) -> Optional[int]:
    val = obj.get("step_index")
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    return None


def next_step_index(transcript_path: Optional[Union[str, os.PathLike[str]]]) -> int:
    """Return max(step_index) + 1 from transcript.jsonl, or 0 if missing/empty/unreadable."""
    entries = parse_transcript_steps(transcript_path)
    max_step = -1
    for obj in entries:
        step_val = _valid_step_index(obj)
        if step_val is not None and step_val > max_step:
            max_step = step_val
    return max(0, max_step + 1)


def current_turn_start_step(
    transcript_path: Optional[Union[str, os.PathLike[str]]],
    *,
    expected_prompt: Optional[str] = None,
) -> int:
    """Return the step_index of the current USER_INPUT turn anchor, falling back to next_step_index."""
    entries = parse_transcript_steps(transcript_path)
    if not entries:
        return 0

    max_step = -1
    for obj in entries:
        step_val = _valid_step_index(obj)
        if step_val is not None and step_val > max_step:
            max_step = step_val

    norm_expected = normalize_user_prompt(expected_prompt)
    if norm_expected:
        for obj in reversed(entries):
            if obj.get("type") == "USER_INPUT" and normalize_user_prompt(obj.get("content")) == norm_expected:
                step_val = _valid_step_index(obj)
                if step_val is not None:
                    return max(0, step_val)

    for obj in reversed(entries):
        stype = obj.get("type")
        if stype == "SYSTEM_MESSAGE":
            continue
        if stype == "PLANNER_RESPONSE":
            break
        if stype == "USER_INPUT":
            step_val = _valid_step_index(obj)
            if step_val is not None:
                return max(0, step_val)
            break
    return max(0, max_step + 1)
