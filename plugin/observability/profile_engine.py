#!/usr/bin/env python3
"""Moteur de profil et d'adaptation consultative Gravity (`quick`/`standard`/`deep`/`evo`, 100 % stdlib Python 3)."""

import argparse
import datetime
import json
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
from state_writer import (  # noqa: E402
    _atomic_write_json,
    exclusive_file_lock,
    resolve_eval_dir,
    resolve_profile_storage_path,
    resolve_runs_log_path,
    resolve_state_path,
    update_live_state,
)

PROFILE_REL_PATH = Path(".agents/gravity-workflow/profile.json")
PROGRESS_REL_PATH = Path(".agents/gravity-workflow/PROGRESS.md")
LIVE_STATE_REL_PATH = Path(".agents/gravity-workflow/observability/live_state.json")
EVAL_REPORT_REL_PATH = Path(".agents/gravity-workflow/evals/report.json")

LEVELS: Tuple[str, ...] = ("quick", "standard", "deep")
VALID_MODES: Tuple[str, ...] = ("evo", "quick", "standard", "deep")
VALID_SUGGESTED_PROFILES: Tuple[str, ...] = ("standard", "deep")
LEVEL_ALIASES: Dict[str, str] = {
    "low": "quick",
    "medium": "standard",
    "high": "deep",
    "ultra": "deep",
}
MODE_ALIASES: Dict[str, str] = {
    "evolutive": "evo",
    **LEVEL_ALIASES,
}
DEPRECATED_PACES = {"slow", "fast"}
VALID_VERIFICATIONS: Tuple[str, ...] = ("safe", "unsafe")

DEFAULT_MODE = "evo"
DEFAULT_LEVEL = "standard"
MAX_HISTORY = 20

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

LEVEL_PRESETS: Dict[str, Dict[str, Any]] = {
    "quick": {
        "plan_model": "flash",
        "build_model": "flash",
        "review_model": "flash",
        "eval_candidate": "flash",
        "eval_judge": "flash",
        "max_remediation_rounds": 0,
        "skip_separate_planner": True,
    },
    "standard": {
        "plan_model": "flash",
        "build_model": "flash",
        "review_model": "pro",
        "eval_candidate": "flash",
        "eval_judge": "pro",
        "max_remediation_rounds": 1,
        "skip_separate_planner": False,
    },
    "deep": {
        "plan_model": "pro",
        "build_model": "flash",
        "review_model": "pro",
        "eval_candidate": "flash",
        "eval_judge": "pro",
        "max_remediation_rounds": 2,
        "skip_separate_planner": False,
    },
}


def default_profile() -> Dict[str, Any]:
    """Retourne un profil Gravity initialisé par défaut (`mode=evo`, `current_level=standard`, `caveman=lite`)."""
    return {
        "schema_version": 2,
        "mode": DEFAULT_MODE,
        "current_level": DEFAULT_LEVEL,
        "locked": False,
        "caveman": DEFAULT_CAVEMAN,
        "caveman_locked": False,
        "suggested_profile": DEFAULT_LEVEL,
        "allow_pro": True,
        "model_overrides": {},
        "sessions_analyzed": 0,
        "clean_streak": 0,
        "last_score": 50,
        "last_reason": "Initialisation par défaut (mode evo consultatif, profil standard).",
        "last_evaluated_conversation_id": None,
        "last_evaluated_execution_num": None,
        "last_updated": None,
        "signals": {
            "reviewer_rejections_recent": 0,
            "tool_or_test_errors_recent": 0,
            "guard_deny_recent": 0,
            "guard_ask_recent": 0,
            "eval_pass_rate": None,
            "session_error": False,
        },
        "history": [],
    }


def normalize_level(raw: str) -> str:
    """Valide et normalise un profil (`quick`, `standard`, `deep` ou alias `low`/`medium`/`high`/`ultra`)."""
    cleaned = str(raw or "").strip().lower()
    cleaned = LEVEL_ALIASES.get(cleaned, cleaned)
    if cleaned not in LEVELS:
        raise ValueError(
            f"Profil Gravity invalide {raw!r} (attendu : {', '.join(LEVELS)})."
        )
    return cleaned


