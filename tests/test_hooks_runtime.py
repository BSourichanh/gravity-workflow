#!/usr/bin/env python3
"""Tests unitaires d'exécution des hooks (PreToolUse, SessionStart, Stop), statusline et workflows."""

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

import profile_engine  # noqa: E402
import state_writer  # noqa: E402


class TestHooksRuntime(WorkflowEnvTestCase):
    def _exec_workflow_without_dunder_file(
        self,
        script_path: Path,
        *,
        workspaces: list,
        args_dict: dict,
        fake_agent=None,
        fake_parallel=None,
    ):
        logs = []
        phases = []

        async def _default_agent(*_a, **_kw):
            return ""

        ns = {
            "__name__": "gravity_workflow_runtime",
            "WORKSPACES": [str(w) for w in workspaces],
            "args": args_dict,
            "log": logs.append,
            "phase": phases.append,
            "agent": fake_agent or _default_agent,
            "parallel": fake_parallel,
        }
        source = script_path.read_text(encoding="utf-8")
        flags = getattr(ast, "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0)
        code = compile(source, str(script_path), "exec", flags=flags)
        res = eval(code, ns)
        if asyncio.iscoroutine(res):
            asyncio.run(res)
        return ns, logs, phases

    def test_pre_tool_guard_ascii_safe_and_non_gravity_no_pollution(self) -> None:
        guard = PLUGIN_ROOT / "hooks/pre_tool_guard.py"
        with tempfile.TemporaryDirectory() as tmp:
            plain_ws = Path(tmp) / "plain_repo"
            plain_ws.mkdir()
            env = dict(os.environ)
            env["GRAVITY_WORKFLOW_HOME"] = str(Path(tmp) / "wf_home")
            env.pop("GRAVITY_WORKFLOW_STATE_PATH", None)
            env["GRAVITY_WORKFLOW_SKIP_REGISTRY"] = "1"
            payload = json.dumps(
                {
                    "cwd": str(plain_ws),
                    "conversationId": "conv-ascii-1",
                    "toolCall": {"name": "run_command", "args": {"CommandLine": "git push --force"}},
                }
            )
            proc = subprocess.run(
                [sys.executable, str(guard)],
                input=payload,
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=str(PLUGIN_ROOT),
                env=env,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            self.assertTrue(proc.stdout.isascii(), f"Sortie non ASCII: {proc.stdout!r}")
            out = json.loads(proc.stdout.strip())
            self.assertEqual(out["decision"], "deny")
            self.assertFalse((plain_ws / ".agents").exists())

    def test_terminal_custom_hook_event_window_and_multiturn_evo(self) -> None:
        stop_hook = PLUGIN_ROOT / "hooks/stop_session_logger.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plain_ws = base / "plain_repo"
            plain_ws.mkdir()

            p_plain = subprocess.run(
                [sys.executable, str(stop_hook)],
                input=json.dumps({"cwd": str(plain_ws), "fullyIdle": True, "conversationId": "c-plain"}),
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=str(PLUGIN_ROOT),
                timeout=5,
                check=False,
            )
            self.assertEqual(p_plain.returncode, 0)
            self.assertEqual(p_plain.stdout.strip(), "{}")
            # Zéro écriture dans le workspace cible mais runs.jsonl + profile.json centralisés créés
            self.assertFalse((plain_ws / ".agents").exists())
            runs_file = state_writer.resolve_runs_log_path(plain_ws)
            self.assertTrue(runs_file.is_file())
            last_run = json.loads(runs_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last_run["preset_profile"], "standard")
            self.assertIn("role_models", last_run)

            ws = base / "gravity_ws"
            ws.mkdir()

            state_writer.update_live_state(
                workspace_root=ws,
                event={"type": "GUARD:run_command", "decision": "deny", "message": "old deny"},
            )
            r1 = profile_engine.evolve_on_stop(
                ws,
                {
                    "conversation_id": "conv-m1",
                    "execution_num": 1,
                    "fully_idle": True,
                    "termination_reason": "TERMINAL_CUSTOM_HOOK",
                },
            )
            self.assertFalse(r1["signals"]["session_error"])
            self.assertEqual(r1["signals"]["guard_deny_recent"], 1)
            self.assertEqual(r1["suggested_profile"], "deep")
            self.assertEqual(r1["sessions_analyzed"], 1)

            # Tour 2 de la même conversation : réévaluation sans double comptage
            r1_t2 = profile_engine.evolve_on_stop(
                ws,
                {
                    "conversation_id": "conv-m1",
                    "execution_num": 2,
                    "fully_idle": True,
                    "termination_reason": "TERMINAL_CUSTOM_HOOK",
                },
            )
            self.assertEqual(r1_t2["sessions_analyzed"], 1)
            self.assertEqual(r1_t2["signals"]["guard_deny_recent"], 0)
            self.assertEqual(r1_t2["suggested_profile"], "standard")

    def test_stop_session_logger_shared_runtime_arbitrary_cwd_and_state_progress(self) -> None:
        stop_hook = PLUGIN_ROOT / "hooks/stop_session_logger.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws = base / "ws_shared_stop"
            ws.mkdir()

            # Invocation directe depuis un cwd arbitraire (bootstrap sys.path vers plugin/_shared)
            proc_ws_paths = subprocess.run(
                [sys.executable, str(stop_hook)],
                input=json.dumps(
                    {
                        "workspacePaths": [str(ws)],
                        "conversationId": "conv-shared-1",
                        "terminationReason": "NO_TOOL_CALL",
                        "fullyIdle": True,
                    }
                ),
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=str(ws),
                timeout=5,
                check=False,
            )
            self.assertEqual(proc_ws_paths.returncode, 0)
            self.assertEqual(proc_ws_paths.stdout.strip(), "{}")
            self.assertFalse((ws / ".agents").exists())

            prof = json.loads(
                state_writer.resolve_profile_storage_path(ws).read_text(encoding="utf-8")
            )
            self.assertEqual(prof["workspace"], str(ws.resolve()))
            self.assertEqual(prof["last_evaluated_conversation_id"], "conv-shared-1")

            # Résolution via GRAVITY_WORKFLOW_WORKSPACE quand stdin est malformé
            ws_env = base / "ws_env_stop"
            ws_env.mkdir()
            env = dict(os.environ)
            env["GRAVITY_WORKFLOW_WORKSPACE"] = str(ws_env)
            proc_bad = subprocess.run(
                [sys.executable, str(stop_hook)],
                input="{not_json",
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=str(base),
                env=env,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc_bad.returncode, 0)
            self.assertEqual(proc_bad.stdout.strip(), "{}")
            self.assertTrue(state_writer.resolve_runs_log_path(ws_env).is_file())

            # state_writer._default_state()["progress"] vaut "0/0"
            self.assertEqual(state_writer._default_state()["progress"], "0/0")
            state_writer.update_live_state(workspace_root=ws_env, stage="idle")
            live_data = json.loads(
                state_writer.resolve_state_path(workspace_root=ws_env).read_text(encoding="utf-8")
            )
            self.assertEqual(live_data["progress"], "0/0")

    def test_session_start_hook_and_statusline_subprocesses(self) -> None:
        session_hook = PLUGIN_ROOT / "hooks/session_start_caveman.py"
        statusline_script = PLUGIN_ROOT / "observability/statusline.py"

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plain = base / "plain"
            plain.mkdir()
            ws = base / "gravity_ws"
            ws.mkdir()

            # Actif partout sans init -> injectSteps (niveau=lite) sur dépôt vierge
            p_plain = subprocess.run(
                [sys.executable, str(session_hook)],
                input=json.dumps({"cwd": str(plain)}),
                text=True,
                capture_output=True,
                cwd=str(PLUGIN_ROOT),
                timeout=5,
                check=False,
            )
            self.assertEqual(p_plain.returncode, 0)
            out_plain = json.loads(p_plain.stdout.strip())
            self.assertIn("niveau=lite", out_plain["injectSteps"][0]["systemMessage"]["systemMessage"])
            self.assertFalse((plain / ".agents").exists())

            # stdin malformé -> {}
            p_bad = subprocess.run(
                [sys.executable, str(session_hook)],
                input="{bad_json",
                text=True,
                capture_output=True,
                cwd=str(PLUGIN_ROOT),
                timeout=5,
                check=False,
            )
            self.assertEqual(p_bad.returncode, 0)
            self.assertEqual(p_bad.stdout.strip(), "{}")

            # Niveaux actifs lite / full / ultra -> injectSteps
            for lvl in ("lite", "full", "ultra"):
                if lvl == "lite":
                    profile_engine.set_caveman(ws, "auto")
                else:
                    profile_engine.set_caveman(ws, lvl)
                p_act = subprocess.run(
                    [sys.executable, str(session_hook)],
                    input=json.dumps({"cwd": str(ws)}),
                    text=True,
                    capture_output=True,
                    cwd=str(PLUGIN_ROOT),
                    timeout=5,
                    check=False,
                )
                self.assertEqual(p_act.returncode, 0)
                out = json.loads(p_act.stdout.strip())
                msg = out["injectSteps"][0]["systemMessage"]["systemMessage"]
                self.assertIn(f"niveau={lvl}", msg)

            # Niveau off -> {}
            profile_engine.set_caveman(ws, "off")
            p_off = subprocess.run(
                [sys.executable, str(session_hook)],
                input=json.dumps({"cwd": str(ws)}),
                text=True,
                capture_output=True,
                cwd=str(PLUGIN_ROOT),
                timeout=5,
                check=False,
            )
            self.assertEqual(p_off.returncode, 0)
            self.assertEqual(p_off.stdout.strip(), "{}")

            # statusline : défaut auto puis verrouillé full
            profile_engine.set_caveman(ws, "auto")
            st_lite = subprocess.run(
                [sys.executable, str(statusline_script)],
                input=json.dumps({"workspace": {"project_dir": str(ws)}}),
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(st_lite.returncode, 0)
            self.assertIn("evo:standard · caveman:lite", st_lite.stdout)
            self.assertNotIn("[lock]", st_lite.stdout.split("caveman:")[1].split("·")[0])

            profile_engine.set_caveman(ws, "full")
            st_full = subprocess.run(
                [sys.executable, str(statusline_script)],
                input=json.dumps({"workspace": {"project_dir": str(ws)}}),
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(st_full.returncode, 0)
            self.assertIn("caveman:full[lock]", st_full.stdout)

    def test_plugin_root_resolution_without_dunder_file_and_explicit_logging(self) -> None:
        rp_path = PLUGIN_ROOT / "workflows/review_pipeline.py"
        rs_path = PLUGIN_ROOT / "evals/run_suite.py"

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # 1. Résolution via args["plugin_root"] sans __file__
            plain_ws = base / "plain_ws"
            plain_ws.mkdir()
            (plain_ws / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (plain_ws / ".git").mkdir()
            (plain_ws / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

            calls = []

            class _FakeBuilder:
                def __init__(self, **kw):
                    self.kw = kw

                async def __call__(self, prompt, schema):
                    calls.append(("builder", self.kw.get("model"), prompt, schema))
                    return {
                        "workspace_path": str(plain_ws),
                        "modified_files": ["app.py"],
                        "git_diff": "",
                        "validation_summary": "python -m unittest -> OK (exit 0)",
                        "refused": False,
                        "summary": "Done",
                    }

            def fake_agent(*a, **kw):
                role = kw.get("role", "")
                if role == "Builder":
                    return _FakeBuilder(**kw)

                async def _oneshot():
                    calls.append((role, kw.get("model"), a[0] if a else kw.get("prompt", "")))
                    if role == "Reviewer":
                        return {
                            "verdict": "PASS",
                            "comprehension_check": "Modifie app.py",
                            "blockers": [],
                            "majors": [],
                            "uncertain": [],
                            "nits": [],
                            "manual_checks": [],
                            "summary": "RAS",
                        }
                    return "Plan"

                return _oneshot()

            ns, logs, _ = self._exec_workflow_without_dunder_file(
                rp_path,
                workspaces=[plain_ws],
                args_dict={
                    "plugin_root": str(PLUGIN_ROOT),
                    "task": "Tester pipeline",
                    "plan": "1. Modifier app.py",
                    "level": "deep",
                },
                fake_agent=fake_agent,
            )
            self.assertEqual(ns["PLUGIN_ROOT"].resolve(), PLUGIN_ROOT.resolve())
            self.assertFalse(any("PLUGIN_ROOT introuvable" in m for m in logs))

            # Planner sauté quand args["plan"] fourni (même en deep)
            roles_called = [c[0] for c in calls]
            self.assertNotIn("Planner", roles_called)
            self.assertIn("builder", roles_called)
            self.assertIn("Reviewer", roles_called)

            # BUILD_SCHEMA inclut validation_summary
            builder_call = [c for c in calls if c[0] == "builder"][0]
            self.assertIn("validation_summary", builder_call[3]["required"])

            # Arborescence bornée injectée sans .git + validation_summary + repli modified_files
            rev_call = [c for c in calls if c[0] == "Reviewer"][0]
            self.assertIn("app.py", rev_call[2])
            self.assertNotIn(".git/HEAD", rev_call[2])
            self.assertIn("python -m unittest -> OK (exit 0)", rev_call[2])
            self.assertIn("print('ok')", rev_call[2])

            # Zéro pollution .agents dans plain_ws ; runs.jsonl centralisé contient preset_profile + role_models
            self.assertFalse((plain_ws / ".agents").exists())
            runs_file = state_writer.resolve_runs_log_path(plain_ws)
            self.assertTrue(runs_file.is_file())
            last_entry = json.loads(runs_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last_entry["event"], "review_pipeline")
            self.assertEqual(last_entry["preset_profile"], "deep")
            self.assertEqual(last_entry["role_models"]["plan"], "skipped")
            self.assertEqual(last_entry["role_models"]["build"], "flash")
            self.assertEqual(last_entry["role_models"]["review"], "pro")

            # 2. run_suite.py sans __file__ résout WS_ROOT / ".agents/plugins/gravity-workflow"
            ws_installed = base / "ws_installed"
            copied_plugin = ws_installed / ".agents" / "plugins" / "gravity-workflow"
            shutil.copytree(
                PLUGIN_ROOT,
                copied_plugin,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

            async def fake_parallel(_items, _fn):
                return []

            ns_rs, logs_rs, _ = self._exec_workflow_without_dunder_file(
                rs_path,
                workspaces=[ws_installed],
                args_dict={"case_ids": ["CASE-01-SCHEMA-INTEGRITY"]},
                fake_parallel=fake_parallel,
            )
            self.assertEqual(ns_rs["PLUGIN_ROOT"].resolve(), copied_plugin.resolve())
            self.assertFalse(any("PLUGIN_ROOT introuvable" in m for m in logs_rs))
            self.assertNotIn(str(ws_installed.resolve()), str(ns_rs["REPORT_JSON"]))

            # 3. Journalisation explicite en cas d'échec de résolution PLUGIN_ROOT (jamais silencieux)
            orphan_ws = base / "orphan"
            orphan_ws.mkdir()
            with mock.patch("pathlib.Path.home", return_value=base / "no_home"):
                logs_orphan = []
                src_rp = rp_path.read_text(encoding="utf-8")
                ns_orphan = {
                    "__name__": "test_orphan",
                    "WORKSPACES": [str(orphan_ws)],
                    "args": {},
                    "log": logs_orphan.append,
                }
                flags = getattr(ast, "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0)
                res_orphan = eval(compile(src_rp, str(rp_path), "exec", flags=flags), ns_orphan)
                if asyncio.iscoroutine(res_orphan):
                    asyncio.run(res_orphan)
            self.assertTrue(any("PLUGIN_ROOT introuvable" in m for m in logs_orphan))

    def test_hooks_migrated_onto_shared_hook_runtime_with_zero_legacy_imports(self) -> None:
        hooks_dir = PLUGIN_ROOT / "hooks"
        self.assertFalse((hooks_dir / "hook_runtime.py").exists())
        for py_file in sorted(hooks_dir.rglob("*.py")):
            src = py_file.read_text(encoding="utf-8")
            self.assertNotIn("from hook_runtime import", src, str(py_file))
            self.assertNotIn("hooks.hook_runtime", src, str(py_file))
            tree = ast.parse(src, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("hook_runtime", alias.name, str(py_file))
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertNotEqual(mod, "hook_runtime", str(py_file))
                    self.assertNotEqual(mod, "hooks.hook_runtime", str(py_file))

        post_hook = PLUGIN_ROOT / "hooks/post_tool_format.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws = base / "ws_fmt"
            ws.mkdir()
            (ws / ".agents").mkdir()
            agents_file = ws / ".agents/sample.py"
            agents_file.write_text("x=1\n", encoding="utf-8")

            for payload in (
                "",
                "{bad_json",
                json.dumps({"cwd": str(ws), "toolCall": {"name": "write_file", "args": {"TargetFile": ".agents/sample.py"}}}),
                json.dumps({"cwd": str(ws), "toolCall": {"name": "write_file", "args": {"TargetFile": "missing.py"}}}),
            ):
                proc = subprocess.run(
                    [sys.executable, "-I", "-B", str(post_hook)],
                    input=payload,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    cwd=str(base),
                    timeout=5,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip(), "{}")

    def test_pre_tool_guard_fail_closed_ec2_and_live_event_recording(self) -> None:
        guard = PLUGIN_ROOT / "hooks/pre_tool_guard.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws = base / "ws_ec2"
            ws.mkdir()
            for raw_input in ("", "{malformed_json", "[]", json.dumps({"cwd": str(ws)})):
                proc = subprocess.run(
                    [sys.executable, "-I", "-B", str(guard)],
                    input=raw_input,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    cwd=str(base),
                    timeout=5,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                out = json.loads(proc.stdout.strip())
                self.assertEqual(out["decision"], "force_ask")
                self.assertTrue(out.get("reason"))
                self.assertFalse((ws / ".agents").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
