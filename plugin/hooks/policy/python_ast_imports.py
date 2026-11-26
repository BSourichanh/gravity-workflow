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
    _OS_PATH_PROPAGATING_FUNCS,
    _OS_PATH_TERMINAL_FUNCS,
    _PyAstState,
    _PyImports,
    _ScopeState,
    _decode_base64_payload,
    _decode_bytes_text,
    _eval_static_bytes,
    _eval_static_str,
    _is_named_mod_expr,
    _match_base64_decode_call,
)
from .python_ast_readonly import (
    _decode_codecs_payload,
    _eval_static_seq_candidates,
    _has_tainted_name,
    _match_codecs_decode_call,
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
    """Collecte les alias importés (`os`, `subprocess`, `shutil`, `re`, `shlex`, `importlib`, `builtins`, `codecs`) et noms shadowés."""
    imp = _PyImports()
    symbol_sources: Dict[str, str] = {
        "os": "mod:os", "posix": "mod:posix", "nt": "mod:nt", "re": "mod:re",
        "pathlib": "mod:pathlib", "json": "mod:json", "posixpath": "mod:os.path", "ntpath": "mod:os.path",
        "Path": "pathlib:Path", "PurePath": "pathlib:PurePath", "PosixPath": "pathlib:PosixPath", "WindowsPath": "pathlib:WindowsPath",
    }
    reserved_builtins = frozenset(
        {"open", "print", "len", "compile", "str", "repr", "sorted", "list", "tuple", "set", "reversed", "enumerate", "zip"}
    )
    shadow_mod_sets = {
        "subprocess": imp.subprocess_mod_names, "shlex": imp.shlex_mod_names, "shutil": imp.shutil_mod_names,
        "importlib": imp.importlib_mod_names, "base64": imp.base64_mod_names,
        "codecs": imp.codecs_mod_names, "builtins": imp.builtins_mod_names,
    }
    os_func_sets = (
        (OS_SHELL_FUNCS, imp.os_shell_func_names),
        (OS_EXEC_LIST_FUNCS, imp.os_exec_list_names),
        (OS_EXEC_VAR_FUNCS, imp.os_exec_var_names),
        (OS_SPAWN_LIST_FUNCS, imp.os_spawn_list_names),
        (OS_SPAWN_VAR_FUNCS, imp.os_spawn_var_names),
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
                elif alias.name in shadow_mod_sets:
                    shadow_mod_sets[alias.name].add(bound)
                    imp.shadowed_names.add(bound)
                elif alias.name in ("re", "pathlib", "json"):
                    getattr(imp, f"{alias.name}_mod_names").add(bound)
                    _record_import_source(bound, f"mod:{alias.name}")
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
                    matched_os = False
                    for fn_set, target_set in os_func_sets:
                        if alias.name in fn_set:
                            target_set.add(bound)
                            imp.shadowed_names.add(bound)
                            matched_os = True
                            break
                    if matched_os:
                        pass
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
                elif node.module == "codecs" and alias.name == "decode":
                    imp.codecs_decode_fns.add(bound)
                    imp.shadowed_names.add(bound)
                elif node.module == "builtins" and alias.name in ("eval", "exec", "compile"):
                    imp.eval_exec_fns[bound] = alias.name
                    imp.shadowed_names.add(bound)
                elif node.module == "re" and alias.name in RE_PATTERN_FUNCS:
                    imp.re_func_names.add(bound)
                    if bound == "compile":
                        imp.eval_exec_fns.pop("compile", None)
                    _record_import_source(bound, f"re:{alias.name}")
                else:
                    imp.shadowed_names.add(bound)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                _mark_target_shadowed(tgt)
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Lambda):
                imp.func_defs.setdefault(node.targets[0].id, []).append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _mark_target_shadowed(node.target)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and isinstance(node.value, ast.Lambda):
                imp.func_defs.setdefault(node.target.id, []).append(node.value)
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
            imp.func_defs.setdefault(node.name, []).append(node)
            if not node.args.args and not node.args.posonlyargs and not node.args.kwonlyargs:
                rets = [s.value for s in ast.walk(node) if isinstance(s, ast.Return) and s.value is not None]
                if rets:
                    imp.func_returns.setdefault(node.name, []).extend(rets[:4])
    return None, imp


def _eval_func_call_code_cands(
    call: ast.Call, fn_node: ast.AST, state: _PyAstState, scope: _ScopeState,
    _depth: int, _seen_fns: Optional[Set[str]], include_bytes: bool,
) -> Tuple[List[str], bool]:
    """Évalue les retours d'un appel à une fonction/lambda locale avec liaison statique des paramètres."""
    seen = set(_seen_fns or ())
    fn_name = getattr(fn_node, "name", "<lambda>")
    if fn_name != "<lambda>":
        if fn_name in seen:
            return [], False
        seen.add(fn_name)
    args_spec: Optional[ast.arguments] = getattr(fn_node, "args", None)
    if (
        args_spec is None or args_spec.vararg or args_spec.kwarg
        or any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords)
    ):
        return [], False
    pos_params = [a.arg for a in args_spec.posonlyargs] + [a.arg for a in args_spec.args]
    kw_params = [a.arg for a in args_spec.kwonlyargs]
    all_params = set(pos_params) | set(kw_params)
    if len(call.args) > len(pos_params):
        return [], False
    bound_exprs: Dict[str, ast.AST] = {}
    for p, d in zip(pos_params[-len(args_spec.defaults) :], args_spec.defaults) if args_spec.defaults else ():
        bound_exprs[p] = d
    for p, d in zip(kw_params, args_spec.kw_defaults):
        if d is not None:
            bound_exprs[p] = d
    for p, a in zip(pos_params, call.args):
        bound_exprs[p] = a
    pos_only = {a.arg for a in args_spec.posonlyargs}
    for kw in call.keywords:
        if kw.arg is None or kw.arg in pos_only or kw.arg not in all_params:
            return [], False
        bound_exprs[kw.arg] = kw.value

    def _eval_s(expr: ast.AST, sc: _ScopeState, inc_b: bool = False) -> Tuple[List[str], bool]:
        return _eval_str_cands_impl(
            expr, sc.cur_str, sc.cur_seq, state.alt_str, state.imp.func_returns, _depth + 1,
            sc.base64_mods, sc.base64_fns, sc.cur_bytes, state=state, scope=sc, include_bytes=inc_b, _seen_fns=seen,
        )

    def _bind_local(dst: _ScopeState, target: str, expr: ast.AST, src: _ScopeState) -> None:
        bv = _eval_static_bytes(
            expr, src.cur_str, src.cur_seq, 0, state.imp.func_returns, src.base64_mods, src.base64_fns, src.cur_bytes
        )
        sc_list, sok = _eval_s(expr, src, False)
        if bv is not None and not _has_tainted_name(expr, state, src):
            dst.cur_bytes[target] = bv
            dst.cur_str.pop(target, None)
        elif sok and len(sc_list) == 1:
            dst.cur_str[target] = sc_list[0]
            dst.cur_bytes.pop(target, None)
        else:
            dst.cur_str.pop(target, None)
            dst.cur_bytes.pop(target, None)
            dst.incomplete_vars.add(target)

    fn_scope = scope.clone()
    fn_scope.shadowed_alts.update(all_params)
    for p in all_params:
        fn_scope.cur_str.pop(p, None)
        fn_scope.cur_bytes.pop(p, None)
        fn_scope.cur_seq.pop(p, None)
        fn_scope.incomplete_vars.discard(p)
        fn_scope.obf_tainted.discard(p)
    for p, expr in bound_exprs.items():
        _bind_local(fn_scope, p, expr, scope)

    if isinstance(fn_node, ast.Lambda):
        return _eval_s(fn_node.body, fn_scope, include_bytes)
    out: List[str] = []
    all_ok, has_ret = True, False
    for st in getattr(fn_node, "body", ()):
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            fn_scope.shadowed_alts.add(st.targets[0].id)
            _bind_local(fn_scope, st.targets[0].id, st.value, fn_scope)
            continue
        for sub in ast.walk(st):
            if isinstance(sub, ast.Return) and sub.value is not None:
                has_ret = True
                rc, rok = _eval_s(sub.value, fn_scope, include_bytes)
                for item in rc:
                    if item not in out and len(out) < 8:
                        out.append(item)
                all_ok = all_ok and rok and bool(rc)
    return out, bool(has_ret and out and all_ok)


