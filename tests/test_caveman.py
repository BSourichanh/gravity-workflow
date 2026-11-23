#!/usr/bin/env python3
"""Tests unitaires du protocole Caveman (règles, directives sous-agents, verrouillage et CLI)."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _unit_helpers import WorkflowEnvTestCase, ensure_plugin_paths

PLUGIN_ROOT = ensure_plugin_paths()

import caveman_protocol  # noqa: E402
import profile_engine  # noqa: E402


class TestCavemanIntegration(WorkflowEnvTestCase):
    def test_caveman_protocol_rules_and_directives(self) -> None:
        self.assertEqual(caveman_protocol.normalize_caveman(" EVO "), "auto")
        self.assertEqual(caveman_protocol.normalize_caveman("FULL"), "full")
        for bad in ("", "medium", "yolo"):
            with self.assertRaises(ValueError):
                caveman_protocol.normalize_caveman(bad)

        self.assertEqual(caveman_protocol.effective_planner_caveman("ultra"), "full")
        self.assertEqual(caveman_protocol.effective_planner_caveman("full"), "full")
        self.assertEqual(caveman_protocol.effective_planner_caveman("lite"), "lite")
        self.assertEqual(caveman_protocol.effective_planner_caveman("off"), "off")
        self.assertEqual(caveman_protocol.effective_planner_caveman("unknown"), "off")

        # lite par défaut sur les 3 profils (full auto réservé Phase 3)
        for tier in ("quick", "standard", "deep"):
            auto_lvl, _ = caveman_protocol.choose_evo_caveman(tier, {}, 10)
            self.assertEqual(auto_lvl, "lite")

        for bad_sig in (
            {"session_error": True},
            {"guard_deny_recent": 1},
            {"eval_pass_rate": 0.75},
        ):
            lvl_err, _ = caveman_protocol.choose_evo_caveman("deep", bad_sig, 5)
            self.assertEqual(lvl_err, "lite")

        self.assertEqual(caveman_protocol.build_session_start_prompt("off"), "")
        prompt_lite = caveman_protocol.build_session_start_prompt("lite", locked=False)
        self.assertIn("niveau=lite (auto/evo)", prompt_lite)
        self.assertIn("PreToolUse", prompt_lite)
        self.assertIn("evals exclus", prompt_lite)
        prompt_ultra = caveman_protocol.build_session_start_prompt("ultra", locked=True)
        self.assertIn("niveau=ultra (verrouillé)", prompt_ultra)

        self.assertEqual(caveman_protocol.build_subagent_directive("planner", "off"), "")
        self.assertEqual(caveman_protocol.build_subagent_directive("unknown", "full"), "")
        self.assertIn("[Caveman:full]", caveman_protocol.build_subagent_directive("planner", "ultra"))
        self.assertIn("[Caveman:lite]", caveman_protocol.build_subagent_directive("planner", "lite"))
        self.assertIn("[Caveman:full]", caveman_protocol.build_subagent_directive("builder", "full"))
        self.assertIn("Preuve ou Silence", caveman_protocol.build_subagent_directive("reviewer", "ultra"))
        self.assertIn("Swarm", caveman_protocol.build_subagent_directive("swarm", "full"))

    def test_profile_engine_caveman_lock_evo_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()

            prof0 = profile_engine.load_profile(ws)
            self.assertEqual(prof0["caveman"], "lite")
            self.assertFalse(prof0["caveman_locked"])
            self.assertEqual(profile_engine.format_caveman_badge(prof0), "caveman:lite")

            # Verrouillage manuel + preset ROI
            prof_ultra = profile_engine.set_caveman(ws, "ultra")
            self.assertEqual(prof_ultra["caveman"], "ultra")
            self.assertTrue(prof_ultra["caveman_locked"])
            self.assertEqual(profile_engine.format_caveman_badge(prof_ultra), "caveman:ultra[lock]")

            preset_ultra = profile_engine.get_active_preset(ws)
            self.assertEqual(preset_ultra["caveman"], "ultra")
            self.assertEqual(preset_ultra["caveman_planner"], "full")
            self.assertEqual(preset_ultra["caveman_evals"], "off")
            self.assertEqual(
                profile_engine.get_active_preset(ws, caveman_override="lite")["caveman_planner"],
                "lite",
            )

            # Respect du verrou caveman_locked lors de evolve_on_stop
            evolved_locked = profile_engine.evolve_on_stop(
                ws, {"conversation_id": "cav-lock-1", "fully_idle": True}
            )
            self.assertEqual(evolved_locked["caveman"], "ultra")
            self.assertTrue(evolved_locked["caveman_locked"])

            # Déverrouillage via auto / evo -> revient au défaut lite
            unlocked = profile_engine.set_caveman(ws, "evo")
            self.assertFalse(unlocked["caveman_locked"])
            self.assertEqual(unlocked["caveman"], "lite")

            ev2 = profile_engine.evolve_on_stop(
                ws, {"conversation_id": "cav-clean-2", "fully_idle": True}
            )
            self.assertEqual(ev2["clean_streak"], 2)
            self.assertEqual(ev2["caveman"], "lite")

            # Indépendance entre locked et caveman_locked
            profile_engine.set_mode(ws, "quick")
            ev5 = profile_engine.evolve_on_stop(
                ws, {"conversation_id": "cav-indep-5", "fully_idle": True}
            )
            self.assertTrue(ev5["locked"])
            self.assertEqual(ev5["current_level"], "quick")
            self.assertFalse(ev5["caveman_locked"])
            self.assertEqual(ev5["caveman"], "lite")

            # CLI set --caveman & get --json
            cli_script = PLUGIN_ROOT / "observability/profile_engine.py"
            proc_set = subprocess.run(
                [
                    sys.executable,
                    str(cli_script),
                    "set",
                    "--workspace",
                    str(ws),
                    "--caveman",
                    "off",
                    "--json",
                ],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc_set.returncode, 0, proc_set.stderr)
            set_data = json.loads(proc_set.stdout)
            self.assertEqual(set_data["caveman_badge"], "caveman:off[lock]")
            self.assertEqual(set_data["profile"]["caveman"], "off")
            self.assertTrue(set_data["profile"]["caveman_locked"])

            proc_get = subprocess.run(
                [sys.executable, str(cli_script), "get", "--workspace", str(ws), "--json"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc_get.returncode, 0, proc_get.stderr)
            get_data = json.loads(proc_get.stdout)
            self.assertEqual(get_data["caveman_badge"], "caveman:off[lock]")
            self.assertEqual(get_data["preset"]["caveman_evals"], "off")


if __name__ == "__main__":
    unittest.main(verbosity=2)
