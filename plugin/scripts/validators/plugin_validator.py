#!/usr/bin/env python3
"""Validateur de conformité du plugin autonome `plugin/` (délègue à `_shared.validator_core`)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from _shared import validator_core  # noqa: E402
from _shared.validator_core import ValidatorConfig  # noqa: E402

from .base import (
    EXPECTED_CAVEMAN_REFERENCES,
    EXPECTED_REFERENCES,
    MAX_AGENTS_MD_BYTES,
    MAX_CAVEMAN_SKILL_MD_BYTES,
    REQUIRED_PRETOOL_MATCHERS,
    SECRET_LEAK_RE,
    Report,
    check_executable,
    check_utf8_no_bom_lf,
    run_hook_dry_runs,
    validate_agent_file,
)

VALID_UI_MODES = frozenset({"none", "core", "transition-sidecar", "transition-mcp-app", "mcp-app"})
EXEC_SCRIPTS: tuple[str, ...] = (
    "scripts/validate.py",
    "scripts/init_workspace.py",
    "hooks/pre_tool_guard.py",
    "hooks/post_tool_format.py",
    "hooks/session_start_caveman.py",
    "hooks/stop_session_logger.py",
    "observability/statusline.py",
    "observability/profile_engine.py",
    "workflows/patch_apply.py",
    "workflows/swarm_wave.py",
)
OPTIONAL_EXEC_SCRIPTS: tuple[str, ...] = ("scripts/dashboard_provider.py",)
TEMPLATE_MAPPINGS = (
    ("AGENTS.md", "AGENTS.md"),
    (".agents/mcp_config.json", ".agents/mcp_config.json"),
    (".agents/rules/01-safety-boundaries.md.tmpl", ".agents/rules/01-safety-boundaries.md"),
    (".agents/rules/02-python-stdlib.md", ".agents/rules/02-python-stdlib.md"),
    (".agents/rules/03-api-architecture.md", ".agents/rules/03-api-architecture.md"),
    (".agents/rules/04-release-checklist.md", ".agents/rules/04-release-checklist.md"),
    (".agents/rules/05-git-versioning.md", ".agents/rules/05-git-versioning.md"),
    (".agents/rules/06-engineering-principles.md", ".agents/rules/06-engineering-principles.md"),
    (".agents/gravity-workflow/.gitignore", ".agents/gravity-workflow/.gitignore"),
    (".agents/gravity-workflow/BACKLOG.md", ".agents/gravity-workflow/BACKLOG.md"),
    (".agents/gravity-workflow/profile.json", ".agents/gravity-workflow/profile.json"),
)
WORKFLOW_LOCAL_MODULES: frozenset[str] = validator_core.ALLOWED_LOCAL_MODULES | frozenset({
    "_shared", "dashboard_provider", "policy", "constants", "paths", "shell_parser", "git_and_fs",
    "python_ast", "python_ast_state", "python_ast_eval", "python_ast_cands", "python_ast_imports", "python_ast_readonly", "python_ast_flow", "python_ast_calls",
    "inline_scripts", "js_eval", "stdin_inspect", "evaluator", "safety_policy",
    "state_writer", "profile_engine", "evo_engine", "metrics_signals", "caveman_protocol",
    "eval_checks", "init_workspace", "validators", "base", "plugin_validator", "workspace_validator",
    "worktree_ops", "patch_apply", "swarm_wave", "wave_runner", "review_pipeline",
})
_NOT_CONTAINS_SECRET_RE = re.compile(r"not_contains\([^)]*sk-" + r"live-[^)]*\)")


def _filter_cases_secret_false_positive(root: Path, report: Report) -> None:
    """Ignore uniquement le faux positif de prédicat `not_contains` dans evals/cases.json."""
    target_err = "Secret potentiel détecté dans evals/cases.json"
    if target_err not in report.errors:
        return
    cases_path = root / "evals" / "cases.json"
    if not cases_path.is_file():
        return
    stripped = _NOT_CONTAINS_SECRET_RE.sub("", cases_path.read_text(encoding="utf-8", errors="replace"))
    if not SECRET_LEAK_RE.search(stripped):
        report.errors = [err for err in report.errors if err != target_err]


def _validate_templates_and_drift(root: Path, report: Report) -> None:
    templates_dir = root / "templates"
    if not templates_dir.is_dir():
        report.error("Dossier templates/ manquant dans le plugin.")
    else:
        for src_rel, _ in TEMPLATE_MAPPINGS:
            src_path = templates_dir / src_rel
            if not src_path.is_file():
                report.error(f"Template requis manquant : templates/{src_rel}")
            else:
                check_utf8_no_bom_lf(
                    src_path,
                    f"templates/{src_rel}",
                    report,
                    max_bytes=MAX_AGENTS_MD_BYTES if src_rel == "AGENTS.md" else None,
                )
    wt_dir = root.parent / "workspace-template"
    if templates_dir.is_dir() and wt_dir.is_dir():
        for src_rel, wt_rel in TEMPLATE_MAPPINGS:
            src_path, wt_path = templates_dir / src_rel, wt_dir / wt_rel
            if not src_path.is_file():
                continue
            if not wt_path.is_file():
                report.error(f"Dérive workspace-template : fichier manquant {wt_rel}.")
            elif src_path.read_bytes() != wt_path.read_bytes():
                report.error(f"Dérive détectée entre templates/{src_rel} et workspace-template/{wt_rel}.")
        p_cases, wt_cases = root / "evals/cases.json", wt_dir / ".agents/gravity-workflow/evals/cases.json"
        if p_cases.is_file() and wt_cases.is_file() and p_cases.read_bytes() != wt_cases.read_bytes():
            report.error("Dérive détectée entre evals/cases.json et workspace-template/.agents/gravity-workflow/evals/cases.json.")


def _validate_eval_cases(root: Path, report: Report) -> None:
    cases_json = root / "evals/cases.json"
    if not cases_json.is_file():
        report.error("evals/cases.json manquant dans le plugin.")
        return
    try:
        cases_data = json.loads(cases_json.read_text(encoding="utf-8"))
        if not isinstance(cases_data, list):
            report.warn("evals/cases.json contient ? cas (recommandé : 10 à 15).")
            return
        evals_dir = str((root / "evals").resolve())
        if evals_dir not in sys.path:
            sys.path.insert(0, evals_dir)
        is_manual = None
        try:
            import eval_checks  # type: ignore[import-not-found]
            is_manual = getattr(eval_checks, "is_manual_case", None)
        except Exception:
            is_manual = None
        auto_cases = [c for c in cases_data if isinstance(c, dict) and not is_manual(c)] if callable(is_manual) else cases_data
        if not (10 <= len(auto_cases) <= 15):
            report.warn(f"evals/cases.json contient {len(auto_cases)} cas (recommandé : 10 à 15).")
    except Exception as exc:
        report.error(f"evals/cases.json invalide : {exc}")


def run_workflow_plugin_checks(root: Path, report: Report) -> None:
    """Contrôles spécifiques Gravity Workflow exécutés via `ValidatorConfig.extra_check_fn`."""
    _filter_cases_secret_false_positive(root, report)
    mod_path = root / "config" / "module.json"
    if mod_path.is_file():
        try:
            mdata = json.loads(mod_path.read_text(encoding="utf-8-sig"))
            ui_mode = (mdata.get("ui") or {}).get("mode") if isinstance(mdata, dict) and isinstance(mdata.get("ui"), dict) else None
            if ui_mode is not None and ui_mode not in VALID_UI_MODES:
                report.error(f"config/module.json : 'ui.mode' ({ui_mode!r}) invalide (attendu : {sorted(VALID_UI_MODES)}).")
        except (OSError, ValueError):
            pass

    agents_dir = root / "agents"
    validate_agent_file(agents_dir / "planner.md", report, readonly=True, prefer_pro=True)
    validate_agent_file(agents_dir / "builder.md", report, readonly=False, prefer_pro=False)
    validate_agent_file(agents_dir / "reviewer.md", report, readonly=True, prefer_pro=True)

    for rel in EXEC_SCRIPTS:
        script_p = root / rel
        if script_p.is_file():
            check_executable(script_p, report)
    for rel in OPTIONAL_EXEC_SCRIPTS:
        script_p = root / rel
        if script_p.is_file():
            check_executable(script_p, report)

    if (root / "sidecars" / "live-widget").exists():
        report.warn("Vestige transitoire détecté : sidecars/live-widget/ doit être retiré (remplacé par /gravity dashboard).")
    for legacy_skill in ("gravity", "gravity-review", "gravity-evals", "gravity-status"):
        if (root / "skills" / legacy_skill).exists():
            report.error(f"Ancien skill redondant détecté : skills/{legacy_skill}/ doit être supprimé (unifié dans /gravity-workflow).")

    cav_skill = root / "skills" / "caveman" / "SKILL.md"
    if cav_skill.is_file():
        check_utf8_no_bom_lf(cav_skill, "skills/caveman/SKILL.md", report, max_bytes=MAX_CAVEMAN_SKILL_MD_BYTES)
    refs_dir = root / "skills/gravity-workflow/references"
    if not refs_dir.is_dir():
        report.error("Dossier skills/gravity-workflow/references/ introuvable.")
    else:
        for exp in EXPECTED_REFERENCES:
            if not (refs_dir / exp).is_file():
                report.error(f"Fiche de référence manquante : {refs_dir / exp}")
    cav_refs = root / "skills/caveman/references"
    if not cav_refs.is_dir():
        report.error("Dossier skills/caveman/references/ introuvable.")
    else:
        for exp in EXPECTED_CAVEMAN_REFERENCES:
            if not (cav_refs / exp).is_file():
                report.error(f"Fiche de référence manquante : {cav_refs / exp}")

    _validate_eval_cases(root, report)
    _validate_templates_and_drift(root, report)
    run_hook_dry_runs(root / "hooks", root, report)


def build_validator_config(
    root: Path,
    *,
    strict: bool = False,
    json_output: bool = False,
    allowed_local_modules: Optional[frozenset[str]] = None,
    workspace_validator_fn: Any = None,
) -> ValidatorConfig:
    """Construit la configuration `_shared.validator_core` pour `gravity-workflow`."""
    return ValidatorConfig(
        plugin_root=root,
        strict=strict,
        json_output=json_output,
        expected_plugin_name="gravity-workflow",
        expected_module_id="workflow",
        required_dirs=("assets", "config", "hooks", "observability", "scripts", "skills", "templates", "workflows", "evals", "_shared"),
        allowed_local_modules=allowed_local_modules or WORKFLOW_LOCAL_MODULES,
        require_module_contract=True,
        require_hooks_config=True,
        require_rules_agents=False,
        skill_max_bytes=6000,
        repo_agents_max_bytes=2500,
        is_portable_template=True,
        check_links=False,
        required_pretool_matchers=REQUIRED_PRETOOL_MATCHERS,
        extra_check_fn=run_workflow_plugin_checks,
        workspace_validator_fn=workspace_validator_fn,
    )


def validate_plugin(root: Path, report: Report) -> None:
    """Audite le plugin Gravity Workflow via `_shared.validator_core` et les contrôles métier."""
    validator_core.validate_plugin_tree(build_validator_config(root), report)
