#!/usr/bin/env python3
"""Tests unitaires de l'inspection AST Python (run_workflow et python -c)."""

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


class TestSafetyPythonAst(unittest.TestCase):
    def test_inspect_workflow_code_ast_and_no_false_positives(self) -> None:
        # Actions destructrices et fuites de secrets -> deny
        for denied_wf in (
            "import shutil\nshutil.rmtree('/')\n",
            "import shutil\nshutil.rmtree('/tmp/..')\n",
            "import os\nprint(os.environ)\n",
            "import os\nprint(getattr(os, 'environ'))\n",
            "print(open('.' + 'env').read())\n",
            "import subprocess\nsubprocess.run(['git', 'push', '--force'])\n",
            "import os\nos.system('rm -rf /')\n",
            "open('.agents/hooks.json', 'w').write('{}')\n",
        ):
            res = safety_policy.inspect_workflow_code(denied_wf)
            self.assertIsNotNone(res, f"Devrait bloquer workflow : {denied_wf!r}")
            assert res is not None
            self.assertEqual(res[0], "deny", f"Attendu deny sur {denied_wf!r}, obtenu {res}")

        # Utilitaires courants de workflow -> autorisés sans force_ask systématique
        for benign_wf in (
            "import shutil\nshutil.rmtree('/tmp/build_cache')\n",
            "import os\nos.remove('a.txt')\n",
            "import os\nos.replace('a.txt', 'b.txt')\n",
            "import os\nos.getenv('APP_MODE')\n",
            "import subprocess\nsubprocess.run(['pytest', '-q'])\n",
            "import importlib\nimportlib.import_module('json')\n",
            "items = ['a', 'b']\nitems.remove('a')\ntext = 'hello'.replace('h', 'j')\n",
        ):
            self.assertIsNone(
                safety_policy.inspect_workflow_code(benign_wf),
                f"Faux positif workflow sur : {benign_wf!r}",
            )

        res_bad_syntax = safety_policy.inspect_workflow_code("def broken(:\n    pass\n")
        self.assertIsNotNone(res_bad_syntax)
        assert res_bad_syntax is not None
        self.assertEqual(res_bad_syntax[0], "force_ask")

    def test_inline_python_threat_detection_and_benign_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws_root = make_gravity_workspace(Path(tmp), "ws")
            (ws_root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")

            ps_herestring = (
                "python -c @'\n"
                "import subprocess, pathlib\n"
                "files = subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines()\n"
                "for f in files:\n"
                "    p = pathlib.Path(f)\n"
                "    if p.is_file():\n"
                "        data = p.read_bytes()\n"
                "        print(f, b'\\r\\n' in data, data.startswith(b'\\xef\\xbb\\xbf'))\n"
                "'@"
            )
            allow_cmds = (
                'python -c "print(sum(range(10)))"',
                'python -c "import subprocess; print(subprocess.check_output([\'git\', \'status\', \'-sb\'], text=True))"',
                'python -c "import subprocess; subprocess.run([\'git\', \'log\', \'-S\', \'.env\'])"',
                'python -c "import re; print(re.findall(r\'\\.env\', \'hello\'))"',
                'python -c "import os; print(os.path.exists(r\'$env:USERPROFILE\'))"',
                'python -c "import os; print([r for r, _, _ in os.walk(\'.\')])"',
                'python -c "compile(open(\'AGENTS.md\').read(), \'AGENTS.md\', \'exec\')"',
                'python -c "import json; print(json.__file__)"',
                'python -c "import ctypes; print(ctypes)"',
                'python -c "open(\'out.txt\', \'w\').write(\'x\')"',
                'python -c "from pathlib import Path; Path(\'x.txt\').write_text(\'a\')"',
                'python -c "import shutil; shutil.rmtree(\'/tmp/build_cache\')"',
                "python -c 'x = ''1''; print(x)'",
                'python -c "print(`"hello`")"',
                ps_herestring,
                'python -c"print(1)"',
                'python -c "import os; os.system(\'echo ok\')"',
            )
            assert_run_commands_decision(self, safety_policy, (ws_root,), allow_cmds, "allow", "python_allow")

            deny_cmds = (
                'python -c "import os; print(os.environ)"',
                'python -c "import os; print(os.environb)"',
                'python -c "import os; print(getattr(os, \'environ\'))"',
                'python -c "import os; print(getattr(os, \'env\' + \'iron\'))"',
                'python -c "import os; print(os.__dict__[\'environ\'])"',
                'python -c "import os; print(vars(os)[\'environ\'])"',
                'python -c "from os import environ; print(environ)"',
                'python -c "print(open(\'.env\').read())"',
                'python -c "open(\'.\' + \'env\').read()"',
                'python -c "a = \'.\'; b = \'env\'; print(open(a + b).read())"',
                'python -c "open(\'\'.join([\'.\', \'env\'])).read()"',
                'python -c "open(chr(46) + \'env\').read()"',
                'python -c "import os; print(open(os.path.join(\'foo\', \'..\', \'.env\')).read())"',
                'python -c "from pathlib import Path; print((Path(\'.ssh\') / \'id_rsa\').read_text())"',
                "python -c 'print(open(''.env'').read())'",
                'python -c "print(open(`".env`").read())"',
                'python -c "open(\'.agents/hooks.json\', \'w\').write(\'{}\')"',
                'python -c "import shutil; shutil.rmtree(\'/\')"',
                'python -c "import shutil; shutil.rmtree(\'C:\\\\\')"',
                'python -c "from shutil import rmtree; rmtree(\'/tmp/..\')"',
                'python -c "import subprocess; subprocess.run([\'rm\', \'-rf\', \'/\'])"',
                'python -c "import subprocess; branch = \'main\'; subprocess.run([\'git\', \'push\', \'--force\', \'origin\', branch])"',
                'python -c "import subprocess; subprocess.run([\'echo\', open(\'.env\').read()])"',
                'python -c "import subprocess; subprocess.run([\'sh\', \'-c\', \'rm -rf /\'])"',
                'python -c "import os; os.system(\'rm -rf /\')"',
                'python -c "import os; os.popen(\'git push --force\')"',
                'python -c "exec(\'import os; print(os.environ)\')"',
            )
            assert_run_commands_decision(self, safety_policy, (ws_root,), deny_cmds, "deny", "python_deny")

    def test_adversarial_python_vars_mutations_and_control_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            gravity_ws = make_gravity_workspace(base)
            plain_ws = base / "plain_ws"
            plain_ws.mkdir()

            deny_cmds = (
                "python -c \"import subprocess; cmd = ['rm', '-rf', '/']; subprocess.run(cmd)\"",
                "python -c \"import subprocess; cmd = ('rm', '-rf', '/'); subprocess.Popen(cmd)\"",
                "python -c \"import subprocess; cmd = ['rm', '-rf']; cmd.append('/'); subprocess.run(cmd)\"",
                "python -c \"import subprocess; cmd = ['rm', '-rf']; cmd += ['/']; subprocess.run(cmd)\"",
                "python -c \"import subprocess; cmd = ['git', 'push']; cmd.extend(['--force', 'origin', 'main']); subprocess.run(cmd)\"",
                "python -c \"import subprocess; a = ['rm', '-rf']; subprocess.run([*a, '/'])\"",
                "python -c \"import subprocess, shlex; subprocess.run(shlex.split('rm -rf /'))\"",
                "python -c \"import subprocess; subprocess.run('rm -rf /'.split())\"",
                "python -c \"import os; cmd = ['rm', '-rf', '/']; os.system(' '.join(cmd))\"",
                "python -c \"import os; cmd = 'echo ok'; cmd = 'rm -rf /'; os.system(cmd)\"",
                "python -c \"import os; cmd = ['rm', '-rf', '/']; os.execvp('rm', cmd)\"",
                "python -c \"from os import execvp; cmd = ['rm', '-rf', '/']; execvp('rm', cmd)\"",
                "python -c \"__import__('subprocess').run(['rm', '-rf', '/'])\"",
                "python -c \"import subprocess; f = subprocess.run; f(['rm', '-rf', '/'])\"",
                "python -c \"def go():\n    cmd = ['rm', '-rf', '/']\n    import subprocess; subprocess.run(cmd)\ngo()\"",
                "python -c \"p = '.'; p += 'env'; open(p).read()\"",
                "python -c \"import subprocess; cmd = ['git', 'log', '-S', '.env']; subprocess.run(cmd); open(cmd[3])\"",
                "python -c \"import subprocess\nif True:\n    cmd = ['rm', '-rf', '/']\nelse:\n    cmd = ['echo', 'ok']\nsubprocess.run(cmd)\"",
                "python -c \"import subprocess; subprocess.run(['rm', '-rf', '/'] if True else ['echo', 'ok'])\"",
                "python -c \"a, b = '.', 'env'; open(a + b).read()\"",
                "python -c \"import subprocess; x = cmd = ['rm', '-rf', '/']; subprocess.run(cmd)\"",
                "python -c \"import subprocess; cmd = ['rm', '-rf', '/tmp/x']; cmd[2] = '/'; subprocess.run(cmd)\"",
                "python -c \"import subprocess; cmd = ['rm', '/']; cmd.insert(1, '-rf'); subprocess.run(cmd)\"",
                "python -c \"import os; os.execvp('rm', ['x', '-rf', '/'])\"",
                "python -c \"import os; os.execlp('rm', 'x', '-rf', '/')\"",
                "python -c \"import subprocess; cmd = [x for x in ['rm', '-rf', '/']]; subprocess.run(cmd)\"",
                "python -c \"import subprocess; cmd = ['/', '-rf', 'rm'][::-1]; subprocess.run(cmd)\"",
                "python -c \"import subprocess; subprocess.run(cmd := ['rm', '-rf', '/'])\"",
                "python -c \"import subprocess\ndef mk(): return ['rm', '-rf', '/']\nsubprocess.run(mk())\"",
            )
            assert_run_commands_decision(self, safety_policy, (gravity_ws, plain_ws), deny_cmds, "deny", "ast_deny")

            allow_cmds = (
                "python -c \"import subprocess; cmd = ['git', 'status', '-sb']; subprocess.run(cmd)\"",
                "python -c \"import subprocess; cmd = ['git', 'log', '-S', '.env']; subprocess.run(cmd)\"",
                "python -c \"import subprocess; cmd = ['git', 'log', '-S']; cmd.append('.env'); subprocess.run(cmd)\"",
                "python -c \"from re import search; print(search(r'\\.env', 'x'))\"",
                "python -c \"import re; pat = r'\\.env'; print(re.search(pat, 'x'))\"",
            )
            assert_run_commands_decision(self, safety_policy, (gravity_ws, plain_ws), allow_cmds, "allow", "ast_allow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
