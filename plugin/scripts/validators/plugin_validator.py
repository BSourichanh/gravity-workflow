#!/usr/bin/env python3
"""Validateur de conformité du plugin autonome `plugin/` (100 % stdlib)."""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .base import (
    EXPECTED_CAVEMAN_REFERENCES,
    EXPECTED_REFERENCES,
    KEBAB_RE,
    MAX_AGENTS_MD_BYTES,
    MAX_CAVEMAN_SKILL_MD_BYTES,
    MAX_DESC_LEN,
    MAX_GRAVITY_SKILL_MD_BYTES,
    MODULE_JARGON_RE,
    Report,
    check_executable,
    check_stdlib_only,
    check_utf8_no_bom_lf,
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
SUGGESTED_PROMPT_RE = re.compile(r"^/[a-z0-9-]+(?:\s+[a-z0-9_-]+)*\s+—\s+\S")
MAX_SUGGESTED_PROMPT_LEN = 100
HARDCODED_USER_PATH_RE = re.compile(
    r"(?:"
    + r"[A-Za-z]:[/\\]Us" + r"ers[/\\]"
    + r"|/Us" + r"ers/[A-Za-z0-9._-]+/"
    + r"|/ho" + r"me/[A-Za-z0-9._-]+/"
    + r")"
)
VALID_UI_MODES = frozenset({"none", "core", "transition-sidecar", "transition-mcp", "mcp-app"})
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


def _read_json_no_bom(
    path: Path, label: str, report: Report, checked_paths: Optional[Set[Path]] = None
) -> Any:
    """Lit un fichier JSON en vérifiant l'absence de BOM UTF-8 et les fins de ligne LF."""
    raw_bytes = check_utf8_no_bom_lf(path, label, report)
    if checked_paths is not None:
        checked_paths.add(path.resolve())
    if raw_bytes is None:
        return None
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
    else:
        for idx, prompt in enumerate(prompts):
            if len(prompt) > MAX_SUGGESTED_PROMPT_LEN:
                report.error(
                    f"plugin.json : 'suggestedPrompts[{idx}]' dépasse {MAX_SUGGESTED_PROMPT_LEN} caractères ({len(prompt)})."
                )
            if not SUGGESTED_PROMPT_RE.match(prompt):
                report.error(
                    f"plugin.json : 'suggestedPrompts[{idx}]' ({prompt!r}) doit respecter le format '/cmd — résumé'."
                )

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
    module_path: Path,
    expected_plugin_name: str,
    report: Report,
    checked_paths: Optional[Set[Path]] = None,
) -> None:
    """Valide le contrat `config/module.json` v1."""
    if not module_path.is_file():
        report.error("config/module.json manquant à la racine du plugin.")
        return

    data = _read_json_no_bom(module_path, "config/module.json", report, checked_paths)
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


def _ensure_plugin_import_paths(root: Path) -> None:
    """Ajoute `observability/`, `scripts/` et `evals/` du plugin cible (ou source) à `sys.path`."""
    fallback_root = Path(__file__).resolve().parents[2]
    for base in (fallback_root, root):
        for sub in ("observability", "scripts", "evals"):
            candidate = base / sub
            if candidate.is_dir() and str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))


