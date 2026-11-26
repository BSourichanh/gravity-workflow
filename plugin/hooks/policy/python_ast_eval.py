#!/usr/bin/env python3
"""Évaluation statique AST (chaînes, séquences, entiers, liaisons) et structures d'état pour `python_ast`."""

import ast
import base64
from dataclasses import dataclass, field
from pathlib import Path
import shlex
from typing import Dict, List, Optional, Set, Tuple

_BASE64_DECODE_FUNCS = frozenset(
    {"b64decode", "standard_b64decode", "urlsafe_b64decode", "decodebytes"}
)
_OBFUSCATED_DECODE_ATTRS = _BASE64_DECODE_FUNCS | frozenset(
    {"b32decode", "b16decode", "b85decode", "a85decode", "fromhex", "decompress", "a2b_base64", "a2b_hex", "unhexlify"}
)
_OBFUSCATED_DECODE_MODS = frozenset({"base64", "codecs", "binascii", "zlib", "bz2", "lzma", "gzip"})
_OS_PATH_TERMINAL_FUNCS = frozenset(
    {"exists", "lexists", "isfile", "isdir", "islink", "ismount", "getsize", "getmtime", "getctime", "getatime", "samefile", "sameopenfile"}
)
_OS_PATH_PROPAGATING_FUNCS = frozenset(
    {"join", "abspath", "realpath", "normpath", "normcase", "dirname", "basename", "split", "splitdrive", "splitext", "expanduser", "expandvars", "relpath", "commonpath", "commonprefix"}
)


@dataclass
class _PyImports:
    os_mod_names: Set[str] = field(default_factory=lambda: {"os", "posix", "nt"})
    subprocess_mod_names: Set[str] = field(default_factory=lambda: {"subprocess"})
    re_mod_names: Set[str] = field(default_factory=lambda: {"re"})
    re_func_names: Set[str] = field(default_factory=set)
    shlex_mod_names: Set[str] = field(default_factory=lambda: {"shlex"})
    shutil_mod_names: Set[str] = field(default_factory=lambda: {"shutil"})
    importlib_mod_names: Set[str] = field(default_factory=lambda: {"importlib"})
    base64_mod_names: Set[str] = field(default_factory=lambda: {"base64"})
    base64_decode_fns: Dict[str, str] = field(default_factory=dict)
    codecs_mod_names: Set[str] = field(default_factory=lambda: {"codecs"})
    codecs_decode_fns: Set[str] = field(default_factory=set)
    builtins_mod_names: Set[str] = field(default_factory=lambda: {"builtins", "__builtins__"})
    eval_exec_fns: Dict[str, str] = field(
        default_factory=lambda: {"eval": "eval", "exec": "exec", "compile": "compile"}
    )
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
    func_defs: Dict[str, List[ast.AST]] = field(default_factory=dict)


