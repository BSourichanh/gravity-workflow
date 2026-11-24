"""Runner de vagues natif Gravity pour `run_workflow` (100 % stdlib Python 3, fail-isolated)."""

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

_TARGET_REPO_RE = re.compile(r"^\s*-\s*\*\*Dépôt cible\s*:\*\*\s*(.+)$", re.MULTILINE)
_BRANCH_RE = re.compile(r"^\s*-\s*\*\*Branche attendue\s*:\*\*\s*`([^`]+)`", re.MULTILINE)
_PROFILE_RE = re.compile(r"^\s*-\s*\*\*Profil workflow\s*:\*\*\s*`([^`]+)`", re.MULTILINE)
_TITLE_RE = re.compile(r"^\s*#\s+(.+)$", re.MULTILINE)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_PAREN_RE = re.compile(r"\(([^)]+)\)")

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
            WS_ROOT.parent / "gravity-workflow" / "plugin",
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
    caveman_directive, cleanup_worktree_best_effort, collect_changed_files,
    collect_repo_tree, create_repo_worktree, load_level_preset_for_ws,
    read_plan_file, resolve_timeout_seconds, resolve_worktree_base,
    safe_append_run, safe_update_live,
)


def _with_directive(prompt: str, directive: str) -> str:
    return f"{prompt}\n\n{directive}" if directive else prompt


def _resolve_repo_root(
    raw_override: Any,
    target_line: str,
    ws_root: Path,
    task_path: Optional[Path],
) -> Path:
    if isinstance(raw_override, (str, Path)) and str(raw_override).strip():
        cand = Path(str(raw_override).strip()).expanduser()
        return cand.resolve() if cand.is_absolute() else (ws_root / cand).resolve()

    paren_match = _PAREN_RE.search(target_line)
    if paren_match:
        raw_path = paren_match.group(1).strip().strip("`")
        if raw_path:
            cand = Path(raw_path).expanduser()
            if cand.is_absolute():
                return cand.resolve()
            ws_cand = (ws_root / cand).resolve()
            if ws_cand.exists() or task_path is None:
                return ws_cand
            return (task_path.parent.parent / cand).resolve()

    bt_match = _BACKTICK_RE.search(target_line)
    if bt_match:
        token = bt_match.group(1).strip()
        for cand in (ws_root / token, ws_root.parent / token):
            if cand.is_dir():
                return cand.resolve()
        if ws_root.name == token:
            return ws_root.resolve()
        return (ws_root.parent / token).resolve()

    return ws_root.resolve()


