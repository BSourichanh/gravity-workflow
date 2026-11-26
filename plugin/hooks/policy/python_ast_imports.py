#!/usr/bin/env python3
"""Collecte des imports, détection du shadowing, résolution d'imports dynamiques et binding de variables."""

import ast
from typing import Dict, List, Optional, Set, Tuple

from .constants import (
    OS_EXEC_LIST_FUNCS,
    OS_EXEC_VAR_FUNCS,
    OS_SHELL_FUNCS,
    OS_SPAWN_LIST_FUNCS,
    OS_SPAWN_VAR_FUNCS,
    RE_PATTERN_FUNCS,
    SUBPROCESS_CALL_FUNCS,
)
from .python_ast_eval import (
    _PyAstState,
    _PyImports,
    _ScopeState,
    _eval_static_seq_candidates,
    _eval_static_str,
    _eval_static_str_candidates,
)

_OS_PATH_TERMINAL_FUNCS = frozenset(
    {
        "exists", "lexists", "isfile", "isdir", "islink", "ismount",
        "getsize", "getmtime", "getctime", "getatime", "samefile", "sameopenfile",
    }
)
_OS_PATH_PROPAGATING_FUNCS = frozenset(
    {
        "join", "abspath", "realpath", "normpath", "normcase", "dirname",
        "basename", "split", "splitdrive", "splitext", "expanduser",
        "expandvars", "relpath", "commonpath", "commonprefix",
    }
)


