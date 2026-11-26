#!/usr/bin/env python3
"""Inspection des appels Python (`rmtree`, `os.system`, `subprocess`, `os.exec*`, `eval`/`exec`/`compile`, `map`, `environ`) pour `python_ast`."""

import ast
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .constants import (
    MAX_SUBSHELL_DEPTH,
    OS_EXEC_LIST_FUNCS,
    OS_EXEC_VAR_FUNCS,
    OS_SHELL_FUNCS,
    OS_SPAWN_LIST_FUNCS,
    OS_SPAWN_VAR_FUNCS,
    RE_PATTERN_FUNCS,
    SUBPROCESS_CALL_FUNCS,
)
from .paths import _is_active_plugin_root_path, _is_destructive_target, is_harness_protected
from .python_ast_cands import (
    _eval_code_arg_cands,
    _eval_scope_seq_cands,
    _eval_scope_str_cands,
    _expr_has_obfuscated_decode,
    _match_dict_lookup_key,
)
from .python_ast_eval import _eval_static_str
from .python_ast_flow import _inspect_user_callable, _unwrap_call_target
from .python_ast_imports import (
    _is_builtins_mod_expr,
    _is_os_dict_expr,
    _resolve_builtins_lookup,
    _resolve_dynamic_import_module,
    _unwrap_attr_candidates,
)
from .python_ast_readonly import _mark_benign_cmd_node, _mark_subtree
from .python_ast_state import _PyAstState, _ScopeState
from .shell_parser import _argv_to_shell_cmd

InspectAstFn = Callable[..., Tuple[str, Optional[str]]]
WalkStmtsFn = Callable[[_PyAstState, _ScopeState, List[ast.stmt]], Optional[Tuple[str, str]]]


def _inspect_rmtree_call(
    state: _PyAstState,
    scope: _ScopeState,
    eff_args: List[ast.AST],
    fn: ast.AST,
    kw_map: Dict[str, ast.AST],
    resolved_dyn_attrs: List[str],
) -> Optional[Tuple[str, str]]:
    """Inspecte `shutil.rmtree(...)` (y compris alias, `getattr`, `partial`, `attrgetter`, `methodcaller`)."""
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
    """Résout si `fn` correspond à `eval`/`exec`/`compile`."""
    _ = resolved_dyn_attrs
    return _resolve_builtins_lookup(fn, state, scope, is_re_call=is_re_call)


def _is_map_call(fn: ast.AST, state: _PyAstState, scope: _ScopeState) -> bool:
    cur = scope.var_nodes.get(fn.id, fn) if isinstance(fn, ast.Name) else fn
    if isinstance(cur, ast.Name) and cur.id == "map" and cur.id not in state.imp.shadowed_names:
        return True
    if isinstance(cur, ast.Attribute) and cur.attr == "map" and _is_builtins_mod_expr(cur.value, state, scope):
        return True
    return False


def _inspect_map_call(
    state: _PyAstState,
    scope: _ScopeState,
    fn: ast.AST,
    eff_args: List[ast.AST],
    inspect_ast_fn: InspectAstFn,
    walk_stmts_fn: WalkStmtsFn,
) -> Optional[Tuple[str, str]]:
    """Inspecte `map(fn, iter, ...)` en appliquant `fn` aux éléments statiques (ou à l'itérable)."""
    if state.call_depth >= 4 or len(eff_args) < 2 or not _is_map_call(fn, state, scope):
        return None
    map_fn, iter_node = eff_args[0], eff_args[1]
    iter_res = scope.var_nodes.get(iter_node.id, iter_node) if isinstance(iter_node, ast.Name) else iter_node
    item_nodes: List[ast.AST] = []
    if isinstance(iter_res, (ast.List, ast.Tuple, ast.Set)):
        for elt in iter_res.elts[:32]:
            if isinstance(elt, ast.Starred):
                sub = scope.var_nodes.get(elt.value.id, elt.value) if isinstance(elt.value, ast.Name) else elt.value
                if isinstance(sub, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in sub.elts):
                    item_nodes.extend(sub.elts[:32])
                else:
                    item_nodes.append(elt.value)
            else:
                item_nodes.append(elt)
    else:
        seq_cands = _eval_scope_seq_cands(iter_node, state, scope)
        if seq_cands:
            for seq in seq_cands:
                for s in seq[:32]:
                    item_nodes.append(ast.Constant(value=s) if s is not None else iter_node)
        else:
            item_nodes.append(iter_node)
    state.call_depth += 1
    try:
        for item in item_nodes[:32]:
            syn_call = ast.Call(func=map_fn, args=[item], keywords=[])
            verdict = _inspect_python_call(state, scope, syn_call, inspect_ast_fn, walk_stmts_fn)
            if verdict is not None:
                return verdict
    finally:
        state.call_depth -= 1
    return None


