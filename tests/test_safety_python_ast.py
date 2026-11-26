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
from policy.python_ast import _raw_code_hard_deny_check  # noqa: E402


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

    def test_harness_readonly_allowed_and_mutations_or_secrets_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ws_root = make_gravity_workspace(base, "ws_ro")
            plain_ws = base / "plain_ro"
            plain_ws.mkdir()

            readonly_allow_cmds = (
                'python -c "from pathlib import Path; print(Path(\'.agents/hooks.json\').read_text(encoding=\'utf-8\'))"',
                'python -c "from pathlib import Path; print(len(Path(\'.agents/rules/01-safety-boundaries.md\').read_bytes()))"',
                'python -c "print(open(\'.agents/hooks.json\').read())"',
                'python -c "print(open(\'.agents/hooks.json\', \'r\', encoding=\'utf-8\').read())"',
                'python -c "with open(\'.agents/hooks.json\', \'rb\') as f: print(len(f.read()))"',
                'python -c "from pathlib import Path\nwith Path(\'.agents/hooks.json\').open(\'rt\', encoding=\'utf-8\') as f:\n    print(f.read().splitlines())"',
                'python -c "import json; print(json.load(open(\'.agents/hooks.json\')))"',
                'python -c "from pathlib import Path; p = Path(\'.agents/rules/01-safety-boundaries.md\'); print(p.exists(), p.is_file(), p.is_dir(), p.is_symlink(), p.stat().st_size, p.lstat().st_size, p.name, p.stem, p.suffix, p.parent.name, p.parts)"',
                'python -c "from pathlib import Path\nfor f in sorted(Path(\'.agents/gravity-workflow/hooks\').glob(\'*.py\')):\n    print(f.name, len(f.read_text().splitlines()))"',
                'python -c "from pathlib import Path; print([f.name for f in Path(\'.agents/gravity-workflow/hooks\').rglob(\'*.py\')])"',
                'python -c "from pathlib import Path\nfor f in Path(\'.agents/gravity-workflow/hooks\').iterdir():\n    if f.is_file(): print(f.read_text())"',
                'python -c "import os; print(sorted(os.listdir(\'.agents/gravity-workflow/hooks\')))"',
                'python -c "import os\nwith os.scandir(\'.agents/gravity-workflow/hooks\') as it:\n    print([e.name for e in it])"',
                'python -c "import os\nfor r, dirs, files in os.walk(\'.agents/gravity-workflow/hooks\'):\n    print(r, len(files))"',
                'python -c "import os; p = os.path.join(\'.agents\', \'hooks.json\'); print(os.path.exists(p), os.access(p, os.R_OK), os.fspath(p))"',
                'python -c "p = \'.agents/hooks.json\'; compile(open(p).read(), p, \'exec\')"',
                'python -c "import os; print(repr(str(os.path.join(\'.agents\', \'hooks.json\'))))"',
            )
            assert_run_commands_decision(
                self, safety_policy, (ws_root, plain_ws), readonly_allow_cmds, "allow", "harness_ro_allow"
            )

            mutation_and_secret_deny_cmds = (
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').write_text(\'{}\')"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').write_bytes(b\'{}\')"',
                'python -c "open(\'.agents/hooks.json\', \'w\').write(\'{}\')"',
                'python -c "open(\'.agents/hooks.json\', \'a\').write(\'{}\')"',
                'python -c "open(\'.agents/hooks.json\', \'r+\').write(\'{}\')"',
                'python -c "m = input(); open(\'.agents/hooks.json\', m)"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').open(\'w\').write(\'{}\')"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').unlink()"',
                'python -c "from pathlib import Path; Path(\'.agents/gravity-workflow/hooks\').rmdir()"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').rename(\'x\')"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').replace(\'x\')"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').chmod(0o777)"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').touch()"',
                'python -c "from pathlib import Path; Path(\'.agents/gravity-workflow/hooks\').mkdir()"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').symlink_to(\'x\')"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').hardlink_to(\'x\')"',
                'python -c "import shutil; shutil.copy(\'.agents/hooks.json\', \'/tmp/x\')"',
                'python -c "import os; os.remove(\'.agents/hooks.json\')"',
                'python -c "import os; os.unlink(\'.agents/hooks.json\')"',
                'python -c "import os; os.rmdir(\'.agents/gravity-workflow/hooks\')"',
                'python -c "import os; os.rename(\'.agents/hooks.json\', \'x\')"',
                'python -c "import os; os.replace(\'.agents/hooks.json\', \'x\')"',
                'python -c "import os; os.chmod(\'.agents/hooks.json\', 0o777)"',
                'python -c "import os; os.open(\'.agents/hooks.json\', os.O_RDONLY)"',
                'python -c "from pathlib import Path; getattr(Path(\'.agents/hooks.json\'), \'unlink\')()"',
                'python -c "from pathlib import Path\nfor f in Path(\'.agents/gravity-workflow/hooks\').glob(\'*.py\'):\n    f.unlink()"',
                'python -c "import subprocess; subprocess.run([\'echo\', \'ok\'], cwd=\'.agents/gravity-workflow/hooks\')"',
                'python -c "def custom(x): return x\ncustom(\'.agents/hooks.json\')"',
                'python -c "from os import unlink as print; print(\'.agents/hooks.json\')"',
                'python -c "import os; open = os.unlink; open(\'.agents/hooks.json\')"',
                'python -c "import os\ndef print(p): os.unlink(p)\nprint(\'.agents/hooks.json\')"',
                'python -c "import shutil as os; os.rmtree(\'.agents/gravity-workflow/hooks\')"',
                'python -c "from os import *\nopen(\'.agents/hooks.json\', 0)"',
                'python -c "import os; open(\'.agents/hooks.json\', \'r\', -1, None, None, None, True, lambda p, f: os.unlink(p))"',
                'python -c "from pathlib import Path; Path(\'.agents/hooks.json\').open(\'r\', -1, None, None, None, None)"',
                'python -c "m = \'r\'; m = input(); open(\'.agents/hooks.json\', m)"',
                'python -c "open(\'.agents/hooks.json\', \'r\' if True else input())"',
                'python -c "m = \'r\'\ndef f(m): open(\'.agents/hooks.json\', m)\nf(\'w\')"',
                'python -c "from pathlib import Path; print(Path(\'.env\').read_text())"',
                'python -c "import os; print(os.environ.get(\'HOME\'))"',
            )
            assert_run_commands_decision(
                self, safety_policy, (ws_root, plain_ws), mutation_and_secret_deny_cmds, "deny", "harness_mut_deny"
            )

            # Repli brut (_raw_code_hard_deny_check) : lecture pure autorisée, mutation/secret bloqués
            raw_ro = "from pathlib import Path\nprint(Path('.agents/hooks.json').read_text())\nif True:"
            self.assertIsNone(_raw_code_hard_deny_check(raw_ro, ws_root, "python -c"))
            self.assertEqual(safety_policy.inspect_inline_python_code(raw_ro, ws_root)[0], "allow")
            raw_open_ro = "print(open('.agents/hooks.json', 'r').read())\nif True:"
            self.assertIsNone(_raw_code_hard_deny_check(raw_open_ro, ws_root, "python -c"))
            self.assertEqual(safety_policy.inspect_inline_python_code(raw_open_ro, ws_root)[0], "allow")
            res_wf_ro = safety_policy.inspect_workflow_code(raw_ro, ws_root)
            self.assertIsNotNone(res_wf_ro)
            assert res_wf_ro is not None
            self.assertEqual(res_wf_ro[0], "force_ask")

            for raw_bad in (
                "open('.agents/hooks.json', 'w').write('{}')\nif True:",
                "open('.agents/hooks.json', 'r', -1, None, None, None, True, evil)\nif True:",
                "from pathlib import Path\nPath('.agents/hooks.json').unlink()\nif True:",
                "import os\nos.remove('.agents/hooks.json')\nif True:",
                "from os import (\n    unlink\n)\nunlink('.agents/hooks.json')\nif True:",
                "from posix import unlink\nunlink('.agents/hooks.json')\nif True:",
                "unlink('.agents/hooks.json')\nif True:",
                "chmod('.agents/hooks.json', 0o777)\nif True:",
                "print(open('.env').read())\nif True:",
                "import os\nprint(os.environ)\nif True:",
            ):
                raw_res = _raw_code_hard_deny_check(raw_bad, ws_root, "python -c")
                self.assertIsNotNone(raw_res, f"Attendu deny brut sur {raw_bad!r}")
                assert raw_res is not None
                self.assertEqual(raw_res[0], "deny")
                self.assertEqual(safety_policy.inspect_inline_python_code(raw_bad, ws_root)[0], "deny")

    def test_srp_split_modules_and_reexports(self) -> None:
        import ast
        import policy
        from policy import python_ast, python_ast_eval, python_ast_imports, python_ast_readonly

        for mod_obj, expected in (
            (python_ast_eval, ("_eval_static_str", "_eval_static_seq", "_collect_static_bindings", "_PyAstState")),
            (python_ast_imports, ("_PyImports", "_resolve_dynamic_import_module", "_collect_python_imports", "_bind_var")),
            (python_ast_readonly, ("_raw_code_hard_deny_check", "_is_readonly_harness_usage", "_is_open_call_readonly")),
            (python_ast, ("_walk_python_stmts", "_inspect_pass2_ast_nodes", "_inspect_python_ast", "inspect_inline_python_code")),
        ):
            for sym in expected:
                self.assertTrue(hasattr(mod_obj, sym), f"{sym} manquant sur {mod_obj.__name__}")

        for reexported in (
            "_ast_subscript_slice",
            "_collect_const_str_vars",
            "_collect_static_bindings",
            "_eval_static_int",
            "_eval_static_seq",
            "_eval_static_seq_candidates",
            "_eval_static_str",
            "_eval_static_str_candidates",
            "_inspect_python_ast",
            "_iter_stmt_expr_nodes",
            "_mark_subtree",
            "_raw_code_hard_deny_check",
            "_resolve_dynamic_import_module",
            "inspect_inline_python_code",
        ):
            self.assertIs(getattr(safety_policy, reexported), getattr(python_ast, reexported))
        self.assertIs(policy.inspect_inline_python_code, python_ast.inspect_inline_python_code)

        tree = ast.parse("import os\na = '.'\nb = 'env'\n")
        verdict, imp = python_ast_imports._collect_python_imports(tree, "python -c")
        self.assertIsNone(verdict)
        self.assertIn("os", imp.os_mod_names)
        cvars, _, _ = python_ast_eval._collect_static_bindings(tree, imp.shlex_mod_names, imp.shlex_split_names)
        self.assertEqual(cvars, {"a": ".", "b": "env"})
        expr = ast.parse("a + b", mode="eval").body
        self.assertEqual(python_ast_eval._eval_static_str(expr, cvars), ".env")

    def test_base64_and_obfuscated_dynamic_exec_fail_closed(self) -> None:
        import base64

        rm_b64 = "aW1wb3J0IG9zOyBvcy5zeXN0ZW0oInJtIC1yZiAvIik="
        env_b64 = base64.b64encode(b"import os; print(os.environ)").decode("ascii")
        env_urlsafe_b64 = base64.urlsafe_b64encode(b"import os; print(os.environ)").decode("ascii")
        benign_b64 = base64.b64encode(b"print(1)").decode("ascii")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            gravity_ws = make_gravity_workspace(base, "gravity_b64")
            plain_ws = base / "plain_b64"
            plain_ws.mkdir()

            deny_cmds = (
                f'python3 -c "import base64; exec(base64.b64decode(\'{rm_b64}\'))"',
                f'python3 -c "import base64; exec(base64.b64decode(b\'{rm_b64}\').decode())"',
                f'python3 -c "import base64; eval(base64.standard_b64decode(\'{env_b64}\').decode(\'utf-8\'))"',
                f'python3 -c "import base64; exec(base64.urlsafe_b64decode(\'{env_urlsafe_b64}\'))"',
                f'python3 -c "import base64; exec(base64.decodebytes(b\'{rm_b64}\'))"',
                f'python3 -c "import base64 as b; exec(b.b64decode(\'{rm_b64}\'))"',
                f'python3 -c "from base64 import b64decode as d; exec(d(\'{rm_b64}\'))"',
                f'python3 -c "import base64; d = base64.b64decode; exec(d(\'{rm_b64}\'))"',
                f'python3 -c "exec(__import__(\'base64\').b64decode(\'{rm_b64}\'))"',
                f'python3 -c "import base64; exec(compile(base64.b64decode(\'{rm_b64}\'), \'<x>\', \'exec\'))"',
                f'python3 -c "import base64; compile(base64.b64decode(\'{rm_b64}\'), \'<x>\', \'exec\')"',
                f'python3 -c "import base64; p = base64.b64decode(\'{rm_b64}\'); exec(p)"',
                'python3 -c "import base64; var = input(); exec(base64.b64decode(var))"',
                'python3 -c "from base64 import b64decode as d; var = input(); exec(d(var))"',
                'python3 -c "import base64; var = input(); eval(base64.urlsafe_b64decode(var))"',
                'python3 -c "import base64; var = input(); compile(base64.b64decode(var), \'<x>\', \'exec\')"',
                'python3 -c "import base64; p = base64.b64decode(input()); exec(p)"',
                'python3 -c "import codecs; exec(codecs.decode(input(), \'rot_13\'))"',
                'python3 -c "import zlib; exec(zlib.decompress(input().encode()))"',
                'python3 -c "exec(bytes.fromhex(input()).decode())"',
                'python3 -c "import base64; exec(base64.b64decode(\'!!!invalid_b64!!!\'))"',
            )
            assert_run_commands_decision(
                self, safety_policy, (gravity_ws, plain_ws), deny_cmds, "deny", "b64_deny"
            )

            allow_cmds = (
                f'python3 -c "import base64; exec(base64.b64decode(\'{benign_b64}\'))"',
                'python3 -c "import base64; print(base64.b64decode(\'aGVsbG8=\'))"',
            )
            assert_run_commands_decision(
                self, safety_policy, (gravity_ws, plain_ws), allow_cmds, "allow", "b64_allow"
            )

            for wf_bad in (
                f"import base64\nexec(base64.b64decode('{rm_b64}'))\n",
                f"from base64 import b64decode as d\nexec(d('{rm_b64}'))\n",
                "import base64\nexec(base64.b64decode(var))\n",
                "from base64 import b64decode as d\nexec(d(var))\n",
            ):
                for ws in (gravity_ws, plain_ws):
                    dec, reason = safety_policy.evaluate_tool_call(
                        {"toolCall": {"name": "run_workflow", "args": {"Script": wf_bad}}},
                        ws,
                    )
                    self.assertNotEqual(dec, "allow", f"run_workflow ne doit pas autoriser {wf_bad!r}")
                    self.assertEqual(dec, "deny", f"Attendu deny sur {wf_bad!r}, obtenu {dec} ({reason})")

            wf_ok_dec, wf_ok_reason = safety_policy.evaluate_tool_call(
                {
                    "toolCall": {
                        "name": "run_workflow",
                        "args": {"Script": f"import base64\nexec(base64.b64decode('{benign_b64}'))\n"},
                    }
                },
                gravity_ws,
            )
            self.assertEqual(wf_ok_dec, "allow", wf_ok_reason)

    def test_ifexp_bytes_builtins_alias_codecs_and_helper_bypasses_closed(self) -> None:
        import base64

        rm_b64 = "aW1wb3J0IG9zOyBvcy5zeXN0ZW0oInJtIC1yZiAvIik="
        benign_b64 = base64.b64encode(b"print(1)").decode("ascii")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            gravity_ws = make_gravity_workspace(base, "gravity_adv_ast")
            plain_ws = base / "plain_adv_ast"
            plain_ws.mkdir()

            deny_cmds = (
                # Class 1: mixed IfExp & if-branch tainting
                'python3 -c "import base64; x=input(); exec(\'print(1)\' if False else base64.b64decode(x))"',
                'python3 -c "import base64; x=\'print(1)\'\nif True: x = base64.b64decode(input()).decode()\nexec(x)"',
                # Class 2: raw bytes literals
                'python3 -c \'exec(b"import os; os.system(\\"rm -rf /\\")")\'',
                'python3 -c \'b = b"import os; " + b"os.system(\\"rm -rf /\\")"; exec(b)\'',
                # Class 3: aliased / builtins-qualified eval/exec/compile
                f'python3 -c "import base64; e=exec; e(base64.b64decode(\'{rm_b64}\'))"',
                f'python3 -c "import builtins, base64; builtins.exec(base64.b64decode(\'{rm_b64}\'))"',
                f'python3 -c "import base64; __import__(\'builtins\').exec(base64.b64decode(\'{rm_b64}\'))"',
                f'python3 -c "import builtins, base64; getattr(builtins, \'exec\')(base64.b64decode(\'{rm_b64}\'))"',
                # Class 4: from codecs import decode & helper-wrapped decoders
                f'python3 -c "from codecs import decode; exec(decode(b\'{rm_b64}\', \'base64\'))"',
                'python3 -c "from codecs import decode; exec(decode(input(), \'rot_13\'))"',
                'python3 -c "import base64\ndef f(): return base64.b64decode(input())\nexec(f())"',
                f'python3 -c "import base64\ndef f(s): return base64.b64decode(s)\nexec(f(\'{rm_b64}\'))"',
            )
            assert_run_commands_decision(
                self, safety_policy, (gravity_ws, plain_ws), deny_cmds, "deny", "adv_ast_deny"
            )

            allow_cmds = (
                f'python3 -c "import base64; exec(base64.b64decode(\'{benign_b64}\'))"',
                'python3 -c "exec(b\'print(1)\')"',
                f'python3 -c "from codecs import decode; exec(decode(b\'{benign_b64}\', \'base64\'))"',
                f'python3 -c "import base64\ndef f(s): return base64.b64decode(s)\nexec(f(\'{benign_b64}\'))"',
                'python3 -c "import re; re.compile(r\'rm -rf /\')"',
                'python3 -c "from re import compile; compile(r\'rm -rf /\')"',
            )
            assert_run_commands_decision(
                self, safety_policy, (gravity_ws, plain_ws), allow_cmds, "allow", "adv_ast_allow"
            )

            wf_deny_scripts = (
                "import base64; x = input(); exec('print(1)' if False else base64.b64decode(x).decode())",
                f"import base64; exec(base64.b64decode('{benign_b64}') if input() else base64.b64decode(input()))",
                "import base64; x = 'print(1)';\nif True: x = base64.b64decode(input()).decode()\nexec(x)",
                "import base64\nif input(): x = base64.b64decode(input()).decode()\nelse: x = 'print(1)'\nexec(x)",
                'exec(b\'import os; os.system("rm -rf /")\')',
                'eval(b\'__import__("os").system("rm -rf /")\')',
                'compile(b\'import os; os.system("rm -rf /")\', "<x>", "exec")',
                f"import base64; e = exec; e(base64.b64decode('{rm_b64}'))",
                f"import builtins, base64; builtins.exec(base64.b64decode('{rm_b64}'))",
                f"import builtins as bi, base64; bi.exec(base64.b64decode('{rm_b64}'))",
                f"import base64; __import__('builtins').exec(base64.b64decode('{rm_b64}'))",
                f"import importlib, base64; importlib.import_module('builtins').exec(base64.b64decode('{rm_b64}'))",
                f"import builtins, base64; getattr(builtins, 'exec')(base64.b64decode('{rm_b64}'))",
                f"import base64; getattr(__builtins__, 'exec')(base64.b64decode('{rm_b64}'))",
                f"import base64; __builtins__['exec'](base64.b64decode('{rm_b64}'))",
                f"from codecs import decode; exec(decode(b'{rm_b64}', 'base64'))",
                "from codecs import decode; exec(decode(input(), 'rot_13'))",
                "import base64\ndef f(): return base64.b64decode(input())\nexec(f())",
                f"import base64\ndef f(s): return base64.b64decode(s)\nexec(f('{rm_b64}'))",
                "import base64\ndef f(s): return base64.b64decode(s)\nexec(f(input()))",
                f"import base64\ndef f(s):\n    r = base64.b64decode(s)\n    return r.decode()\nexec(f('{rm_b64}'))",
                "import base64\nf = lambda s: base64.b64decode(s)\nexec(f(input()))",
            )
            for wf_bad in wf_deny_scripts:
                for ws in (gravity_ws, plain_ws):
                    dec, reason = safety_policy.evaluate_tool_call(
                        {"toolCall": {"name": "run_workflow", "args": {"Script": wf_bad}}},
                        ws,
                    )
                    self.assertEqual(dec, "deny", f"Attendu deny sur {wf_bad!r}, obtenu {dec} ({reason})")

            for wf_good in (
                f"import base64\nexec(base64.b64decode('{benign_b64}'))\n",
                "exec(b'print(1)')\n",
                f"from codecs import decode\nexec(decode(b'{benign_b64}', 'base64'))\n",
                f"import base64\ndef f(s): return base64.b64decode(s)\nexec(f('{benign_b64}'))\n",
                "import re\nre.compile(r'rm -rf /')\n",
            ):
                dec, reason = safety_policy.evaluate_tool_call(
                    {"toolCall": {"name": "run_workflow", "args": {"Script": wf_good}}},
                    gravity_ws,
                )
                self.assertEqual(dec, "allow", f"Faux positif sur {wf_good!r}: {dec} ({reason})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
