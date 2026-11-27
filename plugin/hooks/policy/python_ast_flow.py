#!/usr/bin/env python3
"""Résolution des appels indirects (`partial`, `attrgetter`, `methodcaller`, `__call__`, `setattr`, lambdas/helpers) et suivi de mutation `bytearray`."""

import ast
from typing import Callable, Dict, List, Optional, Set, Tuple

from .python_ast_cands import (
    _eval_scope_str_cands,
    _expr_has_obfuscated_decode,
    _lookup_dict_like_value,
)
from .python_ast_eval import (
    _eval_slice_bounds,
    _eval_static_bytes,
    _eval_static_int,
    _eval_static_int_list,
)
from .python_ast_imports import (
    _bind_call_params_scope,
    _bind_var,
    _match_methodcaller_factory,
    _resolve_builtins_lookup,
    _unwrap_attr_candidates,
)
from .python_ast_state import (
    _PyAstState,
    _ScopeState,
    _ast_subscript_slice,
    _decode_bytes_text,
    _expr_has_tainted_name,
    _resolve_builtin_ctor_name,
)

_BYTEARRAY_MUTATORS = frozenset({"append", "extend", "insert", "clear", "pop", "reverse", "remove"})


def _subst_scope_var_nodes(
    node: ast.AST, loc: _ScopeState, _depth: int = 0, _bound: Optional[Set[str]] = None
) -> ast.AST:
    """Substitue les variables liées dans `loc.var_nodes` au sein d'une expression (y compris attributs et lambdas imbriquées)."""
    if _depth > 6:
        return node
    bound = _bound or set()
    rec = lambda n, b=bound: _subst_scope_var_nodes(n, loc, _depth + 1, b)
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if node.id not in bound and node.id in loc.var_nodes and node.id in loc.shadowed_alts:
            return rec(loc.var_nodes[node.id], bound | {node.id})
        return node
    if isinstance(node, ast.Attribute):
        nv = rec(node.value)
        return ast.Attribute(value=nv, attr=node.attr, ctx=node.ctx) if nv is not node.value else node  # type: ignore[arg-type]
    if isinstance(node, ast.Subscript):
        nv = rec(node.value)
        ns = rec(node.slice)
        return ast.Subscript(value=nv, slice=ns, ctx=node.ctx) if (nv is not node.value or ns is not node.slice) else node  # type: ignore[arg-type]
    if isinstance(node, ast.Call):
        nf = rec(node.func)
        na = [rec(a) for a in node.args]
        nkw = [ast.keyword(arg=k.arg, value=rec(k.value)) for k in node.keywords]  # type: ignore[arg-type]
        return ast.Call(func=nf, args=na, keywords=nkw)  # type: ignore[arg-type]
    if isinstance(node, ast.Lambda):
        l_bound = bound | {a.arg for a in node.args.posonlyargs} | {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        if node.args.vararg:
            l_bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            l_bound.add(node.args.kwarg.arg)
        nb = rec(node.body, l_bound)
        return ast.Lambda(args=node.args, body=nb) if nb is not node.body else node  # type: ignore[arg-type]
    if isinstance(node, (ast.List, ast.Tuple)):
        elts = [rec(e) for e in node.elts]
        return type(node)(elts=elts, ctx=node.ctx)  # type: ignore[arg-type]
    return node


def _unwrap_single_callable(
    expr: ast.AST, state: _PyAstState, scope: _ScopeState, _depth: int = 0
) -> List[ast.AST]:
    """Développe attributs dynamiques, conteneurs (`dict()`, `[]`) et lambdas/helpers/currying sur un callable."""
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
        hits = _lookup_dict_like_value(cont, _eval_scope_str_cands(sl, state, scope), state, scope)
        if hits:
            out = []
            for h in hits:
                out.extend(_unwrap_single_callable(h, state, scope, _depth + 1))
            return out[:6]
    elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "get":
        k_arg = expr.args[0] if expr.args else next((kw.value for kw in expr.keywords if kw.arg == "key"), None)
        if k_arg is not None:
            hits = _lookup_dict_like_value(expr.func.value, _eval_scope_str_cands(k_arg, state, scope), state, scope)
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
                else _unwrap_single_callable(expr.func, state, scope, _depth + 1)
            )
        )
        fn_list = [f for f in fn_list if isinstance(f, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef))]
        if fn_list:
            out = []
            for fd in fn_list:
                loc = _bind_call_params_scope(expr, fd, state, scope)
                if loc is None:
                    continue
                if isinstance(fd, ast.Lambda):
                    subst = _subst_scope_var_nodes(fd.body, loc)
                    out.extend(_unwrap_single_callable(subst, state, loc, _depth + 1))
                else:
                    for st in getattr(fd, "body", ()):
                        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                            _bind_var(state, loc, st.targets[0].id, st.value, record_contrib=False)
                        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.value is not None:
                            _bind_var(state, loc, st.target.id, st.value, record_contrib=False)
                        elif isinstance(st, ast.Return) and st.value is not None:
                            subst = _subst_scope_var_nodes(st.value, loc)
                            out.extend(_unwrap_single_callable(subst, state, loc, _depth + 1))
            if out:
                return out[:6]
    return [expr]


