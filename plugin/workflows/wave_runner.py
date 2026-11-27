"""Runner de vagues natif Gravity pour `run_workflow` (100 % stdlib Python 3.10+, fail-isolated)."""

import asyncio
import datetime
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_AGENTS_PROMPT_CHARS = 4000
MAX_SUMMARY_CHARS = 200
MAX_PROMPT_DIFF_CHARS = 60000

WAVE_PLAN_SCHEMA_VERSION = "wave_plan_v1"
BLOCKED_TASK_STATUSES = frozenset({"draft", "done", "archived", "cancelled"})

_TITLE_RE = re.compile(r"^\s*#\s+(.+)$", re.MULTILINE)

try:
    _WORKSPACES = WORKSPACES  # type: ignore[name-defined]
except NameError:
    _WORKSPACES = None

try:
    _ARGS: Dict[str, Any] = args if isinstance(args, dict) else {}  # type: ignore[name-defined]
except NameError:
    _ARGS = {}

WS_ROOT = Path(_WORKSPACES[0]).resolve() if _WORKSPACES else Path.cwd().resolve()


def _log_warn(message: str) -> None:
    try:
        log(message)  # type: ignore[name-defined]
    except NameError:
        print(message, file=sys.stderr)


def _is_workflow_runtime() -> bool:
    try:
        _ = agent  # type: ignore[name-defined]
        _ = phase  # type: ignore[name-defined]
        _ = parallel  # type: ignore[name-defined]
        return True
    except NameError:
        return False


def _resolve_plugin_root() -> Path:
    candidates: List[Path] = []
    try:
        dunder_file = __file__
    except NameError:
        dunder_file = None
    if isinstance(dunder_file, str) and dunder_file.strip():
        candidates.append(Path(dunder_file).resolve().parent.parent)
    arg_root = _ARGS.get("plugin_root")
    if isinstance(arg_root, str) and arg_root.strip():
        candidates.append(Path(arg_root).expanduser().resolve())
    candidates.extend(
        [
            WS_ROOT / "plugin",
            WS_ROOT / ".agents" / "plugins" / "gravity-workflow",
            Path.home() / ".gemini" / "config" / "plugins" / "gravity-workflow",
        ]
    )
    for cand in candidates:
        if (cand / "observability").is_dir() and (cand / "workflows").is_dir():
            return cand
    _log_warn(
        "[WARN] PLUGIN_ROOT introuvable (observability/ + workflows/ absents) parmi : "
        + ", ".join(str(c) for c in candidates)
    )
    return candidates[0] if candidates else WS_ROOT


PLUGIN_ROOT = _resolve_plugin_root()
for _sub in ("observability", "workflows"):
    _sub_dir = (PLUGIN_ROOT / _sub).resolve()
    if _sub_dir.is_dir() and str(_sub_dir) not in sys.path:
        sys.path.insert(0, str(_sub_dir))

from patch_apply import (  # type: ignore[import-not-found]  # noqa: E402
    BUILDER_HYGIENE_DIRECTIVE, BUILD_SCHEMA, DEFAULT_BUILDER_TIMEOUT_S,
    DEFAULT_REVIEWER_TIMEOUT_S, MAX_CHANGED_FILES, REVIEW_SCHEMA,
    _read_worktree_patch, _safe_save_verified_patch, apply_verified_patch,
    caveman_directive, collect_changed_files, collect_repo_tree,
    load_level_preset_for_ws, read_plan_file, resolve_timeout_seconds,
    safe_append_run, safe_update_live,
)
from worktree_ops import (  # type: ignore[import-not-found]  # noqa: E402
    _run_git, cleanup_worktree_best_effort, create_repo_worktree, resolve_worktree_base,
)


def _with_directive(prompt: str, directive: str) -> str:
    return f"{prompt}\n\n{directive}" if directive else prompt


