#!/usr/bin/env python3
"""Analyse AST Python (passes 1/2, appels OS/subprocess/rmtree/eval, flux indirects) pour `python -c` et `run_workflow`."""

import ast
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .constants import (
    MAX_SUBSHELL_DEPTH, OS_EXEC_LIST_FUNCS, OS_EXEC_VAR_FUNCS, OS_SHELL_FUNCS,
    OS_SPAWN_LIST_FUNCS, OS_SPAWN_VAR_FUNCS, RE_PATTERN_FUNCS,
    SENSITIVE_PATH_PATTERNS, SUBPROCESS_CALL_FUNCS,
)
from .paths import _is_active_plugin_root_path, _is_destructive_target, is_harness_protected, matches_any
from .python_ast_eval import (
    _PyAstState, _PyImports, _ScopeState, _ast_subscript_slice,
    _collect_const_str_vars, _collect_static_bindings, _eval_static_int,
    _eval_static_seq, _eval_static_str, _is_named_mod_expr,
)
from .python_ast_flow import (
    _inspect_user_callable, _mutate_bytes_augassign, _mutate_bytes_method,
    _mutate_bytes_subscript, _register_class_def, _taint_bytes_var, _unwrap_call_target,
)
from .python_ast_imports import (
    _bind_var, _collect_python_imports, _eval_code_arg_cands, _eval_scope_seq_cands,
    _eval_scope_str_cands, _eval_static_str_candidates, _expr_has_obfuscated_decode,
    _merge_scope_branch, _resolve_builtins_lookup, _resolve_dynamic_import_module,
    _unwrap_attr_candidates,
)
from .python_ast_readonly import (
    _build_parent_map, _classify_readonly_call, _collect_target_var_names,
    _eval_static_seq_candidates, _is_open_call_readonly, _is_readonly_harness_usage,
    _iter_stmt_expr_nodes, _mark_benign_cmd_node, _mark_subtree,
    _raw_code_hard_deny_check, _raw_code_has_harness_mutation_risk,
    _resolve_unambiguous_open_modes, _split_top_level_call_args,
)
from .shell_parser import _argv_to_shell_cmd


def _inspect_rmtree_call(
    state: _PyAstState,
    scope: _ScopeState,
    eff_args: List[ast.AST],
    fn: ast.AST,
    kw_map: Dict[str, ast.AST],
    resolved_dyn_attrs: List[str],
) -> Optional[Tuple[str, str]]:
    """Inspecte `shutil.rmtree(...)` (y compris alias, `getattr`, `partial`, `attrgetter`)."""
    is_rmtree = (
        (isinstance(fn, ast.Name) and fn.id in scope.rmtree_fns)
        or (isinstance(fn, ast.Attribute) and fn.attr == "rmtree")
        or ("rmtree" in resolved_dyn_attrs)
    )
    if not is_rmtree:
        return None
    target_node = eff_args[0] if eff_args else kw_map.get("path")
    if target_node is not None:
        for target_str in _eval_scope_str_cands(target_node, state, scope):
            if is_harness_protected(target_str, state.ws_root) or _is_active_plugin_root_path(
                target_str, state.ws_root
            ):
                return "deny", f"Suppression des garde-fous Gravity via `rmtree` dans `{state.context_label}` bloquée."
            if _is_destructive_target(target_str):
                return "deny", f"Suppression récursive destructrice `shutil.rmtree` dans `{state.context_label}` bloquée."
    return None


def _inspect_os_shell_call(
    state: _PyAstState,
    scope: _ScopeState,
    eff_args: List[ast.AST],
    fn: ast.AST,
    kw_map: Dict[str, ast.AST],
    resolved_dyn_attrs: List[str],
) -> Optional[Tuple[str, str]]:
    """Inspecte `os.system` / `os.popen`."""
    from .evaluator import _evaluate_shell_command

    is_os_shell = (
        (isinstance(fn, ast.Name) and fn.id in scope.os_shell_fns)
        or (isinstance(fn, ast.Attribute) and fn.attr in OS_SHELL_FUNCS)
        or any(a in OS_SHELL_FUNCS for a in resolved_dyn_attrs)
    )
    if not (is_os_shell and state.depth < MAX_SUBSHELL_DEPTH):
        return None
    shell_arg = eff_args[0] if eff_args else (kw_map.get("command") or kw_map.get("cmd"))
    if shell_arg is None:
        return None
    str_cands = _eval_scope_str_cands(shell_arg, state, scope)
    if str_cands:
        for cmd_str in str_cands:
            deny_reason, _ = _evaluate_shell_command(cmd_str, state.ws_root, _depth=state.depth + 1)
            if deny_reason:
                return "deny", deny_reason
        if state.call_depth == 0:
            _mark_benign_cmd_node(state, shell_arg)
    return None


