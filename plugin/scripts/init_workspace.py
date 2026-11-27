#!/usr/bin/env python3
"""Initialiseur du socle vital Gravity Workflow dans un projet cible (appelé par /gravity-workflow, 100 % stdlib)."""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, Sequence, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_ROOT = PLUGIN_ROOT / "templates"
GIT_VERSIONING_RULE_REL = ".agents/rules/05-git-versioning.md"
SAFETY_RULE_REL = ".agents/rules/01-safety-boundaries.md"
ENGINEERING_RULE_REL = ".agents/rules/06-engineering-principles.md"

TEMPLATE_SOURCE_OVERRIDES: Dict[str, str] = {
    SAFETY_RULE_REL: ".agents/rules/01-safety-boundaries.md.tmpl",
}

STACK_RULE_MAP: Dict[str, str] = {
    "python": ".agents/rules/02-python-stdlib.md",
    "api": ".agents/rules/03-api-architecture.md",
}

COMMON_TEMPLATE_RELS: Tuple[str, ...] = (
    "AGENTS.md",
    ".agents/mcp_config.json",
    SAFETY_RULE_REL,
    GIT_VERSIONING_RULE_REL,
    ENGINEERING_RULE_REL,
    ".agents/gravity-workflow/.gitignore",
    ".agents/gravity-workflow/BACKLOG.md",
    ".agents/gravity-workflow/profile.json",
)

FULL_EXTRA_TEMPLATE_RELS: Tuple[str, ...] = (".agents/rules/04-release-checklist.md",)

PROGRESS_STEPS: Tuple[str, ...] = (
    "Cadrage du projet",
    "Socle & règles",
    "Équipe & Reviewer indépendant",
    "Outils & connexions",
    "Routeur & workflows",
    "Garde-fous automatiques",
    "Tests & évaluations",
    "Suivi & boucle `/learn`",
    "Tableau de bord (Bonus)",
)


