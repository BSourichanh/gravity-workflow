#!/usr/bin/env python3
"""Tests unitaires des validateurs (eval_checks, plugin_validator, frontmatter, init_workspace)."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _unit_helpers import ensure_plugin_paths

PLUGIN_ROOT = ensure_plugin_paths()

import eval_checks  # noqa: E402
import init_workspace  # noqa: E402
from validators import Report, plugin_validator, validate_plugin, validate_workspace  # noqa: E402
from validators.base import parse_frontmatter, validate_agent_file  # noqa: E402


class TestEvalChecks(unittest.TestCase):
    def test_python_syntax_valid_and_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "good.py").write_text("def ok():\n    return 42\n", encoding="utf-8")
            (base / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

            ok, msg = eval_checks.evaluate_predicate("python_syntax_valid('good.py')", base, {})
            self.assertTrue(ok, msg)

            ok_bad, _ = eval_checks.evaluate_predicate("python_syntax_valid('bad.py')", base, {})
            self.assertFalse(ok_bad)

            ok_miss, _ = eval_checks.evaluate_predicate("python_syntax_valid('missing.py')", base, {})
            self.assertFalse(ok_miss)

    def test_rule_frontmatter_valid_allows_body_sublists_and_rejects_yaml_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            valid_rule = (
                "---\n"
                "trigger: glob\n"
                "globs: '**/*.sql, migrations/**/*.py'\n"
                "description: Règles migrations SQL.\n"
                "---\n\n"
                "# SQL\n"
                "- Règle principale :\n"
                "  - Sous-élément légitime dans le corps Markdown\n"
            )
            (base / "valid.md").write_text(valid_rule, encoding="utf-8")
            ok, msg = eval_checks.evaluate_predicate("rule_frontmatter_valid('valid.md')", base, {})
            self.assertTrue(ok, msg)

            invalid_yaml_list = (
                "---\n"
                "trigger: glob\n"
                "globs:\n"
                "  - '**/*.sql'\n"
                "---\n\n# SQL\n"
            )
            (base / "bad_list.md").write_text(invalid_yaml_list, encoding="utf-8")
            ok_bad1, _ = eval_checks.evaluate_predicate("rule_frontmatter_valid('bad_list.md')", base, {})
            self.assertFalse(ok_bad1)

            invalid_inline = (
                "---\n"
                "trigger: glob\n"
                "globs: ['**/*.sql']\n"
                "---\n\n# SQL\n"
            )
            (base / "bad_inline.md").write_text(invalid_inline, encoding="utf-8")
            ok_bad2, _ = eval_checks.evaluate_predicate("rule_frontmatter_valid('bad_inline.md')", base, {})
            self.assertFalse(ok_bad2)

    def test_render_markdown_report(self) -> None:
        summary = {
            "timestamp": "2026-11-21T16:00:00Z",
            "passed": 1,
            "total": 1,
            "prefilter_rejected": 0,
            "results": [
                {
                    "id": "CASE-01",
                    "verdict": "PASS",
                    "deterministic_failures": [],
                    "judge": {"verdict": "PASS", "summary": "OK | propre"},
                    "manual_checks": ["[manual] Vérifier lisibilité"],
                }
            ],
        }
        md = eval_checks.render_markdown_report(summary)
        self.assertIn("# Gravity Eval Report", md)
        self.assertIn("OK \\| propre", md)
        self.assertIn("[manual] Vérifier lisibilité", md)

    def test_agent_frontmatter_readonly(self) -> None:
        ok_rev, msg_rev = eval_checks.evaluate_predicate(
            "agent_frontmatter_readonly('agents/reviewer.md')", PLUGIN_ROOT, {}
        )
        self.assertTrue(ok_rev, msg_rev)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            safe_agent = (
                "---\n"
                "name: security-auditor\n"
                "tools:\n"
                "  - view_file\n"
                "  - read_file\n"
                "  - grep_search\n"
                "---\n\n"
                "# Auditeur\nNe jamais exécuter de commande shell.\n"
            )
            (base / "safe_auditor.md").write_text(safe_agent, encoding="utf-8")
            ok_safe, msg_safe = eval_checks.evaluate_predicate(
                "agent_frontmatter_readonly('safe_auditor.md')", base, {}
            )
            self.assertTrue(ok_safe, msg_safe)

            bad_agent = (
                "---\n"
                "name: bad-auditor\n"
                "tools:\n"
                "  - read_file\n"
                "  - run_command\n"
                "---\n\n# Bad\n"
            )
            (base / "bad_auditor.md").write_text(bad_agent, encoding="utf-8")
            ok_bad, _ = eval_checks.evaluate_predicate(
                "agent_frontmatter_readonly('bad_auditor.md')", base, {}
            )
            self.assertFalse(ok_bad)


class TestPluginValidator(unittest.TestCase):
    def _build_minimal_plugin(self, root: Path) -> None:
        shutil.copytree(
            PLUGIN_ROOT,
            root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )

    def test_rejects_extra_manifest_keys_bom_and_invalid_module_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            self._build_minimal_plugin(plugin_dir)

            with mock.patch.object(plugin_validator, "run_hook_dry_runs"):
                # 1. Valid baseline with optional disabled=True
                manifest_path = plugin_dir / "plugin.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["disabled"] = False
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                rep_ok = Report()
                validate_plugin(plugin_dir, rep_ok)
                self.assertTrue(rep_ok.ok(strict=True), f"Erreurs inattendues: {rep_ok.errors} {rep_ok.warnings}")

                # 2. Reject forbidden legacy keys ($schema, license, keywords)
                for bad_key in ("$schema", "license", "keywords"):
                    bad_manifest = dict(manifest)
                    bad_manifest[bad_key] = "bad"
                    manifest_path.write_text(json.dumps(bad_manifest) + "\n", encoding="utf-8")
                    rep_bad_key = Report()
                    validate_plugin(plugin_dir, rep_bad_key)
                    self.assertFalse(rep_bad_key.ok(strict=True), f"Devrait rejeter la clé {bad_key}")

                # 3. Reject missing official field
                missing_repo = dict(manifest)
                missing_repo.pop("repository", None)
                manifest_path.write_text(json.dumps(missing_repo) + "\n", encoding="utf-8")
                rep_missing = Report()
                validate_plugin(plugin_dir, rep_missing)
                self.assertFalse(rep_missing.ok(strict=True))

                # 4. Reject UTF-8 BOM in plugin.json
                manifest_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(manifest).encode("utf-8"))
                rep_bom = Report()
                validate_plugin(plugin_dir, rep_bom)
                self.assertFalse(rep_bom.ok(strict=True))
                manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

                # 5. Validate config/module.json v1
                module_path = plugin_dir / "config" / "module.json"
                module_data = json.loads(module_path.read_text(encoding="utf-8"))
                bad_mod = dict(module_data)
                bad_mod["schema_version"] = 2
                module_path.write_text(json.dumps(bad_mod) + "\n", encoding="utf-8")
                rep_sv = Report()
                validate_plugin(plugin_dir, rep_sv)
                self.assertFalse(rep_sv.ok(strict=True))

                bad_name = dict(module_data)
                bad_name["plugin_name"] = "wrong-plugin"
                module_path.write_text(json.dumps(bad_name) + "\n", encoding="utf-8")
                rep_name = Report()
                validate_plugin(plugin_dir, rep_name)
                self.assertFalse(rep_name.ok(strict=True))

                module_path.unlink()
                rep_no_mod = Report()
                validate_plugin(plugin_dir, rep_no_mod)
                self.assertFalse(rep_no_mod.ok(strict=True))

    def test_templates_repo_agents_and_workspace_template_drift_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            plugin_dir = repo_dir / "plugin"
            self._build_minimal_plugin(plugin_dir)
            wt_dir = repo_dir / "workspace-template"
            wt_src = PLUGIN_ROOT.parent / "workspace-template"
            if wt_src.is_dir():
                shutil.copytree(wt_src, wt_dir)
            repo_agents = repo_dir / "AGENTS.md"
            repo_agents.write_text("# Agents\n\n## Invariants\n- OK.\n", encoding="utf-8", newline="\n")

            with mock.patch.object(plugin_validator, "run_hook_dry_runs"):
                rep_ok = Report()
                validate_plugin(plugin_dir, rep_ok)
                self.assertTrue(rep_ok.ok(strict=True), f"Erreurs inattendues: {rep_ok.errors} {rep_ok.warnings}")

                # 1. Root AGENTS.md > 2500 bytes, BOM, CRLF
                repo_agents.write_text("x" * 2501 + "\n", encoding="utf-8", newline="\n")
                rep_big = Report()
                validate_plugin(plugin_dir, rep_big)
                self.assertTrue(any("2500" in err for err in rep_big.errors), rep_big.errors)

                repo_agents.write_bytes(b"\xef\xbb\xbf# Agents\n")
                rep_bom = Report()
                validate_plugin(plugin_dir, rep_bom)
                self.assertTrue(any("BOM" in err for err in rep_bom.errors), rep_bom.errors)

                repo_agents.write_bytes(b"# Agents\r\n")
                rep_crlf = Report()
                validate_plugin(plugin_dir, rep_crlf)
                self.assertTrue(any("CRLF" in err for err in rep_crlf.errors), rep_crlf.errors)
                repo_agents.write_text("# Agents\n", encoding="utf-8", newline="\n")

                # 2. Drift between templates/.agents/rules/06-engineering-principles.md and workspace-template
                if wt_dir.is_dir():
                    wt_rule06 = wt_dir / ".agents/rules/06-engineering-principles.md"
                    orig_rule06 = wt_rule06.read_bytes()
                    wt_rule06.write_bytes(orig_rule06 + b"\n# drift\n")
                    rep_drift = Report()
                    validate_plugin(plugin_dir, rep_drift)
                    self.assertTrue(any("Dérive" in err for err in rep_drift.errors), rep_drift.errors)
                    wt_rule06.write_bytes(orig_rule06)

                # 3. Missing required template (01-safety-boundaries.md.tmpl) or CRLF in template
                tpl_safety = plugin_dir / "templates/.agents/rules/01-safety-boundaries.md.tmpl"
                orig_safety = tpl_safety.read_bytes()
                tpl_safety.write_bytes(orig_safety.replace(b"\n", b"\r\n"))
                rep_tpl_crlf = Report()
                validate_plugin(plugin_dir, rep_tpl_crlf)
                self.assertTrue(any("CRLF" in err for err in rep_tpl_crlf.errors), rep_tpl_crlf.errors)
                tpl_safety.unlink()
                rep_missing_tpl = Report()
                validate_plugin(plugin_dir, rep_missing_tpl)
                self.assertTrue(
                    any("01-safety-boundaries.md.tmpl" in err for err in rep_missing_tpl.errors),
                    rep_missing_tpl.errors,
                )

    def test_agent_tools_rejects_duplicates_and_canonical_alias_collisions(self) -> None:
        agents_dir = PLUGIN_ROOT / "agents"
        for fname, readonly, prefer_pro in (
            ("planner.md", True, True),
            ("builder.md", False, False),
            ("reviewer.md", True, True),
        ):
            rep = Report()
            validate_agent_file(agents_dir / fname, rep, readonly=readonly, prefer_pro=prefer_pro)
            self.assertTrue(rep.ok(strict=True), f"{fname} invalide : {rep.errors} {rep.warnings}")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cases = [
                ("dup_exact.md", ["read_file", "read_file"]),
                ("collision_read.md", ["view_file", "read_file"]),
                ("collision_write.md", ["write_to_file", "write_file"]),
                ("collision_edit.md", ["replace_file_content", "edit_file"]),
                ("collision_multi_edit.md", ["multi_replace_file_content", "edit_file"]),
                ("collision_two_aliases.md", ["replace_file_content", "multi_replace_file_content"]),
            ]
            for filename, tool_list in cases:
                tools_yaml = "\n".join(f"  - {t}" for t in tool_list)
                content = (
                    "---\n"
                    "name: test-agent\n"
                    "description: Agent de test collision outils.\n"
                    "model: inherit\n"
                    "commandExecutionPolicy: sandbox\n"
                    "tools:\n"
                    f"{tools_yaml}\n"
                    "---\n\n# Agent\n"
                )
                agent_file = base / filename
                agent_file.write_text(content, encoding="utf-8")
                rep_bad = Report()
                validate_agent_file(agent_file, rep_bad)
                self.assertTrue(
                    rep_bad.errors,
                    f"{filename} devrait produire une erreur pour {tool_list}",
                )

    def test_skill_md_byte_caps_and_caveman_reference_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            self._build_minimal_plugin(plugin_dir)

            with mock.patch.object(plugin_validator, "run_hook_dry_runs"):
                rep_ok = Report()
                validate_plugin(plugin_dir, rep_ok)
                self.assertTrue(rep_ok.ok(strict=True), f"Erreurs inattendues: {rep_ok.errors} {rep_ok.warnings}")

                # 1. gravity-workflow/SKILL.md > 6000 octets
                gw_skill = plugin_dir / "skills/gravity-workflow/SKILL.md"
                orig_gw = gw_skill.read_text(encoding="utf-8")
                gw_skill.write_text(orig_gw + "\n" + ("x" * 6001) + "\n", encoding="utf-8", newline="\n")
                rep_gw = Report()
                validate_plugin(plugin_dir, rep_gw)
                self.assertTrue(any("6000" in err for err in rep_gw.errors), rep_gw.errors)
                gw_skill.write_text(orig_gw, encoding="utf-8", newline="\n")

                # 2. caveman/SKILL.md > 3000 octets
                cav_skill = plugin_dir / "skills/caveman/SKILL.md"
                orig_cav = cav_skill.read_text(encoding="utf-8")
                cav_skill.write_text(orig_cav + "\n" + ("y" * 3001) + "\n", encoding="utf-8", newline="\n")
                rep_cav = Report()
                validate_plugin(plugin_dir, rep_cav)
                self.assertTrue(any("3000" in err for err in rep_cav.errors), rep_cav.errors)
                cav_skill.write_text(orig_cav, encoding="utf-8", newline="\n")

                # 3. skills/caveman/references/style-examples.md manquant
                cav_ref = plugin_dir / "skills/caveman/references/style-examples.md"
                cav_ref.unlink()
                rep_ref = Report()
                validate_plugin(plugin_dir, rep_ref)
                self.assertTrue(any("style-examples.md" in err for err in rep_ref.errors), rep_ref.errors)


class TestFrontmatterParser(unittest.TestCase):
    def test_scalars_blocks_and_lists(self) -> None:
        sample = (
            "---\n"
            "name: reviewer\n"
            "description: >\n"
            "  Ligne un\n"
            "  Ligne deux\n"
            "notes: |\n"
            "  A\n"
            "  B\n"
            "tools:\n"
            "  - view_file\n"
            "  - grep_search\n"
            "tags: [\"alpha\", \"beta\"]\n"
            "---\n"
            "# Corps\n"
        )
        meta, body = parse_frontmatter(sample)
        self.assertEqual(meta["name"], "reviewer")
        self.assertEqual(meta["description"], "Ligne un Ligne deux")
        self.assertEqual(meta["notes"], "A\nB")
        self.assertEqual(meta["tools"], ["view_file", "grep_search"])
        self.assertEqual(meta["tags"], ["alpha", "beta"])
        self.assertIn("# Corps", body)


class TestInitWorkspace(unittest.TestCase):
    def test_quickstart_state_and_anti_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            custom_agents = "# Custom AGENTS\n"
            (ws / "AGENTS.md").write_text(custom_agents, encoding="utf-8")

            rc = init_workspace.init_workspace(ws, mode="quickstart", force=False)
            self.assertEqual(rc, 0)
            self.assertEqual((ws / "AGENTS.md").read_text(encoding="utf-8"), custom_agents)
            self.assertTrue((ws / ".agents/rules/01-safety-boundaries.md").is_file())
            self.assertTrue((ws / ".agents/rules/05-git-versioning.md").is_file())
            self.assertTrue((ws / ".agents/rules/06-engineering-principles.md").is_file())
            self.assertFalse((ws / ".agents/rules/02-python-stdlib.md").exists())
            self.assertFalse((ws / ".agents/rules/03-api-architecture.md").exists())
            self.assertFalse((ws / ".agents/rules/04-release-checklist.md").exists())
            self.assertTrue((ws / ".agents/gravity-workflow/.gitignore").is_file())

            # Vérifie encodage UTF-8 sans BOM et LF sur les fichiers générés
            for gen_rel in (
                ".agents/rules/01-safety-boundaries.md",
                ".agents/rules/05-git-versioning.md",
                ".agents/rules/06-engineering-principles.md",
                ".agents/gravity-workflow/PROGRESS.md",
                ".agents/gravity-workflow/observability/live_state.json",
            ):
                raw = (ws / gen_rel).read_bytes()
                self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), gen_rel)
                self.assertNotIn(b"\r\n", raw, gen_rel)

            live_state = json.loads(
                (ws / ".agents/gravity-workflow/observability/live_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(live_state["progress"], "3/8")
            self.assertEqual(live_state["stage"], "quickstart")
            self.assertEqual(live_state["mode"], "evo")
            self.assertEqual(live_state["level"], "standard")
            self.assertEqual(live_state["preset_profile"], "standard")
            self.assertEqual(live_state["suggested_profile"], "standard")
            self.assertNotIn("pace", live_state)
            self.assertNotIn("verification", live_state)
            self.assertEqual(live_state["caveman"], "lite")
            self.assertFalse(live_state["caveman_locked"])

            profile_path = ws / ".agents/gravity-workflow/profile.json"
            self.assertTrue(profile_path.is_file())
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(profile["schema_version"], 2)
            self.assertEqual(profile["mode"], "evo")
            self.assertEqual(profile["current_level"], "standard")
            self.assertEqual(profile["suggested_profile"], "standard")
            self.assertNotIn("pace", profile)
            self.assertNotIn("verification", profile)
            self.assertEqual(profile["caveman"], "lite")
            self.assertFalse(profile["caveman_locked"])
            self.assertFalse(profile["locked"])

    def test_full_mode_opt_in_stacks_and_workspace_agents_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws_default = base / "ws_default"
            rc_def = init_workspace.init_workspace(ws_default, mode="full")
            self.assertEqual(rc_def, 0)
            self.assertTrue((ws_default / "AGENTS.md").is_file())
            self.assertTrue((ws_default / ".agents/rules/01-safety-boundaries.md").is_file())
            self.assertTrue((ws_default / ".agents/rules/04-release-checklist.md").is_file())
            self.assertTrue((ws_default / ".agents/rules/05-git-versioning.md").is_file())
            self.assertTrue((ws_default / ".agents/rules/06-engineering-principles.md").is_file())
            self.assertFalse((ws_default / ".agents/rules/02-python-stdlib.md").exists())
            self.assertFalse((ws_default / ".agents/rules/03-api-architecture.md").exists())

            # CLI --with-stack opt-in (répétable)
            ws_stacks = base / "ws_stacks"
            init_cli = PLUGIN_ROOT / "scripts/init_workspace.py"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(init_cli),
                    "--workspace",
                    str(ws_stacks),
                    "--mode",
                    "full",
                    "--with-stack",
                    "python",
                    "--with-stack",
                    "api",
                ],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((ws_stacks / ".agents/rules/02-python-stdlib.md").is_file())
            self.assertTrue((ws_stacks / ".agents/rules/03-api-architecture.md").is_file())

            # workspace_validator contrôle AGENTS.md <= 2500 o, sans BOM, sans CRLF
            with mock.patch("validators.workspace_validator.run_hook_dry_runs"):
                rep_ok = Report()
                validate_workspace(ws_default, rep_ok)
                self.assertTrue(rep_ok.ok(strict=False), rep_ok.errors)

                agents_file = ws_default / "AGENTS.md"
                orig_agents = agents_file.read_bytes()
                agents_file.write_bytes(b"a" * 2501 + b"\n")
                rep_big = Report()
                validate_workspace(ws_default, rep_big)
                self.assertTrue(any("2500" in err for err in rep_big.errors), rep_big.errors)

                agents_file.write_bytes(b"\xef\xbb\xbf" + orig_agents)
                rep_bom = Report()
                validate_workspace(ws_default, rep_bom)
                self.assertTrue(any("BOM" in err for err in rep_bom.errors), rep_bom.errors)

                agents_file.write_bytes(orig_agents.replace(b"\n", b"\r\n"))
                rep_crlf = Report()
                validate_workspace(ws_default, rep_crlf)
                self.assertTrue(any("CRLF" in err for err in rep_crlf.errors), rep_crlf.errors)

    def test_git_versioning_rule_ensure_and_template_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            created = init_workspace.ensure_git_versioning_rule(ws)
            self.assertTrue(created)
            rule_path = ws / ".agents/rules/05-git-versioning.md"
            self.assertTrue(rule_path.is_file())
            self.assertFalse((ws / ".agents/rules/02-python-stdlib.md").exists())
            self.assertFalse((ws / ".agents/rules/03-api-architecture.md").exists())
            self.assertFalse((ws / ".agents/rules/04-release-checklist.md").exists())

            meta, _ = parse_frontmatter(rule_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("trigger"), "model_decision")
            self.assertTrue(str(meta.get("description", "")).strip())

            # Anti-écrasement : un fichier personnalisé est préservé avec force=False
            custom_text = "---\ntrigger: model_decision\ndescription: Custom Git.\n---\n# Custom\n"
            rule_path.write_text(custom_text, encoding="utf-8")
            self.assertFalse(init_workspace.ensure_git_versioning_rule(ws, force=False))
            self.assertEqual(rule_path.read_text(encoding="utf-8"), custom_text)

            # Remplacement uniquement avec force=True
            self.assertTrue(init_workspace.ensure_git_versioning_rule(ws, force=True))
            expected_git_rule = (PLUGIN_ROOT / "templates/.agents/rules/05-git-versioning.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(rule_path.read_text(encoding="utf-8"), expected_git_rule)

            # Vérification CLI --ensure-git-rule idempotente
            cli_ws = Path(tmp) / "cli_proj"
            cli_ws.mkdir()
            init_cli = PLUGIN_ROOT / "scripts/init_workspace.py"
            proc1 = subprocess.run(
                [sys.executable, str(init_cli), "--workspace", str(cli_ws), "--ensure-git-rule"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc1.returncode, 0, proc1.stderr)
            self.assertTrue((cli_ws / ".agents/rules/05-git-versioning.md").is_file())
            self.assertIn("[CREATE]", proc1.stdout)

            proc2 = subprocess.run(
                [sys.executable, str(init_cli), "--workspace", str(cli_ws), "--ensure-git-rule"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
            self.assertIn("[SKIP]", proc2.stdout)

        # Synchronisation exacte avec workspace-template/ si présent dans le dépôt parent
        wt_root = PLUGIN_ROOT.parent / "workspace-template"
        if wt_root.is_dir():
            for src_rel, wt_rel in plugin_validator.TEMPLATE_MAPPINGS:
                self.assertEqual(
                    (wt_root / wt_rel).read_bytes(),
                    (PLUGIN_ROOT / "templates" / src_rel).read_bytes(),
                    f"Dérive entre workspace-template/{wt_rel} et plugin/templates/{src_rel}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