def _inspect_subprocess_call(
    state: _PyAstState,
    scope: _ScopeState,
    eff_args: List[ast.AST],
    fn: ast.AST,
    kw_map: Dict[str, ast.AST],
    resolved_dyn_attrs: List[str],
    dyn_recv_mod: Optional[str],
) -> Optional[Tuple[str, str]]:
    """Inspecte `subprocess.{run,Popen,call,...}`."""
    from .evaluator import _evaluate_shell_command

    is_subproc = (
        (isinstance(fn, ast.Name) and fn.id in scope.subproc_fns)
        or (
            isinstance(fn, ast.Attribute)
            and fn.attr in SUBPROCESS_CALL_FUNCS
            and ((isinstance(fn.value, ast.Name) and fn.value.id in scope.subproc_mods) or dyn_recv_mod == "subprocess")
        )
        or any(a in SUBPROCESS_CALL_FUNCS for a in resolved_dyn_attrs)
    )
    if not (is_subproc and state.depth < MAX_SUBSHELL_DEPTH):
        return None
    cmd_node = eff_args[0] if eff_args else kw_map.get("args")
    if cmd_node is None:
        return None
    str_cands = _eval_scope_str_cands(cmd_node, state, scope)
    seq_cands = _eval_scope_seq_cands(cmd_node, state, scope)
    for cmd_str in str_cands:
        deny_reason, _ = _evaluate_shell_command(cmd_str, state.ws_root, _depth=state.depth + 1)
        if deny_reason:
            return "deny", deny_reason
    for seq_vals in seq_cands:
        if not seq_vals:
            continue
        argv = [val if val is not None else "__dynamic__" for val in seq_vals]
        deny_reason, _ = _evaluate_shell_command(_argv_to_shell_cmd(argv), state.ws_root, _depth=state.depth + 1)
        if deny_reason:
            return "deny", deny_reason
    if (str_cands or seq_cands) and state.call_depth == 0:
        if isinstance(cmd_node, (ast.List, ast.Tuple)):
            for elt in cmd_node.elts:
                if _eval_static_str(elt, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns) is not None:
                    _mark_subtree(elt, state.checked_cmd_nodes)
        elif isinstance(cmd_node, ast.Name):
            state.benign_cmd_name_nodes.add(cmd_node)
            if cmd_node.id in state.single_use_defs:
                def_rhs = state.single_use_defs[cmd_node.id]
                if isinstance(def_rhs, (ast.List, ast.Tuple)):
                    for elt in def_rhs.elts:
                        if _eval_static_str(elt, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns) is not None:
                            _mark_subtree(elt, state.checked_cmd_nodes)
                else:
                    _mark_subtree(def_rhs, state.checked_cmd_nodes)
                _mark_subtree(cmd_node, state.checked_cmd_nodes)
        elif isinstance(cmd_node, ast.Call) or str_cands:
            _mark_subtree(cmd_node, state.checked_cmd_nodes)
    return None


