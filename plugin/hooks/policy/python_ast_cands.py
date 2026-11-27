#!/usr/bin/env python3
"""Évaluation de candidats multi-branches et détection d'obfuscation AST pour `python_ast`."""

import ast
from typing import Callable, Dict, List, Optional, Set, Tuple

from .python_ast_eval import (
    _eval_static_bytes,
    _eval_static_int,
    _eval_static_str,
)
from .python_ast_readonly import (
    _decode_codecs_payload,
    _eval_static_seq_candidates,
    _match_codecs_decode_call,
)
from .python_ast_state import (
    _OBFUSCATED_DECODE_ATTRS,
    _OBFUSCATED_DECODE_MODS,
    _PyAstState,
    _ScopeState,
    _ast_subscript_slice,
    _decode_base64_payload,
    _decode_bytes_text,
    _expr_has_tainted_name,
    _is_named_mod_expr,
    _match_base64_decode_call,
    _resolve_builtin_ctor_name,
)

_STR_TRANSFORM_ATTRS = frozenset(
    {
        "format", "format_map", "replace", "strip", "lstrip", "rstrip",
        "removeprefix", "removesuffix", "lower", "upper", "swapcase",
        "casefold", "title", "capitalize", "translate",
    }
)
_OBF_CALL_ATTRS = _STR_TRANSFORM_ATTRS | frozenset({"tobytes", "join"})


def _lookup_dict_like_value(
    cont: ast.AST, t_keys: List[str], state: _PyAstState, scope: _ScopeState
) -> List[ast.AST]:
    """Résout les valeurs associées aux clés `t_keys` dans un littéral `dict` ou appel `dict(...)`."""
    cur = scope.var_nodes.get(cont.id, cont) if isinstance(cont, ast.Name) else cont
    hits: List[ast.AST] = []
    if isinstance(cur, ast.Dict):
        for kn, vn in zip(cur.keys, cur.values):
            if kn is not None and any(k in t_keys for k in _eval_scope_str_cands(kn, state, scope)):
                hits.append(vn)
    elif isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name) and cur.func.id == "dict":
        for kw in cur.keywords:
            if kw.arg is not None and kw.arg in t_keys:
                hits.append(kw.value)
        if cur.args and not isinstance(cur.args[0], ast.Starred):
            if isinstance(cur.args[0], ast.Dict):
                hits.extend(_lookup_dict_like_value(cur.args[0], t_keys, state, scope))
            elif isinstance(cur.args[0], (ast.List, ast.Tuple)):
                for elt in cur.args[0].elts:
                    if isinstance(elt, (ast.List, ast.Tuple)) and len(elt.elts) == 2 and any(
                        k in t_keys for k in _eval_scope_str_cands(elt.elts[0], state, scope)
                    ):
                        hits.append(elt.elts[1])
    return hits


def _match_dict_lookup_key(
    node: ast.AST, state: _PyAstState, scope: _ScopeState, expected_key: str
) -> Optional[ast.AST]:
    """Retourne le conteneur si `node` accède à `expected_key` via `d[k]` ou `d.get(k)`."""
    if isinstance(node, ast.Subscript) and expected_key in _eval_scope_str_cands(_ast_subscript_slice(node), state, scope):
        return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        kn = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "key"), None)
        if kn is not None and expected_key in _eval_scope_str_cands(kn, state, scope):
            return node.func.value
    return None


