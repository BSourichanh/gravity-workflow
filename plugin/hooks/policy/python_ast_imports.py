#!/usr/bin/env python3
"""Résolution d'imports dynamiques, `builtins`/`attrgetter`/`partial`, taint et binding de variables AST."""

import ast
from typing import Dict, List, Optional, Set, Tuple

from .constants import OS_SHELL_FUNCS, SUBPROCESS_CALL_FUNCS
from .python_ast_eval import _eval_static_bytes, _eval_static_int, _eval_static_int_list, _eval_static_str
from .python_ast_readonly import _decode_codecs_payload, _eval_static_seq_candidates, _match_codecs_decode_call
from .python_ast_state import (
    _BASE64_DECODE_FUNCS, _OBFUSCATED_DECODE_ATTRS, _OBFUSCATED_DECODE_MODS,
    _PyAstState, _PyImports, _ScopeState, _ast_subscript_slice,
    _collect_python_imports, _decode_base64_payload, _decode_bytes_text,
    _is_named_mod_expr, _match_base64_decode_call,
)

_EVAL_NAMES = ("eval", "exec", "compile")


def _expr_has_tainted_name(
    node: ast.AST, state: Optional[_PyAstState], scope: Optional[_ScopeState], _bound_comp: Optional[Set[str]] = None
) -> bool:
    """Retourne True si `node` référence une variable dynamique/incomplète non liée par une compréhension locale."""
    if state is None and scope is None:
        return False
    bound = _bound_comp or set()
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        inner_bound = set(bound)
        for gen in node.generators:
            if _expr_has_tainted_name(gen.iter, state, scope, inner_bound):
                return True
            for sub in ast.walk(gen.target):
                if isinstance(sub, ast.Name):
                    inner_bound.add(sub.id)
            for if_e in gen.ifs:
                if _expr_has_tainted_name(if_e, state, scope, inner_bound):
                    return True
        for field_n in ("elt", "key", "value"):
            sub_e = getattr(node, field_n, None)
            if isinstance(sub_e, ast.AST) and _expr_has_tainted_name(sub_e, state, scope, inner_bound):
                return True
        return False
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id in bound:
            return False
        if scope and (node.id in scope.incomplete_vars or node.id in scope.obf_tainted):
            return True
        if state and node.id in state.dynamic_tainted_vars and not (scope and node.id in scope.shadowed_alts):
            return True
        return False
    return any(_expr_has_tainted_name(ch, state, scope, bound) for ch in ast.iter_child_nodes(node))


def _resolve_dynamic_import_module(
    node: ast.AST, const_vars: Dict[str, str], const_seq_vars: Dict[str, List[Optional[str]]],
    importlib_mod_names: Set[str], import_module_fns: Set[str],
) -> Optional[str]:
    """Retourne le nom de module statique d'un appel `__import__('...')` ou `importlib.import_module('...')`."""
    if not isinstance(node, ast.Call) or not node.args: return None
    fn = node.func
    is_dyn = (isinstance(fn, ast.Name) and (fn.id == "__import__" or fn.id in import_module_fns)) or (
        isinstance(fn, ast.Attribute) and fn.attr == "import_module"
        and isinstance(fn.value, ast.Name) and fn.value.id in importlib_mod_names
    )
    return _eval_static_str(node.args[0], const_vars, const_seq_vars) if is_dyn else None


