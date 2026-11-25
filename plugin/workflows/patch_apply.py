#!/usr/bin/env python3
"""Utilitaires de sauvegarde/application de patch vérifié et nettoyage worktree (100 % stdlib)."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

MAX_PROMPT_DIFF_CHARS = 60000
DEFAULT_BUILDER_TIMEOUT_S = 900
DEFAULT_REVIEWER_TIMEOUT_S = 360
MAX_CHANGED_FILES = 20
MAX_TREE_FILES = 200
EXCLUDED_TREE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
}
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
        "workspace_path": {"type": "string"},
        "modified_files": {"type": "array", "items": {"type": "string"}},
        "git_diff": {"type": "string"},
        "validation_summary": {"type": "string"},
        "refused": {"type": "boolean"},
        "summary": {"type": "string"},
    },
    "required": [
        "workspace_path",
        "modified_files",
        "git_diff",
        "validation_summary",
        "refused",
        "summary",
    ],
}
REVIEW_SCHEMA: Dict[str, Any] = {
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
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
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


def _is_snap_agy() -> bool:
    """Retourne True sur Linux si `agy`/`antigravity` provient d'un paquet Snap (`/snap/`)."""
    if not sys.platform.startswith("linux"):
        return False
    for cmd in ("agy", "antigravity"):
        resolved = shutil.which(cmd)
        if not resolved:
            continue
        norm = resolved.replace("\\", "/")
        if norm.startswith("/snap/") or "/snap/" in norm:
            return True
        try:
            real = os.path.realpath(resolved).replace("\\", "/")
        except OSError:
            real = ""
        if real.endswith("/snap") or real.startswith("/snap/"):
            return True
    return False


def _is_hidden_under_home(path: Path) -> bool:
    """Retourne True si `path` se situe sous un dossier caché (`.*`) de `Path.home()`."""
    try:
        home = Path.home().resolve()
        rel = Path(path).expanduser().resolve().relative_to(home)
    except (OSError, ValueError):
        return False
    return any(part.startswith(".") and part not in {".", ".."} for part in rel.parts)


def resolve_worktree_base(worktree_base: Optional[Path] = None) -> Path:
    """Résout le dossier racine des worktrees isolés (`worktree_base` > `GRAVITY_WORKTREE_BASE` > workflow home, repli Snap Linux `~/gravity-worktrees`)."""
    if worktree_base is not None:
        return Path(worktree_base).expanduser().resolve()
    env_wt_base = os.environ.get("GRAVITY_WORKTREE_BASE", "").strip()
    if env_wt_base:
        return Path(env_wt_base).expanduser().resolve()
    try:
        from state_writer import resolve_workflow_home  # type: ignore[import-not-found]

        candidate = (resolve_workflow_home() / "worktrees").resolve()
    except Exception:
        env_wf = os.environ.get("GRAVITY_WORKFLOW_HOME", "").strip()
        if env_wf:
            candidate = (Path(env_wf).expanduser().resolve() / "worktrees").resolve()
        else:
            env_gravity = os.environ.get("GRAVITY_HOME", "").strip()
            if env_gravity:
                candidate = (Path(env_gravity).expanduser().resolve() / "workflow" / "worktrees").resolve()
            else:
                candidate = (Path.home() / ".gemini" / "gravity" / "workflow" / "worktrees").resolve()
    if sys.platform.startswith("linux") and _is_snap_agy() and _is_hidden_under_home(candidate):
        return (Path.home() / "gravity-worktrees").resolve()
    return candidate


def _is_protected_cleanup_target(
    wt: Path,
    ws: Path,
    plugin_root: Optional[Path] = None,
) -> bool:
    """Refuse toute suppression sur le workspace appelant, ses parents, le plugin ou un chemin protégé."""
    if wt == ws or wt in ws.parents or ws in wt.parents:
        return True
    if wt == wt.parent or wt == Path.home().resolve():
        return True
    plugin_resolved = (plugin_root or PLUGIN_ROOT).resolve()
    if wt == plugin_resolved or wt in plugin_resolved.parents or plugin_resolved in wt.parents:
        return True
    default_base = resolve_worktree_base()
    if wt == default_base or wt in default_base.parents:
        return True
    low_parts = {p.lower() for p in wt.parts}
    if ".git" in low_parts or ".agents" in low_parts or "workspaces" in low_parts:
        return True
    if wt.name.lower() in {"hooks", "worktrees", "workflow", "gravity", "gravity-worktrees"}:
        return True
    if ".gemini" in low_parts and "worktrees" not in low_parts:
        return True
    return False


