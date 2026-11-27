#!/usr/bin/env python3
"""Structures d'état AST, collecte d'imports et utilitaires scalaires/`base64` pour `python_ast`."""

import ast
import base64
from dataclasses import dataclass, field
from pathlib import Path
import re
import string
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

from .constants import (
    OS_EXEC_LIST_FUNCS,
    OS_EXEC_VAR_FUNCS,
    OS_SHELL_FUNCS,
    OS_SPAWN_LIST_FUNCS,
    OS_SPAWN_VAR_FUNCS,
    RE_PATTERN_FUNCS,
    SUBPROCESS_CALL_FUNCS,
)

_BASE64_DECODE_FUNCS = frozenset(
    {"b64decode", "standard_b64decode", "urlsafe_b64decode", "decodebytes"}
)
_OBFUSCATED_DECODE_ATTRS = _BASE64_DECODE_FUNCS | frozenset(
    {
        "b32decode", "b16decode", "b85decode", "a85decode", "fromhex",
        "decompress", "a2b_base64", "a2b_hex", "unhexlify", "getdecoder",
    }
)
_OBFUSCATED_DECODE_MODS = frozenset({"base64", "codecs", "binascii", "zlib", "bz2", "lzma", "gzip"})
_OS_PATH_TERMINAL_FUNCS = frozenset(
    {
        "exists", "lexists", "isfile", "isdir", "islink", "ismount",
        "getsize", "getmtime", "getctime", "getatime", "samefile", "sameopenfile",
    }
)
_OS_PATH_PROPAGATING_FUNCS = frozenset(
    {
        "join", "abspath", "realpath", "normpath", "normcase", "dirname", "basename",
        "split", "splitdrive", "splitext", "expanduser", "expandvars", "relpath",
        "commonpath", "commonprefix",
    }
)
_FORMATTER = string.Formatter()


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
    codecs_decode_fns: Dict[str, str] = field(default_factory=dict)
    sys_mod_names: Set[str] = field(default_factory=lambda: {"sys"})
    functools_mod_names: Set[str] = field(default_factory=lambda: {"functools"})
    partial_func_names: Set[str] = field(default_factory=set)
    operator_mod_names: Set[str] = field(default_factory=lambda: {"operator"})
    attrgetter_func_names: Set[str] = field(default_factory=set)
    methodcaller_func_names: Set[str] = field(default_factory=set)
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
    codecs_fns: Dict[str, str] = field(default_factory=dict)
    sys_mods: Set[str] = field(default_factory=lambda: {"sys"})
    functools_mods: Set[str] = field(default_factory=lambda: {"functools"})
    partial_fns: Set[str] = field(default_factory=set)
    operator_mods: Set[str] = field(default_factory=lambda: {"operator"})
    attrgetter_fns: Set[str] = field(default_factory=set)
    methodcaller_fns: Set[str] = field(default_factory=set)
    builtins_mods: Set[str] = field(default_factory=lambda: {"builtins", "__builtins__"})
    builtins_dicts: Set[str] = field(default_factory=set)
    os_dicts: Set[str] = field(default_factory=set)
    eval_exec_fns: Dict[str, str] = field(
        default_factory=lambda: {"eval": "eval", "exec": "exec", "compile": "compile"}
    )
    func_defs: Dict[str, List[ast.AST]] = field(default_factory=dict)
    class_attrs: Dict[str, Dict[str, ast.AST]] = field(default_factory=dict)
    class_methods: Dict[str, Dict[str, List[ast.AST]]] = field(default_factory=dict)
    var_class_inst: Dict[str, str] = field(default_factory=dict)
    cur_bytes: Dict[str, bytes] = field(default_factory=dict)
    bytes_alias_peers: Dict[str, Set[str]] = field(default_factory=dict)
    cur_ints: Dict[str, int] = field(default_factory=dict)
    var_nodes: Dict[str, ast.AST] = field(default_factory=dict)
    incomplete_vars: Set[str] = field(default_factory=set)
    obf_tainted: Set[str] = field(default_factory=set)
    shadowed_alts: Set[str] = field(default_factory=set)

    def clone(self) -> "_ScopeState":
        return _ScopeState(
            cur_str=dict(self.cur_str), cur_seq={k: list(v) for k, v in self.cur_seq.items()},
            subproc_mods=set(self.subproc_mods), os_mods=set(self.os_mods),
            rmtree_fns=set(self.rmtree_fns), os_shell_fns=set(self.os_shell_fns),
            subproc_fns=set(self.subproc_fns), base64_mods=set(self.base64_mods),
            base64_fns=dict(self.base64_fns), codecs_mods=set(self.codecs_mods),
            codecs_fns=dict(self.codecs_fns), sys_mods=set(self.sys_mods),
            functools_mods=set(self.functools_mods), partial_fns=set(self.partial_fns),
            operator_mods=set(self.operator_mods), attrgetter_fns=set(self.attrgetter_fns),
            methodcaller_fns=set(self.methodcaller_fns),
            builtins_mods=set(self.builtins_mods), builtins_dicts=set(self.builtins_dicts),
            os_dicts=set(self.os_dicts),
            eval_exec_fns=dict(self.eval_exec_fns),
            func_defs={k: list(v) for k, v in self.func_defs.items()},
            class_attrs={k: dict(v) for k, v in self.class_attrs.items()},
            class_methods={k: {mk: list(mv) for mk, mv in v.items()} for k, v in self.class_methods.items()},
            var_class_inst=dict(self.var_class_inst),
            cur_bytes=dict(self.cur_bytes),
            bytes_alias_peers={k: set(v) for k, v in self.bytes_alias_peers.items()},
            cur_ints=dict(self.cur_ints), var_nodes=dict(self.var_nodes),
            incomplete_vars=set(self.incomplete_vars), obf_tainted=set(self.obf_tainted),
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
    env_dict_vars: Set[str] = field(default_factory=set)
    call_depth: int = 0
    active_call_fns: Set[int] = field(default_factory=set)
    fn_call_cb: Optional[Callable[..., Tuple[List[str], bool]]] = None

    def record_alt_str(self, name: str, val: str) -> None:
        bucket = self.alt_str.setdefault(name, [])
        if val not in bucket and len(bucket) < 8: bucket.append(val)

    def record_alt_bytes(self, name: str, val: bytes) -> None:
        bucket = self.alt_bytes.setdefault(name, [])
        if val not in bucket and len(bucket) < 8: bucket.append(val)

    def record_alt_seq(self, name: str, val: List[Optional[str]]) -> None:
        bucket = self.alt_seq.setdefault(name, [])
        snap = list(val)
        if snap not in bucket and len(bucket) < 8: bucket.append(snap)

    def add_resolved_str(self, node_key: ast.AST, val: str) -> None:
        bucket = self.resolved_expr_strs.setdefault(node_key, [])
        if val not in bucket and len(bucket) < 8: bucket.append(val)


def _ast_subscript_slice(node: ast.Subscript) -> ast.AST:
    idx_cls = getattr(ast, "Index", None)
    return node.slice.value if (idx_cls is not None and isinstance(node.slice, idx_cls)) else node.slice


def _decode_bytes_text(raw_b: bytes) -> str:
    try: return raw_b.decode("utf-8")
    except UnicodeDecodeError: return raw_b.decode("latin-1")


def _is_named_mod_expr(node: ast.AST, target_mod: str, mod_aliases: Set[str]) -> bool:
    if isinstance(node, ast.Name): return node.id in mod_aliases
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == target_mod:
        fn = node.func
        return (isinstance(fn, ast.Name) and fn.id == "__import__") or (isinstance(fn, ast.Attribute) and fn.attr == "import_module")
    return False


def _match_base64_decode_call(
    node: ast.Call, base64_mod_names: Optional[Set[str]] = None, base64_decode_fns: Optional[Dict[str, str]] = None,
) -> Optional[Tuple[str, ast.AST]]:
    mods, fns, fn = base64_mod_names or {"base64"}, base64_decode_fns or {}, node.func
    fn_canon: Optional[str] = None
    if isinstance(fn, ast.Name) and fn.id in fns: fn_canon = fns[fn.id]
    elif isinstance(fn, ast.Attribute) and fn.attr in _BASE64_DECODE_FUNCS and _is_named_mod_expr(fn.value, "base64", mods): fn_canon = fn.attr
    elif isinstance(fn, ast.Call) and isinstance(fn.func, ast.Name) and fn.func.id == "getattr" and len(fn.args) >= 2 and isinstance(fn.args[1], ast.Constant) and fn.args[1].value in _BASE64_DECODE_FUNCS and _is_named_mod_expr(fn.args[0], "base64", mods):
        fn_canon = str(fn.args[1].value)
    if fn_canon is None: return None
    kw = {k.arg: k.value for k in node.keywords if k.arg is not None}
    arg0 = node.args[0] if node.args else kw.get("s")
    if arg0 is None or len(node.args) > 1 or any(k not in ("s", "validate") for k in kw): return None
    return fn_canon, arg0


def _decode_base64_payload(raw: object, fn_canon: str) -> Optional[bytes]:
    if isinstance(raw, str):
        try: raw_b = raw.encode("ascii")
        except UnicodeEncodeError: return None
    elif isinstance(raw, bytes): raw_b = raw
    else: return None
    decoder = getattr(base64, fn_canon, None)
    if decoder is None: return None
    try: return decoder(raw_b)
    except (ValueError, TypeError): return None


def _eval_static_int(
    node: Optional[ast.AST], env_ints: Optional[Dict[str, int]] = None, const_vars: Optional[Dict[str, str]] = None, _depth: int = 0,
) -> Optional[int]:
    """Évalue une expression entière statique AST (littéraux, opérateurs arithmétiques/bit-à-bit, `ord`)."""
    if node is None or _depth > 10: return None
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool): return node.value
    if isinstance(node, ast.Name) and env_ints and node.id in env_ints: return env_ints[node.id]
    if isinstance(node, ast.UnaryOp):
        val = _eval_static_int(node.operand, env_ints, const_vars, _depth + 1)
        if val is None: return None
        if isinstance(node.op, ast.USub): return -val
        if isinstance(node.op, ast.UAdd): return val
        if isinstance(node.op, ast.Invert): return ~val
        return None
    if isinstance(node, ast.BinOp):
        left, right = _eval_static_int(node.left, env_ints, const_vars, _depth + 1), _eval_static_int(node.right, env_ints, const_vars, _depth + 1)
        if left is None or right is None: return None
        ops = {ast.Add: left + right, ast.Sub: left - right, ast.BitXor: left ^ right, ast.BitAnd: left & right, ast.BitOr: left | right}
        if type(node.op) in ops: return ops[type(node.op)]
        if isinstance(node.op, ast.Mult): return left * right if abs(left) <= 100000 and abs(right) <= 100000 else None
        if isinstance(node.op, ast.FloorDiv): return left // right if right != 0 else None
        if isinstance(node.op, ast.Mod): return left % right if right != 0 else None
        if isinstance(node.op, ast.LShift): return left << right if 0 <= right <= 32 else None
        if isinstance(node.op, ast.RShift): return left >> right if 0 <= right <= 32 else None
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ord" and len(node.args) == 1 and not node.keywords:
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and len(arg.value) == 1: return ord(arg.value)
        if isinstance(arg, ast.Name) and const_vars and arg.id in const_vars and len(const_vars[arg.id]) == 1: return ord(const_vars[arg.id])
    return None


