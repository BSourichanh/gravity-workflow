"""Utilitaires d'isolation et de nettoyage sécurisé des git worktrees (100 % stdlib Python 3.10+)."""

import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Sequence

EXCLUDED_TREE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
}
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_OBS_DIR = (PLUGIN_ROOT / "observability").resolve()
if _OBS_DIR.is_dir() and str(_OBS_DIR) not in sys.path:
    sys.path.insert(0, str(_OBS_DIR))


def _log_warn(message: str) -> None:
    print(message, file=sys.stderr)


def _run_git(
    repo_or_wt: Path | str,
    args: Sequence[str],
    *,
    timeout: int = 15,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_or_wt), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


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
    """Refuse toute suppression sur le workspace appelant, ses parents, `.git`, `.agents`, le plugin ou un chemin protégé."""
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
    dot_git = wt / ".git"
    if dot_git.is_dir():
        return False

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
        proc = _run_git(ws, ("worktree", "list", "--porcelain"), timeout=10)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if not line.startswith("worktree "):
                    continue
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
    """Crée un `git worktree --detach` dédié sous `resolve_worktree_base()/<repo>-<id>`."""
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
    proc = _run_git(repo, ("worktree", "add", "--detach", str(wt_path), ref), timeout=30)
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
    """Nettoie best-effort un worktree Git isolé (jamais `caller_workspace`, `.git`, `.agents` ni chemin protégé)."""
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
        _run_git(ws, ("worktree", "remove", "--force", str(wt)), timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"[WARN] Nettoyage git worktree échoué ({type(exc).__name__}: {exc})")

    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)

    try:
        _run_git(ws, ("worktree", "prune"), timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
    return not wt.exists()


def _clean_wt_rel_path(raw: str) -> str:
    text = raw.strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        text = text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    if not text or text == "." or text == ".gravity_verified.patch":
        return ""
    return text


def _is_excluded_wt_path(rel_path: str) -> bool:
    """Retourne True si `rel_path` cible `.gravity_verified.patch` ou traverse `EXCLUDED_TREE_DIRS`."""
    norm = str(rel_path or "").replace("\\", "/").strip("/")
    if not norm:
        return True
    parts = [p for p in norm.split("/") if p and p != "."]
    if not parts:
        return True
    return parts[-1] == ".gravity_verified.patch" or any(p in EXCLUDED_TREE_DIRS for p in parts)


def _list_worktree_dirty_files(wt: Path) -> Optional[List[str]]:
    """Liste les fichiers modifiés/non suivis d'un worktree (hors exclusions), ou None si erreur git."""
    try:
        proc = _run_git(wt, ("status", "--porcelain", "-z", "-uall"), timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    entries = proc.stdout.split("\0")
    dirty: List[str] = []
    seen = set()
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        idx += 1
        if not entry or len(entry) < 4:
            continue
        xy = entry[:2]
        rel = entry[3:] if entry[2] == " " else entry[2:].lstrip()
        if "R" in xy or "C" in xy:
            idx += 1
        cleaned = _clean_wt_rel_path(rel)
        if not cleaned or _is_excluded_wt_path(cleaned) or cleaned in seen:
            continue
        seen.add(cleaned)
        dirty.append(cleaned)
    return dirty


def _stage_untracked_intent(
    raw_ws: str,
    *,
    log_fn: Optional[Callable[[str], None]] = None,
) -> None:
    """Enregistre l'intention d'ajout (`git add -N`) des fichiers untracked hors exclusions."""
    warn = log_fn or _log_warn
    try:
        proc = _run_git(raw_ws, ("ls-files", "--others", "--exclude-standard", "-z"), timeout=15)
        if proc.returncode != 0 or not proc.stdout:
            return
        to_stage = [p for p in proc.stdout.split("\0") if p and not _is_excluded_wt_path(p)]
        for idx in range(0, len(to_stage), 50):
            _run_git(raw_ws, ("add", "-N", "--", *to_stage[idx : idx + 50]), timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"[WARN] Enregistrement git add -N échoué ({type(exc).__name__}: {exc})")
