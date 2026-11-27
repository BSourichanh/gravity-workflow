#!/usr/bin/env python3
"""Tests unitaires d'inspection des here-strings PowerShell, heredocs POSIX et scripts inline JS/Perl/Ruby."""

import tempfile
import unittest
from pathlib import Path

from _unit_helpers import (
    assert_run_commands_decision,
    ensure_plugin_paths,
    make_gravity_workspace,
)

ensure_plugin_paths()

import safety_policy  # noqa: E402


class TestSafetyInlineScripts(unittest.TestCase):
    def test_adversarial_herestrings_and_posix_heredocs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            gravity_ws = make_gravity_workspace(base)
            plain_ws = base / "plain_ws"
            plain_ws.mkdir()

            deny_cmds = (
                "@'\nimport os\nprint(os.environ)\n'@ | python -",
                "@'\nimport subprocess\ncmd = ['rm', '-rf', '/']\nsubprocess.run(cmd)\n'@ | python -",
                "@'\nprint(open('.env').read())\n'@ | python3 -u -",
                '@"\nimport os\nprint(os.environ)\n"@ | py -3 -',
                "@'\nrm -rf /\n'@ | sh",
                "@'\nconsole.log(process.env)\n'@ | node -",
                "$code = @'\nimport os\nprint(os.environ)\n'@\n$code | python -",
                "$code=@'\nimport os\nprint(os.environ)\n'@\n$code | python -",
                "$code = @'\nimport subprocess\nsubprocess.run(['rm', '-rf', '/'])\n'@; python -c $code",
                "$c=@'\nimport subprocess\ncmd=['rm','-rf','/']\nsubprocess.run(cmd)\n'@; py -3 -c $c",
                "@'\nimport os\nprint(os.environ)\n'@ | python -c \"import sys; exec(sys.stdin.read())\"",
                "@'\nimport subprocess\nsubprocess.run(['rm', '-rf', '/'])\n'@ | python -c \"import sys; exec(sys.stdin.read())\"",
                "Write-Output @'\nimport os\nprint(os.environ)\n'@ | python -",
                "echo 'import os; print(os.environ)' | python -",
                "python - << 'EOF'\nimport os\nprint(os.environ)\nEOF",
                "cat << 'EOF' | python -\nimport os\nprint(os.environ)\nEOF",
                "cat << 'A' >/dev/null; python - << 'B'\nok\nA\nimport os\nprint(os.environ)\nB",
                "echo \"<< 'X'\" && python - << 'EOF'\nimport os\nprint(os.environ)\nEOF",
                "sh << 'EOF'\nrm -rf /\nEOF",
                "python - <<< 'import os; print(os.environ)'",
            )
            assert_run_commands_decision(self, safety_policy, (gravity_ws, plain_ws), deny_cmds, "deny", "heredoc_deny")

            allow_cmds = (
                "@'\nimport pathlib\nprint(pathlib.Path('README.md').read_text())\n'@ | python -",
                "@'\nimport re\nprint(re.findall(r'\\.env', 'hello'))\n'@ | python -",
                "Write-Output @'\nimport re\nprint(re.findall(r'\\.env', 'hello'))\n'@ | python -",
                "echo \"import re; print(re.findall(r'\\.env', 'x'))\" | python -",
                "@'\nimport subprocess\ncmd = ['git', 'status', '-sb']\nsubprocess.run(cmd)\n'@ | python -",
                "$code = @'\nprint(1 + 1)\n'@\n$code | python -",
                "echo '{\"a\": 1}' | python -m json.tool",
                "python - << 'EOF'\nprint(42)\nEOF",
            )
            assert_run_commands_decision(self, safety_policy, (gravity_ws, plain_ws), allow_cmds, "allow", "heredoc_allow")

    def test_adversarial_inline_node_perl_ruby_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            gravity_ws = make_gravity_workspace(base)
            plain_ws = base / "plain_ws"
            plain_ws.mkdir()

            deny_cmds = (
                """node -e 'require("fs").readFileSync(`${"."}env`)'""",
                """node -e 'const a = "."; require("fs").readFileSync(`${a}env`)'""",
                """node -e 'const a = "."; const b = "env"; require("fs").readFileSync(a + b)'""",
                """node -e 'const a = ".", b = "env"; require("fs").readFileSync(`${a}${b}`)'""",
                """node -e 'a = "."; b = "env"; require("fs").readFileSync(`${a}${b}`)'""",
                """node -e 'let c = "echo ok"; c = `rm -rf ${"/"}`; require("child_process").execSync(c)'""",
                """node -e '/* ` */ require("child_process").execSync(`rm -rf ${"/"}`)'""",
                "node -e \"// '\nrequire('child_process').execSync('rm -rf /')\"",
                """node -e 'require("child_process")["execSync"](`rm -rf ${"/"}`)'""",
                """node -e 'require("child_process")[`exec${"Sync"}`](`rm -rf ${"/"}`)'""",
                """node -e 'require("fs")["rmSync"](`${"/"}`)'""",
                """node -e 'const p = process; console.log(p.env)'""",
                """node -e 'console.log(process[["e","n","v"].join("")])'""",
                """node -e 'require("fs").readFileSync("\\x2eenv")'""",
                """node -e 'require("fs").readFileSync(`\\u002eenv`)'""",
                """node -e 'const args = ["-rf"]; args.push(`${"/"}`); require("child_process").spawnSync("rm", args)'""",
                """node -e 'require("fs").readFileSync(String.fromCharCode(46, 101, 110, 118))'""",
                """node -e 'require("fs").readFileSync(`${dir}/.env`)'""",
                """node -e 'require("child_process").execSync(`rm -rf ${"/"}`)'""",
                """node -e 'const cmd = `rm -rf ${"/"}`; require("child_process").execSync(cmd)'""",
                """node -e 'require("child_process").spawnSync("rm", [`-rf`, `${"/"}`])'""",
                """node -e 'const args = ["-rf", `${"/"}`]; require("child_process").spawnSync("rm", args)'""",
                """node -e 'require("fs").rmSync(`/`)'""",
                """node -e 'require("fs").rmSync(`${"/"}`, { recursive: true, force: true })'""",
                """node -e 'console.log(`${process.env.API_KEY}`)'""",
                """node -e 'console.log(process[`${"en"}v`])'""",
                """node -e 'const { env } = process; console.log(env)'""",
                """node -e 'require("fs").writeFileSync(`${".agents"}/hooks.json`, "{}")'""",
                """ruby -e "system('rm', '-rf', '/')" """,
                """perl -e "system('rm', '-rf', '/')" """,
                """ruby -e 'File.read("#{"."}env")'""",
                """ruby -e 'system("rm -rf #{"/"}")'""",
            )
            assert_run_commands_decision(self, safety_policy, (gravity_ws, plain_ws), deny_cmds, "deny", "inline_deny")

            allow_cmds = (
                """node -e 'console.log(`hello ${name}`)'""",
                """node -e 'console.log(`1 + 1 = ${1 + 1}`)'""",
                """node -e 'const x = "ok"; console.log(`status: ${x}`)'""",
                """node -e 'console.log(new RegExp("\\\\.env").test("hello"))'""",
                """node -e 'require("child_process").execSync(`git status -sb`)'""",
                """node -e 'const d = "/tmp/build_cache"; require("fs").rmSync(`${d}`, { recursive: true, force: true })'""",
            )
            assert_run_commands_decision(self, safety_policy, (gravity_ws, plain_ws), allow_cmds, "allow", "inline_allow")

    def test_srp_split_modules_and_reexports(self) -> None:
        import ast
        from policy import inline_scripts, js_eval, shell_parser, stdin_inspect

        js_exports = (
            "_advance_js_quoted_or_template",
            "_collect_js_var_bindings",
            "_decode_js_escape",
            "_eval_js_str_expr",
            "_expand_js_templates",
            "_extract_balanced_brackets",
            "_extract_balanced_delims",
            "_extract_balanced_parens",
            "_parse_js_array_expr",
            "_scan_js_template_literal",
            "_split_js_statements",
            "_split_script_top_level",
            "_strip_js_comments",
            "_unescape_quoted_literal",
        )
        stdin_exports = (
            "_collect_ps_var_assignment",
            "_extract_inline_spec",
            "_extract_piped_literal_payload",
            "_get_stdin_interpreter_kind",
            "_inspect_stdin_payload",
        )
        inline_local = (
            "_expand_ruby_interpolations",
            "_fold_inline_concat",
            "_inspect_inline_script_code",
            "_tokenize_script_literals",
        )

        for mod_obj, expected in (
            (js_eval, js_exports),
            (stdin_inspect, stdin_exports),
            (inline_scripts, inline_local),
        ):
            for sym in expected:
                self.assertTrue(hasattr(mod_obj, sym), f"{sym} manquant sur {mod_obj.__name__}")

        for sym in js_exports:
            self.assertIs(getattr(inline_scripts, sym), getattr(js_eval, sym))
        for sym in stdin_exports:
            self.assertIs(getattr(inline_scripts, sym), getattr(stdin_inspect, sym))
        safety_reexports = tuple(s for s in js_exports + stdin_exports + inline_local if s != "_advance_js_quoted_or_template")
        for sym in safety_reexports:
            self.assertIs(getattr(safety_policy, sym), getattr(inline_scripts, sym))

        # Vérifie le DAG acyclique au niveau module (js_eval / stdin_inspect n'importent pas inline_scripts au top-level)
        for mod_path in (Path(js_eval.__file__), Path(stdin_inspect.__file__)):
            tree = ast.parse(mod_path.read_text(encoding="utf-8"))
            for stmt in tree.body:
                if isinstance(stmt, ast.ImportFrom) and stmt.module:
                    self.assertNotIn("inline_scripts", stmt.module)
                elif isinstance(stmt, ast.Import):
                    for alias in stmt.names:
                        self.assertNotIn("inline_scripts", alias.name)

        # Vérifie directement les fonctions SRP des 3 modules
        stripped = js_eval._strip_js_comments("const a = '.'; // secret\nconst b = `env`;")
        self.assertNotIn("secret", stripped)
        s_vars, _, _, _, aliases = js_eval._collect_js_var_bindings(stripped)
        self.assertEqual(s_vars, {"a": ".", "b": "env"})
        self.assertIn("process", aliases)
        self.assertEqual(js_eval._eval_js_str_expr("`${a}${b}`", s_vars), ".env")

        head, rich_args, _ = shell_parser._extract_command_head_args_rich("node -e 'console.log(1)'")
        spec = stdin_inspect._extract_inline_spec(head, rich_args)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec[0], "node")

        ps_assign = stdin_inspect._collect_ps_var_assignment("$code = @'\nprint(1)\n'@")
        self.assertEqual(ps_assign, ("$code", "print(1)", True))
        payload, is_bare = stdin_inspect._extract_piped_literal_payload("$code", {"$code": "print(1)"})
        self.assertEqual((payload, is_bare), ("print(1)", True))

        _, rich_py, _ = shell_parser._extract_command_head_args_rich("python3 -u -")
        self.assertEqual(stdin_inspect._get_stdin_interpreter_kind("python3", rich_py), "python")
        deny_py, _ = stdin_inspect._inspect_stdin_payload("python", "import os\nprint(os.environ)\n")
        self.assertIsNotNone(deny_py)
        deny_js, _ = stdin_inspect._inspect_stdin_payload("node", "console.log(process.env)")
        self.assertIsNotNone(deny_js)
        self.assertEqual(stdin_inspect._inspect_stdin_payload("node", "console.log(1)"), (None, None))

        self.assertEqual(inline_scripts._expand_ruby_interpolations('"#{ \'.\' + \'env\' }"'), '".env"')
        folded, lits = inline_scripts._fold_inline_concat("'.' + 'env'")
        self.assertEqual((folded, lits), ("'.env'", [".env"]))
        res_ruby = inline_scripts._inspect_inline_script_code("ruby", 'File.read("#{"."}env")')
        self.assertIsNotNone(res_ruby)
        assert res_ruby is not None
        self.assertEqual(res_ruby[0], "deny")


if __name__ == "__main__":
    unittest.main(verbosity=2)
