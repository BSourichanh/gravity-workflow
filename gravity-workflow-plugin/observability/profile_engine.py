#!/usr/bin/env python3
"""Moteur de profil et d'auto-adaptation Gravity (`low`/`medium`/`high`/`ultra`/`evo`, 100 % stdlib Python 3)."""

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OBS_DIR = Path(__file__).resolve().parent
if str(OBS_DIR) not in sys.path:
    sys.path.insert(0, str(OBS_DIR))

from caveman_protocol import (  # noqa: E402
    DEFAULT_CAVEMAN,
    VALID_CAVEMAN_LEVELS,
    choose_evo_caveman,
    effective_planner_caveman,
    normalize_caveman,
)
from state_writer import _atomic_write_json, exclusive_file_lock, update_live_state  # noqa: E402

PROFILE_REL_PATH = Path(".agents/gravity-workflow/profile.json")
PROGRESS_REL_PATH = Path(".agents/gravity-workflow/PROGRESS.md")
RULES_REL_DIR = Path(".agents/rules")
MCP_REL_PATH = Path(".agents/mcp_config.json")
LIVE_STATE_REL_PATH = Path(".agents/gravity-workflow/observability/live_state.json")
EVAL_REPORT_REL_PATH = Path(".agents/gravity-workflow/evals/report.json")

LEVELS: Tuple[str, ...] = ("low", "medium", "high", "ultra")
VALID_MODES: Tuple[str, ...] = ("evo", "low", "medium", "high", "ultra")
MODE_ALIASES: Dict[str, str] = {"evolutive": "evo"}
VALID_PACES: Tuple[str, ...] = ("slow", "fast")
VALID_VERIFICATIONS: Tuple[str, ...] = ("safe", "unsafe")

DEFAULT_MODE = "evo"
DEFAULT_LEVEL = "medium"
DEFAULT_PACE = "slow"
DEFAULT_VERIFICATION = "safe"
MAX_HISTORY = 20
MAX_SCAN_FILES = 500
HYSTERESIS_MARGIN = 5

VALID_MODEL_TIERS = {"inherit", "flash_lite", "flash", "pro"}
MODEL_OVERRIDE_KEYS: Tuple[str, ...] = (
    "plan_model",
    "build_model",
    "review_model",
    "eval_candidate",
    "eval_judge",
)
NORMAL_TERMINATION_REASONS = {
    "NO_TOOL_CALL",
    "STOP",
    "END_TURN",
    "COMPLETED",
    "TERMINAL_CUSTOM_HOOK",
    "INJECTED_RESPONSE",
    "TERMINAL_STEP_TYPE",
    "USER_CANCELED",
    "",
}
EXCLUDED_SCAN_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".agents",
    ".mypy_cache",
    ".pytest_cache",
}

LEVEL_THRESHOLDS: Tuple[Tuple[int, str], ...] = (
    (25, "low"),
    (55, "medium"),
    (80, "high"),
    (101, "ultra"),
)
# Seuils [inclusif, exclusif) par palier pour l'hystérésis ±5 pts
LEVEL_SCORE_RANGES: Dict[str, Tuple[int, int]] = {
    "low": (0, 25),
    "medium": (25, 55),
    "high": (55, 80),
    "ultra": (80, 101),
}

LEVEL_PRESETS: Dict[str, Dict[str, Any]] = {
    "low": {
        "plan_model": "flash_lite",
        "build_model": "flash_lite",
        "review_model": "flash",
        "eval_candidate": "flash_lite",
        "eval_judge": "flash",
        "max_remediation_rounds": 0,
        "use_review_pipeline": "manual_only",
        "use_swarm": False,
        "swarm_cross_model": False,
        "eval_scope": "manual",
        "init_mode": "quickstart",
        "ui_model_hint": "3.8 Flash / Hydrogen (/effort low)",
    },
    "medium": {
        "plan_model": "inherit",
        "build_model": "flash",
        "review_model": "pro",
        "eval_candidate": "flash",
        "eval_judge": "inherit",
        "max_remediation_rounds": 1,
        "use_review_pipeline": "significant_changes",
        "use_swarm": False,
        "swarm_cross_model": False,
        "eval_scope": "rules_changed",
        "init_mode": "choice",
        "ui_model_hint": "Orchestrator (/effort medium)",
    },
    "high": {
        "plan_model": "pro",
        "build_model": "flash",
        "review_model": "pro",
        "eval_candidate": "flash",
        "eval_judge": "pro",
        "max_remediation_rounds": 2,
        "use_review_pipeline": "default",
        "use_swarm": True,
        "swarm_cross_model": False,
        "eval_scope": "targeted",
        "init_mode": "full",
        "ui_model_hint": "Orchestrator / Neon (/effort high)",
    },
    "ultra": {
        "plan_model": "pro",
        "build_model": "pro",
        "review_model": "pro",
        "eval_candidate": "flash",
        "eval_judge": "pro",
        "max_remediation_rounds": 2,
        "use_review_pipeline": "full_with_boost_escalation",
        "use_swarm": True,
        "swarm_cross_model": True,
        "eval_scope": "full",
        "init_mode": "full",
        "ui_model_hint": "Orchestrator Neon/Carbon (/effort xhigh)",
    },
}