def _merge_scope_branch(dst: _ScopeState, src: _ScopeState, state: Optional[_PyAstState] = None) -> None:
    """Fusionne l'état d'une branche `if`/`try` dans `dst` (et `state.alt_*` si top-level)."""
    for attr in (
        "subproc_mods", "os_mods", "rmtree_fns", "os_shell_fns", "subproc_fns",
        "base64_mods", "codecs_mods", "sys_mods", "functools_mods", "partial_fns",
        "operator_mods", "attrgetter_fns", "builtins_mods", "builtins_dicts",
        "incomplete_vars", "obf_tainted", "shadowed_alts",
    ):
        getattr(dst, attr).update(getattr(src, attr))
    for d_attr in ("base64_fns", "codecs_fns", "eval_exec_fns", "func_defs", "var_nodes", "var_class_inst", "cur_ints"):
        getattr(dst, d_attr).update(getattr(src, d_attr))
    for cls_n, attrs_d in src.class_attrs.items():
        dst.class_attrs.setdefault(cls_n, {}).update(attrs_d)
    for cls_n, meths_d in src.class_methods.items():
        dst.class_methods.setdefault(cls_n, {}).update(meths_d)
    for k in dst.incomplete_vars:
        dst.cur_str.pop(k, None); dst.cur_bytes.pop(k, None); dst.cur_ints.pop(k, None); dst.cur_seq.pop(k, None); dst.var_nodes.pop(k, None)
    for k, v in src.cur_str.items():
        if k not in dst.incomplete_vars: dst.cur_str[k] = v
        if state: state.record_alt_str(k, v)
    for k, vb in src.cur_bytes.items():
        if k not in dst.incomplete_vars: dst.cur_bytes[k] = vb
        if state: state.record_alt_bytes(k, vb)
    for k, vs in src.cur_seq.items():
        dst.cur_seq[k] = list(vs)
        if state: state.record_alt_seq(k, vs)


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
    if _depth > 6: return [expr]
    if isinstance(expr, ast.NamedExpr): return _unwrap_attr_candidates(expr.value, state, scope, _depth + 1)
    if isinstance(expr, ast.Attribute):
        if expr.attr == "__call__": return _unwrap_attr_candidates(expr.value, state, scope, _depth + 1)
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
        if ag_attr is not None: attr_arg = (expr.args[0], ag_attr)
        elif isinstance(fn, ast.Name) and fn.id == "getattr" and len(expr.args) >= 2: attr_arg = (expr.args[0], expr.args[1])
        elif isinstance(fn, ast.Attribute) and fn.attr == "__getattribute__" and len(expr.args) == 1 and not expr.keywords: attr_arg = (fn.value, expr.args[0])
        if attr_arg is not None:
            recv, a_node = attr_arg; out: List[ast.AST] = []
            for a_str in _eval_scope_str_cands(a_node, state, scope):
                if a_str == "__call__":
                    out.extend(_unwrap_attr_candidates(recv, state, scope, _depth + 1))
                else:
                    cur_node: ast.AST = recv
                    for seg in a_str.split("."):
                        if seg: cur_node = ast.Attribute(value=cur_node, attr=seg, ctx=ast.Load())
                    out.append(cur_node)
            if out: return out[:4]
    return [expr]


def _bind_call_params_scope(
    call: ast.Call, fn_node: ast.AST, state: _PyAstState, src_scope: _ScopeState,
    alt_map: Optional[Dict[str, List[str]]] = None, seen_fns: Optional[Set[str]] = None,
) -> Optional[_ScopeState]:
    """Crée un scope cloné avec les paramètres de `fn_node` liés aux arguments de `call`."""
    args_spec: Optional[ast.arguments] = getattr(fn_node, "args", None)
    if args_spec is None or args_spec.vararg or args_spec.kwarg or any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
        return None
    pos_params = [a.arg for a in args_spec.posonlyargs] + [a.arg for a in args_spec.args]
    kw_params = [a.arg for a in args_spec.kwonlyargs]
    all_params = set(pos_params) | set(kw_params)
    if len(call.args) > len(pos_params): return None
    bound_exprs: Dict[str, ast.AST] = {
        **({p: d for p, d in zip(pos_params[-len(args_spec.defaults):], args_spec.defaults)} if args_spec.defaults else {}),
        **{p: d for p, d in zip(kw_params, args_spec.kw_defaults) if d is not None},
        **dict(zip(pos_params, call.args)),
    }
    pos_only = {a.arg for a in args_spec.posonlyargs}
    for kw in call.keywords:
        if kw.arg is None or kw.arg in pos_only or kw.arg not in all_params: return None
        bound_exprs[kw.arg] = kw.value
    fn_scope = src_scope.clone(); fn_scope.shadowed_alts.update(all_params)
    for p in all_params:
        for d in (fn_scope.cur_str, fn_scope.cur_bytes, fn_scope.cur_ints, fn_scope.cur_seq, fn_scope.var_nodes, fn_scope.var_class_inst, fn_scope.base64_fns, fn_scope.codecs_fns, fn_scope.eval_exec_fns, fn_scope.func_defs, fn_scope.class_attrs, fn_scope.class_methods):
            d.pop(p, None)
        for s in (fn_scope.incomplete_vars, fn_scope.obf_tainted, fn_scope.builtins_dicts, fn_scope.sys_mods, fn_scope.functools_mods, fn_scope.partial_fns, fn_scope.operator_mods, fn_scope.attrgetter_fns):
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
        if fn_name in seen: return [], False
        seen.add(fn_name)
    args_spec: Optional[ast.arguments] = getattr(fn_node, "args", None)
    all_params = ({a.arg for a in args_spec.posonlyargs} | {a.arg for a in args_spec.args} | {a.arg for a in args_spec.kwonlyargs}) if args_spec else set()
    fn_alts: Dict[str, List[str]] = {k: list(v) for k, v in state.alt_str.items() if k not in all_params and k not in scope.shadowed_alts}
    fn_scope = _bind_call_params_scope(call, fn_node, state, scope, alt_map=fn_alts, seen_fns=seen)
    if fn_scope is None: return [], False
    _eval_s = lambda expr, sc, inc_b=False: _eval_str_cands_impl(expr, sc.cur_str, sc.cur_seq, fn_alts, state.imp.func_returns, _depth + 1, sc.base64_mods, sc.base64_fns, sc.cur_bytes, state=state, scope=sc, include_bytes=inc_b, _seen_fns=seen)

    def _taint_var(sc: _ScopeState, vn: str, has_obf: bool) -> None:
        sc.cur_str.pop(vn, None); sc.cur_bytes.pop(vn, None); sc.var_nodes.pop(vn, None); sc.incomplete_vars.add(vn)
        if has_obf: sc.obf_tainted.add(vn)

    if isinstance(fn_node, ast.Lambda): return _eval_s(fn_node.body, fn_scope, include_bytes)
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
                    if not in_branch: fn_alts.pop(vn, None)
                    b = fn_alts.setdefault(vn, [])
                    if sc.cur_str[vn] not in b and len(b) < 8: b.append(sc.cur_str[vn])
                else: _taint_var(sc, vn, _expr_has_obfuscated_decode(st.value, state, sc) is not None)
            elif isinstance(st, ast.Return) and st.value is not None:
                flags[1] = True; rc, rok = _eval_s(st.value, sc, include_bytes)
                for item in rc:
                    if item not in out and len(out) < 8: out.append(item)
                flags[0] = flags[0] and rok and bool(rc)
            elif isinstance(st, (ast.If, ast.Try)):
                branches = [list(b) for b in (st.body, getattr(st, "orelse", None)) if b] + [list(h.body) for h in getattr(st, "handlers", None) or [] if h.body]
                base = sc.clone()
                for b_stmts in branches:
                    b_sc = base.clone(); _walk_fn(b_stmts, b_sc, True); _merge_scope_branch(sc, b_sc)
                if getattr(st, "finalbody", None): _walk_fn(list(st.finalbody), sc, in_branch)
            else:
                has_obf = _expr_has_obfuscated_decode(st, state, sc) is not None
                for sub in ast.walk(st):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store): _taint_var(sc, sub.id, has_obf)
                if any(isinstance(sub, ast.Return) and sub.value is not None for sub in ast.walk(st)):
                    flags[1] = True; flags[0] = False
                    for attr in ("body", "orelse", "finalbody"):
                        sub_s = getattr(st, attr, None)
                        if isinstance(sub_s, list) and sub_s: _walk_fn(sub_s, sc.clone(), True)

    _walk_fn(list(getattr(fn_node, "body", ())), fn_scope)
    return out, bool(flags[1] and out and flags[0])


