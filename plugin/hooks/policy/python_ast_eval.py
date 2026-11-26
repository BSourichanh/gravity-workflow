#!/usr/bin/env python3
"""Évaluation statique AST (chaînes, séquences, entiers, liaisons) et structures d'état pour `python_ast`."""

import ast
from dataclasses import dataclass, field
from pathlib import Path
import shlex
from typing import Dict, List, Optional, Set, Tuple


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
    pathlib_mod_names: Set[str] = field(default_factory=lambda: {"pathlib"})
    path_ctor_names: Set[str] = field(default_factory=lambda: {"Path", "PurePath", "PosixPath", "WindowsPath"})
    json_mod_names: Set[str] = field(default_factory=lambda: {"json"})
    json_load_fns: Set[str] = field(default_factory=set)
    os_path_mod_names: Set[str] = field(default_factory=lambda: {"posixpath", "ntpath"})
    os_readonly_term_fns: Set[str] = field(default_factory=set)
    os_readonly_prop_fns: Set[str] = field(default_factory=set)
    shadowed_names: Set[str] = field(default_factory=set)
    has_wildcard_import: bool = False
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
    dynamic_tainted_vars: Set[str] = field(default_factory=set)

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
        return f"{left.rstrip('/\\')}/{right}" if (left is not None and right is not None) else None
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
    """Marque tous les descendants AST de `node` dans `target_set`."""
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


def _mark_benign_cmd_node(state: _PyAstState, arg_node: ast.AST) -> None:
    """Marque un nœud d'argument déjà validé afin d'éviter un faux positif en passe 2."""
    _mark_subtree(arg_node, state.checked_cmd_nodes)
    if isinstance(arg_node, ast.Name):
        state.benign_cmd_name_nodes.add(arg_node)
        if arg_node.id in state.single_use_defs:
            _mark_subtree(state.single_use_defs[arg_node.id], state.checked_cmd_nodes)
