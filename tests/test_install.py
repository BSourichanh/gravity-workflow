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


class TestGravityInstaller(unittest.TestCase):
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
            self.assertEqual(list(target.rglob("__pycache__")), [])

            if os.name == "nt" and not install.has_real_python3():
                installed_hooks = (target / "hooks.json").read_text(encoding="utf-8")
                self.assertNotIn('"python3 ', installed_hooks)
                self.assertIn("hooks/session_start_caveman.py", installed_hooks)
                sidecar_cfg = json.loads(
                    (target / "sidecars" / "live-widget" / "sidecar.json").read_text(encoding="utf-8")
                )
                self.assertNotEqual(sidecar_cfg.get("command"), "python3")

        source_hooks_after = (install.PLUGIN_SOURCE / "hooks.json").read_text(encoding="utf-8")
        self.assertEqual(source_hooks_before, source_hooks_after)
        self.assertIn("python3 hooks/pre_tool_guard.py", source_hooks_after)
        self.assertIn("python3 hooks/session_start_caveman.py", source_hooks_after)

    def test_reinstall_requires_force_and_force_upgrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "gravity-workflow"
            self.assertEqual(_run_quiet(["--target", str(target)]), 0)

            marker = target / "stale_marker.txt"
            marker.write_text("old-install", encoding="utf-8")

            self.assertNotEqual(_run_quiet(["--target", str(target)]), 0)
            self.assertTrue(marker.is_file())

            self.assertEqual(_run_quiet(["--target", str(target), "--force"]), 0)
            self.assertFalse(marker.exists())
            self.assertTrue((target / "plugin.json").is_file())

    def test_rollback_on_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "gravity-workflow"
            self.assertEqual(_run_quiet(["--target", str(target)]), 0)

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
            self.assertEqual(_run_quiet(["--target", str(target), "--skip-validate"]), 0)

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
                    err = PermissionError(13, "Directory locked by running sidecar")
                    err.winerror = 32  # type: ignore[attr-defined]
                    raise err
                real_rename(src, dst)

            with mock.patch("install.os.rename", side_effect=locked_rename):
                rc = _run_quiet(["--target", str(target), "--force"])
                self.assertEqual(rc, 0)

            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue((target / "hooks" / "pre_tool_guard.py").is_file())
            self.assertTrue((target / "sidecars" / "live-widget" / "server.py").is_file())
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

            # Sur Windows, valider aussi avec un vrai verrou OS sur sidecars/live-widget
            if os.name == "nt":
                stale_win = target / "stale_win.txt"
                stale_win.write_text("stale-win", encoding="utf-8")
                readme.write_text("OUTDATED WIN", encoding="utf-8")
                sidecar_dir = target / "sidecars" / "live-widget"
                holder = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    cwd=str(sidecar_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    rc_win = _run_quiet(["--target", str(target), "--force", "--skip-validate"])
                    self.assertEqual(rc_win, 0)
                    self.assertTrue((target / "plugin.json").is_file())
                    self.assertTrue((sidecar_dir / "server.py").is_file())
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
            self.assertEqual(_run_quiet(["--target", str(target), "--skip-validate"]), 0)

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
                rc_rb = _run_quiet(["--target", str(target), "--force", "--skip-validate"])
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
                rc_same = _run_quiet(["--target", str(target), "--force", "--skip-validate"])
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
                rc_diff = _run_quiet(["--target", str(target), "--force", "--skip-validate"])
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
            rc = _run_quiet(["--scope", "workspace", "--workspace", str(ws)])
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
                _run_quiet(["--target", str(target), "--keep-legacy", "--skip-validate"]), 0
            )
            self.assertTrue((target / "plugin.json").is_file())
            self.assertTrue(legacy_dir.is_dir())

            # Installation par défaut (ou --force) supprime proprement l'ancien plugin 'gravity' workflow
            self.assertEqual(_run_quiet(["--target", str(target), "--force", "--skip-validate"]), 0)
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
            self.assertEqual(_run_quiet(["--target", str(target), "--force", "--skip-validate"]), 0)
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
                _run_quiet(["--scope", "workspace", "--workspace", str(ws), "--skip-validate"]), 0
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
            self.assertEqual(_run_quiet(["--target", str(target), "--skip-validate"]), 0)
            self.assertEqual(_run_quiet(["--target", str(target), "--force", "--skip-validate"]), 0)
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
                _run_quiet(["--scope", "workspace", "--workspace", str(ws), "--skip-validate"]), 0
            )
            self.assertTrue((ws_core / "config" / "module.json").is_file())

            # 3. Dossier 'gravity' sans module.json ni marqueurs workflow -> intact
            unmarked_dir = base / "unmarked_plugins" / "gravity"
            unmarked_dir.mkdir(parents=True)
            (unmarked_dir / "plugin.json").write_text(core_manifest, encoding="utf-8")
            unmarked_target = base / "unmarked_plugins" / "gravity-workflow"
            self.assertEqual(_run_quiet(["--target", str(unmarked_target), "--skip-validate"]), 0)
            self.assertTrue((unmarked_dir / "plugin.json").is_file())

    def test_init_workspace_preserves_existing_agents_md_even_with_plugin_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "gravity-workflow"
            ws = base / "project"
            ws.mkdir()
            custom_agents = "# Custom Project AGENTS\nDo not overwrite me.\n"
            (ws / "AGENTS.md").write_text(custom_agents, encoding="utf-8")

            self.assertEqual(_run_quiet(["--target", str(target)]), 0)
            rc = _run_quiet(
                [
                    "--target",
                    str(target),
                    "--force",
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

            self.assertEqual(_run_quiet(["--target", str(target), "--check"]), 0)
            self.assertFalse(target.parent.exists())

    def test_uninstall_removes_installed_gravity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "gravity-workflow"
            self.assertEqual(_run_quiet(["--target", str(target)]), 0)
            self.assertTrue(target.is_dir())

            self.assertEqual(_run_quiet(["--uninstall", "--target", str(target)]), 0)
            self.assertFalse(target.exists())

            self.assertNotEqual(_run_quiet(["--uninstall", "--target", str(target)]), 0)

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
