#!/usr/bin/env python3
"""Moteur de profil et d'adaptation consultative Gravity (`quick`/`standard`/`deep`/`evo`, 100 % stdlib Python 3)."""

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OBS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = OBS_DIR.parent
for _p in (str(PLUGIN_ROOT), str(OBS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _shared.hook_runtime import configure_utf8_stdio  # noqa: E402
from caveman_protocol import (  # noqa: E402
    DEFAULT_CAVEMAN,
    VALID_CAVEMAN_LEVELS,
    choose_evo_caveman,
    effective_planner_caveman,
    normalize_caveman,
)
from evo_engine import (  # noqa: E402
    EVAL_REPORT_REL_PATH,
    LIVE_STATE_REL_PATH,
    NORMAL_TERMINATION_REASONS,
    _has_behavioral_friction,
    _read_recent_live_events,
    _read_recent_pipeline_failures,
    collect_signals,
    compute_metrics_delta,
    compute_score,
    evolve_on_stop,
)
from state_writer import (  # noqa: E402
    _atomic_write_json,
    exclusive_file_lock,
    resolve_profile_storage_path,
    update_live_state,
)

PROFILE_REL_PATH = Path(".agents/gravity-workflow/profile.json")
PROGRESS_REL_PATH = Path(".agents/gravity-workflow/PROGRESS.md")

LEVELS: Tuple[str, ...] = ("quick", "standard", "deep")
VALID_MODES: Tuple[str, ...] = ("evo", "quick", "standard", "deep")
VALID_SUGGESTED_PROFILES: Tuple[str, ...] = ("standard", "deep")
LEVEL_ALIASES: Dict[str, str] = {"low": "quick", "medium": "standard", "high": "deep", "ultra": "deep"}
MODE_ALIASES: Dict[str, str] = {"evolutive": "evo", **LEVEL_ALIASES}
DEPRECATED_PACES = {"slow", "fast"}
VALID_VERIFICATIONS: Tuple[str, ...] = ("safe", "unsafe")

DEFAULT_MODE = "evo"
DEFAULT_LEVEL = "standard"
VALID_EXECUTION_ENGINES: Tuple[str, ...] = ("swarm", "run_workflow")
DEFAULT_EXECUTION_ENGINE = "swarm"
MAX_HISTORY = 20

VALID_MODEL_TIERS = {"inherit", "flash_lite", "flash", "pro"}
MODEL_OVERRIDE_KEYS: Tuple[str, ...] = ("plan_model", "build_model", "review_model", "eval_candidate", "eval_judge")

LEVEL_PRESETS: Dict[str, Dict[str, Any]] = {
    "quick": {
        "plan_model": "flash", "build_model": "flash", "review_model": "flash",
        "eval_candidate": "flash", "eval_judge": "flash",
        "max_remediation_rounds": 0, "skip_separate_planner": True,
    },
    "standard": {
        "plan_model": "flash", "build_model": "flash", "review_model": "pro",
        "eval_candidate": "flash", "eval_judge": "pro",
        "max_remediation_rounds": 1, "skip_separate_planner": False,
    },
    "deep": {
        "plan_model": "pro", "build_model": "flash", "review_model": "pro",
        "eval_candidate": "flash", "eval_judge": "pro",
        "max_remediation_rounds": 2, "skip_separate_planner": False,
    },
}


def default_profile() -> Dict[str, Any]:
    """Retourne un profil Gravity initialisé par défaut (`mode=evo`, `current_level=standard`, `caveman=lite`)."""
    return {
        "schema_version": 2, "mode": DEFAULT_MODE, "current_level": DEFAULT_LEVEL, "locked": False,
        "caveman": DEFAULT_CAVEMAN, "caveman_locked": False, "execution_engine": DEFAULT_EXECUTION_ENGINE,
        "suggested_profile": DEFAULT_LEVEL, "allow_pro": True, "model_overrides": {},
        "sessions_analyzed": 0, "clean_streak": 0, "last_score": 50,
        "last_reason": "Initialisation par défaut (mode evo consultatif, profil standard).",
        "last_evaluated_conversation_id": None, "last_evaluated_execution_num": None, "last_updated": None,
        "signals": {
            "reviewer_rejections_recent": 0, "tool_or_test_errors_recent": 0,
            "guard_deny_recent": 0, "guard_ask_recent": 0,
            "eval_pass_rate": None, "session_error": False,
            "metrics": {"available": False, "sample_count": 0},
        },
        "history": [],
    }


def normalize_level(raw: str) -> str:
    """Valide et normalise un profil (`quick`, `standard`, `deep` ou alias `low`/`medium`/`high`/`ultra`)."""
    cleaned = LEVEL_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
    if cleaned not in LEVELS:
        raise ValueError(f"Profil Gravity invalide {raw!r} (attendu : {', '.join(LEVELS)}).")
    return cleaned


def normalize_mode(raw: str) -> str:
    """Valide et normalise un mode (`evo`, `evolutive`, `quick`, `standard`, `deep` + alias `low`/`medium`/`high`/`ultra`)."""
    cleaned = MODE_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
    if cleaned not in VALID_MODES:
        raise ValueError(f"Mode Gravity invalide {raw!r} (attendu : {', '.join(VALID_MODES)} ou 'evolutive').")
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


def normalize_execution_engine(raw: str) -> str:
    """Valide et normalise un moteur d'exécution (`swarm` ou `run_workflow`)."""
    cleaned = str(raw or "").strip().lower()
    if cleaned not in VALID_EXECUTION_ENGINES:
        raise ValueError(
            f"Moteur d'exécution Gravity invalide {raw!r} (attendu : {', '.join(VALID_EXECUTION_ENGINES)})."
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
        "schema_version", "allow_pro", "sessions_analyzed", "clean_streak",
        "last_score", "last_reason", "last_evaluated_conversation_id",
        "last_evaluated_execution_num", "last_updated",
    ):
        if key in raw:
            merged[key] = raw[key]

    raw_ws = raw.get("workspace")
    if isinstance(raw_ws, str) and raw_ws.strip():
        merged["workspace"] = raw_ws.strip()

    try:
        mode = normalize_mode(str(raw.get("mode", DEFAULT_MODE)))
    except ValueError:
        mode = DEFAULT_MODE

    try:
        current_level = normalize_level(str(raw.get("current_level", DEFAULT_LEVEL)).strip().lower())
    except ValueError:
        current_level = DEFAULT_LEVEL

    current_level, locked = (mode, True) if mode != "evo" else (current_level, False)

    raw_suggested = str(raw.get("suggested_profile", raw.get("suggested_level", DEFAULT_LEVEL))).strip().lower()
    mapped_suggested = LEVEL_ALIASES.get(raw_suggested, raw_suggested)
    suggested_profile = mapped_suggested if mapped_suggested in VALID_SUGGESTED_PROFILES else DEFAULT_LEVEL

    raw_caveman = str(raw.get("caveman", DEFAULT_CAVEMAN)).strip().lower()
    caveman = raw_caveman if raw_caveman in VALID_CAVEMAN_LEVELS else DEFAULT_CAVEMAN

    raw_engine = raw.get("execution_engine", DEFAULT_EXECUTION_ENGINE)
    try:
        execution_engine = normalize_execution_engine("" if raw_engine is None else str(raw_engine))
    except ValueError:
        execution_engine = DEFAULT_EXECUTION_ENGINE

    merged.update({
        "schema_version": 2, "mode": mode, "current_level": current_level, "locked": locked,
        "caveman": caveman, "caveman_locked": bool(raw.get("caveman_locked", False)),
        "execution_engine": execution_engine, "suggested_profile": suggested_profile,
        "allow_pro": bool(merged.get("allow_pro", True)),
    })

    overrides = raw.get("model_overrides")
    merged["model_overrides"] = (
        {k: v for k, v in overrides.items() if k in MODEL_OVERRIDE_KEYS and isinstance(v, str) and v in VALID_MODEL_TIERS}
        if isinstance(overrides, dict)
        else {}
    )

    if isinstance(raw.get("signals"), dict):
        sig = dict(profile["signals"])
        for k in sig:
            if k in raw["signals"]:
                if k == "metrics":
                    raw_m = raw["signals"]["metrics"]
                    sig["metrics"] = dict(raw_m) if isinstance(raw_m, dict) else {"available": False, "sample_count": 0}
                else:
                    sig[k] = raw["signals"][k]
        merged["signals"] = sig

    if isinstance(raw.get("history"), list):
        merged["history"] = [h for h in raw["history"] if isinstance(h, dict)][-MAX_HISTORY:]

    for int_key in ("sessions_analyzed", "clean_streak"):
        try:
            merged[int_key] = max(0, int(merged.get(int_key) or 0))
        except (TypeError, ValueError):
            merged[int_key] = 0
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
            return _sanitize_profile(json.loads(profile_path.read_text(encoding="utf-8")))
        except Exception:
            return default_profile()
    ws_root = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
    legacy_ws_profile = ws_root / PROFILE_REL_PATH
    if legacy_ws_profile.is_file():
        try:
            return _sanitize_profile(json.loads(legacy_ws_profile.read_text(encoding="utf-8")))
        except Exception:
            return default_profile()
    return default_profile()


def get_active_preset(
    workspace_root: Optional[Path] = None, *,
    level_override: Optional[str] = None, pace_override: Optional[str] = None,
    verification_override: Optional[str] = None, no_verify: bool = False,
    caveman_override: Optional[str] = None, execution_engine_override: Optional[str] = None,
    plan_model_override: Optional[str] = None, build_model_override: Optional[str] = None,
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
    for key, arg_val in (("plan_model", plan_model_override), ("build_model", build_model_override), ("review_model", review_model_override)):
        if isinstance(arg_val, str) and arg_val.strip() in VALID_MODEL_TIERS:
            preset[key] = arg_val.strip()
    if not profile.get("allow_pro", True):
        for key in MODEL_OVERRIDE_KEYS:
            if preset.get(key) == "pro":
                preset[key] = "flash"

    skip_verification = bool(no_verify) or (str(verification_override or "").strip().lower() == "unsafe")
    preset["skip_verification"] = skip_verification
    if skip_verification:
        preset["max_remediation_rounds"] = 0

    chosen_caveman = str(caveman_override or "").strip().lower()
    caveman = chosen_caveman if chosen_caveman in VALID_CAVEMAN_LEVELS else profile.get("caveman", DEFAULT_CAVEMAN)
    preset["caveman"] = caveman
    preset["caveman_planner"] = effective_planner_caveman(caveman)
    preset["caveman_evals"] = "off"

    execution_engine = profile.get("execution_engine", DEFAULT_EXECUTION_ENGINE)
    if execution_engine not in VALID_EXECUTION_ENGINES:
        execution_engine = DEFAULT_EXECUTION_ENGINE
    if execution_engine_override is not None and str(execution_engine_override).strip():
        try:
            execution_engine = normalize_execution_engine(str(execution_engine_override))
        except ValueError:
            pass
    preset["execution_engine"] = execution_engine
    preset["mode"] = profile["mode"]
    preset["level"] = level
    preset["locked"] = bool(profile["locked"])
    preset["suggested_profile"] = profile.get("suggested_profile", DEFAULT_LEVEL)
    preset["last_reason"] = profile.get("last_reason", "")
    return preset


def role_models_from_preset(
    preset: Dict[str, Any], *, skip_planner: Optional[bool] = None, skip_review: Optional[bool] = None,
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
    return f"evo:{level}" if mode == "evo" else f"{level}[lock]"


def format_caveman_badge(profile: Dict[str, Any]) -> str:
    """Formate le badge compact du réglage Caveman (`caveman:lite` ou `caveman:full[lock]`)."""
    caveman = str(profile.get("caveman", DEFAULT_CAVEMAN)).strip().lower()
    if caveman not in VALID_CAVEMAN_LEVELS:
        caveman = DEFAULT_CAVEMAN
    return f"caveman:{caveman}[lock]" if bool(profile.get("caveman_locked", False)) else f"caveman:{caveman}"


def format_metrics_line(profile: Dict[str, Any]) -> str:
    """Formate la ligne synthétique Metrics si `signals.metrics.available` vaut `True`, sinon `""`."""
    signals = profile.get("signals") if isinstance(profile, dict) else None
    if not isinstance(signals, dict):
        return ""
    metrics = signals.get("metrics")
    if not isinstance(metrics, dict) or not metrics.get("available"):
        return ""
    try:
        n = int(metrics.get("sample_count") or 0)
        cache = float(metrics.get("avg_cache_hit_rate_pct") or 0.0)
        err = float(metrics.get("avg_error_rate_pct") or 0.0)
    except (TypeError, ValueError):
        return ""
    delta, _ = compute_metrics_delta(signals)
    delta_str = f"{delta:+d}" if delta != 0 else "0"
    return f"Signaux Metrics ({n} sess.) : cache {cache:.0f} %, err {err:.1f} %, delta {delta_str} pts"


def format_status(profile: Dict[str, Any]) -> str:
    """Formate le bilan statut lisible (incluant la ligne Metrics si disponible)."""
    engine = profile.get("execution_engine", DEFAULT_EXECUTION_ENGINE)
    base = (
        f"Mode: {format_mode_badge(profile)} | {format_caveman_badge(profile)} | "
        f"Engine: {engine} | Suggestion: {profile.get('suggested_profile', DEFAULT_LEVEL)} | "
        f"{profile.get('last_reason', '')}"
    )
    metrics_line = format_metrics_line(profile)
    return f"{base}\n{metrics_line}" if metrics_line else base


def _sync_live_state_safe(workspace_root: Path, profile: Dict[str, Any], *, event: Optional[Dict[str, Any]] = None) -> None:
    try:
        preset = get_active_preset(workspace_root)
        update_live_state(
            mode=profile["mode"], level=profile["current_level"], preset_profile=profile["current_level"],
            suggested_profile=profile.get("suggested_profile", DEFAULT_LEVEL),
            suggested_reason=profile.get("last_reason", ""), role_models=role_models_from_preset(preset),
            caveman=profile.get("caveman", DEFAULT_CAVEMAN), caveman_locked=bool(profile.get("caveman_locked", False)),
            event=event, workspace_root=workspace_root,
        )
    except Exception:
        pass


def _apply_model_override(overrides: Dict[str, str], key: str, raw_value: Optional[str]) -> Tuple[bool, Optional[str]]:
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


def _build_update_reason(
    profile: Dict[str, Any], *, target_mode: Optional[str], target_caveman: Optional[str],
    target_engine: Optional[str], override_notes: List[str],
    deprecated_pace: Optional[str], raw_verification: Optional[str],
) -> str:
    fragments: List[str] = []
    if target_mode is not None:
        fragments.append(
            f"mode évolutif consultatif (actif : {profile['current_level']})"
            if target_mode == "evo" else f"verrouillé en {target_mode}"
        )
    if target_caveman is not None:
        fragments.append(
            f"caveman auto ({profile['caveman']})" if target_caveman == "auto" else f"caveman verrouillé en {target_caveman}"
        )
    if target_engine is not None:
        fragments.append(f"moteur {target_engine}")
    if override_notes:
        fragments.append("modèles A/B : " + ", ".join(override_notes))
    if deprecated_pace is not None:
        fragments.append(f"alias historique '{deprecated_pace}' ignoré")
    if raw_verification is not None:
        fragments.append("vérifications actives (safe)")
    if fragments:
        joined = " · ".join(fragments)
        return joined[0].upper() + joined[1:] + "."
    return f"Option allow_pro mise à jour ({profile['allow_pro']})."


def update_profile_settings(
    workspace_root: Path, *,
    raw_mode: Optional[str] = None, raw_pace: Optional[str] = None,
    raw_verification: Optional[str] = None, raw_caveman: Optional[str] = None,
    execution_engine: Optional[str] = None, plan_model: Optional[str] = None,
    build_model: Optional[str] = None, review_model: Optional[str] = None,
    model_overrides: Optional[Dict[str, Optional[str]]] = None,
    reason: Optional[str] = None, allow_pro: Optional[bool] = None, sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour atomiquement le profil centralisé (`mode`, `caveman`, `execution_engine`, `allow_pro`, `model_overrides`)."""
    ws_root = Path(workspace_root).resolve()
    target_mode = normalize_mode(raw_mode) if raw_mode is not None else None
    deprecated_pace = normalize_pace(raw_pace) if raw_pace is not None else None
    if raw_verification is not None and normalize_verification(raw_verification) == "unsafe":
        raise ValueError(
            "Le mode 'unsafe' / '--no-verify' est strictement éphémère (1 tâche ou 1 invocation run_workflow) et n'est jamais persisté dans profile.json."
        )
    target_caveman = normalize_caveman(raw_caveman) if raw_caveman is not None else None
    target_engine = normalize_execution_engine(execution_engine) if execution_engine is not None else None
    has_model_update = plan_model is not None or build_model is not None or review_model is not None or bool(model_overrides)
    if all(x is None for x in (target_mode, deprecated_pace, raw_verification, target_caveman, target_engine, allow_pro)) and not has_model_update:
        raise ValueError(
            "Au moins un paramètre (`mode`, `caveman`, `execution_engine`, `allow_pro` ou surcharge modèle) est requis."
        )

    profile_path = resolve_profile_path(ws_root)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with exclusive_file_lock(profile_path.with_suffix(".lock")):
        profile = load_profile(ws_root)
        prev_mode, prev_level = profile["mode"], profile["current_level"]
        prev_caveman, prev_caveman_locked = profile["caveman"], profile["caveman_locked"]
        prev_engine = profile.get("execution_engine", DEFAULT_EXECUTION_ENGINE)
        prev_overrides = dict(profile.get("model_overrides") or {})

        if allow_pro is not None:
            profile["allow_pro"] = bool(allow_pro)
        if target_engine is not None:
            profile["execution_engine"] = target_engine
        if target_mode is not None:
            if target_mode == "evo":
                profile["mode"], profile["locked"] = "evo", False
                if profile["current_level"] not in LEVELS:
                    profile["current_level"] = DEFAULT_LEVEL
            else:
                profile["mode"], profile["current_level"], profile["locked"] = target_mode, target_mode, True
        if target_caveman is not None:
            if target_caveman == "auto":
                profile["caveman_locked"] = False
                profile["caveman"], _ = choose_evo_caveman(
                    profile["current_level"], profile.get("signals") or {}, int(profile.get("clean_streak") or 0)
                )
            else:
                profile["caveman"], profile["caveman_locked"] = target_caveman, True

        override_notes: List[str] = []
        new_overrides = dict(prev_overrides)
        for key, val in (("plan_model", plan_model), ("build_model", build_model), ("review_model", review_model)):
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
        final_reason = reason or _build_update_reason(
            profile, target_mode=target_mode, target_caveman=target_caveman, target_engine=target_engine,
            override_notes=override_notes, deprecated_pace=deprecated_pace, raw_verification=raw_verification,
        )
        profile["workspace"] = str(ws_root)
        profile["last_reason"] = final_reason
        profile["last_updated"] = now_iso

        changed = (
            prev_mode != profile["mode"] or prev_level != profile["current_level"]
            or prev_caveman != profile["caveman"] or prev_caveman_locked != profile["caveman_locked"]
            or prev_engine != profile.get("execution_engine", DEFAULT_EXECUTION_ENGINE)
            or prev_overrides != profile["model_overrides"] or not profile_path.is_file()
        )
        if changed:
            history = list(profile.get("history") or [])
            history.append({
                "timestamp": now_iso, "event": "manual_set", "from_mode": prev_mode, "to_mode": profile["mode"],
                "from_level": prev_level, "to_level": profile["current_level"], "from_caveman": prev_caveman,
                "to_caveman": profile["caveman"], "caveman_locked": profile["caveman_locked"],
                "model_overrides": dict(profile["model_overrides"]), "score": profile.get("last_score", 50),
                "reason": final_reason,
            })
            profile["history"] = history[-MAX_HISTORY:]
        _atomic_write_json(profile_path, profile)

    if sync_live:
        _sync_live_state_safe(ws_root, profile, event={"type": "PROFILE", "decision": "allow", "message": final_reason})
    return profile


def set_mode(
    workspace_root: Path, raw_mode: str, *, raw_pace: Optional[str] = None,
    raw_verification: Optional[str] = None, reason: Optional[str] = None,
    allow_pro: Optional[bool] = None, sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour et persiste le mode Gravity (`quick`/`standard`/`deep` verrouille, `evo` déverrouille)."""
    return update_profile_settings(
        workspace_root, raw_mode=raw_mode, raw_pace=raw_pace, raw_verification=raw_verification,
        reason=reason, allow_pro=allow_pro, sync_live=sync_live,
    )


def set_caveman(
    workspace_root: Path, raw_caveman: str, *, reason: Optional[str] = None, sync_live: bool = True,
) -> Dict[str, Any]:
    """Met à jour et persiste le réglage Caveman (`off`/`lite`/`full`/`ultra` verrouille, `auto`/`evo` déverrouille)."""
    return update_profile_settings(workspace_root, raw_caveman=raw_caveman, reason=reason, sync_live=sync_live)


def _parse_bool(raw: str) -> bool:
    val = raw.strip().lower()
    if val in ("true", "1", "yes", "oui"):
        return True
    if val in ("false", "0", "no", "non"):
        return False
    raise argparse.ArgumentTypeError(f"Booléen invalide : {raw!r}")


def _configure_utf8_stdio() -> None:
    configure_utf8_stdio()


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Gestionnaire de profil et mode Gravity")
    sub = parser.add_subparsers(dest="command", required=True)

    p_get = sub.add_parser("get", help="Lit le profil et le preset actif.")
    p_get.add_argument("--workspace", type=Path, default=Path("."))
    p_get.add_argument("--json", action="store_true")

    p_set = sub.add_parser("set", help="Définit le mode, `caveman`, `--engine` et/ou les surcharges modèles A/B.")
    p_set.add_argument("--workspace", type=Path, default=Path("."))
    p_set.add_argument("--mode", default=None)
    p_set.add_argument("--pace", default=None, help="Alias historique toléré (`slow`/`fast`, sans effet).")
    p_set.add_argument("--verify", "--verification", dest="verification", default=None)
    p_set.add_argument("--caveman", default=None, help="Niveau Caveman : auto|evo|off|lite|full|ultra.")
    p_set.add_argument("--engine", default=None, help="Moteur d'exécution par défaut : swarm|run_workflow.")
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
        payload = {
            "profile": profile, "preset": get_active_preset(ws),
            "badge": format_mode_badge(profile), "caveman_badge": format_caveman_badge(profile),
            "metrics_status": format_metrics_line(profile), "storage_path": str(resolve_profile_path(ws)),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else format_status(profile))
        return 0

    if args.command == "set":
        set_attrs = ("mode", "pace", "verification", "caveman", "engine", "plan_model", "build_model", "review_model", "allow_pro")
        if all(getattr(args, attr) is None for attr in set_attrs):
            print("[ERROR] Précisez au moins --mode, --caveman, --engine, --plan-model, --build-model, --review-model ou --allow-pro.", file=sys.stderr)
            return 1
        try:
            profile = update_profile_settings(
                ws, raw_mode=args.mode, raw_pace=args.pace, raw_verification=args.verification,
                raw_caveman=args.caveman, execution_engine=args.engine, plan_model=args.plan_model,
                build_model=args.build_model, review_model=args.review_model, reason=args.reason, allow_pro=args.allow_pro,
            )
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1
        payload = {"profile": profile, "preset": get_active_preset(ws), "badge": format_mode_badge(profile), "caveman_badge": format_caveman_badge(profile)}
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else f"{format_mode_badge(profile)} · {format_caveman_badge(profile)} — {profile['last_reason']}")
        return 0

    if args.command == "evolve":
        stop_entry: Dict[str, Any] = {"fully_idle": True, **({"conversation_id": args.conversation_id} if args.conversation_id else {})}
        profile = evolve_on_stop(ws, stop_entry)
        payload = {"profile": profile, "badge": format_mode_badge(profile), "caveman_badge": format_caveman_badge(profile)}
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else f"{format_mode_badge(profile)} · {format_caveman_badge(profile)} — {profile['last_reason']}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
