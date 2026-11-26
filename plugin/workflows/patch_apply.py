#!/usr/bin/env python3
"""Utilitaires de sauvegarde/application de patch vérifié et helpers workflow (100 % stdlib Python 3.10+)."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_WF_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = _WF_DIR.parent
for _sub in (PLUGIN_ROOT / "observability", _WF_DIR):
    _sub_str = str(_sub.resolve())
    if _sub.is_dir() and _sub_str not in sys.path:
        sys.path.insert(0, _sub_str)

from worktree_ops import (  # type: ignore[import-not-found]  # noqa: E402
    EXCLUDED_TREE_DIRS, _is_excluded_wt_path, _is_hidden_under_home,
    _is_protected_cleanup_target, _is_snap_agy, _is_verified_linked_worktree,
    _list_worktree_dirty_files, _log_warn, _run_git, _stage_untracked_intent,
    cleanup_worktree_best_effort, create_repo_worktree, resolve_worktree_base,
)

MAX_PROMPT_DIFF_CHARS = 60000
DEFAULT_BUILDER_TIMEOUT_S = 900
DEFAULT_REVIEWER_TIMEOUT_S = 360
MAX_CHANGED_FILES = 20
MAX_TREE_FILES = 200
BUILDER_HYGIENE_DIRECTIVE = (
    "Hygiène d'exécution obligatoire : "
    "pour toute commande longue via run_command, utilise WaitMsBeforeAsync=10000 et "
    "NotificationTimeoutSeconds=180 (plage 120..300) puis attends la notification sans "
    "jamais appeler manage_task(status) en boucle avant notification ; "
    "sous PowerShell Windows, enchaîne avec ';' (zéro '&&') ; "
    "sous POSIX/Linux, utilise 'grep -rn' pour les recherches (jamais 'rg') ; "
    "pour read_file, passe toujours un EndLine absolu >= StartLine (jamais une taille de fenêtre) ; "
    "chemin exact du guide routage : plugin/skills/gravity-workflow/references/05-workflows-and-routing.md."
)
BUILD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "workspace_path": {"type": "string"}, "modified_files": {"type": "array", "items": {"type": "string"}},
        "git_diff": {"type": "string"}, "validation_summary": {"type": "string"},
        "refused": {"type": "boolean"}, "summary": {"type": "string"},
    },
    "required": ["workspace_path", "modified_files", "git_diff", "validation_summary", "refused", "summary"],
}
REVIEW_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]}, "comprehension_check": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}}, "majors": {"type": "array", "items": {"type": "string"}},
        "uncertain": {"type": "array", "items": {"type": "string"}}, "nits": {"type": "array", "items": {"type": "string"}},
        "manual_checks": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"},
    },
    "required": ["verdict", "comprehension_check", "blockers", "majors", "uncertain", "nits", "summary"],
}
WS_ROOT = Path.cwd().resolve()


def _is_unified_diff(text: str) -> bool:
    """Retourne True si `text` correspond à un vrai diff unifié (`git diff` ou en-têtes `---`/`+++`/`@@`)."""
    if not text or not text.strip():
        return False
    lines = text.splitlines()
    if any(line.startswith("diff --git ") for line in lines):
        return True
    has_old = any(line.startswith("--- ") for line in lines)
    has_new = any(line.startswith("+++ ") for line in lines)
    has_hunk = any(line.startswith("@@ ") for line in lines)
    return has_old and has_new and has_hunk


def _safe_save_verified_patch(
    diff_full: str,
    workspace_path: str,
    workspace_root: Optional[Path] = None,
    *,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Optional[Path]:
    warn = log_fn or _log_warn
    if not diff_full.strip():
        return None
    if not _is_unified_diff(diff_full):
        warn("[WARN] Diff non unifié : sauvegarde last_verified.patch ignorée.")
        return None
    target_ws = WS_ROOT if workspace_root is None else Path(workspace_root).resolve()
    try:
        from state_writer import resolve_patch_path  # type: ignore[import-not-found]

        patch_file = resolve_patch_path(target_ws)
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        normalized = diff_full.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.endswith("\n"):
            normalized += "\n"
        with patch_file.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(normalized)
        warn(f"Patch vérifié sauvegardé dans {patch_file} (worktree source : {workspace_path}).")
        return patch_file
    except Exception as exc:
        warn(f"[WARN] Sauvegarde patch centralisée échouée ({type(exc).__name__}: {exc})")
        return None


def apply_verified_patch(
    workspace_root: Path,
    patch_path: Optional[Path] = None,
    *,
    worktree_path: Optional[Path] = None,
    cleanup_worktree: bool = True,
    plugin_root: Optional[Path] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> bool:
    """Vérifie et applique un patch unifié via `git apply` puis nettoie le worktree best-effort."""
    warn = log_fn or _log_warn
    ws = Path(workspace_root).expanduser().resolve()
    if not ws.is_dir():
        warn(f"[WARN] Workspace introuvable : {ws}")
        return False

    if patch_path is not None:
        patch_file = Path(patch_path).expanduser().resolve()
    else:
        try:
            from state_writer import resolve_patch_path  # type: ignore[import-not-found]

            patch_file = resolve_patch_path(ws)
        except Exception as exc:
            warn(f"[WARN] Résolution patch impossible ({type(exc).__name__}: {exc})")
            return False

    if not patch_file.is_file():
        warn(f"[WARN] Patch introuvable : {patch_file}")
        return False

    patch_text = patch_file.read_text(encoding="utf-8", errors="replace")
    if not _is_unified_diff(patch_text):
        warn(f"[WARN] Fichier non reconnu comme diff unifié : {patch_file}")
        return False

    try:
        chk = _run_git(ws, ("apply", "--check", str(patch_file)), timeout=30)
        if chk.returncode != 0:
            warn(f"[WARN] git apply --check échoué : {chk.stderr.strip()}")
            return False
        app = _run_git(ws, ("apply", "--whitespace=nowarn", str(patch_file)), timeout=30)
        if app.returncode != 0:
            warn(f"[WARN] git apply échoué : {app.stderr.strip()}")
            return False
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"[WARN] Exécution git apply échouée ({type(exc).__name__}: {exc})")
        return False

    if cleanup_worktree and worktree_path is not None:
        wt = Path(worktree_path).expanduser().resolve()
        skip_cleanup = False
        if wt.is_dir() and _is_verified_linked_worktree(wt, ws):
            dirty_files = _list_worktree_dirty_files(wt)
            if dirty_files is None:
                warn(f"[WARN] Vérification git status impossible sur le worktree : {wt} (worktree conservé)")
                skip_cleanup = True
            elif dirty_files:
                covered = set(extract_changed_files_from_diff(patch_text, max_items=MAX_TREE_FILES * 10))
                missing = [f for f in dirty_files if f not in covered]
                if missing:
                    warn(
                        f"[WARN] Worktree conservé ({wt}) : fichiers modifiés/non suivis absents du patch appliqué : "
                        + ", ".join(missing)
                    )
                    skip_cleanup = True
        if not skip_cleanup:
            cleanup_worktree_best_effort(
                worktree_path,
                ws,
                plugin_root=plugin_root,
                log_fn=warn,
            )
    return True


def _read_worktree_patch(
    workspace_path: str,
    fallback_diff: str,
    modified_files: Any = None,
    *,
    max_prompt_diff_chars: int = MAX_PROMPT_DIFF_CHARS,
    stage_untracked: Optional[bool] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> str:
    """Lit `.gravity_verified.patch`, sinon `fallback_diff`, sinon extrait borné des fichiers modifiés."""
    warn = log_fn or _log_warn
    raw_ws = str(workspace_path or "").strip()
    if raw_ws:
        patch_candidate = Path(raw_ws) / ".gravity_verified.patch"
        try:
            if patch_candidate.is_file():
                raw_bytes = patch_candidate.read_bytes()
                enc = "utf-16" if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
                content = raw_bytes.decode(enc, errors="replace")
                if content.strip():
                    return content
        except Exception as exc:
            warn(f"[WARN] Lecture .gravity_verified.patch échouée ({type(exc).__name__}: {exc})")

    text_fallback = str(fallback_diff or "")
    if _is_unified_diff(text_fallback):
        return text_fallback

    if raw_ws and Path(raw_ws).is_dir():
        should_stage = bool(stage_untracked) if stage_untracked is not None else (Path(raw_ws) / ".git").is_file()
        if should_stage:
            _stage_untracked_intent(raw_ws, log_fn=warn)
        try:
            diff_proc = _run_git(raw_ws, ("diff",), timeout=15)
            if diff_proc.returncode == 0 and diff_proc.stdout.strip():
                return diff_proc.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            warn(f"[WARN] Lecture git diff worktree échouée ({type(exc).__name__}: {exc})")

    if text_fallback.strip():
        return text_fallback

    if raw_ws and isinstance(modified_files, list) and modified_files:
        ws_dir = Path(raw_ws)
        chunks: List[str] = []
        total_chars = 0
        for rel in modified_files[:20]:
            if not isinstance(rel, str) or not rel.strip():
                continue
            target = (ws_dir / rel).resolve()
            try:
                target.relative_to(ws_dir.resolve())
            except ValueError:
                continue
            if not target.is_file():
                continue
            try:
                snippet = target.read_text(encoding="utf-8", errors="replace")[:8000]
            except Exception:
                continue
            block = f"--- {rel} ---\n{snippet}\n"
            if total_chars + len(block) > max_prompt_diff_chars:
                break
            chunks.append(block)
            total_chars += len(block)
        if chunks:
            return "\n".join(chunks)

    return ""


def _clean_diff_path(raw: str, prefix: str = "") -> str:
    """Normalise un chemin extrait d'un en-tête unified diff (hors `/dev/null` et patch interne)."""
    text = raw.split("\t", 1)[0].strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        text = text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if prefix and text.startswith(prefix):
        text = text[len(prefix) :]
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    if (
        not text
        or text == "."
        or text == "dev/null"
        or raw.strip() == "/dev/null"
        or text == ".gravity_verified.patch"
    ):
        return ""
    return text


