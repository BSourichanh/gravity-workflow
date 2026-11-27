#!/usr/bin/env python3
"""Tests unitaires isolés pour `plugin/observability/metrics_signals.py` (`SPI-3` `_shared.signal_bus`, 100 % stdlib)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugin"
OBS_DIR = PLUGIN_ROOT / "observability"
for _p in (str(PLUGIN_ROOT), str(OBS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _shared import signal_bus  # noqa: E402
import caveman_protocol  # noqa: E402
import evo_engine  # noqa: E402
import metrics_signals  # noqa: E402
import profile_engine  # noqa: E402


class TestMetricsSignals(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._base = Path(self._tmp.name)
        self._env_keys = (
            "GRAVITY_WORKFLOW_HOME",
            "GRAVITY_HOME",
            "GRAVITY_WORKFLOW_SKIP_REGISTRY",
            "GRAVITY_WORKFLOW_STATE_PATH",
        )
        self._saved_env = {k: os.environ.get(k) for k in self._env_keys}
        self._gravity_home = self._base / "gravity_home"
        os.environ["GRAVITY_HOME"] = str(self._gravity_home)
        os.environ["GRAVITY_WORKFLOW_HOME"] = str(self._base / "wf_home")
        os.environ["GRAVITY_WORKFLOW_SKIP_REGISTRY"] = "1"
        os.environ.pop("GRAVITY_WORKFLOW_STATE_PATH", None)

    def tearDown(self) -> None:
        for key, val in self._saved_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    def test_missing_invalid_symlink_and_min_sample_threshold(self) -> None:
        ws = self._base / "My-Project"
        ws.mkdir()

        # Aucun signal publié -> repli sûr
        self.assertEqual(
            metrics_signals.collect_metrics_signals(ws),
            {"available": False, "sample_count": 0},
        )

        # Fichier signal corrompu / mauvais schéma -> repli sûr
        topic_dir = self._gravity_home / "signals" / "session-metrics"
        topic_dir.mkdir(parents=True)
        (topic_dir / "my-project.json").write_text("{broken_json", encoding="utf-8")
        self.assertEqual(
            metrics_signals.collect_metrics_signals(ws),
            {"available": False, "sample_count": 0},
        )

        (topic_dir / "my-project.json").write_text(
            json.dumps({"schema_version": 99, "topic": "session-metrics", "payload": {}}),
            encoding="utf-8",
        )
        self.assertEqual(
            metrics_signals.collect_metrics_signals(ws),
            {"available": False, "sample_count": 0},
        )

        # Sous le seuil min (< 2 échantillons) -> available=False mais sample_count préservé
        signal_bus.publish_signal(
            "session-metrics",
            "my-project",
            "metrics",
            {"avg_error_rate_pct": 1.0},
            sample_count=1,
            window_size=5,
            gravity_home=self._gravity_home,
        )
        self.assertEqual(
            metrics_signals.collect_metrics_signals(ws),
            {"available": False, "sample_count": 1},
        )

        # Symlink sur signals/ rejeté fail-safe sans exception non gérée
        if hasattr(os, "symlink"):
            sym_home = self._base / "sym_home"
            sym_home.mkdir()
            real_sig = self._base / "real_sig"
            real_sig.mkdir()
            try:
                os.symlink(real_sig, sym_home / "signals")
            except OSError:
                pass
            else:
                self.assertEqual(
                    metrics_signals.collect_metrics_signals(ws, gravity_home=sym_home),
                    {"available": False, "sample_count": 0},
                )

    def test_spi3_payload_keys_slug_normalization_and_workspace_matching(self) -> None:
        ws = self._base / "My-Project"
        ws.mkdir()

        signal_bus.publish_signal(
            "session-metrics",
            "my-project",
            "metrics",
            {
                "avg_error_rate_pct": 3.0,
                "avg_cache_hit_rate_pct": 85.0,
                "max_context_tokens": 110_000,
                "recent_duplicate_spawns": 1,
                "recent_rewind_calls": 1,
                "recent_interrupted_agents": 1,
                "recent_bridge_sessions": 99,  # forcé à 0 par compatibilité API
                "high_output_cost_ratio": 1.5,  # borné à 1.0
            },
            sample_count=2,
            window_size=5,
            gravity_home=self._gravity_home,
        )

        res = metrics_signals.collect_metrics_signals(ws)
        self.assertTrue(res["available"])
        self.assertEqual(res["sample_count"], 2)
        self.assertEqual(res["avg_error_rate_pct"], 3.0)
        self.assertEqual(res["avg_cache_hit_rate_pct"], 85.0)
        self.assertEqual(res["max_context_tokens"], 110_000)
        self.assertEqual(res["recent_duplicate_spawns"], 1)
        self.assertEqual(res["recent_rewind_calls"], 1)
        self.assertEqual(res["recent_interrupted_agents"], 1)
        self.assertEqual(res["recent_bridge_sessions"], 0)
        self.assertEqual(res["high_output_cost_ratio"], 1.0)

        # Fallback list_topic_signals sur payload.workspace sans mélanger un autre projet
        ws_other = self._base / "Gravity_Workflow"
        ws_other.mkdir()
        signal_bus.publish_signal(
            "session-metrics",
            "gravity-metrics",
            "metrics",
            {
                "project": "gravity-metrics",
                "avg_error_rate_pct": 88.0,
            },
            sample_count=5,
            window_size=5,
            gravity_home=self._gravity_home,
        )
        self.assertEqual(
            metrics_signals.collect_metrics_signals(ws_other),
            {"available": False, "sample_count": 0},
        )

        signal_bus.publish_signal(
            "session-metrics",
            "custom-slug-1",
            "metrics",
            {
                "workspace": str(ws_other),
                "avg_error_rate_pct": 1.8,
                "avg_cache_hit_rate_pct": 80.0,
                "max_context_tokens": 100_000,
                "recent_duplicate_spawns": 0,
                "recent_rewind_calls": 0,
                "recent_interrupted_agents": 0,
                "recent_bridge_sessions": 0,
                "high_output_cost_ratio": 0.25,
            },
            sample_count=5,
            window_size=5,
            emitted_at="2026-11-26T10:00:00Z",
            gravity_home=self._gravity_home,
        )
        res_matched = metrics_signals.collect_metrics_signals(ws_other)
        self.assertTrue(res_matched["available"])
        self.assertEqual(res_matched["sample_count"], 5)
        self.assertEqual(res_matched["avg_error_rate_pct"], 1.8)
        self.assertEqual(res_matched["high_output_cost_ratio"], 0.25)

    def test_compute_score_bounds_and_caveman_adaptation(self) -> None:
        base_sig = {
            "reviewer_rejections_recent": 0,
            "tool_or_test_errors_recent": 0,
            "guard_deny_recent": 0,
            "guard_ask_recent": 0,
            "eval_pass_rate": 1.0,
            "session_error": False,
        }

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
        score_s, sugg_s, factors_s = evo_engine.compute_score(sig_stable, clean_streak=0)
        self.assertEqual(score_s, 45)
        self.assertEqual(sugg_s, "standard")
        self.assertIn("metrics: cache/stabilité", factors_s)

        sig_two = dict(sig_stable, metrics={**sig_stable["metrics"], "sample_count": 2})
        score_two, _, factors_two = profile_engine.compute_score(sig_two, clean_streak=0)
        self.assertEqual(score_two, 50)
        self.assertNotIn("metrics: cache/stabilité", factors_two)

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
        score_h, sugg_h, factors_h = evo_engine.compute_score(sig_hot, clean_streak=0)
        self.assertEqual(score_h, 60)
        self.assertEqual(sugg_h, "standard")
        self.assertIn("metrics: erreurs/rewinds", factors_h)
        self.assertIn("metrics: bloat/spawns", factors_h)

        sig_friction = {**sig_stable, "guard_deny_recent": 1}
        score_f, sugg_f, factors_f = profile_engine.compute_score(sig_friction, clean_streak=5)
        self.assertEqual(score_f, 70)
        self.assertEqual(sugg_f, "deep")
        self.assertNotIn("metrics: cache/stabilité", factors_f)

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
        signal_bus.publish_signal(
            "session-metrics",
            "demo-ws",
            "metrics",
            {
                "avg_error_rate_pct": 2.1,
                "avg_cache_hit_rate_pct": 84.0,
                "max_context_tokens": 160_000,
                "recent_duplicate_spawns": 0,
                "recent_rewind_calls": 0,
                "recent_interrupted_agents": 0,
                "recent_bridge_sessions": 0,
                "high_output_cost_ratio": 0.2,
            },
            sample_count=5,
            window_size=5,
            gravity_home=self._gravity_home,
        )

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