def _inspect_exec_spawn_call(
    state: _PyAstState,
    scope: _ScopeState,
    eff_args: List[ast.AST],
    fn: ast.AST,
    dyn_recv_mod: Optional[str],
) -> Optional[Tuple[str, str]]:
    """Inspecte `os.exec*` et `os.spawn*` (y compris `[exe_path] + argv[1:]` contre `argv[0]` usurpé)."""
    from .evaluator import _evaluate_shell_command

    if state.depth >= MAX_SUBSHELL_DEPTH:
        return None
    is_os_attr = isinstance(fn, ast.Attribute) and (
        (isinstance(fn.value, ast.Name) and fn.value.id in scope.os_mods)
        or dyn_recv_mod in ("os", "posix", "nt")
    )
    exec_list_hit = (is_os_attr and fn.attr in OS_EXEC_LIST_FUNCS) or (isinstance(fn, ast.Name) and fn.id in state.imp.os_exec_list_names)
    spawn_list_hit = (is_os_attr and fn.attr in OS_SPAWN_LIST_FUNCS) or (isinstance(fn, ast.Name) and fn.id in state.imp.os_spawn_list_names)
    exec_var_hit = (is_os_attr and fn.attr in OS_EXEC_VAR_FUNCS) or (isinstance(fn, ast.Name) and fn.id in state.imp.os_exec_var_names)
    spawn_var_hit = (is_os_attr and fn.attr in OS_SPAWN_VAR_FUNCS) or (isinstance(fn, ast.Name) and fn.id in state.imp.os_spawn_var_names)

    argv_candidates: List[List[str]] = []
    if (exec_list_hit and len(eff_args) >= 2) or (spawn_list_hit and len(eff_args) >= 3):
        exe_idx, seq_idx = (0, 1) if exec_list_hit else (1, 2)
        exe_cands = _eval_scope_str_cands(eff_args[exe_idx], state, scope)
        for seq_vals in _eval_scope_seq_cands(eff_args[seq_idx], state, scope):
            if seq_vals:
                argv = [v if v is not None else "__dynamic__" for v in seq_vals]
                argv_candidates.append(argv)
                for exe_s in exe_cands:
                    argv_candidates.append([exe_s] + argv[1:])
    elif (exec_var_hit and len(eff_args) >= 2) or (spawn_var_hit and len(eff_args) >= 3):
        exe_idx, arg_start = (0, 1) if exec_var_hit else (1, 2)
        exe_cands = _eval_scope_str_cands(eff_args[exe_idx], state, scope)
        argv = [
            _eval_static_str(e, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns) or "__dynamic__"
            for e in eff_args[arg_start:]
        ]
        argv_candidates.append(argv)
        for exe_s in exe_cands:
            argv_candidates.append([exe_s] + argv[1:])

    for argv in argv_candidates:
        deny_reason, _ = _evaluate_shell_command(_argv_to_shell_cmd(argv), state.ws_root, _depth=state.depth + 1)
        if deny_reason:
            return "deny", deny_reason
    return None


def _resolve_eval_exec_call_kind(
    fn: ast.AST, state: _PyAstState, scope: _ScopeState, resolved_dyn_attrs: List[str], is_re_call: bool
) -> Optional[str]:
    """Résout si `fn` correspond à `eval`/`exec`/`compile` (direct, alias, `builtins`, `getattr`, subscript, `.get`, `partial`, `attrgetter`)."""
    _ = resolved_dyn_attrs
    return _resolve_builtins_lookup(fn, state, scope, is_re_call=is_re_call)


