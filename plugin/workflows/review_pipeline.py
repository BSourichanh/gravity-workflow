"""Workflow de revue Gravity (Builder multi-tour isolé + Reviewer indépendant + suivi Live)."""

import json
import sys
from pathlib import Path
from typing import Any

MAX_REMEDIATION_ROUNDS = 2
MAX_PROMPT_DIFF_CHARS = 60000
WS_ROOT = Path(WORKSPACES[0]) if WORKSPACES else Path.cwd()
PLUGIN_ROOT = Path(__file__).resolve().parent.parent if "__file__" in globals() else WS_ROOT

BUILD_SCHEMA = {
    "type": "object",
    "properties": {
        "workspace_path": {"type": "string"},
        "modified_files": {"type": "array", "items": {"type": "string"}},
        "git_diff": {"type": "string"},
        "refused": {"type": "boolean"},
        "summary": {"type": "string"},
    },
    "required": ["workspace_path", "modified_files", "git_diff", "refused", "summary"],
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


def _safe_update_live(**kwargs: Any) -> None:
    try:
        obs_dir = (PLUGIN_ROOT / "observability").resolve()
        if str(obs_dir) not in sys.path:
            sys.path.insert(0, str(obs_dir))
        from state_writer import update_live_state  # type: ignore[import-not-found]

        update_live_state(workspace_root=WS_ROOT, **kwargs)
    except Exception:
        pass


def _read_worktree_patch(workspace_path: str, fallback_diff: str) -> str:
    """Lit de façon déterministe `.gravity_verified.patch` dans le worktree du builder."""
    raw_ws = str(workspace_path or "").strip()
    if raw_ws:
        patch_candidate = Path(raw_ws) / ".gravity_verified.patch"
        try:
            if patch_candidate.is_file():
                raw_bytes = patch_candidate.read_bytes()
                encoding = (
                    "utf-16"
                    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff"))
                    else "utf-8"
                )
                content = raw_bytes.decode(encoding, errors="replace")
                if content.strip():
                    return content
        except Exception:
            pass
    return str(fallback_diff or "")


def _caveman_directive(role: str, level: str) -> str:
    try:
        obs_dir = (PLUGIN_ROOT / "observability").resolve()
        if str(obs_dir) not in sys.path:
            sys.path.insert(0, str(obs_dir))
        from caveman_protocol import build_subagent_directive  # type: ignore[import-not-found]

        return build_subagent_directive(role, level)
    except Exception:
        return ""


def _with_directive(prompt: str, directive: str) -> str:
    return f"{prompt}\n\n{directive}" if directive else prompt


def _load_level_preset(
    level_override: Any,
    pace_override: Any = None,
    verification_override: Any = None,
    caveman_override: Any = None,
) -> dict:
    fallback = {
        "plan_model": "pro",
        "build_model": "flash",
        "review_model": "pro",
        "max_remediation_rounds": MAX_REMEDIATION_ROUNDS,
        "level": "high",
        "mode": "evo",
        "pace": "slow",
        "verification": "safe",
        "caveman": "lite",
        "skip_separate_planner": False,
        "skip_verification": False,
    }
    try:
        obs_dir = (PLUGIN_ROOT / "observability").resolve()
        if str(obs_dir) not in sys.path:
            sys.path.insert(0, str(obs_dir))
        from profile_engine import get_active_preset  # type: ignore[import-not-found]

        override_str = str(level_override).strip() if level_override else None
        pace_str = str(pace_override).strip() if pace_override else None
        verif_str = str(verification_override).strip() if verification_override else None
        caveman_str = str(caveman_override).strip() if caveman_override else None
        return get_active_preset(
            WS_ROOT,
            level_override=override_str,
            pace_override=pace_str,
            verification_override=verif_str,
            caveman_override=caveman_str,
        )
    except Exception:
        return fallback


task = args.get("task", "Exécuter la tâche métier demandée.")
preset = _load_level_preset(
    args.get("level"),
    args.get("pace"),
    args.get("verification"),
    args.get("caveman"),
)
plan_model = args.get("plan_model", preset["plan_model"])
build_model = args.get("build_model", preset["build_model"])
review_model = args.get("review_model", preset["review_model"])
max_remediation_rounds = int(args.get("max_remediation_rounds", preset["max_remediation_rounds"]))
pace = str(preset.get("pace") or "slow").strip().lower()
verification = str(preset.get("verification") or "safe").strip().lower()
caveman_level = str(preset.get("caveman") or "lite").strip().lower()
planner_cav = _caveman_directive("planner", caveman_level)
builder_cav = _caveman_directive("builder", caveman_level)
reviewer_cav = _caveman_directive("reviewer", caveman_level)
skip_separate_planner = bool(
    args.get("skip_separate_planner", preset.get("skip_separate_planner", pace == "fast"))
)
skip_verification = bool(args.get("skip_verification", verification == "unsafe"))

phase("1. Contexte & Plan")
if skip_separate_planner:
    _safe_update_live(
        stage="workflow:building",
        pace=pace,
        verification=verification,
        caveman=caveman_level,
        nodes={"planner": "done", "builder": "active", "reviewer": ""},
        event={
            "type": "FAST",
            "decision": "allow",
            "message": "Stratégie fast : planification séparée condensée.",
        },
    )
    plan = str(args.get("plan") or task)
    log("Stratégie fast : planification séparée condensée pour optimiser le temps (réflexion et modèles conservés).")
else:
    _safe_update_live(
        stage="workflow:planning",
        pace=pace,
        verification=verification,
        caveman=caveman_level,
        nodes={"planner": "active", "builder": "", "reviewer": ""},
        event={"type": "PLANNER", "decision": "allow", "message": f"Planification : {task[:80]}"},
    )
    plan = await agent(
        _with_directive(
            f"Établis un plan d'action concis et vérifiable pour la tâche : {task}",
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

try:
    build = await builder(
        _with_directive(
            (
                f"Applique le plan suivant dans ta branche isolée :\n{plan}\n\n"
                "N'exécute jamais `git commit`, `git push`, `git tag` ni de montée de version release (strictement réservés au chef d'orchestre). "
                "Exécute `git add -N .` puis `git diff --output=.gravity_verified.patch -- . \":(exclude).gravity_verified.patch\"` dans ton worktree isolé, "
                "puis retourne le chemin absolu réel de ton répertoire de travail dans `workspace_path`, "
                "la liste des fichiers modifiés dans `modified_files`, un extrait ou résumé du diff dans `git_diff`, "
                "`refused` et un résumé factuel."
            ),
            builder_cav,
        ),
        BUILD_SCHEMA,
    )

    if build.get("refused"):
        _safe_update_live(
            stage="workflow:refused",
            caveman=caveman_level,
            nodes={"planner": "done", "builder": "fail", "reviewer": ""},
            event={"type": "BUILDER", "decision": "deny", "message": f"Tâche refusée : {build['summary']}"},
        )
        log(f"Exécution interrompue par le Builder (refus sécurité) : {build['summary']}")
    elif skip_verification:
        phase("3. Livraison directe (mode unsafe)")
        diff_full = _read_worktree_patch(
            str(build.get("workspace_path", "")), str(build.get("git_diff", ""))
        )
        if diff_full.strip():
            patch_file = WS_ROOT / ".agents/gravity-workflow/observability/last_verified.patch"
            patch_file.parent.mkdir(parents=True, exist_ok=True)
            patch_file.write_text(diff_full, encoding="utf-8")
            log(f"Patch sauvegardé dans {patch_file} (worktree source : {build['workspace_path']}).")
        _safe_update_live(
            stage="workflow:unsafe_done",
            pace=pace,
            verification="unsafe",
            caveman=caveman_level,
            nodes={"planner": "done", "builder": "done", "reviewer": ""},
            event={
                "type": "UNSAFE",
                "decision": "allow",
                "message": "Mode unsafe : revue indépendante, vérifications et tests désactivés.",
            },
        )
        log("Mode unsafe actif : livraison directe sans revue indépendante ni tests.")
    else:
        review = None
        for attempt in range(1, max_remediation_rounds + 2):
            phase(f"3.{attempt} Revue indépendante (passage {attempt})")
            _safe_update_live(
                stage=f"workflow:review:{attempt}",
                caveman=caveman_level,
                nodes={"planner": "done", "builder": "done", "reviewer": "active"},
                event={"type": "REVIEWER", "decision": "allow", "message": f"Audit indépendant passage {attempt}."},
            )
            diff_full = _read_worktree_patch(
                str(build.get("workspace_path", "")), str(build.get("git_diff", ""))
            )
            diff_for_prompt = (
                diff_full[:MAX_PROMPT_DIFF_CHARS]
                + "\n... [diff tronqué pour le prompt, inspecter directement le worktree] ..."
                if len(diff_full) > MAX_PROMPT_DIFF_CHARS
                else diff_full
            )
            review = await agent(
                _with_directive(
                    (
                        f"Audite à froid les fichiers produits pour la tâche : {task}\n"
                        f"Répertoire du livrable produit : {build['workspace_path']}\n"
                        f"Fichiers déclarés modifiés : {json.dumps(build['modified_files'], ensure_ascii=False)}\n"
                        f"Diff produit :\n```diff\n{diff_for_prompt}\n```\n"
                        "Inspecte directement les fichiers dans le répertoire indiqué. "
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

            log(f"Passage {attempt} -> Verdict : {effective_verdict} | {review['summary']}")

            if effective_verdict == "PASS":
                if diff_full.strip():
                    patch_file = WS_ROOT / ".agents/gravity-workflow/observability/last_verified.patch"
                    patch_file.parent.mkdir(parents=True, exist_ok=True)
                    patch_file.write_text(diff_full, encoding="utf-8")
                    log(f"Patch vérifié sauvegardé dans {patch_file} (worktree source : {build['workspace_path']}).")
                _safe_update_live(
                    stage="workflow:done",
                    caveman=caveman_level,
                    nodes={"planner": "done", "builder": "done", "reviewer": "done"},
                    event={"type": "REVIEWER", "decision": "allow", "message": f"PASS (passage {attempt})."},
                )
                break

            if attempt > max_remediation_rounds:
                _safe_update_live(
                    stage="workflow:escalated",
                    caveman=caveman_level,
                    nodes={"planner": "done", "builder": "fail", "reviewer": "fail"},
                    event={
                        "type": "REVIEWER",
                        "decision": "force_ask",
                        "message": "Échec après remédiation maximale : escalade humaine requise.",
                    },
                )
                log("Échec après épuisement des tentatives de remédiation. Escalade humaine requise.")
                break

            phase(f"4.{attempt} Remédiation ciblée")
            _safe_update_live(
                stage=f"workflow:remediation:{attempt}",
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
                        f"dans ton worktree courant ({build['workspace_path']}) :\n"
                        f"{findings_block}\n"
                        "Ne modifie pas ce qui est déjà conforme. "
                        "Exécute ensuite `git add -N .` puis `git diff --output=.gravity_verified.patch -- . \":(exclude).gravity_verified.patch\"` et retourne `workspace_path`, "
                        "`modified_files`, `git_diff`, `refused` et le résumé."
                    ),
                    builder_cav,
                ),
                BUILD_SCHEMA,
            )
            if build.get("refused"):
                _safe_update_live(
                    stage="workflow:refused",
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