def _resolve_repo_root(raw_repo_root: Any, ws_root: Path) -> Path:
    if not isinstance(raw_repo_root, (str, Path)) or not str(raw_repo_root).strip():
        raise ValueError("Tâche invalide : 'repo_root' explicite requis.")
    cand = Path(str(raw_repo_root).strip()).expanduser()
    repo_root = cand.resolve() if cand.is_absolute() else (ws_root / cand).resolve()
    if not repo_root.is_dir() or not (repo_root / ".git").exists():
        raise ValueError(f"Tâche invalide : 'repo_root' absent ou non-git ({repo_root}).")
    return repo_root


def parse_task_spec(
    raw_item: Any,
    ws_root: Path,
    *,
    default_level: Any = None,
) -> Dict[str, Any]:
    """Valide une tâche structurée (`wave_plan_v1` / dict) et lit `plan_file` + `<repo>/AGENTS.md` (0 token LLM)."""
    if not isinstance(raw_item, dict):
        raise ValueError("Tâche invalide : dict structuré avec 'repo_root' requis (chaîne brute refusée).")
    overrides: Dict[str, Any] = dict(raw_item)

    status_raw = str(overrides.get("status") or "").strip().lower()
    if status_raw in BLOCKED_TASK_STATUSES:
        raise ValueError(f"Tâche bloquée par statut '{status_raw}'.")

    repo_root = _resolve_repo_root(overrides.get("repo_root"), ws_root)

    plan_file_raw = str(
        overrides.get("plan_file") or overrides.get("task_file") or overrides.get("path") or ""
    ).strip()

    task_path: Optional[Path] = None
    if plan_file_raw:
        p = Path(plan_file_raw).expanduser()
        task_path = p.resolve() if p.is_absolute() else (ws_root / p).resolve()
        plan_text = read_plan_file(task_path)
    else:
        plan_text = str(overrides.get("plan") or overrides.get("task") or "").strip()
        if not plan_text:
            raise ValueError("Tâche invalide : 'plan_file' ou 'plan'/'task' requis.")

    title_match = _TITLE_RE.search(plan_text)
    title = str(overrides.get("title") or "").strip() or (
        title_match.group(1).strip() if title_match else (task_path.stem if task_path else str(overrides.get("id") or "task"))
    )
    expected_branch = str(overrides.get("expected_branch") or "").strip()
    raw_level = overrides.get("profile") or overrides.get("level") or default_level
    repo_id = str(overrides.get("repo_id") or "").strip()
    repo_name = repo_id or repo_root.name

    target_files_raw = overrides.get("target_files") if isinstance(overrides.get("target_files"), list) else overrides.get("code_files")
    target_files = [str(f).strip() for f in target_files_raw if str(f).strip()] if isinstance(target_files_raw, list) else []

    agents_text = ""
    agents_file = repo_root / "AGENTS.md"
    if agents_file.is_file():
        agents_text = agents_file.read_text(encoding="utf-8", errors="replace").strip()[:MAX_AGENTS_PROMPT_CHARS]

    return {
        "id": str(overrides.get("id") or (task_path.stem if task_path else "")).strip(),
        "title": title,
        "plan_file": str(task_path) if task_path else "",
        "plan_text": plan_text,
        "repo_id": repo_id,
        "repo_name": repo_name,
        "repo_root": repo_root,
        "expected_branch": expected_branch,
        "level": raw_level,
        "target_files": target_files,
        "status": status_raw,
        "agents_text": agents_text,
        "overrides": overrides,
    }


def load_wave_plan(raw_wave_plan: Any, ws_root: Path) -> Tuple[List[Any], Path, str]:
    """Charge et valide un manifeste `wave_plan_v1` (dict ou chemin fichier JSON)."""
    if isinstance(raw_wave_plan, (str, Path)):
        raw_path = str(raw_wave_plan).strip()
        if not raw_path:
            raise ValueError("wave_plan invalide : chemin JSON vide.")
        p = Path(raw_path).expanduser()
        plan_path = p.resolve() if p.is_absolute() else (ws_root / p).resolve()
        if not plan_path.is_file():
            raise ValueError(f"wave_plan introuvable : {plan_path}")
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"wave_plan JSON illisible ({plan_path}): {exc}") from exc
    elif isinstance(raw_wave_plan, dict):
        plan_data = raw_wave_plan
    else:
        raise ValueError("wave_plan invalide : dict ou chemin JSON attendu.")

    if not isinstance(plan_data, dict):
        raise ValueError("wave_plan invalide : objet JSON attendu.")

    schema_version = plan_data.get("schema_version")
    if schema_version != WAVE_PLAN_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version wave_plan invalide : {schema_version!r} (attendu {WAVE_PLAN_SCHEMA_VERSION!r})."
        )

    plan_ws_raw = str(plan_data.get("workspace") or "").strip()
    effective_ws = Path(plan_ws_raw).expanduser().resolve() if plan_ws_raw else ws_root

    tasks_raw = plan_data.get("tasks")
    if not isinstance(tasks_raw, list):
        raise ValueError("wave_plan_v1 invalide : 'tasks' doit être une liste.")

    wave_label = str(plan_data.get("wave") or "").strip()
    return list(tasks_raw), effective_ws, wave_label


