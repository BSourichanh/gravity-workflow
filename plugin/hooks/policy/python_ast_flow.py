#!/usr/bin/env python3
"""Résolution des appels indirects (`partial`, `attrgetter`, classes, lambdas/helpers) et suivi de mutation `bytearray`."""

import ast
from typing import Callable, Dict, List, Optional, Tuple

from .python_ast_eval import (
    _eval_slice_bounds,
    _eval_static_bytes,
    _eval_static_int,
    _eval_static_int_list,
)
from .python_ast_imports import (
    _bind_call_params_scope,
    _expr_has_tainted_name,
    _is_partial_factory,
    _lookup_dict_like_value,
    _unwrap_attr_candidates,
)
from .python_ast_state import (
    _PyAstState,
    _ScopeState,
    _ast_subscript_slice,
    _decode_bytes_text,
)


def _eval_scope_strs(expr: ast.AST, state: _PyAstState, scope: _ScopeState) -> List[str]:
    from .python_ast_imports import _eval_scope_str_cands

    return _eval_scope_str_cands(expr, state, scope)


def _unwrap_single_callable(
    expr: ast.AST, state: _PyAstState, scope: _ScopeState, _depth: int = 0
) -> List[ast.AST]:
    """Développe attributs dynamiques, conteneurs (`dict()`, `[]`) et lambdas/helpers identité sur un callable."""
    if _depth > 6:
        return [expr]
    if isinstance(expr, ast.NamedExpr):
        return _unwrap_single_callable(expr.value, state, scope, _depth + 1)
    cands = _unwrap_attr_candidates(expr, state, scope)
    if cands != [expr]:
        out: List[ast.AST] = []
        for c in cands:
            out.extend(_unwrap_single_callable(c, state, scope, _depth + 1))
        return out[:6]
    if isinstance(expr, ast.Subscript):
        cont = scope.var_nodes.get(expr.value.id, expr.value) if isinstance(expr.value, ast.Name) else expr.value
        sl = _ast_subscript_slice(expr)
        if isinstance(cont, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in cont.elts):
            idx = _eval_static_int(sl)
            if idx is not None and -len(cont.elts) <= idx < len(cont.elts):
                return _unwrap_single_callable(cont.elts[idx], state, scope, _depth + 1)
        hits = _lookup_dict_like_value(cont, _eval_scope_strs(sl, state, scope), state, scope)
        if hits:
            out = []
            for h in hits:
                out.extend(_unwrap_single_callable(h, state, scope, _depth + 1))
            return out[:6]
    elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "get":
        k_arg = expr.args[0] if expr.args else next((kw.value for kw in expr.keywords if kw.arg == "key"), None)
        if k_arg is not None:
            cont = scope.var_nodes.get(expr.func.value.id, expr.func.value) if isinstance(expr.func.value, ast.Name) else expr.func.value
            hits = _lookup_dict_like_value(cont, _eval_scope_strs(k_arg, state, scope), state, scope)
            if hits:
                out = []
                for h in hits:
                    out.extend(_unwrap_single_callable(h, state, scope, _depth + 1))
                return out[:6]
    elif isinstance(expr, ast.Call):
        fn_list: List[ast.AST] = (
            [expr.func]
            if isinstance(expr.func, ast.Lambda)
            else (
                list(scope.func_defs.get(expr.func.id) or state.imp.func_defs.get(expr.func.id) or ())
                if isinstance(expr.func, ast.Name)
                else []
            )
        )
        if fn_list:
            out = []
            for fd in fn_list:
                loc = _bind_call_params_scope(expr, fd, state, scope)
                if loc is None:
                    continue
                if isinstance(fd, ast.Lambda) and isinstance(fd.body, ast.Name) and fd.body.id in loc.var_nodes:
                    out.extend(_unwrap_single_callable(loc.var_nodes[fd.body.id], state, loc, _depth + 1))
            if out:
                return out[:6]
    return [expr]


def _unwrap_call_target(
    node: ast.Call, state: _PyAstState, scope: _ScopeState
) -> Tuple[List[ast.AST], List[ast.AST], Dict[str, ast.AST]]:
    """Développe `functools.partial`, `NamedExpr`, `operator.attrgetter`, `__getattribute__`, `dict()`, classes et lambdas."""
    eff_args = list(node.args)
    eff_kw: Dict[str, ast.AST] = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
    fn: ast.AST = node.func
    for _ in range(6):
        if isinstance(fn, ast.NamedExpr):
            fn = fn.value
            continue
        p_call: Optional[ast.Call] = None
        if isinstance(fn, ast.Call) and _is_partial_factory(fn.func, state, scope):
            p_call = fn
        elif isinstance(fn, ast.Name) and fn.id in scope.var_nodes:
            v_node = scope.var_nodes[fn.id]
            if isinstance(v_node, ast.Call) and _is_partial_factory(v_node.func, state, scope):
                p_call = v_node
        if p_call is not None and p_call.args and not isinstance(p_call.args[0], ast.Starred):
            fn = p_call.args[0]
            eff_args = list(p_call.args[1:]) + eff_args
            p_kw = {kw.arg: kw.value for kw in p_call.keywords if kw.arg is not None}
            p_kw.update(eff_kw)
            eff_kw = p_kw
            continue
        break
    return _unwrap_single_callable(fn, state, scope), eff_args, eff_kw


