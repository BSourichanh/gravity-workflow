#!/usr/bin/env python3
"""Résolution d'imports dynamiques, `builtins`/`attrgetter`/`methodcaller`/`partial`, binding de paramètres et variables AST."""

import ast
from typing import Dict, List, Optional, Set, Tuple

from .constants import OS_SHELL_FUNCS, SUBPROCESS_CALL_FUNCS
from .python_ast_cands import (
    _eval_code_arg_cands,
    _eval_scope_seq_cands,
    _eval_scope_str_cands,
    _eval_static_str_candidates,
    _eval_str_cands_impl,
    _expr_has_obfuscated_decode,
    _lookup_dict_like_value,
    _match_dict_lookup_key,
)
from .python_ast_eval import (
    _eval_static_bytes,
    _eval_static_int,
    _eval_static_int_list,
    _eval_static_str,
)
from .python_ast_readonly import _eval_static_seq_candidates
from .python_ast_state import (
    _BASE64_DECODE_FUNCS,
    _PyAstState,
    _PyImports,
    _ScopeState,
    _ast_subscript_slice,
    _collect_python_imports,
    _decode_bytes_text,
    _expr_has_tainted_name,
    _is_named_mod_expr,
    _merge_scope_branch,
    _resolve_builtin_ctor_name,
)

_EVAL_NAMES = ("eval", "exec", "compile")


def _resolve_dynamic_import_module(
    node: ast.AST, const_vars: Dict[str, str], const_seq_vars: Dict[str, List[Optional[str]]],
    importlib_mod_names: Set[str], import_module_fns: Set[str],
) -> Optional[str]:
    """Retourne le nom de module statique d'un appel `__import__('...')` ou `importlib.import_module('...')`."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    fn = node.func
    is_dyn = (isinstance(fn, ast.Name) and (fn.id == "__import__" or fn.id in import_module_fns)) or (
        isinstance(fn, ast.Attribute) and fn.attr == "import_module"
        and isinstance(fn.value, ast.Name) and fn.value.id in importlib_mod_names
    )
    return _eval_static_str(node.args[0], const_vars, const_seq_vars) if is_dyn else None


def _match_attrgetter_factory(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> Optional[ast.AST]:
    """Retourne le nœud d'attribut cible si `node` est `operator.attrgetter('attr')` (ou alias)."""
    cur = scope.var_nodes.get(node.id, node) if isinstance(node, ast.Name) else node
    if not (isinstance(cur, ast.Call) and len(cur.args) == 1 and not cur.keywords and not isinstance(cur.args[0], ast.Starred)):
        return None
    fn = cur.func
    fn_r = scope.var_nodes.get(fn.id, fn) if isinstance(fn, ast.Name) else fn
    is_ag = (
        (isinstance(fn, ast.Name) and fn.id in scope.attrgetter_fns)
        or (isinstance(fn_r, ast.Attribute) and fn_r.attr == "attrgetter" and _is_named_mod_expr(fn_r.value, "operator", scope.operator_mods))
        or (isinstance(fn_r, ast.Call) and isinstance(fn_r.func, ast.Name) and fn_r.func.id == "getattr" and len(fn_r.args) >= 2
            and _is_named_mod_expr(fn_r.args[0], "operator", scope.operator_mods) and "attrgetter" in _eval_scope_str_cands(fn_r.args[1], state, scope))
    )
    return cur.args[0] if is_ag else None


def _match_methodcaller_factory(
    node: ast.AST, state: _PyAstState, scope: _ScopeState
) -> Optional[Tuple[ast.AST, List[ast.AST], Dict[str, ast.AST]]]:
    """Retourne `(method_name_node, args, kw)` si `node` est `operator.methodcaller(name, ...)`."""
    cur = scope.var_nodes.get(node.id, node) if isinstance(node, ast.Name) else node
    if not (isinstance(cur, ast.Call) and cur.args and not isinstance(cur.args[0], ast.Starred)):
        return None
    fn = cur.func
    fn_r = scope.var_nodes.get(fn.id, fn) if isinstance(fn, ast.Name) else fn
    is_mc = (
        (isinstance(fn, ast.Name) and fn.id in scope.methodcaller_fns)
        or (isinstance(fn_r, ast.Attribute) and fn_r.attr == "methodcaller" and _is_named_mod_expr(fn_r.value, "operator", scope.operator_mods))
        or (isinstance(fn_r, ast.Call) and isinstance(fn_r.func, ast.Name) and fn_r.func.id == "getattr" and len(fn_r.args) >= 2
            and _is_named_mod_expr(fn_r.args[0], "operator", scope.operator_mods) and "methodcaller" in _eval_scope_str_cands(fn_r.args[1], state, scope))
    )
    if not is_mc or any(k.arg is None for k in cur.keywords):
        return None
    kw = {k.arg: k.value for k in cur.keywords if k.arg is not None}
    return cur.args[0], list(cur.args[1:]), kw


