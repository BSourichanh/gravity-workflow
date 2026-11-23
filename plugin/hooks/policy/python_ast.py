#!/usr/bin/env python3
"""Analyse AST Python et évaluation statique pour `python -c` et `run_workflow`."""

import ast
from dataclasses import dataclass, field
from pathlib import Path
import re
import shlex
from typing import Dict, List, Optional, Set, Tuple

from .constants import (
    MAX_SUBSHELL_DEPTH,
    OS_EXEC_LIST_FUNCS,
    OS_EXEC_VAR_FUNCS,
    OS_SHELL_FUNCS,
    OS_SPAWN_LIST_FUNCS,
    OS_SPAWN_VAR_FUNCS,
    QUOTED_LITERAL_RE,
    RE_PATTERN_FUNCS,
    SENSITIVE_PATH_PATTERNS,
    SUBPROCESS_CALL_FUNCS,
)
from .paths import _is_active_plugin_root_path, _is_destructive_target, is_harness_protected, matches_any
from .shell_parser import _argv_to_shell_cmd


@dataclass
class _PyImports:
    os_mod_names: Set[str] = field(default_factory=lambda: {"os", "posix", "nt"})
    subprocess_mod_names: Set[str] = field(default_factory=lambda: {"subprocess"})
    re_mod_names: Set[str] = field(default_factory=lambda: {"re"})
    re_func_names: Set[str] = field(default_factory=set)
    shlex_mod_names: Set[str] = field(default_factory=lambda: {"shlex"})
    shutil_mod_names: Set[str] = field(default_factory=lambda: {"shutil"})
    importlib_mod_names: Set[str] = field(default_factory=lambda: {"importlib"})
    import_module_fns: Set[str] = field(default_factory=set)
    shlex_split_names: Set[str] = field(default_factory=set)
    rmtree_func_names: Set[str] = field(default_factory=set)
    os_shell_func_names: Set[str] = field(default_factory=set)
    os_exec_list_names: Set[str] = field(default_factory=set)
    os_exec_var_names: Set[str] = field(default_factory=set)
    os_spawn_list_names: Set[str] = field(default_factory=set)
    os_spawn_var_names: Set[str] = field(default_factory=set)
    subprocess_func_names: Set[str] = field(default_factory=set)
    func_returns: Dict[str, List[ast.AST]] = field(default_factory=dict)


@dataclass
class _ScopeState:
    cur_str: Dict[str, str]
    cur_seq: Dict[str, List[Optional[str]]]
    subproc_mods: Set[str]
    os_mods: Set[str]
    rmtree_fns: Set[str]
    os_shell_fns: Set[str]
    subproc_fns: Set[str]

    def clone(self) -> "_ScopeState":
        return _ScopeState(
            cur_str=dict(self.cur_str),
            cur_seq={k: list(v) for k, v in self.cur_seq.items()},
            subproc_mods=set(self.subproc_mods),
            os_mods=set(self.os_mods),
            rmtree_fns=set(self.rmtree_fns),
            os_shell_fns=set(self.os_shell_fns),
            subproc_fns=set(self.subproc_fns),
        )


@dataclass
class _PyAstState:
    ws_root: Optional[Path]
    context_label: str
    allow_top_level_await: bool
    depth: int
    imp: _PyImports
    const_vars: Dict[str, str]
    const_seq_vars: Dict[str, List[Optional[str]]]
    single_use_defs: Dict[str, ast.AST]
    checked_cmd_nodes: Set[ast.AST] = field(default_factory=set)
    exempt_regex_nodes: Set[ast.AST] = field(default_factory=set)
    resolved_expr_strs: Dict[ast.AST, List[str]] = field(default_factory=dict)
    alt_str: Dict[str, List[str]] = field(default_factory=dict)
    alt_seq: Dict[str, List[List[Optional[str]]]] = field(default_factory=dict)
    var_contributors: Dict[str, List[ast.AST]] = field(default_factory=dict)
    benign_cmd_name_nodes: Set[ast.Name] = field(default_factory=set)
    mutator_receiver_names: Set[ast.Name] = field(default_factory=set)

    def record_alt_str(self, name: str, val: str) -> None:
        bucket = self.alt_str.setdefault(name, [])
        if val not in bucket and len(bucket) < 8:
            bucket.append(val)

    def record_alt_seq(self, name: str, val: List[Optional[str]]) -> None:
        bucket = self.alt_seq.setdefault(name, [])
        snap = list(val)
        if snap not in bucket and len(bucket) < 8:
            bucket.append(snap)

    def add_resolved_str(self, node_key: ast.AST, val: str) -> None:
        bucket = self.resolved_expr_strs.setdefault(node_key, [])
        if val not in bucket and len(bucket) < 8:
            bucket.append(val)