def _eval_slice_bounds(slice_node: ast.Slice) -> Optional[slice]:
    lo = _eval_static_int(slice_node.lower) if slice_node.lower is not None else None
    hi = _eval_static_int(slice_node.upper) if slice_node.upper is not None else None
    st = _eval_static_int(slice_node.step) if slice_node.step is not None else None
    if (slice_node.lower is None or lo is not None) and (slice_node.upper is None or hi is not None) and (slice_node.step is None or (st is not None and st != 0)):
        return slice(lo, hi, st)
    return None


def _apply_format_conversion(val: Union[str, int, float, bool], conversion: int) -> Optional[Union[str, int, float, bool]]:
    return { -1: val, 115: str(val), 114: repr(val), 97: ascii(val) }.get(conversion)


def _is_safe_format_template(tpl: str) -> bool:
    try:
        for _, field_name, format_spec, conversion in _FORMATTER.parse(tpl):
            if field_name is not None and ("." in field_name or "[" in field_name): return False
            if format_spec and len(format_spec) > 32: return False
            if conversion not in (None, "s", "r", "a"): return False
    except ValueError:
        return False
    return True


def _collect_python_imports(tree: ast.Module, context_label: str) -> Tuple[Optional[Tuple[str, str]], _PyImports]:
    """Collecte les alias importés et noms shadowés dans l'AST."""
    imp = _PyImports()
    symbol_sources: Dict[str, str] = {
        "os": "mod:os", "posix": "mod:posix", "nt": "mod:nt", "re": "mod:re",
        "pathlib": "mod:pathlib", "json": "mod:json", "posixpath": "mod:os.path", "ntpath": "mod:os.path",
        "Path": "pathlib:Path", "PurePath": "pathlib:PurePath", "PosixPath": "pathlib:PosixPath", "WindowsPath": "pathlib:WindowsPath",
    }
    reserved_builtins = frozenset({"open", "print", "len", "compile", "str", "repr", "sorted", "list", "tuple", "set", "reversed", "enumerate", "zip"})
    shadow_mod_sets = {
        "subprocess": imp.subprocess_mod_names, "shlex": imp.shlex_mod_names, "shutil": imp.shutil_mod_names,
        "importlib": imp.importlib_mod_names, "base64": imp.base64_mod_names, "codecs": imp.codecs_mod_names,
        "builtins": imp.builtins_mod_names, "sys": imp.sys_mod_names,
        "functools": imp.functools_mod_names, "operator": imp.operator_mod_names,
    }
    os_func_sets = (
        (OS_SHELL_FUNCS, imp.os_shell_func_names), (OS_EXEC_LIST_FUNCS, imp.os_exec_list_names),
        (OS_EXEC_VAR_FUNCS, imp.os_exec_var_names), (OS_SPAWN_LIST_FUNCS, imp.os_spawn_list_names), (OS_SPAWN_VAR_FUNCS, imp.os_spawn_var_names),
    )

    def _record_import_source(bound: str, source_tag: str) -> None:
        if bound in reserved_builtins: imp.shadowed_names.add(bound); return
        prev = symbol_sources.get(bound)
        if prev is not None and prev != source_tag: imp.shadowed_names.add(bound)
        else: symbol_sources[bound] = source_tag

    def _mark_target_shadowed(target: ast.AST) -> None:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)): imp.shadowed_names.add(sub.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if alias.name in ("os", "posix", "nt"): imp.os_mod_names.add(bound); _record_import_source(bound, f"mod:{alias.name}")
                elif alias.name in shadow_mod_sets: shadow_mod_sets[alias.name].add(bound); imp.shadowed_names.add(bound)
                elif alias.name in ("re", "pathlib", "json"): getattr(imp, f"{alias.name}_mod_names").add(bound); _record_import_source(bound, f"mod:{alias.name}")
                elif alias.name in ("posixpath", "ntpath", "os.path"):
                    mb = alias.asname or alias.name; imp.os_path_mod_names.add(mb); _record_import_source(mb, "mod:os.path")
                else: imp.shadowed_names.add(bound)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*": imp.has_wildcard_import = True; continue
                bound = alias.asname or alias.name
                if node.module in ("os", "posix", "nt"):
                    if alias.name in ("environ", "environb"): return ("deny", f"Import de `os.environ` dans `{context_label}` bloqué."), imp
                    os_tgt = next((t for s, t in os_func_sets if alias.name in s), None)
                    if os_tgt is not None: os_tgt.add(bound); imp.shadowed_names.add(bound)
                    elif alias.name == "path": imp.os_path_mod_names.add(bound); _record_import_source(bound, "mod:os.path")
                    elif alias.name in ("stat", "lstat", "access", "readlink"): imp.os_readonly_term_fns.add(bound); _record_import_source(bound, f"os:{alias.name}")
                    elif alias.name in ("fspath", "listdir", "scandir", "walk"): imp.os_readonly_prop_fns.add(bound); _record_import_source(bound, f"os:{alias.name}")
                    else: imp.shadowed_names.add(bound)
                elif node.module in ("os.path", "posixpath", "ntpath"):
                    if alias.name in _OS_PATH_TERMINAL_FUNCS: imp.os_readonly_term_fns.add(bound); _record_import_source(bound, f"os.path:{alias.name}")
                    elif alias.name in _OS_PATH_PROPAGATING_FUNCS: imp.os_readonly_prop_fns.add(bound); _record_import_source(bound, f"os.path:{alias.name}")
                    else: imp.shadowed_names.add(bound)
                elif node.module == "pathlib" and alias.name in ("Path", "PurePath", "PosixPath", "WindowsPath"):
                    imp.path_ctor_names.add(bound); _record_import_source(bound, f"pathlib:{alias.name}")
                elif node.module == "json" and alias.name in ("load", "loads"):
                    imp.json_load_fns.add(bound); _record_import_source(bound, f"json:{alias.name}")
                elif node.module == "re" and alias.name in RE_PATTERN_FUNCS:
                    imp.re_func_names.add(bound)
                    if bound == "compile": imp.eval_exec_fns.pop("compile", None)
                    _record_import_source(bound, f"re:{alias.name}")
                else:
                    mod_fn_sets = {
                        "shutil": ({"rmtree"}, imp.rmtree_func_names), "subprocess": (SUBPROCESS_CALL_FUNCS, imp.subprocess_func_names),
                        "shlex": ({"split"}, imp.shlex_split_names), "importlib": ({"import_module"}, imp.import_module_fns),
                        "functools": ({"partial"}, imp.partial_func_names),
                        "operator": ({"attrgetter"}, imp.attrgetter_func_names),
                    }
                    if node.module == "operator" and alias.name == "methodcaller":
                        imp.methodcaller_func_names.add(bound)
                    mod_fn_dicts = {"base64": (_BASE64_DECODE_FUNCS, imp.base64_decode_fns), "codecs": ({"decode", "encode", "getdecoder", "lookup"}, imp.codecs_decode_fns), "builtins": ({"eval", "exec", "compile"}, imp.eval_exec_fns)}
                    if node.module in mod_fn_sets and alias.name in mod_fn_sets[node.module][0]: mod_fn_sets[node.module][1].add(bound)
                    elif node.module in mod_fn_dicts and alias.name in mod_fn_dicts[node.module][0]: mod_fn_dicts[node.module][1][bound] = alias.name
                    imp.shadowed_names.add(bound)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets: _mark_target_shadowed(tgt)
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Lambda):
                imp.func_defs.setdefault(node.targets[0].id, []).append(node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _mark_target_shadowed(node.target)
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and isinstance(node.value, ast.Lambda):
                imp.func_defs.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)): _mark_target_shadowed(node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None: _mark_target_shadowed(node.optional_vars)
        elif isinstance(node, (ast.NamedExpr, ast.ClassDef)): imp.shadowed_names.add(node.target.id if isinstance(node, ast.NamedExpr) else node.name)
        elif isinstance(node, (ast.ExceptHandler, ast.arg)):
            nm = node.name if isinstance(node, ast.ExceptHandler) else node.arg
            if nm: imp.shadowed_names.add(nm)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            imp.shadowed_names.add(node.name); imp.func_defs.setdefault(node.name, []).append(node)
            if not (node.args.args or node.args.posonlyargs or node.args.kwonlyargs):
                rets = [s.value for s in ast.walk(node) if isinstance(s, ast.Return) and s.value is not None]
                if rets: imp.func_returns.setdefault(node.name, []).extend(rets[:4])
    return None, imp


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


def _merge_scope_branch(dst: _ScopeState, src: _ScopeState, state: Optional[_PyAstState] = None) -> None:
    """Fusionne l'état d'une branche `if`/`try` dans `dst` (et `state.alt_*` si top-level)."""
    for attr in (
        "subproc_mods", "os_mods", "rmtree_fns", "os_shell_fns", "subproc_fns",
        "base64_mods", "codecs_mods", "sys_mods", "functools_mods", "partial_fns",
        "operator_mods", "attrgetter_fns", "methodcaller_fns", "builtins_mods", "builtins_dicts",
        "os_dicts", "incomplete_vars", "obf_tainted", "shadowed_alts",
    ):
        getattr(dst, attr).update(getattr(src, attr))
    for d_attr in ("base64_fns", "codecs_fns", "eval_exec_fns", "func_defs", "var_nodes", "var_class_inst", "cur_ints"):
        getattr(dst, d_attr).update(getattr(src, d_attr))
    for cls_n, attrs_d in src.class_attrs.items():
        dst.class_attrs.setdefault(cls_n, {}).update(attrs_d)
    for cls_n, meths_d in src.class_methods.items():
        dst.class_methods.setdefault(cls_n, {}).update(meths_d)
    for m_var, peers in src.bytes_alias_peers.items():
        merged = dst.bytes_alias_peers.get(m_var, set()) | peers
        for p_v in merged:
            dst.bytes_alias_peers[p_v] = set(merged)
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


_BUILTIN_CTORS = frozenset({"bytes", "bytearray", "memoryview", "chr"})
_PCT_WIDTH_RE = re.compile(r"%(?:\([^)]+\))?[#0\- +]*(\d+)")


def _resolve_builtin_ctor_name(
    fn: ast.AST,
    scope: Optional[_ScopeState] = None,
    var_nodes: Optional[Dict[str, ast.AST]] = None,
    _depth: int = 0,
    _seen: Optional[Set[str]] = None,
) -> Optional[str]:
    """Résout un constructeur builtin (`bytes`, `bytearray`, `memoryview`, `chr`) via alias `var_nodes` ou `builtins`."""
    if _depth > 6:
        return None
    vmap = var_nodes if var_nodes is not None else (scope.var_nodes if scope is not None else None)
    seen = set(_seen or ())
    if isinstance(fn, ast.NamedExpr):
        return _resolve_builtin_ctor_name(fn.value, scope, vmap, _depth + 1, seen)
    if isinstance(fn, ast.Name):
        if vmap and fn.id in vmap and fn.id not in seen:
            seen.add(fn.id)
            res = _resolve_builtin_ctor_name(vmap[fn.id], scope, vmap, _depth + 1, seen)
            if res is not None:
                return res
        if fn.id in _BUILTIN_CTORS and (not vmap or fn.id not in vmap):
            return fn.id
        return None
    if isinstance(fn, ast.Attribute) and fn.attr in _BUILTIN_CTORS:
        bi_mods = scope.builtins_mods if scope else {"builtins", "__builtins__"}
        bi_dicts = scope.builtins_dicts if scope else set()
        if isinstance(fn.value, ast.Name) and (fn.value.id in bi_mods or fn.value.id in bi_dicts):
            return fn.attr
    if isinstance(fn, ast.Subscript):
        sl = _ast_subscript_slice(fn)
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str) and sl.value in _BUILTIN_CTORS:
            if isinstance(fn.value, ast.Name) and (
                fn.value.id in (scope.builtins_dicts if scope else set())
                or fn.value.id in (scope.builtins_mods if scope else {"__builtins__"})
            ):
                return sl.value
    return None