def _eval_str_cands_impl(
    node: ast.AST,
    cur_str: Dict[str, str],
    cur_seq: Dict[str, List[Optional[str]]],
    alt_str: Dict[str, List[str]],
    func_returns: Dict[str, List[ast.AST]],
    _depth: int = 0,
    base64_mod_names: Optional[Set[str]] = None,
    base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
    *,
    state: Optional[_PyAstState] = None,
    scope: Optional[_ScopeState] = None,
    include_bytes: bool = False,
    _seen_fns: Optional[Set[str]] = None,
    fn_call_cb: Optional[Callable[..., Tuple[List[str], bool]]] = None,
) -> Tuple[List[str], bool]:
    """Évalue les chaînes candidates possibles d'une expression AST avec suivi de complétude."""
    if _depth > 6:
        return [], False
    out: List[str] = []
    v_nodes = scope.var_nodes if scope else None
    env_i = scope.cur_ints if scope else None
    call_cb = fn_call_cb or (state.fn_call_cb if state else None)
    _add = lambda val: out.append(val) if (val is not None and val not in out and len(out) < 8) else None
    _rec = lambda n, inc_b=include_bytes: _eval_str_cands_impl(
        n, cur_str, cur_seq, alt_str, func_returns, _depth + 1,
        base64_mod_names, base64_decode_fns, cur_bytes,
        state=state, scope=scope, include_bytes=inc_b, _seen_fns=_seen_fns, fn_call_cb=call_cb,
    )

    if isinstance(node, ast.IfExp):
        b_c, b_ok = _rec(node.body); o_c, o_ok = _rec(node.orelse)
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
        if alt_str is not (state.alt_str if state else None) or not (scope and node.id in scope.shadowed_alts):
            for alt in alt_str.get(node.id, ()):
                _add(alt)
            if include_bytes and state:
                for alt_b in state.alt_bytes.get(node.id, ()):
                    _add(_decode_bytes_text(alt_b))
        tainted = (scope and (node.id in scope.incomplete_vars or node.id in scope.obf_tainted)) or (
            state and node.id in state.dynamic_tainted_vars and not (scope and node.id in scope.shadowed_alts)
        )
        return out, bool(out and not tainted)
    if isinstance(node, ast.Subscript) and scope is not None and state is not None:
        cont = v_nodes.get(node.value.id, node.value) if (v_nodes and isinstance(node.value, ast.Name)) else node.value
        sl = _ast_subscript_slice(node)
        if isinstance(cont, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in cont.elts):
            idx = _eval_static_int(sl, env_i, cur_str, _depth + 1)
            if idx is not None and -len(cont.elts) <= idx < len(cont.elts):
                return _rec(cont.elts[idx])
        k_cands, k_ok = _rec(sl, False)
        hits = _lookup_dict_like_value(cont, k_cands, state, scope) if k_cands else []
        if hits:
            all_ok = k_ok
            for vn in hits:
                vc, vok = _rec(vn)
                for item in vc:
                    _add(item)
                all_ok = all_ok and vok and bool(vc)
            if out:
                return out, all_ok
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
            obj_arg, enc_arg, c_mode = codecs_match
            raw_b = _eval_static_bytes(obj_arg, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i)
            if raw_b is not None and not _expr_has_tainted_name(obj_arg, state, scope):
                raw_list: List[object] = [raw_b]; raw_ok = True
            else:
                obj_cands, raw_ok = _rec(obj_arg, include_bytes); raw_list = list(obj_cands)
            enc_cands, enc_ok = ([None], True) if enc_arg is None else _rec(enc_arg, False)
            all_ok = raw_ok and enc_ok and bool(raw_list) and bool(enc_cands)
            for r_val in raw_list:
                for e_val in enc_cands:
                    dec_s = _decode_codecs_payload(r_val, e_val if isinstance(e_val, str) else None, c_mode)
                    all_ok = all_ok and (dec_s is not None)
                    _add(dec_s)
            return out, bool(out and all_ok)
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "get" and scope is not None and state is not None:
            k_arg = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "key"), None)
            if k_arg is not None:
                k_cands, k_ok = _rec(k_arg, False)
                hits = _lookup_dict_like_value(fn.value, k_cands, state, scope) if k_cands else []
                if hits:
                    all_ok = k_ok
                    for vn in hits:
                        vc, vok = _rec(vn)
                        for item in vc:
                            _add(item)
                        all_ok = all_ok and vok and bool(vc)
                    if out:
                        return out, all_ok
        if include_bytes and _resolve_builtin_ctor_name(fn, scope, v_nodes) in ("bytes", "bytearray") and len(node.args) == 1 and not node.keywords:
            b_val = _eval_static_bytes(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i)
            if b_val is not None:
                _add(_decode_bytes_text(b_val))
                return out, not _expr_has_tainted_name(node, state, scope)
            sub_c, sub_ok = _rec(node.args[0], True)
            if sub_c:
                return sub_c, sub_ok
        if isinstance(fn, ast.Attribute) and fn.attr in ("decode", "encode") and len(node.args) <= 1 and not node.keywords:
            if (fn.attr == "decode" or include_bytes) and isinstance(fn.value, ast.Call) and (
                _match_base64_decode_call(fn.value, base64_mod_names, base64_decode_fns) or _match_codecs_decode_call(fn.value, c_mods, c_fns)
            ):
                return _rec(fn.value)
        if state is not None and scope is not None and call_cb is not None:
            if isinstance(fn, ast.Lambda):
                return call_cb(node, fn, state, scope, _depth + 1, _seen_fns, include_bytes)
            fn_list = (scope.func_defs.get(fn.id) or state.imp.func_defs.get(fn.id)) if isinstance(fn, ast.Name) else None
            if fn_list:
                all_ok = True
                for fn_def in fn_list:
                    fc, fok = call_cb(node, fn_def, state, scope, _depth + 1, _seen_fns, include_bytes)
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
    val = _eval_static_str(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i)
    if val is None and include_bytes:
        b_val = _eval_static_bytes(node, cur_str, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i)
        if b_val is not None:
            val = _decode_bytes_text(b_val)
    _add(val)
    if isinstance(node, (ast.JoinedStr, ast.BinOp)) or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _STR_TRANSFORM_ATTRS):
        for nm in {s.id for s in ast.walk(node) if isinstance(s, ast.Name) and isinstance(s.ctx, ast.Load) and s.id in alt_str}:
            for alt_v in alt_str.get(nm, ()):
                alt_cur = dict(cur_str); alt_cur[nm] = alt_v
                _add(_eval_static_str(node, alt_cur, cur_seq, 0, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=v_nodes, env_ints=env_i))
    return out, bool(out and not _expr_has_tainted_name(node, state, scope))