@dataclass
class _ScopeState:
    cur_str: Dict[str, str]
    cur_seq: Dict[str, List[Optional[str]]]
    subproc_mods: Set[str]
    os_mods: Set[str]
    rmtree_fns: Set[str]
    os_shell_fns: Set[str]
    subproc_fns: Set[str]
    base64_mods: Set[str] = field(default_factory=lambda: {"base64"})
    base64_fns: Dict[str, str] = field(default_factory=dict)
    codecs_mods: Set[str] = field(default_factory=lambda: {"codecs"})
    codecs_fns: Set[str] = field(default_factory=set)
    builtins_mods: Set[str] = field(default_factory=lambda: {"builtins", "__builtins__"})
    eval_exec_fns: Dict[str, str] = field(
        default_factory=lambda: {"eval": "eval", "exec": "exec", "compile": "compile"}
    )
    cur_bytes: Dict[str, bytes] = field(default_factory=dict)
    incomplete_vars: Set[str] = field(default_factory=set)
    obf_tainted: Set[str] = field(default_factory=set)
    shadowed_alts: Set[str] = field(default_factory=set)

    def clone(self) -> "_ScopeState":
        return _ScopeState(
            cur_str=dict(self.cur_str),
            cur_seq={k: list(v) for k, v in self.cur_seq.items()},
            subproc_mods=set(self.subproc_mods),
            os_mods=set(self.os_mods),
            rmtree_fns=set(self.rmtree_fns),
            os_shell_fns=set(self.os_shell_fns),
            subproc_fns=set(self.subproc_fns),
            base64_mods=set(self.base64_mods),
            base64_fns=dict(self.base64_fns),
            codecs_mods=set(self.codecs_mods),
            codecs_fns=set(self.codecs_fns),
            builtins_mods=set(self.builtins_mods),
            eval_exec_fns=dict(self.eval_exec_fns),
            cur_bytes=dict(self.cur_bytes),
            incomplete_vars=set(self.incomplete_vars),
            obf_tainted=set(self.obf_tainted),
            shadowed_alts=set(self.shadowed_alts),
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
    alt_bytes: Dict[str, List[bytes]] = field(default_factory=dict)
    alt_seq: Dict[str, List[List[Optional[str]]]] = field(default_factory=dict)
    var_contributors: Dict[str, List[ast.AST]] = field(default_factory=dict)
    benign_cmd_name_nodes: Set[ast.Name] = field(default_factory=set)
    mutator_receiver_names: Set[ast.Name] = field(default_factory=set)
    dynamic_tainted_vars: Set[str] = field(default_factory=set)

    def record_alt_str(self, name: str, val: str) -> None:
        bucket = self.alt_str.setdefault(name, [])
        if val not in bucket and len(bucket) < 8:
            bucket.append(val)

    def record_alt_bytes(self, name: str, val: bytes) -> None:
        bucket = self.alt_bytes.setdefault(name, [])
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


def _eval_slice_bounds(slice_node: ast.Slice) -> Optional[slice]:
    """Évalue statiquement les bornes d'un `ast.Slice` si toutes sont littérales."""
    lo = _eval_static_int(slice_node.lower) if slice_node.lower is not None else None
    hi = _eval_static_int(slice_node.upper) if slice_node.upper is not None else None
    st = _eval_static_int(slice_node.step) if slice_node.step is not None else None
    if (
        (slice_node.lower is None or lo is not None)
        and (slice_node.upper is None or hi is not None)
        and (slice_node.step is None or (st is not None and st != 0))
    ):
        return slice(lo, hi, st)
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
            seq, sl = rec(node.value), _eval_slice_bounds(slice_node)
            if seq is not None and sl is not None:
                return list(seq[sl])
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


def _decode_bytes_text(raw_b: bytes) -> str:
    """Décode `bytes` en texte UTF-8 avec repli Latin-1 sans perte."""
    try:
        return raw_b.decode("utf-8")
    except UnicodeDecodeError:
        return raw_b.decode("latin-1")


def _is_named_mod_expr(node: ast.AST, target_mod: str, mod_aliases: Set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in mod_aliases
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == target_mod:
        fn = node.func
        return (isinstance(fn, ast.Name) and fn.id == "__import__") or (
            isinstance(fn, ast.Attribute) and fn.attr == "import_module"
        )
    return False


def _match_base64_decode_call(
    node: ast.Call,
    base64_mod_names: Optional[Set[str]] = None,
    base64_decode_fns: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[str, ast.AST]]:
    """Retourne `(fn_canon, arg_node)` si `node` appelle une fonction de décodage `base64`."""
    mods, fns, fn = base64_mod_names or {"base64"}, base64_decode_fns or {}, node.func
    fn_canon: Optional[str] = None
    if isinstance(fn, ast.Name) and fn.id in fns:
        fn_canon = fns[fn.id]
    elif isinstance(fn, ast.Attribute) and fn.attr in _BASE64_DECODE_FUNCS and _is_named_mod_expr(fn.value, "base64", mods):
        fn_canon = fn.attr
    elif (
        isinstance(fn, ast.Call)
        and isinstance(fn.func, ast.Name)
        and fn.func.id == "getattr"
        and len(fn.args) >= 2
        and isinstance(fn.args[1], ast.Constant)
        and fn.args[1].value in _BASE64_DECODE_FUNCS
        and _is_named_mod_expr(fn.args[0], "base64", mods)
    ):
        fn_canon = str(fn.args[1].value)
    if fn_canon is None:
        return None
    kw = {k.arg: k.value for k in node.keywords if k.arg is not None}
    arg0 = node.args[0] if node.args else kw.get("s")
    if arg0 is None or len(node.args) > 1 or any(k not in ("s", "validate") for k in kw):
        return None
    return fn_canon, arg0


def _decode_base64_payload(raw: object, fn_canon: str) -> Optional[bytes]:
    if isinstance(raw, str):
        try:
            raw_b = raw.encode("ascii")
        except UnicodeEncodeError:
            return None
    elif isinstance(raw, bytes):
        raw_b = raw
    else:
        return None
    decoder = getattr(base64, fn_canon, None)
    if decoder is None:
        return None
    try:
        return decoder(raw_b)
    except (ValueError, TypeError):
        return None


def _eval_static_bytes(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Optional[Dict[str, List[Optional[str]]]] = None,
    _depth: int = 0,
    func_returns: Optional[Dict[str, List[ast.AST]]] = None,
    base64_mod_names: Optional[Set[str]] = None,
    base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
) -> Optional[bytes]:
    """Évalue statiquement une expression AST vers `bytes` (littéraux, `+`, `.encode()`, `bytes.fromhex`, `base64.*decode`)."""
    if _depth > 8:
        return None
    rec_b = lambda n: _eval_static_bytes(
        n, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes
    )
    rec_s = lambda n: _eval_static_str(
        n, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes
    )
    if isinstance(node, ast.NamedExpr):
        return rec_b(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
        return node.value
    if isinstance(node, ast.Name) and cur_bytes and node.id in cur_bytes:
        return cur_bytes[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = rec_b(node.left), rec_b(node.right)
        return (left + right) if (left is not None and right is not None) else None
    if isinstance(node, ast.Call):
        b64_match = _match_base64_decode_call(node, base64_mod_names, base64_decode_fns)
        if b64_match is not None:
            fn_canon, arg0 = b64_match
            raw: object = rec_b(arg0)
            if raw is None:
                raw = rec_s(arg0)
            return _decode_base64_payload(raw, fn_canon) if raw is not None else None
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "encode" and len(node.args) <= 1 and not node.keywords:
            s_val, enc = rec_s(fn.value), (rec_s(node.args[0]) if node.args else "utf-8")
            if s_val is not None and enc:
                try:
                    return s_val.encode(enc)
                except (LookupError, UnicodeEncodeError):
                    return None
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "fromhex"
            and isinstance(fn.value, ast.Name)
            and fn.value.id in ("bytes", "bytearray")
            and len(node.args) == 1
            and not node.keywords
        ):
            hex_s = rec_s(node.args[0])
            if hex_s is not None:
                try:
                    return bytes.fromhex(hex_s)
                except ValueError:
                    return None
    return None


def _eval_static_str(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Optional[Dict[str, List[Optional[str]]]] = None,
    _depth: int = 0,
    func_returns: Optional[Dict[str, List[ast.AST]]] = None,
    base64_mod_names: Optional[Set[str]] = None,
    base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
) -> Optional[str]:
    """Évalue statiquement une expression AST vers une chaîne (constantes, `+`, f-strings, slices, `.join`, `chr`, `Path`, `base64`)."""
    if _depth > 8:
        return None
    rec = lambda n: _eval_static_str(
        n, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes
    )
    if isinstance(node, ast.NamedExpr):
        return rec(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in const_vars:
        return const_vars[node.id]
    if isinstance(node, ast.Subscript):
        idx_node = _ast_subscript_slice(node)
        if isinstance(idx_node, ast.Slice):
            base_s, sl = rec(node.value), _eval_slice_bounds(idx_node)
            if base_s is not None and sl is not None:
                return base_s[sl]
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
        b64_match = _match_base64_decode_call(node, base64_mod_names, base64_decode_fns)
        if b64_match is not None:
            fn_canon, arg0 = b64_match
            raw: object = _eval_static_bytes(
                arg0, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes
            )
            if raw is None:
                raw = rec(arg0)
            decoded_b = _decode_base64_payload(raw, fn_canon) if raw is not None else None
            if decoded_b is not None:
                try:
                    return decoded_b.decode("utf-8")
                except UnicodeDecodeError:
                    return None
            return None
        if isinstance(fn, ast.Attribute) and fn.attr == "decode" and len(node.args) <= 1 and not node.keywords:
            b_val = _eval_static_bytes(
                fn.value, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes
            )
            if b_val is not None:
                enc = rec(node.args[0]) if node.args else "utf-8"
                if enc:
                    try:
                        return b_val.decode(enc)
                    except (LookupError, UnicodeDecodeError):
                        return None
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
