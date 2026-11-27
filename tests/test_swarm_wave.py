#!/usr/bin/env python3
"""Tests unitaires pour `plugin/workflows/swarm_wave.py` (100 % stdlib)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _unit_helpers import WorkflowEnvTestCase, ensure_plugin_paths, make_gravity_workspace

PLUGIN_ROOT = ensure_plugin_paths()
WORKFLOWS_DIR = str((PLUGIN_ROOT / "workflows").resolve())
if WORKFLOWS_DIR not in sys.path:
    sys.path.insert(0, WORKFLOWS_DIR)

import swarm_wave  # noqa: E402
import worktree_ops  # noqa: E402


def _init_git_repo(repo_dir: Path, files: dict, branch: str = "dev") -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        fpath = repo_dir / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8", newline="\n")
    for cmd in (
        ["git", "init", "-q", "-b", branch],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "init"],
    ):
        proc = subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True, check=False)
        if proc.returncode != 0 and cmd[:2] == ["git", "init"]:
            subprocess.run(["git", "init", "-q"], cwd=str(repo_dir), check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=str(repo_dir), check=True, capture_output=True)
        elif proc.returncode != 0:
            raise AssertionError(f"Git setup failed {cmd}: {proc.stderr}")


class TestSwarmWave(WorkflowEnvTestCase):
    def _make_wave_fixture(self, base: Path) -> tuple:
        ws = make_gravity_workspace(base, "atelier_ws")
        wt_base = base / "worktrees"
        repo_a = base / "repo-alpha"
        repo_b = base / "repo-beta"
        _init_git_repo(repo_a, {"app.py": "print('a')\n"}, branch="dev")
        _init_git_repo(repo_b, {"lib.py": "print('b')\n"}, branch="dev")

        plan_a = ws / "tasks" / "t-alpha.md"
        plan_b = ws / "tasks" / "t-beta.md"
        plan_a.parent.mkdir(parents=True, exist_ok=True)
        plan_a.write_text("# Task Alpha\nSECRET_INLINE_MARKER_ALPHA\n", encoding="utf-8", newline="\n")
        plan_b.write_text("# Task Beta\nSECRET_INLINE_MARKER_BETA\n", encoding="utf-8", newline="\n")

        wave_plan = {
            "schema_version": "wave_plan_v1",
            "wave": "w4-test",
            "workspace": str(ws),
            "tasks": [
                {
                    "id": "task-alpha",
                    "repo_root": str(repo_a),
                    "expected_branch": "dev",
                    "plan_file": str(plan_a),
                    "status": "ready",
                },
                {
                    "id": "task-beta",
                    "repo_root": str(repo_b),
                    "expected_branch": "dev",
                    "plan_file": str(plan_b),
                    "status": "ready",
                },
            ],
        }
        plan_json = ws / "wave_plan.json"
        plan_json.write_text(json.dumps(wave_plan), encoding="utf-8", newline="\n")
        return ws, wt_base, repo_a, repo_b, plan_json, plan_a, plan_b

    def test_prepare_briefs_topology_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws, wt_base, repo_a, _, plan_json, plan_a, _ = self._make_wave_fixture(base)

            res_quick = swarm_wave.prepare_wave(
                plan_json, workspace_root=ws, worktree_base=wt_base, level_override="quick"
            )
            self.assertEqual(res_quick["execution_engine"], "swarm")
            self.assertEqual(len(res_quick["tasks"]), 2)
            self.assertEqual(
                res_quick["reviewer_topology"],
                {"mode": "shared", "count": 1, "model": "flash", "monitor": False, "fork": False},
            )
            for tid, brief in res_quick["builder_briefs"].items():
                self.assertLessEqual(len(brief), 700, f"Brief trop long pour {tid}: {len(brief)}")
                self.assertNotIn("SECRET_INLINE_MARKER", brief)
                self.assertIn("git commit/push/tag", brief)
                self.assertIn("[Caveman:", brief)
                wt_dir = Path(res_quick["worktrees"][tid])
                self.assertTrue(wt_dir.is_dir())
                head_ref = subprocess.run(
                    ["git", "-C", str(wt_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                self.assertEqual(head_ref, "HEAD")

            swarm_wave.cleanup_wave(res_quick["state_file"], remove_state=True)

            res_std = swarm_wave.prepare_wave(
                plan_json, workspace_root=ws, worktree_base=wt_base, level_override="standard"
            )
            self.assertEqual(
                res_std["reviewer_topology"],
                {"mode": "per_task", "count": 2, "model": "pro", "monitor": False, "fork": False},
            )
            swarm_wave.cleanup_wave(res_std["state_file"], remove_state=True)

            # allow_pro=false -> standard falls back to flash
            (ws / ".agents/gravity-workflow/profile.json").write_text(
                json.dumps({"allow_pro": False}), encoding="utf-8"
            )
            res_no_pro = swarm_wave.prepare_wave(
                plan_json, workspace_root=ws, worktree_base=wt_base, level_override="standard"
            )
            self.assertEqual(res_no_pro["reviewer_topology"]["model"], "flash")
            swarm_wave.cleanup_wave(res_no_pro["state_file"], remove_state=True)

            # Fail-closed: wrong expected_branch
            subprocess.run(["git", "-C", str(repo_a), "checkout", "-q", "-b", "other"], check=True)
            with self.assertRaises(ValueError):
                swarm_wave.prepare_wave(plan_json, workspace_root=ws, worktree_base=wt_base)
            subprocess.run(["git", "-C", str(repo_a), "checkout", "-q", "dev"], check=True)

            # Fail-closed: invalid schema_version and blocked status
            with self.assertRaises(ValueError):
                swarm_wave.prepare_wave({"schema_version": "bad", "tasks": []}, workspace_root=ws)
            with self.assertRaises(ValueError):
                swarm_wave.prepare_wave(
                    {
                        "schema_version": "wave_plan_v1",
                        "tasks": [{"id": "x", "repo_root": str(repo_a), "plan_file": str(plan_a), "status": "draft"}],
                    },
                    workspace_root=ws,
                )

    def test_prepare_cleans_created_worktrees_on_preset_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws, wt_base, _, _, plan_json, _, _ = self._make_wave_fixture(base)

            with mock.patch.object(swarm_wave, "load_level_preset_for_ws", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    swarm_wave.prepare_wave(plan_json, workspace_root=ws, worktree_base=wt_base)

            remaining = [p for p in wt_base.iterdir() if p.is_dir()] if wt_base.exists() else []
            self.assertEqual(remaining, [], f"Worktrees non nettoyés après échec: {remaining}")

    def test_prepare_snap_linux_fallback_uses_non_hidden_home_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_home = base / "fake_home"
            fake_home.mkdir()
            ws, _, repo_a, _, _, plan_a, _ = self._make_wave_fixture(base)
            snap_plan = {
                "schema_version": "wave_plan_v1",
                "wave": "w-snap",
                "workspace": str(ws),
                "tasks": [
                    {
                        "id": "task-snap",
                        "target_project": "repo-alpha",
                        "repo_root": str(repo_a),
                        "expected_branch": "dev",
                        "plan_file": str(plan_a),
                        "code_files": ["app.py"],
                        "status": "ready",
                    }
                ],
            }
            with mock.patch("pathlib.Path.home", return_value=fake_home), mock.patch.object(
                worktree_ops.sys, "platform", "linux"
            ), mock.patch.object(worktree_ops, "_is_snap_agy", return_value=True), mock.patch.dict(
                os.environ, {}, clear=False
            ):
                for key in ("GRAVITY_WORKTREE_BASE", "GRAVITY_WORKFLOW_HOME", "GRAVITY_HOME"):
                    os.environ.pop(key, None)
                prep = swarm_wave.prepare_wave(snap_plan, workspace_root=ws)
                try:
                    expected_base = (fake_home / "gravity-worktrees").resolve()
                    self.assertEqual(Path(prep["worktree_base"]), expected_base)
                    self.assertEqual(prep["tasks"][0]["target_project"], "repo-alpha")
                    self.assertEqual(prep["tasks"][0]["target_files"], ["app.py"])
                    wt_dir = Path(prep["tasks"][0]["worktree_path"])
                    self.assertEqual(wt_dir.parent, expected_base)
                    self.assertTrue(wt_dir.is_dir())
                finally:
                    swarm_wave.cleanup_wave(prep["state_file"], remove_state=True)

    def test_diff_untracked_sanitized_patch_apply_strict_id_branch_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws, wt_base, repo_a, repo_b, _, plan_a, plan_b = self._make_wave_fixture(base)
            evil_plan = {
                "schema_version": "wave_plan_v1",
                "wave": "w4-diff",
                "workspace": str(ws),
                "tasks": [
                    {
                        "id": "../evil/task-alpha",
                        "repo_root": str(repo_a),
                        "expected_branch": "dev",
                        "plan_file": str(plan_a),
                        "status": "ready",
                    },
                    {
                        "id": "task-beta",
                        "repo_root": str(repo_b),
                        "expected_branch": "dev",
                        "plan_file": str(plan_b),
                        "status": "ready",
                    },
                ],
            }
            prep = swarm_wave.prepare_wave(evil_plan, workspace_root=ws, worktree_base=wt_base)
            wt_a = Path(prep["worktrees"]["../evil/task-alpha"])
            wt_b = Path(prep["worktrees"]["task-beta"])

            # Untracked file + .gravity_verified.patch in wt_a + modified tracked file in wt_b
            (wt_a / "new_untracked.py").write_text("NEW_VAR = 42\n", encoding="utf-8", newline="\n")
            (wt_a / ".gravity_verified.patch").write_text("", encoding="utf-8", newline="\n")
            (wt_b / "lib.py").write_text("print('b_v2')\n", encoding="utf-8", newline="\n")

            diff_res = swarm_wave.extract_wave_diffs(prep["state_file"])
            patches_by_id = {d["id"]: d for d in diff_res["diffs"]}
            patch_a = Path(patches_by_id["../evil/task-alpha"]["patch_path"])
            self.assertTrue(patch_a.is_file())
            self.assertIn("swarm-wave-w4-diff-patches", str(patch_a.parent))
            self.assertNotIn("..", patch_a.name)
            self.assertIn("new_untracked.py", patches_by_id["../evil/task-alpha"]["changed_files"])
            self.assertNotIn(".gravity_verified.patch", patches_by_id["../evil/task-alpha"]["changed_files"])
            patch_a_text = patch_a.read_text(encoding="utf-8")
            self.assertIn("NEW_VAR = 42", patch_a_text)
            self.assertNotIn(".gravity_verified.patch", patch_a_text)
            self.assertNotIn("NEW_VAR = 42", diff_res["reviewer_briefs"]["../evil/task-alpha"])
            self.assertIn(".patch", diff_res["reviewer_briefs"]["../evil/task-alpha"])
            self.assertIn("Audite explicitement le fichier `.patch` extrait", diff_res["reviewer_briefs"]["../evil/task-alpha"])

            # Strict task ID matching: passing repo_name instead of task ID must fail
            with self.assertRaises(ValueError):
                swarm_wave.apply_wave(prep["state_file"], ["repo-alpha"])

            # Branch re-check on apply: switching repo_a away from dev must refuse apply
            subprocess.run(["git", "-C", str(repo_a), "checkout", "-q", "-b", "wrong"], check=True)
            bad_apply = swarm_wave.apply_wave(prep["state_file"], ["../evil/task-alpha"])
            self.assertFalse(bad_apply["all_applied"])
            self.assertFalse(bad_apply["results"][0]["applied"])
            self.assertFalse((repo_a / "new_untracked.py").exists())
            subprocess.run(["git", "-C", str(repo_a), "checkout", "-q", "dev"], check=True)

            # Selective PASS apply on task-alpha only; task-beta stays unapplied
            ok_apply = swarm_wave.apply_wave(prep["state_file"], ["../evil/task-alpha"])
            self.assertTrue(ok_apply["all_applied"])
            self.assertTrue((repo_a / "new_untracked.py").is_file())
            self.assertFalse(wt_a.exists())
            self.assertEqual((repo_b / "lib.py").read_text(encoding="utf-8"), "print('b')\n")

            # Idempotent cleanup
            c1 = swarm_wave.cleanup_wave(prep["state_file"])
            c2 = swarm_wave.cleanup_wave(prep["state_file"], remove_state=True)
            c3 = swarm_wave.cleanup_wave(prep["state_file"])
            self.assertFalse(wt_b.exists())
            self.assertFalse(c1["already_missing"])
            self.assertFalse(c2["already_missing"])
            self.assertTrue(c3["already_missing"])

    def test_atomic_io_rejects_symlinks_for_state_and_extracted_patches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws, wt_base, _, _, plan_json, _, _ = self._make_wave_fixture(base)
            target = base / "protected.txt"
            target.write_text("UNTOUCHED", encoding="utf-8")

            # 1. _save_state refuses symlink target
            sym_state = base / "sym_state.json"
            try:
                os.symlink(target, sym_state)
            except OSError:
                return
            with self.assertRaises(ValueError):
                swarm_wave._save_state(sym_state, {"tasks": []})
            self.assertEqual(target.read_text(encoding="utf-8"), "UNTOUCHED")

            # 2. extract_wave_diffs refuses symlinked .patch file
            prep = swarm_wave.prepare_wave(plan_json, workspace_root=ws, worktree_base=wt_base)
            try:
                wt_a = Path(prep["worktrees"]["task-alpha"])
                (wt_a / "app.py").write_text("print('updated')\n", encoding="utf-8", newline="\n")
                patches_dir = Path(prep["state_file"]).parent / "swarm-wave-w4-test-patches"
                patches_dir.mkdir(parents=True, exist_ok=True)
                os.symlink(target, patches_dir / "task-alpha.patch")
                with self.assertRaises(ValueError):
                    swarm_wave.extract_wave_diffs(prep["state_file"], task_ids=["task-alpha"])
                self.assertEqual(target.read_text(encoding="utf-8"), "UNTOUCHED")
            finally:
                swarm_wave.cleanup_wave(prep["state_file"], remove_state=True)


if __name__ == "__main__":
    unittest.main()