def _ast_subscript_slice(node: ast.Subscript) -> ast.AST:
    """Retourne l'expression d'index d'un `ast.Subscript` (compatible Python 3.8+)."""
    idx_cls = getattr(ast, "Index", None)
    if idx_cls is not None and isinstance(node.slice, idx_cls):
        return node.slice.value
    return node.slice


def _eval_static_int(node: Optional[ast.AST]) -> Optional[int]:
    """Évalue un entier littéral AST (positif ou négatif), sans booléens."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    return None


def _eval_static_seq(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Optional[Dict[str, List[Optional[str]]]] = None,
    _depth: int = 0,
    shlex_mod_names: Optional[Set[str]] = None,
    shlex_split_names: Optional[Set[str]] = None,
    func_returns: Optional[Dict[str, List[ast.AST]]] = None,
) -> Optional[List[Optional[str]]]:
    """Évalue statiquement une expression AST vers une liste de chaînes (variables, `+`, `*unpack`, slices, compréhensions, `reversed`, `shlex.split`, `.split`)."""
    if _depth > 8:
        return None
    rec = lambda n: _eval_static_seq(
        n, const_vars, const_seq_vars, _depth + 1, shlex_mod_names, shlex_split_names, func_returns
    )
    if isinstance(node, ast.NamedExpr):
        return rec(node.value)
    if isinstance(node, ast.Name):
        return list(const_seq_vars[node.id]) if (const_seq_vars and node.id in const_seq_vars) else None
    if isinstance(node, (ast.List, ast.Tuple)):
        items: List[Optional[str]] = []
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                unpacked = rec(elt.value)
                items.extend(unpacked) if unpacked is not None else items.append(None)
            else:
                items.append(_eval_static_str(elt, const_vars, const_seq_vars, _depth + 1, func_returns))
        return items
    if isinstance(node, ast.Subscript):
        slice_node = _ast_subscript_slice(node)
        if isinstance(slice_node, ast.Slice):
            seq = rec(node.value)
            if seq is not None:
                lo = _eval_static_int(slice_node.lower) if slice_node.lower is not None else None
                hi = _eval_static_int(slice_node.upper) if slice_node.upper is not None else None
                st = _eval_static_int(slice_node.step) if slice_node.step is not None else None
                if (
                    (slice_node.lower is None or lo is not None)
                    and (slice_node.upper is None or hi is not None)
                    and (slice_node.step is None or (st is not None and st != 0))
                ):
                    return list(seq[slice(lo, hi, st)])
    if isinstance(node, ast.ListComp) and len(node.generators) == 1:
        gen = node.generators[0]
        if not gen.ifs and isinstance(gen.target, ast.Name):
            src_seq = rec(gen.iter)
            if src_seq is not None:
                if isinstance(node.elt, ast.Name) and node.elt.id == gen.target.id:
                    return list(src_seq)
                comp_out: List[Optional[str]] = []
                for item in src_seq:
                    if item is None:
                        comp_out.append(None)
                    else:
                        local_str = dict(const_vars)
                        local_str[gen.target.id] = item
                        comp_out.append(_eval_static_str(node.elt, local_str, const_seq_vars, _depth + 1, func_returns))
                return comp_out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = rec(node.left), rec(node.right)
        return (left + right) if (left is not None and right is not None) else None
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in ("list", "tuple") and len(node.args) == 1 and not node.keywords:
            return rec(node.args[0])
        if isinstance(fn, ast.Name) and fn.id == "reversed" and len(node.args) == 1 and not node.keywords:
            seq = rec(node.args[0])
            return list(reversed(seq)) if seq is not None else None
        if isinstance(fn, ast.Name) and func_returns and fn.id in func_returns and not node.args and not node.keywords:
            for ret_expr in func_returns[fn.id]:
                seq = rec(ret_expr)
                if seq is not None:
                    return seq
        is_shlex_split = (
            isinstance(fn, ast.Name) and shlex_split_names is not None and fn.id in shlex_split_names
        ) or (
            isinstance(fn, ast.Attribute)
            and fn.attr == "split"
            and isinstance(fn.value, ast.Name)
            and fn.value.id in (shlex_mod_names or {"shlex"})
        )
        if is_shlex_split and len(node.args) >= 1:
            raw_s = _eval_static_str(node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns)
            if raw_s is not None:
                try:
                    return list(shlex.split(raw_s))
                except ValueError:
                    return raw_s.split()
        if isinstance(fn, ast.Attribute) and fn.attr == "split" and len(node.args) <= 1 and not node.keywords:
            recv = _eval_static_str(fn.value, const_vars, const_seq_vars, _depth + 1, func_returns)
            if recv is not None:
                if not node.args:
                    return list(recv.split())
                sep = _eval_static_str(node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns)
                if sep:
                    return list(recv.split(sep))
    return None


def _eval_static_str(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Optional[Dict[str, List[Optional[str]]]] = None,
    _depth: int = 0,
    func_returns: Optional[Dict[str, List[ast.AST]]] = None,
) -> Optional[str]:
    """Évalue statiquement une expression AST vers une chaîne (constantes, `+`, f-strings, slices, `.join`, `chr`, `Path`/`os.path.join`)."""
    if _depth > 8:
        return None
    rec = lambda n: _eval_static_str(n, const_vars, const_seq_vars, _depth + 1, func_returns)
    if isinstance(node, ast.NamedExpr):
        return rec(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in const_vars:
        return const_vars[node.id]
    if isinstance(node, ast.Subscript):
        idx_node = _ast_subscript_slice(node)
        if isinstance(idx_node, ast.Slice):
            base_s = rec(node.value)
            if base_s is not None:
                lo = _eval_static_int(idx_node.lower) if idx_node.lower is not None else None
                hi = _eval_static_int(idx_node.upper) if idx_node.upper is not None else None
                st = _eval_static_int(idx_node.step) if idx_node.step is not None else None
                if (
                    (idx_node.lower is None or lo is not None)
                    and (idx_node.upper is None or hi is not None)
                    and (idx_node.step is None or (st is not None and st != 0))
                ):
                    return base_s[slice(lo, hi, st)]
        idx_val = _eval_static_int(idx_node)
        if idx_val is not None:
            seq = _eval_static_seq(node.value, const_vars, const_seq_vars, _depth + 1, func_returns=func_returns)
            if seq is not None:
                try:
                    return seq[idx_val]
                except IndexError:
                    return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = rec(node.left), rec(node.right)
        return (left + right) if (left is not None and right is not None) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = rec(node.left), rec(node.right)
        return f"{left.rstrip('/\\\\')}/{right}" if (left is not None and right is not None) else None
    if isinstance(node, ast.JoinedStr):
        parts: List[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            elif isinstance(val, ast.FormattedValue) and val.format_spec is None:
                resolved = rec(val.value)
                if resolved is None:
                    return None
                parts.append(resolved)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and func_returns and fn.id in func_returns and not node.args and not node.keywords:
            for ret_expr in func_returns[fn.id]:
                val = rec(ret_expr)
                if val is not None:
                    return val
        if isinstance(fn, ast.Name) and fn.id == "chr" and len(node.args) == 1 and not node.keywords:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, int) and not isinstance(arg0.value, bool):
                if 0 <= arg0.value <= 0x10FFFF:
                    return chr(arg0.value)
            return None
        if isinstance(fn, ast.Attribute) and fn.attr == "join" and len(node.args) == 1 and not node.keywords:
            sep = rec(fn.value)
            if sep is not None:
                seq_vals = _eval_static_seq(
                    node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns=func_returns
                )
                if seq_vals is not None and all(v is not None for v in seq_vals):
                    return sep.join(v for v in seq_vals if v is not None)
        if (
            (isinstance(fn, ast.Name) and fn.id in ("Path", "PurePath", "PosixPath", "WindowsPath"))
            or (
                isinstance(fn, ast.Attribute)
                and fn.attr in ("Path", "PurePath", "PosixPath", "WindowsPath", "join")
                and (
                    fn.attr != "join"
                    or (isinstance(fn.value, ast.Attribute) and fn.value.attr == "path")
                    or (isinstance(fn.value, ast.Name) and fn.value.id in ("path", "posixpath", "ntpath"))
                )
            )
        ):
            if node.args and not node.keywords:
                segs: List[str] = []
                for arg in node.args:
                    val = rec(arg)
                    if val is None:
                        return None
                    segs.append(val)
                return "/".join(s.replace("\\", "/") for s in segs)
    return None


def _eval_static_str_candidates(
    node: ast.AST,
    cur_str: Dict[str, str],
    cur_seq: Dict[str, List[Optional[str]]],
    alt_str: Dict[str, List[str]],
    func_returns: Dict[str, List[ast.AST]],
    _depth: int = 0,
) -> List[str]:
    """Retourne les chaînes statiques candidates d'une expression AST (y compris `IfExp`, branches, helpers)."""
    if _depth > 6:
        return []
    out: List[str] = []

    def _add(val: Optional[str]) -> None:
        if val is not None and val not in out and len(out) < 8:
            out.append(val)

    if isinstance(node, ast.IfExp):
        for branch in (node.body, node.orelse):
            for c in _eval_static_str_candidates(branch, cur_str, cur_seq, alt_str, func_returns, _depth + 1):
                _add(c)
        return out
    if isinstance(node, ast.NamedExpr):
        return _eval_static_str_candidates(node.value, cur_str, cur_seq, alt_str, func_returns, _depth + 1)
    if isinstance(node, ast.Name):
        if node.id in cur_str:
            _add(cur_str[node.id])
        for alt in alt_str.get(node.id, ()):
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
            for c in _eval_static_str_candidates(ret_expr, cur_str, cur_seq, alt_str, func_returns, _depth + 1):
                _add(c)
        if out:
            return out
    _add(_eval_static_str(node, cur_str, cur_seq, 0, func_returns))
    return out


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
    _add(
        _eval_static_seq(
            node, cur_str, cur_seq, 0, shlex_mod_names, shlex_split_names, func_returns
        )
    )
    return out