def _register_class_def(stmt: ast.ClassDef, scope: _ScopeState) -> None:
    """Enregistre les attributs et méthodes de classe dans `scope` pour résolution statique."""
    attrs = scope.class_attrs.setdefault(stmt.name, {})
    methods = scope.class_methods.setdefault(stmt.name, {})
    for item in stmt.body:
        if isinstance(item, ast.Assign):
            for tgt in item.targets:
                if isinstance(tgt, ast.Name):
                    attrs[tgt.id] = item.value
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value is not None:
            attrs[item.target.id] = item.value
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.setdefault(item.name, []).append(item)


def _resolve_user_callable(
    fn: ast.AST, eff_args: List[ast.AST], scope: _ScopeState, state: _PyAstState
) -> Optional[Tuple[List[ast.AST], List[ast.AST]]]:
    """Résout une fonction/lambda/méthode utilisateur vers `(callables, pos_args)`."""
    if isinstance(fn, ast.Lambda):
        return [fn], eff_args
    if isinstance(fn, ast.Name):
        fn_list = scope.func_defs.get(fn.id) or state.imp.func_defs.get(fn.id)
        if fn_list:
            return list(fn_list), eff_args
    if isinstance(fn, ast.Attribute):
        recv = fn.value
        if isinstance(recv, ast.Name) and recv.id in scope.class_methods:
            m_list = scope.class_methods[recv.id].get(fn.attr)
            if m_list:
                return list(m_list), eff_args
        cls_n: Optional[str] = None
        if isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name) and recv.func.id in scope.class_methods:
            cls_n = recv.func.id
        elif isinstance(recv, ast.Name) and recv.id in scope.var_class_inst:
            cls_n = scope.var_class_inst[recv.id]
        if cls_n and cls_n in scope.class_methods:
            m_list = scope.class_methods[cls_n].get(fn.attr)
            if m_list:
                return list(m_list), [recv] + eff_args
    return None


def _inspect_user_callable(
    fn_cands: List[ast.AST],
    eff_args: List[ast.AST],
    eff_kw_map: Dict[str, ast.AST],
    state: _PyAstState,
    scope: _ScopeState,
    walk_stmts_fn: Callable[[_PyAstState, _ScopeState, List[ast.stmt]], Optional[Tuple[str, str]]],
) -> Optional[Tuple[str, str]]:
    """Inspecte le corps d'une fonction/lambda/méthode utilisateur avec les arguments liés au site d'appel."""
    if state.call_depth >= 4:
        return None
    kw_nodes = [ast.keyword(arg=k, value=v) for k, v in eff_kw_map.items()]
    for fn_cand in fn_cands:
        resolved = _resolve_user_callable(fn_cand, eff_args, scope, state)
        if resolved is None:
            continue
        callables, pos_args = resolved
        syn_call = ast.Call(func=fn_cand, args=pos_args, keywords=kw_nodes)
        for fd in callables:
            fd_id = id(fd)
            if fd_id in state.active_call_fns:
                continue
            fn_scope = _bind_call_params_scope(syn_call, fd, state, scope)
            if fn_scope is None:
                continue
            body: List[ast.stmt] = [ast.Expr(value=fd.body)] if isinstance(fd, ast.Lambda) else list(getattr(fd, "body", ()))
            state.call_depth += 1
            state.active_call_fns.add(fd_id)
            try:
                verdict = walk_stmts_fn(state, fn_scope, body)
                if verdict is not None:
                    return verdict
            finally:
                state.active_call_fns.discard(fd_id)
                state.call_depth -= 1
    return None


def _taint_bytes_var(state: _PyAstState, scope: _ScopeState, var_name: str) -> None:
    scope.cur_bytes.pop(var_name, None)
    scope.cur_str.pop(var_name, None)
    scope.var_nodes.pop(var_name, None)
    scope.incomplete_vars.add(var_name)
    scope.obf_tainted.add(var_name)
    state.dynamic_tainted_vars.add(var_name)


def _record_mutated_bytes(state: _PyAstState, scope: _ScopeState, var_name: str, new_b: bytes) -> None:
    scope.cur_bytes[var_name] = new_b
    state.record_alt_bytes(var_name, new_b)
    state.record_alt_str(var_name, _decode_bytes_text(new_b))


