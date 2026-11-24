#!/usr/bin/env python3
"""Tests unitaires pour `wave_runner.py`, `review_pipeline.py` (plan_file/watchdog) et `patch_apply.py` (100 % stdlib)."""

import ast
import asyncio
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
WORKFLOWS_DIR = str((PLUGIN_ROOT / "workflows").resolve())
if WORKFLOWS_DIR not in sys.path:
    sys.path.insert(0, WORKFLOWS_DIR)

import patch_apply  # noqa: E402
import state_writer  # noqa: E402


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


def _exec_workflow(script_path: Path, *, workspaces: list, args_dict: dict, fake_agent) -> tuple:
    logs = []
    phases = []

    async def _parallel(items, fn):
        return await asyncio.gather(*(fn(item) for item in items))

    ns = {
        "__name__": "gravity_workflow_runtime",
        "WORKSPACES": [str(w) for w in workspaces],
        "args": args_dict,
        "log": logs.append,
        "phase": phases.append,
        "agent": fake_agent,
        "parallel": _parallel,
    }
    source = script_path.read_text(encoding="utf-8")
    flags = getattr(ast, "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0)
    code = compile(source, str(script_path), "exec", flags=flags)
    res = eval(code, ns)
    if asyncio.iscoroutine(res):
        asyncio.run(res)
    return ns, logs, phases


class TestWaveRunnerAndWatchdogs(WorkflowEnvTestCase):
    def test_create_repo_worktree_detached_and_protected_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            g_home = base / "gravity_home"
            repo = base / "demo-repo"
            _init_git_repo(repo, {"app.py": "x = 1\n", "AGENTS.md": "# Demo AGENTS\n"}, branch="dev")

            with mock.patch.dict(os.environ, {"GRAVITY_HOME": str(g_home)}, clear=False):
                os.environ.pop("GRAVITY_WORKFLOW_HOME", None)
                wt_base = patch_apply.resolve_worktree_base()
                self.assertEqual(wt_base, (g_home / "workflow" / "worktrees").resolve())

                wt_path = patch_apply.create_repo_worktree(repo, wt_base, "run-01")
                self.assertTrue(wt_path.is_dir())
                self.assertEqual(wt_path, (wt_base / "demo-repo-run-01").resolve())
                self.assertNotIn(repo, wt_path.parents)

                head_ref = subprocess.run(
                    ["git", "-C", str(wt_path), "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                self.assertEqual(head_ref, "HEAD")

                # Rejects worktree inside target repo or .gemini/antigravity outside worktrees/
                with self.assertRaises(ValueError):
                    patch_apply.create_repo_worktree(repo, repo / ".wt", "bad")
                with self.assertRaises(ValueError):
                    patch_apply.create_repo_worktree(repo, base / ".gemini" / "antigravity" / "brain", "bad")

                # Protected & non-linked-worktree targets must NEVER be deleted
                external_dir = base / "external_dir"
                external_dir.mkdir()
                (external_dir / "keep.txt").write_text("safe\n", encoding="utf-8")
                inside_ws_wt = repo / "worktrees" / "sub"
                inside_ws_wt.mkdir(parents=True)
                (inside_ws_wt / "keep.txt").write_text("safe\n", encoding="utf-8")
                standalone_clone = base / "standalone_clone"
                _init_git_repo(standalone_clone, {"README.md": "clone\n"}, branch="dev")

                self.assertFalse(patch_apply.cleanup_worktree_best_effort(wt_base, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(base, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(repo, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(inside_ws_wt, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(external_dir, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(standalone_clone, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(base / ".gemini" / "gravity" / "workflow" / "workspaces", repo))
                self.assertTrue((external_dir / "keep.txt").is_file())
                self.assertTrue((inside_ws_wt / "keep.txt").is_file())
                self.assertTrue((standalone_clone / "README.md").is_file())
                self.assertTrue(wt_base.is_dir())

                # Valid detached worktree is cleanly removed & pruned
                self.assertTrue(patch_apply.cleanup_worktree_best_effort(wt_path, repo))
                self.assertFalse(wt_path.exists())

    def test_extract_changed_files_from_diff_bounds_and_formats(self) -> None:
        dotfile_and_patch_diff = (
            "diff --git a/.gravity_verified.patch b/.gravity_verified.patch\n"
            "--- /dev/null\n+++ b/.gravity_verified.patch\n@@ -0,0 +1 @@\n+x\n"
            "diff --git a/./.gravity_verified.patch b/./.gravity_verified.patch\n"
            "--- /dev/null\n+++ b/./.gravity_verified.patch\n@@ -0,0 +1 @@\n+x\n"
            "diff --git a/.agents/rules/01-safety-boundaries.md b/.agents/rules/01-safety-boundaries.md\n"
            "--- a/.agents/rules/01-safety-boundaries.md\n"
            "+++ b/.agents/rules/01-safety-boundaries.md\n"
            "@@ -1 +1 @@\n-a\n+b\n"
            "diff --git a/./.agents/foo.md b/./.agents/foo.md\n"
            "--- a/./.agents/foo.md\n"
            "+++ b/./.agents/foo.md\n"
            "@@ -1 +1 @@\n-a\n+b\n"
        )
        dot_files = patch_apply.extract_changed_files_from_diff(dotfile_and_patch_diff, max_items=20)
        self.assertEqual(dot_files, [".agents/rules/01-safety-boundaries.md", ".agents/foo.md"])

        chunks = []
        for idx in range(25):
            chunks.append(
                f"diff --git a/src/file_{idx}.py b/src/file_{idx}.py\n"
                f"--- a/src/file_{idx}.py\n"
                f"+++ b/src/file_{idx}.py\n"
                "@@ -1 +1 @@\n-old\n+new\n"
            )
        chunks.append(
            "diff --git a/.gravity_verified.patch b/.gravity_verified.patch\n"
            "--- /dev/null\n+++ b/.gravity_verified.patch\n@@ -0,0 +1 @@\n+x\n"
        )
        files_20 = patch_apply.extract_changed_files_from_diff("\n".join(chunks), max_items=20)
        self.assertEqual(len(files_20), 20)
        self.assertEqual(files_20[0], "src/file_0.py")
        self.assertEqual(files_20[-1], "src/file_19.py")
        self.assertNotIn(".gravity_verified.patch", files_20)

        special_diff = (
            "diff --git a/deleted.py b/deleted.py\n"
            "deleted file mode 100644\n"
            "--- a/deleted.py\n"
            "+++ /dev/null\n"
            "diff --git a/old_name.py b/new_name.py\n"
            "rename from old_name.py\n"
            "rename to new_name.py\n"
            'diff --git "a/dir/with space.py" "b/dir/with space.py"\n'
            '--- "a/dir/with space.py"\n'
            '+++ "b/dir/with space.py"\n'
        )
        special_files = patch_apply.extract_changed_files_from_diff(special_diff, max_items=20)
        self.assertEqual(special_files, ["deleted.py", "new_name.py", "dir/with space.py"])

    def test_review_pipeline_plan_file_directives_and_watchdog_timeout(self) -> None:
        rp_path = PLUGIN_ROOT / "workflows" / "review_pipeline.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws = base / "repo"
            _init_git_repo(ws, {"app.py": "print(1)\n"}, branch="dev")
            plan_doc = ws / "tasks" / "plan.md"
            plan_doc.parent.mkdir(parents=True)
            plan_doc.write_text("# Plan Test\n- Modifier app.py\n", encoding="utf-8", newline="\n")

            seen = []

            class _SlowOrOkBuilder:
                def __init__(self, slow: bool) -> None:
                    self.slow = slow

                async def __call__(self, prompt, schema):
                    seen.append(("Builder", prompt, schema))
                    if self.slow:
                        await asyncio.sleep(0.3)
                    return {
                        "workspace_path": str(ws),
                        "modified_files": [f"f_{i}.py" for i in range(24)],
                        "git_diff": "",
                        "validation_summary": "ok",
                        "refused": False,
                        "summary": "Done",
                    }

            def make_fake_agent(slow_builder: bool = False):
                def fake_agent(*_a, **kw):
                    role = kw.get("role", "")
                    if role == "Builder":
                        return _SlowOrOkBuilder(slow_builder)

                    async def _oneshot():
                        seen.append((role, kw.get("model")))
                        if role == "Reviewer":
                            return {
                                "verdict": "PASS",
                                "comprehension_check": "OK",
                                "blockers": [],
                                "majors": [],
                                "uncertain": [],
                                "nits": [],
                                "manual_checks": [],
                                "summary": "PASS",
                            }
                        return "Planner output"

                    return _oneshot()

                return fake_agent

            # 1. plan_file skips Planner, injects hygiene directives, caps changed_files <= 20
            _exec_workflow(
                rp_path,
                workspaces=[ws],
                args_dict={"plugin_root": str(PLUGIN_ROOT), "plan_file": "tasks/plan.md", "level": "deep"},
                fake_agent=make_fake_agent(slow_builder=False),
            )
            roles = [item[0] for item in seen]
            self.assertNotIn("Planner", roles)
            self.assertIn("Builder", roles)
            self.assertIn("Reviewer", roles)
            builder_prompt = [item[1] for item in seen if item[0] == "Builder"][0]
            self.assertIn("# Plan Test", builder_prompt)
            self.assertIn("WaitMsBeforeAsync=10000", builder_prompt)
            self.assertIn("NotificationTimeoutSeconds=180", builder_prompt)
            self.assertIn("manage_task(status)", builder_prompt)
            self.assertIn("EndLine", builder_prompt)
            self.assertIn("plugin/skills/gravity-workflow/references/05-workflows-and-routing.md", builder_prompt)

            runs_file = state_writer.resolve_runs_log_path(ws)
            last_ok = json.loads(runs_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertTrue(last_ok["skip_separate_planner"])
            self.assertEqual(last_ok["verdict"], "PASS")
            self.assertEqual(len(last_ok["changed_files"]), 20)

            # 2. Watchdog timeout produces TIMEOUT verdict cleanly
            _exec_workflow(
                rp_path,
                workspaces=[ws],
                args_dict={
                    "plugin_root": str(PLUGIN_ROOT),
                    "plan_file": "tasks/plan.md",
                    "builder_timeout_s": 0.05,
                },
                fake_agent=make_fake_agent(slow_builder=True),
            )
            last_to = json.loads(runs_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last_to["verdict"], "TIMEOUT")

    def test_wave_runner_fail_isolated_routing_apply_and_timeout(self) -> None:
        wr_path = PLUGIN_ROOT / "workflows" / "wave_runner.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            studio = base / "gravity-studio"
            (studio / "tasks").mkdir(parents=True)
            wt_base = base / "wf_worktrees"

            repo_q = base / "repo-quick"
            repo_s = base / "repo-standard"
            repo_err = base / "repo-error"
            repo_to = base / "repo-timeout"

            _init_git_repo(repo_q, {"q.txt": "init\n", "AGENTS.md": "# Quick Repo Rules\n"}, branch="dev")
            _init_git_repo(repo_s, {"s.txt": "init\n", "AGENTS.md": "# Standard Repo Rules\n"}, branch="dev")
            _init_git_repo(repo_err, {"e.txt": "init\n", "AGENTS.md": "# Error Repo Rules\n"}, branch="dev")
            _init_git_repo(repo_to, {"t.txt": "init\n", "AGENTS.md": "# Timeout Repo Rules\n"}, branch="dev")

            task_files = []
            for fname, rname, prof in (
                ("t1-quick.md", "repo-quick", "quick"),
                ("t2-std.md", "repo-standard", "standard"),
                ("t3-err.md", "repo-error", "standard"),
                ("t4-to.md", "repo-timeout", "standard"),
            ):
                tpath = studio / "tasks" / fname
                tpath.write_text(
                    f"# Task {rname}\n\n"
                    f"- **Dépôt cible :** `{rname}` (`../{rname}`)\n"
                    f"- **Branche attendue :** `dev`\n"
                    f"- **Profil workflow :** `{prof}`\n",
                    encoding="utf-8",
                    newline="\n",
                )
                task_files.append(f"tasks/{fname}")

            calls = []

            class _WaveBuilder:
                def __init__(self, role: str, model: str) -> None:
                    self.role = role
                    self.model = model

                async def __call__(self, prompt, schema):
                    calls.append((self.role, self.model, prompt))
                    if "repo-error" in self.role:
                        raise RuntimeError("Simulated isolated builder crash")
                    if "repo-timeout" in self.role:
                        await asyncio.sleep(0.4)
                    marker = "Travaille EXCLUSIVEMENT dans le worktree isolé `"
                    wt_str = prompt.split(marker, 1)[1].split("`", 1)[0]
                    wt_dir = Path(wt_str)
                    if "repo-quick" in self.role:
                        (wt_dir / "q.txt").write_text("quick-updated\n", encoding="utf-8", newline="\n")
                        mod = ["q.txt"]
                    else:
                        (wt_dir / "s.txt").write_text("std-updated\n", encoding="utf-8", newline="\n")
                        # Create 21 files to verify changed_files <= 20 cap
                        mod = ["s.txt"]
                        for i in range(21):
                            extra = wt_dir / f"extra_{i}.txt"
                            extra.write_text(f"{i}\n", encoding="utf-8", newline="\n")
                            mod.append(f"extra_{i}.txt")
                    subprocess.run(["git", "-C", str(wt_dir), "add", "-N", "."], check=True, capture_output=True)
                    subprocess.run(
                        [
                            "git", "-C", str(wt_dir), "diff",
                            f"--output={wt_dir / '.gravity_verified.patch'}", "--", ".", ":(exclude).gravity_verified.patch",
                        ],
                        check=True,
                        capture_output=True,
                    )
                    spoofed_ws = base / "spoofed_llm_ws" if "repo-quick" in self.role else wt_dir
                    return {
                        "workspace_path": str(spoofed_ws),
                        "modified_files": mod,
                        "git_diff": "",
                        "validation_summary": "unittest OK",
                        "refused": False,
                        "summary": f"Built {self.role}",
                    }

            def fake_agent(*_a, **kw):
                role = kw.get("role", "")
                model = kw.get("model", "")
                if role.startswith("Builder"):
                    return _WaveBuilder(role, model)

                async def _oneshot():
                    calls.append((role, model, _a[0] if _a else kw.get("prompt", "")))
                    return {
                        "verdict": "PASS",
                        "comprehension_check": "Checked",
                        "blockers": [],
                        "majors": [],
                        "uncertain": [],
                        "nits": [],
                        "manual_checks": [],
                        "summary": "PASS",
                    }

                return _oneshot()

            ns, _, _ = _exec_workflow(
                wr_path,
                workspaces=[studio],
                args_dict={
                    "plugin_root": str(PLUGIN_ROOT),
                    "tasks": task_files,
                    "apply_on_pass": True,
                    "worktree_base": str(wt_base),
                    "builder_timeout_s": 0.1,
                    "reviewer_timeout_s": 5.0,
                    "run_id": "w1-test",
                },
                fake_agent=fake_agent,
            )

            results = {r["repo"]: r for r in ns["WAVE_RESULTS"]}
            self.assertEqual(results["repo-quick"]["status"], "PASS")
            self.assertEqual(results["repo-quick"]["profile"], "quick")
            self.assertTrue(results["repo-quick"]["applied"])
            self.assertEqual(results["repo-standard"]["status"], "PASS")
            self.assertEqual(results["repo-standard"]["profile"], "standard")
            self.assertTrue(results["repo-standard"]["applied"])
            self.assertLessEqual(len(results["repo-standard"]["changed_files"]), 20)
            self.assertEqual(len(results["repo-standard"]["changed_files"]), 20)

            self.assertEqual(results["repo-error"]["status"], "ERROR")
            self.assertFalse(results["repo-error"]["applied"])
            self.assertEqual(results["repo-timeout"]["status"], "TIMEOUT")
            self.assertFalse(results["repo-timeout"]["applied"])

            # dev updated on PASS repos and untouched on failed/timed-out repos
            self.assertEqual((repo_q / "q.txt").read_text(encoding="utf-8"), "quick-updated\n")
            self.assertEqual((repo_s / "s.txt").read_text(encoding="utf-8"), "std-updated\n")
            self.assertEqual((repo_err / "e.txt").read_text(encoding="utf-8"), "init\n")
            self.assertEqual((repo_to / "t.txt").read_text(encoding="utf-8"), "init\n")

            # No leftover detached worktrees after try/finally cleanup
            self.assertEqual(list(wt_base.iterdir()), [])

            # 0 Planner calls, quick Reviewer uses flash, standard Reviewer uses pro, AGENTS.md injected
            self.assertFalse(any(c[0].startswith("Planner") for c in calls))
            rev_q = [c for c in calls if c[0] == "Reviewer repo-quick"][0]
            rev_s = [c for c in calls if c[0] == "Reviewer repo-standard"][0]
            self.assertEqual(rev_q[1], "flash")
            self.assertEqual(rev_s[1], "pro")
            self.assertIn(str(wt_base), rev_q[2])
            self.assertNotIn("spoofed_llm_ws", rev_q[2])
            bld_q = [c for c in calls if c[0] == "Builder repo-quick"][0]
            self.assertIn("# Quick Repo Rules", bld_q[2])

            # Compact JSON <= 10 lines/repo (even with 20 changed_files)
            summary_text = ns["WAVE_SUMMARY"]
            parsed = json.loads(summary_text)
            self.assertEqual(parsed["passed"], 2)
            self.assertEqual(parsed["total"], 4)
            self.assertLessEqual(len(summary_text.splitlines()), 10 * 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