def _eval_static_str_candidates(
    node: ast.AST, cur_str: Dict[str, str], cur_seq: Dict[str, List[Optional[str]]],
    alt_str: Dict[str, List[str]], func_returns: Dict[str, List[ast.AST]], _depth: int = 0,
    base64_mod_names: Optional[Set[str]] = None, base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
) -> List[str]:
    return _eval_str_cands_impl(node, cur_str, cur_seq, alt_str, func_returns, _depth, base64_mod_names, base64_decode_fns, cur_bytes)[0]


def _eval_scope_str_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> List[str]:
    return _eval_str_cands_impl(node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns, 0, scope.base64_mods, scope.base64_fns, scope.cur_bytes, state=state, scope=scope, include_bytes=False)[0]


def _eval_code_arg_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> Tuple[List[str], bool]:
    return _eval_str_cands_impl(node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns, 0, scope.base64_mods, scope.base64_fns, scope.cur_bytes, state=state, scope=scope, include_bytes=True)


def _eval_scope_seq_cands(node: ast.AST, state: _PyAstState, scope: _ScopeState) -> List[List[Optional[str]]]:
    return _eval_static_seq_candidates(node, scope.cur_str, scope.cur_seq, state.alt_seq, state.imp.shlex_mod_names, state.imp.shlex_split_names, state.imp.func_returns)


