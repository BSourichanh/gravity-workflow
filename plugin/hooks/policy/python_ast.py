#!/usr/bin/env python3
"""Analyse AST Python (passes 1/2, flux séquentiels, re-exports) pour `python -c` et `run_workflow`."""

import ast
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .constants import SENSITIVE_PATH_PATTERNS
from .paths import is_harness_protected, matches_any
from .python_ast_calls import _inspect_python_call
from .python_ast_cands import (
    _eval_code_arg_cands,
    _eval_scope_seq_cands,
    _eval_scope_str_cands,
    _eval_static_str_candidates,
    _expr_has_obfuscated_decode,
    _match_dict_lookup_key,
)
from .python_ast_eval import (
    _PyAstState,
    _PyImports,
    _ScopeState,
    _ast_subscript_slice,
    _collect_const_str_vars,
    _collect_static_bindings,
    _eval_static_int,
    _eval_static_seq,
    _eval_static_str,
    _is_named_mod_expr,
)
from .python_ast_flow import (
    _inspect_user_callable,
    _mutate_bytes_augassign,
    _mutate_bytes_method,
    _mutate_bytes_subscript,
    _record_setattr_call,
    _register_class_def,
    _taint_bytes_var,
    _unwrap_call_target,
)
from .python_ast_imports import (
    _bind_var,
    _collect_python_imports,
    _eval_func_call_code_cands,
    _is_os_dict_expr,
    _merge_scope_branch,
    _resolve_builtins_lookup,
    _resolve_dynamic_import_module,
    _unwrap_attr_candidates,
)
from .python_ast_readonly import (
    _build_parent_map,
    _classify_readonly_call,
    _collect_target_var_names,
    _eval_static_seq_candidates,
    _is_open_call_readonly,
    _is_readonly_harness_usage,
    _iter_stmt_expr_nodes,
    _mark_benign_cmd_node,
    _mark_subtree,
    _raw_code_hard_deny_check,
    _raw_code_has_harness_mutation_risk,
    _resolve_unambiguous_open_modes,
    _split_top_level_call_args,
)


def _iter_stmt_calls_postorder(stmt: ast.AST) -> List[ast.Call]:
    """Retourne les appels `ast.Call` directs d'une instruction en post-ordre (enfants avant parent)."""
    out: List[ast.Call] = []
    skip_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def _visit(node: ast.AST) -> None:
        for field_name, field_val in ast.iter_fields(node):
            if node is stmt and field_name in ("body", "orelse", "handlers", "finalbody"):
                continue
            if isinstance(field_val, ast.AST) and not isinstance(field_val, skip_types):
                _visit(field_val)
            elif isinstance(field_val, list):
                for item in field_val:
                    if isinstance(item, ast.AST) and not isinstance(item, skip_types):
                        _visit(item)
        if isinstance(node, ast.Call):
            out.append(node)

    _visit(stmt)
    return out


