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


if __name__ == "__main__":
    unittest.main(verbosity=2)