def _inspect_python_call(
    state: _PyAstState, scope: _ScopeState, node: ast.Call
) -> Optional[Tuple[str, str]]:
    """Inspecte un appel `ast.Call` selon sa famille (y compris `partial`, `attrgetter`, `__getattribute__`, classes, lambdas/helpers)."""
    raw_fn = node.func
    raw_kw = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}

    is_re_call = (
        isinstance(raw_fn, ast.Attribute)
        and isinstance(raw_fn.value, ast.Name)
        and raw_fn.value.id in state.imp.re_mod_names
        and raw_fn.attr in RE_PATTERN_FUNCS
    ) or (isinstance(raw_fn, ast.Name) and raw_fn.id in state.imp.re_func_names)
    if is_re_call:
        pat_node = node.args[0] if node.args else raw_kw.get("pattern")
        if isinstance(pat_node, ast.Constant) and isinstance(pat_node.value, str):
            state.exempt_regex_nodes.add(pat_node)
        elif isinstance(pat_node, ast.Name) and pat_node.id in state.single_use_defs:
            state.exempt_regex_nodes.add(pat_node)
            _mark_subtree(state.single_use_defs[pat_node.id], state.exempt_regex_nodes)

    for attr_cand in _unwrap_attr_candidates(node, state, scope):
        if isinstance(attr_cand, ast.Attribute) and attr_cand.attr in ("environ", "environb"):
            return "deny", f"Accès dynamique à `environ` dans `{state.context_label}` bloqué."

    fn_cands, eff_args, kw_map = _unwrap_call_target(node, state, scope)

    for fn in fn_cands:
        if isinstance(fn, ast.Attribute) and fn.attr in ("environ", "environb"):
            return "deny", f"Accès dynamique à `environ` dans `{state.context_label}` bloqué."
        resolved_dyn_attrs: List[str] = []
        if (
            isinstance(fn, ast.Call)
            and isinstance(fn.func, ast.Name)
            and fn.func.id == "getattr"
            and len(fn.args) >= 2
        ):
            resolved_dyn_attrs = _eval_scope_str_cands(fn.args[1], state, scope)

        dyn_recv_mod = (
            _resolve_dynamic_import_module(
                fn.value, scope.cur_str, scope.cur_seq, state.imp.importlib_mod_names, state.imp.import_module_fns
            )
            if isinstance(fn, ast.Attribute)
            else None
        )

        for verdict in (
            _inspect_rmtree_call(state, scope, eff_args, fn, kw_map, resolved_dyn_attrs),
            _inspect_os_shell_call(state, scope, eff_args, fn, kw_map, resolved_dyn_attrs),
            _inspect_subprocess_call(state, scope, eff_args, fn, kw_map, resolved_dyn_attrs, dyn_recv_mod),
            _inspect_exec_spawn_call(state, scope, eff_args, fn, dyn_recv_mod),
        ):
            if verdict is not None:
                return verdict

        eval_kind = _resolve_eval_exec_call_kind(fn, state, scope, resolved_dyn_attrs, is_re_call)
        if eval_kind is not None:
            code_arg = eff_args[0] if eff_args else kw_map.get("source")
            if code_arg is not None:
                code_cands, is_complete = _eval_code_arg_cands(code_arg, state, scope)
                obf_hint = _expr_has_obfuscated_decode(code_arg, state, scope)
                if code_cands:
                    if state.depth >= MAX_SUBSHELL_DEPTH:
                        if obf_hint:
                            return "deny", f"Profondeur maximale atteinte avec décodage `{obf_hint}` dans `{eval_kind}`."
                    else:
                        for inner_code in code_cands:
                            sub_dec, sub_reason = _inspect_python_ast(
                                inner_code, state.ws_root, context_label=state.context_label,
                                allow_top_level_await=state.allow_top_level_await, _depth=state.depth + 1,
                            )
                            if sub_dec != "allow":
                                return "deny", sub_reason or f"Code dynamique `{eval_kind}` bloqué."
                        if obf_hint and not is_complete:
                            return "deny", f"Exécution dynamique `{eval_kind}` avec décodage non résolvable (`{obf_hint}`) bloquée."
                        if state.call_depth == 0:
                            _mark_benign_cmd_node(state, code_arg)
                elif obf_hint:
                    return "deny", f"Exécution dynamique `{eval_kind}` avec décodage non résolvable (`{obf_hint}`) bloquée."

    return _inspect_user_callable(fn_cands, eff_args, kw_map, state, scope, _walk_python_stmts)


def _walk_python_stmts(
    state: _PyAstState, scope: _ScopeState, stmts: List[ast.stmt]
) -> Optional[Tuple[str, str]]:
    """Parcourt les instructions séquentiellement en propageant les bindings, classes, mutations et branches `if`/`try`."""
    for stmt in stmts:
        expr_nodes = _iter_stmt_expr_nodes(stmt)
        for enode in expr_nodes:
            if isinstance(enode, ast.NamedExpr) and isinstance(enode.target, ast.Name):
                _bind_var(state, scope, enode.target.id, enode.value)
        for enode in expr_nodes:
            if isinstance(enode, ast.Call):
                verdict = _inspect_python_call(state, scope, enode)
                if verdict is not None:
                    return verdict
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
                    if vn in scope.cur_bytes or vn in state.alt_bytes:
                        _taint_bytes_var(state, scope, vn)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            _bind_var(state, scope, stmt.target.id, stmt.value)
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            var_name = stmt.target.id
            scope.var_nodes.pop(var_name, None)
            scope.cur_ints.pop(var_name, None)
            state.var_contributors.setdefault(var_name, []).append(stmt.value)
            if isinstance(stmt.op, ast.Add):
                if var_name in scope.cur_bytes:
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
            elif var_name in scope.cur_bytes or var_name in state.alt_bytes:
                _taint_bytes_var(state, scope, var_name)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if _mutate_bytes_method(state, scope, call):
                pass
            elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
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
        if isinstance(node, ast.Subscript):
            slice_node = _ast_subscript_slice(node)
            key_cands = list(state.resolved_expr_strs.get(slice_node, ()))
            fallback_key = _eval_static_str(
                slice_node, state.const_vars, state.const_seq_vars, 0, state.imp.func_returns
            )
            if fallback_key and fallback_key not in key_cands:
                key_cands.append(fallback_key)
            if any(k in ("environ", "environb") for k in key_cands):
                val_node = node.value
                is_env_dict = (
                    (isinstance(val_node, ast.Attribute) and val_node.attr == "__dict__")
                    or (
                        isinstance(val_node, ast.Call)
                        and isinstance(val_node.func, ast.Name)
                        and val_node.func.id == "vars"
                    )
                )
                if is_env_dict:
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