def _eval_str_percent_format(
    tpl: str,
    right_node: ast.AST,
    eval_scalar_fn: Callable[[ast.AST], Tuple[bool, Union[str, int, float, bool, None]]],
    eval_str_fn: Callable[[ast.AST], Optional[str]],
    var_nodes: Optional[Dict[str, ast.AST]] = None,
) -> Optional[str]:
    """Évalue statiquement le formatage `%` sûr (`'...' % (...)` ou `'...' % {...}` ou `'...' % val)."""
    if len(tpl) > 8192 or any(int(m.group(1)) > 4096 for m in _PCT_WIDTH_RE.finditer(tpl)):
        return None
    r_node = var_nodes.get(right_node.id, right_node) if (isinstance(right_node, ast.Name) and var_nodes) else right_node
    arg_obj: object
    if isinstance(r_node, ast.Tuple):
        if any(isinstance(e, ast.Starred) for e in r_node.elts):
            return None
        vals: List[object] = []
        for e in r_node.elts:
            ok, v = eval_scalar_fn(e)
            if not ok or v is None:
                return None
            vals.append(v)
        arg_obj = tuple(vals)
    elif isinstance(r_node, ast.Dict):
        if any(k is None for k in r_node.keys):
            return None
        d_obj: Dict[str, object] = {}
        for k_n, v_n in zip(r_node.keys, r_node.values):
            ks = eval_str_fn(k_n) if k_n is not None else None
            ok, kv = eval_scalar_fn(v_n)
            if ks is None or not ok or kv is None:
                return None
            d_obj[ks] = kv
        arg_obj = d_obj
    elif isinstance(r_node, ast.Call) and isinstance(r_node.func, ast.Name) and r_node.func.id == "dict" and not r_node.args and not any(k.arg is None for k in r_node.keywords):
        d_obj = {}
        for kw in r_node.keywords:
            if kw.arg is None:
                return None
            ok, kv = eval_scalar_fn(kw.value)
            if not ok or kv is None:
                return None
            d_obj[kw.arg] = kv
        arg_obj = d_obj
    else:
        ok, v = eval_scalar_fn(right_node)
        if not ok or v is None:
            return None
        arg_obj = v
    try:
        res = tpl % arg_obj
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    return res if isinstance(res, str) and len(res) <= 16384 else None