def _parse_diff_git_target(line: str) -> str:
    """Extrait le chemin cible d'une ligne `diff --git a/... b/...`."""
    rest = line[len("diff --git ") :].strip()
    if not rest:
        return ""
    if rest.endswith('"'):
        idx = rest.rfind(' "b/')
        if idx != -1:
            return _clean_diff_path(rest[idx + 1 :], prefix="b/")
    idx = rest.find(" b/")
    if idx != -1:
        return _clean_diff_path(rest[idx + 1 :], prefix="b/")
    parts = rest.split()
    return _clean_diff_path(parts[-1], prefix="b/") if len(parts) >= 2 else ""


def extract_changed_files_from_diff(
    diff_text: str, max_items: int = MAX_CHANGED_FILES
) -> List[str]:
    """Extrait au plus `max_items` chemins modifiés distincts depuis un diff unifié."""
    if not diff_text or not str(diff_text).strip():
        return []
    limit = int(max_items)
    if limit <= 0:
        return []

    files: List[str] = []
    seen = set()
    block_git_target = ""
    block_minus = ""
    block_has_target = False

    def _add(path_str: str) -> None:
        if not path_str or path_str in seen or len(files) >= limit:
            return
        seen.add(path_str)
        files.append(path_str)

    def _flush_block() -> None:
        if not block_has_target:
            _add(block_minus or block_git_target)

    for line in str(diff_text).splitlines():
        if line.startswith("diff --git "):
            _flush_block()
            block_git_target = _parse_diff_git_target(line)
            block_minus = ""
            block_has_target = False
        elif line.startswith("rename to "):
            cand = _clean_diff_path(line[len("rename to ") :])
            if cand:
                _add(cand)
                block_has_target = True
        elif line.startswith("--- ") and not line.endswith(" ---"):
            raw_minus = line[4:].strip()
            block_minus = "" if raw_minus == "/dev/null" else _clean_diff_path(raw_minus, prefix="a/")
        elif line.startswith("+++ "):
            raw_plus = line[4:].strip()
            if raw_plus == "/dev/null":
                if block_minus:
                    _add(block_minus)
                    block_has_target = True
            else:
                cand = _clean_diff_path(raw_plus, prefix="b/")
                if cand:
                    _add(cand)
                    block_has_target = True
        if len(files) >= limit:
            return files

    _flush_block()
    return files[:limit]