def _eval_str_cands_impl(
    node: ast.AST, cur_str: Dict[str, str], cur_seq: Dict[str, List[Optional[str]]],
    alt_str: Dict[str, List[str]], func_returns: Dict[str, List[ast.AST]], _depth: int = 0,
    base64_mod_names: Optional[Set[str]] = None, base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None, *, state: Optional[_PyAstState] = None,
    scope: Optional[_ScopeState] = None, include_bytes: bool = False, _seen_fns: Optional[Set[str]] = None,
) -> Tuple[List[str], bool]:
    if _depth > 6:
        return [], False
    out: List[str] = []

    def _add(val: Optional[str]) -> None:
        if val is not None and val not in out and len(out) < 8:
            out.append(val)

    def _rec(n: ast.AST, inc_b: bool = include_bytes) -> Tuple[List[str], bool]:
        return _eval_str_cands_impl(
            n, cur_str, cur_seq, alt_str, func_returns, _depth + 1, base64_mod_names,
            base64_decode_fns, cur_bytes, state=state, scope=scope, include_bytes=inc_b, _seen_fns=_seen_fns,
        )

    if isinstance(node, ast.IfExp):
        b_c, b_ok = _rec(node.body)
        o_c, o_ok = _rec(node.orelse)
        for c in b_c + o_c:
            _add(c)
        return out, bool(b_ok and o_ok and b_c and o_c)
    if isinstance(node, ast.NamedExpr):
        return _rec(node.value)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return [node.value], True
        if include_bytes and isinstance(node.value, bytes):
            return [_decode_bytes_text(node.value)], True
    if isinstance(node, ast.Name):
        if node.id in cur_str:
            _add(cur_str[node.id])
        if cur_bytes and node.id in cur_bytes:
            _add(_decode_bytes_text(cur_bytes[node.id]))
        if not (scope and node.id in scope.shadowed_alts):
            for alt in alt_str.get(node.id, ()):
                _add(alt)
            if include_bytes and state:
                for alt_b in state.alt_bytes.get(node.id, ()):
                    _add(_decode_bytes_text(alt_b))
        tainted = (scope and (node.id in scope.incomplete_vars or node.id in scope.obf_tainted)) or (
            state and node.id in state.dynamic_tainted_vars and not (scope and node.id in scope.shadowed_alts)
        )
        return out, bool(out and not tainted)
    if isinstance(node, ast.Call):
        b64_match = _match_base64_decode_call(node, base64_mod_names, base64_decode_fns)
        if b64_match is not None:
            fn_canon, arg0 = b64_match
            arg_cands, arg_ok = _rec(arg0, True)
            all_ok = arg_ok and bool(arg_cands)
            for cand in arg_cands:
                dec_b = _decode_base64_payload(cand, fn_canon)
                if dec_b is None:
                    all_ok = False
                elif include_bytes:
                    _add(_decode_bytes_text(dec_b))
                else:
                    try:
                        _add(dec_b.decode("utf-8"))
                    except UnicodeDecodeError:
                        all_ok = False
            return out, bool(out and all_ok)
        c_mods = scope.codecs_mods if scope else None
        c_fns = scope.codecs_fns if scope else (state.imp.codecs_decode_fns if state else None)
        codecs_match = _match_codecs_decode_call(node, c_mods, c_fns)
        if codecs_match is not None:
            obj_arg, enc_arg = codecs_match
            raw_b = _eval_static_bytes(obj_arg, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes)
            if raw_b is not None and not _has_tainted_name(obj_arg, state, scope):
                raw_list: List[object] = [raw_b]
                raw_ok = True
            else:
                obj_cands, raw_ok = _rec(obj_arg, include_bytes)
                raw_list = list(obj_cands)
            enc_cands, enc_ok = ([None], True) if enc_arg is None else _rec(enc_arg, False)
            all_ok = raw_ok and enc_ok and bool(raw_list) and bool(enc_cands)
            for r_val in raw_list:
                for e_val in enc_cands:
                    dec_s = _decode_codecs_payload(r_val, e_val if isinstance(e_val, str) else None)
                    all_ok = all_ok and (dec_s is not None)
                    _add(dec_s)
            return out, bool(out and all_ok)
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "decode" and len(node.args) <= 1 and not node.keywords:
            if isinstance(fn.value, ast.Call) and (
                _match_base64_decode_call(fn.value, base64_mod_names, base64_decode_fns)
                or _match_codecs_decode_call(fn.value, c_mods, c_fns)
            ):
                return _rec(fn.value)
        if state is not None and scope is not None:
            if isinstance(fn, ast.Lambda):
                return _eval_func_call_code_cands(node, fn, state, scope, _depth + 1, _seen_fns, include_bytes)
            if isinstance(fn, ast.Name) and fn.id in state.imp.func_defs:
                all_ok = True
                for fn_def in state.imp.func_defs[fn.id]:
                    fc, fok = _eval_func_call_code_cands(node, fn_def, state, scope, _depth + 1, _seen_fns, include_bytes)
                    for item in fc:
                        _add(item)
                    all_ok = all_ok and fok and bool(fc)
                return out, bool(out and all_ok)
        elif isinstance(fn, ast.Name) and fn.id in func_returns and not node.args and not node.keywords:
            all_ok = True
            for ret_expr in func_returns[fn.id]:
                rc, rok = _rec(ret_expr)
                for item in rc:
                    _add(item)
                all_ok = all_ok and rok and bool(rc)
            if out:
                return out, all_ok
    val = _eval_static_str(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes)
    if val is None and include_bytes:
        b_val = _eval_static_bytes(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes)
        if b_val is not None:
            val = _decode_bytes_text(b_val)
    _add(val)
    return out, bool(out and not _has_tainted_name(node, state, scope))


