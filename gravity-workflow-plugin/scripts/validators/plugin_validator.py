#!/usr/bin/env python3
"""Validateur de conformité du plugin autonome `gravity-workflow-plugin/` (100 % stdlib)."""

import json
import re
from pathlib import Path

from .base import (
    EXPECTED_REFERENCES,
    KEBAB_RE,
    MAX_DESC_LEN,
    MODULE_JARGON_RE,
    Report,
    check_executable,
    check_stdlib_only,
    parse_frontmatter,
    run_hook_dry_runs,
    validate_agent_file,
    validate_hooks_config,
)


def validate_plugin(root: Path, report: Report) -> None:
    """Audite le manifeste, le logo, les skills, les agents, les hooks, le sidecar et le moteur du plugin."""
    manifest_path = root / "plugin.json"
    if not manifest_path.is_file():
        report.error("plugin.json introuvable à la racine du plugin.")
        return

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.error(f"plugin.json invalide : {exc}")
        return

    if "$schema" not in manifest:
        report.error("plugin.json : clé '$schema' manquante.")
    name = str(manifest.get("name", ""))
    if not KEBAB_RE.match(name):
        report.error(f"plugin.json : 'name' ({name!r}) doit être en kebab-case.")
    desc = str(manifest.get("description", ""))
    if not desc or len(desc) > MAX_DESC_LEN:
        report.error(
            f"plugin.json : 'description' vide ou > {MAX_DESC_LEN} caractères ({len(desc)})."
        )
    if "skills" in manifest:
        raw_skills = manifest["skills"]
        skill_items = raw_skills if isinstance(raw_skills, list) else [raw_skills]
        redundant = [str(s).rstrip("/") for s in skill_items if str(s).rstrip("/") in ("skills", "./skills")]
        if redundant:
            report.error("plugin.json : 'skills' redondant avec le dossier par défaut 'skills/' doit être omis.")
    prompts = manifest.get("suggestedPrompts")
    if not isinstance(prompts, list) or len(prompts) != 3:
        report.warn("plugin.json : 3 'suggestedPrompts' sont recommandés.")

    logo_rel = str(manifest.get("logo", ""))
    logo_path = root / logo_rel
    if not logo_rel or not logo_path.is_file():
        report.error(f"Logo introuvable : {logo_rel}")
    else:
        svg = logo_path.read_text(encoding="utf-8", errors="replace")
        vb = re.search(
            r"""viewBox\s*=\s*["']0\s+0\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)["']""",
            svg,
        )
        if not vb or float(vb.group(1)) <= 0 or float(vb.group(1)) != float(vb.group(2)):
            report.error(f"Logo {logo_rel} : ratio 1:1 (viewBox carré) non détecté.")

    agents_dir = root / "agents"
    validate_agent_file(agents_dir / "planner.md", report, readonly=True, prefer_pro=True)
    validate_agent_file(agents_dir / "builder.md", report, readonly=False, prefer_pro=False)
    validate_agent_file(agents_dir / "reviewer.md", report, readonly=True, prefer_pro=True)

    hooks_json = root / "hooks.json"
    validate_hooks_config(hooks_json, report, is_portable_template=True)

    local_mods = {
        "validators",
        "state_writer",
        "profile_engine",
        "caveman_protocol",
        "hook_runtime",
        "safety_policy",
        "eval_checks",
        "init_workspace",
        "server",
    }
    exec_scripts = {
        Path("scripts/validate.py"),
        Path("scripts/init_workspace.py"),
        Path("sidecars/live-widget/server.py"),
        Path("hooks/pre_tool_guard.py"),
        Path("hooks/post_tool_format.py"),
        Path("hooks/session_start_caveman.py"),
        Path("hooks/stop_session_logger.py"),
        Path("observability/statusline.py"),
        Path("observability/profile_engine.py"),
    }
    for sub in ("scripts", "hooks", "workflows", "evals", "observability"):
        sub_dir = root / sub
        if not sub_dir.is_dir():
            report.error(f"Dossier moteur manquant dans le plugin : {sub}/")
            continue
        for py_file in sorted(sub_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            check_stdlib_only(py_file, report, local_modules=local_mods)
            if py_file.relative_to(root) in exec_scripts:
                check_executable(py_file, report)

    sidecar_json = root / "sidecars/live-widget/sidecar.json"
    if sidecar_json.is_file():
        try:
            sc_raw = sidecar_json.read_text(encoding="utf-8")
            if "${workspaceFolder}" in sc_raw:
                report.error("sidecar.json contient le placeholder non supporté '${workspaceFolder}'.")
            sc = json.loads(sc_raw)
            for arg in sc.get("args", []):
                if str(arg).endswith(".py"):
                    target = sidecar_json.parent / arg
                    if not target.is_file():
                        report.error(f"Script Sidecar manquant : {target}")
                    else:
                        check_executable(target, report)
                        check_stdlib_only(target, report)
        except Exception as exc:
            report.error(f"sidecar.json invalide : {exc}")
    else:
        report.warn("sidecars/live-widget/sidecar.json absent.")

    for legacy_skill in ("gravity", "gravity-review", "gravity-evals", "gravity-status"):
        if (root / "skills" / legacy_skill).exists():
            report.error(
                f"Ancien skill redondant détecté : skills/{legacy_skill}/ doit être supprimé (unifié dans /gravity-workflow)."
            )

    for skill_name in ("gravity-workflow", "caveman"):
        skill_md = root / "skills" / skill_name / "SKILL.md"
        if not skill_md.is_file():
            report.error(f"skills/{skill_name}/SKILL.md introuvable.")
        else:
            content = skill_md.read_text(encoding="utf-8")
            sk_meta, _ = parse_frontmatter(content)
            if sk_meta.get("name") != skill_name:
                report.error(
                    f"skills/{skill_name}/SKILL.md : 'name' ({sk_meta.get('name')!r}) "
                    f"doit correspondre au dossier parent ({skill_name!r})."
                )
            sk_desc = str(sk_meta.get("description", "")).strip()
            if not sk_desc or len(sk_desc) > MAX_DESC_LEN:
                report.error(
                    f"skills/{skill_name}/SKILL.md : 'description' vide ou > {MAX_DESC_LEN} caractères ({len(sk_desc)})."
                )
            if MODULE_JARGON_RE.search(content):
                report.error(f"skills/{skill_name}/SKILL.md contient le jargon interdit 'Module XX'.")

    refs_dir = root / "skills/gravity-workflow/references"
    if not refs_dir.is_dir():
        report.error("Dossier skills/gravity-workflow/references/ introuvable.")
    else:
        for expected_ref in EXPECTED_REFERENCES:
            ref_path = refs_dir / expected_ref
            if not ref_path.is_file():
                report.error(f"Fiche de référence manquante : {ref_path}")
        for ref in sorted(refs_dir.glob("*.md")):
            if MODULE_JARGON_RE.search(ref.read_text(encoding="utf-8")):
                report.error(f"{ref} contient le jargon interdit 'Module XX'.")

    cases_json = root / "evals/cases.json"
    if not cases_json.is_file():
        report.error("evals/cases.json manquant dans le plugin.")
    else:
        try:
            cases_data = json.loads(cases_json.read_text(encoding="utf-8"))
            if not isinstance(cases_data, list) or not (10 <= len(cases_data) <= 15):
                report.warn(
                    f"evals/cases.json contient {len(cases_data) if isinstance(cases_data, list) else '?'} "
                    "cas (recommandé : 10 à 15)."
                )
        except Exception as exc:
            report.error(f"evals/cases.json invalide : {exc}")

    run_hook_dry_runs(root / "hooks", root, report)