def normalize_mode(raw: str) -> str:
    """Valide et normalise un mode (`evo`, `evolutive`, `quick`, `standard`, `deep` + alias `low`/`medium`/`high`/`ultra`)."""
    cleaned = str(raw or "").strip().lower()
    cleaned = MODE_ALIASES.get(cleaned, cleaned)
    if cleaned not in VALID_MODES:
        raise ValueError(
            f"Mode Gravity invalide {raw!r} (attendu : {', '.join(VALID_MODES)} ou 'evolutive')."
        )
    return cleaned


def normalize_pace(raw: str) -> str:
    """Tolère les anciens mots-clés dépréciés `slow`/`fast` sans erreur."""
    cleaned = str(raw or "").strip().lower()
    if cleaned in DEPRECATED_PACES:
        return cleaned
    raise ValueError(
        f"Paramètre déprécié pace invalide {raw!r} (seuls les alias historiques 'slow'/'fast' sont tolérés)."
    )


def normalize_verification(raw: str) -> str:
    """Valide un mode de vérification éphémère (`safe` ou `unsafe`)."""
    cleaned = str(raw or "").strip().lower()
    if cleaned not in VALID_VERIFICATIONS:
        raise ValueError(
            f"Mode de vérification Gravity invalide {raw!r} (attendu : {', '.join(VALID_VERIFICATIONS)})."
        )
    return cleaned


def resolve_profile_path(workspace_root: Optional[Path] = None) -> Path:
    """Retourne le chemin centralisé vers `profile.json` pour le workspace cible."""
    return resolve_profile_storage_path(workspace_root)


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
        "last_updated",
    ):
        if key in raw:
            merged[key] = raw[key]

    try:
        mode = normalize_mode(str(raw.get("mode", DEFAULT_MODE)))
    except ValueError:
        mode = DEFAULT_MODE

    raw_level = str(raw.get("current_level", DEFAULT_LEVEL)).strip().lower()
    try:
        current_level = normalize_level(raw_level)
    except ValueError:
        current_level = DEFAULT_LEVEL

    if mode != "evo":
        current_level = mode
        locked = True
    else:
        locked = False

    raw_suggested = str(
        raw.get("suggested_profile", raw.get("suggested_level", DEFAULT_LEVEL))
    ).strip().lower()
    mapped_suggested = LEVEL_ALIASES.get(raw_suggested, raw_suggested)
    suggested_profile = (
        mapped_suggested if mapped_suggested in VALID_SUGGESTED_PROFILES else DEFAULT_LEVEL
    )

    raw_caveman = str(raw.get("caveman", DEFAULT_CAVEMAN)).strip().lower()
    caveman = raw_caveman if raw_caveman in VALID_CAVEMAN_LEVELS else DEFAULT_CAVEMAN
    caveman_locked = bool(raw.get("caveman_locked", False))

    merged["schema_version"] = 2
    merged["mode"] = mode
    merged["current_level"] = current_level
    merged["locked"] = locked
    merged["caveman"] = caveman
    merged["caveman_locked"] = caveman_locked
    merged["suggested_profile"] = suggested_profile
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
        for k in sig:
            if k in raw["signals"]:
                sig[k] = raw["signals"][k]
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
        merged["last_score"] = max(0, min(100, int(merged.get("last_score") or 50)))
    except (TypeError, ValueError):
        merged["last_score"] = 50

    raw_exec = merged.get("last_evaluated_execution_num")
    if raw_exec is not None:
        try:
            merged["last_evaluated_execution_num"] = int(raw_exec)
        except (TypeError, ValueError):
            merged["last_evaluated_execution_num"] = None

    return merged


def load_profile(workspace_root: Optional[Path] = None) -> Dict[str, Any]:
    """Charge `profile.json` centralisé (repli lecture seule sur `<ws>/.agents/gravity-workflow/profile.json`)."""
    profile_path = resolve_profile_path(workspace_root)
    if profile_path.is_file():
        try:
            loaded = json.loads(profile_path.read_text(encoding="utf-8"))
            return _sanitize_profile(loaded)
        except Exception:
            return default_profile()

    ws_root = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
    legacy_ws_profile = ws_root / PROFILE_REL_PATH
    if legacy_ws_profile.is_file():
        try:
            loaded = json.loads(legacy_ws_profile.read_text(encoding="utf-8"))
            return _sanitize_profile(loaded)
        except Exception:
            return default_profile()

    return default_profile()