def _resolve_dynamic_import_module(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Dict[str, List[Optional[str]]],
    importlib_mod_names: Set[str],
    import_module_fns: Set[str],
) -> Optional[str]:
    """Retourne le nom de module statique d'un appel `__import__('...')` ou `importlib.import_module('...')`."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    fn = node.func
    is_dyn_imp = (
        (isinstance(fn, ast.Name) and (fn.id == "__import__" or fn.id in import_module_fns))
        or (
            isinstance(fn, ast.Attribute)
            and fn.attr == "import_module"
            and isinstance(fn.value, ast.Name)
            and fn.value.id in importlib_mod_names
        )
    )
    if not is_dyn_imp:
        return None
    return _eval_static_str(node.args[0], const_vars, const_seq_vars)


def _collect_python_imports(
    tree: ast.Module, context_label: str
) -> Tuple[Optional[Tuple[str, str]], _PyImports]:
    """Collecte les alias importés (`os`, `subprocess`, `shutil`, `re`, `shlex`, `importlib`) et noms shadowés."""
    imp = _PyImports()
    symbol_sources: Dict[str, str] = {
        "os": "mod:os",
        "posix": "mod:posix",
        "nt": "mod:nt",
        "re": "mod:re",
        "pathlib": "mod:pathlib",
        "json": "mod:json",
        "posixpath": "mod:os.path",
        "ntpath": "mod:os.path",
        "Path": "pathlib:Path",
        "PurePath": "pathlib:PurePath",
        "PosixPath": "pathlib:PosixPath",
        "WindowsPath": "pathlib:WindowsPath",
    }
    reserved_builtins = frozenset(
        {"open", "print", "len", "compile", "str", "repr", "sorted", "list", "tuple", "set", "reversed", "enumerate", "zip"}
    )

    def _record_import_source(bound: str, source_tag: str) -> None:
        if bound in reserved_builtins:
            imp.shadowed_names.add(bound)
            return
        prev = symbol_sources.get(bound)
        if prev is not None and prev != source_tag:
            imp.shadowed_names.add(bound)
        else:
            symbol_sources[bound] = source_tag

    def _mark_target_shadowed(target: ast.AST) -> None:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                imp.shadowed_names.add(sub.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if alias.name in ("os", "posix", "nt"):
                    imp.os_mod_names.add(bound)
                    _record_import_source(bound, f"mod:{alias.name}")
                elif alias.name == "subprocess":
                    imp.subprocess_mod_names.add(bound)
                    imp.shadowed_names.add(bound)
                elif alias.name == "re":
                    imp.re_mod_names.add(bound)
                    _record_import_source(bound, "mod:re")
                elif alias.name == "shlex":
                    imp.shlex_mod_names.add(bound)
                    imp.shadowed_names.add(bound)
                elif alias.name == "shutil":
                    imp.shutil_mod_names.add(bound)
                    imp.shadowed_names.add(bound)
                elif alias.name == "importlib":
                    imp.importlib_mod_names.add(bound)
                    imp.shadowed_names.add(bound)
                elif alias.name == "pathlib":
                    imp.pathlib_mod_names.add(bound)
                    _record_import_source(bound, "mod:pathlib")
                elif alias.name == "json":
                    imp.json_mod_names.add(bound)
                    _record_import_source(bound, "mod:json")
                elif alias.name in ("posixpath", "ntpath", "os.path"):
                    mod_bound = alias.asname or alias.name
                    imp.os_path_mod_names.add(mod_bound)
                    _record_import_source(mod_bound, "mod:os.path")
                else:
                    imp.shadowed_names.add(bound)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    imp.has_wildcard_import = True
                    continue
                bound = alias.asname or alias.name
                if node.module in ("os", "posix", "nt"):
                    if alias.name in ("environ", "environb"):
                        return ("deny", f"Import de `os.environ` dans `{context_label}` bloqué."), imp
                    if alias.name in OS_SHELL_FUNCS:
                        imp.os_shell_func_names.add(bound)
                        imp.shadowed_names.add(bound)
                    elif alias.name in OS_EXEC_LIST_FUNCS:
                        imp.os_exec_list_names.add(bound)
                        imp.shadowed_names.add(bound)
                    elif alias.name in OS_EXEC_VAR_FUNCS:
                        imp.os_exec_var_names.add(bound)
                        imp.shadowed_names.add(bound)
                    elif alias.name in OS_SPAWN_LIST_FUNCS:
                        imp.os_spawn_list_names.add(bound)
                        imp.shadowed_names.add(bound)
                    elif alias.name in OS_SPAWN_VAR_FUNCS:
                        imp.os_spawn_var_names.add(bound)
                        imp.shadowed_names.add(bound)
                    elif alias.name == "path":
                        imp.os_path_mod_names.add(bound)
                        _record_import_source(bound, "mod:os.path")
                    elif alias.name in ("stat", "lstat", "access", "readlink"):
                        imp.os_readonly_term_fns.add(bound)
                        _record_import_source(bound, f"os:{alias.name}")
                    elif alias.name in ("fspath", "listdir", "scandir", "walk"):
                        imp.os_readonly_prop_fns.add(bound)
                        _record_import_source(bound, f"os:{alias.name}")
                    else:
                        imp.shadowed_names.add(bound)
                elif node.module in ("os.path", "posixpath", "ntpath"):
                    if alias.name in _OS_PATH_TERMINAL_FUNCS:
                        imp.os_readonly_term_fns.add(bound)
                        _record_import_source(bound, f"os.path:{alias.name}")
                    elif alias.name in _OS_PATH_PROPAGATING_FUNCS:
                        imp.os_readonly_prop_fns.add(bound)
                        _record_import_source(bound, f"os.path:{alias.name}")
                    else:
                        imp.shadowed_names.add(bound)
                elif node.module == "pathlib" and alias.name in ("Path", "PurePath", "PosixPath", "WindowsPath"):
                    imp.path_ctor_names.add(bound)
                    _record_import_source(bound, f"pathlib:{alias.name}")
                elif node.module == "json" and alias.name in ("load", "loads"):
                    imp.json_load_fns.add(bound)
                    _record_import_source(bound, f"json:{alias.name}")
                elif node.module == "shutil" and alias.name == "rmtree":
                    imp.rmtree_func_names.add(bound)
                    imp.shadowed_names.add(bound)
                elif node.module == "subprocess" and alias.name in SUBPROCESS_CALL_FUNCS:
                    imp.subprocess_func_names.add(bound)
                    imp.shadowed_names.add(bound)
                elif node.module == "shlex" and alias.name == "split":
                    imp.shlex_split_names.add(bound)
                    imp.shadowed_names.add(bound)
                elif node.module == "importlib" and alias.name == "import_module":
                    imp.import_module_fns.add(bound)
                    imp.shadowed_names.add(bound)
                elif node.module == "re" and alias.name in RE_PATTERN_FUNCS:
                    imp.re_func_names.add(bound)
                    _record_import_source(bound, f"re:{alias.name}")
                else:
                    imp.shadowed_names.add(bound)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                _mark_target_shadowed(tgt)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _mark_target_shadowed(node.target)
        elif isinstance(node, ast.NamedExpr):
            imp.shadowed_names.add(node.target.id)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            _mark_target_shadowed(node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            _mark_target_shadowed(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            imp.shadowed_names.add(node.name)
        elif isinstance(node, ast.arg):
            imp.shadowed_names.add(node.arg)
        elif isinstance(node, ast.ClassDef):
            imp.shadowed_names.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            imp.shadowed_names.add(node.name)
            if not node.args.args and not node.args.posonlyargs and not node.args.kwonlyargs:
                rets = [
                    sub.value
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Return) and sub.value is not None
                ]
                if rets:
                    imp.func_returns.setdefault(node.name, []).extend(rets[:4])
    return None, imp


def _bind_var(state: _PyAstState, scope: _ScopeState, target_name: str, rhs_node: ast.AST) -> None:
    """Met à jour les liaisons dynamiques/statiques locales pour `target_name = rhs_node`."""
    state.var_contributors.setdefault(target_name, []).append(rhs_node)
    dyn_mod = _resolve_dynamic_import_module(
        rhs_node, scope.cur_str, scope.cur_seq, state.imp.importlib_mod_names, state.imp.import_module_fns
    )
    if dyn_mod == "subprocess":
        scope.subproc_mods.add(target_name)
    elif dyn_mod in ("os", "posix", "nt"):
        scope.os_mods.add(target_name)
    elif isinstance(rhs_node, ast.Attribute):
        if rhs_node.attr == "rmtree":
            scope.rmtree_fns.add(target_name)
        elif rhs_node.attr in OS_SHELL_FUNCS:
            scope.os_shell_fns.add(target_name)
        elif rhs_node.attr in SUBPROCESS_CALL_FUNCS and (
            (isinstance(rhs_node.value, ast.Name) and rhs_node.value.id in scope.subproc_mods)
            or _resolve_dynamic_import_module(
                rhs_node.value, scope.cur_str, scope.cur_seq, state.imp.importlib_mod_names, state.imp.import_module_fns
            )
            == "subprocess"
        ):
            scope.subproc_fns.add(target_name)
    elif (
        isinstance(rhs_node, ast.Call)
        and isinstance(rhs_node.func, ast.Name)
        and rhs_node.func.id == "getattr"
        and len(rhs_node.args) >= 2
    ):
        for g_attr in _eval_static_str_candidates(
            rhs_node.args[1], scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
        ):
            if g_attr == "rmtree":
                scope.rmtree_fns.add(target_name)
            elif g_attr in OS_SHELL_FUNCS:
                scope.os_shell_fns.add(target_name)
            elif g_attr in SUBPROCESS_CALL_FUNCS:
                scope.subproc_fns.add(target_name)

    str_cands = _eval_static_str_candidates(
        rhs_node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
    )
    if str_cands:
        scope.cur_str[target_name] = str_cands[0]
        scope.cur_seq.pop(target_name, None)
        for c in str_cands:
            state.record_alt_str(target_name, c)
        return
    seq_cands = _eval_static_seq_candidates(
        rhs_node,
        scope.cur_str,
        scope.cur_seq,
        state.alt_seq,
        state.imp.shlex_mod_names,
        state.imp.shlex_split_names,
        state.imp.func_returns,
    )
    if seq_cands:
        scope.cur_seq[target_name] = list(seq_cands[0])
        scope.cur_str.pop(target_name, None)
        for sc in seq_cands:
            state.record_alt_seq(target_name, sc)
        return
    scope.cur_str.pop(target_name, None)
    scope.cur_seq.pop(target_name, None)
    state.alt_str.pop(target_name, None)
    state.alt_seq.pop(target_name, None)
    state.dynamic_tainted_vars.add(target_name)