def _eval_static_str_candidates(
    node: ast.AST, cur_str: Dict[str, str], cur_seq: Dict[str, List[Optional[str]]],
    alt_str: Dict[str, List[str]], func_returns: Dict[str, List[ast.AST]], _depth: int = 0,
    base64_mod_names: Optional[Set[str]] = None, base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
) -> List[str]:
    """Retourne les chaînes statiques candidates d'une expression AST (y compris `IfExp`, branches, helpers, `base64`)."""
    return _eval_str_cands_impl(
        node, cur_str, cur_seq, alt_str, func_returns, _depth, base64_mod_names, base64_decode_fns, cur_bytes
    )[0]


def _eval_scope_str_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> List[str]:
    """Wrapper pratique de `_eval_static_str_candidates` utilisant `state` et `scope` courants."""
    return _eval_str_cands_impl(
        node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns, 0,
        scope.base64_mods, scope.base64_fns, scope.cur_bytes, state=state, scope=scope, include_bytes=False,
    )[0]


def _eval_code_arg_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> Tuple[List[str], bool]:
    """Évalue les candidats `str`/`bytes` d'un argument de `eval`/`exec`/`compile` avec indicateur d'exhaustivité."""
    return _eval_str_cands_impl(
        node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns, 0,
        scope.base64_mods, scope.base64_fns, scope.cur_bytes, state=state, scope=scope, include_bytes=True,
    )


