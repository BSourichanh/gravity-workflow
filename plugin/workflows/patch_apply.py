#!/usr/bin/env python3
"""Utilitaires de sauvegarde/application de patch vérifié et nettoyage worktree (100 % stdlib)."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional

MAX_PROMPT_DIFF_CHARS = 60000
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_OBS_DIR = (PLUGIN_ROOT / "observability").resolve()
if _OBS_DIR.is_dir() and str(_OBS_DIR) not in sys.path:
    sys.path.insert(0, str(_OBS_DIR))

WS_ROOT = Path.cwd().resolve()


def _log_warn(message: str) -> None:
    print(message, file=sys.stderr)


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
        warn(
            f"Patch vérifié sauvegardé dans {patch_file} (worktree source : {workspace_path})."
        )
        return patch_file
    except Exception as exc:
        warn(f"[WARN] Sauvegarde patch centralisée échouée ({type(exc).__name__}: {exc})")
        return None


def _is_protected_cleanup_target(
    wt: Path,
    ws: Path,
    plugin_root: Optional[Path] = None,
) -> bool:
    """Refuse toute suppression sur le workspace appelant, ses parents, le plugin ou un chemin protégé."""
    if wt == ws or wt in ws.parents:
        return True
    if wt == wt.parent or wt == Path.home().resolve():
        return True
    plugin_resolved = (plugin_root or PLUGIN_ROOT).resolve()
    if wt == plugin_resolved or wt in plugin_resolved.parents or plugin_resolved in wt.parents:
        return True
    low_parts = {p.lower() for p in wt.parts}
    if ".git" in low_parts or ".agents" in low_parts or wt.name.lower() == "hooks":
        return True
    if ".gemini" in low_parts and "worktrees" not in low_parts:
        return True
    return False


def cleanup_worktree_best_effort(
    worktree_path: Optional[Path],
    caller_workspace: Path,
    *,
    plugin_root: Optional[Path] = None,
    log_fn: Optional[Callable[[str], None]] = None,
) -> bool:
    """Nettoie best-effort un worktree Git isolé (jamais `caller_workspace` ni chemin protégé)."""
    warn = log_fn or _log_warn
    if worktree_path is None:
        return False
    try:
        wt = Path(worktree_path).expanduser().resolve()
        ws = Path(caller_workspace).expanduser().resolve()
    except OSError:
        return False
    if _is_protected_cleanup_target(wt, ws, plugin_root=plugin_root) or not wt.exists():
        return False

    try:
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "prune"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"[WARN] Nettoyage git worktree échoué ({type(exc).__name__}: {exc})")

    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)
    return not wt.exists()


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
        chk = subprocess.run(
            ["git", "-C", str(ws), "apply", "--check", str(patch_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if chk.returncode != 0:
            warn(f"[WARN] git apply --check échoué : {chk.stderr.strip()}")
            return False
        app = subprocess.run(
            ["git", "-C", str(ws), "apply", "--whitespace=nowarn", str(patch_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if app.returncode != 0:
            warn(f"[WARN] git apply échoué : {app.stderr.strip()}")
            return False
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"[WARN] Exécution git apply échouée ({type(exc).__name__}: {exc})")
        return False

    if cleanup_worktree and worktree_path is not None:
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
                encoding = (
                    "utf-16"
                    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff"))
                    else "utf-8"
                )
                content = raw_bytes.decode(encoding, errors="replace")
                if content.strip():
                    return content
        except Exception as exc:
            warn(f"[WARN] Lecture .gravity_verified.patch échouée ({type(exc).__name__}: {exc})")

    text_fallback = str(fallback_diff or "")
    if _is_unified_diff(text_fallback):
        return text_fallback

    if raw_ws and Path(raw_ws).is_dir():
        try:
            diff_proc = subprocess.run(
                ["git", "-C", raw_ws, "diff"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
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


def _cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Outils CLI patch Gravity Workflow.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Vérifie et applique `last_verified.patch` sur `--workspace`.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Racine du dépôt cible (défaut: répertoire courant).",
    )
    parser.add_argument(
        "--patch",
        type=Path,
        default=None,
        help="Chemin explicite du patch unifié (défaut: last_verified.patch centralisé).",
    )
    parser.add_argument(
        "--worktree",
        type=Path,
        default=None,
        help="Chemin du worktree isolé à nettoyer best-effort après application.",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Conserve le worktree après application du patch.",
    )
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
