#!/usr/bin/env python3
"""Validateur de conformité du plugin autonome `plugin/` (100 % stdlib)."""

import json
import re
from pathlib import Path
from typing import Any, Dict

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

OFFICIAL_PLUGIN_FIELDS = frozenset(
    {
        "name",
        "displayName",
        "version",
        "description",
        "logo",
        "suggestedPrompts",
        "author",
        "homepage",
        "repository",
    }
)
OPTIONAL_PLUGIN_FIELDS = frozenset({"disabled"})
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
UTF8_BOM = b"\xef\xbb\xbf"
VALID_UI_MODES = frozenset({"none", "core", "transition-sidecar", "transition-mcp-app", "mcp-app"})


def _read_json_no_bom(path: Path, label: str, report: Report) -> Any:
    """Lit un fichier JSON en vérifiant l'absence de BOM UTF-8."""
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        report.error(f"{label} illisible : {exc}")
        return None
    if raw_bytes.startswith(UTF8_BOM):
        report.error(f"{label} : BOM UTF-8 interdit (encoder en UTF-8 sans BOM).")
    try:
        return json.loads(raw_bytes.decode("utf-8-sig"))
    except Exception as exc:
        report.error(f"{label} invalide : {exc}")
        return None


def _is_nonempty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


def _is_str_list(val: Any, *, allow_empty: bool = True, prefix: str = "") -> bool:
    if not isinstance(val, list):
        return False
    if not allow_empty and not val:
        return False
    for item in val:
        if not isinstance(item, str) or not item.strip():
            return False
        if prefix and not item.startswith(prefix):
            return False
    return True


def _validate_manifest_fields(manifest: Dict[str, Any], report: Report) -> str:
    """Valide strictement les 9 champs officiels Antigravity (+ disabled optionnel) de plugin.json."""
    keys = set(manifest.keys())
    missing = sorted(OFFICIAL_PLUGIN_FIELDS - keys)
    if missing:
        report.error(
            f"plugin.json : champ(s) officiel(s) manquant(s) : {', '.join(missing)}."
        )

    unknown = sorted(keys - (OFFICIAL_PLUGIN_FIELDS | OPTIONAL_PLUGIN_FIELDS))
    if unknown:
        report.error(
            f"plugin.json : clé(s) non officielle(s) interdite(s) : {', '.join(unknown)}."
        )

    name = manifest.get("name")
    if not isinstance(name, str) or not KEBAB_RE.match(name):
        report.error(f"plugin.json : 'name' ({name!r}) doit être en kebab-case.")
        name_str = ""
    else:
        name_str = name

    if not _is_nonempty_str(manifest.get("displayName")):
        report.error("plugin.json : 'displayName' doit être une chaîne non vide.")

    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER_RE.match(version.strip()):
        report.error(f"plugin.json : 'version' ({version!r}) doit respecter SemVer (X.Y.Z).")

    desc = manifest.get("description")
    if not isinstance(desc, str) or not desc.strip() or len(desc) > MAX_DESC_LEN:
        desc_len = len(desc) if isinstance(desc, str) else 0
        report.error(
            f"plugin.json : 'description' vide ou > {MAX_DESC_LEN} caractères ({desc_len})."
        )

    prompts = manifest.get("suggestedPrompts")
    if not _is_str_list(prompts, allow_empty=False) or len(prompts) != 3:
        report.error("plugin.json : 'suggestedPrompts' doit contenir exactement 3 chaînes non vides.")

    author = manifest.get("author")
    author_ok = _is_nonempty_str(author) or (
        isinstance(author, dict) and _is_nonempty_str(author.get("name"))
    )
    if not author_ok:
        report.error("plugin.json : 'author' invalide (attendu chaîne ou objet avec 'name').")

    homepage = manifest.get("homepage")
    if not isinstance(homepage, str) or not homepage.strip().startswith(("https://", "http://")):
        report.error("plugin.json : 'homepage' doit être une URL http(s) valide.")

    repo = manifest.get("repository")
    repo_ok = _is_nonempty_str(repo) or (
        isinstance(repo, dict) and _is_nonempty_str(repo.get("url"))
    )
    if not repo_ok:
        report.error("plugin.json : 'repository' doit être une chaîne ou un objet avec 'url' non vide.")

    if "disabled" in manifest and not isinstance(manifest["disabled"], bool):
        report.error("plugin.json : 'disabled' doit être un booléen.")

    return name_str