def _eval_str_translate(
    base: str,
    arg_node: ast.AST,
    eval_str_fn: Callable[[ast.AST], Optional[str]],
    eval_int_fn: Callable[[ast.AST], Optional[int]],
    var_nodes: Optional[Dict[str, ast.AST]] = None,
) -> Optional[str]:
    """Évalue statiquement `.translate(table)` pour dictionnaires et `str.maketrans(...)`."""
    t_node = var_nodes.get(arg_node.id, arg_node) if (isinstance(arg_node, ast.Name) and var_nodes) else arg_node
    table: Optional[Dict[int, Union[int, str, None]]] = None
    if isinstance(t_node, ast.Call) and isinstance(t_node.func, ast.Attribute) and t_node.func.attr == "maketrans" and not t_node.keywords:
        recv = t_node.func.value
        is_str_mt = (isinstance(recv, ast.Name) and recv.id == "str") or isinstance(recv, ast.Constant)
        if is_str_mt and len(t_node.args) in (2, 3):
            s_args = [eval_str_fn(a) for a in t_node.args]
            if all(isinstance(s, str) for s in s_args):
                try:
                    table = str.maketrans(*[s for s in s_args if s is not None])  # type: ignore[arg-type]
                except ValueError:
                    return None
        elif is_str_mt and len(t_node.args) == 1:
            t_node = var_nodes.get(t_node.args[0].id, t_node.args[0]) if (isinstance(t_node.args[0], ast.Name) and var_nodes) else t_node.args[0]
    if table is None and isinstance(t_node, ast.Dict) and not any(k is None for k in t_node.keys):
        tbl: Dict[int, Union[int, str, None]] = {}
        for k_n, v_n in zip(t_node.keys, t_node.values):
            if k_n is None:
                return None
            k_int = eval_int_fn(k_n)
            if k_int is None:
                k_str = eval_str_fn(k_n)
                if k_str is not None and len(k_str) == 1:
                    k_int = ord(k_str)
            if k_int is None:
                return None
            if isinstance(v_n, ast.Constant) and v_n.value is None:
                tbl[k_int] = None
            else:
                v_int = eval_int_fn(v_n)
                if v_int is not None and 0 <= v_int <= 0x10FFFF:
                    tbl[k_int] = v_int
                else:
                    v_str = eval_str_fn(v_n)
                    if v_str is None:
                        return None
                    tbl[k_int] = v_str
        table = tbl
    if table is None:
        return None
    try:
        return base.translate(table)
    except (ValueError, TypeError):
        return None


def _eval_str_method_transform(
    base: str, attr: str, args: List[ast.AST], eval_str_fn: Callable[[ast.AST], Optional[str]]
) -> Optional[str]:
    """Évalue statiquement `.strip`/`.lstrip`/`.rstrip`/`.removeprefix`/`.removesuffix`/casse."""
    if attr in ("lower", "upper", "swapcase", "casefold", "title", "capitalize") and not args:
        return str(getattr(base, attr)())
    if attr in ("strip", "lstrip", "rstrip") and len(args) <= 1:
        if not args or (isinstance(args[0], ast.Constant) and args[0].value is None):
            return str(getattr(base, attr)())
        chars = eval_str_fn(args[0])
        return str(getattr(base, attr)(chars)) if chars is not None else None
    if attr in ("removeprefix", "removesuffix") and len(args) == 1:
        aff = eval_str_fn(args[0])
        return str(getattr(base, attr)(aff)) if aff is not None else None
    return None