def _is_partial_factory(fn: ast.AST, state: _PyAstState, scope: _ScopeState) -> bool:
    fn_r = scope.var_nodes.get(fn.id, fn) if isinstance(fn, ast.Name) else fn
    return (
        (isinstance(fn, ast.Name) and fn.id in scope.partial_fns)
        or (isinstance(fn_r, ast.Attribute) and fn_r.attr == "partial" and _is_named_mod_expr(fn_r.value, "functools", scope.functools_mods))
        or (isinstance(fn_r, ast.Call) and isinstance(fn_r.func, ast.Name) and fn_r.func.id == "getattr" and len(fn_r.args) >= 2
            and _is_named_mod_expr(fn_r.args[0], "functools", scope.functools_mods) and "partial" in _eval_scope_str_cands(fn_r.args[1], state, scope))
    )


def _unwrap_attr_candidates(expr: ast.AST, state: _PyAstState, scope: _ScopeState, _depth: int = 0) -> List[ast.AST]:
    """Développe `__getattribute__`, `getattr`, `operator.attrgetter`, `functools.partial`, `NamedExpr` et attributs de classe."""
    if _depth > 6:
        return [expr]
    if isinstance(expr, ast.NamedExpr):
        return _unwrap_attr_candidates(expr.value, state, scope, _depth + 1)
    if isinstance(expr, ast.Attribute):
        if expr.attr == "__call__":
            return _unwrap_attr_candidates(expr.value, state, scope, _depth + 1)
        cls_n = expr.value.id if (isinstance(expr.value, ast.Name) and expr.value.id in scope.class_attrs) else (
            expr.value.func.id if (isinstance(expr.value, ast.Call) and isinstance(expr.value.func, ast.Name) and expr.value.func.id in scope.class_attrs)
            else (scope.var_class_inst.get(expr.value.id) if isinstance(expr.value, ast.Name) else None)
        )
        if cls_n and cls_n in scope.class_attrs and expr.attr in scope.class_attrs[cls_n]:
            return _unwrap_attr_candidates(scope.class_attrs[cls_n][expr.attr], state, scope, _depth + 1)
        return [expr]
    if isinstance(expr, ast.Call):
        fn = expr.func
        if _is_partial_factory(fn, state, scope) and expr.args and not isinstance(expr.args[0], ast.Starred):
            return _unwrap_attr_candidates(expr.args[0], state, scope, _depth + 1)
        ag_attr = _match_attrgetter_factory(fn, state, scope) if (len(expr.args) == 1 and not expr.keywords and not isinstance(expr.args[0], ast.Starred)) else None
        attr_arg: Optional[Tuple[ast.AST, ast.AST]] = None
        if ag_attr is not None:
            attr_arg = (expr.args[0], ag_attr)
        elif isinstance(fn, ast.Name) and fn.id == "getattr" and len(expr.args) >= 2:
            attr_arg = (expr.args[0], expr.args[1])
        elif isinstance(fn, ast.Attribute) and fn.attr == "__getattribute__" and len(expr.args) == 1 and not expr.keywords:
            attr_arg = (fn.value, expr.args[0])
        if attr_arg is not None:
            recv, a_node = attr_arg
            out: List[ast.AST] = []
            for a_str in _eval_scope_str_cands(a_node, state, scope):
                if a_str == "__call__":
                    out.extend(_unwrap_attr_candidates(recv, state, scope, _depth + 1))
                else:
                    cur_node: ast.AST = recv
                    for seg in a_str.split("."):
                        if seg:
                            cur_node = ast.Attribute(value=cur_node, attr=seg, ctx=ast.Load())
                    out.append(cur_node)
            if out:
                return out[:4]
    return [expr]


