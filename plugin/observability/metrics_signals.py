#!/usr/bin/env python3
"""Adaptateur lecture seule des signaux `gravity-metrics` (`SPI-3` via `_shared.signal_bus`, 100 % stdlib)."""

import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from _shared.signal_bus import list_topic_signals, read_signal  # noqa: E402

SESSION_METRICS_TOPIC = "session-metrics"
DEFAULT_WINDOW_SIZE = 5
MIN_SAMPLE_COUNT = 2
_SLUG_NORM_RE = re.compile(r"[^a-z0-9]+")


def _safe_float(val: Any, default: float = 0.0) -> float:
    if isinstance(val, bool) or val is None:
        return default
    try:
        num = float(val)
    except (TypeError, ValueError):
        return default
    return num if math.isfinite(num) else default


def _safe_int(val: Any, default: int = 0) -> int:
    if isinstance(val, bool) or val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _normalize_signal_slug(raw: str) -> str:
    """Normalise un identifiant de projet en slug kebab-case compatible `signal_bus.SLUG_RE`."""
    cleaned = _SLUG_NORM_RE.sub("-", str(raw or "").strip().lower()).strip("-")
    return cleaned or "default"


def _norm_path_str(raw: str) -> str:
    return os.path.normcase(raw.strip()).replace("\\", "/").rstrip("/").lower()


def _matches_workspace_payload(payload: Dict[str, Any], ws_resolved: Path) -> bool:
    """Vérifie si le payload `signal_v1` cible le workspace donné."""
    ws_name_lc = ws_resolved.name.strip().lower()
    ws_norm = _norm_path_str(str(ws_resolved))
    ws_slug = _normalize_signal_slug(ws_resolved.name)

    for key in ("workspace", "workspace_path", "canonical_path", "cwd", "project"):
        raw_val = payload.get(key)
        if not isinstance(raw_val, str) or not raw_val.strip():
            continue
        candidate = raw_val.strip()
        cand_lc = candidate.lower()
        cand_norm = _norm_path_str(candidate)
        if (
            cand_lc == ws_name_lc
            or cand_norm == ws_name_lc
            or cand_norm == ws_norm
            or _normalize_signal_slug(candidate) == ws_slug
        ):
            return True
        try:
            cand_path = Path(candidate).expanduser()
            if cand_path.is_absolute() and _norm_path_str(str(cand_path.resolve())) == ws_norm:
                return True
        except OSError:
            pass
    return False


def _select_signal_envelope(
    ws_resolved: Path,
    *,
    gravity_home: Optional[Path] = None,
    slug: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if slug is not None:
        return read_signal(
            SESSION_METRICS_TOPIC,
            _normalize_signal_slug(slug),
            gravity_home=gravity_home,
        )

    ws_slug = _normalize_signal_slug(ws_resolved.name)
    direct = read_signal(SESSION_METRICS_TOPIC, ws_slug, gravity_home=gravity_home)
    if direct is not None:
        return direct

    envelopes: List[Dict[str, Any]] = list_topic_signals(
        SESSION_METRICS_TOPIC,
        gravity_home=gravity_home,
    )
    matched = [
        env
        for env in envelopes
        if _matches_workspace_payload(env.get("payload") or {}, ws_resolved)
    ]
    if not matched:
        return None
    matched.sort(key=lambda env: str(env.get("emitted_at") or ""))
    return matched[-1]


def collect_metrics_signals(
    workspace_root: Optional[Path] = None,
    *,
    gravity_home: Optional[Path] = None,
    slug: Optional[str] = None,
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> Dict[str, Any]:
    """Collecte en lecture seule les signaux `signal_v1` via `_shared.signal_bus` (`SPI-3`)."""
    _ = window_size
    fallback: Dict[str, Any] = {"available": False, "sample_count": 0}
    try:
        ws_resolved = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
        envelope = _select_signal_envelope(
            ws_resolved,
            gravity_home=gravity_home,
            slug=slug,
        )
        if not isinstance(envelope, dict):
            return fallback

        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            return fallback

        sample_count = max(
            0,
            _safe_int(envelope.get("sample_count"), _safe_int(payload.get("sample_count"), 0)),
        )
        if payload.get("available") is False or sample_count < MIN_SAMPLE_COUNT:
            return {"available": False, "sample_count": sample_count}

        avg_error_rate_pct = round(max(0.0, _safe_float(payload.get("avg_error_rate_pct"), 0.0)), 2)
        avg_cache_hit_rate_pct = round(
            max(0.0, _safe_float(payload.get("avg_cache_hit_rate_pct"), 0.0)),
            2,
        )
        max_context_tokens = max(0, _safe_int(payload.get("max_context_tokens"), 0))
        recent_duplicate_spawns = max(0, _safe_int(payload.get("recent_duplicate_spawns"), 0))
        recent_rewind_calls = max(0, _safe_int(payload.get("recent_rewind_calls"), 0))
        recent_interrupted_agents = max(0, _safe_int(payload.get("recent_interrupted_agents"), 0))
        # Compatibilité API conservée à 0 (`SPI-3`).
        recent_bridge_sessions = 0
        high_output_cost_ratio = round(
            max(0.0, min(1.0, _safe_float(payload.get("high_output_cost_ratio"), 0.0))),
            4,
        )

        return {
            "available": True,
            "sample_count": sample_count,
            "avg_error_rate_pct": avg_error_rate_pct,
            "avg_cache_hit_rate_pct": avg_cache_hit_rate_pct,
            "max_context_tokens": max_context_tokens,
            "recent_duplicate_spawns": recent_duplicate_spawns,
            "recent_rewind_calls": recent_rewind_calls,
            "recent_interrupted_agents": recent_interrupted_agents,
            "recent_bridge_sessions": recent_bridge_sessions,
            "high_output_cost_ratio": high_output_cost_ratio,
        }
    except Exception:
        return fallback