def _is_verified_linked_worktree(wt: Path, ws: Path) -> bool:
    """Vérifie positivement que `wt` est un vrai git worktree lié (jamais un dossier arbitraire)."""
    if (wt / ".git").is_dir():
        return False

    dot_git = wt / ".git"
    if dot_git.is_file():
        try:
            content = dot_git.read_text(encoding="utf-8", errors="replace").strip()
            if content.lower().startswith("gitdir:"):
                raw_target = content.split(":", 1)[1].strip().replace("\\", "/")
                if "/worktrees/" in raw_target or raw_target.endswith("/worktrees"):
                    return True
        except OSError:
            pass

    try:
        proc = subprocess.run(
            ["git", "-C", str(ws), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("worktree "):
                    cand_raw = line[len("worktree ") :].strip()
                    if not cand_raw:
                        continue
                    try:
                        cand_resolved = Path(cand_raw).expanduser().resolve()
                    except OSError:
                        continue
                    if cand_resolved == wt and cand_resolved != ws:
                        return True
    except (OSError, subprocess.SubprocessError):
        pass

    default_base = resolve_worktree_base()
    if wt.parent == default_base and not (wt / ".git").is_dir():
        return True
    return False


def create_repo_worktree(
    repo_root: Path,
    worktree_base: Optional[Path] = None,
    run_id: Optional[str] = None,
    *,
    ref: str = "HEAD",
) -> Path:
    """Crée un `git worktree --detach` dédié sous `$GRAVITY_HOME/workflow/worktrees/<repo>-<id>`."""
    repo = Path(repo_root).expanduser().resolve()
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ValueError(f"Dépôt Git cible introuvable : {repo}")

    base_dir = resolve_worktree_base(worktree_base)
    if base_dir == repo or repo in base_dir.parents or base_dir in repo.parents:
        raise ValueError(f"worktree_base ne doit pas recouper le dépôt cible : {base_dir}")
    low_base = {p.lower() for p in base_dir.parts}
    if ".gemini" in low_base and "antigravity" in low_base and "worktrees" not in low_base:
        raise ValueError(f"worktree_base interdit sous .gemini/antigravity hors worktrees/ : {base_dir}")

    repo_slug = _SLUG_RE.sub("-", repo.name.lower()).strip("-") or "repo"
    raw_id = str(run_id).strip() if run_id is not None else ""
    clean_id = _ID_RE.sub("-", raw_id).strip("-.") if raw_id else f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    if not clean_id:
        clean_id = uuid.uuid4().hex[:8]

    wt_path = (base_dir / f"{repo_slug}-{clean_id}").resolve()
    try:
        wt_path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(f"Chemin worktree hors de worktree_base : {wt_path}") from exc
    if _is_protected_cleanup_target(wt_path, repo):
        raise ValueError(f"Chemin worktree protégé refusé : {wt_path}")
    if wt_path.exists():
        raise FileExistsError(f"Le worktree cible existe déjà : {wt_path}")

    base_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(wt_path), ref],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if proc.returncode != 0 or not wt_path.is_dir():
        if wt_path.exists():
            shutil.rmtree(wt_path, ignore_errors=True)
        err_msg = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"Échec git worktree add sur {repo}: {err_msg}")
    return wt_path


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
    if not _is_verified_linked_worktree(wt, ws):
        warn(f"[WARN] Suppression refusée hors worktree Git lié vérifié : {wt}")
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
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"[WARN] Nettoyage git worktree échoué ({type(exc).__name__}: {exc})")

    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)

    try:
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "prune"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass
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
    if len(parts) >= 2:
        return _clean_diff_path(parts[-1], prefix="b/")
    return ""


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
        "plan_model": "flash",
        "build_model": "flash",
        "review_model": "flash" if lvl_str == "quick" else "pro",
        "max_remediation_rounds": 0 if lvl_str == "quick" else default_remediations,
        "level": lvl_str or "standard",
        "mode": "evo",
        "suggested_profile": "standard",
        "caveman": "lite",
        "caveman_planner": "lite",
        "skip_separate_planner": lvl_str == "quick",
        "skip_verification": no_verify,
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
