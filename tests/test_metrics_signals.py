#!/usr/bin/env python3
"""Tests unitaires isolés pour `plugin/observability/metrics_signals.py` (100 % stdlib)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
OBS_DIR = REPO_ROOT / "plugin" / "observability"
if str(OBS_DIR) not in sys.path:
    sys.path.insert(0, str(OBS_DIR))

import caveman_protocol  # noqa: E402
import metrics_signals  # noqa: E402
import profile_engine  # noqa: E402


class TestMetricsSignals(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._base = Path(self._tmp.name)
        self._env_keys = (
            "GRAVITY_METRICS_HOME",
            "GRAVITY_WORKFLOW_HOME",
            "GRAVITY_HOME",
            "GRAVITY_WORKFLOW_SKIP_REGISTRY",
            "GRAVITY_WORKFLOW_STATE_PATH",
        )
        self._saved_env = {k: os.environ.get(k) for k in self._env_keys}
        os.environ["GRAVITY_WORKFLOW_HOME"] = str(self._base / "wf_home")
        os.environ["GRAVITY_METRICS_HOME"] = str(self._base / "metrics_home")
        os.environ["GRAVITY_WORKFLOW_SKIP_REGISTRY"] = "1"
        os.environ.pop("GRAVITY_HOME", None)
        os.environ.pop("GRAVITY_WORKFLOW_STATE_PATH", None)

    def tearDown(self) -> None:
        for key, val in self._saved_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    def test_resolve_history_path_precedence(self) -> None:
        fake_home = self._base / "home"
        explicit = self._base / "custom" / "history.jsonl"
        self.assertEqual(metrics_signals.resolve_history_path(explicit), explicit.resolve())

        saved_metrics = os.environ.pop("GRAVITY_METRICS_HOME", None)
        try:
            with mock.patch("pathlib.Path.home", return_value=fake_home):
                self.assertEqual(
                    metrics_signals.resolve_history_path(),
                    (fake_home / ".gemini" / "gravity" / "metrics" / "data" / "history.jsonl").resolve(),
                )
                os.environ["GRAVITY_HOME"] = str(self._base / "g_home")
                self.assertEqual(
                    metrics_signals.resolve_history_path(),
                    (self._base / "g_home" / "metrics" / "data" / "history.jsonl").resolve(),
                )
                os.environ["GRAVITY_METRICS_HOME"] = str(self._base / "m_home")
                self.assertEqual(
                    metrics_signals.resolve_history_path(),
                    (self._base / "m_home" / "data" / "history.jsonl").resolve(),
                )
        finally:
            if saved_metrics is not None:
                os.environ["GRAVITY_METRICS_HOME"] = saved_metrics

    def test_missing_empty_malformed_and_oversized_history(self) -> None:
        ws = self._base / "My-Project"
        ws.mkdir()

        missing = self._base / "missing.jsonl"
        self.assertEqual(
            metrics_signals.collect_metrics_signals(ws, history_path=missing),
            {"available": False, "sample_count": 0},
        )

        empty = self._base / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        self.assertEqual(
            metrics_signals.collect_metrics_signals(ws, history_path=empty),
            {"available": False, "sample_count": 0},
        )

        # Fichier volumineux (> MAX_SCAN_BYTES) avec lignes malformées et non-dict en fin de fichier
        large = self._base / "large.jsonl"
        filler_rec = json.dumps(
            {
                "status": "archived",
                "project": "other-repo",
                "ended_at": "2026-11-01T00:00:00Z",
                "note": "x" * 1024,
            }
        )
        valid_tail = [
            "{broken_json",
            json.dumps(["not", "a", "dict"]),
            json.dumps(
                {
                    "status": "archived",
                    "project": "my-project",
                    "ended_at": "2026-11-22T10:00:00Z",
                    "error_rate_pct": 2.0,
                    "cache_hit_rate_pct": 80.0,
                    "max_context_tokens": 90_000,
                    "duplicate_spawns": 0,
                    "rewind_calls": 0,
                    "interrupted_agents": 0,
                    "bridge_children_count": 0,
                    "cost_usd": 1.0,
                    "cost_output_usd": 0.2,
                }
            ),
            json.dumps(
                {
                    "status": "archived",
                    "project": "My-Project",
                    "ended_at": "2026-11-23T10:00:00Z",
                    "error_rate_pct": 4.0,
                    "cache_hit_rate_pct": 90.0,
                    "max_context_tokens": 110_000,
                    "duplicate_spawns": 1,
                    "rewind_calls": 1,
                    "interrupted_agents": 1,
                    "bridge_children_count": 2,
                    "cost_usd": 1.0,
                    "cost_output_usd": 0.4,
                }
            ),
        ]
        # > 600 Ko de lignes antérieures
        lines = [filler_rec] * 600 + valid_tail
        large.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertGreater(large.stat().st_size, metrics_signals.MAX_SCAN_BYTES)

        res = metrics_signals.collect_metrics_signals(ws, history_path=large)
        self.assertTrue(res["available"])
        self.assertEqual(res["sample_count"], 2)
        self.assertEqual(res["avg_error_rate_pct"], 3.0)
        self.assertEqual(res["avg_cache_hit_rate_pct"], 85.0)
        self.assertEqual(res["max_context_tokens"], 110_000)
        self.assertEqual(res["recent_duplicate_spawns"], 1)
        self.assertEqual(res["recent_rewind_calls"], 1)
        self.assertEqual(res["recent_interrupted_agents"], 1)
        self.assertEqual(res["recent_bridge_sessions"], 0)
        self.assertEqual(res["high_output_cost_ratio"], 0.3)

    def test_project_matching_active_exclusion_threshold_and_window_sort(self) -> None:
        ws = self._base / "Gravity-Workflow"
        ws.mkdir()
        hpath = self._base / "history.jsonl"

        records = [
            # Doit être exclu car status == "active"
            {
                "status": "active",
                "project": "gravity-workflow",
                "ended_at": "2026-11-23T23:59:59Z",
                "error_rate_pct": 99.0,
                "max_context_tokens": 999_999,
                "duplicate_spawns": 50,
            },
            # Doit être exclu car autre projet
            {
                "status": "archived",
                "project": "gravity-metrics",
                "ended_at": "2026-11-23T22:00:00Z",
                "error_rate_pct": 88.0,
            },
            # 1 seule session archivée -> sous le seuil min (< 2)
            {
                "status": "archived",
                "project": str(ws).upper(),
                "ended_at": "2026-11-23T12:00:00Z",
                "error_rate_pct": 1.0,
            },
        ]
        hpath.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        res_one = metrics_signals.collect_metrics_signals(ws, history_path=hpath)
        self.assertEqual(res_one, {"available": False, "sample_count": 1})

        # Ajouter 6 sessions (dont 1 sans status pour rétrocompatibilité) dans le désordre chronologique
        extra = [
            {
                "status": "",
                "project": "gravity-workflow",
                "added_at": f"2026-11-20T0{i}:00:00Z",
                "error_rate_pct": 50.0 if i == 0 else 2.0,
                "cache_hit_rate_pct": 80.0,
                "max_context_tokens": 50_000 + i * 10_000,
                "duplicate_spawns": 0,
                "rewind_calls": 0,
                "interrupted_agents": 0,
                "bridge_children_count": 0,
                "cost_usd": 0.0,
                "cost_output_usd": 5.0,
            }
            for i in range(6)
        ]
        # Garder l'entrée la plus ancienne avec erreur 50.0 hors de la fenêtre des 5 dernières
        hpath.write_text(
            "\n".join(json.dumps(r) for r in (records + extra[::-1])) + "\n",
            encoding="utf-8",
        )
        res_win = metrics_signals.collect_metrics_signals(ws, history_path=hpath, window_size=5)
        self.assertTrue(res_win["available"])
        self.assertEqual(res_win["sample_count"], 5)
        # Les 5 plus récentes : 12:00:00Z (err=1.0) + i=2,3,4,5 (err=2.0) -> moyenne 1.8
        self.assertEqual(res_win["avg_error_rate_pct"], 1.8)
        self.assertEqual(res_win["high_output_cost_ratio"], 0.0)

        # Ratio coût output borné à 1.0 si cost_output_usd > cost_usd
        clamped_recs = [
            {
                "status": "archived",
                "project": "gravity-workflow",
                "archived_at": "2026-11-23T10:00:00Z",
                "cost_usd": 1.0,
                "cost_output_usd": 1.5,
            },
            {
                "status": "archived",
                "project": "gravity-workflow",
                "archived_at": "2026-11-23T11:00:00Z",
                "cost_usd": 1.0,
                "cost_output_usd": 1.5,
            },
        ]
        hpath.write_text("\n".join(json.dumps(r) for r in clamped_recs) + "\n", encoding="utf-8")
        res_clamped = metrics_signals.collect_metrics_signals(ws, history_path=hpath)
        self.assertEqual(res_clamped["high_output_cost_ratio"], 1.0)

    def test_compute_score_bounds_and_caveman_adaptation(self) -> None:
        base_sig = {
            "reviewer_rejections_recent": 0,
            "tool_or_test_errors_recent": 0,
            "guard_deny_recent": 0,
            "guard_ask_recent": 0,
            "eval_pass_rate": 1.0,
            "session_error": False,
        }

        # Stabilité prouvée -> -5 (avec sample_count >= 3, pas de friction)
        sig_stable = {
            **base_sig,
            "metrics": {
                "available": True,
                "sample_count": 5,
                "avg_error_rate_pct": 2.1,
                "avg_cache_hit_rate_pct": 84.0,
                "max_context_tokens": 80_000,
                "recent_duplicate_spawns": 0,
                "recent_rewind_calls": 0,
            },
        }
        score_s, sugg_s, factors_s = profile_engine.compute_score(sig_stable, clean_streak=0)
        self.assertEqual(score_s, 45)
        self.assertEqual(sugg_s, "standard")
        self.assertIn("metrics: cache/stabilité", factors_s)

        # sample_count == 2 -> pas de bonus -5
        sig_two = dict(sig_stable, metrics={**sig_stable["metrics"], "sample_count": 2})
        score_two, _, factors_two = profile_engine.compute_score(sig_two, clean_streak=0)
        self.assertEqual(score_two, 50)
        self.assertNotIn("metrics: cache/stabilité", factors_two)

        # +10 max (erreurs/rewinds +5 et bloat/spawns +5)
        sig_hot = {
            **base_sig,
            "metrics": {
                "available": True,
                "sample_count": 4,
                "avg_error_rate_pct": 16.0,
                "avg_cache_hit_rate_pct": 90.0,
                "max_context_tokens": 185_000,
                "recent_duplicate_spawns": 0,
                "recent_rewind_calls": 0,
            },
        }
        score_h, sugg_h, factors_h = profile_engine.compute_score(sig_hot, clean_streak=0)
        self.assertEqual(score_h, 60)
        self.assertEqual(sugg_h, "standard")
        self.assertIn("metrics: erreurs/rewinds", factors_h)
        self.assertIn("metrics: bloat/spawns", factors_h)

        # Stabilité refusée si friction comportementale présente
        sig_friction = {**sig_stable, "guard_deny_recent": 1}
        score_f, sugg_f, factors_f = profile_engine.compute_score(sig_friction, clean_streak=5)
        self.assertEqual(score_f, 70)
        self.assertEqual(sugg_f, "deep")
        self.assertNotIn("metrics: cache/stabilité", factors_f)

        # Caveman evo : full si contexte ou ratio output élevé sain, mais priorité lite si erreur/deny/eval fail
        lvl_ctx, reason_ctx = caveman_protocol.choose_evo_caveman(
            "standard",
            {
                **base_sig,
                "metrics": {
                    "available": True,
                    "max_context_tokens": 150_000,
                    "high_output_cost_ratio": 0.1,
                },
            },
            0,
        )
        self.assertEqual(lvl_ctx, "full")
        self.assertEqual(reason_ctx, "caveman full (contexte/output élevé mesuré par metrics)")

        lvl_cost, _ = caveman_protocol.choose_evo_caveman(
            "quick",
            {
                **base_sig,
                "metrics": {
                    "available": True,
                    "max_context_tokens": 20_000,
                    "high_output_cost_ratio": 0.35,
                },
            },
            0,
        )
        self.assertEqual(lvl_cost, "full")

        for unsafe_override in (
            {"session_error": True},
            {"guard_deny_recent": 1},
            {"eval_pass_rate": 0.9},
        ):
            lvl_safe, _ = caveman_protocol.choose_evo_caveman(
                "deep",
                {
                    **base_sig,
                    **unsafe_override,
                    "metrics": {
                        "available": True,
                        "max_context_tokens": 220_000,
                        "high_output_cost_ratio": 0.9,
                    },
                },
                5,
            )
            self.assertEqual(lvl_safe, "lite")

    def test_evolve_on_stop_format_status_and_statusline(self) -> None:
        ws = self._base / "demo-ws"
        ws.mkdir()
        hfile = Path(os.environ["GRAVITY_METRICS_HOME"]) / "data" / "history.jsonl"
        hfile.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "status": "archived",
                "project": "demo-ws",
                "ended_at": f"2026-11-23T0{i}:00:00Z",
                "error_rate_pct": 2.1,
                "cache_hit_rate_pct": 84.0,
                "max_context_tokens": 160_000 if i == 4 else 90_000,
                "duplicate_spawns": 0,
                "rewind_calls": 0,
                "interrupted_agents": 0,
                "bridge_children_count": 0,
                "cost_usd": 1.0,
                "cost_output_usd": 0.2,
            }
            for i in range(5)
        ]
        hfile.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

        prof = profile_engine.evolve_on_stop(
            ws,
            {"conversation_id": "conv-metrics-1", "fully_idle": True, "termination_reason": "NO_TOOL_CALL"},
        )
        self.assertTrue(prof["signals"]["metrics"]["available"])
        self.assertEqual(prof["signals"]["metrics"]["sample_count"], 5)
        self.assertEqual(prof["last_score"], 45)
        self.assertEqual(prof["caveman"], "full")

        status_text = profile_engine.format_status(prof)
        self.assertIn("Signaux Metrics (5 sess.) : cache 84 %, err 2.1 %, delta -5 pts", status_text)

        proc_status = subprocess.run(
            [sys.executable, str(OBS_DIR / "statusline.py")],
            input=json.dumps({"workspace": {"project_dir": str(ws)}}),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(proc_status.returncode, 0)
        self.assertIn("Signaux Metrics (5 sess.) : cache 84 %, err 2.1 %, delta -5 pts", proc_status.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