def _eval_str_cands_impl(
    node: ast.AST, cur_str: Dict[str, str], cur_seq: Dict[str, List[Optional[str]]],
    alt_str: Dict[str, List[str]], func_returns: Dict[str, List[ast.AST]], _depth: int = 0,
    base64_mod_names: Optional[Set[str]] = None, base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None, *, state: Optional[_PyAstState] = None,
    scope: Optional[_ScopeState] = None, include_bytes: bool = False, _seen_fns: Optional[Set[str]] = None,
) -> Tuple[List[str], bool]:
    if _depth > 6: return [], False
    out: List[str] = []
    v_nodes = scope.var_nodes if scope else None
    env_i = scope.cur_ints if scope else None
    _add = lambda val: out.append(val) if (val is not None and val not in out and len(out) < 8) else None
    _rec = lambda n, inc_b=include_bytes: _eval_str_cands_impl(n, cur_str, cur_seq, alt_str, func_returns, _depth + 1, base64_mod_names, base64_decode_fns, cur_bytes, state=state, scope=scope, include_bytes=inc_b, _seen_fns=_seen_fns)

    if isinstance(node, ast.IfExp):
        b_c, b_ok = _rec(node.body); o_c, o_ok = _rec(node.orelse)
        for c in b_c + o_c: _add(c)
        return out, bool(b_ok and o_ok and b_c and o_c)
    if isinstance(node, ast.NamedExpr): return _rec(node.value)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str): return [node.value], True
        if include_bytes and isinstance(node.value, bytes): return [_decode_bytes_text(node.value)], True
    if isinstance(node, ast.Name):
        if node.id in cur_str: _add(cur_str[node.id])
        if cur_bytes and node.id in cur_bytes: _add(_decode_bytes_text(cur_bytes[node.id]))
        if alt_str is not (state.alt_str if state else None) or not (scope and node.id in scope.shadowed_alts):
            for alt in alt_str.get(node.id, ()): _add(alt)
            if include_bytes and state:
                for alt_b in state.alt_bytes.get(node.id, ()): _add(_decode_bytes_text(alt_b))
        tainted = (scope and (node.id in scope.incomplete_vars or node.id in scope.obf_tainted)) or (
            state and node.id in state.dynamic_tainted_vars and not (scope and node.id in scope.shadowed_alts)
        )
        return out, bool(out and not tainted)
    if isinstance(node, ast.Call):
        b64_match = _match_base64_decode_call(node, base64_mod_names, base64_decode_fns)
        if b64_match is not None:
            fn_canon, arg0 = b64_match; arg_cands, arg_ok = _rec(arg0, True); all_ok = arg_ok and bool(arg_cands)
            for cand in arg_cands:
                dec_b = _decode_base64_payload(cand, fn_canon)
                if dec_b is None: all_ok = False
                elif include_bytes: _add(_decode_bytes_text(dec_b))
                else:
                    try: _add(dec_b.decode("utf-8"))
                    except UnicodeDecodeError: all_ok = False
            return out, bool(out and all_ok)
        c_mods = scope.codecs_mods if scope else None
        c_fns = scope.codecs_fns if scope else (state.imp.codecs_decode_fns if state else None)
        codecs_match = _match_codecs_decode_call(node, c_mods, c_fns)
        if codecs_match is not None:
            obj_arg, enc_arg, c_mode = codecs_match
            raw_b = _eval_static_bytes(obj_arg, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i)
            if raw_b is not None and not _expr_has_tainted_name(obj_arg, state, scope): raw_list: List[object] = [raw_b]; raw_ok = True
            else: obj_cands, raw_ok = _rec(obj_arg, include_bytes); raw_list = list(obj_cands)
            enc_cands, enc_ok = ([None], True) if enc_arg is None else _rec(enc_arg, False)
            all_ok = raw_ok and enc_ok and bool(raw_list) and bool(enc_cands)
            for r_val in raw_list:
                for e_val in enc_cands:
                    dec_s = _decode_codecs_payload(r_val, e_val if isinstance(e_val, str) else None, c_mode)
                    all_ok = all_ok and (dec_s is not None); _add(dec_s)
            return out, bool(out and all_ok)
        fn = node.func
        if include_bytes and isinstance(fn, ast.Name) and fn.id in ("bytes", "bytearray") and len(node.args) == 1 and not node.keywords:
            b_val = _eval_static_bytes(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i)
            if b_val is not None:
                _add(_decode_bytes_text(b_val)); return out, not _expr_has_tainted_name(node, state, scope)
            sub_c, sub_ok = _rec(node.args[0], True)
            if sub_c: return sub_c, sub_ok
        if isinstance(fn, ast.Attribute) and fn.attr in ("decode", "encode") and len(node.args) <= 1 and not node.keywords:
            if (fn.attr == "decode" or include_bytes) and isinstance(fn.value, ast.Call) and (
                _match_base64_decode_call(fn.value, base64_mod_names, base64_decode_fns) or _match_codecs_decode_call(fn.value, c_mods, c_fns)
            ): return _rec(fn.value)
        if state is not None and scope is not None:
            if isinstance(fn, ast.Lambda): return _eval_func_call_code_cands(node, fn, state, scope, _depth + 1, _seen_fns, include_bytes)
            fn_list = (scope.func_defs.get(fn.id) or state.imp.func_defs.get(fn.id)) if isinstance(fn, ast.Name) else None
            if fn_list:
                all_ok = True
                for fn_def in fn_list:
                    fc, fok = _eval_func_call_code_cands(node, fn_def, state, scope, _depth + 1, _seen_fns, include_bytes)
                    for item in fc: _add(item)
                    all_ok = all_ok and fok and bool(fc)
                return out, bool(out and all_ok)
        elif isinstance(fn, ast.Name) and fn.id in func_returns and not node.args and not node.keywords:
            all_ok = True
            for ret_expr in func_returns[fn.id]:
                rc, rok = _rec(ret_expr)
                for item in rc: _add(item)
                all_ok = all_ok and rok and bool(rc)
            if out: return out, all_ok
    val = _eval_static_str(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i)
    if val is None and include_bytes:
        b_val = _eval_static_bytes(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i)
        if b_val is not None: val = _decode_bytes_text(b_val)
    _add(val)
    if isinstance(node, (ast.JoinedStr, ast.BinOp)) or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("format", "replace")):
        for nm in {s.id for s in ast.walk(node) if isinstance(s, ast.Name) and isinstance(s.ctx, ast.Load) and s.id in alt_str}:
            for alt_v in alt_str.get(nm, ()):
                alt_cur = dict(cur_str); alt_cur[nm] = alt_v
                _add(_eval_static_str(node, alt_cur, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i))
    return out, bool(out and not _expr_has_tainted_name(node, state, scope))


