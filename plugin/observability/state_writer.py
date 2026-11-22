#!/usr/bin/env python3
"""Écrivain atomique pour .agents/gravity-workflow/observability/live_state.json (100 % stdlib Python 3)."""

import contextlib
import datetime
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]

def default_workflow_home() -> Path:
    """Résout le dossier centralisé Gravity Workflow ($GRAVITY_WORKFLOW_HOME > $GRAVITY_HOME/workflow > ~/.gemini/gravity/workflow)."""
    env_wf = os.environ.get("GRAVITY_WORKFLOW_HOME", "").strip()
    if env_wf:
        return Path(env_wf).expanduser().resolve()
    env_gravity = os.environ.get("GRAVITY_HOME", "").strip()
    if env_gravity:
        return (Path(env_gravity).expanduser().resolve() / "workflow").resolve()
    return (Path.home() / ".gemini" / "gravity" / "workflow").resolve()


DEFAULT_STATE_PATH = Path(".agents/gravity-workflow/observability/live_state.json")
_DEFAULT_REGISTRY_SENTINEL = default_workflow_home() / "live_workspaces.json"
REGISTRY_PATH = _DEFAULT_REGISTRY_SENTINEL
MAX_EVENTS = 50
MAX_WORKSPACES = 20


def resolve_registry_path(registry_path: Optional[Path] = None) -> Path:
    """Résout live_workspaces.json via arg, monkeypatch REGISTRY_PATH, $GRAVITY_WORKFLOW_REGISTRY_PATH ou default_workflow_home()."""
    if registry_path is not None:
        return Path(registry_path).expanduser().resolve()
    if REGISTRY_PATH is not None and REGISTRY_PATH != _DEFAULT_REGISTRY_SENTINEL:
        return Path(REGISTRY_PATH).expanduser()
    env_reg = os.environ.get("GRAVITY_WORKFLOW_REGISTRY_PATH", "").strip()
    if env_reg:
        return Path(env_reg).expanduser().resolve()
    return (default_workflow_home() / "live_workspaces.json").resolve()

LOCK_RETRIES = 50
LOCK_RETRY_DELAY_S = 0.05


@contextlib.contextmanager
def exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    """Verrou exclusif multi-plateforme (fcntl.flock sur POSIX, msvcrt.locking sur Windows)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        if fcntl is not None:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            lock_fh.seek(0, os.SEEK_END)
            if lock_fh.tell() == 0:
                lock_fh.write("0")
                lock_fh.flush()
            acquired = False
            for _ in range(LOCK_RETRIES):
                try:
                    lock_fh.seek(0)
                    msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    time.sleep(LOCK_RETRY_DELAY_S)
            if not acquired:
                raise TimeoutError(f"Impossible d'acquérir le verrou sur {lock_path}")
            try:
                yield
            finally:
                try:
                    lock_fh.seek(0)
                    msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            yield


def _resolve_workspace_root(workspace_root: Optional[Path] = None) -> Path:
    if workspace_root is not None:
        return Path(workspace_root).resolve()
    env_ws = os.environ.get("GRAVITY_WORKFLOW_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).resolve()
    return Path.cwd().resolve()


def resolve_state_path(
    state_path: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> Path:
    if state_path is not None:
        return Path(state_path)
    env_path = os.environ.get("GRAVITY_WORKFLOW_STATE_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return _resolve_workspace_root(workspace_root) / DEFAULT_STATE_PATH


def _atomic_write_json(target: Path, payload: Dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.stem}_", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
            json.dump(payload, tmp_fh, indent=2, ensure_ascii=False)
            tmp_fh.write("\n")
            tmp_fh.flush()
            os.fsync(tmp_fh.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_name, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _register_active_workspace(ws_root: Path, state_file: Path) -> None:
    """Enregistre le workspace actif dans live_workspaces.json pour le Sidecar."""
    if os.environ.get("GRAVITY_WORKFLOW_SKIP_REGISTRY") == "1":
        return
    try:
        reg_path = resolve_registry_path()
        reg_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = reg_path.with_suffix(".lock")
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with exclusive_file_lock(lock_path):
            entries: List[Dict[str, str]] = []
            if reg_path.is_file():
                try:
                    raw = json.loads(reg_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and isinstance(raw.get("workspaces"), list):
                        entries = [
                            e
                            for e in raw["workspaces"]
                            if isinstance(e, dict) and isinstance(e.get("state_path"), str)
                        ]
                except Exception:
                    entries = []
            resolved_state = str(state_file.resolve())
            resolved_ws = str(ws_root.resolve())
            filtered = [e for e in entries if e.get("state_path") != resolved_state]
            filtered.insert(
                0,
                {
                    "workspace": resolved_ws,
                    "state_path": resolved_state,
                    "updated_at": now_iso,
                },
            )
            _atomic_write_json(reg_path, {"workspaces": filtered[:MAX_WORKSPACES]})
    except Exception:
        pass


def _default_state() -> Dict[str, Any]:
    return {
        "stage": "idle",
        "progress": "0/8",
        "mode": "evo",
        "level": "medium",
        "pace": "slow",
        "verification": "safe",
        "caveman": "lite",
        "caveman_locked": False,
        "guard_status": "ON",
        "nodes": {"planner": "", "builder": "", "reviewer": ""},
        "events": [],
    }


def update_live_state(
    *,
    stage: Optional[str] = None,
    progress: Optional[str] = None,
    mode: Optional[str] = None,
    level: Optional[str] = None,
    pace: Optional[str] = None,
    verification: Optional[str] = None,
    caveman: Optional[str] = None,
    caveman_locked: Optional[bool] = None,
    guard_status: Optional[str] = None,
    nodes: Optional[Dict[str, str]] = None,
    event: Optional[Dict[str, Any]] = None,
    state_path: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> None:
    """Met à jour live_state.json de façon atomique et tolérante aux accès concurrents."""
    ws_root = _resolve_workspace_root(workspace_root)
    target = resolve_state_path(state_path, workspace_root=ws_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(".lock")

    with exclusive_file_lock(lock_path):
        state = _default_state()
        if target.is_file():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    state.update(loaded)
            except Exception:
                pass

        if stage is not None:
            state["stage"] = stage
        if progress is not None:
            state["progress"] = progress
        if mode is not None:
            state["mode"] = mode
        if level is not None:
            state["level"] = level
        if pace is not None:
            state["pace"] = pace
        if verification is not None:
            state["verification"] = verification
        if caveman is not None:
            state["caveman"] = caveman
        if caveman_locked is not None:
            state["caveman_locked"] = bool(caveman_locked)
        if guard_status is not None:
            state["guard_status"] = guard_status
        if nodes:
            merged_nodes = dict(state.get("nodes") or {})
            merged_nodes.update(nodes)
            state["nodes"] = merged_nodes
        if event is not None:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            ev = dict(event)
            ev.setdefault("ts", now_utc.strftime("%H:%M:%S"))
            ev.setdefault("ts_iso", now_utc.isoformat())
            events = list(state.get("events") or [])
            events.append(ev)
            state["events"] = events[-MAX_EVENTS:]

        _atomic_write_json(target, state)

    _register_active_workspace(ws_root, target)