def _eval_scope_seq_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> List[List[Optional[str]]]:
    """Wrapper pratique de `_eval_static_seq_candidates` utilisant `state` et `scope` courants."""
    return _eval_static_seq_candidates(
        node, scope.cur_str, scope.cur_seq, state.alt_seq,
        state.imp.shlex_mod_names, state.imp.shlex_split_names, state.imp.func_returns,
    )


def _expr_has_obfuscated_decode(
    node: ast.AST, state: _PyAstState, scope: _ScopeState, _depth: int = 0, _seen: Optional[Set[str]] = None
) -> Optional[str]:
    """Détecte un appel de décodage/décompression/désérialisation obfusqué dans `node` (y compris alias/variables/helpers)."""
    if _depth > 4:
        return None
    seen = _seen if _seen is not None else set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if _match_base64_decode_call(sub, scope.base64_mods, scope.base64_fns):
                return "b64decode"
            if _match_codecs_decode_call(sub, scope.codecs_mods, scope.codecs_fns):
                return "codecs.decode"
            fn = sub.func
            if isinstance(fn, ast.Name) and (fn.id in scope.base64_fns or fn.id in state.imp.base64_decode_fns):
                return fn.id
            if isinstance(fn, ast.Name) and (fn.id in scope.codecs_fns or fn.id in state.imp.codecs_decode_fns):
                return "codecs.decode"
            if isinstance(fn, ast.Attribute):
                if fn.attr in _OBFUSCATED_DECODE_ATTRS:
                    return fn.attr
                if fn.attr == "decode" and _is_named_mod_expr(fn.value, "codecs", scope.codecs_mods):
                    return "codecs.decode"
                if fn.attr == "loads" and isinstance(fn.value, ast.Name) and fn.value.id in ("marshal", "pickle"):
                    return f"{fn.value.id}.loads"
            if isinstance(fn, ast.Name) and fn.id == "getattr" and len(sub.args) >= 2:
                for attr_s in _eval_scope_str_cands(sub.args[1], state, scope):
                    if attr_s in _OBFUSCATED_DECODE_ATTRS or attr_s in ("decode", "loads"):
                        return attr_s
                if isinstance(sub.args[0], ast.Name) and (
                    sub.args[0].id in (_OBFUSCATED_DECODE_MODS | scope.codecs_mods | scope.base64_mods)
                ):
                    return "getattr"
            if isinstance(fn, ast.Lambda):
                hit = _expr_has_obfuscated_decode(fn.body, state, scope, _depth + 1, seen)
                if hit is not None:
                    return hit
            elif isinstance(fn, ast.Name) and fn.id in state.imp.func_defs and f"fn:{fn.id}" not in seen:
                seen.add(f"fn:{fn.id}")
                for fn_def in state.imp.func_defs[fn.id]:
                    for b_node in ([fn_def.body] if isinstance(fn_def, ast.Lambda) else list(getattr(fn_def, "body", ()))):
                        hit = _expr_has_obfuscated_decode(b_node, state, scope, _depth + 1, seen)
                        if hit is not None:
                            return hit
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id not in seen:
            seen.add(sub.id)
            for contrib in state.var_contributors.get(sub.id, ()):
                hit = _expr_has_obfuscated_decode(contrib, state, scope, _depth + 1, seen)
                if hit is not None:
                    return hit
            if sub.id in scope.obf_tainted:
                return "obf_tainted"
    return None