def _validate_templates_and_repo_charter(
    root: Path, report: Report, checked_paths: Optional[Set[Path]] = None
) -> None:
    """Contrôle les templates du plugin, l'AGENTS.md racine et l'absence de dérive avec workspace-template/."""
    _ensure_plugin_import_paths(root)
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
                if checked_paths is not None:
                    checked_paths.add(src_path.resolve())

        tpl_profile = templates_dir / ".agents/gravity-workflow/profile.json"
        if tpl_profile.is_file():
            try:
                from profile_engine import default_profile  # type: ignore[import-not-found]

                tpl_profile_data = json.loads(tpl_profile.read_text(encoding="utf-8-sig"))
                if tpl_profile_data != default_profile():
                    report.error(
                        "Dérive détectée entre templates/.agents/gravity-workflow/profile.json "
                        "et profile_engine.default_profile()."
                    )
            except Exception as exc:
                report.error(
                    f"Contrôle profile_engine.default_profile() impossible sur templates/.agents/gravity-workflow/profile.json : {exc}"
                )

    repo_agents = root.parent / "AGENTS.md"
    if root.name == "plugin" and repo_agents.is_file():
        check_utf8_no_bom_lf(
            repo_agents,
            "AGENTS.md (racine dépôt)",
            report,
            max_bytes=MAX_AGENTS_MD_BYTES,
        )

    wt_dir = root.parent / "workspace-template"
    if templates_dir.is_dir() and wt_dir.is_dir():
        for src_rel, wt_rel in TEMPLATE_MAPPINGS:
            src_path = templates_dir / src_rel
            wt_path = wt_dir / wt_rel
            if not src_path.is_file():
                continue
            if not wt_path.is_file():
                report.error(f"Dérive workspace-template : fichier manquant {wt_rel}.")
            elif src_path.read_bytes() != wt_path.read_bytes():
                report.error(
                    f"Dérive détectée entre templates/{src_rel} et workspace-template/{wt_rel}."
                )
        plugin_cases = root / "evals/cases.json"
        wt_cases = wt_dir / ".agents/gravity-workflow/evals/cases.json"
        if plugin_cases.is_file() and wt_cases.is_file():
            if plugin_cases.read_bytes() != wt_cases.read_bytes():
                report.error(
                    "Dérive détectée entre evals/cases.json et workspace-template/.agents/gravity-workflow/evals/cases.json."
                )

    if wt_dir.is_dir():
        try:
            from init_workspace import (  # type: ignore[import-not-found]
                _render_live_state_json,
                _render_progress_md,
            )

            wt_progress = wt_dir / ".agents/gravity-workflow/PROGRESS.md"
            if wt_progress.is_file():
                if wt_progress.read_text(encoding="utf-8", errors="replace") != _render_progress_md("full"):
                    report.error(
                        "Dérive détectée : workspace-template/.agents/gravity-workflow/PROGRESS.md "
                        "!= init_workspace._render_progress_md('full')."
                    )
            wt_live = wt_dir / ".agents/gravity-workflow/observability/live_state.json"
            if wt_live.is_file():
                if wt_live.read_text(encoding="utf-8", errors="replace") != _render_live_state_json("full"):
                    report.error(
                        "Dérive détectée : workspace-template/.agents/gravity-workflow/observability/live_state.json "
                        "!= init_workspace._render_live_state_json('full')."
                    )
        except Exception as exc:
            report.error(f"Contrôle rendu init_workspace impossible sur workspace-template/ : {exc}")


def _validate_tree_encoding_and_paths(
    root: Path, report: Report, checked_paths: Set[Path]
) -> None:
    """Contrôle UTF-8 sans BOM + LF et absence de chemins machine hardcodés sur `.py`, `.md`, `.json`."""
    scan_roots = [(root, root.name)]
    wt_dir = root.parent / "workspace-template"
    if wt_dir.is_dir():
        scan_roots.append((wt_dir, "workspace-template"))

    for base_dir, prefix in scan_roots:
        for fpath in sorted(base_dir.rglob("*")):
            if not fpath.is_file() or "__pycache__" in fpath.parts:
                continue
            if fpath.suffix.lower() not in (".py", ".md", ".json"):
                continue
            rel = fpath.relative_to(base_dir)
            rel_str = rel.as_posix()
            label = rel_str if base_dir == root else f"{prefix}/{rel_str}"
            resolved = fpath.resolve()
            raw_bytes: Optional[bytes] = None
            if resolved not in checked_paths:
                raw_bytes = check_utf8_no_bom_lf(fpath, label, report)
                checked_paths.add(resolved)
            if not any(part in ("tests", "fixtures") for part in rel.parts):
                try:
                    content_bytes = raw_bytes if raw_bytes is not None else fpath.read_bytes()
                    text = content_bytes.decode("utf-8", errors="replace")
                except OSError:
                    continue
                match = HARDCODED_USER_PATH_RE.search(text)
                if match:
                    report.error(
                        f"{label} : chemin machine hardcodé interdit ({match.group(0)!r})."
                    )