def _walk_python_stmts(
    state: _PyAstState, scope: _ScopeState, stmts: List[ast.stmt]
) -> Optional[Tuple[str, str]]:
    """Parcourt les instructions séquentiellement en propageant les bindings, classes, mutations et branches `if`/`try`."""
    for stmt in stmts:
        expr_nodes = _iter_stmt_expr_nodes(stmt)
        for enode in expr_nodes:
            if isinstance(enode, ast.NamedExpr) and isinstance(enode.target, ast.Name):
                _bind_var(state, scope, enode.target.id, enode.value)
        for call in _iter_stmt_calls_postorder(stmt):
            _mutate_bytes_method(state, scope, call)
            _record_setattr_call(state, scope, call)
            verdict = _inspect_python_call(state, scope, call, _inspect_python_ast, _walk_python_stmts)
            if verdict is not None:
                return verdict
        for enode in expr_nodes:
            if isinstance(enode, ast.Subscript):
                for env_k in ("environ", "environb"):
                    env_recv = _match_dict_lookup_key(enode, state, scope, env_k)
                    if env_recv is not None and _is_os_dict_expr(env_recv, state, scope):
                        return "deny", f"Accès indirect à `{env_k}` dans `{state.context_label}` bloqué."
            if isinstance(enode, (ast.Name, ast.BinOp, ast.JoinedStr, ast.Call, ast.Subscript)):
                if isinstance(enode, ast.Name) and not isinstance(enode.ctx, ast.Load):
                    continue
                for s_val in _eval_scope_str_cands(enode, state, scope):
                    if s_val:
                        state.add_resolved_str(enode, s_val)

        if isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    _bind_var(state, scope, tgt.id, stmt.value)
                elif (
                    isinstance(tgt, (ast.Tuple, ast.List))
                    and isinstance(stmt.value, (ast.Tuple, ast.List))
                    and len(tgt.elts) == len(stmt.value.elts)
                ):
                    for t_elt, v_elt in zip(tgt.elts, stmt.value.elts):
                        if isinstance(t_elt, ast.Name):
                            _bind_var(state, scope, t_elt.id, v_elt)
                elif isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                    var_name = tgt.value.id
                    if _mutate_bytes_subscript(state, scope, var_name, tgt, stmt.value):
                        continue
                    scope.var_nodes.pop(var_name, None)
                    if var_name in scope.cur_seq:
                        idx_val = _eval_static_int(_ast_subscript_slice(tgt))
                        if idx_val is not None:
                            state.mutator_receiver_names.add(tgt.value)
                            state.var_contributors.setdefault(var_name, []).append(stmt.value)
                            new_val = _eval_static_str(stmt.value, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns)
                            try:
                                scope.cur_seq[var_name][idx_val] = new_val
                                state.record_alt_seq(var_name, scope.cur_seq[var_name])
                            except IndexError:
                                pass
        elif isinstance(stmt, ast.Delete):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                    vn = tgt.value.id
                    if vn in scope.cur_bytes or vn in state.alt_bytes or vn in scope.bytes_alias_peers:
                        _taint_bytes_var(state, scope, vn)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            _bind_var(state, scope, stmt.target.id, stmt.value)
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            var_name = stmt.target.id
            scope.var_nodes.pop(var_name, None)
            scope.cur_ints.pop(var_name, None)
            state.var_contributors.setdefault(var_name, []).append(stmt.value)
            if isinstance(stmt.op, ast.Add):
                if var_name in scope.cur_bytes or var_name in scope.bytes_alias_peers:
                    _mutate_bytes_augassign(state, scope, var_name, stmt.value)
                elif var_name in scope.cur_str:
                    rhs_s = _eval_static_str(stmt.value, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns)
                    if rhs_s is not None:
                        scope.cur_str[var_name] = scope.cur_str[var_name] + rhs_s
                        state.record_alt_str(var_name, scope.cur_str[var_name])
                    else:
                        scope.cur_str.pop(var_name, None)
                        scope.incomplete_vars.add(var_name)
                        if _expr_has_obfuscated_decode(stmt.value, state, scope) is not None:
                            scope.obf_tainted.add(var_name)
                        state.dynamic_tainted_vars.add(var_name)
                elif var_name in scope.cur_seq:
                    rhs_seq = _eval_static_seq(
                        stmt.value, scope.cur_str, scope.cur_seq, 0,
                        state.imp.shlex_mod_names, state.imp.shlex_split_names, state.imp.func_returns,
                    )
                    scope.cur_seq[var_name] = scope.cur_seq[var_name] + (rhs_seq if rhs_seq is not None else [None])
                    state.record_alt_seq(var_name, scope.cur_seq[var_name])
            elif var_name in scope.cur_bytes or var_name in state.alt_bytes or var_name in scope.bytes_alias_peers:
                _taint_bytes_var(state, scope, var_name)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                target_var = call.func.value.id
                if call.func.attr in ("append", "extend", "insert", "pop", "clear", "remove", "reverse"):
                    scope.var_nodes.pop(target_var, None)
                if target_var in scope.cur_seq:
                    if call.func.attr == "append" and len(call.args) == 1:
                        state.mutator_receiver_names.add(call.func.value)
                        state.var_contributors.setdefault(target_var, []).append(call.args[0])
                        scope.cur_seq[target_var].append(_eval_static_str(call.args[0], scope.cur_str, scope.cur_seq, 0, state.imp.func_returns))
                        state.record_alt_seq(target_var, scope.cur_seq[target_var])
                    elif call.func.attr == "extend" and len(call.args) == 1:
                        state.mutator_receiver_names.add(call.func.value)
                        state.var_contributors.setdefault(target_var, []).append(call.args[0])
                        ext_vals = _eval_static_seq(
                            call.args[0], scope.cur_str, scope.cur_seq, 0,
                            state.imp.shlex_mod_names, state.imp.shlex_split_names, state.imp.func_returns,
                        )
                        scope.cur_seq[target_var].extend(ext_vals) if ext_vals is not None else scope.cur_seq[target_var].append(None)
                        state.record_alt_seq(target_var, scope.cur_seq[target_var])
                    elif call.func.attr == "insert" and len(call.args) == 2:
                        idx_val = _eval_static_int(call.args[0])
                        if idx_val is not None:
                            state.mutator_receiver_names.add(call.func.value)
                            state.var_contributors.setdefault(target_var, []).append(call.args[1])
                            scope.cur_seq[target_var].insert(idx_val, _eval_static_str(call.args[1], scope.cur_str, scope.cur_seq, 0, state.imp.func_returns))
                            state.record_alt_seq(target_var, scope.cur_seq[target_var])

        if isinstance(stmt, ast.ClassDef):
            _register_class_def(stmt, scope)
            verdict = _walk_python_stmts(state, scope.clone(), stmt.body)
            if verdict is not None:
                return verdict
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            verdict = _walk_python_stmts(state, scope.clone(), stmt.body)
            if verdict is not None:
                return verdict
        elif isinstance(stmt, (ast.If, ast.Try)):
            branch_lists: List[List[ast.stmt]] = []
            if stmt.body:
                branch_lists.append(list(stmt.body))
            if getattr(stmt, "orelse", None):
                branch_lists.append(list(stmt.orelse))
            for handler in getattr(stmt, "handlers", None) or []:
                if getattr(handler, "body", None):
                    branch_lists.append(list(handler.body))
            base_scope = scope.clone()
            for b_stmts in branch_lists:
                b_scope = base_scope.clone()
                verdict = _walk_python_stmts(state, b_scope, b_stmts)
                if verdict is not None:
                    return verdict
                _merge_scope_branch(scope, b_scope, state)
            final_stmts = getattr(stmt, "finalbody", None)
            if isinstance(final_stmts, list) and final_stmts:
                verdict = _walk_python_stmts(state, scope, final_stmts)
                if verdict is not None:
                    return verdict
        else:
            for attr in ("body", "orelse", "finalbody"):
                sub_stmts = getattr(stmt, attr, None)
                if isinstance(sub_stmts, list) and sub_stmts:
                    verdict = _walk_python_stmts(state, scope, sub_stmts)
                    if verdict is not None:
                        return verdict
    return None


