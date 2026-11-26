#!/usr/bin/env python3
"""Tests unitaires pour `worktree_ops.py`, `patch_apply.py`, `wave_runner.py` et `review_pipeline.py` (100 % stdlib)."""

import ast
import asyncio
import json
import os
import shutil
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
    def test_patch_apply_reexports_worktree_ops_symbols(self) -> None:
        for attr in (
            "_is_snap_agy",
            "resolve_worktree_base",
            "_is_protected_cleanup_target",
            "_is_verified_linked_worktree",
            "create_repo_worktree",
            "cleanup_worktree_best_effort",
        ):
            self.assertIs(getattr(patch_apply, attr), getattr(worktree_ops, attr))

    def test_create_repo_worktree_detached_and_protected_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            g_home = base / "gravity_home"
            repo = base / "demo-repo"
            _init_git_repo(repo, {"app.py": "x = 1\n", "AGENTS.md": "# Demo AGENTS\n"}, branch="dev")
            (repo / ".agents").mkdir(parents=True, exist_ok=True)
            (repo / ".agents" / "keep.md").write_text("safe\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"GRAVITY_HOME": str(g_home)}, clear=False):
                os.environ.pop("GRAVITY_WORKFLOW_HOME", None)
                os.environ.pop("GRAVITY_WORKTREE_BASE", None)
                wt_base = worktree_ops.resolve_worktree_base()
                self.assertEqual(wt_base, (g_home / "workflow" / "worktrees").resolve())

                wt_path = worktree_ops.create_repo_worktree(repo, wt_base, "run-01")
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
                    worktree_ops.create_repo_worktree(repo, repo / ".wt", "bad")
                with self.assertRaises(ValueError):
                    worktree_ops.create_repo_worktree(repo, base / ".gemini" / "antigravity" / "brain", "bad")

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
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(repo / ".git", repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(repo / ".agents", repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(inside_ws_wt, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(external_dir, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(standalone_clone, repo))
                self.assertFalse(
                    patch_apply.cleanup_worktree_best_effort(
                        base / ".gemini" / "gravity" / "workflow" / "workspaces", repo
                    )
                )
                self.assertTrue((external_dir / "keep.txt").is_file())
                self.assertTrue((inside_ws_wt / "keep.txt").is_file())
                self.assertTrue((repo / ".git").exists())
                self.assertTrue((repo / ".agents" / "keep.md").is_file())
                self.assertTrue((standalone_clone / "README.md").is_file())
                self.assertTrue(wt_base.is_dir())

                # Valid detached worktree is cleanly removed & pruned
                self.assertTrue(worktree_ops.cleanup_worktree_best_effort(wt_path, repo))
                self.assertFalse(wt_path.exists())

    def test_resolve_worktree_base_env_snap_fallback_and_protection(self) -> None:
        self.assertIn("grep -rn", patch_apply.BUILDER_HYGIENE_DIRECTIVE)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_home = base / "home_user"
            fake_home.mkdir()
            explicit_arg = base / "explicit_wt"
            env_wt = base / "env_wt"
            non_hidden_gravity = base / "gravity_home"

            with mock.patch("pathlib.Path.home", return_value=fake_home), mock.patch.object(
                worktree_ops.sys, "platform", "linux"
            ):
                # 1. Explicit arg > GRAVITY_WORKTREE_BASE > GRAVITY_WORKFLOW_HOME > GRAVITY_HOME
                with mock.patch.dict(
                    os.environ,
                    {
                        "GRAVITY_WORKTREE_BASE": str(env_wt),
                        "GRAVITY_WORKFLOW_HOME": str(base / "wf_home"),
                        "GRAVITY_HOME": str(non_hidden_gravity),
                    },
                    clear=False,
                ), mock.patch.object(worktree_ops, "_is_snap_agy", return_value=True):
                    self.assertEqual(patch_apply.resolve_worktree_base(explicit_arg), explicit_arg.resolve())
                    self.assertEqual(patch_apply.resolve_worktree_base(), env_wt.resolve())

                # 2. Snap Linux fallback applies to hidden dotdir under $HOME (~/.gemini/... or ~/.custom)
                with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
                    worktree_ops, "_is_snap_agy", return_value=True
                ):
                    for key in ("GRAVITY_WORKTREE_BASE", "GRAVITY_WORKFLOW_HOME", "GRAVITY_HOME"):
                        os.environ.pop(key, None)
                    self.assertEqual(
                        patch_apply.resolve_worktree_base(),
                        (fake_home / "gravity-worktrees").resolve(),
                    )

                    os.environ["GRAVITY_HOME"] = str(fake_home / ".hidden_gravity")
                    self.assertEqual(
                        patch_apply.resolve_worktree_base(),
                        (fake_home / "gravity-worktrees").resolve(),
                    )

                    # Non-hidden GRAVITY_HOME stays unchanged even under Snap
                    os.environ["GRAVITY_HOME"] = str(non_hidden_gravity)
                    self.assertEqual(
                        patch_apply.resolve_worktree_base(),
                        (non_hidden_gravity / "workflow" / "worktrees").resolve(),
                    )

                # 2b. Mocking shutil.which('/snap/bin/agy') via patch_apply.resolve_worktree_base works
                with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
                    shutil, "which", return_value="/snap/bin/agy"
                ):
                    for key in ("GRAVITY_WORKTREE_BASE", "GRAVITY_WORKFLOW_HOME", "GRAVITY_HOME"):
                        os.environ.pop(key, None)
                    self.assertEqual(
                        patch_apply.resolve_worktree_base(),
                        (fake_home / "gravity-worktrees").resolve(),
                    )

                # 3. Without Snap, ~/.gemini/gravity/workflow/worktrees is preserved
                with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(
                    worktree_ops, "_is_snap_agy", return_value=False
                ):
                    for key in ("GRAVITY_WORKTREE_BASE", "GRAVITY_WORKFLOW_HOME", "GRAVITY_HOME"):
                        os.environ.pop(key, None)
                    self.assertEqual(
                        worktree_ops.resolve_worktree_base(),
                        (fake_home / ".gemini" / "gravity" / "workflow" / "worktrees").resolve(),
                    )

                # 4. gravity-worktrees root is protected even if GRAVITY_WORKTREE_BASE points elsewhere
                repo = base / "repo"
                _init_git_repo(repo, {"a.txt": "1\n"}, branch="dev")
                snap_root = (fake_home / "gravity-worktrees").resolve()
                snap_root.mkdir(parents=True, exist_ok=True)
                with mock.patch.dict(os.environ, {"GRAVITY_WORKTREE_BASE": str(env_wt)}, clear=False):
                    self.assertTrue(worktree_ops._is_protected_cleanup_target(snap_root, repo))
                    self.assertFalse(worktree_ops.cleanup_worktree_best_effort(snap_root, repo))
                    self.assertTrue(snap_root.is_dir())

                    # Nested linked worktree inside gravity-worktrees can still be created and cleaned
                    nested_wt = worktree_ops.create_repo_worktree(repo, snap_root, "snap-01")
                    self.assertTrue(nested_wt.is_dir())
                    self.assertTrue(worktree_ops.cleanup_worktree_best_effort(nested_wt, repo))
                    self.assertFalse(nested_wt.exists())
                    self.assertTrue(snap_root.is_dir())

            # 5. _is_snap_agy detects /snap/ paths and /usr/bin/snap realpaths on Linux only
            with mock.patch.object(worktree_ops.sys, "platform", "linux"):
                with mock.patch("shutil.which", side_effect=lambda c: "/snap/bin/agy" if c == "agy" else None), mock.patch(
                    "os.path.realpath", return_value="/usr/bin/snap"
                ):
                    self.assertTrue(worktree_ops._is_snap_agy())
                with mock.patch("shutil.which", side_effect=lambda c: "/usr/local/bin/agy" if c == "agy" else None), mock.patch(
                    "os.path.realpath", return_value="/usr/bin/snap"
                ):
                    self.assertTrue(worktree_ops._is_snap_agy())
                with mock.patch("shutil.which", side_effect=lambda c: "/usr/bin/agy" if c == "agy" else None), mock.patch(
                    "os.path.realpath", return_value="/usr/share/antigravity/agy"
                ):
                    self.assertFalse(worktree_ops._is_snap_agy())
            with mock.patch.object(worktree_ops.sys, "platform", "win32"), mock.patch(
                "shutil.which", return_value="/snap/bin/agy"
            ):
                self.assertFalse(worktree_ops._is_snap_agy())

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

            live_ok = json.loads(state_writer.resolve_state_path(workspace_root=ws).read_text(encoding="utf-8"))
            self.assertEqual(live_ok["progress"], "1/1")
            self.assertNotEqual(live_ok["progress"], "0/8")

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
            live_to = json.loads(state_writer.resolve_state_path(workspace_root=ws).read_text(encoding="utf-8"))
            self.assertEqual(live_to["progress"], "0/1")

    def test_wave_runner_fail_isolated_routing_apply_and_timeout(self) -> None:
        wr_path = PLUGIN_ROOT / "workflows" / "wave_runner.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            atelier = base / "atelier"
            (atelier / "tasks").mkdir(parents=True)
            wt_base = base / "wf_worktrees"

            repo_q = base / "repo-quick"
            repo_s = base / "repo-standard"
            repo_err = base / "repo-error"
            repo_to = base / "repo-timeout"

            _init_git_repo(repo_q, {"q.txt": "init\n", "AGENTS.md": "# Quick Repo Rules\n"}, branch="dev")
            _init_git_repo(repo_s, {"s.txt": "init\n", "AGENTS.md": "# Standard Repo Rules\n"}, branch="dev")
            _init_git_repo(repo_err, {"e.txt": "init\n", "AGENTS.md": "# Error Repo Rules\n"}, branch="dev")
            _init_git_repo(repo_to, {"t.txt": "init\n", "AGENTS.md": "# Timeout Repo Rules\n"}, branch="dev")

            wave_tasks = []
            for fname, repo_dir, rname, prof, target in (
                ("t1-quick.md", repo_q, "repo-quick", "quick", ["q.txt"]),
                ("t2-std.md", repo_s, "repo-standard", "standard", ["s.txt"]),
                ("t3-err.md", repo_err, "repo-error", "standard", ["e.txt"]),
                ("t4-to.md", repo_to, "repo-timeout", "standard", ["t.txt"]),
            ):
                tpath = atelier / "tasks" / fname
                tpath.write_text(
                    f"# Task {rname}\n\n- Update `{target[0]}` in `{rname}`.\n",
                    encoding="utf-8",
                    newline="\n",
                )
                wave_tasks.append(
                    {
                        "id": Path(fname).stem,
                        "title": f"Task {rname}",
                        "repo_id": rname,
                        "repo_root": str(repo_dir),
                        "expected_branch": "dev",
                        "profile": prof,
                        "plan_file": str(tpath),
                        "target_files": target,
                        "status": "ready",
                    }
                )

            wave_plan = {
                "schema_version": "wave_plan_v1",
                "workspace": str(atelier),
                "wave": 1,
                "tasks": wave_tasks,
            }

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
                workspaces=[atelier],
                args_dict={
                    "plugin_root": str(PLUGIN_ROOT),
                    "wave_plan": wave_plan,
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

            # Real progress recorded in live_state.json (never 0/8)
            live_q = json.loads(state_writer.resolve_state_path(workspace_root=repo_q).read_text(encoding="utf-8"))
            self.assertEqual(live_q["progress"], "1/1")
            live_to = json.loads(state_writer.resolve_state_path(workspace_root=repo_to).read_text(encoding="utf-8"))
            self.assertEqual(live_to["progress"], "0/1")

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
            self.assertIn("q.txt", bld_q[2])

            # Compact JSON <= 10 lines/repo (even with 20 changed_files)
            summary_text = ns["WAVE_SUMMARY"]
            parsed = json.loads(summary_text)
            self.assertEqual(parsed["passed"], 2)
            self.assertEqual(parsed["total"], 4)
            self.assertLessEqual(len(summary_text.splitlines()), 10 * 4)

    def test_wave_runner_empty_tasks_sets_zero_of_zero_progress(self) -> None:
        wr_path = PLUGIN_ROOT / "workflows" / "wave_runner.py"
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "empty-ws"
            ws.mkdir()
            ns, _, _ = _exec_workflow(
                wr_path,
                workspaces=[ws],
                args_dict={"plugin_root": str(PLUGIN_ROOT), "tasks": []},
                fake_agent=lambda *_a, **_kw: None,
            )
            self.assertEqual(ns["WAVE_RESULTS"], [])
            live_empty = json.loads(state_writer.resolve_state_path(workspace_root=ws).read_text(encoding="utf-8"))
            self.assertEqual(live_empty["progress"], "0/0")

    def test_wave_runner_wave_plan_json_file_and_structured_tasks(self) -> None:
        wr_path = PLUGIN_ROOT / "workflows" / "wave_runner.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws = base / "runner-ws"
            ws.mkdir(parents=True)
            wt_base = base / "wf_worktrees"
            repo_a = base / "alpha"
            repo_b = base / "beta"
            _init_git_repo(repo_a, {"a.txt": "v0\n"}, branch="dev")
            _init_git_repo(repo_b, {"b.txt": "v0\n"}, branch="dev")

            plan_md = ws / "alpha-task.md"
            plan_md.write_text("# Alpha Task\n\nUpdate a.txt\n", encoding="utf-8", newline="\n")

            wave_json = ws / "wave1.json"
            wave_json.write_text(
                json.dumps(
                    {
                        "schema_version": "wave_plan_v1",
                        "workspace": str(ws),
                        "wave": 2,
                        "tasks": [
                            {
                                "id": "w2-alpha",
                                "title": "Alpha Task",
                                "repo_id": "alpha",
                                "repo_root": str(repo_a),
                                "expected_branch": "dev",
                                "profile": "quick",
                                "plan_file": "alpha-task.md",
                                "target_files": ["a.txt"],
                                "status": "ready",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
                newline="\n",
            )

            seen_roles = []

            class _OkBuilder:
                def __init__(self, role: str) -> None:
                    self.role = role

                async def __call__(self, prompt, schema):
                    seen_roles.append(self.role)
                    marker = "Travaille EXCLUSIVEMENT dans le worktree isolé `"
                    wt_dir = Path(prompt.split(marker, 1)[1].split("`", 1)[0])
                    target_name = "a.txt" if "alpha" in self.role else "b.txt"
                    (wt_dir / target_name).write_text("v1\n", encoding="utf-8", newline="\n")
                    subprocess.run(["git", "-C", str(wt_dir), "add", "-N", "."], check=True, capture_output=True)
                    subprocess.run(
                        [
                            "git", "-C", str(wt_dir), "diff",
                            f"--output={wt_dir / '.gravity_verified.patch'}", "--", ".", ":(exclude).gravity_verified.patch",
                        ],
                        check=True,
                        capture_output=True,
                    )
                    return {
                        "workspace_path": str(wt_dir),
                        "modified_files": [target_name],
                        "git_diff": "",
                        "validation_summary": "ok",
                        "refused": False,
                        "summary": "Done",
                    }

            def fake_agent(*_a, **kw):
                role = kw.get("role", "")
                if role.startswith("Builder"):
                    return _OkBuilder(role)

                async def _oneshot():
                    seen_roles.append(role)
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

                return _oneshot()

            # 1. wave_plan passed as JSON file path; quick runs Reviewer flash
            ns_json, _, _ = _exec_workflow(
                wr_path,
                workspaces=[ws],
                args_dict={
                    "plugin_root": str(PLUGIN_ROOT),
                    "wave_plan": str(wave_json),
                    "apply_on_pass": True,
                    "worktree_base": str(wt_base),
                },
                fake_agent=fake_agent,
            )
            self.assertEqual(ns_json["WAVE_RESULTS"][0]["status"], "PASS")
            self.assertEqual((repo_a / "a.txt").read_text(encoding="utf-8"), "v1\n")
            self.assertIn("Reviewer alpha", seen_roles)

            # 2. Structured args["tasks"] with inline plan + no_verify skips Reviewer
            seen_roles.clear()
            ns_tasks, _, _ = _exec_workflow(
                wr_path,
                workspaces=[ws],
                args_dict={
                    "plugin_root": str(PLUGIN_ROOT),
                    "tasks": [
                        {
                            "repo_root": str(repo_b),
                            "expected_branch": "dev",
                            "profile": "quick",
                            "plan": "# Beta inline\nUpdate b.txt\n",
                            "no_verify": True,
                        }
                    ],
                    "apply_on_pass": True,
                    "worktree_base": str(wt_base),
                },
                fake_agent=fake_agent,
            )
            self.assertEqual(ns_tasks["WAVE_RESULTS"][0]["status"], "UNSAFE_SKIPPED")
            self.assertEqual((repo_b / "b.txt").read_text(encoding="utf-8"), "v1\n")
            self.assertFalse(any(r.startswith("Reviewer") for r in seen_roles))

    def test_wave_runner_fail_closed_rejections(self) -> None:
        wr_path = PLUGIN_ROOT / "workflows" / "wave_runner.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws = base / "ws"
            ws.mkdir()
            repo = base / "repo"
            _init_git_repo(repo, {"x.txt": "1\n"}, branch="dev")
            non_git = base / "non-git"
            non_git.mkdir()

            agent_calls = []

            def fail_if_called(*_a, **_kw):
                agent_calls.append(True)
                raise AssertionError("Agent must not be called on fail-closed rejection")

            valid_task = {
                "id": "t1",
                "title": "Valid",
                "repo_id": "repo",
                "repo_root": str(repo),
                "expected_branch": "dev",
                "profile": "standard",
                "plan": "# Valid\nUpdate x.txt\n",
                "status": "ready",
            }

            bad_args_cases = [
                {"wave_plan": {"schema_version": "wave_plan_v0", "tasks": [valid_task]}},
                {"wave_plan": {"tasks": [valid_task]}},
                {"tasks": ["tasks/raw-markdown.md"]},
                {
                    "wave_plan": {
                        "schema_version": "wave_plan_v1",
                        "workspace": str(ws),
                        "wave": 1,
                        "tasks": ["tasks/raw-markdown.md"],
                    }
                },
                {"tasks": [{"plan": "# No repo", "profile": "standard"}]},
                {"tasks": [{"repo_root": str(base / "missing-repo"), "plan": "# Missing"}]},
                {"tasks": [{"repo_root": str(non_git), "plan": "# Non git"}]},
            ]
            for blocked_status in ("draft", "done", "archived", "cancelled", "DRAFT"):
                bad_args_cases.append(
                    {
                        "wave_plan": {
                            "schema_version": "wave_plan_v1",
                            "workspace": str(ws),
                            "wave": 1,
                            "tasks": [{**valid_task, "status": blocked_status}],
                        }
                    }
                )

            for idx, bad_args in enumerate(bad_args_cases):
                with self.subTest(case=idx, bad_args=bad_args):
                    with self.assertRaises(ValueError):
                        _exec_workflow(
                            wr_path,
                            workspaces=[ws],
                            args_dict={"plugin_root": str(PLUGIN_ROOT), **bad_args},
                            fake_agent=fail_if_called,
                        )
            self.assertEqual(agent_calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
