#!/usr/bin/env python3
"""Tests unitaires de protection des chemins, outils d'écriture, MCP et auto-verrouillage."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _unit_helpers import ensure_plugin_paths, make_gravity_workspace

PLUGIN_ROOT = ensure_plugin_paths()

import safety_policy  # noqa: E402


class TestSafetyPaths(unittest.TestCase):
    def test_call_mcp_tool_inspection(self) -> None:
        deny_env = {
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {"server": "fs", "arguments": {"nested": ["foo/../.env"]}},
            }
        }
        dec_env, _ = safety_policy.evaluate_tool_call(deny_env, PLUGIN_ROOT)
        self.assertEqual(dec_env, "deny")

        deny_hooks = {
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {"server": "fs", "arguments": {"target": ".agents/hooks.json"}},
            }
        }
        dec_hooks, _ = safety_policy.evaluate_tool_call(deny_hooks, PLUGIN_ROOT)
        self.assertEqual(dec_hooks, "deny")

        allow_benign = {
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {"server": "sentry", "arguments": {"query": "status"}},
            }
        }
        dec_ok, _ = safety_policy.evaluate_tool_call(allow_benign, PLUGIN_ROOT)
        self.assertEqual(dec_ok, "allow")

    def test_write_tools_check_all_keys_and_nested_chunks(self) -> None:
        multi_key = {
            "toolCall": {
                "name": "write_to_file",
                "args": {"TargetFile": "src/ok.py", "path": ".agents/hooks.json"},
            }
        }
        dec, _ = safety_policy.evaluate_tool_call(multi_key, PLUGIN_ROOT)
        self.assertEqual(dec, "deny")

        nested_edit = {
            "toolCall": {
                "name": "multi_replace_file_content",
                "args": {"TargetFile": "", "edits": [{"path": ".env"}]},
            }
        }
        dec_nested, _ = safety_policy.evaluate_tool_call(nested_edit, PLUGIN_ROOT)
        self.assertEqual(dec_nested, "deny")

    def test_scoped_hooks_protection_and_hardening(self) -> None:
        for allowed_target in (
            "gravity-metrics/plugin/hooks/session_start_metrics.py",
            "gravity-metrics/plugin/hooks/pre_invocation_metrics.py",
            "gravity-metrics/plugin/hooks.json",
            "src/hooks/useAuth.ts",
        ):
            dec, reason = safety_policy.evaluate_tool_call(
                {"toolCall": {"name": "write_to_file", "args": {"TargetFile": allowed_target}}},
                PLUGIN_ROOT,
            )
            self.assertEqual(dec, "allow", f"Faux positif sur {allowed_target}: {reason}")

        for denied_target in (
            "hooks/safety_policy.py",
            "hooks/pre_tool_guard.py",
            ".agents/plugins/gravity-workflow/hooks/safety_policy.py",
            "plugins/gravity-workflow/hooks/pre_tool_guard.py",
            ".agents/hooks.json",
            "hooks.json",
            ".agents/rules/01-safety-boundaries.md",
        ):
            dec, _ = safety_policy.evaluate_tool_call(
                {"toolCall": {"name": "edit_file", "args": {"path": denied_target}}},
                PLUGIN_ROOT,
            )
            self.assertEqual(dec, "deny", f"Devrait bloquer {denied_target}")

        for denied_cmd in (
            "git -C repo push --force",
            "git.exe --no-pager push origin +main",
            "rd /s /q C:\\",
            "rmdir /s /q /",
            "Remove-Item -Path:C:\\ -Recurse -Force",
            "[System.Environment]::GetEnvironmentVariables()",
            "Get-Item Env:*",
            "export -p",
            'Add-Content .agents/hooks.json "bad"',
            "python.exe -c \"print(open('.env').read())\"",
        ):
            dec, _ = safety_policy.evaluate_tool_call(
                {"toolCall": {"name": "run_command", "args": {"CommandLine": denied_cmd}}},
                PLUGIN_ROOT,
            )
            self.assertEqual(dec, "deny", f"Devrait bloquer la commande : {denied_cmd}")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            gravity_ws = make_gravity_workspace(base, "gravity_proj")
            plain_ws = base / "plain_proj"
            plain_ws.mkdir()
            legacy_ws = base / "legacy_proj"
            (legacy_ws / ".agents/gravity").mkdir(parents=True)
            (legacy_ws / ".agents/gravity/profile.json").write_text("{}", encoding="utf-8")

            # Zéro shim legacy : un ancien dossier .agents/gravity/ ne doit pas être reconnu
            self.assertTrue(safety_policy.is_gravity_workspace(gravity_ws))
            self.assertFalse(safety_policy.is_gravity_workspace(legacy_ws))

            # Heuristiques : force_ask dans un workspace Gravity Workflow, allow hors workspace (et sur legacy)
            dec_enc_g, _ = safety_policy.evaluate_tool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": "powershell.exe -EncodedCommand ZQBjAGgAbwAgADEA"},
                    }
                },
                gravity_ws,
            )
            self.assertEqual(dec_enc_g, "force_ask")

            dec_enc_p, _ = safety_policy.evaluate_tool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": "powershell.exe -EncodedCommand ZQBjAGgAbwAgADEA"},
                    }
                },
                plain_ws,
            )
            self.assertEqual(dec_enc_p, "allow")

            dec_enc_legacy, _ = safety_policy.evaluate_tool_call(
                {
                    "toolCall": {
                        "name": "run_command",
                        "args": {"CommandLine": "powershell.exe -EncodedCommand ZQBjAGgAbwAgADEA"},
                    }
                },
                legacy_ws,
            )
            self.assertEqual(dec_enc_legacy, "allow")

            # run_workflow introuvable : force_ask dans Gravity, allow hors Gravity
            dec_missing_g, _ = safety_policy.evaluate_tool_call(
                {"toolCall": {"name": "run_workflow", "args": {"ScriptPath": "does_not_exist.py"}}},
                gravity_ws,
            )
            self.assertEqual(dec_missing_g, "force_ask")

            dec_missing_p, _ = safety_policy.evaluate_tool_call(
                {"toolCall": {"name": "run_workflow", "args": {"ScriptPath": "does_not_exist.py"}}},
                plain_ws,
            )
            self.assertEqual(dec_missing_p, "allow")

            # run_workflow avec Cwd : bénin -> allow, destructeur -> deny
            wf_dir = base / "wf_dir"
            wf_dir.mkdir()
            (wf_dir / "wf_ok.py").write_text("import os\nos.remove('x.txt')\n", encoding="utf-8")
            (wf_dir / "wf_bad.py").write_text("import shutil\nshutil.rmtree('/')\n", encoding="utf-8")
            dec_cwd_ok, _ = safety_policy.evaluate_tool_call(
                {"toolCall": {"name": "run_workflow", "args": {"ScriptPath": "wf_ok.py", "Cwd": str(wf_dir)}}},
                gravity_ws,
            )
            self.assertEqual(dec_cwd_ok, "allow")
            dec_cwd_bad, _ = safety_policy.evaluate_tool_call(
                {"toolCall": {"name": "run_workflow", "args": {"ScriptPath": "wf_bad.py", "Cwd": str(wf_dir)}}},
                plain_ws,
            )
            self.assertEqual(dec_cwd_bad, "deny")

    def test_source_repo_not_self_locked_when_installed_elsewhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            installed_plugin = base / "installed_plugins" / "gravity-workflow"
            installed_hooks = installed_plugin / "hooks"
            installed_hooks.mkdir(parents=True)
            (installed_plugin / "hooks.json").write_text("{}", encoding="utf-8")
            (installed_hooks / "safety_policy.py").write_text("# installed\n", encoding="utf-8")

            source_repo = base / "src_gravity"
            source_hooks = source_repo / "plugin" / "hooks"
            source_hooks.mkdir(parents=True)
            (source_repo / "plugin" / "hooks.json").write_text("{}", encoding="utf-8")
            (source_hooks / "safety_policy.py").write_text("# source\n", encoding="utf-8")

            with mock.patch.object(safety_policy, "HOOKS_DIR", installed_hooks):
                # Édition du dépôt source plugin/ autorisée (pas d'auto-verrouillage)
                for src_path in (
                    str(source_hooks / "safety_policy.py"),
                    str(source_repo / "plugin" / "hooks.json"),
                    "plugin/hooks/safety_policy.py",
                    "plugin/hooks.json",
                ):
                    dec, reason = safety_policy.evaluate_tool_call(
                        {"toolCall": {"name": "edit_file", "args": {"Path": src_path}}},
                        source_repo,
                    )
                    self.assertEqual(dec, "allow", f"Source repo auto-verrouillé sur {src_path}: {reason}")

                # Édition du plugin actif installé toujours bloquée (deny)
                for active_path in (
                    str(installed_hooks / "safety_policy.py"),
                    str(installed_plugin / "hooks.json"),
                ):
                    dec, _ = safety_policy.evaluate_tool_call(
                        {"toolCall": {"name": "edit_file", "args": {"Path": active_path}}},
                        source_repo,
                    )
                    self.assertEqual(dec, "deny", f"Plugin actif non protégé sur {active_path}")

                # Suppression/déplacement de la racine entière du plugin actif bloquée, validation autorisée
                for rm_cmd in (
                    f'rm -rf "{installed_plugin}"',
                    f'Remove-Item -Recurse -Force "{installed_plugin}"',
                ):
                    dec_rm, _ = safety_policy.evaluate_tool_call(
                        {"toolCall": {"name": "run_command", "args": {"CommandLine": rm_cmd}}},
                        source_repo,
                    )
                    self.assertEqual(dec_rm, "deny", f"Racine plugin actif non protégée sur {rm_cmd}")

                dec_val, reason_val = safety_policy.evaluate_tool_call(
                    {
                        "toolCall": {
                            "name": "run_command",
                            "args": {
                                "CommandLine": f'python "{installed_plugin / "scripts" / "validate.py"}" --plugin-root "{installed_plugin}" --strict'
                            },
                        }
                    },
                    source_repo,
                )
                self.assertEqual(dec_val, "allow", f"Faux positif validation plugin actif: {reason_val}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
