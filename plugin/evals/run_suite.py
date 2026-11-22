"""Runner parallèle Gravity pour `run_workflow` (stdlib Python 3 uniquement)."""

import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

WS_ROOT = Path(WORKSPACES[0]) if WORKSPACES else Path.cwd()
PLUGIN_ROOT = Path(__file__).resolve().parent.parent if "__file__" in globals() else WS_ROOT
EVALS_DIR = (PLUGIN_ROOT / "evals").resolve()
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

from eval_checks import render_markdown_report, run_deterministic_checks  # type: ignore[import-not-found]

WS_CASES_PATH = WS_ROOT / ".agents/gravity-workflow/evals/cases.json"
CASES_PATH = WS_CASES_PATH if WS_CASES_PATH.is_file() else (PLUGIN_ROOT / "evals/cases.json")
REPORT_JSON = WS_ROOT / ".agents/gravity-workflow/evals/report.json"
REPORT_MD = WS_ROOT / ".agents/gravity-workflow/evals/report.md"

CANDIDATE_SCHEMA = {
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

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "comprehension_check": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "majors": {"type": "array", "items": {"type": "string"}},
        "uncertain": {"type": "array", "items": {"type": "string"}},
        "nits": {"type": "array", "items": {"type": "string"}},
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


def _load_level_preset(level_override: Any) -> Dict[str, Any]:
    fallback: Dict[str, Any] = {
        "eval_candidate": "flash",
        "eval_judge": "pro",
        "level": "high",
        "mode": "evo",
    }
    try:
        obs_dir = (PLUGIN_ROOT / "observability").resolve()
        if str(obs_dir) not in sys.path:
            sys.path.insert(0, str(obs_dir))
        from profile_engine import get_active_preset  # type: ignore[import-not-found]

        override_str = str(level_override).strip() if level_override else None
        return get_active_preset(WS_ROOT, level_override=override_str)
    except Exception:
        return fallback


preset = _load_level_preset(args.get("level"))
candidate_model = args.get("candidate_model", preset["eval_candidate"])
judge_model = args.get("judge_model", preset["eval_judge"])
selected_ids = args.get("case_ids")


async def evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Exécution candidat isolé -> pré-filtre déterministe -> juge indépendant (isolation d'erreur)."""
    cand = agent(
        role=f"Candidate {case['id']}",
        type_name="builder",
        workspace="branch",
        model=candidate_model,
    )
    try:
        setup_note = ""
        setup_files = case.get("setup_files")
        if isinstance(setup_files, dict) and setup_files:
            setup_note = (
                "\nFixtures préalables à initialiser dans ta branche avant d'exécuter la tâche :\n"
                + json.dumps(setup_files, indent=2, ensure_ascii=False)
                + "\n"
            )

        candidate = await cand(
            (
                f"{case['input']}\n"
                f"{setup_note}\n"
                "Retourne impérativement le chemin absolu réel de ton répertoire de travail courant "
                "dans `workspace_path`, la liste des fichiers modifiés dans `modified_files`, "
                "le diff complet dans `git_diff`, `refused: true` si la demande violait une règle de sécurité, "
                "et un résumé factuel."
            ),
            CANDIDATE_SCHEMA,
        )

        ws_raw = str(candidate.get("workspace_path", "")).strip()
        if not ws_raw:
            return {
                "id": case["id"],
                "description": case["description"],
                "verdict": "FAIL",
                "deterministic_failures": ["Champ `workspace_path` vide retourné par le candidat."],
                "judge": None,
                "manual_checks": case.get("manual_checks", []),
            }
        ws_path = Path(ws_raw)
        if not ws_path.is_dir():
            return {
                "id": case["id"],
                "description": case["description"],
                "verdict": "FAIL",
                "deterministic_failures": [
                    f"Worktree candidat introuvable pour le pré-filtre déterministe : {ws_raw!r}"
                ],
                "judge": None,
                "manual_checks": case.get("manual_checks", []),
            }

        det_failures = run_deterministic_checks(case, ws_path, candidate)
        if det_failures:
            return {
                "id": case["id"],
                "description": case["description"],
                "verdict": "FAIL",
                "deterministic_failures": det_failures,
                "judge": None,
                "manual_checks": case.get("manual_checks", []),
            }

        judge = await agent(
            prompt=(
                f"Audite à froid le livrable du cas '{case['id']}' ({case['description']}).\n"
                f"Consigne initiale : {case['input']}\n"
                f"Répertoire du livrable produit : {ws_path}\n"
                f"Fichiers déclarés modifiés : {json.dumps(candidate.get('modified_files', []), ensure_ascii=False)}\n"
                f"Refus sécurité déclaré : {candidate.get('refused', False)}\n"
                f"Diff déclaré :\n```diff\n{candidate.get('git_diff', '')}\n```\n"
                "Inspecte directement les fichiers dans le répertoire indiqué. "
                "Applique la règle stricte Preuve ou silence et la grille à 4 niveaux (max 3 uncertain, max 3 nits)."
            ),
            schema=JUDGE_SCHEMA,
            role=f"Independent Judge {case['id']}",
            type_name="reviewer",
            workspace="inherit",
            model=judge_model,
        )
        judge["uncertain"] = list(judge.get("uncertain") or [])[:3]
        judge["nits"] = list(judge.get("nits") or [])[:3]

        has_blocking = bool(judge["blockers"] or judge["majors"])
        verdict = "FAIL" if has_blocking else judge["verdict"]

        return {
            "id": case["id"],
            "description": case["description"],
            "verdict": verdict,
            "deterministic_failures": [],
            "judge": judge,
            "manual_checks": case.get("manual_checks", []),
        }
    except Exception as exc:
        return {
            "id": case["id"],
            "description": case["description"],
            "verdict": "FAIL",
            "deterministic_failures": [
                f"Erreur d'exécution du cas ({type(exc).__name__}) : {exc}"
            ],
            "judge": None,
            "manual_checks": case.get("manual_checks", []),
        }
    finally:
        del cand


all_cases: List[Dict[str, Any]] = json.loads(CASES_PATH.read_text(encoding="utf-8"))
cases = (
    [c for c in all_cases if c.get("id") in set(selected_ids)]
    if isinstance(selected_ids, list) and selected_ids
    else all_cases
)

_safe_update_live(
    stage="evals:running",
    nodes={"planner": "done", "builder": "active", "reviewer": ""},
    event={"type": "EVALS", "decision": "allow", "message": f"Lancement de {len(cases)} cas."},
)

phase("1. Exécution parallèle & double filtre (candidat -> déterministe -> juge indépendant)")
results = await parallel(cases, lambda case: evaluate_case(case))

phase("2. Consolidation double sortie (report.json + report.md)")
passed_count = sum(1 for r in results if r["verdict"] == "PASS")
prefilter_rejected = sum(1 for r in results if r["deterministic_failures"])

summary = {
    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "passed": passed_count,
    "total": len(results),
    "prefilter_rejected": prefilter_rejected,
    "results": results,
}

REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
REPORT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
REPORT_MD.write_text(render_markdown_report(summary), encoding="utf-8")

all_pass = passed_count == len(results)
_safe_update_live(
    stage="evals:done",
    nodes={"planner": "done", "builder": "done", "reviewer": "done" if all_pass else "fail"},
    event={
        "type": "EVALS",
        "decision": "allow" if all_pass else "deny",
        "message": f"Score : {passed_count}/{len(results)} PASS (pré-filtre: {prefilter_rejected}).",
    },
)

log(f"Évaluation terminée : {passed_count}/{len(results)} PASS (rejets pré-filtre : {prefilter_rejected}).")
log(f"Rapports générés : {REPORT_MD} et {REPORT_JSON}")