def _inspect_python_call(
    state: _PyAstState,
    scope: _ScopeState,
    node: ast.Call,
    inspect_ast_fn: InspectAstFn,
    walk_stmts_fn: WalkStmtsFn,
) -> Optional[Tuple[str, str]]:
    """Inspecte un appel `ast.Call` selon sa famille (y compris `partial`, `methodcaller`, `attrgetter`, `__getattribute__`, `map`, classes, lambdas/helpers)."""
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

    for env_k in ("environ", "environb"):
        env_recv = _match_dict_lookup_key(node, state, scope, env_k)
        if env_recv is not None and _is_os_dict_expr(env_recv, state, scope):
            return "deny", f"Accès indirect à `{env_k}` dans `{state.context_label}` bloqué."

    for attr_cand in _unwrap_attr_candidates(node, state, scope):
        if isinstance(attr_cand, ast.Attribute) and attr_cand.attr in ("environ", "environb"):
            return "deny", f"Accès dynamique à `environ` dans `{state.context_label}` bloqué."

    fn_cands, eff_args, kw_map = _unwrap_call_target(node, state, scope)

    for fn in fn_cands:
        if isinstance(fn, ast.Attribute) and fn.attr in ("environ", "environb"):
            return "deny", f"Accès dynamique à `environ` dans `{state.context_label}` bloqué."
        syn_probe = ast.Call(
            func=fn,
            args=eff_args,
            keywords=[ast.keyword(arg=k, value=v) for k, v in kw_map.items()],
        )
        for env_k in ("environ", "environb"):
            env_recv = _match_dict_lookup_key(syn_probe, state, scope, env_k)
            if env_recv is not None and _is_os_dict_expr(env_recv, state, scope):
                return "deny", f"Accès indirect à `{env_k}` dans `{state.context_label}` bloqué."
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
            _inspect_map_call(state, scope, fn, eff_args, inspect_ast_fn, walk_stmts_fn),
        ):
            if verdict is not None:
                return verdict

        eval_kind = _resolve_eval_exec_call_kind(fn, state, scope, resolved_dyn_attrs, is_re_call)
        if eval_kind is not None:
            code_arg = eff_args[0] if eff_args else kw_map.get("source")
            if isinstance(code_arg, ast.Starred):
                return "deny", f"Exécution dynamique `{eval_kind}` avec argument dépaqueté non résolvable bloquée."
            if code_arg is not None:
                code_cands, is_complete = _eval_code_arg_cands(code_arg, state, scope)
                obf_hint = _expr_has_obfuscated_decode(code_arg, state, scope)
                if code_cands:
                    if state.depth >= MAX_SUBSHELL_DEPTH:
                        if obf_hint:
                            return "deny", f"Profondeur maximale atteinte avec décodage `{obf_hint}` dans `{eval_kind}`."
                    else:
                        for inner_code in code_cands:
                            sub_dec, sub_reason = inspect_ast_fn(
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

    return _inspect_user_callable(fn_cands, eff_args, kw_map, state, scope, walk_stmts_fn)
