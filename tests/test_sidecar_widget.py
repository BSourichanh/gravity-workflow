#!/usr/bin/env python3
"""Tests unitaires de state_writer et du registre centralisé live_workspaces.json."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _unit_helpers import ensure_plugin_paths

ensure_plugin_paths()

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

                    # Stockage centralisé par défaut sous workspaces/<slug>-<sha256_8>/
                    default_state = state_writer.resolve_state_path(workspace_root=ws)
                    self.assertEqual(default_state.parent.parent, wf_home.resolve() / "workspaces")
                    self.assertIn(state_writer.workspace_slug_hash(ws), str(default_state))

                    # Explicit REGISTRY_PATH monkeypatch remains supported in state_writer
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