def _eval_static_str_candidates(node: ast.AST, cur_str: Dict[str, str], cur_seq: Dict[str, List[Optional[str]]], alt_str: Dict[str, List[str]], func_returns: Dict[str, List[ast.AST]], _depth: int = 0, base64_mod_names: Optional[Set[str]] = None, base64_decode_fns: Optional[Dict[str, str]] = None, cur_bytes: Optional[Dict[str, bytes]] = None) -> List[str]:
    return _eval_str_cands_impl(node, cur_str, cur_seq, alt_str, func_returns, _depth, base64_mod_names, base64_decode_fns, cur_bytes)[0]


def _eval_scope_str_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> List[str]:
    return _eval_str_cands_impl(node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns, 0, scope.base64_mods, scope.base64_fns, scope.cur_bytes, state=state, scope=scope, include_bytes=False)[0]


def _eval_code_arg_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> Tuple[List[str], bool]:
    return _eval_str_cands_impl(node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns, 0, scope.base64_mods, scope.base64_fns, scope.cur_bytes, state=state, scope=scope, include_bytes=True)


def _eval_scope_seq_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> List[List[Optional[str]]]:
    return _eval_static_seq_candidates(node, scope.cur_str, scope.cur_seq, state.alt_seq, state.imp.shlex_mod_names, state.imp.shlex_split_names, state.imp.func_returns)