def _expand_starred_eff_args(
    eff_args: List[ast.AST], state: _PyAstState, scope: _ScopeState
) -> List[ast.AST]:
    """Développe les arguments `Starred` statiquement résolubles dans `eff_args` (conserve les non résolubles)."""
    out: List[ast.AST] = []
    for a in eff_args:
        if isinstance(a, ast.Starred):
            v = scope.var_nodes.get(a.value.id, a.value) if isinstance(a.value, ast.Name) else a.value
            if isinstance(v, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in v.elts):
                out.extend(v.elts)
            elif isinstance(a.value, ast.Name) and a.value.id in scope.cur_seq and all(x is not None for x in scope.cur_seq[a.value.id]):
                out.extend(ast.Constant(value=x) for x in scope.cur_seq[a.value.id] if x is not None)
            else:
                out.append(a)
        else:
            out.append(a)
    return out


def _unwrap_call_target(
    node: ast.Call, state: _PyAstState, scope: _ScopeState
) -> Tuple[List[ast.AST], List[ast.AST], Dict[str, ast.AST]]:
    """Développe `functools.partial`, `operator.methodcaller`, `NamedExpr`, `operator.attrgetter`, `__getattribute__`, `dict()`, classes et lambdas."""
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
        mc = _match_methodcaller_factory(fn, state, scope)
        if mc is not None and eff_args and not isinstance(eff_args[0], ast.Starred):
            m_name_node, mc_args, mc_kw = mc
            fn = ast.Call(func=ast.Name(id="getattr", ctx=ast.Load()), args=[eff_args[0], m_name_node], keywords=[])
            eff_args = list(mc_args)
            eff_kw = dict(mc_kw)
            continue
        break
    return _unwrap_single_callable(fn, state, scope), _expand_starred_eff_args(eff_args, state, scope), eff_kw


def _is_partial_factory(fn: ast.AST, state: _PyAstState, scope: _ScopeState) -> bool:
    from .python_ast_imports import _is_partial_factory as _imp_partial

    return _imp_partial(fn, state, scope)


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


