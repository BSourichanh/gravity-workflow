"""Workflow de revue Gravity (Builder multi-tour isolé + Reviewer indépendant + suivi Live)."""

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

MAX_REMEDIATION_ROUNDS = 1
MAX_PROMPT_DIFF_CHARS = 60000
MAX_TREE_FILES = 200
EXCLUDED_TREE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
}

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
        if (cand / "observability").is_dir():
            return cand
    _log_warn(
        "[WARN] PLUGIN_ROOT introuvable (observability/ absent) parmi : "
        + ", ".join(str(c) for c in candidates)
    )
    return candidates[0] if candidates else WS_ROOT


PLUGIN_ROOT = _resolve_plugin_root()
for _sub in ("observability", "workflows"):
    _sub_dir = (PLUGIN_ROOT / _sub).resolve()
    if _sub_dir.is_dir() and str(_sub_dir) not in sys.path:
        sys.path.insert(0, str(_sub_dir))

from patch_apply import (  # type: ignore[import-not-found]  # noqa: E402
    _read_worktree_patch,
    _safe_save_verified_patch,
    apply_verified_patch,
)

BUILD_SCHEMA = {
    "type": "object",
    "properties": {
        "workspace_path": {"type": "string"},
        "modified_files": {"type": "array", "items": {"type": "string"}},
        "git_diff": {"type": "string"},
        "validation_summary": {"type": "string"},
        "refused": {"type": "boolean"},
        "summary": {"type": "string"},
    },
    "required": [
        "workspace_path",
        "modified_files",
        "git_diff",
        "validation_summary",
        "refused",
        "summary",
    ],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "comprehension_check": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "majors": {"type": "array", "items": {"type": "string"}},
        "uncertain": {"type": "array", "items": {"type": "string"}},
        "nits": {"type": "array", "items": {"type": "string"}},
        "manual_checks": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": [
        "verdict",
        "comprehension_check",
        "blockers",
        "majors",
        "uncertain",
        "nits",
        "summary",
    ],
}


def _collect_repo_tree(root: Path, max_files: int = MAX_TREE_FILES) -> str:
    """Collecte en pur Python une arborescence bornée du dépôt sans appel shell."""
    if not root.is_dir():
        return "(répertoire introuvable)"
    collected: List[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_TREE_DIRS)
        rel_dir = Path(dirpath).relative_to(root)
        for fname in sorted(filenames):
            rel_path = (rel_dir / fname).as_posix() if str(rel_dir) != "." else fname
            collected.append(rel_path)
            if len(collected) >= max_files:
                truncated = True
                break
        if truncated:
            break
    if not collected:
        return "(aucun fichier trouvé)"
    suffix = f"\n... [arborescence bornée à {max_files} fichiers]" if truncated else ""
    return "\n".join(collected) + suffix


def _safe_update_live(**kwargs: Any) -> None:
    try:
        from state_writer import update_live_state  # type: ignore[import-not-found]

        update_live_state(workspace_root=WS_ROOT, **kwargs)
    except Exception as exc:
        _log_warn(f"[WARN] update_live_state échoué ({type(exc).__name__}: {exc})")


def _safe_append_pipeline_run(entry: Dict[str, Any]) -> None:
    try:
        from state_writer import append_run_log  # type: ignore[import-not-found]

        append_run_log(WS_ROOT, entry)
    except Exception as exc:
        _log_warn(f"[WARN] Journalisation runs.jsonl échouée ({type(exc).__name__}: {exc})")


def _caveman_directive(role: str, level: str) -> str:
    try:
        from caveman_protocol import build_subagent_directive  # type: ignore[import-not-found]

        return build_subagent_directive(role, level)
    except Exception as exc:
        _log_warn(f"[WARN] caveman_protocol indisponible ({type(exc).__name__}: {exc})")
        return ""


def _with_directive(prompt: str, directive: str) -> str:
    return f"{prompt}\n\n{directive}" if directive else prompt


