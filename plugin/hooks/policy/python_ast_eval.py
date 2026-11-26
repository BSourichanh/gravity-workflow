#!/usr/bin/env python3
"""Évaluation statique AST (chaînes, séquences, octets, compréhensions, `.format`/`.replace`) pour `python_ast`."""

import ast
import shlex
from typing import Dict, List, Optional, Set, Tuple, Union

from .python_ast_state import (
    _BASE64_DECODE_FUNCS,
    _OBFUSCATED_DECODE_ATTRS,
    _OBFUSCATED_DECODE_MODS,
    _OS_PATH_PROPAGATING_FUNCS,
    _OS_PATH_TERMINAL_FUNCS,
    _PyAstState,
    _PyImports,
    _ScopeState,
    _apply_format_conversion,
    _ast_subscript_slice,
    _collect_python_imports,
    _decode_base64_payload,
    _decode_bytes_text,
    _eval_slice_bounds,
    _eval_static_int,
    _eval_str_method_transform,
    _eval_str_percent_format,
    _eval_str_translate,
    _is_named_mod_expr,
    _is_safe_format_template,
    _match_base64_decode_call,
    _resolve_builtin_ctor_name,
)

_MAX_EVAL_ITER = 4096


def _eval_static_scalar(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Optional[Dict[str, List[Optional[str]]]] = None,
    _depth: int = 0,
    func_returns: Optional[Dict[str, List[ast.AST]]] = None,
    base64_mod_names: Optional[Set[str]] = None,
    base64_decode_fns: Optional[Dict[str, str]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
    var_nodes: Optional[Dict[str, ast.AST]] = None,
    env_ints: Optional[Dict[str, int]] = None,
) -> Tuple[bool, Union[str, int, float, bool, None]]:
    """Évalue un scalaire statique (`str`, `int`, `float`, `bool`) pour f-strings et `.format`."""
    s_val = _eval_static_str(
        node, const_vars, const_seq_vars, _depth + 1, func_returns,
        base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=var_nodes, env_ints=env_ints,
    )
    if s_val is not None:
        return True, s_val
    i_val = _eval_static_int(node, env_ints, const_vars, _depth + 1)
    if i_val is not None:
        return True, i_val
    if isinstance(node, ast.Constant) and isinstance(node.value, (bool, float)):
        return True, node.value
    return False, None


def _eval_static_int_list(
    node: ast.AST,
    const_vars: Optional[Dict[str, str]] = None,
    var_nodes: Optional[Dict[str, ast.AST]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
    env_ints: Optional[Dict[str, int]] = None,
    _depth: int = 0,
    _seen_vars: Optional[Set[str]] = None,
) -> Optional[List[int]]:
    """Évalue statiquement une séquence d'entiers (listes, `+`, slices, `ListComp`/`GeneratorExp`, `range`, `bytes`, `var_nodes`)."""
    if _depth > 8:
        return None
    seen = set(_seen_vars or ())
    rec = lambda n, env=env_ints: _eval_static_int_list(n, const_vars, var_nodes, cur_bytes, env, _depth + 1, seen)
    if isinstance(node, ast.NamedExpr):
        return rec(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
        return list(node.value) if len(node.value) <= _MAX_EVAL_ITER else None
    if isinstance(node, ast.Name):
        if cur_bytes and node.id in cur_bytes:
            return list(cur_bytes[node.id]) if len(cur_bytes[node.id]) <= _MAX_EVAL_ITER else None
        if var_nodes and node.id in var_nodes and node.id not in seen:
            seen.add(node.id)
            return _eval_static_int_list(var_nodes[node.id], const_vars, var_nodes, cur_bytes, env_ints, _depth + 1, seen)
        return None
    if isinstance(node, (ast.List, ast.Tuple)):
        out: List[int] = []
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                sub = rec(elt.value)
                if sub is None:
                    return None
                out.extend(sub)
            else:
                iv = _eval_static_int(elt, env_ints, const_vars, _depth + 1)
                if iv is None:
                    return None
                out.append(iv)
            if len(out) > _MAX_EVAL_ITER:
                return None
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = rec(node.left), rec(node.right)
        return (left + right) if (left is not None and right is not None and len(left) + len(right) <= _MAX_EVAL_ITER) else None
    if isinstance(node, ast.Subscript):
        sl_node = _ast_subscript_slice(node)
        if isinstance(sl_node, ast.Slice):
            seq, sl = rec(node.value), _eval_slice_bounds(sl_node)
            return list(seq[sl]) if (seq is not None and sl is not None) else None
        return None
    if isinstance(node, (ast.ListComp, ast.GeneratorExp)) and len(node.generators) == 1:
        gen = node.generators[0]
        if not gen.ifs and isinstance(gen.target, ast.Name):
            src_ints = rec(gen.iter)
            if src_ints is not None and len(src_ints) <= _MAX_EVAL_ITER:
                comp_out: List[int] = []
                base_env = dict(env_ints or {})
                for iv in src_ints:
                    base_env[gen.target.id] = iv
                    ev = _eval_static_int(node.elt, base_env, const_vars, _depth + 1)
                    if ev is None:
                        return None
                    comp_out.append(ev)
                return comp_out
            iter_str = _eval_static_str(gen.iter, const_vars or {}, None, _depth + 1, cur_bytes=cur_bytes, var_nodes=var_nodes)
            if iter_str is not None and len(iter_str) <= _MAX_EVAL_ITER:
                comp_out = []
                base_str = dict(const_vars or {})
                for ch in iter_str:
                    base_str[gen.target.id] = ch
                    ev = _eval_static_int(node.elt, env_ints, base_str, _depth + 1)
                    if ev is None:
                        return None
                    comp_out.append(ev)
                return comp_out
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        if node.func.id in ("list", "tuple") and len(node.args) == 1:
            return rec(node.args[0])
        if node.func.id == "reversed" and len(node.args) == 1:
            seq = rec(node.args[0])
            return list(reversed(seq)) if seq is not None else None
        if node.func.id == "range" and 1 <= len(node.args) <= 3:
            r_args = [_eval_static_int(a, env_ints, const_vars, _depth + 1) for a in node.args]
            if all(a is not None for a in r_args):
                try:
                    rng = range(*[a for a in r_args if a is not None])
                    return list(rng) if len(rng) <= _MAX_EVAL_ITER else None
                except ValueError:
                    return None
    return None


def _eval_static_seq(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Optional[Dict[str, List[Optional[str]]]] = None,
    _depth: int = 0,
    shlex_mod_names: Optional[Set[str]] = None,
    shlex_split_names: Optional[Set[str]] = None,
    func_returns: Optional[Dict[str, List[ast.AST]]] = None,
    var_nodes: Optional[Dict[str, ast.AST]] = None,
    cur_bytes: Optional[Dict[str, bytes]] = None,
    env_ints: Optional[Dict[str, int]] = None,
) -> Optional[List[Optional[str]]]:
    """Évalue statiquement une expression AST vers une liste de chaînes."""
    if _depth > 8:
        return None
    rec = lambda n: _eval_static_seq(
        n, const_vars, const_seq_vars, _depth + 1, shlex_mod_names, shlex_split_names, func_returns, var_nodes, cur_bytes, env_ints
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
                items.append(_eval_static_str(elt, const_vars, const_seq_vars, _depth + 1, func_returns, cur_bytes=cur_bytes, var_nodes=var_nodes))
        return items
    if isinstance(node, ast.Subscript):
        slice_node = _ast_subscript_slice(node)
        if isinstance(slice_node, ast.Slice):
            seq, sl = rec(node.value), _eval_slice_bounds(slice_node)
            if seq is not None and sl is not None:
                return list(seq[sl])
    if isinstance(node, (ast.ListComp, ast.GeneratorExp)) and len(node.generators) == 1:
        gen = node.generators[0]
        if not gen.ifs and isinstance(gen.target, ast.Name):
            src_seq = rec(gen.iter)
            if src_seq is not None and any(item is not None for item in src_seq):
                if isinstance(node.elt, ast.Name) and node.elt.id == gen.target.id:
                    return list(src_seq)
                comp_out: List[Optional[str]] = []
                for item in src_seq:
                    if item is None:
                        comp_out.append(None)
                    else:
                        local_str = dict(const_vars); local_str[gen.target.id] = item
                        comp_out.append(_eval_static_str(node.elt, local_str, const_seq_vars, _depth + 1, func_returns, cur_bytes=cur_bytes, var_nodes=var_nodes))
                return comp_out
            src_ints = _eval_static_int_list(gen.iter, const_vars, var_nodes, cur_bytes, env_ints, _depth=_depth + 1)
            if src_ints is not None:
                return [
                    _eval_static_str(node.elt, const_vars, const_seq_vars, _depth + 1, func_returns, cur_bytes=cur_bytes, var_nodes=var_nodes, env_ints={**(env_ints or {}), gen.target.id: iv})
                    for iv in src_ints
                ]
            if src_seq is not None:
                return list(src_seq)
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
        if isinstance(fn, ast.Name) and fn.id == "map" and len(node.args) == 2 and not node.keywords and _resolve_builtin_ctor_name(node.args[0], var_nodes=var_nodes) == "chr":
            src_ints = _eval_static_int_list(node.args[1], const_vars, var_nodes, cur_bytes, env_ints, _depth=_depth + 1)
            if src_ints is not None and all(0 <= iv <= 0x10FFFF for iv in src_ints):
                return [chr(iv) for iv in src_ints]
        if isinstance(fn, ast.Name) and func_returns and fn.id in func_returns and not node.args and not node.keywords:
            for ret_expr in func_returns[fn.id]:
                seq = rec(ret_expr)
                if seq is not None:
                    return seq
        is_shlex_split = (isinstance(fn, ast.Name) and shlex_split_names is not None and fn.id in shlex_split_names) or (
            isinstance(fn, ast.Attribute) and fn.attr == "split" and isinstance(fn.value, ast.Name) and fn.value.id in (shlex_mod_names or {"shlex"})
        )
        if is_shlex_split and len(node.args) >= 1:
            raw_s = _eval_static_str(node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns)
            if raw_s is not None:
                try: return list(shlex.split(raw_s))
                except ValueError: return raw_s.split()
        if isinstance(fn, ast.Attribute) and fn.attr == "split" and len(node.args) <= 1 and not node.keywords:
            recv = _eval_static_str(fn.value, const_vars, const_seq_vars, _depth + 1, func_returns)
            if recv is not None:
                if not node.args: return list(recv.split())
                sep = _eval_static_str(node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns)
                if sep: return list(recv.split(sep))
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
    *,
    var_nodes: Optional[Dict[str, ast.AST]] = None,
    env_ints: Optional[Dict[str, int]] = None,
) -> Optional[bytes]:
    """Évalue statiquement une expression AST vers `bytes` (`bytes`/`bytearray`/`memoryview`, `+`, slices, `.join`, `.encode()`, `fromhex`, `base64`)."""
    if _depth > 8:
        return None
    rec_b = lambda n: _eval_static_bytes(n, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=var_nodes, env_ints=env_ints)
    rec_s = lambda n: _eval_static_str(n, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=var_nodes, env_ints=env_ints)
    if isinstance(node, ast.NamedExpr): return rec_b(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, bytes): return node.value
    if isinstance(node, ast.Name) and cur_bytes and node.id in cur_bytes: return cur_bytes[node.id]
    if isinstance(node, ast.Subscript):
        idx_node = _ast_subscript_slice(node)
        if isinstance(idx_node, ast.Slice):
            base_b, sl = rec_b(node.value), _eval_slice_bounds(idx_node)
            if base_b is not None and sl is not None: return base_b[sl]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = rec_b(node.left), rec_b(node.right)
        return (left + right) if (left is not None and right is not None) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        lb, rn = rec_b(node.left), _eval_static_int(node.right, env_ints, const_vars, _depth + 1)
        if lb is not None and rn is not None:
            return b"" if rn <= 0 else (lb * rn if len(lb) * rn <= 16384 else None)
        ln, rb = _eval_static_int(node.left, env_ints, const_vars, _depth + 1), rec_b(node.right)
        if ln is not None and rb is not None:
            return b"" if ln <= 0 else (rb * ln if len(rb) * ln <= 16384 else None)
    if isinstance(node, ast.Call):
        b64_match = _match_base64_decode_call(node, base64_mod_names, base64_decode_fns)
        if b64_match is not None:
            fn_canon, arg0 = b64_match
            raw: object = rec_b(arg0)
            if raw is None: raw = rec_s(arg0)
            return _decode_base64_payload(raw, fn_canon) if raw is not None else None
        fn = node.func
        ctor = _resolve_builtin_ctor_name(fn, var_nodes=var_nodes)
        if isinstance(fn, ast.Attribute) and fn.attr == "tobytes" and not node.args and not node.keywords: return rec_b(fn.value)
        if ctor == "memoryview" and len(node.args) == 1 and not node.keywords: return rec_b(node.args[0])
        if isinstance(fn, ast.Attribute) and fn.attr == "encode" and len(node.args) <= 1 and not node.keywords:
            s_val, enc = rec_s(fn.value), (rec_s(node.args[0]) if node.args else "utf-8")
            if s_val is not None and enc:
                try: return s_val.encode(enc)
                except (LookupError, UnicodeEncodeError): return None
        if isinstance(fn, ast.Attribute) and fn.attr == "join" and len(node.args) == 1 and not node.keywords:
            sep = rec_b(fn.value)
            arg0 = var_nodes[node.args[0].id] if (isinstance(node.args[0], ast.Name) and var_nodes and node.args[0].id in var_nodes) else node.args[0]
            if sep is not None and isinstance(arg0, (ast.List, ast.Tuple)):
                parts = [rec_b(e) for e in arg0.elts]
                if all(p is not None for p in parts): return sep.join(p for p in parts if p is not None)
        if ctor in ("bytes", "bytearray"):
            if not node.args and not node.keywords: return b""
            if len(node.args) == 1 and not node.keywords:
                int_list = _eval_static_int_list(node.args[0], const_vars, var_nodes, cur_bytes, env_ints=env_ints, _depth=_depth + 1)
                if int_list is not None:
                    return bytes(int_list) if all(0 <= iv <= 255 for iv in int_list) else None
                return rec_b(node.args[0])
            enc_n = node.args[1] if len(node.args) == 2 and not node.keywords else (
                node.keywords[0].value if len(node.args) == 1 and len(node.keywords) == 1 and node.keywords[0].arg == "encoding" else None
            )
            if enc_n is not None:
                s_val, enc = rec_s(node.args[0]), rec_s(enc_n)
                if s_val is not None and enc:
                    try: return s_val.encode(enc)
                    except (LookupError, UnicodeEncodeError): return None
        if isinstance(fn, ast.Attribute) and fn.attr == "fromhex" and _resolve_builtin_ctor_name(fn.value, var_nodes=var_nodes) in ("bytes", "bytearray") and len(node.args) == 1 and not node.keywords:
            hex_s = rec_s(node.args[0])
            if hex_s is not None:
                try: return bytes.fromhex(hex_s)
                except ValueError: return None
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
    *,
    var_nodes: Optional[Dict[str, ast.AST]] = None,
    env_ints: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    """Évalue statiquement une expression AST vers une chaîne (constantes, `+`, f-strings, `.format`, `.replace`, slices, `.join`, `chr`, `Path`, `base64`)."""
    if _depth > 8:
        return None
    rec = lambda n: _eval_static_str(
        n, const_vars, const_seq_vars, _depth + 1, func_returns,
        base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=var_nodes, env_ints=env_ints,
    )
    if isinstance(node, ast.NamedExpr): return rec(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, str): return node.value
    if isinstance(node, ast.Name) and node.id in const_vars: return const_vars[node.id]
    if isinstance(node, ast.Subscript):
        idx_node = _ast_subscript_slice(node)
        if isinstance(idx_node, ast.Slice):
            base_s, sl = rec(node.value), _eval_slice_bounds(idx_node)
            if base_s is not None and sl is not None: return base_s[sl]
        idx_val = _eval_static_int(idx_node, env_ints, const_vars, _depth + 1)
        if idx_val is not None:
            seq = _eval_static_seq(node.value, const_vars, const_seq_vars, _depth + 1, func_returns=func_returns, var_nodes=var_nodes, cur_bytes=cur_bytes)
            if seq is not None:
                try: return seq[idx_val]
                except IndexError: return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = rec(node.left), rec(node.right)
        return (left + right) if (left is not None and right is not None) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = rec(node.left), rec(node.right)
        return f"{left.rstrip('/\\')}/{right}" if (left is not None and right is not None) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        ls, rn = rec(node.left), _eval_static_int(node.right, env_ints, const_vars, _depth + 1)
        if ls is not None and rn is not None:
            return "" if rn <= 0 else (ls * rn if len(ls) * rn <= 16384 else None)
        ln, rs = _eval_static_int(node.left, env_ints, const_vars, _depth + 1), rec(node.right)
        if ln is not None and rs is not None:
            return "" if ln <= 0 else (rs * ln if len(rs) * ln <= 16384 else None)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        tpl = rec(node.left)
        if tpl is not None:
            return _eval_str_percent_format(
                tpl, node.right,
                lambda n: _eval_static_scalar(n, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes, env_ints),
                rec, var_nodes,
            )
    if isinstance(node, ast.JoinedStr):
        parts: List[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            elif isinstance(val, ast.FormattedValue):
                ok_v, raw_v = _eval_static_scalar(val.value, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes, env_ints)
                if not ok_v or raw_v is None: return None
                conv_v = _apply_format_conversion(raw_v, val.conversion)
                if conv_v is None: return None
                if val.format_spec is not None:
                    spec = rec(val.format_spec)
                    if spec is None or len(spec) > 32: return None
                    try: parts.append(format(conv_v, spec))
                    except (ValueError, TypeError): return None
                else:
                    parts.append(str(conv_v))
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.Call):
        fn = node.func
        b64_match = _match_base64_decode_call(node, base64_mod_names, base64_decode_fns)
        if b64_match is not None:
            fn_canon, arg0 = b64_match
            raw: object = _eval_static_bytes(arg0, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=var_nodes, env_ints=env_ints)
            if raw is None: raw = rec(arg0)
            decoded_b = _decode_base64_payload(raw, fn_canon) if raw is not None else None
            if decoded_b is not None:
                try: return decoded_b.decode("utf-8")
                except UnicodeDecodeError: return None
            return None
        if isinstance(fn, ast.Attribute) and fn.attr == "decode" and len(node.args) <= 1 and not node.keywords:
            b_val = _eval_static_bytes(fn.value, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes=var_nodes, env_ints=env_ints)
            if b_val is not None:
                enc = rec(node.args[0]) if node.args else "utf-8"
                if enc:
                    try: return b_val.decode(enc)
                    except (LookupError, UnicodeDecodeError): return None
        if isinstance(fn, ast.Attribute) and fn.attr == "format" and not any(isinstance(a, ast.Starred) for a in node.args) and not any(k.arg is None for k in node.keywords):
            tpl = rec(fn.value)
            if tpl is not None and _is_safe_format_template(tpl):
                pos_vals: List[object] = []; kw_vals: Dict[str, object] = {}; ok = True
                for a in node.args:
                    ok_a, av = _eval_static_scalar(a, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes, env_ints)
                    if not ok_a: ok = False; break
                    pos_vals.append(av)
                if ok:
                    for kw in node.keywords:
                        if kw.arg is None: ok = False; break
                        ok_k, kv = _eval_static_scalar(kw.value, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes, env_ints)
                        if not ok_k: ok = False; break
                        kw_vals[kw.arg] = kv
                if ok:
                    try: return tpl.format(*pos_vals, **kw_vals)
                    except (ValueError, KeyError, IndexError, TypeError): return None
        if isinstance(fn, ast.Attribute) and fn.attr == "format_map" and len(node.args) == 1 and not node.keywords:
            tpl = rec(fn.value)
            m_node = var_nodes[node.args[0].id] if (isinstance(node.args[0], ast.Name) and var_nodes and node.args[0].id in var_nodes) else node.args[0]
            if tpl is not None and _is_safe_format_template(tpl):
                map_vals: Dict[str, object] = {}; ok = False
                if isinstance(m_node, ast.Dict) and not any(k is None for k in m_node.keys):
                    ok = True
                    for k_n, v_n in zip(m_node.keys, m_node.values):
                        ks = rec(k_n) if k_n is not None else None
                        ok_v, kv = _eval_static_scalar(v_n, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes, env_ints)
                        if ks is None or not ok_v: ok = False; break
                        map_vals[ks] = kv
                elif isinstance(m_node, ast.Call) and isinstance(m_node.func, ast.Name) and m_node.func.id == "dict" and not m_node.args and not any(k.arg is None for k in m_node.keywords):
                    ok = True
                    for kw in m_node.keywords:
                        if kw.arg is None: ok = False; break
                        ok_v, kv = _eval_static_scalar(kw.value, const_vars, const_seq_vars, _depth + 1, func_returns, base64_mod_names, base64_decode_fns, cur_bytes, var_nodes, env_ints)
                        if not ok_v: ok = False; break
                        map_vals[kw.arg] = kv
                if ok:
                    try: return tpl.format_map(map_vals)
                    except (ValueError, KeyError, IndexError, TypeError): return None
        if isinstance(fn, ast.Attribute) and fn.attr == "replace" and len(node.args) in (2, 3) and not node.keywords:
            base, old_s, new_s = rec(fn.value), rec(node.args[0]), rec(node.args[1])
            if base is not None and old_s is not None and new_s is not None:
                if len(node.args) == 2: return base.replace(old_s, new_s)
                cnt = _eval_static_int(node.args[2], env_ints, const_vars, _depth + 1)
                if cnt is not None: return base.replace(old_s, new_s, cnt)
        if isinstance(fn, ast.Attribute) and fn.attr in ("strip", "lstrip", "rstrip", "removeprefix", "removesuffix", "lower", "upper", "swapcase", "casefold", "title", "capitalize") and not node.keywords:
            base = rec(fn.value)
            if base is not None:
                res = _eval_str_method_transform(base, fn.attr, node.args, rec)
                if res is not None: return res
        if isinstance(fn, ast.Attribute) and fn.attr == "translate" and len(node.args) == 1 and not node.keywords:
            base = rec(fn.value)
            if base is not None:
                res = _eval_str_translate(base, node.args[0], rec, lambda n: _eval_static_int(n, env_ints, const_vars, _depth + 1), var_nodes)
                if res is not None: return res
        if isinstance(fn, ast.Name) and func_returns and fn.id in func_returns and not node.args and not node.keywords:
            for ret_expr in func_returns[fn.id]:
                val = rec(ret_expr)
                if val is not None: return val
        if _resolve_builtin_ctor_name(fn, var_nodes=var_nodes) == "chr" and len(node.args) == 1 and not node.keywords:
            c_val = _eval_static_int(node.args[0], env_ints, const_vars, _depth + 1)
            return chr(c_val) if (c_val is not None and 0 <= c_val <= 0x10FFFF) else None
        if isinstance(fn, ast.Attribute) and fn.attr == "join" and len(node.args) == 1 and not node.keywords:
            sep = rec(fn.value)
            if sep is not None:
                seq_vals = _eval_static_seq(node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns=func_returns, var_nodes=var_nodes, cur_bytes=cur_bytes, env_ints=env_ints)
                if seq_vals is not None and all(v is not None for v in seq_vals):
                    return sep.join(v for v in seq_vals if v is not None)
        if (isinstance(fn, ast.Name) and fn.id in ("Path", "PurePath", "PosixPath", "WindowsPath")) or (
            isinstance(fn, ast.Attribute) and fn.attr in ("Path", "PurePath", "PosixPath", "WindowsPath", "join")
            and (fn.attr != "join" or (isinstance(fn.value, ast.Attribute) and fn.value.attr == "path") or (isinstance(fn.value, ast.Name) and fn.value.id in ("path", "posixpath", "ntpath")))
        ):
            if node.args and not node.keywords:
                segs = [rec(arg) for arg in node.args]
                if all(s is not None for s in segs):
                    return "/".join(s.replace("\\", "/") for s in segs if s is not None)
    return None


def _collect_static_bindings(
    tree: ast.Module, shlex_mod_names: Set[str], shlex_split_names: Set[str],
) -> Tuple[Dict[str, str], Dict[str, List[Optional[str]]], Dict[str, ast.AST]]:
    """Collecte les constantes chaînes et listes/tuples assignées une seule fois dans l'AST."""
    bind_counts: Dict[str, int] = {}; load_counts: Dict[str, int] = {}; mutated_vars: Set[str] = set()
    _bump = lambda name: bind_counts.__setitem__(name, bind_counts.get(name, 0) + 1) if name else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)): _bump(node.id)
            elif isinstance(node.ctx, ast.Load): load_counts[node.id] = load_counts.get(node.id, 0) + 1
        elif isinstance(node, ast.arg): _bump(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name: _bump(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names: _bump(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): _bump(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for nm in node.names: _bump(nm)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("append", "extend", "insert", "pop", "clear", "remove", "reverse") and isinstance(node.func.value, ast.Name):
                mutated_vars.add(node.func.value.id)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.value, ast.Name):
            mutated_vars.add(node.value.id)

    assign_pairs = [
        (node.targets[0].id, node.value) if isinstance(node, ast.Assign) else (node.target.id, node.value)
        for node in ast.walk(tree)
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name))
        or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None)
    ]
    const_vars: Dict[str, str] = {}; const_seq_vars: Dict[str, List[Optional[str]]] = {}; single_use_defs: Dict[str, ast.AST] = {}
    for _ in range(3):
        changed = False
        for var_name, rhs in assign_pairs:
            if bind_counts.get(var_name, 0) != 1 or var_name in mutated_vars or var_name in const_vars or var_name in const_seq_vars:
                continue
            s_val = _eval_static_str(rhs, const_vars, const_seq_vars)
            if s_val is not None:
                const_vars[var_name] = s_val; changed = True
                if load_counts.get(var_name, 0) == 1: single_use_defs[var_name] = rhs
                continue
            seq_val = _eval_static_seq(rhs, const_vars, const_seq_vars, 0, shlex_mod_names, shlex_split_names)
            if seq_val is not None:
                const_seq_vars[var_name] = seq_val; changed = True
                if load_counts.get(var_name, 0) == 1: single_use_defs[var_name] = rhs
        if not changed: break
    return const_vars, const_seq_vars, single_use_defs


def _collect_const_str_vars(tree: ast.Module) -> Dict[str, str]:
    const_vars, _, _ = _collect_static_bindings(tree, {"shlex"}, set())
    return const_vars
