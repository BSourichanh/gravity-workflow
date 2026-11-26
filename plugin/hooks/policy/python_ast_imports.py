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
    _BASE64_DECODE_FUNCS,
    _OBFUSCATED_DECODE_ATTRS,
    _OBFUSCATED_DECODE_MODS,
    _PyAstState,
    _PyImports,
    _ScopeState,
    _decode_base64_payload,
    _eval_static_bytes,
    _eval_static_seq,
    _eval_static_str,
    _match_base64_decode_call,
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
                elif alias.name == "base64":
                    imp.base64_mod_names.add(bound)
                    imp.shadowed_names.add(bound)
                elif alias.name == "codecs":
                    imp.codecs_mod_names.add(bound)
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
                elif node.module == "base64" and alias.name in _BASE64_DECODE_FUNCS:
                    imp.base64_decode_fns[bound] = alias.name
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


def _eval_static_str_candidates(
    node: ast.AST,
    cur_str: Dict[str, str],
    cur_seq: Dict[str, List[Optional[str]]],
    alt_str: Dict[str, List[str]],
    func_returns: Dict[str, List[ast.AST]],
    _depth: int = 0,
    base64_mod_names: Optional[Set[str]] = None,
    base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
) -> List[str]:
    """Retourne les chaînes statiques candidates d'une expression AST (y compris `IfExp`, branches, helpers, `base64`)."""
    if _depth > 6:
        return []
    out: List[str] = []

    def _add(val: Optional[str]) -> None:
        if val is not None and val not in out and len(out) < 8:
            out.append(val)

    def _rec(n: ast.AST) -> List[str]:
        return _eval_static_str_candidates(
            n, cur_str, cur_seq, alt_str, func_returns, _depth + 1, base64_mod_names, base64_decode_fns, cur_bytes
        )

    if isinstance(node, ast.IfExp):
        for branch in (node.body, node.orelse):
            for c in _rec(branch):
                _add(c)
        return out
    if isinstance(node, ast.NamedExpr):
        return _rec(node.value)
    if isinstance(node, ast.Name):
        if node.id in cur_str:
            _add(cur_str[node.id])
        for alt in alt_str.get(node.id, ()):
            _add(alt)
        if cur_bytes and node.id in cur_bytes:
            try:
                _add(cur_bytes[node.id].decode("utf-8"))
            except UnicodeDecodeError:
                pass
        return out
    if isinstance(node, ast.Call):
        b64_match = _match_base64_decode_call(node, base64_mod_names, base64_decode_fns)
        if b64_match is not None:
            fn_canon, arg0 = b64_match
            for cand in _rec(arg0):
                dec_b = _decode_base64_payload(cand, fn_canon)
                if dec_b is not None:
                    try:
                        _add(dec_b.decode("utf-8"))
                    except UnicodeDecodeError:
                        pass
            if out:
                return out
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "decode" and len(node.args) <= 1 and not node.keywords:
            if isinstance(fn.value, ast.Call) and _match_base64_decode_call(fn.value, base64_mod_names, base64_decode_fns):
                for cand in _rec(fn.value):
                    _add(cand)
                if out:
                    return out
        if isinstance(fn, ast.Name) and fn.id in func_returns and not node.args and not node.keywords:
            for ret_expr in func_returns[fn.id]:
                for c in _rec(ret_expr):
                    _add(c)
            if out:
                return out
    _add(_eval_static_str(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes))
    return out


def _eval_scope_str_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> List[str]:
    """Wrapper pratique de `_eval_static_str_candidates` utilisant `state` et `scope` courants."""
    return _eval_static_str_candidates(
        node,
        scope.cur_str,
        scope.cur_seq,
        state.alt_str,
        state.imp.func_returns,
        0,
        scope.base64_mods,
        scope.base64_fns,
        scope.cur_bytes,
    )


def _eval_static_seq_candidates(
    node: ast.AST,
    cur_str: Dict[str, str],
    cur_seq: Dict[str, List[Optional[str]]],
    alt_seq: Dict[str, List[List[Optional[str]]]],
    shlex_mod_names: Set[str],
    shlex_split_names: Set[str],
    func_returns: Dict[str, List[ast.AST]],
    _depth: int = 0,
) -> List[List[Optional[str]]]:
    """Retourne les séquences statiques candidates d'une expression AST (y compris `IfExp`, branches, helpers)."""
    if _depth > 6:
        return []
    out: List[List[Optional[str]]] = []

    def _add(val: Optional[List[Optional[str]]]) -> None:
        if val is not None and val not in out and len(out) < 8:
            out.append(list(val))

    if isinstance(node, ast.IfExp):
        for branch in (node.body, node.orelse):
            for c in _eval_static_seq_candidates(
                branch, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1
            ):
                _add(c)
        return out
    if isinstance(node, ast.NamedExpr):
        return _eval_static_seq_candidates(
            node.value, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1
        )
    if isinstance(node, ast.Name):
        if node.id in cur_seq:
            _add(cur_seq[node.id])
        for alt in alt_seq.get(node.id, ()):
            _add(alt)
        return out
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in func_returns
        and not node.args
        and not node.keywords
    ):
        for ret_expr in func_returns[node.func.id]:
            for c in _eval_static_seq_candidates(
                ret_expr, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1
            ):
                _add(c)
        if out:
            return out
    _add(_eval_static_seq(node, cur_str, cur_seq, 0, shlex_mod_names, shlex_split_names, func_returns))
    return out


def _mark_subtree(node: ast.AST, target_set: Set[ast.AST]) -> None:
    """Marque tous les descendants AST de `node` dans `target_set`."""
    for child in ast.walk(node):
        target_set.add(child)


