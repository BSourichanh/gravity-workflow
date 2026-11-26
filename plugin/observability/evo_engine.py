#!/usr/bin/env python3
"""Moteur consultatif `evo` Gravity : collecte des signaux, scoring et réévaluation Stop (100 % stdlib)."""

import datetime
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OBS_DIR = Path(__file__).resolve().parent
if str(OBS_DIR) not in sys.path:
    sys.path.insert(0, str(OBS_DIR))

from caveman_protocol import choose_evo_caveman  # noqa: E402
from metrics_signals import collect_metrics_signals  # noqa: E402
from state_writer import (  # noqa: E402
    _atomic_write_json,
    exclusive_file_lock,
    resolve_eval_dir,
    resolve_runs_log_path,
    resolve_state_path,
)

DEFAULT_LEVEL = "standard"
MAX_HISTORY = 20
LIVE_STATE_REL_PATH = Path(".agents/gravity-workflow/observability/live_state.json")
EVAL_REPORT_REL_PATH = Path(".agents/gravity-workflow/evals/report.json")
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

    try:
        metrics_signals = collect_metrics_signals(ws_root)
    except Exception:
        metrics_signals = {"available": False, "sample_count": 0}

    return {
        "reviewer_rejections_recent": reviewer_rejections_recent,
        "tool_or_test_errors_recent": tool_or_test_errors_recent,
        "guard_deny_recent": guard_deny_recent,
        "guard_ask_recent": guard_ask_recent,
        "eval_pass_rate": eval_pass_rate,
        "session_error": session_error,
        "metrics": metrics_signals,
    }


def _has_behavioral_friction(signals: Dict[str, Any]) -> bool:
    rev_rej = int(signals.get("reviewer_rejections_recent") or 0)
    tool_errs = int(signals.get("tool_or_test_errors_recent") or 0)
    session_err = bool(signals.get("session_error"))
    deny_cnt = int(signals.get("guard_deny_recent") or 0)
    eval_rate = signals.get("eval_pass_rate")
    eval_failing = eval_rate is not None and float(eval_rate) < 1.0
    return bool(rev_rej > 0 or tool_errs > 0 or session_err or deny_cnt > 0 or eval_failing)


def compute_metrics_delta(
    signals: Dict[str, Any],
    *,
    has_friction: Optional[bool] = None,
) -> Tuple[int, List[str]]:
    """Calcule l'ajustement consultatif `metrics` strictement borné dans `[-5, +10]`."""
    sig = signals if isinstance(signals, dict) else {}
    metrics = sig.get("metrics")
    if not isinstance(metrics, dict) or not metrics.get("available"):
        return 0, []

    if has_friction is None:
        has_friction = _has_behavioral_friction(sig)

    try:
        sample_count = int(metrics.get("sample_count") or 0)
        avg_err = float(metrics.get("avg_error_rate_pct") or 0.0)
        avg_cache = float(metrics.get("avg_cache_hit_rate_pct") or 0.0)
        max_ctx = int(metrics.get("max_context_tokens") or 0)
        dup_spawns = int(metrics.get("recent_duplicate_spawns") or 0)
        rewinds = int(metrics.get("recent_rewind_calls") or 0)
    except (TypeError, ValueError):
        return 0, []

    delta = 0
    m_factors: List[str] = []
    if avg_err >= 15.0 or rewinds >= 3:
        delta += 5
        m_factors.append("metrics: erreurs/rewinds")
    if dup_spawns >= 2 or max_ctx >= 180_000:
        delta += 5
        m_factors.append("metrics: bloat/spawns")

    if (
        delta == 0
        and not has_friction
        and sample_count >= 3
        and avg_err <= 3.0
        and avg_cache >= 75.0
        and dup_spawns == 0
        and rewinds == 0
    ):
        delta = -5
        m_factors.append("metrics: cache/stabilité")

    return max(-5, min(10, delta)), m_factors


def compute_score(signals: Dict[str, Any], clean_streak: int) -> Tuple[int, str, List[str]]:
    """Évalue la recommandation consultative `evo` (`standard` | `deep`) à partir des signaux comportementaux et Metrics."""
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
    metrics_delta, metrics_factors = compute_metrics_delta(signals, has_friction=has_friction)
    score += metrics_delta
    factors.extend(metrics_factors)

    if not has_friction and clean_streak >= 2:
        score -= 10
        factors.append(f"stabilité {clean_streak} sessions")
    elif not has_friction and not factors:
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
    import profile_engine  # noqa: E402

    ws_root = Path(workspace_root).resolve()
    profile_path = profile_engine.resolve_profile_path(ws_root)

    if isinstance(stop_entry, dict) and stop_entry.get("fully_idle") is False:
        return profile_engine.load_profile(ws_root)

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
        profile = profile_engine.load_profile(ws_root)
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

        profile["workspace"] = str(ws_root)
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
            reason_text = f"Suggestion evo : {prev_suggested} → {suggested_profile} ({factors_str})."
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
            profile["last_reason"] = f"Profil verrouillé : {profile['current_level']} · {reason_text}"
        else:
            profile["last_reason"] = f"Profil actif : {profile['current_level']} · {reason_text}"

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
        profile_engine._sync_live_state_safe(ws_root, profile, event=ev)

    return profile