def validate_module_contract(
    module_path: Path, expected_plugin_name: str, report: Report
) -> None:
    """Valide le contrat `config/module.json` v1."""
    if not module_path.is_file():
        report.error("config/module.json manquant à la racine du plugin.")
        return

    data = _read_json_no_bom(module_path, "config/module.json", report)
    if data is None:
        return
    if not isinstance(data, dict):
        report.error("config/module.json doit être un objet JSON.")
        return

    sv = data.get("schema_version")
    if not isinstance(sv, int) or isinstance(sv, bool) or sv != 1:
        report.error(f"config/module.json : 'schema_version' doit valoir 1 (obtenu {sv!r}).")

    mod_id = data.get("id")
    if not isinstance(mod_id, str) or not KEBAB_RE.match(mod_id):
        report.error(f"config/module.json : 'id' ({mod_id!r}) doit être en kebab-case.")

    mod_plugin = data.get("plugin_name")
    if not _is_nonempty_str(mod_plugin):
        report.error("config/module.json : 'plugin_name' doit être une chaîne non vide.")
    elif expected_plugin_name and mod_plugin != expected_plugin_name:
        report.error(
            f"config/module.json : 'plugin_name' ({mod_plugin!r}) ne correspond pas à "
            f"plugin.json ('{expected_plugin_name}')."
        )

    if not _is_nonempty_str(data.get("role")):
        report.error("config/module.json : 'role' doit être une chaîne non vide.")

    storage = data.get("storage")
    if not isinstance(storage, dict):
        report.error("config/module.json : 'storage' doit être un objet.")
    else:
        if not _is_nonempty_str(storage.get("subdir")):
            report.error("config/module.json : 'storage.subdir' doit être une chaîne non vide.")
        if not _is_nonempty_str(storage.get("env_override")):
            report.error("config/module.json : 'storage.env_override' doit être une chaîne non vide.")

    if not _is_str_list(data.get("commands"), allow_empty=False, prefix="/"):
        report.error("config/module.json : 'commands' doit être une liste non vide de commandes '/...'.")

    if not _is_str_list(data.get("capabilities"), allow_empty=False):
        report.error("config/module.json : 'capabilities' doit être une liste non vide de chaînes.")

    ui = data.get("ui")
    if not isinstance(ui, dict):
        report.error("config/module.json : 'ui' doit être un objet.")
    else:
        ui_mode = ui.get("mode")
        if not isinstance(ui_mode, str) or ui_mode not in VALID_UI_MODES:
            report.error(
                f"config/module.json : 'ui.mode' ({ui_mode!r}) invalide (attendu : {sorted(VALID_UI_MODES)})."
            )
        if not _is_nonempty_str(ui.get("target")):
            report.error("config/module.json : 'ui.target' doit être une chaîne non vide.")

    signals = data.get("signals")
    if not isinstance(signals, dict):
        report.error("config/module.json : 'signals' doit être un objet.")
    else:
        if not _is_str_list(signals.get("produces"), allow_empty=True):
            report.error("config/module.json : 'signals.produces' doit être une liste de chaînes.")
        if not _is_str_list(signals.get("consumes"), allow_empty=True):
            report.error("config/module.json : 'signals.consumes' doit être une liste de chaînes.")


def validate_plugin(root: Path, report: Report) -> None:
    """Audite le manifeste, le contrat module, le logo, les skills, les agents, les hooks, le sidecar et le moteur."""
    manifest_path = root / "plugin.json"
    if not manifest_path.is_file():
        report.error("plugin.json introuvable à la racine du plugin.")
        return

    manifest = _read_json_no_bom(manifest_path, "plugin.json", report)
    if manifest is None:
        return
    if not isinstance(manifest, dict):
        report.error("plugin.json doit être un objet JSON.")
        return

    plugin_name = _validate_manifest_fields(manifest, report)
    validate_module_contract(root / "config" / "module.json", plugin_name, report)

    logo_rel = str(manifest.get("logo", "")) if isinstance(manifest.get("logo"), str) else ""
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
                        check_stdlib_only(target, report, local_modules=local_mods)
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