CHECKED_STEP_RE = re.compile(r"^\s*-\s*\[x\]\s+(.*)$", re.IGNORECASE)
TEST_FILE_RE = re.compile(r"(?:^test_.*|_test\.py|\.(?:test|spec)\.[a-z0-9]+)$", re.IGNORECASE)
CI_FILE_NAMES = {
    ".gitlab-ci.yml",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "jenkinsfile",
    "azure-pipelines.yml",
}
CI_DIR_NAMES = {"terraform", "k8s", "helm"}


def default_profile() -> Dict[str, Any]:
    """Retourne un profil Gravity initialisé par défaut (`mode=evo (medium)`, `pace=slow`, `verification=safe`, `caveman=lite`)."""
    return {
        "schema_version": 1,
        "mode": DEFAULT_MODE,
        "current_level": DEFAULT_LEVEL,
        "locked": False,
        "pace": DEFAULT_PACE,
        "verification": DEFAULT_VERIFICATION,
        "caveman": DEFAULT_CAVEMAN,
        "caveman_locked": False,
        "suggested_level": DEFAULT_LEVEL,
        "allow_pro": True,
        "model_overrides": {},
        "sessions_analyzed": 0,
        "clean_streak": 0,
        "last_score": 40,
        "last_reason": "Initialisation par défaut (mode evo au palier medium).",
        "last_evaluated_conversation_id": None,
        "last_evaluated_execution_num": None,
        "last_conversation_start_level": None,
        "last_updated": None,
        "signals": {
            "source_files": 0,
            "rules_count": 0,
            "has_tests": False,
            "has_ci": False,
            "mcp_servers": 0,
            "progress_steps": 0,
            "guard_deny_recent": 0,
            "guard_ask_recent": 0,
            "eval_pass_rate": None,
            "session_error": False,
        },
        "history": [],
    }


def normalize_mode(raw: str) -> str:
    """Valide et normalise un mode (`evo`, `evolutive`, `low`, `medium`, `high`, `ultra`)."""
    cleaned = str(raw or "").strip().lower()
    cleaned = MODE_ALIASES.get(cleaned, cleaned)
    if cleaned not in VALID_MODES:
        raise ValueError(
            f"Mode Gravity invalide {raw!r} (attendu : {', '.join(VALID_MODES)} ou 'evolutive')."
        )
    return cleaned


def normalize_pace(raw: str) -> str:
    """Valide et normalise une stratégie workflow (`slow` ou `fast`)."""
    cleaned = str(raw or "").strip().lower()
    if cleaned not in VALID_PACES:
        raise ValueError(
            f"Stratégie workflow Gravity invalide {raw!r} (attendu : {', '.join(VALID_PACES)})."
        )
    return cleaned


def normalize_verification(raw: str) -> str:
    """Valide et normalise un mode de vérification (`safe` ou `unsafe`)."""
    cleaned = str(raw or "").strip().lower()
    if cleaned not in VALID_VERIFICATIONS:
        raise ValueError(
            f"Mode de vérification Gravity invalide {raw!r} (attendu : {', '.join(VALID_VERIFICATIONS)})."
        )
    return cleaned


def resolve_profile_path(workspace_root: Optional[Path] = None) -> Path:
    """Retourne le chemin vers `.agents/gravity-workflow/profile.json` dans le workspace cible."""
    root = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
    return root / PROFILE_REL_PATH


