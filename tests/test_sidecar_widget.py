#!/usr/bin/env python3
"""Tests unitaires de state_writer, du registre centralisé et du fournisseur SPI-1 dashboard_provider.py."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _unit_helpers import PLUGIN_ROOT, ensure_plugin_paths

ensure_plugin_paths()

from _shared import signal_bus  # noqa: E402
import profile_engine  # noqa: E402
import state_writer  # noqa: E402


class TestStateWriterAndCentralRegistry(unittest.TestCase):
    def test_atomic_write_and_max_events_cap(self) -> None:
        prev_skip = os.environ.get("GRAVITY_WORKFLOW_SKIP_REGISTRY")
        os.environ["GRAVITY_WORKFLOW_SKIP_REGISTRY"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ws_root = Path(tmp)
                state_path = ws_root / ".agents/gravity-workflow/observability/live_state.json"
                for i in range(60):
                    state_writer.update_live_state(
                        stage="testing",
                        progress="3/8",
                        event={"type": "TEST", "decision": "allow", "message": f"ev-{i}"},
                        state_path=state_path,
                        workspace_root=ws_root,
                    )
                data = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(data["stage"], "testing")
                self.assertEqual(data["progress"], "3/8")
                self.assertEqual(len(data["events"]), state_writer.MAX_EVENTS)
                self.assertEqual(data["events"][0]["message"], "ev-10")
                self.assertEqual(data["events"][-1]["message"], "ev-59")
        finally:
            if prev_skip is None:
                os.environ.pop("GRAVITY_WORKFLOW_SKIP_REGISTRY", None)
            else:
                os.environ["GRAVITY_WORKFLOW_SKIP_REGISTRY"] = prev_skip

    def test_centralized_storage_precedence_and_monkeypatch_compat(self) -> None:
        env_keys = (
            "GRAVITY_WORKFLOW_HOME",
            "GRAVITY_HOME",
            "GRAVITY_WORKFLOW_REGISTRY_PATH",
            "GRAVITY_WORKFLOW_SKIP_REGISTRY",
        )
        saved_env = {k: os.environ.get(k) for k in env_keys}
        try:
            for k in env_keys:
                os.environ.pop(k, None)

            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                fake_home = base / "fake_home"
                gravity_home = base / "gravity_home"
                wf_home = base / "wf_home"
                explicit_reg = base / "explicit" / "live_workspaces.json"

                with mock.patch("pathlib.Path.home", return_value=fake_home):
                    self.assertEqual(
                        state_writer.default_workflow_home(),
                        (fake_home / ".gemini" / "gravity" / "workflow").resolve(),
                    )
                    self.assertEqual(
                        state_writer.resolve_registry_path(),
                        (fake_home / ".gemini" / "gravity" / "workflow" / "live_workspaces.json").resolve(),
                    )

                    os.environ["GRAVITY_HOME"] = str(gravity_home)
                    self.assertEqual(
                        state_writer.default_workflow_home(),
                        (gravity_home / "workflow").resolve(),
                    )
                    self.assertEqual(
                        state_writer.resolve_registry_path(),
                        (gravity_home / "workflow" / "live_workspaces.json").resolve(),
                    )

                    os.environ["GRAVITY_WORKFLOW_HOME"] = str(wf_home)
                    self.assertEqual(state_writer.default_workflow_home(), wf_home.resolve())
                    self.assertEqual(
                        state_writer.resolve_registry_path(),
                        (wf_home / "live_workspaces.json").resolve(),
                    )

                    ws = base / "ws"
                    ws.mkdir()
                    state_file = ws / ".agents/gravity-workflow/observability/live_state.json"
                    state_writer.update_live_state(
                        stage="testing",
                        state_path=state_file,
                        workspace_root=ws,
                    )
                    reg_file = wf_home / "live_workspaces.json"
                    self.assertTrue(reg_file.is_file())
                    reg_payload = json.loads(reg_file.read_text(encoding="utf-8"))
                    registered = reg_payload["workspaces"]
                    self.assertEqual(len(registered), 1)
                    self.assertEqual(Path(registered[0]["state_path"]).resolve(), state_file.resolve())
                    self.assertEqual(Path(registered[0]["workspace_path"]).resolve(), ws.resolve())

                    default_state = state_writer.resolve_state_path(workspace_root=ws)
                    self.assertEqual(default_state.parent.parent, wf_home.resolve() / "workspaces")
                    self.assertIn(state_writer.workspace_slug_hash(ws), str(default_state))

                    patched_reg = base / "patched" / "live_workspaces.json"
                    with mock.patch.object(state_writer, "REGISTRY_PATH", patched_reg):
                        self.assertEqual(state_writer.resolve_registry_path(), patched_reg)
                        state_writer.update_live_state(
                            stage="patched",
                            state_path=state_file,
                            workspace_root=ws,
                        )
                        self.assertTrue(patched_reg.is_file())

                    os.environ["GRAVITY_WORKFLOW_REGISTRY_PATH"] = str(explicit_reg)
                    self.assertEqual(state_writer.resolve_registry_path(), explicit_reg.resolve())
        finally:
            for k, val in saved_env.items():
                if val is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = val


class TestDashboardProviderSPI1(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._base = Path(self._tmp.name)
        self._env_keys = (
            "GRAVITY_HOME",
            "GRAVITY_WORKFLOW_HOME",
            "GRAVITY_WORKFLOW_REGISTRY_PATH",
            "GRAVITY_WORKFLOW_SKIP_REGISTRY",
            "GRAVITY_WORKFLOW_STATE_PATH",
        )
        self._saved_env = {k: os.environ.get(k) for k in self._env_keys}
        self._gravity_home = self._base / "gravity_home"
        self._wf_home = self._gravity_home / "workflow"
        os.environ["GRAVITY_HOME"] = str(self._gravity_home)
        os.environ["GRAVITY_WORKFLOW_HOME"] = str(self._wf_home)
        os.environ.pop("GRAVITY_WORKFLOW_REGISTRY_PATH", None)
        os.environ.pop("GRAVITY_WORKFLOW_SKIP_REGISTRY", None)
        os.environ.pop("GRAVITY_WORKFLOW_STATE_PATH", None)
        self._provider = PLUGIN_ROOT / "scripts" / "dashboard_provider.py"

    def tearDown(self) -> None:
        for key, val in self._saved_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    def _run_provider(self, args: list[str], stdin_obj: object = None) -> dict:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(self._provider), *args],
            input=json.dumps(stdin_obj) if stdin_obj is not None else "",
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=dict(os.environ),
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip())

    def test_module_contract_declares_spi1_dashboard_and_session_metrics_consume(self) -> None:
        mod = json.loads((PLUGIN_ROOT / "config" / "module.json").read_text(encoding="utf-8"))
        self.assertEqual(mod["dashboard"]["provider_script"], "scripts/dashboard_provider.py")
        self.assertEqual(mod["dashboard"]["views"], ["workflow"])
        self.assertEqual(mod["dashboard"]["actions"], [])
        self.assertIn("session-metrics", mod["signals"]["consumes"])
        self.assertTrue(self._provider.is_file())

    def test_default_empty_view_returns_zero_of_zero_progress_and_since_signature(self) -> None:
        data = self._run_provider(["--view", "workflow", "--json"])
        self.assertTrue(data["changed"])
        self.assertFalse(data["unchanged"])
        self.assertFalse(data["available"])
        self.assertEqual(data["progress"], "0/0")
        self.assertEqual(data["stage"], "idle")
        self.assertEqual(data["preset_profile"], "standard")
        self.assertEqual(data["runs"]["count"], 0)
        self.assertIsNone(data["runs"]["latest"])
        self.assertEqual(data["signal_v1"], {"available": False, "sample_count": 0})
        self.assertTrue(data["signature"])

        unchanged = self._run_provider(["--view", "workflow", "--since-signature", data["signature"], "--json"])
        self.assertEqual(unchanged, {"changed": False, "unchanged": True, "signature": data["signature"]})

    def test_populated_workspace_surfaces_profile_runs_and_signal_v1(self) -> None:
        ws = self._base / "demo-repo"
        ws.mkdir()
        profile_engine.update_profile_settings(ws, raw_mode="deep", reason="SPI-1 test")
        state_writer.append_run_log(
            ws,
            {
                "timestamp": "2026-11-26T14:00:00Z",
                "event": "Stop",
                "conversation_id": "conv-spi1-1",
                "preset_profile": "deep",
                "role_models": {"plan": "pro", "build": "flash", "review": "pro"},
            },
        )
        signal_bus.publish_signal(
            "session-metrics",
            "demo-repo",
            "metrics",
            {
                "avg_error_rate_pct": 1.5,
                "avg_cache_hit_rate_pct": 88.0,
                "max_context_tokens": 90_000,
                "recent_duplicate_spawns": 0,
                "recent_rewind_calls": 0,
                "recent_interrupted_agents": 0,
                "recent_bridge_sessions": 0,
                "high_output_cost_ratio": 0.12,
            },
            sample_count=4,
            window_size=5,
            gravity_home=self._gravity_home,
        )

        data = self._run_provider(["--view", "workflow", "--workspace", str(ws), "--json"])
        self.assertTrue(data["available"])
        self.assertEqual(data["mode"], "deep")
        self.assertEqual(data["level"], "deep")
        self.assertEqual(data["preset_profile"], "deep")
        self.assertEqual(data["progress"], "0/0")
        self.assertEqual(data["runs"]["count"], 1)
        self.assertEqual(data["runs"]["latest"]["conversation_id"], "conv-spi1-1")
        self.assertTrue(data["signal_v1"]["available"])
        self.assertEqual(data["signal_v1"]["sample_count"], 4)

        via_stdin = self._run_provider(
            ["--view", "workflow", "--json"],
            stdin_obj={"params": {"ws": str(ws)}, "context": {"workflow_home": str(self._wf_home)}},
        )
        self.assertEqual(via_stdin["signature"], data["signature"])
        self.assertEqual(via_stdin["runs"]["count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