def _is_builtins_mod_expr(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> bool:
    if _is_named_mod_expr(node, "builtins", scope.builtins_mods):
        return True
    s_recv = _match_dict_lookup_key(node, state, scope, "builtins")
    if s_recv is not None:
        sr = scope.var_nodes.get(s_recv.id, s_recv) if isinstance(s_recv, ast.Name) else s_recv
        for cand in _unwrap_attr_candidates(sr, state, scope):
            if isinstance(cand, ast.Attribute) and cand.attr == "modules" and _is_named_mod_expr(cand.value, "sys", scope.sys_mods):
                return True
    g_recv = _match_dict_lookup_key(node, state, scope, "__builtins__")
    if g_recv is not None:
        gr = scope.var_nodes.get(g_recv.id, g_recv) if isinstance(g_recv, ast.Name) else g_recv
        if isinstance(gr, ast.Call) and isinstance(gr.func, ast.Name) and gr.func.id in ("globals", "locals", "vars") and not gr.args and not gr.keywords:
            return True
    return False


def _is_builtins_dict_expr(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> bool:
    return (
        (isinstance(node, ast.Name) and node.id in scope.builtins_dicts) or _is_builtins_mod_expr(node, state, scope)
        or any(isinstance(c, ast.Attribute) and c.attr == "__dict__" and _is_builtins_mod_expr(c.value, state, scope) for c in _unwrap_attr_candidates(node, state, scope))
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "vars" and len(node.args) == 1 and not node.keywords and _is_builtins_mod_expr(node.args[0], state, scope))
    )


def _is_os_dict_expr(node: ast.AST, state: _PyAstState, scope: _ScopeState, _depth: int = 0) -> bool:
    """Retourne True si `node` représente `os.__dict__` ou `vars(os)` (y compris alias et `getattr`)."""
    if _depth > 4:
        return False
    if isinstance(node, ast.Name):
        if node.id in scope.os_dicts:
            return True
        if node.id in scope.var_nodes:
            return _is_os_dict_expr(scope.var_nodes[node.id], state, scope, _depth + 1)
        return False
    for cand in _unwrap_attr_candidates(node, state, scope):
        if isinstance(cand, ast.Attribute) and cand.attr == "__dict__":
            return True
        if isinstance(cand, ast.Call) and isinstance(cand.func, ast.Name) and cand.func.id == "vars" and len(cand.args) == 1 and not cand.keywords:
            return True
    return False


def _resolve_builtins_lookup(
    expr: ast.AST, state: _PyAstState, scope: _ScopeState, *, is_re_call: bool = False, _depth: int = 0
) -> Optional[str]:
    """Résout `eval`/`exec`/`compile` via alias, `builtins`, `.__call__`, `__getattribute__`, `attrgetter`, `partial`, `sys.modules`, `globals()`/`vars()`, `dict()`, classes ou lambdas."""
    if _depth > 6:
        return None
    rec_fn = lambda e, sc=scope: _resolve_builtins_lookup(e, state, sc, is_re_call=False, _depth=_depth + 1)
    if isinstance(expr, ast.NamedExpr):
        return rec_fn(expr.value)
    if isinstance(expr, ast.Name):
        return scope.eval_exec_fns[expr.id] if (expr.id in scope.eval_exec_fns and not (expr.id == "compile" and is_re_call)) else None
    unwrapped = _unwrap_attr_candidates(expr, state, scope)
    if unwrapped != [expr]:
        for cand in unwrapped:
            hit = rec_fn(cand)
            if hit is not None:
                return hit
    if isinstance(expr, ast.Attribute) and expr.attr in _EVAL_NAMES and (_is_builtins_mod_expr(expr.value, state, scope) or (isinstance(expr.value, ast.Name) and expr.value.id in scope.builtins_dicts)):
        return expr.attr
    key_node: Optional[ast.AST] = None
    if isinstance(expr, ast.Subscript):
        if _is_builtins_dict_expr(expr.value, state, scope):
            key_node = _ast_subscript_slice(expr)
        else:
            cont = scope.var_nodes.get(expr.value.id, expr.value) if isinstance(expr.value, ast.Name) else expr.value
            sl = _ast_subscript_slice(expr)
            if isinstance(cont, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in cont.elts):
                idx = _eval_static_int(sl)
                if idx is not None and -len(cont.elts) <= idx < len(cont.elts):
                    return rec_fn(cont.elts[idx])
            else:
                for vn in _lookup_dict_like_value(cont, _eval_scope_str_cands(sl, state, scope), state, scope):
                    hit = rec_fn(vn)
                    if hit is not None:
                        return hit
    elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "get":
        k_arg = expr.args[0] if expr.args else next((kw.value for kw in expr.keywords if kw.arg == "key"), None)
        if k_arg is not None:
            if _is_builtins_dict_expr(expr.func.value, state, scope):
                key_node = k_arg
            else:
                for vn in _lookup_dict_like_value(expr.func.value, _eval_scope_str_cands(k_arg, state, scope), state, scope):
                    hit = rec_fn(vn)
                    if hit is not None:
                        return hit
    elif isinstance(expr, ast.Call):
        fn_list: List[ast.AST] = [expr.func] if isinstance(expr.func, ast.Lambda) else (
            list(scope.func_defs.get(expr.func.id) or state.imp.func_defs.get(expr.func.id) or ()) if isinstance(expr.func, ast.Name) else []
        )
        for fd in fn_list:
            loc = _bind_call_params_scope(expr, fd, state, scope) or scope.clone()
            if isinstance(fd, ast.Lambda):
                hit = rec_fn(fd.body, loc)
                if hit is not None:
                    return hit
            else:
                for st in getattr(fd, "body", ()):
                    if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                        _bind_var(state, loc, st.targets[0].id, st.value, record_contrib=False)
                for sub in ast.walk(fd):
                    if isinstance(sub, ast.Return) and sub.value is not None:
                        hit = rec_fn(sub.value, loc)
                        if hit is not None:
                            return hit
    return next((k for k in _eval_scope_str_cands(key_node, state, scope) if k in _EVAL_NAMES), None) if key_node is not None else None


def _unpack_call_args_and_kw(
    call: ast.Call, state: _PyAstState, src_scope: _ScopeState
) -> Optional[Tuple[List[ast.AST], List[Tuple[str, ast.AST]]]]:
    """Développe les arguments `*args` et `**kwargs` résolubles au site d'appel."""
    exp_args: List[ast.AST] = []
    for a in call.args:
        if isinstance(a, ast.Starred):
            v = src_scope.var_nodes.get(a.value.id, a.value) if isinstance(a.value, ast.Name) else a.value
            if isinstance(v, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in v.elts):
                exp_args.extend(v.elts)
            elif isinstance(a.value, ast.Name) and a.value.id in src_scope.cur_seq and all(x is not None for x in src_scope.cur_seq[a.value.id]):
                exp_args.extend(ast.Constant(value=x) for x in src_scope.cur_seq[a.value.id] if x is not None)
            else:
                return None
        else:
            exp_args.append(a)
    exp_kw: List[Tuple[str, ast.AST]] = []
    for kw in call.keywords:
        if kw.arg is not None:
            exp_kw.append((kw.arg, kw.value))
        else:
            v = src_scope.var_nodes.get(kw.value.id, kw.value) if isinstance(kw.value, ast.Name) else kw.value
            if isinstance(v, ast.Dict) and not any(k is None for k in v.keys):
                for kn, vn in zip(v.keys, v.values):
                    ks = _eval_scope_str_cands(kn, state, src_scope) if kn is not None else []
                    if len(ks) != 1:
                        return None
                    exp_kw.append((ks[0], vn))
            else:
                return None
    return exp_args, exp_kw


def _bind_call_params_scope(
    call: ast.Call, fn_node: ast.AST, state: _PyAstState, src_scope: _ScopeState,
    alt_map: Optional[Dict[str, List[str]]] = None, seen_fns: Optional[Set[str]] = None,
) -> Optional[_ScopeState]:
    """Crée un scope cloné avec les paramètres de `fn_node` (y compris `*args`/`**kwargs`) liés aux arguments de `call`."""
    args_spec: Optional[ast.arguments] = getattr(fn_node, "args", None)
    if args_spec is None:
        return None
    unpacked = _unpack_call_args_and_kw(call, state, src_scope)
    if unpacked is None:
        return None
    exp_args, exp_kw = unpacked
    pos_params = [a.arg for a in args_spec.posonlyargs] + [a.arg for a in args_spec.args]
    kw_params = [a.arg for a in args_spec.kwonlyargs]
    vararg_name = args_spec.vararg.arg if args_spec.vararg else None
    kwarg_name = args_spec.kwarg.arg if args_spec.kwarg else None
    all_params = set(pos_params) | set(kw_params) | ({vararg_name} if vararg_name else set()) | ({kwarg_name} if kwarg_name else set())
    if len(exp_args) > len(pos_params) and vararg_name is None:
        return None
    bound_exprs: Dict[str, ast.AST] = {
        **({p: d for p, d in zip(pos_params[-len(args_spec.defaults):], args_spec.defaults)} if args_spec.defaults else {}),
        **{p: d for p, d in zip(kw_params, args_spec.kw_defaults) if d is not None},
        **dict(zip(pos_params, exp_args[:len(pos_params)])),
    }
    if vararg_name is not None:
        bound_exprs[vararg_name] = ast.Tuple(elts=list(exp_args[len(pos_params):]), ctx=ast.Load())
    pos_only = {a.arg for a in args_spec.posonlyargs}
    extra_kw_keys: List[Optional[ast.expr]] = []
    extra_kw_vals: List[ast.expr] = []
    for k_name, k_val in exp_kw:
        if k_name not in pos_only and k_name in (set(pos_params) | set(kw_params)):
            bound_exprs[k_name] = k_val
        elif kwarg_name is not None:
            extra_kw_keys.append(ast.Constant(value=k_name))
            extra_kw_vals.append(k_val if isinstance(k_val, ast.expr) else ast.Constant(value=None))
        else:
            return None
    if kwarg_name is not None:
        bound_exprs[kwarg_name] = ast.Dict(keys=extra_kw_keys, values=extra_kw_vals)
    fn_scope = src_scope.clone()
    fn_scope.shadowed_alts.update(all_params)
    for p in all_params:
        for d in (
            fn_scope.cur_str, fn_scope.cur_bytes, fn_scope.cur_ints, fn_scope.cur_seq,
            fn_scope.var_nodes, fn_scope.var_class_inst, fn_scope.base64_fns,
            fn_scope.codecs_fns, fn_scope.eval_exec_fns, fn_scope.func_defs,
            fn_scope.class_attrs, fn_scope.class_methods, fn_scope.bytes_alias_peers,
        ):
            d.pop(p, None)
        for s in (
            fn_scope.incomplete_vars, fn_scope.obf_tainted, fn_scope.builtins_dicts,
            fn_scope.os_dicts, fn_scope.sys_mods, fn_scope.functools_mods,
            fn_scope.partial_fns, fn_scope.operator_mods, fn_scope.attrgetter_fns,
            fn_scope.methodcaller_fns,
        ):
            s.discard(p)
    for p, expr in bound_exprs.items():
        _bind_var(state, fn_scope, p, expr, src_scope=src_scope, alt_map=alt_map, seen_fns=seen_fns, record_contrib=False)
    return fn_scope


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
    all_params = (
        {a.arg for a in args_spec.posonlyargs}
        | {a.arg for a in args_spec.args}
        | {a.arg for a in args_spec.kwonlyargs}
        | ({args_spec.vararg.arg} if args_spec.vararg else set())
        | ({args_spec.kwarg.arg} if args_spec.kwarg else set())
    ) if args_spec else set()
    fn_alts: Dict[str, List[str]] = {k: list(v) for k, v in state.alt_str.items() if k not in all_params and k not in scope.shadowed_alts}
    fn_scope = _bind_call_params_scope(call, fn_node, state, scope, alt_map=fn_alts, seen_fns=seen)
    if fn_scope is None:
        return [], False
    _eval_s = lambda expr, sc, inc_b=False: _eval_str_cands_impl(
        expr, sc.cur_str, sc.cur_seq, fn_alts, state.imp.func_returns, _depth + 1,
        sc.base64_mods, sc.base64_fns, sc.cur_bytes, state=state, scope=sc, include_bytes=inc_b, _seen_fns=seen,
        fn_call_cb=_eval_func_call_code_cands,
    )

    def _taint_var(sc: _ScopeState, vn: str, has_obf: bool) -> None:
        sc.cur_str.pop(vn, None); sc.cur_bytes.pop(vn, None); sc.var_nodes.pop(vn, None); sc.incomplete_vars.add(vn)
        if has_obf:
            sc.obf_tainted.add(vn)

    if isinstance(fn_node, ast.Lambda):
        return _eval_s(fn_node.body, fn_scope, include_bytes)
    out: List[str] = []; flags = [True, False]

    def _walk_fn(stmts: List[ast.stmt], sc: _ScopeState, in_branch: bool = False) -> None:
        for st in stmts:
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                _bind_var(state, sc, st.targets[0].id, st.value, alt_map=fn_alts, clear_alts=not in_branch, seen_fns=seen, record_contrib=False)
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.value is not None:
                _bind_var(state, sc, st.target.id, st.value, alt_map=fn_alts, clear_alts=not in_branch, seen_fns=seen, record_contrib=False)
            elif isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name) and isinstance(st.op, ast.Add):
                vn = st.target.id; sc.shadowed_alts.add(vn); sc.var_nodes.pop(vn, None); rc_s, rok_s = _eval_s(st.value, sc, False)
                if vn in sc.cur_str and rok_s and len(rc_s) == 1 and len(fn_alts.get(vn, ())) <= 1:
                    sc.cur_str[vn] += rc_s[0]
                    if not in_branch:
                        fn_alts.pop(vn, None)
                    b = fn_alts.setdefault(vn, [])
                    if sc.cur_str[vn] not in b and len(b) < 8:
                        b.append(sc.cur_str[vn])
                else:
                    _taint_var(sc, vn, _expr_has_obfuscated_decode(st.value, state, sc) is not None)
            elif isinstance(st, ast.Return) and st.value is not None:
                flags[1] = True; rc, rok = _eval_s(st.value, sc, include_bytes)
                for item in rc:
                    if item not in out and len(out) < 8:
                        out.append(item)
                flags[0] = flags[0] and rok and bool(rc)
            elif isinstance(st, (ast.If, ast.Try)):
                branches = [list(b) for b in (st.body, getattr(st, "orelse", None)) if b] + [list(h.body) for h in getattr(st, "handlers", None) or [] if h.body]
                base = sc.clone()
                for b_stmts in branches:
                    b_sc = base.clone(); _walk_fn(b_stmts, b_sc, True); _merge_scope_branch(sc, b_sc)
                if getattr(st, "finalbody", None):
                    _walk_fn(list(st.finalbody), sc, in_branch)
            else:
                has_obf = _expr_has_obfuscated_decode(st, state, sc) is not None
                for sub in ast.walk(st):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        _taint_var(sc, sub.id, has_obf)
                if any(isinstance(sub, ast.Return) and sub.value is not None for sub in ast.walk(st)):
                    flags[1] = True; flags[0] = False
                    for attr in ("body", "orelse", "finalbody"):
                        sub_s = getattr(st, attr, None)
                        if isinstance(sub_s, list) and sub_s:
                            _walk_fn(sub_s, sc.clone(), True)

    _walk_fn(list(getattr(fn_node, "body", ())), fn_scope)
    return out, bool(flags[1] and out and flags[0])