def _expr_has_obfuscated_decode(
    node: ast.AST, state: _PyAstState, scope: _ScopeState, _depth: int = 0, _seen: Optional[Set[str]] = None
) -> Optional[str]:
    """Détecte un appel de décodage/décompression/construction/transformation dynamique obfusqué dans `node`."""
    if _depth > 4:
        return None
    seen = _seen if _seen is not None else set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.JoinedStr):
            return "fstring"
        if isinstance(sub, ast.BinOp) and isinstance(sub.op, (ast.Mod, ast.Mult)):
            if _eval_static_int(sub, scope.cur_ints, scope.cur_str) is None:
                return "%" if isinstance(sub.op, ast.Mod) else "*"
        if isinstance(sub, ast.Attribute):
            if sub.attr in _OBFUSCATED_DECODE_ATTRS:
                return sub.attr
            if sub.attr in ("decode", "encode", "getdecoder", "lookup") and _is_named_mod_expr(sub.value, "codecs", scope.codecs_mods):
                return f"codecs.{sub.attr}"
        if isinstance(sub, ast.Call):
            if _match_base64_decode_call(sub, scope.base64_mods, scope.base64_fns):
                return "b64decode"
            c_hit = _match_codecs_decode_call(sub, scope.codecs_mods, scope.codecs_fns)
            if c_hit is not None:
                return f"codecs.{c_hit[2]}"
            fn = sub.func
            ctor = _resolve_builtin_ctor_name(fn, scope, scope.var_nodes)
            if ctor is not None:
                return ctor
            if isinstance(fn, ast.Attribute) and fn.attr in _OBF_CALL_ATTRS:
                return fn.attr
            if isinstance(fn, ast.Attribute) and fn.attr == "loads" and isinstance(fn.value, ast.Name) and fn.value.id in ("marshal", "pickle"):
                return f"{fn.value.id}.loads"
            if (isinstance(fn, ast.Name) and fn.id == "getattr" and len(sub.args) >= 2) or (isinstance(fn, ast.Attribute) and fn.attr == "__getattribute__" and len(sub.args) == 1):
                a_node = sub.args[1] if isinstance(fn, ast.Name) else sub.args[0]
                r_node = sub.args[0] if isinstance(fn, ast.Name) else fn.value
                for attr_s in _eval_scope_str_cands(a_node, state, scope):
                    if attr_s in _OBFUSCATED_DECODE_ATTRS or attr_s in ("decode", "loads") or (attr_s in ("encode", "lookup") and _is_named_mod_expr(r_node, "codecs", scope.codecs_mods)):
                        return attr_s
                if isinstance(r_node, ast.Name) and r_node.id in (_OBFUSCATED_DECODE_MODS | scope.codecs_mods | scope.base64_mods):
                    return "getattr"
            fn_bodies = [fn.body] if isinstance(fn, ast.Lambda) else (
                [b for fd in (scope.func_defs.get(fn.id) or state.imp.func_defs.get(fn.id) or ())
                 for b in ([fd.body] if isinstance(fd, ast.Lambda) else list(getattr(fd, "body", ())))]
                if isinstance(fn, ast.Name) and f"fn:{fn.id}" not in seen else []
            )
            if isinstance(fn, ast.Name) and fn_bodies:
                seen.add(f"fn:{fn.id}")
            for b_node in fn_bodies:
                hit = _expr_has_obfuscated_decode(b_node, state, scope, _depth + 1, seen)
                if hit is not None:
                    return hit
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            if sub.id in scope.base64_fns or sub.id in state.imp.base64_decode_fns:
                return sub.id
            c_mode = scope.codecs_fns.get(sub.id) or state.imp.codecs_decode_fns.get(sub.id)
            if c_mode:
                return f"codecs.{c_mode}"
            if sub.id in scope.obf_tainted:
                return "obf_tainted"
            ctor_ref = _resolve_builtin_ctor_name(sub, scope, scope.var_nodes) if sub.id in scope.var_nodes else None
            if ctor_ref is not None:
                return ctor_ref
            if sub.id not in seen:
                seen.add(sub.id)
                if sub.id in scope.var_nodes and (sub.id in scope.shadowed_alts or sub.id not in state.var_contributors):
                    hit = _expr_has_obfuscated_decode(scope.var_nodes[sub.id], state, scope, _depth + 1, seen)
                    if hit is not None:
                        return hit
                for contrib in state.var_contributors.get(sub.id, ()):
                    hit = _expr_has_obfuscated_decode(contrib, state, scope, _depth + 1, seen)
                    if hit is not None:
                        return hit
    return None