def resolve_wave_tasks(global_args: Dict[str, Any], ws_root: Path) -> Tuple[List[Dict[str, Any]], Path, str]:
    """Résout et valide fail-closed les tâches depuis `args['wave_plan']` ou `args['tasks']`."""
    default_level = global_args.get("level")
    if "wave_plan" in global_args and global_args.get("wave_plan") is not None:
        raw_items, effective_ws, wave_label = load_wave_plan(global_args["wave_plan"], ws_root)
    else:
        raw_tasks_arg = global_args.get("tasks")
        if raw_tasks_arg is None:
            raw_items = []
        elif isinstance(raw_tasks_arg, list):
            raw_items = list(raw_tasks_arg)
        else:
            raise ValueError("args['tasks'] invalide : liste de dicts structurés attendue.")
        effective_ws = ws_root
        wave_label = ""

    validated_specs: List[Dict[str, Any]] = []
    for item in raw_items:
        validated_specs.append(parse_task_spec(item, effective_ws, default_level=default_level))
    return validated_specs, effective_ws, wave_label


def _check_expected_branch(repo_root: Path, expected_branch: str) -> Optional[str]:
    if not expected_branch:
        return None
    try:
        proc = _run_git(repo_root, ("branch", "--show-current"), timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"Vérification branche impossible sur {repo_root}: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        return f"Vérification branche impossible sur {repo_root}: {detail}"
    current = proc.stdout.strip()
    if not current or current != expected_branch:
        return f"Branche attendue '{expected_branch}', courante '{current or 'HEAD detached'}'."
    return None


def _build_repo_result(
    repo_name: str,
    *,
    status: str,
    profile: str = "standard",
    applied: bool = False,
    remediations: int = 0,
    changed_files: Optional[List[str]] = None,
    summary: str = "",
) -> Dict[str, Any]:
    return {
        "repo": repo_name,
        "status": status,
        "profile": profile,
        "applied": bool(applied),
        "remediations": int(remediations),
        "changed_files": list(changed_files or [])[:MAX_CHANGED_FILES],
        "summary": " ".join(str(summary or "").split())[:MAX_SUMMARY_CHARS],
    }


def _build_initial_prompt(spec: Dict[str, Any], wt_path: Path, ws_root: Path, skip_verification: bool) -> str:
    agents_block = f"\nInvariants `{spec['repo_name']}/AGENTS.md` :\n{spec['agents_text']}\n" if spec["agents_text"] else ""
    target_block = f"Fichiers cibles déclarés : {', '.join(spec['target_files'])}\n" if spec.get("target_files") else ""
    verify_note = (
        "Vérifications ignorées (--no-verify éphémère)."
        if skip_verification
        else "Exécute les vérifications/tests obligatoires du dépôt dans le worktree et consigne commandes + durées dans `validation_summary`."
    )
    return (
        f"Travaille EXCLUSIVEMENT dans le worktree isolé `{wt_path}` (n'édite jamais `{spec['repo_root']}` ni `{ws_root}`).\n"
        f"{target_block}"
        f"Plan issu de `{spec['plan_file'] or 'args'}` :\n{spec['plan_text']}\n"
        f"{agents_block}\n{BUILDER_HYGIENE_DIRECTIVE}\n"
        "Zéro `git commit`, `git push`, `git tag` ni release. "
        f"{verify_note} "
        f"Exécute `git -C \"{wt_path}\" add -N .` puis "
        f"`git -C \"{wt_path}\" diff --output=\"{wt_path / '.gravity_verified.patch'}\" -- . \":(exclude).gravity_verified.patch\"`, "
        f"et retourne `workspace_path` (`{wt_path}`), `modified_files`, `git_diff`, `validation_summary`, `refused`, `summary`."
    )


async def _execute_in_worktree(
    spec: Dict[str, Any],
    wt_path: Path,
    *,
    ws_root: Path,
    preset_profile: str,
    build_model: str,
    review_model: str,
    caveman_level: str,
    skip_verification: bool,
    max_remediations: int,
    apply_on_pass: bool,
    builder_timeout_s: float,
    reviewer_timeout_s: float,
) -> Tuple[str, bool, int, bool, List[str], str]:
    repo_root: Path = spec["repo_root"]
    repo_name: str = spec["repo_name"]
    builder_cav = caveman_directive("builder", caveman_level, log_fn=_log_warn)
    reviewer_cav = caveman_directive("reviewer", caveman_level, log_fn=_log_warn)
    builder = agent(role=f"Builder {repo_name}", type_name="builder", workspace="inherit", model=build_model)  # type: ignore[name-defined]

    final_status = "ERROR"
    remediation_count = 0
    applied = False
    refused_flag = False
    changed_files: List[str] = []
    summary_text = ""

    try:
        build = await asyncio.wait_for(
            builder(
                _with_directive(_build_initial_prompt(spec, wt_path, ws_root, skip_verification), builder_cav),
                BUILD_SCHEMA,
            ),
            timeout=builder_timeout_s,
        )
        if build.get("refused"):
            return "REFUSED", False, 0, True, [], str(build.get("summary") or "Refus sécurité Builder")

        worktree_ws = str(wt_path)
        diff_full = _read_worktree_patch(
            worktree_ws, str(build.get("git_diff", "")), build.get("modified_files"),
            max_prompt_diff_chars=MAX_PROMPT_DIFF_CHARS, log_fn=_log_warn,
        )
        changed_files = collect_changed_files(diff_full, build.get("modified_files"), max_items=MAX_CHANGED_FILES)

        if skip_verification:
            saved = _safe_save_verified_patch(diff_full, worktree_ws, workspace_root=repo_root, log_fn=_log_warn)
            if apply_on_pass and saved is not None:
                branch_err = _check_expected_branch(repo_root, spec["expected_branch"])
                if branch_err:
                    return "APPLY_FAILED", False, 0, False, changed_files, branch_err
                applied = apply_verified_patch(
                    repo_root, saved, worktree_path=wt_path, cleanup_worktree=True, plugin_root=PLUGIN_ROOT, log_fn=_log_warn
                )
                if not applied:
                    return "APPLY_FAILED", False, 0, False, changed_files, "UNSAFE_SKIPPED mais échec git apply sur la branche cible."
            return "UNSAFE_SKIPPED", applied, 0, False, changed_files, str(build.get("summary") or "Livré sans revue (--no-verify)")

        for attempt in range(1, max_remediations + 2):
            review_tree = collect_repo_tree(wt_path if wt_path.is_dir() else repo_root)
            diff_for_prompt = (
                diff_full[:MAX_PROMPT_DIFF_CHARS] + "\n... [diff tronqué]"
                if len(diff_full) > MAX_PROMPT_DIFF_CHARS
                else diff_full
            )
            review = await asyncio.wait_for(
                agent(  # type: ignore[name-defined]
                    _with_directive(
                        (
                            f"Audite à froid `{repo_name}` pour `{spec['title']}` (worktree `{worktree_ws}`).\n"
                            f"Fichiers modifiés : {json.dumps(changed_files, ensure_ascii=False)}\n"
                            f"Vérifications Builder :\n{build.get('validation_summary') or '(non fourni)'}\n\n"
                            f"Arborescence :\n{review_tree}\n\nDiff :\n```diff\n{diff_for_prompt}\n```\n"
                            "Applique Preuve ou Silence (max 3 uncertain, max 3 nits)."
                        ),
                        reviewer_cav,
                    ),
                    schema=REVIEW_SCHEMA,
                    role=f"Reviewer {repo_name}",
                    type_name="reviewer",
                    model=review_model,
                    workspace="inherit",
                ),
                timeout=reviewer_timeout_s,
            )
            has_blocking = bool(review.get("blockers") or review.get("majors"))
            verdict = "FAIL" if has_blocking else str(review.get("verdict") or "FAIL")
            summary_text = str(review.get("summary") or "")
            if verdict == "PASS":
                final_status = "PASS"
                saved = _safe_save_verified_patch(diff_full, worktree_ws, workspace_root=repo_root, log_fn=_log_warn)
                if apply_on_pass and saved is not None:
                    branch_err = _check_expected_branch(repo_root, spec["expected_branch"])
                    if branch_err:
                        final_status = "APPLY_FAILED"
                        applied = False
                        summary_text = branch_err
                    else:
                        applied = apply_verified_patch(
                            repo_root, saved, worktree_path=wt_path, cleanup_worktree=True, plugin_root=PLUGIN_ROOT, log_fn=_log_warn
                        )
                        if not applied:
                            final_status = "APPLY_FAILED"
                            summary_text = "PASS mais échec git apply sur la branche cible."
                break
            if attempt > max_remediations:
                final_status = "FAIL"
                break
            remediation_count += 1
            findings = "\n".join(
                [f"- [BLOCKER] {x}" for x in review.get("blockers") or []]
                + [f"- [MAJOR] {x}" for x in review.get("majors") or []]
            )
            build = await asyncio.wait_for(
                builder(
                    _with_directive(
                        (
                            f"Corrige uniquement ces constats dans `{wt_path}` :\n{findings}\n"
                            f"{BUILDER_HYGIENE_DIRECTIVE}\n"
                            "Re-vérifie, mets à jour `.gravity_verified.patch` et retourne `BUILD_SCHEMA`."
                        ),
                        builder_cav,
                    ),
                    BUILD_SCHEMA,
                ),
                timeout=builder_timeout_s,
            )
            if build.get("refused"):
                return "REFUSED", False, remediation_count, True, changed_files, str(build.get("summary") or "Remédiation refusée")
            diff_full = _read_worktree_patch(
                worktree_ws, str(build.get("git_diff", "")), build.get("modified_files"),
                max_prompt_diff_chars=MAX_PROMPT_DIFF_CHARS, log_fn=_log_warn,
            )
            changed_files = collect_changed_files(diff_full, build.get("modified_files"), max_items=MAX_CHANGED_FILES)
    except asyncio.TimeoutError:
        return (
            "TIMEOUT", False, remediation_count, refused_flag, changed_files,
            f"Watchdog timeout ({builder_timeout_s:.0f}s Builder / {reviewer_timeout_s:.0f}s Reviewer).",
        )
    finally:
        del builder
    return final_status, applied, remediation_count, refused_flag, changed_files, summary_text


async def _run_single_task(
    indexed_item: Tuple[int, Any],
    *,
    ws_root: Path,
    worktree_base: Optional[Path],
    wave_id: str,
    apply_on_pass: bool,
    builder_timeout_s: float,
    reviewer_timeout_s: float,
    global_args: Dict[str, Any],
) -> Dict[str, Any]:
    """Exécute un dépôt de façon fail-isolated dans un `git worktree` détaché."""
    idx, raw_item = indexed_item
    resolved_repo = (
        str(raw_item.get("repo_id") or raw_item.get("repo_name") or Path(str(raw_item["repo_root"])).name)
        if isinstance(raw_item, dict) and raw_item.get("repo_root")
        else f"task-{idx}"
    )
    resolved_profile = "standard"
    try:
        if isinstance(raw_item, dict) and isinstance(raw_item.get("repo_root"), Path) and "plan_text" in raw_item:
            spec = raw_item
        else:
            spec = parse_task_spec(raw_item, ws_root, default_level=global_args.get("level"))
        repo_root: Path = spec["repo_root"]
        repo_name: str = spec["repo_name"]
        resolved_repo = repo_name
        overrides: Dict[str, Any] = spec["overrides"]

        branch_err = _check_expected_branch(repo_root, spec["expected_branch"])
        if branch_err:
            return _build_repo_result(repo_name, status="ERROR", summary=branch_err)

        no_verify = bool(
            overrides.get("no_verify")
            or overrides.get("skip_verification")
            or global_args.get("no_verify")
            or global_args.get("skip_verification")
        )
        preset = load_level_preset_for_ws(
            repo_root,
            spec["level"],
            no_verify=no_verify,
            caveman_override=overrides.get("caveman") or global_args.get("caveman"),
            build_model_override=overrides.get("build_model") or global_args.get("build_model"),
            review_model_override=overrides.get("review_model") or global_args.get("review_model"),
            log_fn=_log_warn,
        )
        preset_profile = str(preset.get("level") or "standard")
        resolved_profile = preset_profile
        build_model = str(overrides.get("build_model") or global_args.get("build_model") or preset["build_model"])
        review_model = str(overrides.get("review_model") or global_args.get("review_model") or preset["review_model"])
        skip_verification = bool(no_verify or preset.get("skip_verification", False))
        max_remediations = (
            0 if skip_verification else int(
                overrides["max_remediation_rounds"]
                if overrides.get("max_remediation_rounds") is not None
                else preset.get("max_remediation_rounds", 1)
            )
        )
        skip_separate_planner = True
        caveman_level = str(preset.get("caveman") or "lite").strip().lower()
        role_models = {"plan": "skipped", "build": build_model, "review": "skipped" if skip_verification else review_model}

        wt_path = create_repo_worktree(repo_root, worktree_base=worktree_base, run_id=f"{wave_id}-{idx}")
        try:
            safe_update_live(
                repo_root, log_fn=_log_warn, stage="workflow:building", progress="0/1",
                preset_profile=preset_profile, role_models=role_models, caveman=caveman_level,
                nodes={"planner": "done", "builder": "active", "reviewer": ""},
                event={"type": "BUILDER", "decision": "allow", "message": f"Wave {wave_id}: build isolé."},
            )
            final_status, applied, remediation_count, refused_flag, changed_files, summary_text = await _execute_in_worktree(
                spec, wt_path, ws_root=ws_root, preset_profile=preset_profile, build_model=build_model,
                review_model=review_model, caveman_level=caveman_level, skip_verification=skip_verification,
                max_remediations=max_remediations, apply_on_pass=apply_on_pass,
                builder_timeout_s=builder_timeout_s, reviewer_timeout_s=reviewer_timeout_s,
            )
        finally:
            cleanup_worktree_best_effort(wt_path, repo_root, plugin_root=PLUGIN_ROOT, log_fn=_log_warn)

        stage_map = {
            "PASS": "workflow:done",
            "UNSAFE_SKIPPED": "workflow:unsafe_done",
            "TIMEOUT": "workflow:timeout",
            "REFUSED": "workflow:refused",
        }
        task_done = final_status in ("PASS", "UNSAFE_SKIPPED")
        safe_update_live(
            repo_root, log_fn=_log_warn, stage=stage_map.get(final_status, "workflow:escalated"),
            progress="1/1" if task_done else "0/1",
            preset_profile=preset_profile, role_models=role_models, caveman=caveman_level,
            nodes={
                "planner": "done",
                "builder": "done" if task_done else "fail",
                "reviewer": "done" if final_status == "PASS" else ("" if skip_verification else "fail"),
            },
            event={
                "type": "WAVE_RUNNER",
                "decision": "allow" if task_done else "deny",
                "message": f"Wave {wave_id}: {final_status} (applied={applied}).",
            },
        )
        safe_append_run(
            repo_root,
            {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "event": "wave_runner", "wave_id": wave_id, "preset_profile": preset_profile,
                "role_models": role_models, "skip_separate_planner": skip_separate_planner,
                "skip_verification": skip_verification, "verdict": final_status,
                "applied": applied, "remediation_rounds": remediation_count,
                "refused": refused_flag, "changed_files": changed_files, "cwd": str(repo_root),
            },
            log_fn=_log_warn,
        )
        return _build_repo_result(
            repo_name, status=final_status, profile=preset_profile, applied=applied,
            remediations=remediation_count, changed_files=changed_files, summary=summary_text,
        )
    except Exception as exc:
        return _build_repo_result(
            resolved_repo, status="ERROR", profile=resolved_profile, summary=f"{type(exc).__name__}: {exc}",
        )


def format_wave_summary(results: List[Dict[str, Any]], *, wave_id: str = "") -> str:
    """Formate un résumé JSON compact valide (`<= 10` lignes par dépôt, `changed_files` <= 20)."""
    lines = ['{"wave_id": ' + json.dumps(wave_id, ensure_ascii=False) + ', "repos": [']
    for idx, item in enumerate(results):
        comma = "," if idx + 1 < len(results) else ""
        compact = {
            "repo": str(item.get("repo") or ""),
            "status": str(item.get("status") or "ERROR"),
            "profile": str(item.get("profile") or "standard"),
            "applied": bool(item.get("applied", False)),
            "remediations": int(item.get("remediations", 0)),
            "changed_files": list(item.get("changed_files") or [])[:MAX_CHANGED_FILES],
            "summary": str(item.get("summary") or "")[:MAX_SUMMARY_CHARS],
        }
        lines.append("  " + json.dumps(compact, ensure_ascii=False) + comma)
    passed = sum(1 for r in results if r.get("status") in ("PASS", "UNSAFE_SKIPPED"))
    lines.append(f'], "passed": {passed}, "total": {len(results)}}}')
    return "\n".join(lines)


if _is_workflow_runtime():
    validated_specs, effective_ws, wave_label = resolve_wave_tasks(_ARGS, WS_ROOT)
    default_wave_id = f"w{wave_label}-{uuid.uuid4().hex[:8]}" if wave_label else f"w-{uuid.uuid4().hex[:8]}"
    wave_id = str(_ARGS.get("run_id") or default_wave_id).strip() or default_wave_id
    apply_on_pass = bool(_ARGS.get("apply_on_pass", True))
    raw_wt_base = _ARGS.get("worktree_base")
    wt_base = (
        Path(str(raw_wt_base)).expanduser().resolve()
        if isinstance(raw_wt_base, str) and raw_wt_base.strip()
        else resolve_worktree_base()
    )
    builder_timeout_s = resolve_timeout_seconds(_ARGS.get("builder_timeout_s"), DEFAULT_BUILDER_TIMEOUT_S)
    reviewer_timeout_s = resolve_timeout_seconds(_ARGS.get("reviewer_timeout_s"), DEFAULT_REVIEWER_TIMEOUT_S)

    total_tasks = len(validated_specs)
    phase(f"1. Exécution vague parallèle ({total_tasks} tâche(s), id={wave_id})")  # type: ignore[name-defined]
    if not validated_specs:
        WAVE_RESULTS: List[Dict[str, Any]] = []
        safe_update_live(effective_ws, log_fn=_log_warn, stage="idle", progress="0/0")
        _log_warn("[WARN] Aucune tâche fournie dans args['wave_plan'] / args['tasks'].")
    else:
        WAVE_RESULTS = await parallel(  # type: ignore[name-defined, top-level-await]
            list(enumerate(validated_specs, 1)),
            lambda item: _run_single_task(
                item, ws_root=effective_ws, worktree_base=wt_base, wave_id=wave_id,
                apply_on_pass=apply_on_pass, builder_timeout_s=builder_timeout_s,
                reviewer_timeout_s=reviewer_timeout_s, global_args=_ARGS,
            ),
        )

    phase("2. Consolidation compacte de la vague")  # type: ignore[name-defined]
    WAVE_SUMMARY = format_wave_summary(WAVE_RESULTS, wave_id=wave_id)
    for res in WAVE_RESULTS:
        log(f"[{res['status']}] {res['repo']} ({res['profile']}) | applied={res['applied']} | files={len(res['changed_files'])}")  # type: ignore[name-defined]
    print(WAVE_SUMMARY)