def _write_utf8_lf(dest: Path, content: str) -> None:
    """Écrit un fichier texte en UTF-8 sans BOM avec fins de ligne LF."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    with dest.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(normalized)


def _read_template(rel_path: str) -> str:
    """Lit un fichier template statique depuis `plugin/templates/` (en résolvant les suffixes `.tmpl`)."""
    src_rel = TEMPLATE_SOURCE_OVERRIDES.get(rel_path, rel_path)
    src = TEMPLATES_ROOT / src_rel
    if not src.is_file():
        raise FileNotFoundError(f"Template introuvable : {src}")
    return src.read_text(encoding="utf-8").replace("\r\n", "\n")


def _render_progress_md(mode: str) -> str:
    """Génère `PROGRESS.md` paramétré selon le mode (`full` ou `quickstart`)."""
    is_quickstart = mode == "quickstart"
    parcours = "Quick Start [~] 3/8" if is_quickstart else "Guidé (0/8)"
    done_count = 3 if is_quickstart else 0
    checklist = "\n".join(
        f"- [{'x' if idx < done_count else ' '}] {step}"
        for idx, step in enumerate(PROGRESS_STEPS)
    )
    return (
        "# Gravity Workflow Progress\n\n"
        f"- **Parcours :** {parcours}\n"
        "- **Domaine :** À définir\n"
        "- **Livrable principal :** À définir\n"
        "- **Sources de vérité :** À définir\n"
        "- **3 Lignes rouges :**\n"
        "  1. Aucune lecture/fuite de secrets (`.env`, `secrets/`, clés SSH/privées, `printenv`)\n"
        "  2. Aucune commande destructrice système/Git/DB (`rm -rf`, `git push --force`, `DROP DATABASE`)\n"
        "  3. Auto-protection des hooks et règles de sécurité\n\n"
        "## Avancement des étapes\n"
        f"{checklist}\n"
    )


def _render_live_state_json(mode: str) -> str:
    """Génère `observability/live_state.json` v2 paramétré selon le mode (`full` ou `quickstart`)."""
    is_quickstart = mode == "quickstart"
    payload = {
        "stage": "quickstart" if is_quickstart else "idle",
        "progress": "3/8" if is_quickstart else "0/8",
        "mode": "evo",
        "level": "standard",
        "preset_profile": "standard",
        "suggested_profile": "standard",
        "suggested_reason": "",
        "role_models": {
            "plan": "flash",
            "build": "flash",
            "review": "pro",
        },
        "caveman": "lite",
        "caveman_locked": False,
        "guard_status": "ON",
        "nodes": {
            "planner": "",
            "builder": "",
            "reviewer": "",
        },
        "events": [],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def ensure_git_versioning_rule(target_root: Path, force: bool = False) -> bool:
    """Crée `.agents/rules/05-git-versioning.md` s'il est absent du projet cible (sans écraser l'existant)."""
    target_root = target_root.resolve()
    dest = target_root / GIT_VERSIONING_RULE_REL
    if dest.exists() and not force:
        print(f"[SKIP]   {GIT_VERSIONING_RULE_REL} (existe déjà, anti-écrasement actif)")
        return False
    _write_utf8_lf(dest, _read_template(GIT_VERSIONING_RULE_REL))
    print(f"[CREATE] {GIT_VERSIONING_RULE_REL}")
    return True


def init_workspace(
    target_root: Path,
    mode: str = "full",
    force: bool = False,
    stacks: Sequence[str] = (),
) -> int:
    """Déploie le socle Gravity Workflow depuis `plugin/templates/` (sans écrasement par défaut)."""
    target_root = target_root.resolve()
    target_root.mkdir(parents=True, exist_ok=True)

    rel_paths = list(COMMON_TEMPLATE_RELS)
    if mode == "full":
        rel_paths.extend(FULL_EXTRA_TEMPLATE_RELS)

    seen_stacks = set()
    for stack in stacks:
        if stack not in STACK_RULE_MAP:
            raise ValueError(
                f"Stack inconnue : {stack!r} (attendu : {sorted(STACK_RULE_MAP)})"
            )
        if stack in seen_stacks:
            continue
        seen_stacks.add(stack)
        rel_paths.append(STACK_RULE_MAP[stack])

    files: Dict[str, str] = {rel: _read_template(rel) for rel in rel_paths}
    files[".agents/gravity-workflow/PROGRESS.md"] = _render_progress_md(mode)
    files[".agents/gravity-workflow/observability/live_state.json"] = _render_live_state_json(mode)

    for rel_path, content in files.items():
        dest = target_root / rel_path
        if dest.exists() and not force:
            print(f"[SKIP]   {rel_path} (existe déjà, anti-écrasement actif)")
            continue
        _write_utf8_lf(dest, content)
        print(f"[CREATE] {rel_path}")

    default_cases = PLUGIN_ROOT / "evals/cases.json"
    dest_cases = target_root / ".agents/gravity-workflow/evals/cases.json"
    if default_cases.is_file():
        if dest_cases.exists() and not force:
            print("[SKIP]   .agents/gravity-workflow/evals/cases.json (existe déjà, anti-écrasement actif)")
        else:
            dest_cases.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(default_cases, dest_cases)
            print("[CREATE] .agents/gravity-workflow/evals/cases.json")

    return 0


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main() -> int:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Initialise le socle vital Gravity Workflow dans un projet.")
    parser.add_argument("--workspace", type=Path, default=Path("."), help="Répertoire du projet cible.")
    parser.add_argument(
        "--mode",
        choices=("full", "quickstart"),
        default="full",
        help="Mode d'initialisation : 'quickstart' (socle minimal 10 min) ou 'full' (socle complet).",
    )
    parser.add_argument(
        "--with-stack",
        choices=tuple(STACK_RULE_MAP.keys()),
        action="append",
        default=[],
        help="Déploie en opt-in une règle de stack complémentaire (répétable : python, api).",
    )
    parser.add_argument(
        "--ensure-git-rule",
        action="store_true",
        help="Crée uniquement `.agents/rules/05-git-versioning.md` s'il est absent du projet (sans écraser l'existant).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remplace les fichiers existants (uniquement après validation explicite de l'utilisateur).",
    )
    args = parser.parse_args()
    if args.ensure_git_rule:
        ensure_git_versioning_rule(args.workspace, force=args.force)
        return 0
    return init_workspace(
        args.workspace,
        mode=args.mode,
        force=args.force,
        stacks=args.with_stack,
    )


if __name__ == "__main__":
    raise SystemExit(main())