def _expr_has_obfuscated_decode(node: ast.AST, state: _PyAstState, scope: _ScopeState, _depth: int = 0, _seen: Optional[Set[str]] = None) -> Optional[str]:
    """Détecte un appel de décodage/décompression/construction dynamique obfusqué dans `node`."""
    if _depth > 4: return None
    seen = _seen if _seen is not None else set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.JoinedStr): return "fstring"
        if isinstance(sub, ast.Attribute):
            if sub.attr in _OBFUSCATED_DECODE_ATTRS: return sub.attr
            if sub.attr in ("decode", "encode", "getdecoder") and _is_named_mod_expr(sub.value, "codecs", scope.codecs_mods): return f"codecs.{sub.attr}"
        if isinstance(sub, ast.Call):
            if _match_base64_decode_call(sub, scope.base64_mods, scope.base64_fns): return "b64decode"
            c_hit = _match_codecs_decode_call(sub, scope.codecs_mods, scope.codecs_fns)
            if c_hit is not None: return f"codecs.{c_hit[2]}"
            fn = sub.func
            if isinstance(fn, ast.Name) and fn.id in ("bytes", "bytearray", "memoryview", "chr"): return fn.id
            if isinstance(fn, ast.Attribute) and fn.attr in ("tobytes", "format", "replace", "join"): return fn.attr
            if isinstance(fn, ast.Attribute) and fn.attr == "loads" and isinstance(fn.value, ast.Name) and fn.value.id in ("marshal", "pickle"): return f"{fn.value.id}.loads"
            if (isinstance(fn, ast.Name) and fn.id == "getattr" and len(sub.args) >= 2) or (isinstance(fn, ast.Attribute) and fn.attr == "__getattribute__" and len(sub.args) == 1):
                a_node = sub.args[1] if isinstance(fn, ast.Name) else sub.args[0]
                r_node = sub.args[0] if isinstance(fn, ast.Name) else fn.value
                for attr_s in _eval_scope_str_cands(a_node, state, scope):
                    if attr_s in _OBFUSCATED_DECODE_ATTRS or attr_s in ("decode", "loads") or (attr_s == "encode" and _is_named_mod_expr(r_node, "codecs", scope.codecs_mods)):
                        return attr_s
                if isinstance(r_node, ast.Name) and r_node.id in (_OBFUSCATED_DECODE_MODS | scope.codecs_mods | scope.base64_mods): return "getattr"
            fn_bodies = [fn.body] if isinstance(fn, ast.Lambda) else (
                [b for fd in (scope.func_defs.get(fn.id) or state.imp.func_defs.get(fn.id) or ())
                 for b in ([fd.body] if isinstance(fd, ast.Lambda) else list(getattr(fd, "body", ())))]
                if isinstance(fn, ast.Name) and f"fn:{fn.id}" not in seen else []
            )
            if isinstance(fn, ast.Name) and fn_bodies: seen.add(f"fn:{fn.id}")
            for b_node in fn_bodies:
                hit = _expr_has_obfuscated_decode(b_node, state, scope, _depth + 1, seen)
                if hit is not None: return hit
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            if sub.id in scope.base64_fns or sub.id in state.imp.base64_decode_fns: return sub.id
            c_mode = scope.codecs_fns.get(sub.id) or state.imp.codecs_decode_fns.get(sub.id)
            if c_mode: return f"codecs.{c_mode}"
            if sub.id in scope.obf_tainted: return "obf_tainted"
            if sub.id not in seen:
                seen.add(sub.id)
                if sub.id in scope.var_nodes and (sub.id in scope.shadowed_alts or sub.id not in state.var_contributors):
                    hit = _expr_has_obfuscated_decode(scope.var_nodes[sub.id], state, scope, _depth + 1, seen)
                    if hit is not None: return hit
                for contrib in state.var_contributors.get(sub.id, ()):
                    hit = _expr_has_obfuscated_decode(contrib, state, scope, _depth + 1, seen)
                    if hit is not None: return hit
    return None