def _load_level_preset(
    level_override: Any,
    *,
    no_verify: bool = False,
    caveman_override: Any = None,
    plan_model_override: Any = None,
    build_model_override: Any = None,
    review_model_override: Any = None,
) -> dict:
    fallback = {
        "plan_model": "flash",
        "build_model": "flash",
        "review_model": "pro",
        "max_remediation_rounds": MAX_REMEDIATION_ROUNDS,
        "level": "standard",
        "mode": "evo",
        "suggested_profile": "standard",
        "caveman": "lite",
        "caveman_planner": "lite",
        "skip_separate_planner": False,
        "skip_verification": no_verify,
    }
    try:
        from profile_engine import get_active_preset  # type: ignore[import-not-found]

        override_str = str(level_override).strip() if level_override else None
        caveman_str = str(caveman_override).strip() if caveman_override else None
        return get_active_preset(
            WS_ROOT,
            level_override=override_str,
            no_verify=no_verify,
            caveman_override=caveman_str,
            plan_model_override=str(plan_model_override).strip() if plan_model_override else None,
            build_model_override=str(build_model_override).strip() if build_model_override else None,
            review_model_override=str(review_model_override).strip() if review_model_override else None,
        )
    except Exception as exc:
        _log_warn(
            f"[WARN] Chargement preset profile_engine échoué ({type(exc).__name__}: {exc}) — repli sur preset standard."
        )
        return fallback