def _bind_var(state: _PyAstState, scope: _ScopeState, target_name: str, rhs_node: ast.AST) -> None:
    """Met à jour les liaisons dynamiques/statiques locales pour `target_name = rhs_node`."""
    state.var_contributors.setdefault(target_name, []).append(rhs_node)
    dyn_mod = _resolve_dynamic_import_module(
        rhs_node, scope.cur_str, scope.cur_seq, state.imp.importlib_mod_names, state.imp.import_module_fns
    )
    bound_eval_exec = False
    mod_map = {
        "subprocess": scope.subproc_mods, "os": scope.os_mods, "posix": scope.os_mods,
        "nt": scope.os_mods, "base64": scope.base64_mods, "codecs": scope.codecs_mods, "builtins": scope.builtins_mods,
    }
    if dyn_mod in mod_map:
        mod_map[dyn_mod].add(target_name)
    elif isinstance(rhs_node, ast.Name):
        if rhs_node.id in scope.eval_exec_fns and rhs_node.id not in state.imp.re_func_names:
            scope.eval_exec_fns[target_name] = scope.eval_exec_fns[rhs_node.id]
            bound_eval_exec = True
        for mset in (scope.builtins_mods, scope.codecs_mods, scope.codecs_fns, scope.base64_mods):
            if rhs_node.id in mset:
                mset.add(target_name)
        if rhs_node.id in scope.base64_fns:
            scope.base64_fns[target_name] = scope.base64_fns[rhs_node.id]
    elif isinstance(rhs_node, ast.Attribute):
        if rhs_node.attr == "rmtree":
            scope.rmtree_fns.add(target_name)
        elif rhs_node.attr in OS_SHELL_FUNCS:
            scope.os_shell_fns.add(target_name)
        elif rhs_node.attr in ("eval", "exec", "compile") and _is_named_mod_expr(rhs_node.value, "builtins", scope.builtins_mods):
            scope.eval_exec_fns[target_name] = rhs_node.attr
            bound_eval_exec = True
        elif rhs_node.attr == "decode" and _is_named_mod_expr(rhs_node.value, "codecs", scope.codecs_mods):
            scope.codecs_fns.add(target_name)
        elif rhs_node.attr in _BASE64_DECODE_FUNCS and _is_named_mod_expr(rhs_node.value, "base64", scope.base64_mods):
            scope.base64_fns[target_name] = rhs_node.attr
        elif rhs_node.attr in SUBPROCESS_CALL_FUNCS and _is_named_mod_expr(rhs_node.value, "subprocess", scope.subproc_mods):
            scope.subproc_fns.add(target_name)
    elif isinstance(rhs_node, ast.Call) and isinstance(rhs_node.func, ast.Name) and rhs_node.func.id == "getattr" and len(rhs_node.args) >= 2:
        for g_attr in _eval_scope_str_cands(rhs_node.args[1], state, scope):
            if g_attr == "rmtree":
                scope.rmtree_fns.add(target_name)
            elif g_attr in OS_SHELL_FUNCS:
                scope.os_shell_fns.add(target_name)
            elif g_attr in SUBPROCESS_CALL_FUNCS:
                scope.subproc_fns.add(target_name)
            elif g_attr in _BASE64_DECODE_FUNCS:
                scope.base64_fns[target_name] = g_attr
            elif g_attr == "decode" and _is_named_mod_expr(rhs_node.args[0], "codecs", scope.codecs_mods):
                scope.codecs_fns.add(target_name)
            elif g_attr in ("eval", "exec", "compile") and _is_named_mod_expr(rhs_node.args[0], "builtins", scope.builtins_mods):
                scope.eval_exec_fns[target_name] = g_attr
                bound_eval_exec = True
    if not bound_eval_exec:
        scope.eval_exec_fns.pop(target_name, None)

    obf_rhs = _expr_has_obfuscated_decode(rhs_node, state, scope)
    str_cands = _eval_scope_str_cands(rhs_node, state, scope)
    if str_cands:
        _, is_complete = _eval_code_arg_cands(rhs_node, state, scope)
        scope.cur_str[target_name] = str_cands[0]
        scope.cur_seq.pop(target_name, None)
        scope.cur_bytes.pop(target_name, None)
        for c in str_cands:
            state.record_alt_str(target_name, c)
        if is_complete:
            scope.incomplete_vars.discard(target_name)
            scope.obf_tainted.discard(target_name)
        else:
            scope.incomplete_vars.add(target_name)
            if obf_rhs is not None:
                scope.obf_tainted.add(target_name)
        return
    bytes_val = _eval_static_bytes(
        rhs_node, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns,
        scope.base64_mods, scope.base64_fns, scope.cur_bytes,
    )
    if bytes_val is not None:
        scope.cur_bytes[target_name] = bytes_val
        state.record_alt_bytes(target_name, bytes_val)
        scope.cur_str.pop(target_name, None)
        scope.cur_seq.pop(target_name, None)
        if not _has_tainted_name(rhs_node, state, scope):
            scope.incomplete_vars.discard(target_name)
            scope.obf_tainted.discard(target_name)
        else:
            scope.incomplete_vars.add(target_name)
            if obf_rhs is not None:
                scope.obf_tainted.add(target_name)
        return
    seq_cands = _eval_scope_seq_cands(rhs_node, state, scope)
    if seq_cands:
        scope.cur_seq[target_name] = list(seq_cands[0])
        scope.cur_str.pop(target_name, None)
        scope.cur_bytes.pop(target_name, None)
        for sc in seq_cands:
            state.record_alt_seq(target_name, sc)
        scope.incomplete_vars.discard(target_name)
        return
    scope.cur_str.pop(target_name, None)
    scope.cur_seq.pop(target_name, None)
    scope.cur_bytes.pop(target_name, None)
    scope.incomplete_vars.add(target_name)
    if obf_rhs is not None:
        scope.obf_tainted.add(target_name)
    state.alt_seq.pop(target_name, None)
    state.dynamic_tainted_vars.add(target_name)