def _sanitize_profile(raw: Any) -> Dict[str, Any]:
    profile = default_profile()
    if not isinstance(raw, dict):
        return profile

    merged = dict(profile)
    for key in (
        "schema_version",
        "allow_pro",
        "sessions_analyzed",
        "clean_streak",
        "last_score",
        "last_reason",
        "last_evaluated_conversation_id",
        "last_evaluated_execution_num",
        "last_conversation_start_level",
        "last_updated",
    ):
        if key in raw:
            merged[key] = raw[key]

    try:
        mode = normalize_mode(str(raw.get("mode", DEFAULT_MODE)))
    except ValueError:
        mode = DEFAULT_MODE

    raw_level = str(raw.get("current_level", DEFAULT_LEVEL)).strip().lower()
    current_level = raw_level if raw_level in LEVELS else DEFAULT_LEVEL

    if mode != "evo":
        current_level = mode
        locked = True
    else:
        locked = False

    raw_suggested = str(raw.get("suggested_level", current_level)).strip().lower()
    suggested_level = raw_suggested if raw_suggested in LEVELS else current_level

    try:
        pace = normalize_pace(str(raw.get("pace", DEFAULT_PACE)))
    except ValueError:
        pace = DEFAULT_PACE

    try:
        verification = normalize_verification(str(raw.get("verification", DEFAULT_VERIFICATION)))
    except ValueError:
        verification = DEFAULT_VERIFICATION

    raw_caveman = str(raw.get("caveman", DEFAULT_CAVEMAN)).strip().lower()
    caveman = raw_caveman if raw_caveman in VALID_CAVEMAN_LEVELS else DEFAULT_CAVEMAN
    caveman_locked = bool(raw.get("caveman_locked", False))

    merged["mode"] = mode
    merged["current_level"] = current_level
    merged["locked"] = locked
    merged["pace"] = pace
    merged["verification"] = verification
    merged["caveman"] = caveman
    merged["caveman_locked"] = caveman_locked
    merged["suggested_level"] = suggested_level
    merged["allow_pro"] = bool(merged.get("allow_pro", True))

    overrides = raw.get("model_overrides")
    if isinstance(overrides, dict):
        merged["model_overrides"] = {
            k: v
            for k, v in overrides.items()
            if k in MODEL_OVERRIDE_KEYS and isinstance(v, str) and v in VALID_MODEL_TIERS
        }
    else:
        merged["model_overrides"] = {}

    if isinstance(raw.get("signals"), dict):
        sig = dict(profile["signals"])
        sig.update(raw["signals"])
        merged["signals"] = sig

    if isinstance(raw.get("history"), list):
        merged["history"] = [h for h in raw["history"] if isinstance(h, dict)][-MAX_HISTORY:]

    try:
        merged["sessions_analyzed"] = max(0, int(merged.get("sessions_analyzed") or 0))
    except (TypeError, ValueError):
        merged["sessions_analyzed"] = 0
    try:
        merged["clean_streak"] = max(0, int(merged.get("clean_streak") or 0))
    except (TypeError, ValueError):
        merged["clean_streak"] = 0
    try:
        merged["last_score"] = max(0, min(100, int(merged.get("last_score") or 40)))
    except (TypeError, ValueError):
        merged["last_score"] = 40

    raw_exec = merged.get("last_evaluated_execution_num")
    if raw_exec is not None:
        try:
            merged["last_evaluated_execution_num"] = int(raw_exec)
        except (TypeError, ValueError):
            merged["last_evaluated_execution_num"] = None

    raw_start_lvl = merged.get("last_conversation_start_level")
    if raw_start_lvl is not None:
        s_lvl = str(raw_start_lvl).strip().lower()
        merged["last_conversation_start_level"] = s_lvl if s_lvl in LEVELS else None

    return merged


def load_profile(workspace_root: Optional[Path] = None) -> Dict[str, Any]:
    """Charge `.agents/gravity-workflow/profile.json` ou retourne le profil par défaut."""
    profile_path = resolve_profile_path(workspace_root)
    if not profile_path.is_file():
        return default_profile()
    try:
        loaded = json.loads(profile_path.read_text(encoding="utf-8"))
        return _sanitize_profile(loaded)
    except Exception:
        return default_profile()


