#!/usr/bin/env python3
"""Validateur de conformité d'un workspace projet initialisé par `/gravity-workflow` (100 % stdlib)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Set

from _shared import validator_core  # noqa: E402

from .base import (
    MAX_ACTIVE_TOKENS,
    MAX_AGENTS_MD_BYTES,
    MAX_ALWAYS_ON_BYTES,
    MAX_RULE_BYTES,
    SECRET_LEAK_RE,
    VALID_RULE_TRIGGERS,
    Report,
    check_utf8_no_bom_lf,
    parse_frontmatter,
    run_hook_dry_runs,
    validate_agent_file,
    validate_hooks_config,
)
from .plugin_validator import TEMPLATE_MAPPINGS

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
AT_INCLUDE_RE = re.compile(r"@\[[^\]]*\]\(([^)]+)\)")


def _expand_at_includes(text: str, base_dir: Path, ws_root: Path, visited: Set[Path]) -> str:
    """Résout récursivement les inclusions locales `@[label](path)` avec retrait de frontmatter."""
    def _replace(match: re.Match[str]) -> str:
        raw_target = match.group(1).strip()
        if not raw_target or "://" in raw_target:
            return match.group(0)
        rel_path = Path(raw_target)
        candidate = (base_dir / rel_path).resolve()
        if not candidate.is_file():
            candidate = (ws_root / rel_path).resolve()
        if not candidate.is_file() or candidate in visited:
            return match.group(0)
        try:
            candidate.relative_to(ws_root.resolve())
        except ValueError:
            return match.group(0)
        _, inc_body = parse_frontmatter(candidate.read_text(encoding="utf-8", errors="replace"))
        return _expand_at_includes(inc_body, candidate.parent, ws_root, visited | {candidate})

    return AT_INCLUDE_RE.sub(_replace, text)


def _validate_rules_and_charter(ws: Path, report: Report) -> None:
    active_chars = 0
    agents_md = ws / "AGENTS.md"
    if not agents_md.is_file():
        report.error("AGENTS.md manquant à la racine du workspace.")
    else:
        check_utf8_no_bom_lf(agents_md, "AGENTS.md", report, max_bytes=MAX_AGENTS_MD_BYTES)
        active_chars += len(_expand_at_includes(agents_md.read_text(encoding="utf-8-sig", errors="replace"), agents_md.parent, ws, {agents_md.resolve()}))

    gemini_md = ws / "GEMINI.md"
    if gemini_md.is_file():
        active_chars += len(_expand_at_includes(gemini_md.read_text(encoding="utf-8"), gemini_md.parent, ws, {gemini_md.resolve()}))

    rules_dir = ws / ".agents/rules"
    safety_file = rules_dir / "01-safety-boundaries.md"
    if not safety_file.is_file():
        report.error(".agents/rules/01-safety-boundaries.md manquant.")
    else:
        raw_safety = safety_file.read_text(encoding="utf-8")
        expanded_safety = _expand_at_includes(raw_safety, safety_file.parent, ws, {safety_file.resolve()})
        size = max(safety_file.stat().st_size, len(expanded_safety.encode("utf-8")))
        if size > MAX_ALWAYS_ON_BYTES:
            report.error(f"01-safety-boundaries.md dépasse 2 Ko ({size} octets après inclusions).")
        if parse_frontmatter(raw_safety)[0].get("trigger") != "always_on":
            report.error("01-safety-boundaries.md doit déclarer 'trigger: always_on' (sinon la règle est silencieusement ignorée par Antigravity).")

    if rules_dir.is_dir():
        for rule in sorted(rules_dir.glob("*.md")):
            text = rule.read_text(encoding="utf-8")
            expanded_rule = _expand_at_includes(text, rule.parent, ws, {rule.resolve()})
            expanded_bytes = max(rule.stat().st_size, len(expanded_rule.encode("utf-8")))
            if expanded_bytes > MAX_RULE_BYTES:
                report.error(f"{rule.name} dépasse la limite par fichier de {MAX_RULE_BYTES} octets ({expanded_bytes}).")
            meta, _ = parse_frontmatter(text)
            trig = meta.get("trigger")
            if trig not in VALID_RULE_TRIGGERS:
                report.error(f"{rule.name} : frontmatter 'trigger' absent ou invalide ({trig!r}). Attendu : {sorted(VALID_RULE_TRIGGERS)}.")
            if trig == "always_on":
                active_chars += len(expanded_rule)
            elif trig == "model_decision" and not str(meta.get("description", "")).strip():
                report.error(f"{rule.name} : 'description' obligatoire avec 'trigger: model_decision'.")
            elif trig == "glob":
                globs_val = meta.get("globs") if "globs" in meta else meta.get("glob")
                if not isinstance(globs_val, str) or not globs_val.strip():
                    report.error(f"{rule.name} : 'globs' doit être une chaîne séparée par des virgules (jamais un tableau YAML).")
        if not (rules_dir / "05-git-versioning.md").is_file():
            report.info("Règle .agents/rules/05-git-versioning.md absente : /gravity-workflow ou init_workspace.py --ensure-git-rule l'ajoutera automatiquement (chef d'orchestre uniquement).")

    if (est_active_tokens := active_chars // 4) >= MAX_ACTIVE_TOKENS:
        report.error(f"Budget contexte actif dépassé : ~{est_active_tokens} tokens (plafond < {MAX_ACTIVE_TOKENS}).")

    root_gitignore = ws / ".gitignore"
    if not root_gitignore.is_file():
        report.info("Aucun .gitignore racine détecté : pensez à ignorer .env, secrets/, *.pem et *.key.")
    elif ".env" not in root_gitignore.read_text(encoding="utf-8", errors="replace"):
        report.info(".gitignore racine ne mentionne pas '.env' : pensez à protéger les secrets locaux.")


def _validate_mcp_and_overrides(ws: Path, report: Report) -> None:
    mcp_config = ws / ".agents/mcp_config.json"
    if not mcp_config.is_file():
        report.error(".agents/mcp_config.json manquant.")
    else:
        mcp_data = validator_core._read_json_checked(mcp_config, ".agents/mcp_config.json", report)
        if mcp_data is not None:
            if not isinstance(mcp_data.get("mcpServers"), dict):
                report.error(".agents/mcp_config.json doit contenir un objet 'mcpServers'.")
            else:
                for srv_name, srv_cfg in mcp_data["mcpServers"].items():
                    if isinstance(srv_cfg, dict) and "httpUrl" in srv_cfg:
                        report.error(f".agents/mcp_config.json ({srv_name}) : 'httpUrl' non supporté (utiliser 'serverUrl').")
            if SECRET_LEAK_RE.search(mcp_config.read_text(encoding="utf-8", errors="replace")):
                report.error(".agents/mcp_config.json contient un motif de secret en clair.")

    agents_dir = ws / ".agents/agents"
    if agents_dir.is_dir():
        for agent_md in sorted(agents_dir.glob("*.md")):
            is_ro = agent_md.stem in ("planner", "reviewer")
            validate_agent_file(agent_md, report, readonly=is_ro, prefer_pro=is_ro)

    ws_hooks_json = ws / ".agents/hooks.json"
    if ws_hooks_json.is_file():
        validate_hooks_config(ws_hooks_json, report, is_portable_template=(ws.name == "workspace-template"))


def _validate_profile_json(profile_json: Path, report: Report) -> None:
    if not profile_json.is_file():
        report.error(".agents/gravity-workflow/profile.json manquant.")
        return
    pdata = validator_core._read_json_checked(profile_json, ".agents/gravity-workflow/profile.json", report)
    if pdata is None:
        return
    mode_val, level_val, locked_val = pdata.get("mode"), pdata.get("current_level"), pdata.get("locked")
    level_aliases = {"low": "quick", "medium": "standard", "high": "deep", "ultra": "deep"}
    valid_levels = {"quick", "standard", "deep"} | set(level_aliases)
    valid_modes = {"evo"} | valid_levels
    if mode_val not in valid_modes:
        report.error(f".agents/gravity-workflow/profile.json : 'mode' ({mode_val!r}) invalide (attendu : {sorted({'evo', 'quick', 'standard', 'deep'})}).")
    if level_val not in valid_levels:
        report.error(f".agents/gravity-workflow/profile.json : 'current_level' ({level_val!r}) invalide (attendu : {sorted({'quick', 'standard', 'deep'})}).")
    if "pace" in pdata and pdata.get("pace") not in {"slow", "fast"}:
        report.error(f".agents/gravity-workflow/profile.json : 'pace' ({pdata.get('pace')!r}) invalide.")
    if pdata.get("verification") == "unsafe":
        report.error(".agents/gravity-workflow/profile.json : 'verification: unsafe' ne doit jamais être persisté.")
    if not isinstance(locked_val, bool):
        report.error(".agents/gravity-workflow/profile.json : 'locked' doit être un booléen.")
    elif mode_val in valid_modes and locked_val != (mode_val != "evo"):
        report.error(f".agents/gravity-workflow/profile.json : incohérence entre mode={mode_val!r} et locked={locked_val!r}.")
    if mode_val in valid_levels and level_aliases.get(str(level_val), str(level_val)) != level_aliases.get(str(mode_val), str(mode_val)):
        report.error(f".agents/gravity-workflow/profile.json : en mode verrouillé {mode_val!r}, 'current_level' ({level_val!r}) doit correspondre à 'mode'.")
    if "caveman" in pdata and pdata.get("caveman") not in {"off", "lite", "full", "ultra"}:
        report.error(f".agents/gravity-workflow/profile.json : 'caveman' ({pdata.get('caveman')!r}) invalide.")
    if "caveman_locked" in pdata and not isinstance(pdata.get("caveman_locked"), bool):
        report.error(".agents/gravity-workflow/profile.json : 'caveman_locked' doit être un booléen.")
    if "execution_engine" in pdata and pdata.get("execution_engine") not in {"swarm", "run_workflow"}:
        report.error(f".agents/gravity-workflow/profile.json : 'execution_engine' ({pdata.get('execution_engine')!r}) invalide.")


def _validate_gravity_project_state(ws: Path, report: Report) -> None:
    gravity_dir = ws / ".agents/gravity-workflow"
    if not gravity_dir.is_dir():
        report.error("Dossier .agents/gravity-workflow/ manquant (exécuter /gravity-workflow ou scripts/init_workspace.py).")
        return
    for required_rel in ("PROGRESS.md", "BACKLOG.md", ".gitignore"):
        if not (gravity_dir / required_rel).is_file():
            report.error(f".agents/gravity-workflow/{required_rel} manquant.")
    _validate_profile_json(gravity_dir / "profile.json", report)
    live_state = gravity_dir / "observability/live_state.json"
    if not live_state.is_file():
        report.error(".agents/gravity-workflow/observability/live_state.json manquant.")
    else:
        validator_core._read_json_checked(live_state, ".agents/gravity-workflow/observability/live_state.json", report)
    cases_json = gravity_dir / "evals/cases.json"
    if not cases_json.is_file():
        report.error(".agents/gravity-workflow/evals/cases.json manquant.")
    else:
        try:
            cases_data = json.loads(cases_json.read_text(encoding="utf-8"))
            if not isinstance(cases_data, list) or not (10 <= len(cases_data) <= 15):
                report.warn(f".agents/gravity-workflow/evals/cases.json contient {len(cases_data) if isinstance(cases_data, list) else '?'} cas (recommandé : 10 à 15).")
        except Exception as exc:
            report.error(f".agents/gravity-workflow/evals/cases.json invalide : {exc}")


def _validate_workspace_template_drift(ws: Path, report: Report) -> None:
    """Vérifie la parité octet pour octet de `workspace-template/` avec `plugin/templates/`."""
    templates_dir = PLUGIN_ROOT / "templates"
    if ws.name != "workspace-template" or ws.resolve().parent != PLUGIN_ROOT.resolve().parent or not templates_dir.is_dir():
        return
    for src_rel, wt_rel in TEMPLATE_MAPPINGS:
        src_path, wt_path = templates_dir / src_rel, ws / wt_rel
        if src_path.is_file() and (not wt_path.is_file() or src_path.read_bytes() != wt_path.read_bytes()):
            report.error(f"Dérive détectée entre templates/{src_rel} et workspace-template/{wt_rel}.")
    p_cases, wt_cases = PLUGIN_ROOT / "evals/cases.json", ws / ".agents/gravity-workflow/evals/cases.json"
    if p_cases.is_file() and wt_cases.is_file() and p_cases.read_bytes() != wt_cases.read_bytes():
        report.error("Dérive détectée entre evals/cases.json et workspace-template/.agents/gravity-workflow/evals/cases.json.")


def validate_workspace(ws: Path, report: Report) -> None:
    """Audite un workspace projet initialisé par `/gravity-workflow`."""
    _validate_rules_and_charter(ws, report)
    _validate_mcp_and_overrides(ws, report)
    _validate_gravity_project_state(ws, report)
    _validate_workspace_template_drift(ws, report)
    ws_hooks_dir = ws / ".agents/gravity-workflow/hooks"
    if ws_hooks_dir.is_dir():
        run_hook_dry_runs(ws_hooks_dir, ws / ".agents", report)
    elif (PLUGIN_ROOT / "hooks").is_dir():
        run_hook_dry_runs(PLUGIN_ROOT / "hooks", PLUGIN_ROOT, report)
    else:
        report.error("Aucun moteur de hooks trouvé (ni dans le plugin ni dans le workspace).")