def _mutate_bytes_subscript(
    state: _PyAstState, scope: _ScopeState, var_name: str, tgt: ast.Subscript, rhs: ast.AST
) -> bool:
    """Met à jour ou invalide/taint un `bytearray` muté par affectation d'indice/slice (`b[0] = ...`)."""
    if var_name not in scope.cur_bytes and var_name not in state.alt_bytes:
        return False
    scope.var_nodes.pop(var_name, None)
    if isinstance(tgt.value, ast.Name):
        state.mutator_receiver_names.add(tgt.value)
    state.var_contributors.setdefault(var_name, []).append(rhs)
    if var_name in scope.cur_bytes and not _expr_has_tainted_name(rhs, state, scope):
        sl_node = _ast_subscript_slice(tgt)
        ba = bytearray(scope.cur_bytes[var_name])
        if isinstance(sl_node, ast.Slice):
            sl = _eval_slice_bounds(sl_node)
            rhs_b = _eval_static_bytes(
                rhs, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns,
                scope.base64_mods, scope.base64_fns, scope.cur_bytes, var_nodes=scope.var_nodes,
            )
            if sl is not None and rhs_b is not None:
                try:
                    ba[sl] = rhs_b
                    _record_mutated_bytes(state, scope, var_name, bytes(ba))
                    return True
                except (ValueError, IndexError, TypeError):
                    pass
        else:
            idx = _eval_static_int(sl_node)
            val = _eval_static_int(rhs)
            if idx is not None and val is not None and 0 <= val <= 255:
                try:
                    ba[idx] = val
                    _record_mutated_bytes(state, scope, var_name, bytes(ba))
                    return True
                except IndexError:
                    pass
    _taint_bytes_var(state, scope, var_name)
    return True


def _mutate_bytes_augassign(
    state: _PyAstState, scope: _ScopeState, var_name: str, rhs: ast.AST
) -> None:
    """Met à jour ou taint un `bytearray` muté par `b += ...`."""
    rhs_b = _eval_static_bytes(
        rhs, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns,
        scope.base64_mods, scope.base64_fns, scope.cur_bytes, var_nodes=scope.var_nodes,
    )
    if rhs_b is not None and not _expr_has_tainted_name(rhs, state, scope):
        _record_mutated_bytes(state, scope, var_name, scope.cur_bytes[var_name] + rhs_b)
    else:
        _taint_bytes_var(state, scope, var_name)


def _mutate_bytes_method(state: _PyAstState, scope: _ScopeState, call: ast.Call) -> bool:
    """Met à jour ou invalide/taint un `bytearray` muté par `.extend`/`.append`/`.insert`/`.clear`/`.pop`/`.reverse`."""
    if not (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)):
        return False
    var_name, attr = call.func.value.id, call.func.attr
    if attr not in ("append", "extend", "insert", "clear", "pop", "reverse", "remove"):
        return False
    if var_name not in scope.cur_bytes and var_name not in state.alt_bytes:
        return False
    scope.var_nodes.pop(var_name, None)
    state.mutator_receiver_names.add(call.func.value)
    for arg in call.args:
        state.var_contributors.setdefault(var_name, []).append(arg)
    if var_name in scope.cur_bytes and not call.keywords and all(not _expr_has_tainted_name(a, state, scope) for a in call.args):
        ba = bytearray(scope.cur_bytes[var_name])
        try:
            if attr == "clear" and not call.args:
                ba.clear(); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "reverse" and not call.args:
                ba.reverse(); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "pop" and len(call.args) <= 1:
                idx = _eval_static_int(call.args[0]) if call.args else -1
                if idx is not None:
                    ba.pop(idx); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "append" and len(call.args) == 1:
                iv = _eval_static_int(call.args[0])
                if iv is not None and 0 <= iv <= 255:
                    ba.append(iv); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "remove" and len(call.args) == 1:
                iv = _eval_static_int(call.args[0])
                if iv is not None and 0 <= iv <= 255:
                    ba.remove(iv); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "insert" and len(call.args) == 2:
                idx, iv = _eval_static_int(call.args[0]), _eval_static_int(call.args[1])
                if idx is not None and iv is not None and 0 <= iv <= 255:
                    ba.insert(idx, iv); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "extend" and len(call.args) == 1:
                ext_b = _eval_static_bytes(
                    call.args[0], scope.cur_str, scope.cur_seq, 0, state.imp.func_returns,
                    scope.base64_mods, scope.base64_fns, scope.cur_bytes, var_nodes=scope.var_nodes,
                )
                if ext_b is None:
                    ext_ints = _eval_static_int_list(call.args[0], scope.cur_str, scope.var_nodes, scope.cur_bytes)
                    if ext_ints is not None and all(0 <= iv <= 255 for iv in ext_ints):
                        ext_b = bytes(ext_ints)
                if ext_b is not None:
                    ba.extend(ext_b); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
        except (IndexError, ValueError, TypeError):
            pass
    _taint_bytes_var(state, scope, var_name)
    return True