def get_active_preset(
    workspace_root: Optional[Path] = None,
    *,
    level_override: Optional[str] = None,
    pace_override: Optional[str] = None,
    verification_override: Optional[str] = None,
    caveman_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Résout le preset de réflexion/modèles, stratégie workflow (`pace`), vérification (`verification`) et Caveman."""
    profile = load_profile(workspace_root)
    chosen = str(level_override or "").strip().lower()
    level = chosen if chosen in LEVELS else profile["current_level"]

    chosen_pace = str(pace_override or "").strip().lower()
    pace = chosen_pace if chosen_pace in VALID_PACES else profile["pace"]

    chosen_verif = str(verification_override or "").strip().lower()
    verification = (
        chosen_verif if chosen_verif in VALID_VERIFICATIONS else profile["verification"]
    )

    chosen_caveman = str(caveman_override or "").strip().lower()
    caveman = (
        chosen_caveman
        if chosen_caveman in VALID_CAVEMAN_LEVELS
        else profile.get("caveman", DEFAULT_CAVEMAN)
    )

    # Axe 1 (Mode / Réflexion) : fixe strictement les modèles sans jamais être altéré par pace ou verification
    preset = dict(LEVEL_PRESETS[level])
    overrides = profile.get("model_overrides") or {}
    for key in MODEL_OVERRIDE_KEYS:
        val = overrides.get(key)
        if isinstance(val, str) and val in VALID_MODEL_TIERS:
            preset[key] = val

    if not profile.get("allow_pro", True):
        for key in MODEL_OVERRIDE_KEYS:
            if preset.get(key) == "pro":
                preset[key] = "inherit"

    # Axe 2 (Stratégie Workflow / Temps) : optimise l'exécution temporelle sans réduire la réflexion
    preset["pace"] = pace
    if pace == "fast":
        preset["workflow_strategy"] = "time_optimized"
        preset["parallelize_aggressively"] = True
        preset["skip_separate_planner"] = True
        preset["max_remediation_rounds"] = min(int(preset["max_remediation_rounds"]), 1)
    else:
        preset["workflow_strategy"] = "by_responsibility"
        preset["parallelize_aggressively"] = False
        preset["skip_separate_planner"] = False

    # Axe 3 (Vérification & Tests) : active ou désactive toute vérification/test du workflow
    preset["verification"] = verification
    preset["skip_verification"] = verification == "unsafe"
    preset["skip_tests"] = verification == "unsafe"
    if verification == "unsafe":
        preset["max_remediation_rounds"] = 0
        preset["use_review_pipeline"] = "disabled_unsafe"
        preset["eval_scope"] = "disabled_unsafe"

    # Protocole Caveman ciblé par ROI (evals toujours exclus)
    preset["caveman"] = caveman
    preset["caveman_locked"] = bool(profile.get("caveman_locked", False))
    preset["caveman_planner"] = effective_planner_caveman(caveman)
    preset["caveman_reviewer"] = caveman
    preset["caveman_builder"] = caveman
    preset["caveman_evals"] = "off"

    preset["mode"] = profile["mode"]
    preset["level"] = level
    preset["locked"] = bool(profile["locked"])
    preset["suggested_level"] = profile.get("suggested_level", level)
    preset["last_reason"] = profile.get("last_reason", "")
    return preset


def format_mode_badge(profile: Dict[str, Any]) -> str:
    """Formate le badge compact des 3 axes pour `/statusline` et les micro-cartes."""
    mode = str(profile.get("mode", DEFAULT_MODE))
    level = str(profile.get("current_level", DEFAULT_LEVEL))
    pace = str(profile.get("pace", DEFAULT_PACE))
    verification = str(profile.get("verification", DEFAULT_VERIFICATION))
    if mode == "evo":
        return f"evo:{level}/{pace}/{verification}"
    return f"{level}[lock]/{pace}/{verification}"


def format_caveman_badge(profile: Dict[str, Any]) -> str:
    """Formate le badge compact du réglage Caveman (`caveman:lite` ou `caveman:full[lock]`)."""
    caveman = str(profile.get("caveman", DEFAULT_CAVEMAN)).strip().lower()
    if caveman not in VALID_CAVEMAN_LEVELS:
        caveman = DEFAULT_CAVEMAN
    if bool(profile.get("caveman_locked", False)):
        return f"caveman:{caveman}[lock]"
    return f"caveman:{caveman}"


def _sync_live_state_safe(
    workspace_root: Path,
    profile: Dict[str, Any],
    *,
    event: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        update_live_state(
            mode=profile["mode"],
            level=profile["current_level"],
            pace=profile.get("pace", DEFAULT_PACE),
            verification=profile.get("verification", DEFAULT_VERIFICATION),
            caveman=profile.get("caveman", DEFAULT_CAVEMAN),
            caveman_locked=bool(profile.get("caveman_locked", False)),
            event=event,
            workspace_root=workspace_root,
        )
    except Exception:
        pass


def update_profile_settings(
    workspace_root: Path,
    *,
    raw_mode: Optional[str] = None,
    raw_pace: Optional[str] = None,
    raw_verification: Optional[str] = None,
    raw_caveman: Optional[str] = None,
    reason: Optional[str] = None,
    allow_pro: Optional[bool] = None,
    sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour atomiquement le mode, la stratégie workflow, `verification`, `caveman` et/ou `allow_pro`."""
    ws_root = Path(workspace_root).resolve()
    target_mode = normalize_mode(raw_mode) if raw_mode is not None else None
    target_pace = normalize_pace(raw_pace) if raw_pace is not None else None
    target_verif = (
        normalize_verification(raw_verification) if raw_verification is not None else None
    )
    target_caveman = normalize_caveman(raw_caveman) if raw_caveman is not None else None
    if (
        target_mode is None
        and target_pace is None
        and target_verif is None
        and target_caveman is None
        and allow_pro is None
    ):
        raise ValueError(
            "Au moins un paramètre (`mode`, `pace`, `verification`, `caveman` ou `allow_pro`) est requis."
        )

    profile_path = resolve_profile_path(ws_root)
    lock_path = profile_path.with_suffix(".lock")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with exclusive_file_lock(lock_path):
        profile = load_profile(ws_root)
        prev_mode = profile["mode"]
        prev_level = profile["current_level"]
        prev_pace = profile["pace"]
        prev_verif = profile["verification"]
        prev_caveman = profile["caveman"]
        prev_caveman_locked = profile["caveman_locked"]

        if allow_pro is not None:
            profile["allow_pro"] = bool(allow_pro)

        if target_mode is not None:
            if target_mode == "evo":
                profile["mode"] = "evo"
                profile["locked"] = False
                if profile["current_level"] not in LEVELS:
                    profile["current_level"] = DEFAULT_LEVEL
            else:
                profile["mode"] = target_mode
                profile["current_level"] = target_mode
                profile["locked"] = True
                profile["suggested_level"] = target_mode

        if target_pace is not None:
            profile["pace"] = target_pace

        if target_verif is not None:
            profile["verification"] = target_verif

        if target_caveman is not None:
            if target_caveman == "auto":
                profile["caveman_locked"] = False
                next_cav, _ = choose_evo_caveman(
                    profile["current_level"],
                    profile.get("signals") or {},
                    int(profile.get("clean_streak") or 0),
                )
                profile["caveman"] = next_cav
            else:
                profile["caveman"] = target_caveman
                profile["caveman_locked"] = True

        if reason:
            final_reason = reason
        else:
            fragments: List[str] = []
            if target_mode is not None:
                if target_mode == "evo":
                    fragments.append(f"mode évolutif (palier : {profile['current_level']})")
                else:
                    fragments.append(f"verrouillé en {target_mode}")
            if target_pace is not None:
                if target_pace == "fast":
                    fragments.append(
                        "stratégie workflow optimisée temps (fast, réflexion inchangée)"
                    )
                else:
                    fragments.append("stratégie workflow par responsabilité (slow)")
            if target_verif is not None:
                if target_verif == "unsafe":
                    fragments.append("vérifications et tests désactivés (unsafe)")
                else:
                    fragments.append("vérifications et tests actifs (safe)")
            if target_caveman is not None:
                if target_caveman == "auto":
                    fragments.append(f"caveman auto ({profile['caveman']})")
                else:
                    fragments.append(f"caveman verrouillé en {target_caveman}")
            if fragments:
                joined = " · ".join(fragments)
                final_reason = joined[0].upper() + joined[1:] + "."
            else:
                final_reason = f"Option allow_pro mise à jour ({profile['allow_pro']})."

        profile["last_reason"] = final_reason
        profile["last_updated"] = now_iso

        changed = (
            prev_mode != profile["mode"]
            or prev_level != profile["current_level"]
            or prev_pace != profile["pace"]
            or prev_verif != profile["verification"]
            or prev_caveman != profile["caveman"]
            or prev_caveman_locked != profile["caveman_locked"]
            or not profile_path.is_file()
        )
        if changed:
            history = list(profile.get("history") or [])
            history.append(
                {
                    "timestamp": now_iso,
                    "event": "manual_set",
                    "from_mode": prev_mode,
                    "to_mode": profile["mode"],
                    "from_level": prev_level,
                    "to_level": profile["current_level"],
                    "from_pace": prev_pace,
                    "to_pace": profile["pace"],
                    "from_verification": prev_verif,
                    "to_verification": profile["verification"],
                    "from_caveman": prev_caveman,
                    "to_caveman": profile["caveman"],
                    "caveman_locked": profile["caveman_locked"],
                    "score": profile.get("last_score", 40),
                    "reason": final_reason,
                }
            )
            profile["history"] = history[-MAX_HISTORY:]

        _atomic_write_json(profile_path, profile)

    if sync_live:
        _sync_live_state_safe(
            ws_root,
            profile,
            event={
                "type": "PROFILE",
                "decision": "allow",
                "message": final_reason,
            },
        )
    return profile


def set_mode(
    workspace_root: Path,
    raw_mode: str,
    *,
    raw_pace: Optional[str] = None,
    raw_verification: Optional[str] = None,
    reason: Optional[str] = None,
    allow_pro: Optional[bool] = None,
    sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour et persiste le mode Gravity (`low`/`medium`/`high`/`ultra` verrouille, `evo` déverrouille)."""
    return update_profile_settings(
        workspace_root,
        raw_mode=raw_mode,
        raw_pace=raw_pace,
        raw_verification=raw_verification,
        reason=reason,
        allow_pro=allow_pro,
        sync_live=sync_live,
    )


def set_pace(
    workspace_root: Path,
    raw_pace: str,
    *,
    reason: Optional[str] = None,
    sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour et persiste la stratégie workflow (`slow` ou `fast`) sans changer la réflexion ni les tests."""
    return update_profile_settings(
        workspace_root,
        raw_pace=raw_pace,
        reason=reason,
        sync_live=sync_live,
    )


def set_verification(
    workspace_root: Path,
    raw_verification: str,
    *,
    reason: Optional[str] = None,
    sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour et persiste le mode de vérification (`safe` ou `unsafe`) sans changer le mode ni la stratégie temps."""
    return update_profile_settings(
        workspace_root,
        raw_verification=raw_verification,
        reason=reason,
        sync_live=sync_live,
    )


def set_caveman(
    workspace_root: Path,
    raw_caveman: str,
    *,
    reason: Optional[str] = None,
    sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour et persiste le réglage Caveman (`off`/`lite`/`full`/`ultra` verrouille, `auto`/`evo` déverrouille)."""
    return update_profile_settings(
        workspace_root,
        raw_caveman=raw_caveman,
        reason=reason,
        sync_live=sync_live,
    )


def collect_signals(
    workspace_root: Path,
    stop_entry: Optional[Dict[str, Any]] = None,
    *,
    since_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Collecte de manière déterministe les signaux projet et session (scan borné)."""
    ws_root = Path(workspace_root).resolve()
    source_files = 0
    has_tests = False
    has_ci = (ws_root / ".github" / "workflows").is_dir()

    if ws_root.is_dir():
        for dirpath, dirnames, filenames in os.walk(ws_root):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in EXCLUDED_SCAN_DIRS and not (d.startswith(".") and d not in (".github",))
            ]
            lower_dirs = {d.lower() for d in dirnames}
            if "test" in lower_dirs or "tests" in lower_dirs or "__tests__" in lower_dirs:
                has_tests = True
            if CI_DIR_NAMES.intersection(lower_dirs):
                has_ci = True

            for fname in filenames:
                if fname.startswith(".") and fname.lower() not in CI_FILE_NAMES:
                    continue
                source_files += 1
                low = fname.lower()
                if low in CI_FILE_NAMES:
                    has_ci = True
                if TEST_FILE_RE.search(fname):
                    has_tests = True
                if source_files >= MAX_SCAN_FILES:
                    break
            if source_files >= MAX_SCAN_FILES:
                break

    rules_dir = ws_root / RULES_REL_DIR
    rules_count = len(list(rules_dir.glob("*.md"))) if rules_dir.is_dir() else 0

    mcp_servers = 0
    mcp_path = ws_root / MCP_REL_PATH
    if mcp_path.is_file():
        try:
            mcp_data = json.loads(mcp_path.read_text(encoding="utf-8"))
            if isinstance(mcp_data, dict) and isinstance(mcp_data.get("mcpServers"), dict):
                mcp_servers = len(mcp_data["mcpServers"])
        except Exception:
            pass

    progress_steps = 0
    progress_path = ws_root / PROGRESS_REL_PATH
    if progress_path.is_file():
        try:
            for line in progress_path.read_text(encoding="utf-8").splitlines():
                match = CHECKED_STEP_RE.match(line)
                if match and "(bonus)" not in match.group(1).lower():
                    progress_steps += 1
            progress_steps = min(progress_steps, 8)
        except Exception:
            pass

    guard_deny_recent = 0
    guard_ask_recent = 0
    live_path = ws_root / LIVE_STATE_REL_PATH
    if live_path.is_file():
        try:
            live_data = json.loads(live_path.read_text(encoding="utf-8"))
            events = live_data.get("events", []) if isinstance(live_data, dict) else []
            if isinstance(events, list):
                for ev in events[-15:]:
                    if not isinstance(ev, dict):
                        continue
                    ev_ts_iso = ev.get("ts_iso")
                    if since_iso and isinstance(ev_ts_iso, str) and ev_ts_iso <= since_iso:
                        continue
                    dec = ev.get("decision")
                    if dec == "deny":
                        guard_deny_recent += 1
                    elif dec in ("ask", "force_ask"):
                        guard_ask_recent += 1
        except Exception:
            pass

    eval_pass_rate: Optional[float] = None
    report_path = ws_root / EVAL_REPORT_REL_PATH
    if report_path.is_file():
        try:
            report_data = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(report_data, dict):
                total = int(report_data.get("total") or 0)
                passed = int(report_data.get("passed") or 0)
                if total > 0:
                    eval_pass_rate = round(max(0.0, min(1.0, passed / float(total))), 4)
        except Exception:
            pass

    session_error = False
    if isinstance(stop_entry, dict):
        term_reason = str(stop_entry.get("termination_reason") or "").strip().upper()
        if stop_entry.get("error") or term_reason not in NORMAL_TERMINATION_REASONS:
            session_error = True

    return {
        "source_files": source_files,
        "rules_count": rules_count,
        "has_tests": has_tests,
        "has_ci": has_ci,
        "mcp_servers": mcp_servers,
        "progress_steps": progress_steps,
        "guard_deny_recent": guard_deny_recent,
        "guard_ask_recent": guard_ask_recent,
        "eval_pass_rate": eval_pass_rate,
        "session_error": session_error,
    }


def _score_to_level(score: int) -> str:
    for upper_excl, level in LEVEL_THRESHOLDS:
        if score < upper_excl:
            return level
    return "ultra"


def compute_score(signals: Dict[str, Any], clean_streak: int) -> Tuple[int, str, List[str]]:
    """Calcule le score de complexité/risque (0..100), le palier cible et les facteurs explicatifs."""
    score = 0
    factors: List[str] = []

    files = int(signals.get("source_files") or 0)
    if files >= 250:
        score += 35
        factors.append(f"{files}+ fichiers")
    elif files >= 80:
        score += 25
        factors.append(f"{files} fichiers")
    elif files >= 15:
        score += 15
        factors.append(f"{files} fichiers")
    else:
        score += 5
        factors.append(f"{files} fichiers")

    if signals.get("has_tests"):
        score += 5
        factors.append("tests")
    if signals.get("has_ci"):
        score += 5
        factors.append("CI/infra")

    if int(signals.get("rules_count") or 0) >= 4:
        score += 5
        factors.append("4+ règles")
    if int(signals.get("mcp_servers") or 0) > 0:
        score += 5
        factors.append("MCP actif")
    if int(signals.get("progress_steps") or 0) >= 5:
        score += 5

    if int(signals.get("guard_deny_recent") or 0) > 0:
        score += 15
        factors.append(f"{signals['guard_deny_recent']} alerte(s) deny")
    if int(signals.get("guard_ask_recent") or 0) >= 2:
        score += 10
        factors.append(f"{signals['guard_ask_recent']} validations force_ask")

    eval_rate = signals.get("eval_pass_rate")
    eval_failing = eval_rate is not None and float(eval_rate) < 1.0
    if eval_failing:
        score += 15
        factors.append(f"évals {int(round(float(eval_rate) * 100))}%")
    elif eval_rate is not None and float(eval_rate) >= 1.0:
        score -= 5
        factors.append("évals 100 %")

    if signals.get("session_error"):
        score += 15
        factors.append("erreur/arrêt anormal")

    if (
        clean_streak >= 3
        and not signals.get("session_error")
        and int(signals.get("guard_deny_recent") or 0) == 0
        and not eval_failing
    ):
        score -= 10
        factors.append(f"stabilité {clean_streak} sessions")

    clamped = max(0, min(100, score))
    return clamped, _score_to_level(clamped), factors


def choose_next_level(current_level: str, score: int, target_level: str) -> str:
    """Applique une hystérésis ±5 pts et limite la transition à ±1 palier par session."""
    if current_level not in LEVELS:
        return DEFAULT_LEVEL
    idx = LEVELS.index(current_level)
    low_bound, high_bound_excl = LEVEL_SCORE_RANGES[current_level]

    if idx < len(LEVELS) - 1 and score >= (high_bound_excl + HYSTERESIS_MARGIN):
        return LEVELS[idx + 1]
    if idx > 0 and score < (low_bound - HYSTERESIS_MARGIN):
        return LEVELS[idx - 1]
    return current_level


def evolve_on_stop(
    workspace_root: Path,
    stop_entry: Optional[Dict[str, Any]] = None,
    *,
    sync_live: bool = True,
) -> Optional[Dict[str, Any]]:
    """Réévalue le profil en fin de session (`Stop`) sans créer de fichier hors projets Gravity."""
    ws_root = Path(workspace_root).resolve()
    profile_path = resolve_profile_path(ws_root)
    progress_path = ws_root / PROGRESS_REL_PATH

    if not profile_path.is_file() and not progress_path.is_file():
        return None

    if isinstance(stop_entry, dict) and stop_entry.get("fully_idle") is False:
        return load_profile(ws_root)

    conv_id = None
    exec_num: Optional[int] = None
    if isinstance(stop_entry, dict):
        raw_cid = stop_entry.get("conversation_id") or stop_entry.get("conversationId")
        if isinstance(raw_cid, str) and raw_cid.strip():
            conv_id = raw_cid.strip()
        raw_exec = (
            stop_entry.get("execution_num")
            if stop_entry.get("execution_num") is not None
            else stop_entry.get("executionNum")
        )
        if raw_exec is not None:
            try:
                exec_num = int(raw_exec)
            except (TypeError, ValueError):
                exec_num = None

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    lock_path = profile_path.with_suffix(".lock")
    level_changed = False
    caveman_changed = False
    prev_level = DEFAULT_LEVEL

    with exclusive_file_lock(lock_path):
        profile = load_profile(ws_root)
        same_conv_next_turn = False
        if conv_id and profile.get("last_evaluated_conversation_id") == conv_id:
            if exec_num is None or exec_num == profile.get("last_evaluated_execution_num"):
                return profile
            same_conv_next_turn = True

        since_iso = (
            str(profile["last_updated"])
            if isinstance(profile.get("last_updated"), str) and profile.get("last_updated")
            else None
        )
        signals = collect_signals(ws_root, stop_entry, since_iso=since_iso)
        eval_rate = signals.get("eval_pass_rate")
        is_clean = (
            not signals["session_error"]
            and signals["guard_deny_recent"] == 0
            and (eval_rate is None or float(eval_rate) >= 1.0)
        )
        prev_streak = int(profile.get("clean_streak") or 0)
        if same_conv_next_turn:
            clean_streak = prev_streak if is_clean else 0
        else:
            clean_streak = (prev_streak + 1) if is_clean else 0
        score, target_level, factors = compute_score(signals, clean_streak)
        factors_str = ", ".join(factors[:4]) if factors else "signaux nominaux"

        prev_level = profile["current_level"]
        prev_caveman = profile["caveman"]

        if same_conv_next_turn:
            conv_start_level = str(profile.get("last_conversation_start_level") or prev_level)
            if conv_start_level not in LEVELS:
                conv_start_level = prev_level
        else:
            profile["sessions_analyzed"] = int(profile.get("sessions_analyzed") or 0) + 1
            conv_start_level = prev_level
            profile["last_conversation_start_level"] = conv_start_level

        profile["clean_streak"] = clean_streak
        profile["last_score"] = score
        profile["suggested_level"] = target_level
        profile["signals"] = signals
        profile["last_updated"] = now_iso
        if conv_id:
            profile["last_evaluated_conversation_id"] = conv_id
            profile["last_evaluated_execution_num"] = exec_num

        if profile["locked"]:
            profile["last_reason"] = (
                f"Verrouillé en {prev_level} (score {score}/100, suggestion projet : {target_level})."
            )
        else:
            next_level = choose_next_level(conv_start_level, score, target_level)
            profile["current_level"] = next_level
            if next_level != prev_level:
                level_changed = True
                reason_text = (
                    f"Passé de {prev_level} → {next_level} (score {score}/100 : {factors_str})."
                )
                profile["last_reason"] = reason_text
                history = list(profile.get("history") or [])
                history.append(
                    {
                        "timestamp": now_iso,
                        "event": "evo_adapt",
                        "conversation_id": conv_id,
                        "from_level": prev_level,
                        "to_level": next_level,
                        "score": score,
                        "reason": reason_text,
                    }
                )
                profile["history"] = history[-MAX_HISTORY:]
            else:
                profile["last_reason"] = (
                    f"Maintenu en {prev_level} (score {score}/100 : {factors_str})."
                )

        if not profile["caveman_locked"]:
            next_caveman, caveman_reason = choose_evo_caveman(
                profile["current_level"], signals, clean_streak
            )
            profile["caveman"] = next_caveman
            if next_caveman != prev_caveman:
                caveman_changed = True
                base_reason = str(profile["last_reason"]).rstrip(".")
                profile["last_reason"] = f"{base_reason} · {caveman_reason}."
                history = list(profile.get("history") or [])
                if level_changed and history and history[-1].get("event") == "evo_adapt":
                    history[-1]["from_caveman"] = prev_caveman
                    history[-1]["to_caveman"] = next_caveman
                    history[-1]["reason"] = profile["last_reason"]
                else:
                    history.append(
                        {
                            "timestamp": now_iso,
                            "event": "evo_caveman",
                            "conversation_id": conv_id,
                            "from_caveman": prev_caveman,
                            "to_caveman": next_caveman,
                            "score": score,
                            "reason": caveman_reason,
                        }
                    )
                profile["history"] = history[-MAX_HISTORY:]

        _atomic_write_json(profile_path, profile)

    if sync_live:
        ev = (
            {
                "type": "EVO",
                "decision": "allow",
                "message": profile["last_reason"],
            }
            if (level_changed or caveman_changed)
            else None
        )
        _sync_live_state_safe(ws_root, profile, event=ev)

    return profile


def _parse_bool(raw: str) -> bool:
    val = raw.strip().lower()
    if val in ("true", "1", "yes", "oui"):
        return True
    if val in ("false", "0", "no", "non"):
        return False
    raise argparse.ArgumentTypeError(f"Booléen invalide : {raw!r}")


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
    parser = argparse.ArgumentParser(description="Gestionnaire de profil et mode Gravity")
    sub = parser.add_subparsers(dest="command", required=True)

    p_get = sub.add_parser("get", help="Lit le profil et le preset actif.")
    p_get.add_argument("--workspace", type=Path, default=Path("."))
    p_get.add_argument("--json", action="store_true")

    p_set = sub.add_parser(
        "set",
        help="Définit le mode, la stratégie workflow (`slow`/`fast`), la vérification (`safe`/`unsafe`) et/ou Caveman.",
    )
    p_set.add_argument("--workspace", type=Path, default=Path("."))
    p_set.add_argument("--mode", default=None)
    p_set.add_argument("--pace", default=None)
    p_set.add_argument("--verify", "--verification", dest="verification", default=None)
    p_set.add_argument(
        "--caveman",
        default=None,
        help="Niveau Caveman : auto|evo|off|lite|full|ultra.",
    )
    p_set.add_argument("--allow-pro", type=_parse_bool, default=None)
    p_set.add_argument("--reason", default=None)
    p_set.add_argument("--json", action="store_true")

    p_evo = sub.add_parser("evolve", help="Déclenche une réévaluation evo sur le workspace.")
    p_evo.add_argument("--workspace", type=Path, default=Path("."))
    p_evo.add_argument("--conversation-id", default=None)
    p_evo.add_argument("--json", action="store_true")

    args = parser.parse_args()
    ws = args.workspace.resolve()

    if args.command == "get":
        profile = load_profile(ws)
        preset = get_active_preset(ws)
        payload = {
            "profile": profile,
            "preset": preset,
            "badge": format_mode_badge(profile),
            "caveman_badge": format_caveman_badge(profile),
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(
                f"Mode: {format_mode_badge(profile)} | {format_caveman_badge(profile)} | "
                f"Score: {profile['last_score']}/100 | Raison: {profile['last_reason']}"
            )
        return 0

    if args.command == "set":
        if (
            args.mode is None
            and args.pace is None
            and args.verification is None
            and args.caveman is None
            and args.allow_pro is None
        ):
            print(
                "[ERROR] Précisez au moins --mode, --pace, --verify, --caveman ou --allow-pro.",
                file=sys.stderr,
            )
            return 1
        try:
            profile = update_profile_settings(
                ws,
                raw_mode=args.mode,
                raw_pace=args.pace,
                raw_verification=args.verification,
                raw_caveman=args.caveman,
                reason=args.reason,
                allow_pro=args.allow_pro,
            )
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        preset = get_active_preset(ws)
        payload = {
            "profile": profile,
            "preset": preset,
            "badge": format_mode_badge(profile),
            "caveman_badge": format_caveman_badge(profile),
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(
                f"{format_mode_badge(profile)} · {format_caveman_badge(profile)} — {profile['last_reason']}"
            )
        return 0

    if args.command == "evolve":
        stop_entry: Dict[str, Any] = {"fully_idle": True}
        if args.conversation_id:
            stop_entry["conversation_id"] = args.conversation_id
        profile = evolve_on_stop(ws, stop_entry)
        if profile is None:
            if args.json:
                print(json.dumps({"skipped": True, "reason": "Non initialisé Gravity"}, ensure_ascii=False))
            else:
                print("[SKIP] Workspace non initialisé Gravity.")
            return 0
        if args.json:
            print(
                json.dumps(
                    {
                        "profile": profile,
                        "badge": format_mode_badge(profile),
                        "caveman_badge": format_caveman_badge(profile),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(
                f"{format_mode_badge(profile)} · {format_caveman_badge(profile)} — {profile['last_reason']}"
            )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
