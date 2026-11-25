#!/usr/bin/env python3
"""Helper CLI déterministe (0 token LLM) pour orchestrer les vagues Swarm multi-dépôts."""

import argparse
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

WAVE_PLAN_SCHEMA_VERSION = "wave_plan_v1"
BLOCKED_TASK_STATUSES = frozenset({"draft", "done", "archived", "cancelled"})
MAX_BUILDER_BRIEF_CHARS = 700
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_COMPACT_HYGIENE = (
    "Hygiène: WaitMsBeforeAsync=10000, NotificationTimeoutSeconds=180, "
    "PS ';', EndLine>=StartLine, 0 git commit/push/tag."
)


def _resolve_plugin_root() -> Path:
    here = Path(__file__).resolve().parent.parent
    if (here / "observability").is_dir() and (here / "workflows").is_dir():
        return here
    cwd_plugin = Path.cwd().resolve() / "plugin"
    return cwd_plugin if (cwd_plugin / "observability").is_dir() else here


PLUGIN_ROOT = _resolve_plugin_root()
for _sub in ("observability", "workflows"):
    _sub_dir = (PLUGIN_ROOT / _sub).resolve()
    if _sub_dir.is_dir() and str(_sub_dir) not in sys.path:
        sys.path.insert(0, str(_sub_dir))

from patch_apply import (  # type: ignore[import-not-found]  # noqa: E402
    BUILDER_HYGIENE_DIRECTIVE, MAX_TREE_FILES, _list_worktree_dirty_files,
    _read_worktree_patch, _safe_save_verified_patch, apply_verified_patch,
    caveman_directive, cleanup_worktree_best_effort, collect_changed_files,
    create_repo_worktree, extract_changed_files_from_diff,
    load_level_preset_for_ws, resolve_worktree_base,
)


def _sanitize_name(raw: str, fallback: str = "item") -> str:
    cleaned = _SAFE_NAME_RE.sub("-", str(raw or "").strip()).strip("-.").replace("..", "-")
    return cleaned or fallback


def _run_git(repo_or_wt: Path, args: Sequence[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_or_wt), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def _check_expected_branch(repo_root: Path, expected_branch: str) -> None:
    if not expected_branch:
        return
    proc = _run_git(repo_root, ("branch", "--show-current"), timeout=10)
    if proc.returncode != 0:
        raise ValueError(f"Vérification branche impossible sur {repo_root}: {proc.stderr.strip() or proc.returncode}")
    current = proc.stdout.strip()
    if current != expected_branch:
        raise ValueError(
            f"Branche attendue '{expected_branch}' sur {repo_root.name}, courante '{current or 'HEAD detached'}'."
        )


def _load_wave_plan(wave_plan: Any, ws_root: Path) -> Tuple[Dict[str, Any], Path]:
    if isinstance(wave_plan, (str, Path)):
        raw = str(wave_plan).strip()
        if not raw:
            raise ValueError("Chemin wave_plan vide.")
        p = Path(raw).expanduser()
        plan_path = p.resolve() if p.is_absolute() else (ws_root / p).resolve()
        if not plan_path.is_file():
            raise ValueError(f"wave_plan introuvable : {plan_path}")
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"wave_plan JSON illisible ({plan_path}): {exc}") from exc
    elif isinstance(wave_plan, dict):
        data = wave_plan
    else:
        raise ValueError("wave_plan invalide : chemin JSON ou dict attendu.")

    if not isinstance(data, dict) or data.get("schema_version") != WAVE_PLAN_SCHEMA_VERSION:
        got = data.get("schema_version") if isinstance(data, dict) else type(data).__name__
        raise ValueError(f"schema_version invalide : {got!r} (attendu {WAVE_PLAN_SCHEMA_VERSION!r}).")
    raw_ws = str(data.get("workspace") or "").strip()
    return data, (Path(raw_ws).expanduser().resolve() if raw_ws else ws_root)


