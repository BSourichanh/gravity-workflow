#!/usr/bin/env python3
"""Normalisation de chemins et protections du plugin actif, du harnais et des secrets."""

import fnmatch
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import List, Optional

from .constants import (
    BENIGN_GLOB_PROBES,
    DESTRUCTIVE_RM_TARGETS,
    HARNESS_SELF_PROTECT_PATTERNS,
    SECRET_GLOB_PROBES,
    SENSITIVE_PATH_PATTERNS,
)

DEFAULT_HOOKS_DIR = Path(__file__).resolve().parent.parent
HOOKS_DIR = DEFAULT_HOOKS_DIR


def _get_hooks_dir() -> Path:
    """Retourne `HOOKS_DIR` en respectant un éventuel monkeypatch sur `safety_policy.HOOKS_DIR`."""
    if HOOKS_DIR != DEFAULT_HOOKS_DIR:
        return HOOKS_DIR
    sp = sys.modules.get("safety_policy")
    if sp is not None:
        val = getattr(sp, "HOOKS_DIR", None)
        if isinstance(val, Path):
            return val
    return HOOKS_DIR


def normalize_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip().strip("\"'")
    if not cleaned:
        return ""
    norm = posixpath.normpath(cleaned)
    if norm == ".":
        return ""
    if norm.startswith("./"):
        norm = norm[2:]
    return norm


def matches_any(path: str, patterns: List[re.Pattern]) -> bool:
    norm = normalize_path(path)
    if not norm:
        return False
    return any(p.search(norm) for p in patterns)


def _is_active_plugin_protected_path(path: str, ws_root: Optional[Path] = None) -> bool:
    """Vérifie si `path` pointe réellement vers le plugin Gravity actif (`hooks.json` ou `hooks/`)."""
    cleaned = str(path or "").strip().strip("\"'")
    if not cleaned or any(ch in cleaned for ch in "*?<>|\r\n\x00"):
        return False
    try:
        hooks_dir = _get_hooks_dir()
        candidate = Path(cleaned).expanduser()
        if not candidate.is_absolute():
            base = ws_root if ws_root is not None else hooks_dir.parent
            candidate = base / candidate
        resolved = Path(os.path.normcase(str(candidate.resolve())))
        active_hooks_dir = Path(os.path.normcase(str(hooks_dir.resolve())))
        active_hooks_json = Path(os.path.normcase(str((hooks_dir.parent / "hooks.json").resolve())))
        return (
            resolved == active_hooks_json
            or resolved == active_hooks_dir
            or active_hooks_dir in resolved.parents
        )
    except (OSError, ValueError):
        return False


def _is_active_plugin_root_path(path: str, ws_root: Optional[Path] = None) -> bool:
    """Vérifie si `path` cible la racine entière du plugin Gravity actif (pour bloquer rm/mv/rmtree)."""
    cleaned = str(path or "").strip().strip("\"'")
    if not cleaned or any(ch in cleaned for ch in "*?<>|\r\n\x00"):
        return False
    try:
        hooks_dir = _get_hooks_dir()
        candidate = Path(cleaned).expanduser()
        if not candidate.is_absolute():
            base = ws_root if ws_root is not None else hooks_dir.parent
            candidate = base / candidate
        resolved = Path(os.path.normcase(str(candidate.resolve())))
        active_root = Path(os.path.normcase(str(hooks_dir.parent.resolve())))
        return resolved == active_root
    except (OSError, ValueError):
        return False


def is_harness_protected(path: str, ws_root: Optional[Path] = None) -> bool:
    """Retourne True si `path` cible un fichier protégé du harnais Gravity actif ou du workspace."""
    return matches_any(path, HARNESS_SELF_PROTECT_PATTERNS) or _is_active_plugin_protected_path(
        path, ws_root
    )


def _is_destructive_target(tok: str) -> bool:
    cleaned = tok.replace("\\", "/").strip().strip("\"'")
    if not cleaned:
        return False
    upper = cleaned.upper()
    if upper in {t.upper() for t in DESTRUCTIVE_RM_TARGETS}:
        return True
    if re.match(r"^[A-Za-z]:(?:/(?:\*)?)?$", cleaned):
        return True
    if ".." in cleaned.split("/"):
        return True
    return False


def _is_secret_matching_glob(raw_tok: str) -> bool:
    """Retourne True si le glob `raw_tok` cible spécifiquement un nom de fichier sensible."""
    cleaned = raw_tok.replace("\\", "/").strip().strip("\"'")
    if not cleaned or not any(ch in cleaned for ch in "*?["):
        return False
    base = posixpath.basename(cleaned.rstrip("/")).lower()
    if not base or not any(ch in base for ch in "*?["):
        return False
    if any(fnmatch.fnmatchcase(probe, base) for probe in BENIGN_GLOB_PROBES):
        return False
    return any(fnmatch.fnmatchcase(probe, base) for probe in SECRET_GLOB_PROBES)


def _token_hits_secret(tok: str) -> bool:
    """Vérifie si un jeton de chemin (ou sous-composant `=`/`:`/`,`/`@`) cible un fichier sensible."""
    cleaned = tok.strip().strip("\"'")
    if not cleaned:
        return False
    raw_cands: List[str] = [cleaned, cleaned.strip(",:[]{}()<>")]
    if "=" in cleaned:
        rhs = cleaned.split("=", 1)[1]
        raw_cands.extend([rhs, rhs.strip(",:[]{}()<>\"'")])
    if ":" in cleaned:
        lhs, rhs = cleaned.split(":", 1)
        if not (len(lhs) == 1 and lhs.isalpha()):
            raw_cands.extend([rhs, rhs.strip(",:[]{}()<>\"'")])
    if "," in cleaned:
        for piece in cleaned.split(","):
            raw_cands.append(piece.strip(",:[]{}()<>\"'"))
    candidates: List[str] = []
    for cand in raw_cands:
        if not cand:
            continue
        candidates.append(cand)
        if cand.startswith("@") and len(cand) > 1:
            candidates.append(cand[1:].strip("\"'"))
    for cand in candidates:
        if cand and matches_any(cand, SENSITIVE_PATH_PATTERNS):
            return True
    return False
