#!/usr/bin/env python3
"""Unit tests for the cross-platform Gravity Workflow installer (100% Python 3 stdlib)."""

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


def _run_quiet(argv: List[str]) -> int:
    """Invoke install.main while silencing stdout/stderr noise in test output."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return install.main(argv)


def _run_capture(argv: List[str]) -> tuple:
    """Invoke install.main and return (returncode, stdout, stderr)."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = install.main(argv)
    return rc, out_buf.getvalue(), err_buf.getvalue()


class TestGravityInstaller(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_wf_home = os.environ.get("GRAVITY_WORKFLOW_HOME")
        self._prev_reg = os.environ.get("GRAVITY_WORKFLOW_REGISTRY_PATH")
        self._prev_leg_reg = os.environ.get("GRAVITY_WORKFLOW_LEGACY_REGISTRY_PATH")
        self._tmp_env_dir = tempfile.TemporaryDirectory()
        self._fake_home = Path(self._tmp_env_dir.name) / "home"
        self._fake_home.mkdir(parents=True, exist_ok=True)
        self._default_reg = (
            self._fake_home / ".gemini" / "gravity" / "workflow" / "live_workspaces.json"
        )
        self._default_leg_reg = self._fake_home / ".gravity" / "live_workspaces.json"
        os.environ["GRAVITY_WORKFLOW_HOME"] = str(self._fake_home / ".gemini" / "gravity" / "workflow")
        os.environ["GRAVITY_WORKFLOW_REGISTRY_PATH"] = str(self._default_reg)
        os.environ["GRAVITY_WORKFLOW_LEGACY_REGISTRY_PATH"] = str(self._default_leg_reg)
        self._home_patcher = mock.patch("pathlib.Path.home", return_value=self._fake_home)
        self._home_patcher.start()

    def tearDown(self) -> None:
        self._home_patcher.stop()
        for key, prev in (
            ("GRAVITY_WORKFLOW_HOME", self._prev_wf_home),
            ("GRAVITY_WORKFLOW_REGISTRY_PATH", self._prev_reg),
            ("GRAVITY_WORKFLOW_LEGACY_REGISTRY_PATH", self._prev_leg_reg),
        ):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        self._tmp_env_dir.cleanup()

    def test_fresh_install_and_strict_validation(self) -> None:
        source_hooks_before = (install.PLUGIN_SOURCE / "hooks.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "plugins" / "gravity-workflow"
            rc = _run_quiet(["--target", str(target)])
            self.assertEqual(rc, 0)
            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue((target / "config" / "module.json").is_file())
            self.assertTrue((target / "hooks.json").is_file())
            self.assertTrue((target / "skills" / "gravity-workflow" / "SKILL.md").is_file())
            self.assertTrue((target / "skills" / "caveman" / "SKILL.md").is_file())
            self.assertTrue((target / "hooks" / "session_start_caveman.py").is_file())
            for rel_policy in (
                "__init__.py",
                "constants.py",
                "paths.py",
                "shell_parser.py",
                "git_and_fs.py",
                "python_ast.py",
                "inline_scripts.py",
                "evaluator.py",
            ):
                self.assertTrue((target / "hooks" / "policy" / rel_policy).is_file())
            self.assertEqual(list(target.rglob("__pycache__")), [])

            isolated_ws = Path(tmp) / "isolated-ws"
            isolated_ws.mkdir()
            guard_proc = subprocess.run(
                [sys.executable, "-B", str(target / "hooks" / "pre_tool_guard.py")],
                cwd=str(target),
                input=json.dumps(
                    {
                        "cwd": str(isolated_ws),
                        "toolCall": {"name": "read_file", "args": {"AbsolutePath": ".env"}},
                    }
                ),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(guard_proc.returncode, 0)
            self.assertEqual(json.loads(guard_proc.stdout).get("decision"), "deny")

            if os.name == "nt" and not install.has_real_python3():
                installed_hooks = (target / "hooks.json").read_text(encoding="utf-8")
                self.assertNotIn('"python3 ', installed_hooks)
                self.assertIn("hooks/session_start_caveman.py", installed_hooks)

        source_hooks_after = (install.PLUGIN_SOURCE / "hooks.json").read_text(encoding="utf-8")
        self.assertEqual(source_hooks_before, source_hooks_after)
        self.assertIn("python3 hooks/pre_tool_guard.py", source_hooks_after)
        self.assertIn("python3 hooks/session_start_caveman.py", source_hooks_after)

    def test_reinstall_requires_force_and_force_upgrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "gravity-workflow"
            self.assertEqual(_run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0)

            marker = target / "stale_marker.txt"
            marker.write_text("old-install", encoding="utf-8")

            self.assertNotEqual(_run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0)
            self.assertTrue(marker.is_file())

            self.assertEqual(
                _run_quiet(["--target", str(target), "--force", "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertFalse(marker.exists())
            self.assertTrue((target / "plugin.json").is_file())

    def test_rollback_on_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "gravity-workflow"
            self.assertEqual(_run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0)

            marker = target / "existing_marker.txt"
            marker.write_text("preserve-me", encoding="utf-8")

            failed_proc = subprocess.CompletedProcess(
                args=["validate.py"],
                returncode=1,
                stdout="",
                stderr="Simulated validation failure",
            )
            with mock.patch.object(install, "run_plugin_validation", return_value=failed_proc):
                rc = _run_quiet(["--target", str(target), "--force"])
                self.assertNotEqual(rc, 0)

            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve-me")
            leftovers = [
                p.name
                for p in parent.iterdir()
                if p.name.startswith(".gravity-workflow-stage-")
                or p.name.startswith(".gravity-workflow-backup-")
            ]
            self.assertEqual(leftovers, [])

    def test_upgrade_in_place_fallback_when_directory_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "gravity-workflow"
            self.assertEqual(
                _run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0
            )

            stale_file = target / "stale_marker.txt"
            stale_file.write_text("stale-root", encoding="utf-8")
            stale_sub = target / "stale_dir" / "old.txt"
            stale_sub.parent.mkdir(parents=True)
            stale_sub.write_text("stale-nested", encoding="utf-8")
            readme = target / "README.md"
            readme.write_text("OUTDATED CONTENT", encoding="utf-8")

            real_rename = os.rename

            def locked_rename(src: str, dst: str) -> None:
                if Path(src).resolve() == target.resolve():
                    err = PermissionError(13, "Directory locked by running process")
                    err.winerror = 32  # type: ignore[attr-defined]
                    raise err
                real_rename(src, dst)

            with mock.patch("install.os.rename", side_effect=locked_rename):
                rc = _run_quiet(["--target", str(target), "--force", "--skip-validate", "--no-fix-python"])
                self.assertEqual(rc, 0)

            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue((target / "hooks" / "pre_tool_guard.py").is_file())
            self.assertFalse(stale_file.exists())
            self.assertFalse(stale_sub.parent.exists())
            self.assertEqual(
                readme.read_bytes(),
                (install.PLUGIN_SOURCE / "README.md").read_bytes(),
            )
            leftovers = [
                p.name
                for p in parent.iterdir()
                if p.name.startswith(".gravity-workflow-stage-")
                or p.name.startswith(".gravity-workflow-backup-")
            ]
            self.assertEqual(leftovers, [])

            # Sur Windows, valider aussi avec un vrai verrou OS sur hooks/
            if os.name == "nt":
                stale_win = target / "stale_win.txt"
                stale_win.write_text("stale-win", encoding="utf-8")
                readme.write_text("OUTDATED WIN", encoding="utf-8")
                hooks_dir = target / "hooks"
                holder = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    cwd=str(hooks_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    rc_win = _run_quiet(
                        ["--target", str(target), "--force", "--skip-validate", "--no-fix-python"]
                    )
                    self.assertEqual(rc_win, 0)
                    self.assertTrue((target / "plugin.json").is_file())
                    self.assertTrue((hooks_dir / "pre_tool_guard.py").is_file())
                    self.assertFalse(stale_win.exists())
                    self.assertEqual(
                        readme.read_bytes(),
                        (install.PLUGIN_SOURCE / "README.md").read_bytes(),
                    )
                finally:
                    holder.terminate()
                    try:
                        holder.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        holder.kill()
                        holder.wait(timeout=5)

    def test_upgrade_rollback_and_locked_file_handling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "gravity-workflow"
            self.assertEqual(
                _run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0
            )

            marker = target / "keep_on_rollback.txt"
            marker.write_text("v-old", encoding="utf-8")

            # 1. Rollback sûr si le second rename (staged -> target) échoue après backup
            real_rename = os.rename
            call_count = {"n": 0}

            def fail_second_rename(src: str, dst: str) -> None:
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise OSError("Simulated move failure")
                real_rename(src, dst)

            with mock.patch("install.os.rename", side_effect=fail_second_rename):
                rc_rb = _run_quiet(["--target", str(target), "--force", "--skip-validate", "--no-fix-python"])
                self.assertEqual(rc_rb, 1)

            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_text(encoding="utf-8"), "v-old")

            # 2. En repli in-place : un fichier verrouillé identique est toléré, un fichier différent renvoie rc=1
            real_copy2 = install.shutil.copy2

            def deny_first_rename(src: str, dst: str) -> None:
                if Path(src).resolve() == target.resolve():
                    raise PermissionError(13, "Target locked")
                real_rename(src, dst)

            def locked_readme_copy2(src: str, dst: str) -> str:
                if Path(dst).name == "README.md":
                    raise PermissionError(13, "README.md locked")
                return real_copy2(src, dst)

            # Identique -> succès
            with mock.patch("install.os.rename", side_effect=deny_first_rename), mock.patch(
                "install.shutil.copy2", side_effect=locked_readme_copy2
            ):
                rc_same = _run_quiet(["--target", str(target), "--force", "--skip-validate", "--no-fix-python"])
                self.assertEqual(rc_same, 0)

            # Différent -> échec propre (rc=1) avec rollback in-place complet (pas d'état mixte)
            old_manifest = '{"name": "gravity-workflow", "version": "0.0.1"}\n'
            (target / "plugin.json").write_text(old_manifest, encoding="utf-8")
            (target / "README.md").write_text("DIFFERENT", encoding="utf-8")
            stale_keep = target / "stale_preserve_on_fail.txt"
            stale_keep.write_text("keep-me", encoding="utf-8")
            (target / "hooks" / "hook_runtime.py").unlink()
            with mock.patch("install.os.rename", side_effect=deny_first_rename), mock.patch(
                "install.shutil.copy2", side_effect=locked_readme_copy2
            ):
                rc_diff = _run_quiet(["--target", str(target), "--force", "--skip-validate", "--no-fix-python"])
                self.assertEqual(rc_diff, 1)
            self.assertEqual((target / "plugin.json").read_text(encoding="utf-8"), old_manifest)
            self.assertEqual((target / "README.md").read_text(encoding="utf-8"), "DIFFERENT")
            self.assertTrue(stale_keep.is_file())
            self.assertFalse((target / "hooks" / "hook_runtime.py").exists())
            leftovers = [
                p.name
                for p in parent.iterdir()
                if p.name.startswith(".gravity-workflow-")
            ]
            self.assertEqual(leftovers, [])

    def test_refuses_non_gravity_dir_and_source_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            plain_dir = base / "plain"
            plain_dir.mkdir()
            keep_file = plain_dir / "keep.txt"
            keep_file.write_text("safe", encoding="utf-8")
            self.assertNotEqual(_run_quiet(["--target", str(plain_dir), "--force"]), 0)
            self.assertNotEqual(_run_quiet(["--uninstall", "--target", str(plain_dir)]), 0)
            self.assertTrue(keep_file.is_file())

            other_plugin = base / "other-plugin"
            other_plugin.mkdir()
            other_manifest = other_plugin / "plugin.json"
            other_manifest.write_text('{"name": "other"}', encoding="utf-8")
            self.assertNotEqual(_run_quiet(["--target", str(other_plugin), "--force"]), 0)
            self.assertNotEqual(_run_quiet(["--uninstall", "--target", str(other_plugin)]), 0)
            self.assertTrue(other_manifest.is_file())

            self.assertNotEqual(
                _run_quiet(["--target", str(install.PLUGIN_SOURCE), "--force"]), 0
            )
            self.assertNotEqual(
                _run_quiet(["--uninstall", "--target", str(install.PLUGIN_SOURCE)]), 0
            )
            self.assertTrue((install.PLUGIN_SOURCE / "plugin.json").is_file())

    def test_workspace_scope_install(self) -> None:
        self.assertEqual(_run_quiet(["--scope", "workspace"]), 2)

        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "my-project"
            ws.mkdir()
            rc = _run_quiet(["--scope", "workspace", "--workspace", str(ws), "--skip-validate", "--no-fix-python"])
            self.assertEqual(rc, 0)
            expected = ws / ".agents" / "plugins" / "gravity-workflow"
            self.assertTrue((expected / "plugin.json").is_file())

    def test_legacy_gravity_plugin_cleanup_and_keep_legacy_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / "plugins"
            plugins_dir.mkdir()
            target = plugins_dir / "gravity-workflow"
            legacy_dir = plugins_dir / "gravity"
            (legacy_dir / "hooks").mkdir(parents=True)
            (legacy_dir / "plugin.json").write_text(
                json.dumps({"name": "gravity", "version": "0.1.3"}),
                encoding="utf-8",
            )
            (legacy_dir / "hooks" / "pre_tool_guard.py").write_text("# legacy hook\n", encoding="utf-8")

            # --check et --dry-run ne suppriment rien
            self.assertEqual(_run_quiet(["--target", str(target), "--check"]), 0)
            self.assertEqual(_run_quiet(["--target", str(target), "--dry-run"]), 0)
            self.assertTrue(legacy_dir.is_dir())

            # --keep-legacy préserve l'ancien plugin sibling
            self.assertEqual(
                _run_quiet(["--target", str(target), "--keep-legacy", "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue(legacy_dir.is_dir())

            # Installation par défaut (ou --force) supprime proprement l'ancien plugin 'gravity' workflow
            self.assertEqual(
                _run_quiet(["--target", str(target), "--force", "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertTrue((target / "plugin.json").is_file())
            self.assertFalse(legacy_dir.exists())

            # Ne jamais supprimer un dossier 'gravity' tiers dont le manifeste n'est pas name=='gravity'
            foreign_dir = plugins_dir / "gravity"
            (foreign_dir / "hooks").mkdir(parents=True)
            (foreign_dir / "plugin.json").write_text(
                json.dumps({"name": "other-plugin", "version": "1.0.0"}),
                encoding="utf-8",
            )
            (foreign_dir / "hooks" / "pre_tool_guard.py").write_text("# foreign\n", encoding="utf-8")
            self.assertEqual(
                _run_quiet(["--target", str(target), "--force", "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertTrue(foreign_dir.is_dir())

            # En portée workspace, nettoie également .agents/plugins/gravity si marqueurs workflow hérités
            ws = base / "ws"
            ws_legacy = ws / ".agents" / "plugins" / "gravity"
            (ws_legacy / "skills" / "gravity").mkdir(parents=True)
            (ws_legacy / "plugin.json").write_text(
                json.dumps({"name": "gravity", "version": "0.1.3"}),
                encoding="utf-8",
            )
            (ws_legacy / "skills" / "gravity" / "SKILL.md").write_text(
                "---\nname: gravity\n---\n", encoding="utf-8"
            )
            self.assertEqual(
                _run_quiet(["--scope", "workspace", "--workspace", str(ws), "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertTrue((ws / ".agents" / "plugins" / "gravity-workflow" / "plugin.json").is_file())
            self.assertFalse(ws_legacy.exists())

    def test_leaves_gravity_core_plugin_untouched_and_ignores_unmarked_gravity_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / "plugins"
            plugins_dir.mkdir()
            target = plugins_dir / "gravity-workflow"

            # 1. Nouveau plugin Core 'gravity' (avec config/module.json id='core' ET skills/gravity/)
            core_dir = plugins_dir / "gravity"
            (core_dir / "config").mkdir(parents=True)
            (core_dir / "skills" / "gravity").mkdir(parents=True)
            core_manifest = json.dumps({"name": "gravity", "version": "0.1.0"})
            core_module = json.dumps({"schema_version": 1, "id": "core", "plugin_name": "gravity"})
            core_skill = "---\nname: gravity\ndescription: Gravity Core\n---\n"
            (core_dir / "plugin.json").write_text(core_manifest, encoding="utf-8")
            (core_dir / "config" / "module.json").write_text(core_module, encoding="utf-8")
            (core_dir / "skills" / "gravity" / "SKILL.md").write_text(core_skill, encoding="utf-8")

            self.assertFalse(install.is_legacy_gravity_plugin_dir(core_dir))
            self.assertTrue(install.is_core_gravity_plugin_dir(core_dir))
            self.assertEqual(
                _run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertEqual(
                _run_quiet(["--target", str(target), "--force", "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue((core_dir / "config" / "module.json").is_file())
            self.assertEqual((core_dir / "config" / "module.json").read_text(encoding="utf-8"), core_module)
            self.assertEqual((core_dir / "skills" / "gravity" / "SKILL.md").read_text(encoding="utf-8"), core_skill)

            # 2. Portée workspace : un plugin Core 'gravity' dans .agents/plugins/gravity reste intact
            ws = base / "ws"
            ws_core = ws / ".agents" / "plugins" / "gravity"
            (ws_core / "config").mkdir(parents=True)
            (ws_core / "skills" / "gravity").mkdir(parents=True)
            (ws_core / "plugin.json").write_text(core_manifest, encoding="utf-8")
            (ws_core / "config" / "module.json").write_text(core_module, encoding="utf-8")
            (ws_core / "skills" / "gravity" / "SKILL.md").write_text(core_skill, encoding="utf-8")
            self.assertEqual(
                _run_quiet(["--scope", "workspace", "--workspace", str(ws), "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertTrue((ws_core / "config" / "module.json").is_file())

            # 3. Dossier 'gravity' sans module.json ni marqueurs workflow -> intact
            unmarked_dir = base / "unmarked_plugins" / "gravity"
            unmarked_dir.mkdir(parents=True)
            (unmarked_dir / "plugin.json").write_text(core_manifest, encoding="utf-8")
            unmarked_target = base / "unmarked_plugins" / "gravity-workflow"
            self.assertEqual(
                _run_quiet(["--target", str(unmarked_target), "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertTrue((unmarked_dir / "plugin.json").is_file())

    def test_init_workspace_preserves_existing_agents_md_even_with_plugin_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "gravity-workflow"
            ws = base / "project"
            ws.mkdir()
            custom_agents = "# Custom Project AGENTS\nDo not overwrite me.\n"
            (ws / "AGENTS.md").write_text(custom_agents, encoding="utf-8", newline="\n")

            self.assertEqual(
                _run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0
            )
            rc = _run_quiet(
                [
                    "--target",
                    str(target),
                    "--force",
                    "--skip-validate",
                    "--no-fix-python",
                    "--init-workspace",
                    str(ws),
                    "--mode",
                    "quickstart",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertEqual((ws / "AGENTS.md").read_text(encoding="utf-8"), custom_agents)
            self.assertTrue((ws / ".agents" / "gravity-workflow" / "PROGRESS.md").is_file())

            rc_force_ws = _run_quiet(
                [
                    "--target",
                    str(target),
                    "--force",
                    "--skip-validate",
                    "--no-fix-python",
                    "--init-workspace",
                    str(ws),
                    "--mode",
                    "quickstart",
                    "--force-workspace",
                ]
            )
            self.assertEqual(rc_force_ws, 0)
            self.assertNotEqual((ws / "AGENTS.md").read_text(encoding="utf-8"), custom_agents)

    def test_dry_run_and_check_do_not_mutate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "uncreated" / "gravity-workflow"
            self.assertEqual(_run_quiet(["--target", str(target), "--dry-run"]), 0)
            self.assertFalse(target.parent.exists())

            rc_dry_strict, out_dry_strict, _ = _run_capture(
                ["--target", str(target), "--dry-run", "--strict", "--skip-validate"]
            )
            self.assertEqual(rc_dry_strict, 0)
            self.assertIn("validate.py --strict", out_dry_strict)
            self.assertFalse(target.parent.exists())

            self.assertEqual(_run_quiet(["--target", str(target), "--check"]), 0)
            self.assertFalse(target.parent.exists())

            ok_proc = subprocess.CompletedProcess(
                args=["validate.py"], returncode=0, stdout="[ok]\n", stderr=""
            )
            with mock.patch.object(
                install, "run_plugin_validation", return_value=ok_proc
            ) as mock_val:
                rc_chk_strict, out_chk_strict, _ = _run_capture(
                    ["--target", str(target), "--check", "--strict"]
                )
            mock_val.assert_called_once_with(install.PLUGIN_SOURCE)
            self.assertEqual(rc_chk_strict, 0)
            self.assertIn("Strict validation: PASSED", out_chk_strict)
            self.assertFalse(target.parent.exists())

    def test_uninstall_removes_installed_gravity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "gravity-workflow"
            self.assertEqual(
                _run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0
            )
            self.assertTrue(target.is_dir())

            self.assertEqual(_run_quiet(["--uninstall", "--target", str(target)]), 0)
            self.assertFalse(target.exists())

            self.assertNotEqual(_run_quiet(["--uninstall", "--target", str(target)]), 0)

    def test_uninstall_cleanup_flags_via_install_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "gravity-workflow"
            ws = base / "proj"
            other_ws = base / "other-proj"
            other_ws.mkdir()

            (self._fake_home / ".gemini" / "config").mkdir(parents=True, exist_ok=True)
            (self._fake_home / ".gemini" / "antigravity-cli").mkdir(parents=True, exist_ok=True)
            (self._fake_home / ".gemini" / "config" / "config.json").write_text(
                json.dumps({"sidecars": {"gravity-workflow/live-widget": {"enabled": True}}}),
                encoding="utf-8",
            )
            (self._fake_home / ".gemini" / "antigravity-cli" / "settings.json").write_text(
                json.dumps({"statusLine": {"command": "python3 .../statusline.py"}}),
                encoding="utf-8",
            )

            self.assertEqual(
                _run_quiet(
                    [
                        "--target",
                        str(target),
                        "--skip-validate",
                        "--no-fix-python",
                        "--init-workspace",
                        str(ws),
                        "--mode",
                        "full",
                    ]
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
                                "state_path": str((ws / ".agents/gravity-workflow/observability/live_state.json").resolve()),
                            },
                            {
                                "workspace": str(other_ws.resolve()),
                                "state_path": str((other_ws / ".agents/gravity-workflow/observability/live_state.json").resolve()),
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            # --uninstall --check (read-only)
            rc_chk, out_chk, _ = _run_capture(
                ["--uninstall", "--check", "--target", str(target), "--clean-workspace", str(ws)]
            )
            self.assertEqual(rc_chk, 0)
            self.assertIn("Gravity Workflow Uninstall Check", out_chk)
            self.assertIn("detected (manual removal needed)", out_chk)
            self.assertTrue((target / "plugin.json").is_file())

            # --workspace-only --check (read-only)
            rc_wschk, out_wschk, _ = _run_capture(
                ["--workspace-only", "--check", "--clean-workspace", str(ws)]
            )
            self.assertEqual(rc_wschk, 0)
            self.assertIn("Gravity Workflow Uninstall Check", out_wschk)

            # --uninstall --dry-run --all (no mutation)
            rc_dry, out_dry, _ = _run_capture(
                [
                    "--uninstall",
                    "--dry-run",
                    "--all",
                    "--target",
                    str(target),
                    "--clean-workspace",
                    str(ws),
                    "--purge-workspace-templates",
                ]
            )
            self.assertEqual(rc_dry, 0)
            self.assertIn("[DRY-RUN]", out_dry)
            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue((ws / ".agents/gravity-workflow/profile.json").is_file())
            self.assertTrue(self._default_reg.is_file())

            # --workspace-only --clean-workspace keeps user files & other registry entry
            (ws / "AGENTS.md").write_text("# Custom Project AGENTS\n", encoding="utf-8")
            rc_ws, out_ws, _ = _run_capture(
                [
                    "--workspace-only",
                    "--clean-workspace",
                    str(ws),
                    "--purge-workspace-templates",
                ]
            )
            self.assertEqual(rc_ws, 0)
            self.assertIn("[KEEP]  AGENTS.md", out_ws)
            self.assertFalse((ws / ".agents/gravity-workflow").exists())
            self.assertFalse((ws / ".agents/rules/01-safety-boundaries.md").exists())
            self.assertTrue((ws / "AGENTS.md").is_file())
            self.assertTrue((target / "plugin.json").is_file())
            reg_data = json.loads(self._default_reg.read_text(encoding="utf-8"))
            self.assertEqual(len(reg_data["workspaces"]), 1)
            self.assertEqual(reg_data["workspaces"][0]["workspace"], str(other_ws.resolve()))

            # --workspace-only --clean-registry
            self._default_reg.with_suffix(".lock").write_text("0", encoding="utf-8")
            self.assertEqual(_run_quiet(["--workspace-only", "--clean-registry"]), 0)
            self.assertFalse(self._default_reg.exists())
            self.assertFalse(self._default_reg.with_suffix(".lock").exists())

            # --uninstall --all
            self._default_reg.parent.mkdir(parents=True, exist_ok=True)
            self._default_reg.write_text('{"workspaces":[]}', encoding="utf-8")
            self._default_leg_reg.parent.mkdir(parents=True, exist_ok=True)
            self._default_leg_reg.write_text('{"workspaces":[]}', encoding="utf-8")
            self.assertEqual(
                _run_quiet(["--uninstall", "--all", "--target", str(target), "--clean-workspace", str(ws)]),
                0,
            )
            self.assertFalse(target.exists())
            self.assertFalse(self._default_reg.exists())
            self.assertFalse(self._default_leg_reg.exists())

    def test_uninstall_clean_legacy_preserves_gravity_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / "plugins"
            target = plugins_dir / "gravity-workflow"
            self.assertEqual(
                _run_quiet(["--target", str(target), "--skip-validate", "--no-fix-python"]), 0
            )

            core_plugin = plugins_dir / "gravity"
            (core_plugin / "config").mkdir(parents=True)
            (core_plugin / "skills" / "gravity").mkdir(parents=True)
            (core_plugin / "plugin.json").write_text(
                json.dumps({"name": "gravity", "version": "0.1.0"}), encoding="utf-8"
            )
            (core_plugin / "config" / "module.json").write_text(
                json.dumps({"schema_version": 1, "id": "core", "plugin_name": "gravity"}),
                encoding="utf-8",
            )
            (core_plugin / "skills" / "gravity" / "SKILL.md").write_text("---\nname: gravity\n---\n", encoding="utf-8")

            ws = base / "ws"
            (ws / ".agents" / "gravity").mkdir(parents=True)
            (ws / ".agents" / "gravity" / "profile.json").write_text("{}", encoding="utf-8")
            ws_core = ws / ".agents" / "plugins" / "gravity"
            (ws_core / "config").mkdir(parents=True)
            (ws_core / "skills" / "gravity").mkdir(parents=True)
            (ws_core / "plugin.json").write_text(
                json.dumps({"name": "gravity", "version": "0.1.0"}), encoding="utf-8"
            )
            (ws_core / "config" / "module.json").write_text(
                json.dumps({"schema_version": 1, "id": "core", "plugin_name": "gravity"}),
                encoding="utf-8",
            )

            self._default_leg_reg.parent.mkdir(parents=True, exist_ok=True)
            self._default_leg_reg.write_text('{"workspaces":[]}', encoding="utf-8")

            rc = _run_quiet(
                ["--uninstall", "--clean-legacy", "--target", str(target), "--clean-workspace", str(ws)]
            )
            self.assertEqual(rc, 0)
            self.assertFalse(target.exists())
            self.assertFalse((ws / ".agents" / "gravity").exists())
            self.assertFalse(self._default_leg_reg.exists())
            self.assertTrue((core_plugin / "config" / "module.json").is_file())
            self.assertTrue((ws_core / "config" / "module.json").is_file())

    def test_uninstall_cli_argument_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "proj"
            ws.mkdir()
            self.assertEqual(_run_quiet(["--uninstall", "--init-workspace", str(ws)]), 2)
            self.assertEqual(
                _run_quiet(["--workspace-only", "--clean-workspace", str(ws), "--init-workspace", str(ws)]),
                2,
            )
            self.assertEqual(_run_quiet(["--workspace-only"]), 2)
            self.assertEqual(_run_quiet(["--workspace-only", "--all"]), 2)
            self.assertEqual(_run_quiet(["--uninstall", "--purge-workspace-templates"]), 2)
            self.assertEqual(_run_quiet(["--uninstall", "--scope", "workspace"]), 2)
            self.assertEqual(_run_quiet(["--clean-workspace", str(ws)]), 2)
            self.assertEqual(_run_quiet(["--clean-registry"]), 2)
            self.assertEqual(_run_quiet(["--clean-legacy"]), 2)
            self.assertEqual(_run_quiet(["--all"]), 2)

    def test_method_agy_validation_and_delegation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "proj"
            ws.mkdir()
            self.assertEqual(
                _run_quiet(["--method", "agy", "--scope", "workspace", "--workspace", str(ws)]),
                2,
            )
            self.assertEqual(_run_quiet(["--method", "agy", "--target", str(tmp)]), 2)

        calls: List[List[str]] = []

        def fake_run(cmd: List[str], **_kwargs: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[ok]\n", stderr="")

        with mock.patch("shutil.which", return_value="/usr/local/bin/agy"), mock.patch(
            "subprocess.run", side_effect=fake_run
        ):
            rc = _run_quiet(["--method", "agy"])
            self.assertEqual(rc, 0)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:3], ["/usr/local/bin/agy", "plugin", "validate"])
        self.assertEqual(calls[1][:3], ["/usr/local/bin/agy", "plugin", "install"])
        self.assertEqual(Path(calls[0][3]), install.PLUGIN_SOURCE)
        self.assertEqual(Path(calls[1][3]), install.PLUGIN_SOURCE)

    def test_stdlib_only_ast(self) -> None:
        stdlib_names = getattr(sys, "stdlib_module_names", None)
        if not stdlib_names:
            self.skipTest("sys.stdlib_module_names requires Python 3.10+")

        allowed = set(stdlib_names) | {"install"}
        for py_path in (REPO_ROOT / "install.py", Path(__file__).resolve()):
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