if _is_workflow_runtime():
    task = _ARGS.get("task", "Exécuter la tâche métier demandée.")
    no_verify_arg = (
        bool(_ARGS.get("no_verify"))
        or str(_ARGS.get("verification") or "").strip().lower() == "unsafe"
        or bool(_ARGS.get("skip_verification"))
    )
    preset = _load_level_preset(
        _ARGS.get("level"),
        no_verify=no_verify_arg,
        caveman_override=_ARGS.get("caveman"),
        plan_model_override=_ARGS.get("plan_model"),
        build_model_override=_ARGS.get("build_model"),
        review_model_override=_ARGS.get("review_model"),
    )
    plan_model = str(_ARGS.get("plan_model") or preset["plan_model"])
    build_model = str(_ARGS.get("build_model") or preset["build_model"])
    review_model = str(_ARGS.get("review_model") or preset["review_model"])
    skip_verification = bool(no_verify_arg or preset.get("skip_verification", False))
    max_remediation_rounds = (
        0
        if skip_verification
        else int(
            _ARGS["max_remediation_rounds"]
            if _ARGS.get("max_remediation_rounds") is not None
            else preset["max_remediation_rounds"]
        )
    )
    caveman_level = str(preset.get("caveman") or "lite").strip().lower()
    planner_cav = _caveman_directive("planner", str(preset.get("caveman_planner") or caveman_level))
    builder_cav = _caveman_directive("builder", caveman_level)
    reviewer_cav = _caveman_directive("reviewer", caveman_level)

    provided_plan = str(_ARGS.get("plan") or "").strip()
    if _ARGS.get("skip_separate_planner") is not None:
        skip_separate_planner = bool(_ARGS["skip_separate_planner"])
    elif provided_plan:
        skip_separate_planner = True
    else:
        skip_separate_planner = bool(preset.get("skip_separate_planner", False))

    preset_profile = str(preset.get("level") or "standard")
    role_models = {
        "plan": "skipped" if skip_separate_planner else plan_model,
        "build": build_model,
        "review": "skipped" if skip_verification else review_model,
    }
    ws_tree = _collect_repo_tree(WS_ROOT)

    phase("1. Contexte & Plan")
    if skip_separate_planner:
        skip_msg = (
            "Plan fourni dans args : planification séparée sautée."
            if provided_plan
            else f"Profil {preset_profile} : planification séparée sautée."
        )
        _safe_update_live(
            stage="workflow:building",
            mode=str(preset.get("mode", "evo")),
            level=preset_profile,
            preset_profile=preset_profile,
            suggested_profile=str(preset.get("suggested_profile", "standard")),
            role_models=role_models,
            caveman=caveman_level,
            nodes={"planner": "done", "builder": "active", "reviewer": ""},
            event={
                "type": "PLANNER",
                "decision": "allow",
                "message": skip_msg,
            },
        )
        plan = provided_plan or str(task)
        log(skip_msg)
    else:
        _safe_update_live(
            stage="workflow:planning",
            mode=str(preset.get("mode", "evo")),
            level=preset_profile,
            preset_profile=preset_profile,
            suggested_profile=str(preset.get("suggested_profile", "standard")),
            role_models=role_models,
            caveman=caveman_level,
            nodes={"planner": "active", "builder": "", "reviewer": ""},
            event={"type": "PLANNER", "decision": "allow", "message": f"Planification : {task[:80]}"},
        )
        plan = await agent(
            _with_directive(
                (
                    f"Établis un plan d'action concis et vérifiable pour la tâche : {task}\n\n"
                    f"Arborescence pré-collectée du dépôt ({WS_ROOT}) :\n{ws_tree}"
                ),
                planner_cav,
            ),
            schema={"type": "string"},
            role="Planner",
            type_name="planner",
            model=plan_model,
        )

    phase("2. Exécution initiale (Builder multi-tour en branche isolée)")
    _safe_update_live(
        stage="workflow:building",
        preset_profile=preset_profile,
        role_models=role_models,
        caveman=caveman_level,
        nodes={"planner": "done", "builder": "active", "reviewer": ""},
        event={"type": "BUILDER", "decision": "allow", "message": "Exécution initiale en branche isolée."},
    )

    builder = agent(
        role="Builder",
        type_name="builder",
        workspace="branch",
        model=build_model,
    )

    final_verdict = "UNKNOWN"
    remediation_count = 0
    refused_flag = False
    try:
        verify_instruction = (
            "Vérifications/tests ignorés (--no-verify éphémère) : indique-le dans `validation_summary`."
            if skip_verification
            else "Exécute les commandes de validation/tests pertinentes et consigne les commandes exactes et sorties dans `validation_summary`."
        )
        build = await builder(
            _with_directive(
                (
                    f"Applique le plan suivant dans ta branche isolée :\n{plan}\n\n"
                    "N'exécute jamais `git commit`, `git push`, `git tag` ni de montée de version release (strictement réservés au chef d'orchestre). "
                    f"{verify_instruction} "
                    "Exécute `git add -N .` puis `git diff --output=.gravity_verified.patch -- . \":(exclude).gravity_verified.patch\"` dans ton worktree isolé, "
                    "puis retourne le chemin absolu réel de ton répertoire de travail dans `workspace_path`, "
                    "la liste des fichiers modifiés dans `modified_files`, un extrait ou résumé du diff dans `git_diff`, "
                    "`validation_summary`, `refused` et un résumé factuel."
                ),
                builder_cav,
            ),
            BUILD_SCHEMA,
        )

        if build.get("refused"):
            refused_flag = True
            final_verdict = "REFUSED"
            _safe_update_live(
                stage="workflow:refused",
                preset_profile=preset_profile,
                role_models=role_models,
                caveman=caveman_level,
                nodes={"planner": "done", "builder": "fail", "reviewer": ""},
                event={"type": "BUILDER", "decision": "deny", "message": f"Tâche refusée : {build['summary']}"},
            )
            log(f"Exécution interrompue par le Builder (refus sécurité) : {build['summary']}")
        elif skip_verification:
            phase("3. Livraison directe (--no-verify / unsafe éphémère)")
            final_verdict = "UNSAFE_SKIPPED"
            diff_full = _read_worktree_patch(
                str(build.get("workspace_path", "")),
                str(build.get("git_diff", "")),
                build.get("modified_files"),
                max_prompt_diff_chars=MAX_PROMPT_DIFF_CHARS,
                log_fn=_log_warn,
            )
            _safe_save_verified_patch(
                diff_full,
                str(build.get("workspace_path", "")),
                workspace_root=WS_ROOT,
                log_fn=_log_warn,
            )
            _safe_update_live(
                stage="workflow:unsafe_done",
                preset_profile=preset_profile,
                role_models=role_models,
                caveman=caveman_level,
                nodes={"planner": "done", "builder": "done", "reviewer": ""},
                event={
                    "type": "UNSAFE",
                    "decision": "allow",
                    "message": "Mode --no-verify éphémère : revue indépendante et remédiations désactivées.",
                },
            )
            log("Mode --no-verify éphémère actif : livraison directe sans revue indépendante.")
        else:
            review = None
            for attempt in range(1, max_remediation_rounds + 2):
                phase(f"3.{attempt} Revue indépendante (passage {attempt})")
                _safe_update_live(
                    stage=f"workflow:review:{attempt}",
                    preset_profile=preset_profile,
                    role_models=role_models,
                    caveman=caveman_level,
                    nodes={"planner": "done", "builder": "done", "reviewer": "active"},
                    event={"type": "REVIEWER", "decision": "allow", "message": f"Audit indépendant passage {attempt}."},
                )
                worktree_ws = str(build.get("workspace_path", "")).strip()
                worktree_path = Path(worktree_ws) if worktree_ws else WS_ROOT
                review_tree = _collect_repo_tree(worktree_path if worktree_path.is_dir() else WS_ROOT)
                diff_full = _read_worktree_patch(
                    worktree_ws,
                    str(build.get("git_diff", "")),
                    build.get("modified_files"),
                    max_prompt_diff_chars=MAX_PROMPT_DIFF_CHARS,
                    log_fn=_log_warn,
                )
                diff_for_prompt = (
                    diff_full[:MAX_PROMPT_DIFF_CHARS]
                    + "\n... [diff tronqué pour le prompt, inspecter directement le worktree] ..."
                    if len(diff_full) > MAX_PROMPT_DIFF_CHARS
                    else diff_full
                )
                val_summary = str(build.get("validation_summary") or "(non fourni)")
                review = await agent(
                    _with_directive(
                        (
                            f"Audite à froid les fichiers produits pour la tâche : {task}\n"
                            f"Répertoire du livrable produit : {worktree_ws}\n"
                            f"Fichiers déclarés modifiés : {json.dumps(build['modified_files'], ensure_ascii=False)}\n"
                            f"Vérifications Builder (`validation_summary`) :\n{val_summary}\n\n"
                            f"Arborescence pré-collectée :\n{review_tree}\n\n"
                            f"Diff / extrait produit :\n```diff\n{diff_for_prompt}\n```\n"
                            "Inspecte les fichiers pertinents dans le répertoire indiqué. "
                            "Si `Non-Workspace File Access` restreint la lecture directe du worktree, "
                            "appuie-toi strictement sur le diff, l'arborescence et `validation_summary` ci-dessus "
                            "(classe les éléments non vérifiables en `UNCERTAIN` sans boucler sur des `read_file` en échec). "
                            "Applique strictement le protocole Preuve ou Silence (max 3 uncertain, max 3 nits)."
                        ),
                        reviewer_cav,
                    ),
                    schema=REVIEW_SCHEMA,
                    role="Reviewer",
                    type_name="reviewer",
                    model=review_model,
                    workspace="inherit",
                )
                review["uncertain"] = list(review.get("uncertain") or [])[:3]
                review["nits"] = list(review.get("nits") or [])[:3]

                has_blocking_findings = bool(review["blockers"] or review["majors"])
                effective_verdict = "FAIL" if has_blocking_findings else review["verdict"]
                final_verdict = effective_verdict

                log(f"Passage {attempt} -> Verdict : {effective_verdict} | {review['summary']}")

                if effective_verdict == "PASS":
                    saved_patch = _safe_save_verified_patch(
                        diff_full,
                        worktree_ws,
                        workspace_root=WS_ROOT,
                        log_fn=_log_warn,
                    )
                    if saved_patch is not None:
                        apply_cmd = (
                            f'python3 "{PLUGIN_ROOT / "workflows" / "patch_apply.py"}" '
                            f'--apply --workspace "{WS_ROOT}"'
                            + (f' --worktree "{worktree_ws}"' if worktree_ws else "")
                        )
                        log(f"Patch vérifié : {saved_patch} | Appliquer : {apply_cmd}")
                        if bool(_ARGS.get("apply_on_pass")):
                            wt_arg = Path(worktree_ws) if worktree_ws else None
                            applied = apply_verified_patch(
                                WS_ROOT,
                                saved_patch,
                                worktree_path=wt_arg,
                                cleanup_worktree=True,
                                plugin_root=PLUGIN_ROOT,
                                log_fn=_log_warn,
                            )
                            log(
                                "Patch appliqué sur le workspace cible et worktree nettoyé."
                                if applied
                                else "[WARN] Échec application automatique du patch sur le workspace cible."
                            )
                    _safe_update_live(
                        stage="workflow:done",
                        preset_profile=preset_profile,
                        role_models=role_models,
                        caveman=caveman_level,
                        nodes={"planner": "done", "builder": "done", "reviewer": "done"},
                        event={"type": "REVIEWER", "decision": "allow", "message": f"PASS (passage {attempt})."},
                    )
                    break

                if attempt > max_remediation_rounds:
                    _safe_update_live(
                        stage="workflow:escalated",
                        preset_profile=preset_profile,
                        role_models=role_models,
                        caveman=caveman_level,
                        nodes={"planner": "done", "builder": "fail", "reviewer": "fail"},
                        event={
                            "type": "REVIEWER",
                            "decision": "force_ask",
                            "message": "FAIL — Échec après remédiation maximale : escalade humaine requise.",
                        },
                    )
                    log("Échec après épuisement des tentatives de remédiation. Escalade humaine requise.")
                    break

                remediation_count += 1
                phase(f"4.{attempt} Remédiation ciblée")
                _safe_update_live(
                    stage=f"workflow:remediation:{attempt}",
                    preset_profile=preset_profile,
                    role_models=role_models,
                    caveman=caveman_level,
                    nodes={"planner": "done", "builder": "active", "reviewer": "fail"},
                    event={
                        "type": "REMEDIATION",
                        "decision": "force_ask",
                        "message": f"Correction ciblée ({len(review['blockers'])} BLOCKER, {len(review['majors'])} MAJOR).",
                    },
                )
                findings_block = "\n".join(
                    [f"- [BLOCKER] {item}" for item in review["blockers"]]
                    + [f"- [MAJOR] {item}" for item in review["majors"]]
                )
                build = await builder(
                    _with_directive(
                        (
                            f"Corrige uniquement les constats bloquants suivants sur la tâche '{task}' "
                            f"dans ton worktree courant ({worktree_ws}) :\n"
                            f"{findings_block}\n"
                            "Ne modifie pas ce qui est déjà conforme. "
                            "Exécute les vérifications pertinentes (`validation_summary`), puis `git add -N .` et "
                            "`git diff --output=.gravity_verified.patch -- . \":(exclude).gravity_verified.patch\"`, "
                            "et retourne `workspace_path`, `modified_files`, `git_diff`, `validation_summary`, `refused` et le résumé."
                        ),
                        builder_cav,
                    ),
                    BUILD_SCHEMA,
                )
                if build.get("refused"):
                    refused_flag = True
                    final_verdict = "REFUSED"
                    _safe_update_live(
                        stage="workflow:refused",
                        preset_profile=preset_profile,
                        role_models=role_models,
                        caveman=caveman_level,
                        nodes={"planner": "done", "builder": "fail", "reviewer": ""},
                        event={
                            "type": "BUILDER",
                            "decision": "deny",
                            "message": f"Remédiation refusée : {build.get('summary', '')}",
                        },
                    )
                    log(
                        f"Remédiation interrompue par le Builder (refus sécurité) : {build.get('summary', '')}"
                    )
                    break

            if review and review.get("manual_checks"):
                log(
                    "Checklist [manual] pour validation humaine :\n"
                    + "\n".join(f"- [ ] {c}" for c in review["manual_checks"])
                )
    finally:
        del builder
        _safe_append_pipeline_run(
            {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "event": "review_pipeline",
                "preset_profile": preset_profile,
                "role_models": role_models,
                "skip_separate_planner": skip_separate_planner,
                "skip_verification": skip_verification,
                "verdict": final_verdict,
                "remediation_rounds": remediation_count,
                "refused": refused_flag,
                "cwd": str(WS_ROOT),
            }
        )