def _is_pass2_env_dict(val_node: ast.AST, state: _PyAstState) -> bool:
    return (
        (isinstance(val_node, ast.Attribute) and val_node.attr == "__dict__")
        or (isinstance(val_node, ast.Call) and isinstance(val_node.func, ast.Name) and val_node.func.id == "vars")
        or (isinstance(val_node, ast.Name) and val_node.id in state.env_dict_vars)
    )


def _inspect_pass2_ast_nodes(state: _PyAstState, tree: ast.Module) -> Tuple[str, Optional[str]]:
    """Passe 2 AST : accès `environ`, chemins sensibles et auto-protection du harnais actif."""
    other_loaded_vars: Set[str] = set()
    var_loads: Dict[str, List[ast.Name]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            var_loads.setdefault(node.id, []).append(node)
            if node not in state.benign_cmd_name_nodes and node not in state.mutator_receiver_names:
                other_loaded_vars.add(node.id)
    for cmd_name_node in state.benign_cmd_name_nodes:
        if cmd_name_node.id not in other_loaded_vars:
            state.checked_cmd_nodes.add(cmd_name_node)
            for contrib in state.var_contributors.get(cmd_name_node.id, ()):
                _mark_subtree(contrib, state.checked_cmd_nodes)

    parent_map: Optional[Dict[ast.AST, ast.AST]] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "environb"):
            return "deny", f"Accès à `environ` dans `{state.context_label}` bloqué."
        if isinstance(node, ast.Subscript) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
        ):
            key_node = (
                _ast_subscript_slice(node)
                if isinstance(node, ast.Subscript)
                else (node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "key"), None))
            )
            if key_node is not None:
                key_cands = list(state.resolved_expr_strs.get(key_node, ()))
                fallback_key = _eval_static_str(
                    key_node, state.const_vars, state.const_seq_vars, 0, state.imp.func_returns
                )
                if fallback_key and fallback_key not in key_cands:
                    key_cands.append(fallback_key)
                if any(k in ("environ", "environb") for k in key_cands):
                    recv = node.value if isinstance(node, ast.Subscript) else node.func.value  # type: ignore[union-attr]
                    if _is_pass2_env_dict(recv, state):
                        return "deny", f"Accès indirect à `environ` dans `{state.context_label}` bloqué."
        if node in state.checked_cmd_nodes or node in state.exempt_regex_nodes:
            continue
        if isinstance(node, (ast.Constant, ast.Name, ast.BinOp, ast.JoinedStr, ast.Call, ast.Subscript)):
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                continue
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                continue
            vals = list(state.resolved_expr_strs.get(node, ()))
            fallback_val = _eval_static_str(
                node, state.const_vars, state.const_seq_vars, 0, state.imp.func_returns
            )
            if fallback_val and fallback_val not in vals:
                vals.append(fallback_val)
            for val in vals:
                if matches_any(val, SENSITIVE_PATH_PATTERNS):
                    return "deny", f"Script `{state.context_label}` ciblant un chemin sensible bloqué."
                if is_harness_protected(val, state.ws_root):
                    if parent_map is None:
                        parent_map = _build_parent_map(tree)
                    if not _is_readonly_harness_usage(node, parent_map, var_loads, state):
                        return "deny", f"Script `{state.context_label}` ciblant les garde-fous Gravity bloqué."

    return "allow", None