def _detach_bytes_alias(scope: _ScopeState, var_name: str) -> None:
    old_group = scope.bytes_alias_peers.pop(var_name, None)
    if old_group:
        for peer in list(old_group):
            if peer != var_name and peer in scope.bytes_alias_peers:
                scope.bytes_alias_peers[peer].discard(var_name)


def _bind_var(
    state: _PyAstState, scope: _ScopeState, target_name: str, rhs_node: ast.AST, *,
    src_scope: Optional[_ScopeState] = None, alt_map: Optional[Dict[str, List[str]]] = None,
    clear_alts: bool = True, seen_fns: Optional[Set[str]] = None, record_contrib: bool = True,
) -> None:
    """Met à jour les liaisons dynamiques/statiques locales pour `target_name = rhs_node`."""
    src = src_scope or scope
    alts = alt_map if alt_map is not None else state.alt_str
    if record_contrib:
        state.var_contributors.setdefault(target_name, []).append(rhs_node)
    else:
        scope.shadowed_alts.add(target_name)
        if clear_alts:
            alts.pop(target_name, None)
    _detach_bytes_alias(scope, target_name)
    scope.var_nodes[target_name] = rhs_node
    dyn_mod = _resolve_dynamic_import_module(rhs_node, src.cur_str, src.cur_seq, state.imp.importlib_mod_names, state.imp.import_module_fns)
    mod_map = {
        "subprocess": scope.subproc_mods, "os": scope.os_mods, "posix": scope.os_mods, "nt": scope.os_mods,
        "base64": scope.base64_mods, "codecs": scope.codecs_mods, "builtins": scope.builtins_mods,
        "sys": scope.sys_mods, "functools": scope.functools_mods, "operator": scope.operator_mods,
    }
    if dyn_mod in mod_map:
        mod_map[dyn_mod].add(target_name)
    elif isinstance(rhs_node, ast.Name):
        for s_set, d_set in (
            (src.builtins_mods, scope.builtins_mods), (src.builtins_dicts, scope.builtins_dicts),
            (src.os_dicts, scope.os_dicts),
            (src.codecs_mods, scope.codecs_mods), (src.base64_mods, scope.base64_mods),
            (src.subproc_mods, scope.subproc_mods), (src.os_mods, scope.os_mods),
            (src.sys_mods, scope.sys_mods), (src.functools_mods, scope.functools_mods),
            (src.partial_fns, scope.partial_fns), (src.operator_mods, scope.operator_mods),
            (src.attrgetter_fns, scope.attrgetter_fns), (src.methodcaller_fns, scope.methodcaller_fns),
        ):
            if rhs_node.id in s_set:
                d_set.add(target_name)
        if rhs_node.id in src.codecs_fns:
            scope.codecs_fns[target_name] = src.codecs_fns[rhs_node.id]
        if rhs_node.id in src.base64_fns:
            scope.base64_fns[target_name] = src.base64_fns[rhs_node.id]
        if rhs_node.id in src.rmtree_fns:
            scope.rmtree_fns.add(target_name)
        if rhs_node.id in src.os_shell_fns:
            scope.os_shell_fns.add(target_name)
        if rhs_node.id in src.subproc_fns:
            scope.subproc_fns.add(target_name)
        if rhs_node.id in src.bytes_alias_peers:
            group = set(src.bytes_alias_peers[rhs_node.id]) | {target_name}
            for peer in group:
                if peer == target_name or peer in scope.bytes_alias_peers:
                    scope.bytes_alias_peers[peer] = set(group)
            scope.bytes_alias_peers[target_name] = set(group)
    else:
        for cand in _unwrap_attr_candidates(rhs_node, state, src):
            if isinstance(cand, ast.Attribute):
                attr_n, recv = cand.attr, cand.value; is_dyn = cand is not rhs_node
                if attr_n == "rmtree": scope.rmtree_fns.add(target_name)
                elif attr_n in OS_SHELL_FUNCS: scope.os_shell_fns.add(target_name)
                elif attr_n in SUBPROCESS_CALL_FUNCS and (is_dyn or _is_named_mod_expr(recv, "subprocess", src.subproc_mods)): scope.subproc_fns.add(target_name)
                elif attr_n in _BASE64_DECODE_FUNCS and (is_dyn or _is_named_mod_expr(recv, "base64", src.base64_mods)): scope.base64_fns[target_name] = attr_n
                elif attr_n in ("decode", "encode", "getdecoder", "lookup") and _is_named_mod_expr(recv, "codecs", src.codecs_mods): scope.codecs_fns[target_name] = attr_n
                elif attr_n == "partial" and _is_named_mod_expr(recv, "functools", src.functools_mods): scope.partial_fns.add(target_name)
                elif attr_n == "attrgetter" and _is_named_mod_expr(recv, "operator", src.operator_mods): scope.attrgetter_fns.add(target_name)
                elif attr_n == "methodcaller" and _is_named_mod_expr(recv, "operator", src.operator_mods): scope.methodcaller_fns.add(target_name)
    if isinstance(rhs_node, ast.Call) and _resolve_builtin_ctor_name(rhs_node.func, src, src.var_nodes) == "bytearray":
        scope.bytes_alias_peers[target_name] = {target_name}
    if _is_builtins_mod_expr(rhs_node, state, src) and not _is_named_mod_expr(rhs_node, "builtins", src.builtins_mods):
        scope.builtins_mods.add(target_name); scope.builtins_dicts.add(target_name)
    elif isinstance(rhs_node, (ast.Attribute, ast.Call, ast.Subscript)) and _is_builtins_dict_expr(rhs_node, state, src) and not _is_named_mod_expr(rhs_node, "builtins", src.builtins_mods):
        scope.builtins_dicts.add(target_name)
    elif not (isinstance(rhs_node, ast.Name) and rhs_node.id in src.builtins_dicts):
        scope.builtins_dicts.discard(target_name)
    if _is_os_dict_expr(rhs_node, state, src) and not _is_builtins_dict_expr(rhs_node, state, src):
        scope.os_dicts.add(target_name)
        if record_contrib:
            state.env_dict_vars.add(target_name)
    elif not (isinstance(rhs_node, ast.Name) and rhs_node.id in src.os_dicts):
        scope.os_dicts.discard(target_name)
    if isinstance(rhs_node, ast.Call) and isinstance(rhs_node.func, ast.Name) and (rhs_node.func.id in src.class_attrs or rhs_node.func.id in src.class_methods):
        scope.var_class_inst[target_name] = rhs_node.func.id
    elif isinstance(rhs_node, ast.Name) and rhs_node.id in src.var_class_inst:
        scope.var_class_inst[target_name] = src.var_class_inst[rhs_node.id]
    else:
        scope.var_class_inst.pop(target_name, None)
    bi_kind = _resolve_builtins_lookup(rhs_node, state, src, is_re_call=(isinstance(rhs_node, ast.Name) and rhs_node.id in state.imp.re_func_names))
    if bi_kind is not None:
        scope.eval_exec_fns[target_name] = bi_kind
    else:
        scope.eval_exec_fns.pop(target_name, None)
    fn_list = (src.func_defs.get(rhs_node.id) or state.imp.func_defs.get(rhs_node.id)) if isinstance(rhs_node, ast.Name) else None
    if fn_list:
        scope.func_defs[target_name] = list(fn_list)
    elif isinstance(rhs_node, ast.Lambda):
        scope.func_defs[target_name] = [rhs_node]
    else:
        scope.func_defs.pop(target_name, None)
    obf_rhs = _expr_has_obfuscated_decode(rhs_node, state, src)

    def _rec_alt(val: str) -> None:
        if record_contrib:
            state.record_alt_str(target_name, val)
        else:
            b = alts.setdefault(target_name, [])
            if val not in b and len(b) < 8:
                b.append(val)

    def _set_taint(is_ok: bool) -> None:
        (scope.incomplete_vars.discard if is_ok else scope.incomplete_vars.add)(target_name)
        if is_ok:
            scope.obf_tainted.discard(target_name)
        elif obf_rhs is not None:
            scope.obf_tainted.add(target_name)

    def _clear_other(*dicts: Dict[str, object]) -> None:
        for d in dicts:
            d.pop(target_name, None)

    bytes_val = _eval_static_bytes(rhs_node, src.cur_str, src.cur_seq, 0, state.imp.func_returns, src.base64_mods, src.base64_fns, src.cur_bytes, var_nodes=src.var_nodes, env_ints=src.cur_ints)
    if bytes_val is not None and not _expr_has_tainted_name(rhs_node, state, src):
        scope.cur_bytes[target_name] = bytes_val; _rec_alt(_decode_bytes_text(bytes_val))
        if record_contrib:
            state.record_alt_bytes(target_name, bytes_val)
        _clear_other(scope.cur_str, scope.cur_seq, scope.cur_ints); _set_taint(True); return
    eval_c = lambda inc_b: _eval_str_cands_impl(
        rhs_node, src.cur_str, src.cur_seq, alts, state.imp.func_returns, 0,
        src.base64_mods, src.base64_fns, src.cur_bytes, state=state, scope=src,
        include_bytes=inc_b, _seen_fns=seen_fns, fn_call_cb=_eval_func_call_code_cands,
    )
    str_cands, str_ok = eval_c(False)
    if str_cands:
        for c in str_cands:
            _rec_alt(c)
        inc_ok = str_ok if record_contrib is False else eval_c(True)[1]
        if inc_ok and (record_contrib or len(str_cands) == 1):
            scope.cur_str[target_name] = str_cands[0]; _clear_other(scope.cur_seq, scope.cur_bytes, scope.cur_ints)
        else:
            scope.cur_str.pop(target_name, None); _clear_other(scope.cur_seq, scope.cur_bytes, scope.cur_ints)
        _set_taint(inc_ok); return
    inc_cands, inc_ok = eval_c(True)
    if inc_cands:
        for c in inc_cands:
            _rec_alt(c)
        scope.cur_str.pop(target_name, None); _clear_other(scope.cur_seq, scope.cur_bytes, scope.cur_ints); _set_taint(inc_ok); return
    int_val = _eval_static_int(rhs_node, src.cur_ints, src.cur_str)
    if int_val is not None and not _expr_has_tainted_name(rhs_node, state, src):
        scope.cur_ints[target_name] = int_val; _clear_other(scope.cur_str, scope.cur_seq, scope.cur_bytes); _set_taint(True); return
    int_list = _eval_static_int_list(rhs_node, src.cur_str, src.var_nodes, src.cur_bytes, src.cur_ints)
    if int_list is not None and not _expr_has_tainted_name(rhs_node, state, src):
        _clear_other(scope.cur_str, scope.cur_seq, scope.cur_bytes, scope.cur_ints); _set_taint(True); return
    seq_cands = _eval_static_seq_candidates(rhs_node, src.cur_str, src.cur_seq, state.alt_seq, state.imp.shlex_mod_names, state.imp.shlex_split_names, state.imp.func_returns)
    if seq_cands:
        scope.cur_seq[target_name] = list(seq_cands[0]); _clear_other(scope.cur_str, scope.cur_bytes, scope.cur_ints)
        if record_contrib:
            for sc in seq_cands:
                state.record_alt_seq(target_name, sc)
        scope.incomplete_vars.discard(target_name); return
    _clear_other(scope.cur_str, scope.cur_seq, scope.cur_bytes, scope.cur_ints)
    _set_taint(False)
    if record_contrib:
        state.alt_seq.pop(target_name, None); state.dynamic_tainted_vars.add(target_name)
