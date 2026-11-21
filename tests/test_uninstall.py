#!/usr/bin/env python3
"""Unit tests for the cross-platform Gravity uninstaller (100% Python 3 stdlib)."""

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import install  # noqa: E402
import uninstall  # noqa: E402


def _run_install_quiet(argv: List[str]) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return install.main(argv)


def _run_uninstall_quiet(argv: List[str]) -> tuple:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = uninstall.main(argv)
    return rc, out_buf.getvalue(), err_buf.getvalue()


class TestGravityUninstaller(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_reg = os.environ.get("GRAVITY_REGISTRY_PATH")
        self._tmp_reg_dir = tempfile.TemporaryDirectory()
        self._default_reg = Path(self._tmp_reg_dir.name) / ".gravity" / "live_workspaces.json"
        os.environ["GRAVITY_REGISTRY_PATH"] = str(self._default_reg)

    def tearDown(self) -> None:
        if self._prev_reg is None:
            os.environ.pop("GRAVITY_REGISTRY_PATH", None)
        else:
            os.environ["GRAVITY_REGISTRY_PATH"] = self._prev_reg
        self._tmp_reg_dir.cleanup()

    def test_uninstall_plugin_target_and_workspace_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "global" / "gravity"
            self.assertEqual(_run_install_quiet(["--target", str(target)]), 0)
            self.assertTrue((target / "plugin.json").is_file())

            rc, _, _ = _run_uninstall_quiet(["--target", str(target)])
            self.assertEqual(rc, 0)
            self.assertFalse(target.exists())

            ws = base / "proj"
            ws.mkdir()
            self.assertEqual(
                _run_install_quiet(["--scope", "workspace", "--workspace", str(ws)]), 0
            )
            ws_plugin = ws / ".agents" / "plugins" / "gravity"
            self.assertTrue((ws_plugin / "plugin.json").is_file())

            rc_ws, _, _ = _run_uninstall_quiet(
                ["--scope", "workspace", "--workspace", str(ws)]
            )
            self.assertEqual(rc_ws, 0)
            self.assertFalse(ws_plugin.exists())

    def test_refuses_unsafe_and_non_gravity_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            plain_dir = base / "plain"
            plain_dir.mkdir()
            keep_file = plain_dir / "keep.txt"
            keep_file.write_text("safe", encoding="utf-8")
            rc_plain, _, _ = _run_uninstall_quiet(["--target", str(plain_dir)])
            self.assertNotEqual(rc_plain, 0)
            self.assertTrue(keep_file.is_file())

            other_plugin = base / "other-plugin"
            other_plugin.mkdir()
            other_manifest = other_plugin / "plugin.json"
            other_manifest.write_text('{"name": "other"}', encoding="utf-8")
            rc_other, _, _ = _run_uninstall_quiet(["--target", str(other_plugin)])
            self.assertNotEqual(rc_other, 0)
            self.assertTrue(other_manifest.is_file())

            # Refuse source directories & workspace-template
            for forbidden in (
                install.PLUGIN_SOURCE,
                REPO_ROOT,
                REPO_ROOT / "workspace-template",
            ):
                rc_src, _, _ = _run_uninstall_quiet(["--target", str(forbidden)])
                self.assertNotEqual(rc_src, 0)
                rc_ws, _, _ = _run_uninstall_quiet(
                    ["--workspace-only", "--clean-workspace", str(forbidden)]
                )
                self.assertNotEqual(rc_ws, 0)

            self.assertTrue((install.PLUGIN_SOURCE / "plugin.json").is_file())
            self.assertTrue((REPO_ROOT / "workspace-template" / "AGENTS.md").is_file())

            rc_home, _, _ = _run_uninstall_quiet(
                ["--workspace-only", "--clean-workspace", str(Path.home())]
            )
            self.assertNotEqual(rc_home, 0)

    def test_dry_run_and_check_do_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "gravity"
            ws = base / "proj"
            fake_home = base / "home"
            (fake_home / ".gemini" / "config").mkdir(parents=True)
            (fake_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
            (fake_home / ".gemini" / "config" / "config.json").write_text(
                json.dumps({"sidecars": {"gravity/live-widget": {"enabled": True}}}),
                encoding="utf-8",
            )
            (fake_home / ".gemini" / "antigravity-cli" / "settings.json").write_text(
                json.dumps({"statusLine": {"command": "python3 .../statusline.py"}}),
                encoding="utf-8",
            )

            self.assertEqual(
                _run_install_quiet(
                    ["--target", str(target), "--init-workspace", str(ws), "--mode", "full"]
                ),
                0,
            )
            self._default_reg.parent.mkdir(parents=True, exist_ok=True)
            self._default_reg.write_text(
                json.dumps(
                    {
                        "workspaces": [
                            {
                                "workspace": str(ws.resolve()),
                                "state_path": str((ws / ".agents/gravity/observability/live_state.json").resolve()),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            manual = uninstall.detect_manual_config_entries(home_dir=fake_home)
            self.assertTrue(manual["sidecar"])
            self.assertTrue(manual["statusline"])

            rc_chk, out_chk, _ = _run_uninstall_quiet(
                ["--check", "--target", str(target), "--clean-workspace", str(ws)]
            )
            self.assertEqual(rc_chk, 0)
            self.assertIn("Gravity Uninstall Check", out_chk)

            rc_dry, out_dry, _ = _run_uninstall_quiet(
                [
                    "--dry-run",
                    "--target",
                    str(target),
                    "--clean-workspace",
                    str(ws),
                    "--purge-workspace-templates",
                    "--clean-registry",
                ]
            )
            self.assertEqual(rc_dry, 0)
            self.assertIn("[DRY-RUN]", out_dry)
            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue((ws / ".agents/gravity/profile.json").is_file())
            self.assertTrue((ws / "AGENTS.md").is_file())
            self.assertTrue(self._default_reg.is_file())

    def test_clean_workspace_preserves_user_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws = base / "my-project"
            other_ws = base / "other-project"
            other_ws.mkdir()

            self.assertEqual(
                _run_install_quiet(
                    ["--scope", "workspace", "--workspace", str(ws), "--init-workspace", str(ws)]
                ),
                0,
            )
            self._default_reg.parent.mkdir(parents=True, exist_ok=True)
            self._default_reg.write_text(
                json.dumps(
                    {
                        "workspaces": [
                            {
                                "workspace": str(ws.resolve()),
                                "state_path": str((ws / ".agents/gravity/observability/live_state.json").resolve()),
                            },
                            {
                                "workspace": str(other_ws.resolve()),
                                "state_path": str((other_ws / ".agents/gravity/observability/live_state.json").resolve()),
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            rc, _, _ = _run_uninstall_quiet(
                ["--workspace-only", "--clean-workspace", str(ws)]
            )
            self.assertEqual(rc, 0)
            self.assertFalse((ws / ".agents/gravity").exists())
            self.assertFalse((ws / ".agents/plugins/gravity").exists())
            self.assertTrue((ws / "AGENTS.md").is_file())
            self.assertTrue((ws / ".agents/rules/01-safety-boundaries.md").is_file())
            self.assertTrue((ws / ".agents/rules/05-git-versioning.md").is_file())
            self.assertTrue((ws / ".agents/mcp_config.json").is_file())

            reg_data = json.loads(self._default_reg.read_text(encoding="utf-8"))
            self.assertEqual(len(reg_data["workspaces"]), 1)
            self.assertEqual(reg_data["workspaces"][0]["workspace"], str(other_ws.resolve()))

    def test_purge_workspace_templates_keeps_modified_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "gravity"
            ws_mod = base / "ws-modified"
            ws_clean = base / "ws-unmodified"

            self.assertEqual(
                _run_install_quiet(
                    ["--target", str(target), "--init-workspace", str(ws_mod), "--mode", "full"]
                ),
                0,
            )
            self.assertEqual(
                _run_install_quiet(
                    ["--target", str(target), "--force", "--init-workspace", str(ws_clean), "--mode", "full"]
                ),
                0,
            )

            custom_agents = "# Custom Project AGENTS\n"
            custom_rule = "---\ntrigger: always_on\n---\n# Custom rule\n"
            (ws_mod / "AGENTS.md").write_text(custom_agents, encoding="utf-8")
            (ws_mod / ".agents/rules/02-python-stdlib.md").write_text(custom_rule, encoding="utf-8")

            rc_mod, out_mod, _ = _run_uninstall_quiet(
                [
                    "--workspace-only",
                    "--clean-workspace",
                    str(ws_mod),
                    "--purge-workspace-templates",
                ]
            )
            self.assertEqual(rc_mod, 0)
            self.assertIn("[KEEP]  AGENTS.md", out_mod)
            self.assertIn("[KEEP]  .agents/rules/02-python-stdlib.md", out_mod)
            self.assertEqual((ws_mod / "AGENTS.md").read_text(encoding="utf-8"), custom_agents)
            self.assertTrue((ws_mod / ".agents/rules/02-python-stdlib.md").is_file())
            self.assertFalse((ws_mod / ".agents/rules/01-safety-boundaries.md").exists())
            self.assertFalse((ws_mod / ".agents/rules/05-git-versioning.md").exists())
            self.assertFalse((ws_mod / ".agents/mcp_config.json").exists())
            self.assertFalse((ws_mod / ".agents/gravity").exists())

            rc_clean, _, _ = _run_uninstall_quiet(
                [
                    "--workspace-only",
                    "--clean-workspace",
                    str(ws_clean),
                    "--purge-workspace-templates",
                ]
            )
            self.assertEqual(rc_clean, 0)
            self.assertFalse((ws_clean / "AGENTS.md").exists())
            self.assertFalse((ws_clean / ".agents/rules/05-git-versioning.md").exists())
            self.assertFalse((ws_clean / ".agents").exists())

    def test_clean_registry_and_all_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "gravity"
            ws = base / "proj"

            self.assertEqual(
                _run_install_quiet(
                    ["--target", str(target), "--init-workspace", str(ws), "--mode", "quickstart"]
                ),
                0,
            )
            self._default_reg.parent.mkdir(parents=True, exist_ok=True)
            self._default_reg.write_text('{"workspaces":[]}', encoding="utf-8")
            self._default_reg.with_suffix(".lock").write_text("0", encoding="utf-8")

            rc_reg, _, _ = _run_uninstall_quiet(["--workspace-only", "--clean-registry"])
            self.assertEqual(rc_reg, 0)
            self.assertFalse(self._default_reg.exists())
            self.assertFalse(self._default_reg.with_suffix(".lock").exists())
            self.assertFalse(self._default_reg.parent.exists())

            self._default_reg.parent.mkdir(parents=True, exist_ok=True)
            self._default_reg.write_text('{"workspaces":[]}', encoding="utf-8")
            rc_all, _, _ = _run_uninstall_quiet(
                ["--all", "--target", str(target), "--clean-workspace", str(ws)]
            )
            self.assertEqual(rc_all, 0)
            self.assertFalse(target.exists())
            self.assertFalse((ws / ".agents/gravity").exists())
            self.assertFalse(self._default_reg.exists())

    def test_method_agy_validation_and_delegation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "proj"
            ws.mkdir()
            rc_ws, _, _ = _run_uninstall_quiet(
                ["--method", "agy", "--scope", "workspace", "--workspace", str(ws)]
            )
            self.assertEqual(rc_ws, 2)
            rc_tgt, _, _ = _run_uninstall_quiet(["--method", "agy", "--target", str(tmp)])
            self.assertEqual(rc_tgt, 2)

        calls: List[List[str]] = []

        def fake_run(cmd: List[str], **_kwargs: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[ok]\n", stderr="")

        with mock.patch("shutil.which", return_value="/usr/local/bin/agy"), mock.patch(
            "subprocess.run", side_effect=fake_run
        ):
            rc, _, _ = _run_uninstall_quiet(["--method", "agy"])
            self.assertEqual(rc, 0)

        self.assertEqual(calls, [["/usr/local/bin/agy", "plugin", "uninstall", "gravity"]])

    def test_cli_argument_errors(self) -> None:
        rc_purge, _, _ = _run_uninstall_quiet(["--purge-workspace-templates"])
        self.assertEqual(rc_purge, 2)

        rc_ws_only, _, _ = _run_uninstall_quiet(["--workspace-only"])
        self.assertEqual(rc_ws_only, 2)

        rc_ws_all, _, _ = _run_uninstall_quiet(["--workspace-only", "--all"])
        self.assertEqual(rc_ws_all, 2)

        rc_scope, _, _ = _run_uninstall_quiet(["--scope", "workspace"])
        self.assertEqual(rc_scope, 2)

    def test_uninstall_handles_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_plugin = base / "real_plugin"
            real_plugin.mkdir()
            (real_plugin / "plugin.json").write_text('{"name": "gravity"}', encoding="utf-8")
            link_plugin = base / "gravity_link"
            try:
                os.symlink(real_plugin, link_plugin, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink creation requires elevated privileges on this host.")

            rc_plugin, _, _ = _run_uninstall_quiet(["--target", str(link_plugin)])
            self.assertEqual(rc_plugin, 0)
            self.assertFalse(link_plugin.exists() or link_plugin.is_symlink())
            self.assertTrue((real_plugin / "plugin.json").is_file())

            ws = base / "ws"
            (ws / ".agents").mkdir(parents=True)
            real_state = base / "real_state"
            real_state.mkdir()
            (real_state / "PROGRESS.md").write_text("# Progress\n", encoding="utf-8")
            link_state = ws / ".agents" / "gravity"
            os.symlink(real_state, link_state, target_is_directory=True)

            rc_ws, _, _ = _run_uninstall_quiet(["--workspace-only", "--clean-workspace", str(ws)])
            self.assertEqual(rc_ws, 0)
            self.assertFalse(link_state.exists() or link_state.is_symlink())
            self.assertTrue((real_state / "PROGRESS.md").is_file())

    def test_stdlib_only_ast(self) -> None:
        stdlib_names = getattr(sys, "stdlib_module_names", None)
        if not stdlib_names:
            self.skipTest("sys.stdlib_module_names requires Python 3.10+")

        allowed = set(stdlib_names) | {"install", "uninstall", "init_workspace"}
        for py_path in (REPO_ROOT / "uninstall.py", Path(__file__).resolve()):
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            for node in ast.walk(tree):
                imported: List[str] = []
                if isinstance(node, ast.Import):
                    imported = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    imported = [node.module.split(".")[0]]
                for mod in imported:
                    self.assertIn(mod, allowed, f"Non-stdlib import '{mod}' in {py_path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