def collect_changed_files(
    diff_text: str,
    modified_files: Any = None,
    *,
    max_items: int = MAX_CHANGED_FILES,
) -> List[str]:
    """Retourne `changed_files` borné (`<= max_items`) depuis le diff unifié ou `modified_files`."""
    limit = max(0, int(max_items))
    if limit == 0:
        return []
    extracted = extract_changed_files_from_diff(diff_text, max_items=limit)
    if extracted:
        return extracted
    if not isinstance(modified_files, list):
        return []
    result: List[str] = []
    seen = set()
    for item in modified_files:
        if not isinstance(item, str):
            continue
        cleaned = _clean_diff_path(item)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
            if len(result) >= limit:
                break
    return result


def collect_repo_tree(root: Path, max_files: int = MAX_TREE_FILES) -> str:
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


def read_plan_file(plan_file: Any, *, base_dir: Optional[Path] = None) -> str:
    """Lit un fichier plan UTF-8 en Python (0 token LLM) et rejette les chemins absents ou vides."""
    raw = str(plan_file or "").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() and base_dir is not None:
        candidate = Path(base_dir).resolve() / candidate
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Fichier plan_file introuvable : {resolved}")
    content = resolved.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        raise ValueError(f"Fichier plan_file vide : {resolved}")
    return content