def _match_dict_lookup_key(node: ast.AST, state: _PyAstState, scope: _ScopeState, expected_key: str) -> Optional[ast.AST]:
    if isinstance(node, ast.Subscript) and expected_key in _eval_scope_str_cands(_ast_subscript_slice(node), state, scope): return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        kn = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "key"), None)
        if kn is not None and expected_key in _eval_scope_str_cands(kn, state, scope): return node.func.value
    return None


def _is_builtins_mod_expr(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> bool:
    if _is_named_mod_expr(node, "builtins", scope.builtins_mods): return True
    s_recv = _match_dict_lookup_key(node, state, scope, "builtins")
    if s_recv is not None:
        sr = scope.var_nodes.get(s_recv.id, s_recv) if isinstance(s_recv, ast.Name) else s_recv
        for cand in _unwrap_attr_candidates(sr, state, scope):
            if isinstance(cand, ast.Attribute) and cand.attr == "modules" and _is_named_mod_expr(cand.value, "sys", scope.sys_mods): return True
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


def _lookup_dict_like_value(cont: ast.AST, t_keys: List[str], state: _PyAstState, scope: _ScopeState) -> List[ast.AST]:
    hits: List[ast.AST] = []
    if isinstance(cont, ast.Dict):
        for kn, vn in zip(cont.keys, cont.values):
            if kn is not None and any(k in t_keys for k in _eval_scope_str_cands(kn, state, scope)): hits.append(vn)
    elif isinstance(cont, ast.Call) and isinstance(cont.func, ast.Name) and cont.func.id == "dict":
        for kw in cont.keywords:
            if kw.arg is not None and kw.arg in t_keys: hits.append(kw.value)
        if cont.args and not isinstance(cont.args[0], ast.Starred):
            if isinstance(cont.args[0], ast.Dict): hits.extend(_lookup_dict_like_value(cont.args[0], t_keys, state, scope))
            elif isinstance(cont.args[0], (ast.List, ast.Tuple)):
                for elt in cont.args[0].elts:
                    if isinstance(elt, (ast.List, ast.Tuple)) and len(elt.elts) == 2 and any(k in t_keys for k in _eval_scope_str_cands(elt.elts[0], state, scope)):
                        hits.append(elt.elts[1])
    return hits


def _resolve_builtins_lookup(expr: ast.AST, state: _PyAstState, scope: _ScopeState, *, is_re_call: bool = False, _depth: int = 0) -> Optional[str]:
    """Résout `eval`/`exec`/`compile` via alias, `builtins`, `.__call__`, `__getattribute__`, `attrgetter`, `partial`, `sys.modules`, `globals()`/`vars()`, `dict()`, classes ou lambdas."""
    if _depth > 6: return None
    rec_fn = lambda e, sc=scope: _resolve_builtins_lookup(e, state, sc, is_re_call=False, _depth=_depth + 1)
    if isinstance(expr, ast.NamedExpr): return rec_fn(expr.value)
    if isinstance(expr, ast.Name):
        return scope.eval_exec_fns[expr.id] if (expr.id in scope.eval_exec_fns and not (expr.id == "compile" and is_re_call)) else None
    unwrapped = _unwrap_attr_candidates(expr, state, scope)
    if unwrapped != [expr]:
        for cand in unwrapped:
            hit = rec_fn(cand)
            if hit is not None: return hit
    if isinstance(expr, ast.Attribute) and expr.attr in _EVAL_NAMES and (_is_builtins_mod_expr(expr.value, state, scope) or (isinstance(expr.value, ast.Name) and expr.value.id in scope.builtins_dicts)):
        return expr.attr
    key_node: Optional[ast.AST] = None
    if isinstance(expr, ast.Subscript):
        if _is_builtins_dict_expr(expr.value, state, scope): key_node = _ast_subscript_slice(expr)
        else:
            cont = scope.var_nodes.get(expr.value.id, expr.value) if isinstance(expr.value, ast.Name) else expr.value
            sl = _ast_subscript_slice(expr)
            if isinstance(cont, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in cont.elts):
                idx = _eval_static_int(sl)
                if idx is not None and -len(cont.elts) <= idx < len(cont.elts): return rec_fn(cont.elts[idx])
            else:
                for vn in _lookup_dict_like_value(cont, _eval_scope_str_cands(sl, state, scope), state, scope):
                    hit = rec_fn(vn)
                    if hit is not None: return hit
    elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "get":
        k_arg = expr.args[0] if expr.args else next((kw.value for kw in expr.keywords if kw.arg == "key"), None)
        if k_arg is not None:
            if _is_builtins_dict_expr(expr.func.value, state, scope): key_node = k_arg
            else:
                cont = scope.var_nodes.get(expr.func.value.id, expr.func.value) if isinstance(expr.func.value, ast.Name) else expr.func.value
                for vn in _lookup_dict_like_value(cont, _eval_scope_str_cands(k_arg, state, scope), state, scope):
                    hit = rec_fn(vn)
                    if hit is not None: return hit
    elif isinstance(expr, ast.Call):
        fn_list: List[ast.AST] = [expr.func] if isinstance(expr.func, ast.Lambda) else (
            list(scope.func_defs.get(expr.func.id) or state.imp.func_defs.get(expr.func.id) or ()) if isinstance(expr.func, ast.Name) else []
        )
        for fd in fn_list:
            loc = _bind_call_params_scope(expr, fd, state, scope) or scope.clone()
            if isinstance(fd, ast.Lambda):
                hit = rec_fn(fd.body, loc)
                if hit is not None: return hit
            else:
                for st in getattr(fd, "body", ()):
                    if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                        _bind_var(state, loc, st.targets[0].id, st.value, record_contrib=False)
                for sub in ast.walk(fd):
                    if isinstance(sub, ast.Return) and sub.value is not None:
                        hit = rec_fn(sub.value, loc)
                        if hit is not None: return hit
    return next((k for k in _eval_scope_str_cands(key_node, state, scope) if k in _EVAL_NAMES), None) if key_node is not None else None


def _bind_var(
    state: _PyAstState, scope: _ScopeState, target_name: str, rhs_node: ast.AST, *,
    src_scope: Optional[_ScopeState] = None, alt_map: Optional[Dict[str, List[str]]] = None,
    clear_alts: bool = True, seen_fns: Optional[Set[str]] = None, record_contrib: bool = True,
) -> None:
    """Met à jour les liaisons dynamiques/statiques locales pour `target_name = rhs_node`."""
    src = src_scope or scope
    alts = alt_map if alt_map is not None else state.alt_str
    if record_contrib: state.var_contributors.setdefault(target_name, []).append(rhs_node)
    else:
        scope.shadowed_alts.add(target_name)
        if clear_alts: alts.pop(target_name, None)
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
            (src.codecs_mods, scope.codecs_mods), (src.base64_mods, scope.base64_mods),
            (src.subproc_mods, scope.subproc_mods), (src.os_mods, scope.os_mods),
            (src.sys_mods, scope.sys_mods), (src.functools_mods, scope.functools_mods),
            (src.partial_fns, scope.partial_fns), (src.operator_mods, scope.operator_mods),
            (src.attrgetter_fns, scope.attrgetter_fns),
        ):
            if rhs_node.id in s_set: d_set.add(target_name)
        if rhs_node.id in src.codecs_fns: scope.codecs_fns[target_name] = src.codecs_fns[rhs_node.id]
        if rhs_node.id in src.base64_fns: scope.base64_fns[target_name] = src.base64_fns[rhs_node.id]
        if rhs_node.id in src.rmtree_fns: scope.rmtree_fns.add(target_name)
        if rhs_node.id in src.os_shell_fns: scope.os_shell_fns.add(target_name)
        if rhs_node.id in src.subproc_fns: scope.subproc_fns.add(target_name)
    else:
        for cand in _unwrap_attr_candidates(rhs_node, state, src):
            if isinstance(cand, ast.Attribute):
                attr_n, recv = cand.attr, cand.value; is_dyn = cand is not rhs_node
                if attr_n == "rmtree": scope.rmtree_fns.add(target_name)
                elif attr_n in OS_SHELL_FUNCS: scope.os_shell_fns.add(target_name)
                elif attr_n in SUBPROCESS_CALL_FUNCS and (is_dyn or _is_named_mod_expr(recv, "subprocess", src.subproc_mods)): scope.subproc_fns.add(target_name)
                elif attr_n in _BASE64_DECODE_FUNCS and (is_dyn or _is_named_mod_expr(recv, "base64", src.base64_mods)): scope.base64_fns[target_name] = attr_n
                elif attr_n in ("decode", "encode", "getdecoder") and _is_named_mod_expr(recv, "codecs", src.codecs_mods): scope.codecs_fns[target_name] = attr_n
                elif attr_n == "partial" and _is_named_mod_expr(recv, "functools", src.functools_mods): scope.partial_fns.add(target_name)
                elif attr_n == "attrgetter" and _is_named_mod_expr(recv, "operator", src.operator_mods): scope.attrgetter_fns.add(target_name)
    if _is_builtins_mod_expr(rhs_node, state, src) and not _is_named_mod_expr(rhs_node, "builtins", src.builtins_mods):
        scope.builtins_mods.add(target_name); scope.builtins_dicts.add(target_name)
    elif isinstance(rhs_node, (ast.Attribute, ast.Call, ast.Subscript)) and _is_builtins_dict_expr(rhs_node, state, src) and not _is_named_mod_expr(rhs_node, "builtins", src.builtins_mods):
        scope.builtins_dicts.add(target_name)
    elif not (isinstance(rhs_node, ast.Name) and rhs_node.id in src.builtins_dicts):
        scope.builtins_dicts.discard(target_name)
    if isinstance(rhs_node, ast.Call) and isinstance(rhs_node.func, ast.Name) and (rhs_node.func.id in src.class_attrs or rhs_node.func.id in src.class_methods):
        scope.var_class_inst[target_name] = rhs_node.func.id
    elif isinstance(rhs_node, ast.Name) and rhs_node.id in src.var_class_inst:
        scope.var_class_inst[target_name] = src.var_class_inst[rhs_node.id]
    else: scope.var_class_inst.pop(target_name, None)
    bi_kind = _resolve_builtins_lookup(rhs_node, state, src, is_re_call=(isinstance(rhs_node, ast.Name) and rhs_node.id in state.imp.re_func_names))
    if bi_kind is not None: scope.eval_exec_fns[target_name] = bi_kind
    else: scope.eval_exec_fns.pop(target_name, None)
    fn_list = (src.func_defs.get(rhs_node.id) or state.imp.func_defs.get(rhs_node.id)) if isinstance(rhs_node, ast.Name) else None
    if fn_list: scope.func_defs[target_name] = list(fn_list)
    elif isinstance(rhs_node, ast.Lambda): scope.func_defs[target_name] = [rhs_node]
    else: scope.func_defs.pop(target_name, None)
    obf_rhs = _expr_has_obfuscated_decode(rhs_node, state, src)

    def _rec_alt(val: str) -> None:
        if record_contrib: state.record_alt_str(target_name, val)
        else:
            b = alts.setdefault(target_name, [])
            if val not in b and len(b) < 8: b.append(val)

    def _set_taint(is_ok: bool) -> None:
        (scope.incomplete_vars.discard if is_ok else scope.incomplete_vars.add)(target_name)
        if is_ok: scope.obf_tainted.discard(target_name)
        elif obf_rhs is not None: scope.obf_tainted.add(target_name)

    def _clear_other(*dicts: Dict[str, object]) -> None:
        for d in dicts: d.pop(target_name, None)

    bytes_val = _eval_static_bytes(rhs_node, src.cur_str, src.cur_seq, 0, state.imp.func_returns, src.base64_mods, src.base64_fns, src.cur_bytes, var_nodes=src.var_nodes, env_ints=src.cur_ints)
    if bytes_val is not None and not _expr_has_tainted_name(rhs_node, state, src):
        scope.cur_bytes[target_name] = bytes_val; _rec_alt(_decode_bytes_text(bytes_val))
        if record_contrib: state.record_alt_bytes(target_name, bytes_val)
        _clear_other(scope.cur_str, scope.cur_seq, scope.cur_ints); _set_taint(True); return
    eval_c = lambda inc_b: _eval_str_cands_impl(rhs_node, src.cur_str, src.cur_seq, alts, state.imp.func_returns, 0, src.base64_mods, src.base64_fns, src.cur_bytes, state=state, scope=src, include_bytes=inc_b, _seen_fns=seen_fns)
    str_cands, str_ok = eval_c(False)
    if str_cands:
        for c in str_cands: _rec_alt(c)
        inc_ok = str_ok if record_contrib is False else eval_c(True)[1]
        if inc_ok and (record_contrib or len(str_cands) == 1):
            scope.cur_str[target_name] = str_cands[0]; _clear_other(scope.cur_seq, scope.cur_bytes, scope.cur_ints)
        else:
            scope.cur_str.pop(target_name, None); _clear_other(scope.cur_seq, scope.cur_bytes, scope.cur_ints)
        _set_taint(inc_ok); return
    inc_cands, inc_ok = eval_c(True)
    if inc_cands:
        for c in inc_cands: _rec_alt(c)
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
            for sc in seq_cands: state.record_alt_seq(target_name, sc)
        scope.incomplete_vars.discard(target_name); return
    _clear_other(scope.cur_str, scope.cur_seq, scope.cur_bytes, scope.cur_ints)
    _set_taint(False)
    if record_contrib: state.alt_seq.pop(target_name, None); state.dynamic_tainted_vars.add(target_name)