def validate_plugin(root: Path, report: Report) -> None:
    """Audite le manifeste, le contrat module, le logo, les skills, les agents, les hooks et le moteur."""
    checked_paths: Set[Path] = set()
    manifest_path = root / "plugin.json"
    if not manifest_path.is_file():
        report.error("plugin.json introuvable à la racine du plugin.")
        return

    manifest = _read_json_no_bom(manifest_path, "plugin.json", report, checked_paths)
    if manifest is None:
        return
    if not isinstance(manifest, dict):
        report.error("plugin.json doit être un objet JSON.")
        return

    plugin_name = _validate_manifest_fields(manifest, report)
    validate_module_contract(root / "config" / "module.json", plugin_name, report, checked_paths)

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
        "metrics_signals",
        "caveman_protocol",
        "hook_runtime",
        "safety_policy",
        "policy",
        "constants",
        "paths",
        "shell_parser",
        "git_and_fs",
        "python_ast",
        "inline_scripts",
        "evaluator",
        "eval_checks",
        "init_workspace",
        "patch_apply",
    }
    exec_scripts = {
        Path("scripts/validate.py"),
        Path("scripts/init_workspace.py"),
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

    if (root / "sidecars" / "live-widget").exists():
        report.warn(
            "Vestige transitoire détecté : sidecars/live-widget/ doit être retiré "
            "(remplacé par /gravity dashboard)."
        )

    for legacy_skill in ("gravity", "gravity-review", "gravity-evals", "gravity-status"):
        if (root / "skills" / legacy_skill).exists():
            report.error(
                f"Ancien skill redondant détecté : skills/{legacy_skill}/ doit être supprimé (unifié dans /gravity-workflow)."
            )

    skill_caps = {
        "gravity-workflow": MAX_GRAVITY_SKILL_MD_BYTES,
        "caveman": MAX_CAVEMAN_SKILL_MD_BYTES,
    }
    for skill_name in ("gravity-workflow", "caveman"):
        skill_md = root / "skills" / skill_name / "SKILL.md"
        if not skill_md.is_file():
            report.error(f"skills/{skill_name}/SKILL.md introuvable.")
        else:
            check_utf8_no_bom_lf(
                skill_md,
                f"skills/{skill_name}/SKILL.md",
                report,
                max_bytes=skill_caps[skill_name],
            )
            checked_paths.add(skill_md.resolve())
            content = skill_md.read_text(encoding="utf-8", errors="replace")
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
            if MODULE_JARGON_RE.search(ref.read_text(encoding="utf-8", errors="replace")):
                report.error(f"{ref} contient le jargon interdit 'Module XX'.")

    cav_refs_dir = root / "skills/caveman/references"
    if not cav_refs_dir.is_dir():
        report.error("Dossier skills/caveman/references/ introuvable.")
    else:
        for expected_cav_ref in EXPECTED_CAVEMAN_REFERENCES:
            cav_ref_path = cav_refs_dir / expected_cav_ref
            if not cav_ref_path.is_file():
                report.error(f"Fiche de référence manquante : {cav_ref_path}")
            else:
                check_utf8_no_bom_lf(
                    cav_ref_path,
                    f"skills/caveman/references/{expected_cav_ref}",
                    report,
                )
                checked_paths.add(cav_ref_path.resolve())
        for ref in sorted(cav_refs_dir.glob("*.md")):
            if MODULE_JARGON_RE.search(ref.read_text(encoding="utf-8", errors="replace")):
                report.error(f"{ref} contient le jargon interdit 'Module XX'.")

    cases_json = root / "evals/cases.json"
    if not cases_json.is_file():
        report.error("evals/cases.json manquant dans le plugin.")
    else:
        try:
            _ensure_plugin_import_paths(root)
            from eval_checks import is_manual_case  # type: ignore[import-not-found]

            cases_data = json.loads(cases_json.read_text(encoding="utf-8"))
            if not isinstance(cases_data, list):
                report.warn("evals/cases.json contient ? cas auto (recommandé : 10 à 15).")
            else:
                auto_cases = [
                    c for c in cases_data if isinstance(c, dict) and not is_manual_case(c)
                ]
                if not (10 <= len(auto_cases) <= 15):
                    report.warn(
                        f"evals/cases.json contient {len(auto_cases)} "
                        "cas auto (recommandé : 10 à 15)."
                    )
        except Exception as exc:
            report.error(f"evals/cases.json invalide : {exc}")

    _validate_templates_and_repo_charter(root, report, checked_paths)
    _validate_tree_encoding_and_paths(root, report, checked_paths)

    run_hook_dry_runs(root / "hooks", root, report)