def get_active_preset(
    workspace_root: Optional[Path] = None,
    *,
    level_override: Optional[str] = None,
    pace_override: Optional[str] = None,
    verification_override: Optional[str] = None,
    no_verify: bool = False,
    caveman_override: Optional[str] = None,
    plan_model_override: Optional[str] = None,
    build_model_override: Optional[str] = None,
    review_model_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Résout le preset actif sans clé morte (`quick`/`standard`/`deep`, surcharges A/B, `skip_verification` éphémère)."""
    profile = load_profile(workspace_root)

    if pace_override is not None and str(pace_override).strip():
        _ = str(pace_override).strip().lower()  # Toléré comme alias historique sans effet

    level = profile["current_level"]
    if level_override is not None and str(level_override).strip():
        try:
            level = normalize_level(str(level_override))
        except ValueError:
            pass

    preset: Dict[str, Any] = dict(LEVEL_PRESETS[level])

    overrides = profile.get("model_overrides") or {}
    for key in MODEL_OVERRIDE_KEYS:
        val = overrides.get(key)
        if isinstance(val, str) and val in VALID_MODEL_TIERS:
            preset[key] = val

    for key, arg_val in (
        ("plan_model", plan_model_override),
        ("build_model", build_model_override),
        ("review_model", review_model_override),
    ):
        if isinstance(arg_val, str) and arg_val.strip() in VALID_MODEL_TIERS:
            preset[key] = arg_val.strip()

    if not profile.get("allow_pro", True):
        for key in MODEL_OVERRIDE_KEYS:
            if preset.get(key) == "pro":
                preset[key] = "flash"

    chosen_verif = str(verification_override or "").strip().lower()
    skip_verification = bool(no_verify) or (chosen_verif == "unsafe")
    preset["skip_verification"] = skip_verification
    if skip_verification:
        preset["max_remediation_rounds"] = 0

    chosen_caveman = str(caveman_override or "").strip().lower()
    caveman = (
        chosen_caveman
        if chosen_caveman in VALID_CAVEMAN_LEVELS
        else profile.get("caveman", DEFAULT_CAVEMAN)
    )
    preset["caveman"] = caveman
    preset["caveman_planner"] = effective_planner_caveman(caveman)
    preset["caveman_evals"] = "off"

    preset["mode"] = profile["mode"]
    preset["level"] = level
    preset["locked"] = bool(profile["locked"])
    preset["suggested_profile"] = profile.get("suggested_profile", DEFAULT_LEVEL)
    preset["last_reason"] = profile.get("last_reason", "")
    return preset


def role_models_from_preset(
    preset: Dict[str, Any],
    *,
    skip_planner: Optional[bool] = None,
    skip_review: Optional[bool] = None,
) -> Dict[str, str]:
    """Construit la carte `role_models` effective pour `live_state.json` et `runs.jsonl` (essais A/B)."""
    plan_skipped = bool(preset.get("skip_separate_planner")) if skip_planner is None else skip_planner
    review_skipped = bool(preset.get("skip_verification")) if skip_review is None else skip_review
    return {
        "plan": "skipped" if plan_skipped else str(preset.get("plan_model", "flash")),
        "build": str(preset.get("build_model", "flash")),
        "review": "skipped" if review_skipped else str(preset.get("review_model", "pro")),
    }


def format_mode_badge(profile: Dict[str, Any]) -> str:
    """Formate le badge compact du profil actif (`evo:standard` ou `deep[lock]`)."""
    mode = str(profile.get("mode", DEFAULT_MODE))
    level = str(profile.get("current_level", DEFAULT_LEVEL))
    if mode == "evo":
        return f"evo:{level}"
    return f"{level}[lock]"


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
        preset = get_active_preset(workspace_root)
        update_live_state(
            mode=profile["mode"],
            level=profile["current_level"],
            preset_profile=profile["current_level"],
            suggested_profile=profile.get("suggested_profile", DEFAULT_LEVEL),
            suggested_reason=profile.get("last_reason", ""),
            role_models=role_models_from_preset(preset),
            caveman=profile.get("caveman", DEFAULT_CAVEMAN),
            caveman_locked=bool(profile.get("caveman_locked", False)),
            event=event,
            workspace_root=workspace_root,
        )
    except Exception:
        pass


def _apply_model_override(
    overrides: Dict[str, str],
    key: str,
    raw_value: Optional[str],
) -> Tuple[bool, Optional[str]]:
    if raw_value is None:
        return False, None
    cleaned = str(raw_value).strip().lower()
    if cleaned in ("", "default", "reset", "none", "clear"):
        removed = key in overrides
        overrides.pop(key, None)
        return removed, f"{key} réinitialisé" if removed else None
    if cleaned not in VALID_MODEL_TIERS:
        raise ValueError(
            f"Modèle invalide pour {key} : {raw_value!r} (attendu : {', '.join(sorted(VALID_MODEL_TIERS))} ou 'reset')."
        )
    changed = overrides.get(key) != cleaned
    overrides[key] = cleaned
    return changed, f"{key}={cleaned}"


def update_profile_settings(
    workspace_root: Path,
    *,
    raw_mode: Optional[str] = None,
    raw_pace: Optional[str] = None,
    raw_verification: Optional[str] = None,
    raw_caveman: Optional[str] = None,
    plan_model: Optional[str] = None,
    build_model: Optional[str] = None,
    review_model: Optional[str] = None,
    model_overrides: Optional[Dict[str, Optional[str]]] = None,
    reason: Optional[str] = None,
    allow_pro: Optional[bool] = None,
    sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour atomiquement le profil centralisé (`mode`, `caveman`, `allow_pro`, `model_overrides`)."""
    ws_root = Path(workspace_root).resolve()
    target_mode = normalize_mode(raw_mode) if raw_mode is not None else None
    deprecated_pace: Optional[str] = None
    if raw_pace is not None:
        deprecated_pace = normalize_pace(raw_pace)

    if raw_verification is not None:
        verif_norm = normalize_verification(raw_verification)
        if verif_norm == "unsafe":
            raise ValueError(
                "Le mode 'unsafe' / '--no-verify' est strictement éphémère (1 tâche ou 1 invocation run_workflow) et n'est jamais persisté dans profile.json."
            )

    target_caveman = normalize_caveman(raw_caveman) if raw_caveman is not None else None
    has_model_update = (
        plan_model is not None
        or build_model is not None
        or review_model is not None
        or bool(model_overrides)
    )
    if (
        target_mode is None
        and deprecated_pace is None
        and raw_verification is None
        and target_caveman is None
        and allow_pro is None
        and not has_model_update
    ):
        raise ValueError(
            "Au moins un paramètre (`mode`, `caveman`, `allow_pro` ou surcharge modèle) est requis."
        )

    profile_path = resolve_profile_path(ws_root)
    lock_path = profile_path.with_suffix(".lock")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with exclusive_file_lock(lock_path):
        profile = load_profile(ws_root)
        prev_mode = profile["mode"]
        prev_level = profile["current_level"]
        prev_caveman = profile["caveman"]
        prev_caveman_locked = profile["caveman_locked"]
        prev_overrides = dict(profile.get("model_overrides") or {})

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

        override_notes: List[str] = []
        new_overrides = dict(prev_overrides)
        for key, val in (
            ("plan_model", plan_model),
            ("build_model", build_model),
            ("review_model", review_model),
        ):
            _, note = _apply_model_override(new_overrides, key, val)
            if note:
                override_notes.append(note)

        if isinstance(model_overrides, dict):
            for key, val in model_overrides.items():
                if key not in MODEL_OVERRIDE_KEYS:
                    raise ValueError(f"Clé model_overrides inconnue : {key!r}")
                _, note = _apply_model_override(new_overrides, key, val)
                if note:
                    override_notes.append(note)

        profile["model_overrides"] = new_overrides

        if reason:
            final_reason = reason
        else:
            fragments: List[str] = []
            if target_mode is not None:
                if target_mode == "evo":
                    fragments.append(f"mode évolutif consultatif (actif : {profile['current_level']})")
                else:
                    fragments.append(f"verrouillé en {target_mode}")
            if target_caveman is not None:
                if target_caveman == "auto":
                    fragments.append(f"caveman auto ({profile['caveman']})")
                else:
                    fragments.append(f"caveman verrouillé en {target_caveman}")
            if override_notes:
                fragments.append("modèles A/B : " + ", ".join(override_notes))
            if deprecated_pace is not None:
                fragments.append(f"alias historique '{deprecated_pace}' ignoré")
            if raw_verification is not None:
                fragments.append("vérifications actives (safe)")
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
            or prev_caveman != profile["caveman"]
            or prev_caveman_locked != profile["caveman_locked"]
            or prev_overrides != profile["model_overrides"]
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
                    "from_caveman": prev_caveman,
                    "to_caveman": profile["caveman"],
                    "caveman_locked": profile["caveman_locked"],
                    "model_overrides": dict(profile["model_overrides"]),
                    "score": profile.get("last_score", 50),
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
    """Met à jour et persiste le mode Gravity (`quick`/`standard`/`deep` verrouille, `evo` déverrouille)."""
    return update_profile_settings(
        workspace_root,
        raw_mode=raw_mode,
        raw_pace=raw_pace,
        raw_verification=raw_verification,
        reason=reason,
        allow_pro=allow_pro,
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


def _read_recent_live_events(ws_root: Path, since_iso: Optional[str]) -> Tuple[int, int, int, int]:
    """Retourne `(reviewer_rejections, tool_or_test_errors, guard_deny, guard_ask)` depuis `live_state.json`."""
    reviewer_rejections = 0
    tool_or_test_errors = 0
    guard_deny = 0
    guard_ask = 0

    central_live = resolve_state_path(workspace_root=ws_root)
    fallback_live = ws_root / LIVE_STATE_REL_PATH
    live_path = central_live if central_live.is_file() else fallback_live
    if not live_path.is_file():
        return 0, 0, 0, 0

    try:
        live_data = json.loads(live_path.read_text(encoding="utf-8"))
        events = live_data.get("events", []) if isinstance(live_data, dict) else []
        if isinstance(events, list):
            for ev in events[-25:]:
                if not isinstance(ev, dict):
                    continue
                ev_ts_iso = ev.get("ts_iso")
                if since_iso and isinstance(ev_ts_iso, str) and ev_ts_iso <= since_iso:
                    continue
                ev_type = str(ev.get("type") or "").upper()
                dec = str(ev.get("decision") or "").lower()
                msg = str(ev.get("message") or "").upper()
                if ev_type in ("REVIEWER", "REMEDIATION"):
                    if dec in ("deny", "force_ask") or "FAIL" in msg or "ESCALADE" in msg:
                        reviewer_rejections += 1
                elif ev_type == "EVALS":
                    if dec == "deny":
                        tool_or_test_errors += 1
                else:
                    if dec == "deny":
                        guard_deny += 1
                    elif dec in ("ask", "force_ask"):
                        guard_ask += 1
    except Exception:
        pass
    return reviewer_rejections, tool_or_test_errors, guard_deny, guard_ask


def _read_recent_pipeline_failures(ws_root: Path, since_iso: Optional[str]) -> int:
    runs_path = resolve_runs_log_path(ws_root)
    if not runs_path.is_file():
        return 0
    failures = 0
    try:
        for line in runs_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]:
            if not line.strip():
                continue
            entry = json.loads(line)
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp")
            if since_iso and isinstance(ts, str) and ts <= since_iso:
                continue
            if entry.get("event") == "review_pipeline" and str(entry.get("verdict", "")).upper() == "FAIL":
                failures += 1
    except Exception:
        pass
    return failures


def collect_signals(
    workspace_root: Path,
    stop_entry: Optional[Dict[str, Any]] = None,
    *,
    since_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Collecte uniquement les signaux comportementaux `evo` (zéro comptage de fichiers `< 80`)."""
    ws_root = Path(workspace_root).resolve()

    rev_ev, eval_err_ev, guard_deny_recent, guard_ask_recent = _read_recent_live_events(
        ws_root, since_iso
    )
    rev_runs = _read_recent_pipeline_failures(ws_root, since_iso)
    reviewer_rejections_recent = rev_ev + rev_runs

    eval_pass_rate: Optional[float] = None
    central_report = resolve_eval_dir(ws_root) / "report.json"
    fallback_report = ws_root / EVAL_REPORT_REL_PATH
    report_path = central_report if central_report.is_file() else fallback_report
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
    extra_errors = 0
    if isinstance(stop_entry, dict):
        term_reason = str(stop_entry.get("termination_reason") or "").strip().upper()
        if stop_entry.get("error") or term_reason not in NORMAL_TERMINATION_REASONS:
            session_error = True
        for key in ("tool_errors", "test_errors"):
            raw_val = stop_entry.get(key)
            if raw_val is not None:
                try:
                    extra_errors += max(0, int(raw_val))
                except (TypeError, ValueError):
                    pass

    tool_or_test_errors_recent = eval_err_ev + extra_errors + (1 if session_error else 0)

    return {
        "reviewer_rejections_recent": reviewer_rejections_recent,
        "tool_or_test_errors_recent": tool_or_test_errors_recent,
        "guard_deny_recent": guard_deny_recent,
        "guard_ask_recent": guard_ask_recent,
        "eval_pass_rate": eval_pass_rate,
        "session_error": session_error,
    }


def compute_score(signals: Dict[str, Any], clean_streak: int) -> Tuple[int, str, List[str]]:
    """Évalue la recommandation consultative `evo` (`standard` | `deep`) à partir des signaux comportementaux."""
    score = 50
    factors: List[str] = []

    rev_rej = int(signals.get("reviewer_rejections_recent") or 0)
    if rev_rej > 0:
        score += min(40, 25 * rev_rej)
        factors.append(f"{rev_rej} rejet(s) Reviewer")

    tool_errs = int(signals.get("tool_or_test_errors_recent") or 0)
    session_err = bool(signals.get("session_error"))
    if tool_errs > 0 or session_err:
        score += 20
        factors.append("erreur outil/test ou session")

    deny_cnt = int(signals.get("guard_deny_recent") or 0)
    if deny_cnt > 0:
        score += 20
        factors.append(f"{deny_cnt} alerte(s) deny")

    eval_rate = signals.get("eval_pass_rate")
    eval_failing = eval_rate is not None and float(eval_rate) < 1.0
    if eval_failing:
        score += 15
        factors.append(f"évals {int(round(float(eval_rate) * 100))}%")

    has_friction = bool(rev_rej > 0 or tool_errs > 0 or session_err or deny_cnt > 0 or eval_failing)
    if not has_friction and clean_streak >= 2:
        score -= 10
        factors.append(f"stabilité {clean_streak} sessions")
    elif not has_friction:
        factors.append("signaux sains")

    clamped = max(0, min(100, score))
    suggested = "deep" if (has_friction or clamped >= 65) else "standard"
    return clamped, suggested, factors


def evolve_on_stop(
    workspace_root: Path,
    stop_entry: Optional[Dict[str, Any]] = None,
    *,
    sync_live: bool = True,
) -> Dict[str, Any]:
    """Réévalue `evo` en mode consultatif (`suggested_profile`: `standard`|`deep`) sans muter le profil actif."""
    ws_root = Path(workspace_root).resolve()
    profile_path = resolve_profile_path(ws_root)

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
    suggestion_changed = False
    caveman_changed = False

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
            and signals["reviewer_rejections_recent"] == 0
            and signals["tool_or_test_errors_recent"] == 0
            and signals["guard_deny_recent"] == 0
            and (eval_rate is None or float(eval_rate) >= 1.0)
        )
        prev_streak = int(profile.get("clean_streak") or 0)
        if same_conv_next_turn:
            clean_streak = prev_streak if is_clean else 0
        else:
            profile["sessions_analyzed"] = int(profile.get("sessions_analyzed") or 0) + 1
            clean_streak = (prev_streak + 1) if is_clean else 0

        score, suggested_profile, factors = compute_score(signals, clean_streak)
        factors_str = ", ".join(factors[:4]) if factors else "signaux sains"

        prev_suggested = str(profile.get("suggested_profile") or DEFAULT_LEVEL)
        prev_caveman = profile["caveman"]

        profile["clean_streak"] = clean_streak
        profile["last_score"] = score
        profile["suggested_profile"] = suggested_profile
        profile["signals"] = signals
        profile["last_updated"] = now_iso
        if conv_id:
            profile["last_evaluated_conversation_id"] = conv_id
            profile["last_evaluated_execution_num"] = exec_num

        if suggested_profile != prev_suggested:
            suggestion_changed = True
            reason_text = (
                f"Suggestion evo : {prev_suggested} → {suggested_profile} ({factors_str})."
            )
            history = list(profile.get("history") or [])
            history.append(
                {
                    "timestamp": now_iso,
                    "event": "evo_suggest",
                    "conversation_id": conv_id,
                    "from_suggested": prev_suggested,
                    "to_suggested": suggested_profile,
                    "score": score,
                    "reason": reason_text,
                }
            )
            profile["history"] = history[-MAX_HISTORY:]
        else:
            reason_text = f"Suggestion evo : {suggested_profile} ({factors_str})."

        if profile["locked"]:
            profile["last_reason"] = (
                f"Profil verrouillé : {profile['current_level']} · {reason_text}"
            )
        else:
            profile["last_reason"] = (
                f"Profil actif : {profile['current_level']} · {reason_text}"
            )

        if not profile["caveman_locked"]:
            next_caveman, caveman_reason = choose_evo_caveman(
                profile["current_level"], signals, clean_streak
            )
            profile["caveman"] = next_caveman
            if next_caveman != prev_caveman:
                caveman_changed = True
                profile["last_reason"] = f"{profile['last_reason'].rstrip('.')} · {caveman_reason}."

        _atomic_write_json(profile_path, profile)

    if sync_live:
        ev = (
            {
                "type": "EVO",
                "decision": "allow",
                "message": profile["last_reason"],
            }
            if (suggestion_changed or caveman_changed)
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
        help="Définit le mode (`quick`/`standard`/`deep`/`evo`), `caveman` et/ou les surcharges modèles A/B.",
    )
    p_set.add_argument("--workspace", type=Path, default=Path("."))
    p_set.add_argument("--mode", default=None)
    p_set.add_argument("--pace", default=None, help="Alias historique toléré (`slow`/`fast`, sans effet).")
    p_set.add_argument("--verify", "--verification", dest="verification", default=None)
    p_set.add_argument(
        "--caveman",
        default=None,
        help="Niveau Caveman : auto|evo|off|lite|full|ultra.",
    )
    p_set.add_argument("--plan-model", default=None, help="Surcharge A/B : inherit|flash_lite|flash|pro|reset.")
    p_set.add_argument("--build-model", default=None, help="Surcharge A/B : inherit|flash_lite|flash|pro|reset.")
    p_set.add_argument("--review-model", default=None, help="Surcharge A/B : inherit|flash_lite|flash|pro|reset.")
    p_set.add_argument("--allow-pro", type=_parse_bool, default=None)
    p_set.add_argument("--reason", default=None)
    p_set.add_argument("--json", action="store_true")

    p_evo = sub.add_parser("evolve", help="Déclenche une réévaluation consultative evo sur le workspace.")
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
            "storage_path": str(resolve_profile_path(ws)),
        }
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(
                f"Mode: {format_mode_badge(profile)} | {format_caveman_badge(profile)} | "
                f"Suggestion: {profile['suggested_profile']} | {profile['last_reason']}"
            )
        return 0

    if args.command == "set":
        if (
            args.mode is None
            and args.pace is None
            and args.verification is None
            and args.caveman is None
            and args.plan_model is None
            and args.build_model is None
            and args.review_model is None
            and args.allow_pro is None
        ):
            print(
                "[ERROR] Précisez au moins --mode, --caveman, --plan-model, --build-model, --review-model ou --allow-pro.",
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
                plan_model=args.plan_model,
                build_model=args.build_model,
                review_model=args.review_model,
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
