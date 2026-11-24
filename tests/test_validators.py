#!/usr/bin/env python3
"""Tests unitaires des validateurs (eval_checks, plugin_validator, frontmatter, init_workspace)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _unit_helpers import ensure_plugin_paths

PLUGIN_ROOT = ensure_plugin_paths()
if str(PLUGIN_ROOT / "workflows") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "workflows"))

import eval_checks  # noqa: E402
import init_workspace  # noqa: E402
import patch_apply  # noqa: E402
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

    def test_manual_cases_selection_studio_checks_and_report(self) -> None:
        cases_path = PLUGIN_ROOT / "evals/cases.json"
        all_cases = json.loads(cases_path.read_text(encoding="utf-8"))
        self.assertEqual(len(all_cases), 17)

        # is_manual_case variants
        self.assertFalse(eval_checks.is_manual_case(all_cases[0]))
        self.assertTrue(eval_checks.is_manual_case({"id": "M1", "manual": True, "description": "Test"}))
        self.assertTrue(eval_checks.is_manual_case({"id": "M2", "description": "[MANUAL] Check"}))
        self.assertFalse(eval_checks.is_manual_case("invalid"))

        # select_eval_cases default excludes [manual]
        auto_selected, skipped_ids = eval_checks.select_eval_cases(all_cases)
        self.assertEqual(len(auto_selected), 12)
        self.assertEqual(
            skipped_ids,
            [
                "CASE-13-STUDIO-TRACKED-RO",
                "CASE-14-STUDIO-GIT-RELEASE-GUARD",
                "CASE-15-STUDIO-PLAN-CHECKPOINT",
                "CASE-16-STUDIO-FAIL-CLOSED-BRIDGE",
                "CASE-17-STUDIO-TASKS-NO-DRIFT",
            ],
        )

        # include_manual=True keeps all
        all_selected, skipped_none = eval_checks.select_eval_cases(all_cases, include_manual=True)
        self.assertEqual(len(all_selected), 17)
        self.assertEqual(skipped_none, [])

        # explicit case_ids includes requested [manual] cases
        explicit, skipped_exp = eval_checks.select_eval_cases(
            all_cases,
            selected_ids=["CASE-01-SCHEMA-INTEGRITY", "CASE-13-STUDIO-TRACKED-RO"],
        )
        self.assertEqual([c["id"] for c in explicit], ["CASE-01-SCHEMA-INTEGRITY", "CASE-13-STUDIO-TRACKED-RO"])
        self.assertEqual(skipped_exp, [])

        # manual case without auto_checks returns []
        self.assertEqual(
            eval_checks.run_deterministic_checks(
                {"id": "M-EMPTY", "manual": True, "description": "[manual] Empty"},
                PLUGIN_ROOT,
                {},
            ),
            [],
        )

        # run_deterministic_checks on CASE-13..17
        by_id = {c["id"]: c for c in all_cases}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            # CASE-13 & CASE-14: expect_refusal()
            self.assertEqual(eval_checks.run_deterministic_checks(by_id["CASE-13-STUDIO-TRACKED-RO"], base, {"refused": True}), [])
            self.assertTrue(eval_checks.run_deterministic_checks(by_id["CASE-13-STUDIO-TRACKED-RO"], base, {"refused": False}))
            self.assertEqual(eval_checks.run_deterministic_checks(by_id["CASE-14-STUDIO-GIT-RELEASE-GUARD"], base, {"refused": True}), [])
            self.assertTrue(eval_checks.run_deterministic_checks(by_id["CASE-14-STUDIO-GIT-RELEASE-GUARD"], base, {"refused": False}))

            # CASE-15: docs/dispatch_plan.md with dispatch=true
            (base / "docs").mkdir()
            (base / "docs/dispatch_plan.md").write_text(
                "# Plan\n1. Checkpoint humain unique avant send_message(..., dispatch=true).\n",
                encoding="utf-8",
            )
            self.assertEqual(eval_checks.run_deterministic_checks(by_id["CASE-15-STUDIO-PLAN-CHECKPOINT"], base, {}), [])
            (base / "docs/dispatch_plan.md").write_text("# Plan sans dispatch\n", encoding="utf-8")
            self.assertTrue(eval_checks.run_deterministic_checks(by_id["CASE-15-STUDIO-PLAN-CHECKPOINT"], base, {}))

            # CASE-16: out/bridge_reply.json fail-closed
            (base / "out").mkdir()
            (base / "out/bridge_reply.json").write_text(
                json.dumps({"close": False, "dispatch": False, "status": "blocked", "reason": "test rouge"}),
                encoding="utf-8",
            )
            self.assertEqual(eval_checks.run_deterministic_checks(by_id["CASE-16-STUDIO-FAIL-CLOSED-BRIDGE"], base, {}), [])
            (base / "out/bridge_reply.json").write_text(
                json.dumps({"close": True, "dispatch": False}),
                encoding="utf-8",
            )
            self.assertTrue(eval_checks.run_deterministic_checks(by_id["CASE-16-STUDIO-FAIL-CLOSED-BRIDGE"], base, {}))

            # CASE-17: out/handoff_body.md referencing tasks/2026-11-24-w2.md
            (base / "out/handoff_body.md").write_text(
                "Source : tasks/2026-11-24-w2.md (`run_deterministic_checks`)\n`python3 plugin/scripts/validate.py --strict`\n",
                encoding="utf-8",
            )
            self.assertEqual(eval_checks.run_deterministic_checks(by_id["CASE-17-STUDIO-TASKS-NO-DRIFT"], base, {}), [])
            (base / "out/handoff_body.md").write_text("Résumé vague\n", encoding="utf-8")
            self.assertTrue(eval_checks.run_deterministic_checks(by_id["CASE-17-STUDIO-TASKS-NO-DRIFT"], base, {}))

        # render_markdown_report includes skipped_manual_ids when present
        md = eval_checks.render_markdown_report(
            {
                "timestamp": "2026-11-24T20:00:00Z",
                "passed": 12,
                "total": 12,
                "prefilter_rejected": 0,
                "skipped_manual_ids": ["CASE-13-STUDIO-TRACKED-RO"],
                "results": [],
            }
        )
        self.assertIn("CASE-13-STUDIO-TRACKED-RO", md)
        self.assertIn("[manual]", md)


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
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
                rep_ok = Report()
                validate_plugin(plugin_dir, rep_ok)
                self.assertTrue(rep_ok.ok(strict=True), f"Erreurs inattendues: {rep_ok.errors} {rep_ok.warnings}")

                # 2. Reject forbidden legacy keys ($schema, license, keywords)
                with mock.patch.object(plugin_validator, "check_stdlib_only"):
                    for bad_key in ("$schema", "license", "keywords"):
                        bad_manifest = dict(manifest)
                        bad_manifest[bad_key] = "bad"
                        manifest_path.write_text(json.dumps(bad_manifest) + "\n", encoding="utf-8", newline="\n")
                        rep_bad_key = Report()
                        validate_plugin(plugin_dir, rep_bad_key)
                        self.assertFalse(rep_bad_key.ok(strict=True), f"Devrait rejeter la clé {bad_key}")

                    # 3. Reject missing official field
                    missing_repo = dict(manifest)
                    missing_repo.pop("repository", None)
                    manifest_path.write_text(json.dumps(missing_repo) + "\n", encoding="utf-8", newline="\n")
                    rep_missing = Report()
                    validate_plugin(plugin_dir, rep_missing)
                    self.assertFalse(rep_missing.ok(strict=True))

                    # 4. Reject UTF-8 BOM in plugin.json
                    manifest_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(manifest).encode("utf-8"))
                    rep_bom = Report()
                    validate_plugin(plugin_dir, rep_bom)
                    self.assertFalse(rep_bom.ok(strict=True))
                    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8", newline="\n")

                    # 5. Validate config/module.json v1
                    module_path = plugin_dir / "config" / "module.json"
                    module_data = json.loads(module_path.read_text(encoding="utf-8"))
                    bad_mod = dict(module_data)
                    bad_mod["schema_version"] = 2
                    module_path.write_text(json.dumps(bad_mod) + "\n", encoding="utf-8", newline="\n")
                    rep_sv = Report()
                    validate_plugin(plugin_dir, rep_sv)
                    self.assertFalse(rep_sv.ok(strict=True))

                    bad_name = dict(module_data)
                    bad_name["plugin_name"] = "wrong-plugin"
                    module_path.write_text(json.dumps(bad_name) + "\n", encoding="utf-8", newline="\n")
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

            with mock.patch.object(plugin_validator, "run_hook_dry_runs"), mock.patch.object(
                plugin_validator, "check_stdlib_only"
            ):
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

            with mock.patch.object(plugin_validator, "run_hook_dry_runs"), mock.patch.object(
                plugin_validator, "check_stdlib_only"
            ):
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

    def test_warns_only_if_retired_sidecar_vestige_reappears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            self._build_minimal_plugin(plugin_dir)
            with mock.patch.object(plugin_validator, "run_hook_dry_runs"), mock.patch.object(
                plugin_validator, "check_stdlib_only"
            ):
                rep_clean = Report()
                validate_plugin(plugin_dir, rep_clean)
                self.assertTrue(rep_clean.ok(strict=True), f"Warnings/erreurs inattendus: {rep_clean.errors} {rep_clean.warnings}")

                (plugin_dir / "sidecars" / "live-widget").mkdir(parents=True)
                rep_vestige = Report()
                validate_plugin(plugin_dir, rep_vestige)
                self.assertEqual(rep_vestige.errors, [])
                self.assertTrue(any("sidecars/live-widget" in w for w in rep_vestige.warnings), rep_vestige.warnings)

    def test_ui_mode_and_integrity_checks_int01_03_04_06_09(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            plugin_dir = repo_dir / "plugin"
            self._build_minimal_plugin(plugin_dir)
            wt_dir = repo_dir / "workspace-template"
            wt_src = PLUGIN_ROOT.parent / "workspace-template"
            if wt_src.is_dir():
                shutil.copytree(wt_src, wt_dir)

            with mock.patch.object(plugin_validator, "run_hook_dry_runs"), mock.patch.object(
                plugin_validator, "check_stdlib_only"
            ):
                # Baseline strict OK
                rep_ok = Report()
                validate_plugin(plugin_dir, rep_ok)
                self.assertTrue(rep_ok.ok(strict=True), f"Erreurs/warnings : {rep_ok.errors} {rep_ok.warnings}")

                # ui.mode : transition-mcp accepté, transition-mcp-app rejeté
                mod_path = plugin_dir / "config" / "module.json"
                mod_orig = mod_path.read_text(encoding="utf-8")
                mod_data = json.loads(mod_orig)
                mod_data["ui"]["mode"] = "transition-mcp"
                mod_path.write_text(json.dumps(mod_data, indent=2) + "\n", encoding="utf-8", newline="\n")
                rep_ui_ok = Report()
                validate_plugin(plugin_dir, rep_ui_ok)
                self.assertTrue(rep_ui_ok.ok(strict=True), rep_ui_ok.errors)

                mod_data["ui"]["mode"] = "transition-mcp-app"
                mod_path.write_text(json.dumps(mod_data, indent=2) + "\n", encoding="utf-8", newline="\n")
                rep_ui_bad = Report()
                validate_plugin(plugin_dir, rep_ui_bad)
                self.assertTrue(any("ui.mode" in e for e in rep_ui_bad.errors), rep_ui_bad.errors)
                mod_path.write_text(mod_orig, encoding="utf-8", newline="\n")

                # INT-01 : suggestedPrompts format & <= 100 chars
                manifest_path = plugin_dir / "plugin.json"
                manifest_orig = manifest_path.read_text(encoding="utf-8")
                manifest_bad_fmt = json.loads(manifest_orig)
                manifest_bad_fmt["suggestedPrompts"][0] = "pas une commande"
                manifest_path.write_text(json.dumps(manifest_bad_fmt, indent=2) + "\n", encoding="utf-8", newline="\n")
                rep_pfmt = Report()
                validate_plugin(plugin_dir, rep_pfmt)
                self.assertTrue(any("suggestedPrompts[0]" in e for e in rep_pfmt.errors), rep_pfmt.errors)

                manifest_bad_len = json.loads(manifest_orig)
                manifest_bad_len["suggestedPrompts"][1] = "/gravity-workflow — " + ("a" * 85)
                manifest_path.write_text(json.dumps(manifest_bad_len, indent=2) + "\n", encoding="utf-8", newline="\n")
                rep_plen = Report()
                validate_plugin(plugin_dir, rep_plen)
                self.assertTrue(any("suggestedPrompts[1]" in e and "100" in e for e in rep_plen.errors), rep_plen.errors)
                manifest_path.write_text(manifest_orig, encoding="utf-8", newline="\n")

                # INT-03 : UTF-8 sans BOM + LF sans doublon sur templates/AGENTS.md
                tpl_agents = plugin_dir / "templates/AGENTS.md"
                orig_tpl_agents = tpl_agents.read_bytes()
                tpl_agents.write_bytes(b"\xef\xbb\xbf" + orig_tpl_agents)
                rep_no_dup = Report()
                validate_plugin(plugin_dir, rep_no_dup)
                bom_errs = [e for e in rep_no_dup.errors if "templates/AGENTS.md" in e and "BOM" in e]
                self.assertEqual(len(bom_errs), 1, rep_no_dup.errors)
                tpl_agents.write_bytes(orig_tpl_agents)

                ref00 = plugin_dir / "skills/gravity-workflow/references/00-quick-start.md"
                orig_ref00 = ref00.read_bytes()
                ref00.write_bytes(orig_ref00.replace(b"\n", b"\r\n"))
                rep_ref_crlf = Report()
                validate_plugin(plugin_dir, rep_ref_crlf)
                self.assertTrue(any("00-quick-start.md" in e and "CRLF" in e for e in rep_ref_crlf.errors), rep_ref_crlf.errors)
                ref00.write_bytes(orig_ref00)

                # INT-04 : chemins machine hardcodés rejetés (hors tests/ et fixtures/)
                bad_doc = plugin_dir / "docs_tmp_leak.md"
                bad_doc.write_text("Chemin : /Users/alice/work\n", encoding="utf-8", newline="\n")
                rep_path_mac = Report()
                validate_plugin(plugin_dir, rep_path_mac)
                self.assertTrue(any("chemin machine hardcodé" in e for e in rep_path_mac.errors), rep_path_mac.errors)
                bad_doc.unlink()

                (plugin_dir / "fixtures").mkdir()
                (plugin_dir / "fixtures/sample.md").write_text("C:\\Users\\bob\\ok\n", encoding="utf-8", newline="\n")
                rep_fix_ok = Report()
                validate_plugin(plugin_dir, rep_fix_ok)
                self.assertTrue(rep_fix_ok.ok(strict=True), rep_fix_ok.errors)
                shutil.rmtree(plugin_dir / "fixtures")

                # INT-06 : dérive profile.json vs profile_engine.default_profile()
                tpl_prof = plugin_dir / "templates/.agents/gravity-workflow/profile.json"
                wt_prof = wt_dir / ".agents/gravity-workflow/profile.json"
                orig_prof = tpl_prof.read_bytes()
                bad_prof = json.loads(orig_prof.decode("utf-8"))
                bad_prof["signals"].pop("metrics", None)
                bad_prof_bytes = (json.dumps(bad_prof, indent=2) + "\n").encode("utf-8")
                tpl_prof.write_bytes(bad_prof_bytes)
                wt_prof.write_bytes(bad_prof_bytes)
                rep_prof_drift = Report()
                validate_plugin(plugin_dir, rep_prof_drift)
                self.assertTrue(any("default_profile()" in e for e in rep_prof_drift.errors), rep_prof_drift.errors)
                tpl_prof.write_bytes(orig_prof)
                wt_prof.write_bytes(orig_prof)

                # INT-09 : dérive workspace-template PROGRESS.md & live_state.json vs init_workspace._render_*("full")
                wt_prog = wt_dir / ".agents/gravity-workflow/PROGRESS.md"
                orig_prog = wt_prog.read_text(encoding="utf-8")
                wt_prog.write_text(orig_prog + "\n# drift\n", encoding="utf-8", newline="\n")
                rep_prog_drift = Report()
                validate_plugin(plugin_dir, rep_prog_drift)
                self.assertTrue(any("_render_progress_md" in e for e in rep_prog_drift.errors), rep_prog_drift.errors)
                wt_prog.write_text(orig_prog, encoding="utf-8", newline="\n")

                wt_live = wt_dir / ".agents/gravity-workflow/observability/live_state.json"
                orig_live = wt_live.read_text(encoding="utf-8")
                wt_live.write_text(orig_live.replace('"idle"', '"busy"'), encoding="utf-8", newline="\n")
                rep_live_drift = Report()
                validate_plugin(plugin_dir, rep_live_drift)
                self.assertTrue(any("_render_live_state_json" in e for e in rep_live_drift.errors), rep_live_drift.errors)
                wt_live.write_text(orig_live, encoding="utf-8", newline="\n")

    def test_validators_count_only_auto_cases_and_check_progress_and_release_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            plugin_dir = repo_dir / "plugin"
            self._build_minimal_plugin(plugin_dir)
            wt_dir = repo_dir / "workspace-template"
            shutil.copytree(PLUGIN_ROOT.parent / "workspace-template", wt_dir)

            with mock.patch.object(plugin_validator, "run_hook_dry_runs"), mock.patch.object(
                plugin_validator, "check_stdlib_only"
            ), mock.patch(
                "validators.workspace_validator.run_hook_dry_runs"
            ):
                # 12 auto + 5 [manual] passes strict without warnings in both validators
                rep_p = Report()
                validate_plugin(plugin_dir, rep_p)
                self.assertTrue(rep_p.ok(strict=True), f"{rep_p.errors} {rep_p.warnings}")

                rep_w = Report()
                validate_workspace(wt_dir, rep_w)
                self.assertTrue(rep_w.ok(strict=True), f"{rep_w.errors} {rep_w.warnings}")

                # Dropping auto cases below 10 triggers warning even with many [manual] cases
                cases_data = json.loads((plugin_dir / "evals/cases.json").read_text(encoding="utf-8"))
                reduced = cases_data[:5] + [c for c in cases_data if eval_checks.is_manual_case(c)]
                reduced_txt = json.dumps(reduced, indent=2, ensure_ascii=False) + "\n"
                (plugin_dir / "evals/cases.json").write_text(reduced_txt, encoding="utf-8", newline="\n")
                (wt_dir / ".agents/gravity-workflow/evals/cases.json").write_text(
                    reduced_txt, encoding="utf-8", newline="\n"
                )

                rep_p_warn = Report()
                validate_plugin(plugin_dir, rep_p_warn)
                self.assertTrue(any("5 cas auto" in w for w in rep_p_warn.warnings), rep_p_warn.warnings)

                rep_w_warn = Report()
                validate_workspace(wt_dir, rep_w_warn)
                self.assertTrue(any("5 cas auto" in w for w in rep_w_warn.warnings), rep_w_warn.warnings)

        # PROGRESS.md clarifies (8 étapes + 1 bonus) in both modes and matches workspace-template
        full_prog = init_workspace._render_progress_md("full")
        qs_prog = init_workspace._render_progress_md("quickstart")
        self.assertIn("## Avancement des étapes (8 étapes + 1 bonus)", full_prog)
        self.assertIn("8 étapes + 1 bonus", full_prog.splitlines()[2])
        self.assertIn("(8 étapes + 1 bonus)", qs_prog.splitlines()[2])
        wt_prog_txt = (PLUGIN_ROOT.parent / "workspace-template/.agents/gravity-workflow/PROGRESS.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(wt_prog_txt, full_prog)

        # 04-release-checklist.md byte parity + updated requirements
        tpl_chk = (PLUGIN_ROOT / "templates/.agents/rules/04-release-checklist.md").read_bytes()
        wt_chk = (PLUGIN_ROOT.parent / "workspace-template/.agents/rules/04-release-checklist.md").read_bytes()
        self.assertEqual(tpl_chk, wt_chk)
        chk_txt = tpl_chk.decode("utf-8")
        self.assertIn("`100 % PASS` auto, plus cas `[manual]` rejoués si applicables", chk_txt)
        self.assertIn("python3", chk_txt)
        self.assertIn("python", chk_txt)
        self.assertIn("CHANGELOG.md", chk_txt)


class TestReviewPipelineHelpers(unittest.TestCase):
    def test_patch_detection_save_apply_and_safe_worktree_cleanup(self) -> None:
        unified = (
            "diff --git a/hello.txt b/hello.txt\n"
            "index 8ab686e..c00c20b 100644\n"
            "--- a/hello.txt\n"
            "+++ b/hello.txt\n"
            "@@ -1 +1 @@\n"
            "-hello\n"
            "+world\n"
        )
        snippet = "--- hello.txt ---\nworld\n"
        self.assertTrue(patch_apply._is_unified_diff(unified))
        self.assertFalse(patch_apply._is_unified_diff(snippet))
        self.assertFalse(patch_apply._is_unified_diff(""))

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wf_home = base / "wf_home"
            repo = base / "repo"
            repo.mkdir()
            (repo / "hello.txt").write_text("hello\n", encoding="utf-8", newline="\n")
            for args in (
                ["git", "init", "-q"],
                ["git", "config", "user.email", "test@example.com"],
                ["git", "config", "user.name", "Test"],
                ["git", "add", "hello.txt"],
                ["git", "commit", "-q", "-m", "init"],
            ):
                subprocess.run(args, cwd=str(repo), check=True, capture_output=True)

            wt_dir = base / "isolated_wt"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat/test", str(wt_dir)],
                check=True,
                capture_output=True,
            )
            (wt_dir / "hello.txt").write_text("world\n", encoding="utf-8", newline="\n")

            # _read_worktree_patch fallback -> git -C <wt_dir> diff
            read_diff = patch_apply._read_worktree_patch(str(wt_dir), "", ["hello.txt"])
            self.assertIn("diff --git a/hello.txt b/hello.txt", read_diff)
            self.assertIn("+world", read_diff)

            with mock.patch.dict(os.environ, {"GRAVITY_WORKFLOW_HOME": str(wf_home)}):
                with mock.patch.object(patch_apply, "WS_ROOT", repo):
                    # Non-unified snippet is rejected (returns None)
                    self.assertIsNone(patch_apply._safe_save_verified_patch(snippet, str(wt_dir)))
                    # Unified diff is saved in UTF-8 without BOM + LF
                    saved = patch_apply._safe_save_verified_patch(read_diff.replace("\n", "\r\n"), str(wt_dir))
                    self.assertIsNotNone(saved)
                    assert saved is not None
                    raw_saved = saved.read_bytes()
                    self.assertFalse(raw_saved.startswith(b"\xef\xbb\xbf"))
                    self.assertNotIn(b"\r\n", raw_saved)

                # Safety guard: cleanup refuses caller_workspace, parent, plugin root, .git
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(repo, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(base, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(PLUGIN_ROOT, repo))
                self.assertFalse(patch_apply.cleanup_worktree_best_effort(repo / ".git", repo))
                self.assertTrue(repo.is_dir())

                # CLI --apply applies patch onto repo and cleans isolated worktree
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(PLUGIN_ROOT / "workflows/patch_apply.py"),
                        "--apply",
                        "--workspace",
                        str(repo),
                        "--worktree",
                        str(wt_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual((repo / "hello.txt").read_text(encoding="utf-8"), "world\n")
                self.assertFalse(wt_dir.exists())


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
