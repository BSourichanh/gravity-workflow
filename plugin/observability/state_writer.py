#!/usr/bin/env python3
"""Écrivain atomique pour live_state.json et stockage centralisé Gravity Workflow (100 % stdlib Python 3)."""

import contextlib
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from _shared.atomic_io import append_jsonl_atomic, write_json_atomic  # noqa: E402
from _shared.file_lock import exclusive_file_lock as _shared_exclusive_file_lock  # noqa: E402


def resolve_workflow_home() -> Path:
    """Résout le dossier centralisé Gravity Workflow ($GRAVITY_WORKFLOW_HOME > $GRAVITY_HOME/workflow > ~/.gemini/gravity/workflow)."""
    env_wf = os.environ.get("GRAVITY_WORKFLOW_HOME", "").strip()
    if env_wf:
        return Path(env_wf).expanduser().resolve()
    env_gravity = os.environ.get("GRAVITY_HOME", "").strip()
    if env_gravity:
        return (Path(env_gravity).expanduser().resolve() / "workflow").resolve()
    return (Path.home() / ".gemini" / "gravity" / "workflow").resolve()


def default_workflow_home() -> Path:
    """Alias conservé pour rétrocompatibilité vers `resolve_workflow_home()`."""
    return resolve_workflow_home()


DEFAULT_STATE_PATH = Path(".agents/gravity-workflow/observability/live_state.json")
_DEFAULT_REGISTRY_SENTINEL = resolve_workflow_home() / "live_workspaces.json"
REGISTRY_PATH = _DEFAULT_REGISTRY_SENTINEL
MAX_EVENTS = 50
MAX_WORKSPACES = 20
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def resolve_registry_path(registry_path: Optional[Path] = None) -> Path:
    """Résout live_workspaces.json via arg, monkeypatch REGISTRY_PATH, $GRAVITY_WORKFLOW_REGISTRY_PATH ou resolve_workflow_home()."""
    if registry_path is not None:
        return Path(registry_path).expanduser().resolve()
    if REGISTRY_PATH is not None and REGISTRY_PATH != _DEFAULT_REGISTRY_SENTINEL:
        return Path(REGISTRY_PATH).expanduser()
    env_reg = os.environ.get("GRAVITY_WORKFLOW_REGISTRY_PATH", "").strip()
    if env_reg:
        return Path(env_reg).expanduser().resolve()
    return (resolve_workflow_home() / "live_workspaces.json").resolve()

@contextlib.contextmanager
def exclusive_file_lock(
    lock_path: Path,
    timeout_s: float = 5.0,
    poll_interval_s: float = 0.02,
) -> Iterator[None]:
    """Verrou exclusif multi-plateforme fail-closed délégué à `_shared.file_lock`."""
    with _shared_exclusive_file_lock(
        lock_path,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    ):
        yield


def _resolve_workspace_root(workspace_root: Optional[Path] = None) -> Path:
    if workspace_root is not None:
        return Path(workspace_root).resolve()
    env_ws = os.environ.get("GRAVITY_WORKFLOW_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).resolve()
    return Path.cwd().resolve()


def workspace_slug_hash(workspace_root: Optional[Path] = None) -> str:
    """Calcule un identifiant stable `<slug>-<sha256_8>` pour le workspace cible."""
    resolved = _resolve_workspace_root(workspace_root)
    norm = os.path.normcase(str(resolved))
    slug = _SLUG_RE.sub("-", resolved.name.lower()).strip("-")[:32] or "workspace"
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def resolve_workspace_storage_dir(workspace_root: Optional[Path] = None) -> Path:
    """Retourne le dossier centralisé du workspace sous `resolve_workflow_home() / workspaces / <slug>-<sha256_8>`."""
    ws_root = _resolve_workspace_root(workspace_root)
    return (resolve_workflow_home() / "workspaces" / workspace_slug_hash(ws_root)).resolve()


def resolve_profile_storage_path(workspace_root: Optional[Path] = None) -> Path:
    """Retourne le chemin centralisé de `profile.json` pour un workspace."""
    return resolve_workspace_storage_dir(workspace_root) / "profile.json"


def resolve_runs_log_path(workspace_root: Optional[Path] = None) -> Path:
    """Retourne le chemin centralisé de `runs.jsonl` pour un workspace."""
    return resolve_workspace_storage_dir(workspace_root) / "runs.jsonl"


def resolve_patch_path(workspace_root: Optional[Path] = None) -> Path:
    """Retourne le chemin centralisé de `last_verified.patch` pour un workspace."""
    return resolve_workspace_storage_dir(workspace_root) / "last_verified.patch"


def resolve_eval_dir(workspace_root: Optional[Path] = None) -> Path:
    """Retourne le dossier centralisé `evals/` pour les rapports d'évaluation d'un workspace."""
    return resolve_workspace_storage_dir(workspace_root) / "evals"


def resolve_state_path(
    state_path: Optional[Path] = None,
    workspace_root: Optional[Path] = None,
) -> Path:
    if state_path is not None:
        return Path(state_path)
    env_path = os.environ.get("GRAVITY_WORKFLOW_STATE_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return resolve_workspace_storage_dir(workspace_root) / "live_state.json"


def _atomic_write_json(target: Path, payload: Dict[str, Any]) -> None:
    write_json_atomic(
        target,
        payload,
        indent=2,
        sort_keys=False,
        ensure_ascii=False,
        reject_symlink=True,
    )


def _register_active_workspace(ws_root: Path, state_file: Path) -> None:
    """Enregistre le workspace actif dans live_workspaces.json pour le dashboard gravity Core."""
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
                    "workspace_path": resolved_ws,
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
        "progress": "0/0",
        "mode": "evo",
        "level": "standard",
        "preset_profile": "standard",
        "suggested_profile": "standard",
        "suggested_reason": "",
        "role_models": {"plan": "flash", "build": "flash", "review": "pro"},
        "caveman": "lite",
        "caveman_locked": False,
        "guard_status": "ON",
        "nodes": {"planner": "", "builder": "", "reviewer": ""},
        "events": [],
    }


def append_run_log(
    workspace_root: Optional[Path],
    entry: Dict[str, Any],
    *,
    log_path: Optional[Path] = None,
) -> Path:
    """Ajoute de façon atomique et verrouillée une ligne JSON dans `runs.jsonl` centralisé."""
    ws_root = _resolve_workspace_root(workspace_root)
    target = Path(log_path) if log_path is not None else resolve_runs_log_path(ws_root)
    append_jsonl_atomic(
        target,
        entry,
        lock_path=target.with_suffix(".lock"),
        ensure_ascii=False,
        reject_symlink=True,
    )
    return target


def update_live_state(
    *,
    stage: Optional[str] = None,
    progress: Optional[str] = None,
    mode: Optional[str] = None,
    level: Optional[str] = None,
    preset_profile: Optional[str] = None,
    suggested_profile: Optional[str] = None,
    suggested_reason: Optional[str] = None,
    role_models: Optional[Dict[str, str]] = None,
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

        state.pop("pace", None)
        state.pop("verification", None)

        if stage is not None:
            state["stage"] = stage
        if progress is not None:
            state["progress"] = progress
        if mode is not None:
            state["mode"] = mode
        if level is not None:
            state["level"] = level
        if preset_profile is not None:
            state["preset_profile"] = preset_profile
        if suggested_profile is not None:
            state["suggested_profile"] = suggested_profile
        if suggested_reason is not None:
            state["suggested_reason"] = suggested_reason
        if role_models is not None:
            state["role_models"] = dict(role_models)
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