def _validate_task(raw: Any, idx: int, ws_root: Path, default_level: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Tâche #{idx} invalide : dict attendu.")
    status = str(raw.get("status") or "").strip().lower()
    if status in BLOCKED_TASK_STATUSES:
        raise ValueError(f"Tâche #{idx} bloquée par statut '{status}'.")

    raw_repo = str(raw.get("repo_root") or "").strip()
    if not raw_repo:
        raise ValueError(f"Tâche #{idx} invalide : 'repo_root' requis.")
    cand_repo = Path(raw_repo).expanduser()
    repo_root = cand_repo.resolve() if cand_repo.is_absolute() else (ws_root / cand_repo).resolve()
    if not repo_root.is_dir() or not (repo_root / ".git").exists():
        raise ValueError(f"Tâche #{idx} invalide : dépôt git introuvable ({repo_root}).")

    expected_branch = str(raw.get("expected_branch") or "").strip()
    _check_expected_branch(repo_root, expected_branch)

    raw_plan = str(raw.get("plan_file") or raw.get("task_file") or raw.get("path") or "").strip()
    plan_path_str = ""
    if raw_plan:
        p = Path(raw_plan).expanduser()
        plan_path = p.resolve() if p.is_absolute() else (ws_root / p).resolve()
        if not plan_path.is_file() or not plan_path.read_text(encoding="utf-8", errors="replace").strip():
            raise ValueError(f"Tâche #{idx} invalide : plan_file absent ou vide ({plan_path}).")
        plan_path_str = str(plan_path)
    elif not str(raw.get("plan") or raw.get("task") or "").strip():
        raise ValueError(f"Tâche #{idx} invalide : 'plan_file' ou 'plan'/'task' requis.")

    task_id = str(raw.get("id") or (Path(plan_path_str).stem if plan_path_str else f"task-{idx}")).strip() or f"task-{idx}"
    repo_name = str(raw.get("repo_id") or "").strip() or repo_root.name
    raw_targets = raw.get("target_files") if isinstance(raw.get("target_files"), list) else []
    return {
        "id": task_id,
        "repo_name": repo_name,
        "repo_root": str(repo_root),
        "expected_branch": expected_branch,
        "plan_file": plan_path_str,
        "level": str(raw.get("profile") or raw.get("level") or default_level or "").strip().lower(),
        "target_files": [str(f).strip() for f in raw_targets if str(f).strip()],
        "status": status,
    }


def _build_builder_brief(task: Dict[str, Any], wt_path: Path, caveman_level: str) -> str:
    caveman_line = caveman_directive("builder", caveman_level).strip()
    plan_ref = task["plan_file"] or f"task:{task['id']}"
    header = (
        f"Tâche `{task['id']}` ({task['repo_name']}). "
        f"Édite UNIQUEMENT `{wt_path}` (jamais `{task['repo_root']}`). Plan: `{plan_ref}`. "
    )
    full = f"{header}{caveman_line} {BUILDER_HYGIENE_DIRECTIVE} Zéro git commit/push/tag.".strip()
    if len(full) <= MAX_BUILDER_BRIEF_CHARS:
        return full
    compact = f"{header}{caveman_line} {_COMPACT_HYGIENE}".strip()
    return compact if len(compact) <= MAX_BUILDER_BRIEF_CHARS else f"{header}{_COMPACT_HYGIENE}"[:MAX_BUILDER_BRIEF_CHARS]


def _compute_reviewer_topology(level: str, task_count: int, allow_pro: bool) -> Dict[str, Any]:
    if str(level or "standard").strip().lower() == "quick":
        return {"mode": "shared", "count": 1, "model": "flash", "monitor": False, "fork": False}
    return {
        "mode": "per_task", "count": max(1, int(task_count)),
        "model": "pro" if allow_pro else "flash", "monitor": False, "fork": False,
    }


def _save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _load_state(state_file: Any) -> Tuple[Path, Dict[str, Any]]:
    raw = str(state_file or "").strip()
    if not raw:
        raise ValueError("Chemin state JSON vide.")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Fichier state introuvable : {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError(f"Fichier state invalide : {path}")
    return path, data


def prepare_wave(
    wave_plan: Any,
    *,
    workspace_root: Optional[Path] = None,
    worktree_base: Optional[Path] = None,
    level_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Valide fail-closed `wave_plan_v1`, crée les worktrees détachés, briefs et état JSON."""
    ws_base = Path(workspace_root).expanduser().resolve() if workspace_root else Path.cwd().resolve()
    plan_data, eff_ws = _load_wave_plan(wave_plan, ws_base)
    raw_tasks = plan_data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("wave_plan_v1 invalide : 'tasks' doit être une liste non vide.")

    wave_id = str(plan_data.get("wave") or f"w-{uuid.uuid4().hex[:8]}").strip()
    wave_slug = _sanitize_name(wave_id, "wave")
    validated_tasks: List[Dict[str, Any]] = []
    seen_ids = set()
    for idx, item in enumerate(raw_tasks, start=1):
        spec = _validate_task(item, idx, eff_ws, level_override)
        if spec["id"] in seen_ids:
            raise ValueError(f"ID de tâche dupliqué : {spec['id']!r}")
        seen_ids.add(spec["id"])
        validated_tasks.append(spec)

    wt_base = resolve_worktree_base(worktree_base)
    created_worktrees: List[Tuple[Path, Path]] = []
    try:
        for spec in validated_tasks:
            repo_root = Path(spec["repo_root"])
            wt_path = create_repo_worktree(repo_root, wt_base, f"{wave_slug}-{_sanitize_name(spec['id'], 'task')}")
            created_worktrees.append((wt_path, repo_root))
            spec.update({"worktree_path": str(wt_path), "patch_path": "", "changed_files": [], "applied": False})

        preset = load_level_preset_for_ws(eff_ws, level_override)
        eff_level = str(level_override or preset.get("level") or "standard").strip().lower()
        caveman_level = str(preset.get("caveman") or "lite").strip().lower()
        allow_pro = eff_level != "quick" and str(preset.get("review_model") or "pro").strip().lower() == "pro"
        builder_briefs = {s["id"]: _build_builder_brief(s, Path(s["worktree_path"]), caveman_level) for s in validated_tasks}
        reviewer_topology = _compute_reviewer_topology(eff_level, len(validated_tasks), allow_pro)
        state_path = wt_base / f"swarm-wave-{wave_slug}.json"
        _save_state(state_path, {
            "schema_version": WAVE_PLAN_SCHEMA_VERSION, "wave_id": wave_id, "workspace": str(eff_ws),
            "worktree_base": str(wt_base), "state_file": str(state_path), "level": eff_level,
            "caveman": caveman_level, "allow_pro": allow_pro, "reviewer_topology": reviewer_topology,
            "tasks": validated_tasks,
        })
    except Exception:
        for wt_path, repo_root in created_worktrees:
            try:
                cleanup_worktree_best_effort(wt_path, repo_root)
            except Exception:
                pass
        raise

    return {
        "wave_id": wave_id, "state_file": str(state_path), "worktree_base": str(wt_base),
        "level": eff_level, "reviewer_topology": reviewer_topology,
        "worktrees": {s["id"]: s["worktree_path"] for s in validated_tasks},
        "builder_briefs": builder_briefs,
    }


def _select_tasks(all_tasks: List[Dict[str, Any]], task_ids: Optional[Sequence[str]]) -> List[Dict[str, Any]]:
    if not task_ids:
        return list(all_tasks)
    by_id = {str(t["id"]): t for t in all_tasks}
    selected: List[Dict[str, Any]] = []
    for tid in task_ids:
        key = str(tid).strip()
        if not key:
            continue
        if key not in by_id:
            raise ValueError(f"ID de tâche inconnu dans state : {key!r}")
        selected.append(by_id[key])
    if not selected:
        raise ValueError("Aucun ID de tâche valide fourni.")
    return selected


def extract_wave_diffs(state_file: Any, *, task_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Exécute `git add -N` sur les untracked, extrait les patches et génère les `reviewer_briefs` à froid."""
    state_path, state = _load_state(state_file)
    selected = _select_tasks(state["tasks"], task_ids)
    wave_slug = _sanitize_name(str(state.get("wave_id") or "wave"), "wave")
    patches_dir = state_path.parent / f"swarm-wave-{wave_slug}-patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    caveman_rev = caveman_directive("reviewer", str(state.get("caveman") or "lite")).strip()

    diffs_out: List[Dict[str, Any]] = []
    reviewer_briefs: Dict[str, str] = {}
    for task in selected:
        tid = str(task["id"])
        wt_path = Path(task["worktree_path"])
        if not wt_path.is_dir():
            raise FileNotFoundError(f"Worktree introuvable pour '{tid}' : {wt_path}")
        diff_text = _read_worktree_patch(
            str(wt_path), "", task.get("target_files"), stage_untracked=True
        )
        dirty_files = _list_worktree_dirty_files(wt_path)
        extracted_files = extract_changed_files_from_diff(diff_text, max_items=MAX_TREE_FILES * 10)
        if dirty_files and (not diff_text.strip() or not extracted_files):
            raise RuntimeError(
                f"Diff vide ou incomplet pour '{tid}' malgré des changements worktree : {dirty_files}"
            )
        changed_files = collect_changed_files(diff_text, task.get("target_files"))
        if dirty_files and not changed_files:
            raise RuntimeError(f"changed_files vide pour '{tid}' malgré des changements worktree : {dirty_files}")
        patch_file = patches_dir / f"{_sanitize_name(tid, 'task')}.patch"
        normalized = diff_text.replace("\r\n", "\n").replace("\r", "\n")
        if normalized and not normalized.endswith("\n"):
            normalized += "\n"
        patch_file.write_text(normalized, encoding="utf-8", newline="\n")
        if normalized.strip():
            _safe_save_verified_patch(normalized, str(wt_path), Path(task["repo_root"]))

        task["patch_path"] = str(patch_file)
        task["changed_files"] = changed_files
        plan_ref = task.get("plan_file") or f"task:{tid}"
        reviewer_briefs[tid] = (
            f"Revue à froid (0 fork) tâche `{tid}` ({task['repo_name']}). "
            f"Audite explicitement le fichier `.patch` extrait (`{patch_file}`), pas seulement l'arbre du worktree. "
            f"Plan: `{plan_ref}`. Verdict PASS/FAIL + BLOCKER/MAJOR/NIT. {caveman_rev}"
        ).strip()
        diffs_out.append({
            "id": tid, "repo_name": task["repo_name"], "patch_path": str(patch_file),
            "changed_files": changed_files, "has_diff": bool(normalized.strip()),
        })

    _save_state(state_path, state)
    return {
        "wave_id": state.get("wave_id", ""), "state_file": str(state_path),
        "reviewer_topology": state.get("reviewer_topology", {}),
        "diffs": diffs_out, "reviewer_briefs": reviewer_briefs,
    }


def apply_wave(state_file: Any, pass_ids: Sequence[str], *, cleanup_worktree: bool = True) -> Dict[str, Any]:
    """Applique sélectivement les patches des tâches `PASS` (matching strict sur `id`) après re-vérif branche."""
    state_path, state = _load_state(state_file)
    if not pass_ids:
        raise ValueError("apply_wave requiert au moins un ID de tâche (--tasks / --pass-ids).")
    selected = _select_tasks(state["tasks"], pass_ids)

    results: List[Dict[str, Any]] = []
    all_ok = True
    for task in selected:
        tid = str(task["id"])
        repo_root, wt_path = Path(task["repo_root"]), Path(task["worktree_path"])
        patch_raw = str(task.get("patch_path") or "").strip()
        error_msg, applied = "", False
        try:
            _check_expected_branch(repo_root, str(task.get("expected_branch") or ""))
            if not patch_raw or not Path(patch_raw).is_file():
                raise FileNotFoundError(f"Patch introuvable pour '{tid}' : {patch_raw}")
            applied = apply_verified_patch(
                repo_root, Path(patch_raw), worktree_path=wt_path, cleanup_worktree=cleanup_worktree,
            )
            if not applied:
                error_msg = f"apply_verified_patch a échoué pour '{tid}'."
        except Exception as exc:
            error_msg, applied = f"{type(exc).__name__}: {exc}", False

        task["applied"] = applied
        all_ok = all_ok and applied
        results.append({"id": tid, "repo_name": task["repo_name"], "applied": applied, "error": error_msg})

    _save_state(state_path, state)
    return {
        "wave_id": state.get("wave_id", ""), "state_file": str(state_path),
        "all_applied": all_ok, "applied_count": sum(1 for r in results if r["applied"]),
        "total_requested": len(results), "results": results,
    }


def cleanup_wave(state_file: Any, *, remove_state: bool = False) -> Dict[str, Any]:
    """Nettoie de façon idempotente tous les worktrees de la vague."""
    raw = str(state_file or "").strip()
    if not raw:
        raise ValueError("Chemin state JSON vide.")
    state_path = Path(raw).expanduser().resolve()
    if not state_path.is_file():
        return {"wave_id": "", "state_file": str(state_path), "cleaned": [], "already_missing": True}

    _, state = _load_state(state_path)
    cleaned: List[Dict[str, Any]] = []
    for task in state.get("tasks", []):
        wt_raw, repo_raw = str(task.get("worktree_path") or "").strip(), str(task.get("repo_root") or "").strip()
        ok = False
        if wt_raw and repo_raw:
            try:
                ok = cleanup_worktree_best_effort(Path(wt_raw), Path(repo_raw))
            except Exception:
                ok = False
        cleaned.append({"id": str(task.get("id") or ""), "cleaned": ok})

    if remove_state:
        try:
            state_path.unlink(missing_ok=True)
        except OSError:
            pass
    return {"wave_id": state.get("wave_id", ""), "state_file": str(state_path), "cleaned": cleaned, "already_missing": False}


def _parse_id_list(values: Optional[List[str]]) -> List[str]:
    return [part.strip() for val in (values or []) for part in str(val).split(",") if part.strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Orchestration déterministe de vagues Swarm (wave_plan_v1).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help="Valide wave_plan_v1, crée les worktrees et briefs.")
    p_prep.add_argument("--wave-plan", required=True)
    p_prep.add_argument("--workspace", default=None)
    p_prep.add_argument("--worktree-base", default=None)
    p_prep.add_argument("--level", default=None, choices=["quick", "standard", "deep"])
    p_prep.add_argument("--json", action="store_true")

    p_diff = sub.add_parser("diff", help="Extrait les patches unifiés et briefs reviewer.")
    p_diff.add_argument("--state", required=True)
    p_diff.add_argument("--tasks", nargs="*", default=None)
    p_diff.add_argument("--json", action="store_true")

    p_apply = sub.add_parser("apply", help="Applique sélectivement les patches PASS sur dev.")
    p_apply.add_argument("--state", required=True)
    p_apply.add_argument("--tasks", "--pass-ids", dest="tasks", nargs="+", required=True)
    p_apply.add_argument("--no-cleanup", action="store_true")
    p_apply.add_argument("--json", action="store_true")

    p_clean = sub.add_parser("cleanup", help="Nettoie de façon idempotente les worktrees de la vague.")
    p_clean.add_argument("--state", required=True)
    p_clean.add_argument("--remove-state", action="store_true")
    p_clean.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            res = prepare_wave(
                args.wave_plan,
                workspace_root=Path(args.workspace) if args.workspace else None,
                worktree_base=Path(args.worktree_base) if args.worktree_base else None,
                level_override=args.level,
            )
        elif args.command == "diff":
            res = extract_wave_diffs(args.state, task_ids=_parse_id_list(args.tasks) or None)
        elif args.command == "apply":
            res = apply_wave(args.state, _parse_id_list(args.tasks), cleanup_worktree=not args.no_cleanup)
        else:
            res = cleanup_wave(args.state, remove_state=bool(args.remove_state))
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 1 if (args.command == "apply" and not res.get("all_applied", False)) else 0
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