def _inspect_python_ast(
    code: str,
    ws_root: Optional[Path] = None,
    *,
    context_label: str = "python -c",
    allow_top_level_await: bool = False,
    _depth: int = 0,
) -> Tuple[str, Optional[str]]:
    """Détecteur AST chirurgical focalisé sur les menaces `deny` (secrets, `os.environ`, harnais actif, rmtree/subprocess destructeurs)."""
    try:
        if allow_top_level_await:
            flags = getattr(ast, "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0)
            tree = (
                ast.parse(code, mode="exec", type_comments=False)
                if not flags
                else compile(code, f"<{context_label}>", "exec", flags=ast.PyCF_ONLY_AST | flags)
            )
        else:
            tree = ast.parse(code, mode="exec", type_comments=False)
    except (SyntaxError, ValueError, TypeError):
        raw_deny = _raw_code_hard_deny_check(code, ws_root, context_label)
        if raw_deny is not None:
            return raw_deny
        if context_label == "run_workflow":
            return (
                "force_ask",
                "Script `run_workflow` non analysable par AST : validation humaine requise.",
            )
        return "allow", None

    early_verdict, imp = _collect_python_imports(tree, context_label)
    if early_verdict is not None:
        return early_verdict

    const_vars, const_seq_vars, single_use_defs = _collect_static_bindings(
        tree, imp.shlex_mod_names, imp.shlex_split_names
    )
    state = _PyAstState(
        ws_root=ws_root,
        context_label=context_label,
        allow_top_level_await=allow_top_level_await,
        depth=_depth,
        imp=imp,
        const_vars=const_vars,
        const_seq_vars=const_seq_vars,
        single_use_defs=single_use_defs,
        fn_call_cb=_eval_func_call_code_cands,
    )
    for sub in ast.walk(tree):
        if isinstance(sub, ast.arg):
            state.dynamic_tainted_vars.add(sub.arg)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            state.dynamic_tainted_vars.add(sub.name)
        elif isinstance(sub, (ast.For, ast.AsyncFor, ast.comprehension)):
            for nm in _collect_target_var_names(sub.target) or ():
                state.dynamic_tainted_vars.add(nm)
        elif isinstance(sub, ast.withitem) and sub.optional_vars is not None:
            for nm in _collect_target_var_names(sub.optional_vars) or ():
                state.dynamic_tainted_vars.add(nm)
    scope = _ScopeState(
        cur_str=dict(const_vars),
        cur_seq={k: list(v) for k, v in const_seq_vars.items()},
        subproc_mods=set(imp.subprocess_mod_names),
        os_mods=set(imp.os_mod_names),
        rmtree_fns=set(imp.rmtree_func_names),
        os_shell_fns=set(imp.os_shell_func_names),
        subproc_fns=set(imp.subprocess_func_names),
        base64_mods=set(imp.base64_mod_names),
        base64_fns=dict(imp.base64_decode_fns),
        codecs_mods=set(imp.codecs_mod_names),
        codecs_fns=dict(imp.codecs_decode_fns),
        sys_mods=set(imp.sys_mod_names),
        functools_mods=set(imp.functools_mod_names),
        partial_fns=set(imp.partial_func_names),
        operator_mods=set(imp.operator_mod_names),
        attrgetter_fns=set(imp.attrgetter_func_names),
        methodcaller_fns=set(imp.methodcaller_func_names),
        builtins_mods=set(imp.builtins_mod_names),
        eval_exec_fns=dict(imp.eval_exec_fns),
        func_defs={k: list(v) for k, v in imp.func_defs.items()},
    )
    pass1_verdict = _walk_python_stmts(state, scope, list(tree.body))
    if pass1_verdict is not None:
        return pass1_verdict
    return _inspect_pass2_ast_nodes(state, tree)


def inspect_inline_python_code(
    code: str, ws_root: Optional[Path] = None, _depth: int = 0
) -> Tuple[str, Optional[str]]:
    """Inspecte un script `python -c` pour menaces `deny` (secrets, `os.environ`, harnais actif, rmtree/subprocess destructeurs)."""
    return _inspect_python_ast(
        code,
        ws_root,
        context_label="python -c",
        allow_top_level_await=False,
        _depth=_depth,
    )
