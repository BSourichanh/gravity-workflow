#!/usr/bin/env python3
"""Adaptateur lecture seule des signaux `gravity-metrics` (`history.jsonl`, 100 % stdlib, fail-safe)."""

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_SCAN_BYTES = 512 * 1024
MAX_TAIL_LINES = 200
DEFAULT_WINDOW_SIZE = 5
MIN_SAMPLE_COUNT = 2


def resolve_history_path(history_path: Optional[Path] = None) -> Path:
    """Résout `history.jsonl` : arg > $GRAVITY_METRICS_HOME > $GRAVITY_HOME/metrics > ~/.gemini/gravity/metrics."""
    if history_path is not None:
        return Path(history_path).expanduser().resolve()
    env_metrics = os.environ.get("GRAVITY_METRICS_HOME", "").strip()
    if env_metrics:
        return (Path(env_metrics).expanduser().resolve() / "data" / "history.jsonl").resolve()
    env_gravity = os.environ.get("GRAVITY_HOME", "").strip()
    if env_gravity:
        return (Path(env_gravity).expanduser().resolve() / "metrics" / "data" / "history.jsonl").resolve()
    return (Path.home() / ".gemini" / "gravity" / "metrics" / "data" / "history.jsonl").resolve()


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


def _read_tail_lines(path: Path, max_bytes: int = MAX_SCAN_BYTES, max_lines: int = MAX_TAIL_LINES) -> List[str]:
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        if size <= 0:
            return []
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                raw = fh.read(max_bytes)
                nl_idx = raw.find(b"\n")
                raw = raw[nl_idx + 1 :] if nl_idx != -1 else b""
            else:
                raw = fh.read()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return lines[-max_lines:] if max_lines > 0 else lines
    except OSError:
        return []


def _norm_path_str(raw: str) -> str:
    return os.path.normcase(raw.strip()).replace("\\", "/").rstrip("/").lower()


def _matches_workspace(rec: Dict[str, Any], ws_resolved: Path) -> bool:
    raw_proj = str(rec.get("project") or "").strip()
    if not raw_proj:
        return False

    ws_name_lc = ws_resolved.name.strip().lower()
    ws_norm = _norm_path_str(str(ws_resolved))
    proj_lc = raw_proj.lower()
    proj_norm = _norm_path_str(raw_proj)

    if proj_lc == ws_name_lc or proj_norm == ws_name_lc or proj_norm == ws_norm:
        return True

    try:
        proj_path = Path(raw_proj).expanduser()
        if proj_path.is_absolute() and _norm_path_str(str(proj_path.resolve())) == ws_norm:
            return True
    except OSError:
        pass
    return False


def _record_sort_key(rec: Dict[str, Any], idx: int) -> Tuple[str, int]:
    for key in ("ended_at", "archived_at", "added_at", "timestamp"):
        val = rec.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip(), idx
    return "", idx


def collect_metrics_signals(
    workspace_root: Optional[Path] = None,
    *,
    history_path: Optional[Path] = None,
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> Dict[str, Any]:
    """Collecte en lecture seule les signaux agrégés des `window_size` dernières sessions archivées du projet."""
    fallback: Dict[str, Any] = {"available": False, "sample_count": 0}
    try:
        ws_resolved = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
        target = resolve_history_path(history_path)
        lines = _read_tail_lines(target)
        if not lines:
            return fallback

        matched: List[Tuple[Tuple[str, int], Dict[str, Any]]] = []
        for idx, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue

            status = str(obj.get("status") or "").strip().lower()
            if status == "active" or (status and status != "archived"):
                continue
            if not _matches_workspace(obj, ws_resolved):
                continue

            matched.append((_record_sort_key(obj, idx), obj))

        if not matched:
            return fallback

        matched.sort(key=lambda item: item[0])
        limit = max(1, int(window_size))
        selected = [item[1] for item in matched[-limit:]]
        sample_count = len(selected)
        if sample_count < MIN_SAMPLE_COUNT:
            return {"available": False, "sample_count": sample_count}

        avg_error_rate_pct = round(
            sum(max(0.0, _safe_float(r.get("error_rate_pct"), 0.0)) for r in selected) / sample_count,
            2,
        )
        avg_cache_hit_rate_pct = round(
            sum(max(0.0, _safe_float(r.get("cache_hit_rate_pct"), 0.0)) for r in selected) / sample_count,
            2,
        )
        max_context_tokens = max(
            max(0, _safe_int(r.get("max_context_tokens"), 0)) for r in selected
        )
        recent_duplicate_spawns = sum(
            max(0, _safe_int(r.get("duplicate_spawns"), 0)) for r in selected
        )
        recent_rewind_calls = sum(
            max(0, _safe_int(r.get("rewind_calls"), 0)) for r in selected
        )
        recent_interrupted_agents = sum(
            max(0, _safe_int(r.get("interrupted_agents"), 0)) for r in selected
        )
        recent_bridge_sessions = sum(
            1 for r in selected if _safe_int(r.get("bridge_children_count"), 0) > 0
        )

        total_cost_usd = sum(max(0.0, _safe_float(r.get("cost_usd"), 0.0)) for r in selected)
        total_output_usd = sum(max(0.0, _safe_float(r.get("cost_output_usd"), 0.0)) for r in selected)
        if total_cost_usd > 0.0:
            ratio = max(0.0, min(1.0, total_output_usd / total_cost_usd))
            high_output_cost_ratio = round(ratio, 4)
        else:
            high_output_cost_ratio = 0.0

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