def resolve_timeout_seconds(raw: Any, default: float) -> float:
    """Résout un délai watchdog positif en secondes avec repli sur `default`."""
    if raw is None or isinstance(raw, bool):
        return float(default)
    try:
        val = float(raw)
        return val if val > 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def safe_update_live(
    workspace_root: Path,
    *,
    log_fn: Optional[Callable[[str], None]] = None,
    **kwargs: Any,
) -> None:
    """Met à jour `live_state.json` sans jamais faire échouer l'appelant."""
    warn = log_fn or _log_warn
    try:
        from state_writer import update_live_state  # type: ignore[import-not-found]

        update_live_state(workspace_root=workspace_root, **kwargs)
    except Exception as exc:
        warn(f"[WARN] update_live_state échoué ({type(exc).__name__}: {exc})")


def safe_append_run(
    workspace_root: Path,
    entry: Dict[str, Any],
    *,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """Ajoute une entrée dans `runs.jsonl` centralisé sans jamais lever d'exception."""
    warn = log_fn or _log_warn
    try:
        from state_writer import append_run_log  # type: ignore[import-not-found]

        append_run_log(workspace_root, entry)
    except Exception as exc:
        warn(f"[WARN] Journalisation runs.jsonl échouée ({type(exc).__name__}: {exc})")


def caveman_directive(
    role: str,
    level: str,
    *,
    log_fn: Optional[Callable[[str], None]] = None,
) -> str:
    """Retourne la directive Caveman d'un rôle, ou `""` si indisponible."""
    warn = log_fn or _log_warn
    try:
        from caveman_protocol import build_subagent_directive  # type: ignore[import-not-found]

        return build_subagent_directive(role, level)
    except Exception as exc:
        warn(f"[WARN] caveman_protocol indisponible ({type(exc).__name__}: {exc})")
        return ""


def load_level_preset_for_ws(
    workspace_root: Path,
    level_override: Any,
    *,
    no_verify: bool = False,
    caveman_override: Any = None,
    plan_model_override: Any = None,
    build_model_override: Any = None,
    review_model_override: Any = None,
    default_remediations: int = 1,
    log_fn: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Charge le preset actif depuis `profile_engine` avec repli sûr."""
    warn = log_fn or _log_warn
    lvl_str = str(level_override).strip().lower() if level_override else ""
    fallback: Dict[str, Any] = {
        "plan_model": "flash", "build_model": "flash",
        "review_model": "flash" if lvl_str == "quick" else "pro",
        "max_remediation_rounds": 0 if lvl_str == "quick" else default_remediations,
        "level": lvl_str or "standard", "mode": "evo", "suggested_profile": "standard",
        "caveman": "lite", "caveman_planner": "lite", "execution_engine": "swarm",
        "skip_separate_planner": lvl_str == "quick", "skip_verification": no_verify,
    }
    try:
        from profile_engine import get_active_preset  # type: ignore[import-not-found]

        return get_active_preset(
            workspace_root,
            level_override=str(level_override).strip() if level_override else None,
            no_verify=no_verify,
            caveman_override=str(caveman_override).strip() if caveman_override else None,
            plan_model_override=str(plan_model_override).strip() if plan_model_override else None,
            build_model_override=str(build_model_override).strip() if build_model_override else None,
            review_model_override=str(review_model_override).strip() if review_model_override else None,
        )
    except Exception as exc:
        warn(f"[WARN] Chargement preset profile_engine échoué ({type(exc).__name__}: {exc})")
        return fallback


def _cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Outils CLI patch Gravity Workflow.")
    parser.add_argument("--apply", action="store_true", help="Vérifie et applique `last_verified.patch` sur `--workspace`.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Racine du dépôt cible (défaut: répertoire courant).")
    parser.add_argument("--patch", type=Path, default=None, help="Chemin explicite du patch unifié (défaut: last_verified.patch centralisé).")
    parser.add_argument("--worktree", type=Path, default=None, help="Chemin du worktree isolé à nettoyer best-effort après application.")
    parser.add_argument("--no-cleanup", action="store_true", help="Conserve le worktree après application du patch.")
    parsed = parser.parse_args(argv)
    if not parsed.apply:
        parser.print_help()
        return 2
    ok = apply_verified_patch(
        parsed.workspace,
        parsed.patch,
        worktree_path=parsed.worktree,
        cleanup_worktree=not parsed.no_cleanup,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_cli_main())