def _collect_static_bindings(
    tree: ast.Module,
    shlex_mod_names: Set[str],
    shlex_split_names: Set[str],
) -> Tuple[Dict[str, str], Dict[str, List[Optional[str]]], Dict[str, ast.AST]]:
    """Collecte les constantes chaînes et listes/tuples assignées une seule fois dans l'AST."""
    bind_counts: Dict[str, int] = {}
    load_counts: Dict[str, int] = {}
    mutated_vars: Set[str] = set()

    def _bump(name: str) -> None:
        if name:
            bind_counts[name] = bind_counts.get(name, 0) + 1

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                _bump(node.id)
            elif isinstance(node.ctx, ast.Load):
                load_counts[node.id] = load_counts.get(node.id, 0) + 1
        elif isinstance(node, ast.arg):
            _bump(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            _bump(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                _bump(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _bump(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for nm in node.names:
                _bump(nm)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("append", "extend", "insert", "pop", "clear", "remove") and isinstance(
                node.func.value, ast.Name
            ):
                mutated_vars.add(node.func.value.id)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(node.value, ast.Name):
                mutated_vars.add(node.value.id)

    assign_pairs: List[Tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assign_pairs.append((node.targets[0].id, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assign_pairs.append((node.target.id, node.value))

    const_vars: Dict[str, str] = {}
    const_seq_vars: Dict[str, List[Optional[str]]] = {}
    single_use_defs: Dict[str, ast.AST] = {}
    for _ in range(3):
        changed = False
        for var_name, rhs in assign_pairs:
            if bind_counts.get(var_name, 0) != 1 or var_name in mutated_vars:
                continue
            if var_name not in const_vars and var_name not in const_seq_vars:
                s_val = _eval_static_str(rhs, const_vars, const_seq_vars)
                if s_val is not None:
                    const_vars[var_name] = s_val
                    if load_counts.get(var_name, 0) == 1:
                        single_use_defs[var_name] = rhs
                    changed = True
                    continue
                seq_val = _eval_static_seq(
                    rhs, const_vars, const_seq_vars, 0, shlex_mod_names, shlex_split_names
                )
                if seq_val is not None:
                    const_seq_vars[var_name] = seq_val
                    if load_counts.get(var_name, 0) == 1:
                        single_use_defs[var_name] = rhs
                    changed = True
        if not changed:
            break
    return const_vars, const_seq_vars, single_use_defs


def _collect_const_str_vars(tree: ast.Module) -> Dict[str, str]:
    """Collecte les constantes chaînes assignées une seule fois dans l'AST."""
    const_vars, _, _ = _collect_static_bindings(tree, {"shlex"}, set())
    return const_vars


def _mark_subtree(node: ast.AST, target_set: Set[ast.AST]) -> None:
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
            if isinstance(field_val, ast.AST):
                if not isinstance(field_val, skip_types):
                    _visit(field_val)
            elif isinstance(field_val, list):
                for item in field_val:
                    if isinstance(item, ast.AST) and not isinstance(item, skip_types):
                        _visit(item)

    _visit(stmt)
    return out


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


def _raw_code_hard_deny_check(
    code: str, ws_root: Optional[Path], context_label: str
) -> Optional[Tuple[str, str]]:
    """Repli regex `deny` si un script Python/workflow est non analysable par AST."""
    if re.search(r"\b(?:os|posix|nt)\.environb?\b", code):
        return "deny", f"Accès à `os.environ` dans `{context_label}` bloqué."
    for match in QUOTED_LITERAL_RE.finditer(code):
        lit = match.group(1) if match.group(1) is not None else (
            match.group(2) if match.group(2) is not None else match.group(3)
        )
        if not lit:
            continue
        if is_harness_protected(lit, ws_root):
            return "deny", f"Script `{context_label}` ciblant les garde-fous Gravity bloqué."
        if matches_any(lit, SENSITIVE_PATH_PATTERNS):
            return "deny", f"Script `{context_label}` ciblant un chemin sensible bloqué."
    return None


def _collect_python_imports(
    tree: ast.Module, context_label: str
) -> Tuple[Optional[Tuple[str, str]], _PyImports]:
    """Collecte les alias importés (`os`, `subprocess`, `shutil`, `re`, `shlex`, `importlib`) et fonctions 0-arg."""
    imp = _PyImports()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if alias.name in ("os", "posix", "nt"):
                    imp.os_mod_names.add(bound)
                elif alias.name == "subprocess":
                    imp.subprocess_mod_names.add(bound)
                elif alias.name == "re":
                    imp.re_mod_names.add(bound)
                elif alias.name == "shlex":
                    imp.shlex_mod_names.add(bound)
                elif alias.name == "shutil":
                    imp.shutil_mod_names.add(bound)
                elif alias.name == "importlib":
                    imp.importlib_mod_names.add(bound)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                if node.module in ("os", "posix", "nt"):
                    if alias.name in ("environ", "environb"):
                        return ("deny", f"Import de `os.environ` dans `{context_label}` bloqué."), imp
                    if alias.name in OS_SHELL_FUNCS:
                        imp.os_shell_func_names.add(bound)
                    elif alias.name in OS_EXEC_LIST_FUNCS:
                        imp.os_exec_list_names.add(bound)
                    elif alias.name in OS_EXEC_VAR_FUNCS:
                        imp.os_exec_var_names.add(bound)
                    elif alias.name in OS_SPAWN_LIST_FUNCS:
                        imp.os_spawn_list_names.add(bound)
                    elif alias.name in OS_SPAWN_VAR_FUNCS:
                        imp.os_spawn_var_names.add(bound)
                elif node.module == "shutil" and alias.name == "rmtree":
                    imp.rmtree_func_names.add(bound)
                elif node.module == "subprocess" and alias.name in SUBPROCESS_CALL_FUNCS:
                    imp.subprocess_func_names.add(bound)
                elif node.module == "shlex" and alias.name == "split":
                    imp.shlex_split_names.add(bound)
                elif node.module == "importlib" and alias.name == "import_module":
                    imp.import_module_fns.add(bound)
                elif node.module == "re" and alias.name in RE_PATTERN_FUNCS:
                    imp.re_func_names.add(bound)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.args.args and not node.args.posonlyargs and not node.args.kwonlyargs:
                rets = [
                    sub.value
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Return) and sub.value is not None
                ]
                if rets:
                    imp.func_returns.setdefault(node.name, []).extend(rets[:4])
    return None, imp


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
    elif isinstance(rhs_node, ast.Attribute):
        if rhs_node.attr == "rmtree":
            scope.rmtree_fns.add(target_name)
        elif rhs_node.attr in OS_SHELL_FUNCS:
            scope.os_shell_fns.add(target_name)
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
        for g_attr in _eval_static_str_candidates(
            rhs_node.args[1], scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
        ):
            if g_attr == "rmtree":
                scope.rmtree_fns.add(target_name)
            elif g_attr in OS_SHELL_FUNCS:
                scope.os_shell_fns.add(target_name)
            elif g_attr in SUBPROCESS_CALL_FUNCS:
                scope.subproc_fns.add(target_name)

    str_cands = _eval_static_str_candidates(
        rhs_node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
    )
    if str_cands:
        scope.cur_str[target_name] = str_cands[0]
        scope.cur_seq.pop(target_name, None)
        for c in str_cands:
            state.record_alt_str(target_name, c)
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
        for sc in seq_cands:
            state.record_alt_seq(target_name, sc)
        return
    scope.cur_str.pop(target_name, None)
    scope.cur_seq.pop(target_name, None)


def _mark_benign_cmd_node(state: _PyAstState, arg_node: ast.AST) -> None:
    """Marque un nœud d'argument déjà validé afin d'éviter un faux positif en passe 2."""
    _mark_subtree(arg_node, state.checked_cmd_nodes)
    if isinstance(arg_node, ast.Name):
        state.benign_cmd_name_nodes.add(arg_node)
        if arg_node.id in state.single_use_defs:
            _mark_subtree(state.single_use_defs[arg_node.id], state.checked_cmd_nodes)


def _inspect_rmtree_call(
    state: _PyAstState,
    scope: _ScopeState,
    node: ast.Call,
    fn: ast.AST,
    kw_map: Dict[str, ast.AST],
    resolved_dyn_attrs: List[str],
) -> Optional[Tuple[str, str]]:
    """Inspecte `shutil.rmtree(...)` (y compris alias et `getattr`)."""
    is_rmtree = (
        (isinstance(fn, ast.Name) and fn.id in scope.rmtree_fns)
        or (isinstance(fn, ast.Attribute) and fn.attr == "rmtree")
        or ("rmtree" in resolved_dyn_attrs)
    )
    if not is_rmtree:
        return None
    target_node = node.args[0] if node.args else kw_map.get("path")
    if target_node is not None:
        for target_str in _eval_static_str_candidates(
            target_node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
        ):
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
    node: ast.Call,
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
    shell_arg = node.args[0] if node.args else (kw_map.get("command") or kw_map.get("cmd"))
    if shell_arg is None:
        return None
    str_cands = _eval_static_str_candidates(
        shell_arg, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
    )
    if str_cands:
        for cmd_str in str_cands:
            deny_reason, _ = _evaluate_shell_command(cmd_str, state.ws_root, _depth=state.depth + 1)
            if deny_reason:
                return "deny", deny_reason
        _mark_benign_cmd_node(state, shell_arg)
    return None


def _inspect_subprocess_call(
    state: _PyAstState,
    scope: _ScopeState,
    node: ast.Call,
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
    cmd_node = node.args[0] if node.args else kw_map.get("args")
    if cmd_node is None:
        return None
    str_cands = _eval_static_str_candidates(
        cmd_node, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
    )
    seq_cands = _eval_static_seq_candidates(
        cmd_node,
        scope.cur_str,
        scope.cur_seq,
        state.alt_seq,
        state.imp.shlex_mod_names,
        state.imp.shlex_split_names,
        state.imp.func_returns,
    )
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
    if str_cands or seq_cands:
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
    node: ast.Call,
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
    exec_list_hit = (is_os_attr and fn.attr in OS_EXEC_LIST_FUNCS) or (
        isinstance(fn, ast.Name) and fn.id in state.imp.os_exec_list_names
    )
    spawn_list_hit = (is_os_attr and fn.attr in OS_SPAWN_LIST_FUNCS) or (
        isinstance(fn, ast.Name) and fn.id in state.imp.os_spawn_list_names
    )
    exec_var_hit = (is_os_attr and fn.attr in OS_EXEC_VAR_FUNCS) or (
        isinstance(fn, ast.Name) and fn.id in state.imp.os_exec_var_names
    )
    spawn_var_hit = (is_os_attr and fn.attr in OS_SPAWN_VAR_FUNCS) or (
        isinstance(fn, ast.Name) and fn.id in state.imp.os_spawn_var_names
    )

    argv_candidates: List[List[str]] = []
    if (exec_list_hit and len(node.args) >= 2) or (spawn_list_hit and len(node.args) >= 3):
        exe_idx, seq_idx = (0, 1) if exec_list_hit else (1, 2)
        exe_cands = _eval_static_str_candidates(
            node.args[exe_idx], scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
        )
        for seq_vals in _eval_static_seq_candidates(
            node.args[seq_idx],
            scope.cur_str,
            scope.cur_seq,
            state.alt_seq,
            state.imp.shlex_mod_names,
            state.imp.shlex_split_names,
            state.imp.func_returns,
        ):
            if seq_vals:
                argv = [v if v is not None else "__dynamic__" for v in seq_vals]
                argv_candidates.append(argv)
                for exe_s in exe_cands:
                    argv_candidates.append([exe_s] + argv[1:])
    elif (exec_var_hit and len(node.args) >= 2) or (spawn_var_hit and len(node.args) >= 3):
        exe_idx, arg_start = (0, 1) if exec_var_hit else (1, 2)
        exe_cands = _eval_static_str_candidates(
            node.args[exe_idx], scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
        )
        argv = [
            _eval_static_str(e, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns) or "__dynamic__"
            for e in node.args[arg_start:]
        ]
        argv_candidates.append(argv)
        for exe_s in exe_cands:
            argv_candidates.append([exe_s] + argv[1:])

    for argv in argv_candidates:
        deny_reason, _ = _evaluate_shell_command(_argv_to_shell_cmd(argv), state.ws_root, _depth=state.depth + 1)
        if deny_reason:
            return "deny", deny_reason
    return None


def _inspect_python_call(
    state: _PyAstState, scope: _ScopeState, node: ast.Call
) -> Optional[Tuple[str, str]]:
    """Inspecte un appel `ast.Call` selon sa famille (`re.*`, `getattr`, `rmtree`, `os`, `subprocess`, `eval/exec`)."""
    fn = node.func
    kw_map = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}

    is_re_call = (
        isinstance(fn, ast.Attribute)
        and isinstance(fn.value, ast.Name)
        and fn.value.id in state.imp.re_mod_names
        and fn.attr in RE_PATTERN_FUNCS
    ) or (isinstance(fn, ast.Name) and fn.id in state.imp.re_func_names)
    if is_re_call:
        pat_node = node.args[0] if node.args else kw_map.get("pattern")
        if isinstance(pat_node, ast.Constant) and isinstance(pat_node.value, str):
            state.exempt_regex_nodes.add(pat_node)
        elif isinstance(pat_node, ast.Name) and pat_node.id in state.single_use_defs:
            state.exempt_regex_nodes.add(pat_node)
            _mark_subtree(state.single_use_defs[pat_node.id], state.exempt_regex_nodes)

    if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
        for attr_str in _eval_static_str_candidates(
            node.args[1], scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
        ):
            if attr_str in ("environ", "environb"):
                return "deny", f"Accès dynamique à `environ` dans `{state.context_label}` bloqué."

    resolved_dyn_attrs: List[str] = []
    if (
        isinstance(fn, ast.Call)
        and isinstance(fn.func, ast.Name)
        and fn.func.id == "getattr"
        and len(fn.args) >= 2
    ):
        resolved_dyn_attrs = _eval_static_str_candidates(
            fn.args[1], scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
        )

    dyn_recv_mod = (
        _resolve_dynamic_import_module(
            fn.value, scope.cur_str, scope.cur_seq, state.imp.importlib_mod_names, state.imp.import_module_fns
        )
        if isinstance(fn, ast.Attribute)
        else None
    )

    for verdict in (
        _inspect_rmtree_call(state, scope, node, fn, kw_map, resolved_dyn_attrs),
        _inspect_os_shell_call(state, scope, node, fn, kw_map, resolved_dyn_attrs),
        _inspect_subprocess_call(state, scope, node, fn, kw_map, resolved_dyn_attrs, dyn_recv_mod),
        _inspect_exec_spawn_call(state, scope, node, fn, dyn_recv_mod),
    ):
        if verdict is not None:
            return verdict

    if isinstance(fn, ast.Name) and fn.id in ("eval", "exec") and node.args and state.depth < MAX_SUBSHELL_DEPTH:
        code_cands = _eval_static_str_candidates(
            node.args[0], scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
        )
        if code_cands:
            for inner_code in code_cands:
                sub_dec, sub_reason = _inspect_python_ast(
                    inner_code,
                    state.ws_root,
                    context_label=state.context_label,
                    allow_top_level_await=state.allow_top_level_await,
                    _depth=state.depth + 1,
                )
                if sub_dec == "deny":
                    return sub_dec, sub_reason or f"Code dynamique `{fn.id}` bloqué."
            _mark_benign_cmd_node(state, node.args[0])

    return None


def _walk_python_stmts(
    state: _PyAstState, scope: _ScopeState, stmts: List[ast.stmt]
) -> Optional[Tuple[str, str]]:
    """Parcourt les instructions séquentiellement en propageant les bindings et branches `if`/`try`."""
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
                for s_val in _eval_static_str_candidates(
                    enode, scope.cur_str, scope.cur_seq, state.alt_str, state.imp.func_returns
                ):
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
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            _bind_var(state, scope, stmt.target.id, stmt.value)
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name) and isinstance(stmt.op, ast.Add):
            var_name = stmt.target.id
            state.var_contributors.setdefault(var_name, []).append(stmt.value)
            if var_name in scope.cur_str:
                rhs_s = _eval_static_str(stmt.value, scope.cur_str, scope.cur_seq, 0, state.imp.func_returns)
                if rhs_s is not None:
                    scope.cur_str[var_name] = scope.cur_str[var_name] + rhs_s
                    state.record_alt_str(var_name, scope.cur_str[var_name])
                else:
                    scope.cur_str.pop(var_name, None)
            elif var_name in scope.cur_seq:
                rhs_seq = _eval_static_seq(
                    stmt.value,
                    scope.cur_str,
                    scope.cur_seq,
                    0,
                    state.imp.shlex_mod_names,
                    state.imp.shlex_split_names,
                    state.imp.func_returns,
                )
                scope.cur_seq[var_name] = scope.cur_seq[var_name] + (rhs_seq if rhs_seq is not None else [None])
                state.record_alt_seq(var_name, scope.cur_seq[var_name])
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in scope.cur_seq
            ):
                target_var = call.func.value.id
                if call.func.attr == "append" and len(call.args) == 1:
                    state.mutator_receiver_names.add(call.func.value)
                    state.var_contributors.setdefault(target_var, []).append(call.args[0])
                    scope.cur_seq[target_var].append(
                        _eval_static_str(call.args[0], scope.cur_str, scope.cur_seq, 0, state.imp.func_returns)
                    )
                    state.record_alt_seq(target_var, scope.cur_seq[target_var])
                elif call.func.attr == "extend" and len(call.args) == 1:
                    state.mutator_receiver_names.add(call.func.value)
                    state.var_contributors.setdefault(target_var, []).append(call.args[0])
                    ext_vals = _eval_static_seq(
                        call.args[0],
                        scope.cur_str,
                        scope.cur_seq,
                        0,
                        state.imp.shlex_mod_names,
                        state.imp.shlex_split_names,
                        state.imp.func_returns,
                    )
                    if ext_vals is not None:
                        scope.cur_seq[target_var].extend(ext_vals)
                    else:
                        scope.cur_seq[target_var].append(None)
                    state.record_alt_seq(target_var, scope.cur_seq[target_var])
                elif call.func.attr == "insert" and len(call.args) == 2:
                    idx_val = _eval_static_int(call.args[0])
                    if idx_val is not None:
                        state.mutator_receiver_names.add(call.func.value)
                        state.var_contributors.setdefault(target_var, []).append(call.args[1])
                        scope.cur_seq[target_var].insert(
                            idx_val,
                            _eval_static_str(call.args[1], scope.cur_str, scope.cur_seq, 0, state.imp.func_returns),
                        )
                        state.record_alt_seq(target_var, scope.cur_seq[target_var])

        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
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
                scope.subproc_mods.update(b_scope.subproc_mods)
                scope.os_mods.update(b_scope.os_mods)
                scope.rmtree_fns.update(b_scope.rmtree_fns)
                scope.os_shell_fns.update(b_scope.os_shell_fns)
                scope.subproc_fns.update(b_scope.subproc_fns)
                for k, v in b_scope.cur_str.items():
                    scope.cur_str[k] = v
                    state.record_alt_str(k, v)
                for k, v in b_scope.cur_seq.items():
                    scope.cur_seq[k] = list(v)
                    state.record_alt_seq(k, v)
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
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node not in state.benign_cmd_name_nodes
            and node not in state.mutator_receiver_names
        ):
            other_loaded_vars.add(node.id)
    for cmd_name_node in state.benign_cmd_name_nodes:
        if cmd_name_node.id not in other_loaded_vars:
            state.checked_cmd_nodes.add(cmd_name_node)
            for contrib in state.var_contributors.get(cmd_name_node.id, ()):
                _mark_subtree(contrib, state.checked_cmd_nodes)

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
                if is_harness_protected(val, state.ws_root):
                    return "deny", f"Script `{state.context_label}` ciblant les garde-fous Gravity bloqué."
                if matches_any(val, SENSITIVE_PATH_PATTERNS):
                    return "deny", f"Script `{state.context_label}` ciblant un chemin sensible bloqué."

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
    scope = _ScopeState(
        cur_str=dict(const_vars),
        cur_seq={k: list(v) for k, v in const_seq_vars.items()},
        subproc_mods=set(imp.subprocess_mod_names),
        os_mods=set(imp.os_mod_names),
        rmtree_fns=set(imp.rmtree_func_names),
        os_shell_fns=set(imp.os_shell_func_names),
        subproc_fns=set(imp.subprocess_func_names),
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