def parse_task_spec(
    raw_item: Any,
    ws_root: Path,
    *,
    default_level: Any = None,
) -> Dict[str, Any]:
    """Lit `tasks/*.md` et `<repo>/AGENTS.md` en Python (0 token LLM)."""
    overrides: Dict[str, Any] = dict(raw_item) if isinstance(raw_item, dict) else {}
    plan_file_raw = str(
        overrides.get("plan_file")
        or overrides.get("task_file")
        or overrides.get("path")
        or (raw_item if isinstance(raw_item, str) else "")
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
    title = title_match.group(1).strip() if title_match else (task_path.stem if task_path else "task")
    target_match = _TARGET_REPO_RE.search(plan_text)
    target_line = target_match.group(1).strip() if target_match else ""
    repo_override = overrides.get("repo_root") or overrides.get("repo") or overrides.get("workspace")
    repo_root = _resolve_repo_root(repo_override, target_line, ws_root, task_path)

    branch_match = _BRANCH_RE.search(plan_text)
    expected_branch = str(
        overrides.get("expected_branch") or (branch_match.group(1).strip() if branch_match else "")
    ).strip()
    profile_match = _PROFILE_RE.search(plan_text)
    raw_level = overrides.get("level") or (profile_match.group(1).strip() if profile_match else None) or default_level

    agents_text = ""
    agents_file = repo_root / "AGENTS.md"
    if agents_file.is_file():
        agents_text = agents_file.read_text(encoding="utf-8", errors="replace").strip()[:MAX_AGENTS_PROMPT_CHARS]

    return {
        "title": title,
        "plan_file": str(task_path) if task_path else "",
        "plan_text": plan_text,
        "repo_name": repo_root.name,
        "repo_root": repo_root,
        "expected_branch": expected_branch,
        "level": raw_level,
        "agents_text": agents_text,
        "overrides": overrides,
    }


def _check_expected_branch(repo_root: Path, expected_branch: str) -> Optional[str]:
    if not expected_branch:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "branch", "--show-current"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            current = proc.stdout.strip()
            if current and current != expected_branch:
                return f"Branche attendue '{expected_branch}', courante '{current}'."
    except (OSError, subprocess.SubprocessError):
        pass
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
    verify_note = (
        "Vérifications ignorées (--no-verify éphémère)."
        if skip_verification
        else "Exécute les vérifications/tests obligatoires du dépôt dans le worktree et consigne commandes + durées dans `validation_summary`."
    )
    return (
        f"Travaille EXCLUSIVEMENT dans le worktree isolé `{wt_path}` (n'édite jamais `{spec['repo_root']}` ni `{ws_root}`).\n"
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
    builder = agent(role=f"Builder {repo_name}", type_name="builder", workspace="inherit", model=build_model)

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
                applied = apply_verified_patch(
                    repo_root, saved, worktree_path=wt_path, cleanup_worktree=True, plugin_root=PLUGIN_ROOT, log_fn=_log_warn
                )
            return "UNSAFE_SKIPPED", applied, 0, False, changed_files, str(build.get("summary") or "Livré sans revue (--no-verify)")

        for attempt in range(1, max_remediations + 2):
            review_tree = collect_repo_tree(wt_path if wt_path.is_dir() else repo_root)
            diff_for_prompt = (
                diff_full[:MAX_PROMPT_DIFF_CHARS] + "\n... [diff tronqué]"
                if len(diff_full) > MAX_PROMPT_DIFF_CHARS
                else diff_full
            )
            review = await asyncio.wait_for(
                agent(
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
                    applied = apply_verified_patch(
                        repo_root, saved, worktree_path=wt_path, cleanup_worktree=True, plugin_root=PLUGIN_ROOT, log_fn=_log_warn
                    )
                    if not applied:
                        final_status = "APPLY_FAILED"
                        summary_text = "PASS mais échec git apply sur dev."
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
        str(raw_item.get("repo_root") or raw_item.get("repo") or f"task-{idx}")
        if isinstance(raw_item, dict)
        else Path(str(raw_item)).stem
    )
    resolved_profile = "standard"
    try:
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
        # plan_file / plan fourni => skip_separate_planner = True (0 token Planner)
        skip_separate_planner = True
        caveman_level = str(preset.get("caveman") or "lite").strip().lower()
        role_models = {"plan": "skipped", "build": build_model, "review": "skipped" if skip_verification else review_model}

        wt_path = create_repo_worktree(repo_root, worktree_base=worktree_base, run_id=f"{wave_id}-{idx}")
        try:
            safe_update_live(
                repo_root, log_fn=_log_warn, stage="workflow:building", preset_profile=preset_profile,
                role_models=role_models, caveman=caveman_level,
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
        safe_update_live(
            repo_root, log_fn=_log_warn, stage=stage_map.get(final_status, "workflow:escalated"),
            preset_profile=preset_profile, role_models=role_models, caveman=caveman_level,
            nodes={
                "planner": "done",
                "builder": "done" if final_status in ("PASS", "UNSAFE_SKIPPED") else "fail",
                "reviewer": "done" if final_status == "PASS" else ("" if skip_verification else "fail"),
            },
            event={
                "type": "WAVE_RUNNER",
                "decision": "allow" if final_status in ("PASS", "UNSAFE_SKIPPED") else "deny",
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
    raw_tasks = _ARGS.get("tasks") if isinstance(_ARGS.get("tasks"), list) else []
    wave_id = str(_ARGS.get("run_id") or f"w-{uuid.uuid4().hex[:8]}").strip() or f"w-{uuid.uuid4().hex[:8]}"
    apply_on_pass = bool(_ARGS.get("apply_on_pass", True))
    raw_wt_base = _ARGS.get("worktree_base")
    wt_base = (
        Path(str(raw_wt_base)).expanduser().resolve()
        if isinstance(raw_wt_base, str) and raw_wt_base.strip()
        else resolve_worktree_base()
    )
    builder_timeout_s = resolve_timeout_seconds(_ARGS.get("builder_timeout_s"), DEFAULT_BUILDER_TIMEOUT_S)
    reviewer_timeout_s = resolve_timeout_seconds(_ARGS.get("reviewer_timeout_s"), DEFAULT_REVIEWER_TIMEOUT_S)

    phase(f"1. Exécution vague parallèle ({len(raw_tasks)} tâche(s), id={wave_id})")
    if not raw_tasks:
        WAVE_RESULTS: List[Dict[str, Any]] = []
        _log_warn("[WARN] Aucune tâche fournie dans args['tasks'].")
    else:
        WAVE_RESULTS = await parallel(
            list(enumerate(raw_tasks, 1)),
            lambda item: _run_single_task(
                item, ws_root=WS_ROOT, worktree_base=wt_base, wave_id=wave_id,
                apply_on_pass=apply_on_pass, builder_timeout_s=builder_timeout_s,
                reviewer_timeout_s=reviewer_timeout_s, global_args=_ARGS,
            ),
        )

    phase("2. Consolidation compacte de la vague")
    WAVE_SUMMARY = format_wave_summary(WAVE_RESULTS, wave_id=wave_id)
    for res in WAVE_RESULTS:
        log(f"[{res['status']}] {res['repo']} ({res['profile']}) | applied={res['applied']} | files={len(res['changed_files'])}")
    print(WAVE_SUMMARY)
