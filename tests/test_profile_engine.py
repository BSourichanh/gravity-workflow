#!/usr/bin/env python3
"""Tests unitaires du moteur de profils (3 profils, A/B, evo consultatif, métriques)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _unit_helpers import WorkflowEnvTestCase, ensure_plugin_paths

PLUGIN_ROOT = ensure_plugin_paths()

import caveman_protocol  # noqa: E402
import profile_engine  # noqa: E402
import state_writer  # noqa: E402


class TestProfileEngine(WorkflowEnvTestCase):
    def test_default_profile_centralized_and_lock_unlock_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "plain_repo"
            ws.mkdir()

            default_prof = profile_engine.load_profile(ws)
            self.assertEqual(default_prof["mode"], "evo")
            self.assertEqual(default_prof["current_level"], "standard")
            self.assertEqual(default_prof["suggested_profile"], "standard")
            self.assertFalse(default_prof["locked"])
            self.assertEqual(profile_engine.format_mode_badge(default_prof), "evo:standard")

            locked_prof = profile_engine.set_mode(ws, "deep")
            self.assertEqual(locked_prof["mode"], "deep")
            self.assertEqual(locked_prof["current_level"], "deep")
            self.assertTrue(locked_prof["locked"])
            self.assertEqual(profile_engine.format_mode_badge(locked_prof), "deep[lock]")

            # Zéro écriture dans le workspace cible : tout est centralisé
            self.assertFalse((ws / ".agents").exists())
            central_live = state_writer.resolve_state_path(workspace_root=ws)
            self.assertTrue(central_live.is_file())
            live = json.loads(central_live.read_text(encoding="utf-8"))
            self.assertEqual(live["mode"], "deep")
            self.assertEqual(live["level"], "deep")
            self.assertEqual(live["preset_profile"], "deep")

            unlocked_evo = profile_engine.set_mode(ws, "evo")
            self.assertEqual(unlocked_evo["mode"], "evo")
            self.assertFalse(unlocked_evo["locked"])
            self.assertEqual(unlocked_evo["current_level"], "deep")

            # Alias rétrocompatibles low->quick, medium->standard, high/ultra->deep, evolutive->evo
            self.assertEqual(profile_engine.set_mode(ws, "low")["current_level"], "quick")
            self.assertEqual(profile_engine.set_mode(ws, "medium")["current_level"], "standard")
            self.assertEqual(profile_engine.set_mode(ws, "high")["current_level"], "deep")
            self.assertEqual(profile_engine.set_mode(ws, "ultra")["current_level"], "deep")
            unlocked_alias = profile_engine.set_mode(ws, "evolutive")
            self.assertEqual(unlocked_alias["mode"], "evo")
            self.assertFalse(unlocked_alias["locked"])

    def test_three_profiles_no_dead_keys_ab_overrides_and_allow_pro_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "repo"
            ws.mkdir()

            expected_keys = {
                "mode",
                "level",
                "locked",
                "suggested_profile",
                "last_reason",
                "plan_model",
                "build_model",
                "review_model",
                "eval_candidate",
                "eval_judge",
                "max_remediation_rounds",
                "skip_separate_planner",
                "skip_verification",
                "caveman",
                "caveman_planner",
                "caveman_evals",
                "execution_engine",
            }

            p_quick = profile_engine.get_active_preset(ws, level_override="quick")
            self.assertEqual(set(p_quick.keys()), expected_keys)
            self.assertEqual(p_quick["build_model"], "flash")
            self.assertEqual(p_quick["plan_model"], "flash")
            self.assertEqual(p_quick["review_model"], "flash")
            self.assertEqual(p_quick["max_remediation_rounds"], 0)
            self.assertTrue(p_quick["skip_separate_planner"])

            p_std = profile_engine.get_active_preset(ws)
            self.assertEqual(set(p_std.keys()), expected_keys)
            self.assertEqual(p_std["level"], "standard")
            self.assertEqual(p_std["plan_model"], "flash")
            self.assertEqual(p_std["build_model"], "flash")
            self.assertEqual(p_std["review_model"], "pro")
            self.assertEqual(p_std["max_remediation_rounds"], 1)
            self.assertFalse(p_std["skip_separate_planner"])

            p_deep = profile_engine.get_active_preset(ws, level_override="deep")
            self.assertEqual(p_deep["plan_model"], "pro")
            self.assertEqual(p_deep["build_model"], "flash")
            self.assertEqual(p_deep["review_model"], "pro")
            self.assertEqual(p_deep["max_remediation_rounds"], 2)

            # Repli lecture seule sur <ws>/.agents/gravity-workflow/profile.json si centralisé absent
            ro_prof = ws / ".agents/gravity-workflow/profile.json"
            ro_prof.parent.mkdir(parents=True)
            ro_prof.write_text(
                json.dumps({"mode": "high", "allow_pro": False, "verification": "unsafe"}),
                encoding="utf-8",
            )
            p_ro = profile_engine.get_active_preset(ws)
            self.assertEqual(p_ro["level"], "deep")
            self.assertEqual(p_ro["plan_model"], "flash")
            self.assertEqual(p_ro["review_model"], "flash")
            # unsafe persisté dans un ancien fichier est ignoré
            self.assertFalse(p_ro["skip_verification"])

            # Surcharges A/B via CLI (--plan-model, --build-model, --review-model)
            cli_script = PLUGIN_ROOT / "observability/profile_engine.py"
            proc_ab = subprocess.run(
                [
                    sys.executable,
                    str(cli_script),
                    "set",
                    "--workspace",
                    str(ws),
                    "--mode",
                    "deep",
                    "--allow-pro",
                    "true",
                    "--build-model",
                    "pro",
                    "--plan-model",
                    "flash",
                    "--json",
                ],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc_ab.returncode, 0, proc_ab.stderr)
            ab_data = json.loads(proc_ab.stdout)
            self.assertEqual(ab_data["preset"]["plan_model"], "flash")
            self.assertEqual(ab_data["preset"]["build_model"], "pro")
            self.assertEqual(ab_data["preset"]["review_model"], "pro")

    def test_consultative_evo_behavioral_without_file_count_or_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "small_plain_repo"
            ws.mkdir()
            (ws / "only_one.py").write_text("x = 1\n", encoding="utf-8")

            # Dépôt < 80 fichiers et sans init : evo s'exécute sans polluer ws et sans rétrograder en quick/flash_lite
            r1 = profile_engine.evolve_on_stop(
                ws, {"conversation_id": "s1", "fully_idle": True, "termination_reason": "NO_TOOL_CALL"}
            )
            self.assertEqual(r1["current_level"], "standard")
            self.assertEqual(r1["suggested_profile"], "standard")
            self.assertFalse((ws / ".agents").exists())

            # Friction comportementale (rejet Reviewer + deny) -> suggested_profile='deep' sans muter current_level
            state_writer.update_live_state(
                workspace_root=ws,
                event={"type": "REVIEWER", "decision": "force_ask", "message": "FAIL — écart majeur"},
            )
            state_writer.update_live_state(
                workspace_root=ws,
                event={"type": "GUARD:run_command", "decision": "deny", "message": "blocked"},
            )
            r2 = profile_engine.evolve_on_stop(
                ws, {"conversation_id": "s2", "fully_idle": True, "termination_reason": "NO_TOOL_CALL"}
            )
            self.assertEqual(r2["current_level"], "standard")
            self.assertEqual(r2["suggested_profile"], "deep")
            self.assertIn("rejet(s) Reviewer", r2["last_reason"])

            live = json.loads(state_writer.resolve_state_path(workspace_root=ws).read_text(encoding="utf-8"))
            self.assertEqual(live["suggested_profile"], "deep")
            self.assertEqual(live["level"], "standard")

            # Retour au calme sur session suivante -> suggested_profile='standard'
            r3 = profile_engine.evolve_on_stop(
                ws, {"conversation_id": "s3", "fully_idle": True, "termination_reason": "NO_TOOL_CALL"}
            )
            self.assertEqual(r3["current_level"], "standard")
            self.assertEqual(r3["suggested_profile"], "standard")

    def test_deprecated_pace_and_ephemeral_unsafe_never_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ephemeral_ws"
            ws.mkdir()

            # Alias historiques slow/fast tolérés sans crash ni persistance
            p_fast = profile_engine.update_profile_settings(ws, raw_pace="fast")
            self.assertNotIn("pace", p_fast)

            # unsafe ne doit JAMAIS être persisté
            with self.assertRaises(ValueError):
                profile_engine.update_profile_settings(ws, raw_verification="unsafe")

            # Mais éphémère en mémoire via get_active_preset
            ephem = profile_engine.get_active_preset(ws, no_verify=True)
            self.assertTrue(ephem["skip_verification"])
            self.assertEqual(ephem["max_remediation_rounds"], 0)

            persisted = profile_engine.get_active_preset(ws)
            self.assertFalse(persisted["skip_verification"])
            self.assertEqual(persisted["max_remediation_rounds"], 1)

            for bad_mode in ("auto", "yolo"):
                with self.assertRaises(ValueError):
                    profile_engine.set_mode(ws, bad_mode)

    def test_metrics_signals_integration_bounds_and_caveman_evo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws = base / "metrics-proj"
            ws.mkdir()
            metrics_home = base / "metrics_home"
            hfile = metrics_home / "data" / "history.jsonl"
            hfile.parent.mkdir(parents=True)
            hfile.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "status": "active",
                                "project": "metrics-proj",
                                "ended_at": "2026-11-23T23:00:00Z",
                                "error_rate_pct": 95.0,
                            }
                        ),
                        json.dumps(
                            {
                                "status": "archived",
                                "project": "metrics-proj",
                                "ended_at": "2026-11-23T10:00:00Z",
                                "error_rate_pct": 2.0,
                                "cache_hit_rate_pct": 85.0,
                                "max_context_tokens": 160_000,
                                "duplicate_spawns": 0,
                                "rewind_calls": 0,
                                "cost_usd": 1.0,
                                "cost_output_usd": 0.2,
                            }
                        ),
                        json.dumps(
                            {
                                "status": "archived",
                                "project": "metrics-proj",
                                "ended_at": "2026-11-23T11:00:00Z",
                                "error_rate_pct": 2.0,
                                "cache_hit_rate_pct": 85.0,
                                "max_context_tokens": 100_000,
                                "duplicate_spawns": 0,
                                "rewind_calls": 0,
                                "cost_usd": 1.0,
                                "cost_output_usd": 0.2,
                            }
                        ),
                        json.dumps(
                            {
                                "status": "archived",
                                "project": "metrics-proj",
                                "ended_at": "2026-11-23T12:00:00Z",
                                "error_rate_pct": 2.0,
                                "cache_hit_rate_pct": 82.0,
                                "max_context_tokens": 90_000,
                                "duplicate_spawns": 0,
                                "rewind_calls": 0,
                                "cost_usd": 1.0,
                                "cost_output_usd": 0.2,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"GRAVITY_METRICS_HOME": str(metrics_home)}):
                sig = profile_engine.collect_signals(ws)
                self.assertTrue(sig["metrics"]["available"])
                self.assertEqual(sig["metrics"]["sample_count"], 3)
                score, sugg, factors = profile_engine.compute_score(sig, clean_streak=0)
                self.assertEqual(score, 45)
                self.assertEqual(sugg, "standard")
                self.assertIn("metrics: cache/stabilité", factors)
                lvl, reason = caveman_protocol.choose_evo_caveman("standard", sig, 0)
                self.assertEqual(lvl, "full")
                self.assertIn("metrics", reason)
                prof = profile_engine.evolve_on_stop(ws, {"conversation_id": "m1", "fully_idle": True})
                self.assertIn("Signaux Metrics (3 sess.)", profile_engine.format_status(prof))

    def test_execution_engine_default_cli_override_and_backward_compat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "engine_ws"
            ws.mkdir()

            default_prof = profile_engine.default_profile()
            self.assertEqual(default_prof["execution_engine"], "swarm")

            preset_default = profile_engine.get_active_preset(ws)
            self.assertEqual(preset_default["execution_engine"], "swarm")
            self.assertEqual(
                profile_engine.get_active_preset(ws, execution_engine_override="run_workflow")["execution_engine"],
                "run_workflow",
            )
            self.assertEqual(
                profile_engine.get_active_preset(ws, execution_engine_override="invalid")["execution_engine"],
                "swarm",
            )
            self.assertIn("Engine: swarm", profile_engine.format_status(default_prof))

            # Rétrocompatibilité : un profile.json sans execution_engine ou avec valeur invalide -> "swarm"
            ro_prof = ws / ".agents/gravity-workflow/profile.json"
            ro_prof.parent.mkdir(parents=True)
            ro_prof.write_text(json.dumps({"mode": "evo", "current_level": "standard"}), encoding="utf-8")
            self.assertEqual(profile_engine.load_profile(ws)["execution_engine"], "swarm")
            ro_prof.write_text(
                json.dumps({"mode": "evo", "current_level": "standard", "execution_engine": "legacy"}),
                encoding="utf-8",
            )
            self.assertEqual(profile_engine.load_profile(ws)["execution_engine"], "swarm")

            with self.assertRaises(ValueError):
                profile_engine.update_profile_settings(ws, execution_engine="invalid")

            cli_script = PLUGIN_ROOT / "observability/profile_engine.py"
            proc_rw = subprocess.run(
                [sys.executable, str(cli_script), "set", "--workspace", str(ws), "--engine", "run_workflow", "--json"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc_rw.returncode, 0, proc_rw.stderr)
            rw_data = json.loads(proc_rw.stdout)
            self.assertEqual(rw_data["profile"]["execution_engine"], "run_workflow")
            self.assertEqual(rw_data["preset"]["execution_engine"], "run_workflow")
            self.assertIn("Engine: run_workflow", profile_engine.format_status(rw_data["profile"]))

            proc_sw = subprocess.run(
                [sys.executable, str(cli_script), "set", "--workspace", str(ws), "--engine", "swarm", "--json"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc_sw.returncode, 0, proc_sw.stderr)
            self.assertEqual(json.loads(proc_sw.stdout)["profile"]["execution_engine"], "swarm")

            proc_bad = subprocess.run(
                [sys.executable, str(cli_script), "set", "--workspace", str(ws), "--engine", "bogus"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertNotEqual(proc_bad.returncode, 0)
            self.assertIn("Moteur d'exécution Gravity invalide", proc_bad.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