def _iter_stmt_expr_nodes(stmt: ast.AST) -> List[ast.AST]:
    """Retourne les nœuds d'expressions appartenant directement à `stmt` (sans descendre dans les blocs imbriqués)."""
    out: List[ast.AST] = []
    skip_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def _visit(node: ast.AST) -> None:
        out.append(node)
        for field_name, field_val in ast.iter_fields(node):
            if node is stmt and field_name in ("body", "orelse", "handlers", "finalbody"):
                continue
            if isinstance(field_val, ast.AST) and not isinstance(field_val, skip_types):
                _visit(field_val)
            elif isinstance(field_val, list):
                for item in field_val:
                    if isinstance(item, ast.AST) and not isinstance(item, skip_types):
                        _visit(item)

    _visit(stmt)
    return out


def _mark_benign_cmd_node(state: _PyAstState, arg_node: ast.AST) -> None:
    """Marque un nœud d'argument déjà validé afin d'éviter un faux positif en passe 2."""
    _mark_subtree(arg_node, state.checked_cmd_nodes)
    if isinstance(arg_node, ast.Name):
        state.benign_cmd_name_nodes.add(arg_node)
        if arg_node.id in state.single_use_defs:
            _mark_subtree(state.single_use_defs[arg_node.id], state.checked_cmd_nodes)


def _expr_has_obfuscated_decode(
    node: ast.AST, state: _PyAstState, scope: _ScopeState, _depth: int = 0, _seen: Optional[Set[str]] = None
) -> Optional[str]:
    """Détecte un appel de décodage/décompression/désérialisation obfusqué dans `node` (y compris alias/variables)."""
    if _depth > 4:
        return None
    seen = _seen if _seen is not None else set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if _match_base64_decode_call(sub, scope.base64_mods, scope.base64_fns):
                return "b64decode"
            fn = sub.func
            if isinstance(fn, ast.Name) and (fn.id in scope.base64_fns or fn.id in state.imp.base64_decode_fns):
                return fn.id
            if isinstance(fn, ast.Attribute):
                if fn.attr in _OBFUSCATED_DECODE_ATTRS:
                    return fn.attr
                if fn.attr == "decode" and isinstance(fn.value, ast.Name) and fn.value.id in state.imp.codecs_mod_names:
                    return "codecs.decode"
                if fn.attr == "loads" and isinstance(fn.value, ast.Name) and fn.value.id in ("marshal", "pickle"):
                    return f"{fn.value.id}.loads"
            if isinstance(fn, ast.Name) and fn.id == "getattr" and len(sub.args) >= 2:
                for attr_s in _eval_scope_str_cands(sub.args[1], state, scope):
                    if attr_s in _OBFUSCATED_DECODE_ATTRS or attr_s in ("decode", "loads"):
                        return attr_s
                if isinstance(sub.args[0], ast.Name) and sub.args[0].id in _OBFUSCATED_DECODE_MODS:
                    return "getattr"
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id not in seen:
            seen.add(sub.id)
            for contrib in state.var_contributors.get(sub.id, ()):
                hit = _expr_has_obfuscated_decode(contrib, state, scope, _depth + 1, seen)
                if hit is not None:
                    return hit
    return None


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
    elif dyn_mod == "base64":
        scope.base64_mods.add(target_name)
    elif isinstance(rhs_node, ast.Attribute):
        if rhs_node.attr == "rmtree":
            scope.rmtree_fns.add(target_name)
        elif rhs_node.attr in OS_SHELL_FUNCS:
            scope.os_shell_fns.add(target_name)
        elif rhs_node.attr in _BASE64_DECODE_FUNCS and (
            (isinstance(rhs_node.value, ast.Name) and rhs_node.value.id in scope.base64_mods)
            or _resolve_dynamic_import_module(
                rhs_node.value, scope.cur_str, scope.cur_seq, state.imp.importlib_mod_names, state.imp.import_module_fns
            )
            == "base64"
        ):
            scope.base64_fns[target_name] = rhs_node.attr
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
        for g_attr in _eval_scope_str_cands(rhs_node.args[1], state, scope):
            if g_attr == "rmtree":
                scope.rmtree_fns.add(target_name)
            elif g_attr in OS_SHELL_FUNCS:
                scope.os_shell_fns.add(target_name)
            elif g_attr in SUBPROCESS_CALL_FUNCS:
                scope.subproc_fns.add(target_name)
            elif g_attr in _BASE64_DECODE_FUNCS:
                scope.base64_fns[target_name] = g_attr

    str_cands = _eval_scope_str_cands(rhs_node, state, scope)
    if str_cands:
        scope.cur_str[target_name] = str_cands[0]
        scope.cur_seq.pop(target_name, None)
        scope.cur_bytes.pop(target_name, None)
        for c in str_cands:
            state.record_alt_str(target_name, c)
        return
    bytes_val = _eval_static_bytes(
        rhs_node,
        scope.cur_str,
        scope.cur_seq,
        0,
        state.imp.func_returns,
        scope.base64_mods,
        scope.base64_fns,
        scope.cur_bytes,
    )
    if bytes_val is not None:
        scope.cur_bytes[target_name] = bytes_val
        scope.cur_str.pop(target_name, None)
        scope.cur_seq.pop(target_name, None)
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
        scope.cur_bytes.pop(target_name, None)
        for sc in seq_cands:
            state.record_alt_seq(target_name, sc)
        return
    scope.cur_str.pop(target_name, None)
    scope.cur_seq.pop(target_name, None)
    scope.cur_bytes.pop(target_name, None)
    state.alt_str.pop(target_name, None)
    state.alt_seq.pop(target_name, None)
    state.dynamic_tainted_vars.add(target_name)