def _record_setattr_call(state: _PyAstState, scope: _ScopeState, call: ast.Call) -> None:
    """Enregistre `setattr(cls_or_inst, attr, val)` dans `scope.class_attrs` pour résolution statique."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "setattr"):
        return
    if call.func.id in state.imp.shadowed_names:
        return
    kw = {k.arg: k.value for k in call.keywords if k.arg is not None}
    obj_n = call.args[0] if len(call.args) >= 1 else kw.get("obj")
    attr_n = call.args[1] if len(call.args) >= 2 else kw.get("name")
    val_n = call.args[2] if len(call.args) >= 3 else kw.get("value")
    if not (isinstance(obj_n, ast.Name) and attr_n is not None and val_n is not None):
        return
    cls_n = (
        obj_n.id
        if (obj_n.id in scope.class_attrs or obj_n.id in scope.class_methods)
        else scope.var_class_inst.get(obj_n.id)
    )
    if cls_n is None:
        return
    resolved_val = scope.var_nodes.get(val_n.id, val_n) if isinstance(val_n, ast.Name) else val_n
    attrs = scope.class_attrs.setdefault(cls_n, {})
    for a_name in _eval_scope_str_cands(attr_n, state, scope):
        if a_name:
            attrs[a_name] = resolved_val


def _resolve_user_callable(
    fn: ast.AST, eff_args: List[ast.AST], scope: _ScopeState, state: _PyAstState
) -> Optional[Tuple[List[ast.AST], List[ast.AST]]]:
    """Résout une fonction/lambda/méthode/`__call__` utilisateur vers `(callables, pos_args)`."""
    if isinstance(fn, ast.Lambda):
        return [fn], eff_args
    if isinstance(fn, ast.Name):
        fn_list = scope.func_defs.get(fn.id) or state.imp.func_defs.get(fn.id)
        if fn_list:
            return list(fn_list), eff_args
        if fn.id in scope.var_class_inst:
            cls_n = scope.var_class_inst[fn.id]
            m_list = scope.class_methods.get(cls_n, {}).get("__call__")
            if m_list:
                return list(m_list), [fn] + eff_args
    if isinstance(fn, ast.Call) and isinstance(fn.func, ast.Name) and fn.func.id in scope.class_methods:
        m_list = scope.class_methods[fn.func.id].get("__call__")
        if m_list:
            return list(m_list), [fn] + eff_args
    if isinstance(fn, ast.Attribute):
        recv = fn.value
        if isinstance(recv, ast.Name) and recv.id in scope.class_methods:
            m_list = scope.class_methods[recv.id].get(fn.attr)
            if m_list:
                return list(m_list), eff_args
            attr_val = scope.class_attrs.get(recv.id, {}).get(fn.attr)
            if isinstance(attr_val, ast.Lambda):
                return [attr_val], eff_args
        cls_n: Optional[str] = None
        if isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name) and recv.func.id in scope.class_methods:
            cls_n = recv.func.id
        elif isinstance(recv, ast.Name) and recv.id in scope.var_class_inst:
            cls_n = scope.var_class_inst[recv.id]
        if cls_n and cls_n in scope.class_methods:
            m_list = scope.class_methods[cls_n].get(fn.attr)
            if m_list:
                return list(m_list), [recv] + eff_args
            attr_val = scope.class_attrs.get(cls_n, {}).get(fn.attr)
            if isinstance(attr_val, ast.Lambda):
                return [attr_val], eff_args
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
                if any(isinstance(n, ast.Call) for n in ast.walk(fd)) and any(
                    _resolve_builtins_lookup(a, state, scope) is not None or _expr_has_obfuscated_decode(a, state, scope) is not None
                    for a in eff_args + list(eff_kw_map.values())
                ):
                    return "deny", f"Appel indirect non résolvable avec argument dynamique dans `{state.context_label}` bloqué."
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
    for alias in scope.bytes_alias_peers.get(var_name, {var_name}):
        scope.cur_bytes.pop(alias, None)
        scope.cur_str.pop(alias, None)
        scope.var_nodes.pop(alias, None)
        scope.incomplete_vars.add(alias)
        scope.obf_tainted.add(alias)
        state.dynamic_tainted_vars.add(alias)


def _record_mutated_bytes(state: _PyAstState, scope: _ScopeState, var_name: str, new_b: bytes) -> None:
    text = _decode_bytes_text(new_b)
    for alias in scope.bytes_alias_peers.get(var_name, {var_name}):
        scope.cur_bytes[alias] = new_b
        scope.var_nodes.pop(alias, None)
        state.record_alt_bytes(alias, new_b)
        state.record_alt_str(alias, text)


def _mutate_bytes_subscript(
    state: _PyAstState, scope: _ScopeState, var_name: str, tgt: ast.Subscript, rhs: ast.AST
) -> bool:
    """Met à jour ou invalide/taint un `bytearray` (et ses alias) muté par affectation d'indice/slice (`b[0] = ...`)."""
    if var_name not in scope.cur_bytes and var_name not in state.alt_bytes and var_name not in scope.bytes_alias_peers:
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
    """Met à jour ou taint un `bytearray` (et ses alias) muté par `b += ...`."""
    rhs_b = _eval_static_bytes(
        rhs, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns,
        scope.base64_mods, scope.base64_fns, scope.cur_bytes, var_nodes=scope.var_nodes,
    )
    if var_name in scope.cur_bytes and rhs_b is not None and not _expr_has_tainted_name(rhs, state, scope):
        _record_mutated_bytes(state, scope, var_name, scope.cur_bytes[var_name] + rhs_b)
    else:
        _taint_bytes_var(state, scope, var_name)


def _mutate_bytes_method(state: _PyAstState, scope: _ScopeState, call: ast.Call) -> bool:
    """Met à jour ou invalide/taint un `bytearray` (et ses alias) muté par `.extend`/`.append`/`.insert`/`.clear`/`.pop`/`.reverse` ou `bytearray.<mut>(b, ...)`."""
    fn_node = scope.var_nodes.get(call.func.id, call.func) if isinstance(call.func, ast.Name) else call.func
    if not isinstance(fn_node, ast.Attribute) or fn_node.attr not in _BYTEARRAY_MUTATORS:
        return False
    attr = fn_node.attr
    recv_node: Optional[ast.Name] = None
    m_args: List[ast.AST] = list(call.args)
    if isinstance(fn_node.value, ast.Name) and (
        fn_node.value.id in scope.cur_bytes or fn_node.value.id in state.alt_bytes or fn_node.value.id in scope.bytes_alias_peers
    ):
        recv_node = fn_node.value
    elif _resolve_builtin_ctor_name(fn_node.value, scope, scope.var_nodes) == "bytearray" and m_args and isinstance(m_args[0], ast.Name):
        cand_nm = m_args[0]
        if cand_nm.id in scope.cur_bytes or cand_nm.id in state.alt_bytes or cand_nm.id in scope.bytes_alias_peers:
            recv_node = cand_nm
            m_args = m_args[1:]
    if recv_node is None:
        return False
    var_name = recv_node.id
    scope.var_nodes.pop(var_name, None)
    state.mutator_receiver_names.add(recv_node)
    for arg in m_args:
        state.var_contributors.setdefault(var_name, []).append(arg)
    if var_name in scope.cur_bytes and not call.keywords and all(not _expr_has_tainted_name(a, state, scope) for a in m_args):
        ba = bytearray(scope.cur_bytes[var_name])
        try:
            if attr == "clear" and not m_args:
                ba.clear(); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "reverse" and not m_args:
                ba.reverse(); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "pop" and len(m_args) <= 1:
                idx = _eval_static_int(m_args[0]) if m_args else -1
                if idx is not None:
                    ba.pop(idx); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "append" and len(m_args) == 1:
                iv = _eval_static_int(m_args[0])
                if iv is not None and 0 <= iv <= 255:
                    ba.append(iv); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "remove" and len(m_args) == 1:
                iv = _eval_static_int(m_args[0])
                if iv is not None and 0 <= iv <= 255:
                    ba.remove(iv); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "insert" and len(m_args) == 2:
                idx, iv = _eval_static_int(m_args[0]), _eval_static_int(m_args[1])
                if idx is not None and iv is not None and 0 <= iv <= 255:
                    ba.insert(idx, iv); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
            if attr == "extend" and len(m_args) == 1:
                ext_b = _eval_static_bytes(
                    m_args[0], scope.cur_str, scope.cur_seq, 0, state.imp.func_returns,
                    scope.base64_mods, scope.base64_fns, scope.cur_bytes, var_nodes=scope.var_nodes,
                )
                if ext_b is None:
                    ext_ints = _eval_static_int_list(m_args[0], scope.cur_str, scope.var_nodes, scope.cur_bytes)
                    if ext_ints is not None and all(0 <= iv <= 255 for iv in ext_ints):
                        ext_b = bytes(ext_ints)
                if ext_b is not None:
                    ba.extend(ext_b); _record_mutated_bytes(state, scope, var_name, bytes(ba)); return True
        except (IndexError, ValueError, TypeError):
            pass
    _taint_bytes_var(state, scope, var_name)
    return True
