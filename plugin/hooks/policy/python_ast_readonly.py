#!/usr/bin/env python3
"""Vérification lecture seule du harnais actif et repli hard-deny sur code Python non analysable par AST."""

import ast
import codecs
from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple

from .constants import QUOTED_LITERAL_RE, RE_PATTERN_FUNCS, SENSITIVE_PATH_PATTERNS
from .paths import is_harness_protected, matches_any
from .python_ast_eval import (
    _OS_PATH_PROPAGATING_FUNCS,
    _OS_PATH_TERMINAL_FUNCS,
    _PyAstState,
    _ScopeState,
    _decode_bytes_text,
    _eval_static_seq,
    _eval_static_str,
    _is_named_mod_expr,
)

_SAFE_CODECS_ENCODINGS = frozenset(
    {"base64", "base64_codec", "hex", "hex_codec", "rot_13", "rot-13", "utf-8", "utf8", "ascii", "latin-1", "latin1"}
)

_RAW_OS_MUTATION_NAMES = (
    r"remove|unlink|rmdir|rename|replace|chmod|lchmod|chown|open|system|popen|"
    r"exec\w*|spawn\w*|truncate|mkdir|makedirs|link|symlink"
)
_RAW_HARNESS_MUTATION_RE = re.compile(
    rf"\.(?:write_text|write_bytes|write|writelines|unlink|rmdir|rename|replace|"
    rf"chmod|lchmod|chown|touch|mkdir|symlink_to|hardlink_to|truncate)\s*\("
    rf"|\b(?:unlink|remove|rmdir|rename|replace|chmod|lchmod|chown|rmtree|symlink|link|"
    rf"truncate|makedirs|mkdir|system|popen|exec\w*|spawn\w*)\s*\("
    rf"|\b(?:shutil\.\w+|(?:os|posix|nt)\.(?:{_RAW_OS_MUTATION_NAMES})"
    rf"|subprocess\.\w+|eval|exec|getattr|setattr|delattr|__import__)\s*\("
    rf"|\bfrom\s+(?:shutil|subprocess)\s+import\b"
    rf"|\bfrom\s+(?:os|posix|nt)\s+import\b[^\n;]*\b(?:{_RAW_OS_MUTATION_NAMES})\b"
    rf"|\bfrom\s+(?:os|posix|nt)\s+import\s*\([^)]*\b(?:{_RAW_OS_MUTATION_NAMES})\b"
)
_RAW_OPEN_CALL_RE = re.compile(r"\bopen\s*\(")
_RAW_READONLY_MODE_RE = re.compile(r"""^['"](?:r|rb|rt|br|tr)['"]$""")

_PATH_READONLY_TERMINAL_METHODS = frozenset(
    {
        "read_text", "read_bytes", "stat", "lstat", "exists", "is_file", "is_dir",
        "is_symlink", "is_mount", "is_socket", "is_fifo", "is_block_device",
        "is_char_device", "is_absolute", "is_relative_to", "samefile", "match",
        "splitlines", "startswith", "endswith",
    }
)
_PATH_PROPAGATING_METHODS = frozenset(
    {
        "resolve", "absolute", "expanduser", "joinpath", "with_name", "with_stem",
        "with_suffix", "relative_to", "as_posix", "glob", "rglob", "iterdir",
    }
)
_PASSIVE_PATH_ATTRS = frozenset(
    {"name", "stem", "suffix", "suffixes", "parent", "parents", "parts", "anchor", "drive", "root"}
)
_READONLY_OPEN_MODES = frozenset({"r", "rb", "rt", "br", "tr"})


def _split_top_level_call_args(arg_str: str) -> Optional[List[str]]:
    """Découpe une liste d'arguments brute au niveau 0 sans dépendre de `ast.parse`."""
    parts: List[str] = []
    cur: List[str] = []
    depth = 0
    quote: Optional[str] = None
    i = 0
    n = len(arg_str)
    while i < n:
        ch = arg_str[i]
        if quote is not None:
            cur.append(ch)
            if ch == "\\" and i + 1 < n:
                cur.append(arg_str[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch in ("(", "[", "{"):
            depth += 1
            cur.append(ch)
        elif ch in (")", "]", "}"):
            if depth == 0:
                return None
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur.clear()
        else:
            cur.append(ch)
        i += 1
    if quote is not None or depth != 0:
        return None
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _raw_code_has_harness_mutation_risk(code: str) -> bool:
    """Retourne True si `code` (non analysable par AST) contient un indice de mutation/écriture/exécution."""
    if _RAW_HARNESS_MUTATION_RE.search(code):
        return True
    for match in _RAW_OPEN_CALL_RE.finditer(code):
        prefix = code[: match.start()].rstrip()
        is_method = prefix.endswith(".")
        idx = match.end()
        depth = 1
        quote: Optional[str] = None
        while idx < len(code) and depth > 0:
            ch = code[idx]
            if quote is not None:
                if ch == "\\" and idx + 1 < len(code):
                    idx += 2
                    continue
                if ch == quote:
                    quote = None
            else:
                if ch in ("'", '"'):
                    quote = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
            idx += 1
        if depth != 0:
            return True
        args_list = _split_top_level_call_args(code[match.end() : idx])
        if args_list is None:
            return True
        pos_args: List[str] = []
        mode_expr: Optional[str] = None
        allowed_kws = {"mode", "buffering", "encoding", "errors", "newline"}
        if not is_method:
            allowed_kws.add("file")
        for item in args_list:
            if not item or item.startswith("*"):
                return True
            if "=" in item and not item.startswith(("'", '"', "(")):
                kw_name, kw_val = item.split("=", 1)
                kw_clean = kw_name.strip()
                if kw_clean not in allowed_kws:
                    return True
                if kw_clean == "mode":
                    mode_expr = kw_val.strip()
            else:
                pos_args.append(item)
        if len(pos_args) > (5 if is_method else 6):
            return True
        mode_pos = 0 if is_method else 1
        if mode_expr is None and len(pos_args) > mode_pos:
            mode_expr = pos_args[mode_pos].strip()
        if mode_expr is not None and not _RAW_READONLY_MODE_RE.match(mode_expr):
            return True
    return False


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
        if is_harness_protected(lit, ws_root) and _raw_code_has_harness_mutation_risk(code):
            return "deny", f"Script `{context_label}` ciblant les garde-fous Gravity bloqué."
        if matches_any(lit, SENSITIVE_PATH_PATTERNS):
            return "deny", f"Script `{context_label}` ciblant un chemin sensible bloqué."
    return None


def _build_parent_map(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    """Construit la table enfant -> parent de l'arbre AST."""
    parents: Dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _collect_target_var_names(target: ast.AST) -> Optional[List[str]]:
    """Extrait les noms de variables d'une cible d'affectation/boucle si simple (`Name`/tuple/list)."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _collect_target_var_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        out: List[str] = []
        for elt in target.elts:
            sub = _collect_target_var_names(elt)
            if sub is None:
                return None
            out.extend(sub)
        return out
    return None


def _resolve_unambiguous_open_modes(
    mode_node: ast.AST, state: _PyAstState
) -> Optional[List[str]]:
    """Résout statiquement et sans ambiguïté les modes possibles pour `open()`."""
    for sub in ast.walk(mode_node):
        if (
            isinstance(sub, ast.Name)
            and isinstance(sub.ctx, ast.Load)
            and sub.id in state.dynamic_tainted_vars
        ):
            return None
    if isinstance(mode_node, ast.IfExp):
        left = _resolve_unambiguous_open_modes(mode_node.body, state)
        right = _resolve_unambiguous_open_modes(mode_node.orelse, state)
        if not left or not right:
            return None
        return left + [m for m in right if m not in left]
    cands = list(state.resolved_expr_strs.get(mode_node, ()))
    fallback = _eval_static_str(
        mode_node, state.const_vars, state.const_seq_vars, 0, state.imp.func_returns
    )
    if fallback and fallback not in cands:
        cands.append(fallback)
    return cands or None


def _is_open_call_readonly(call: ast.Call, *, is_method: bool, state: _PyAstState) -> bool:
    """Vérifie qu'un appel `open(...)` ou `Path.open(...)` est strictement en lecture seule."""
    if any(isinstance(a, ast.Starred) for a in call.args):
        return False
    if len(call.args) > (5 if is_method else 6):
        return False
    kw_map: Dict[str, ast.AST] = {}
    allowed_kws = {"mode", "buffering", "encoding", "errors", "newline"}
    if not is_method:
        allowed_kws.add("file")
    for kw in call.keywords:
        if kw.arg is None or kw.arg not in allowed_kws:
            return False
        kw_map[kw.arg] = kw.value
    mode_pos = 0 if is_method else 1
    if len(call.args) > mode_pos:
        if "mode" in kw_map:
            return False
        mode_node: Optional[ast.AST] = call.args[mode_pos]
    else:
        mode_node = kw_map.get("mode")
    if mode_node is None:
        return True
    cands = _resolve_unambiguous_open_modes(mode_node, state)
    return bool(cands) and all(m in _READONLY_OPEN_MODES for m in cands)


def _classify_readonly_call(
    call: ast.Call, arg_node: ast.AST, is_kw_arg: bool, kw_name: Optional[str], state: _PyAstState
) -> str:
    """Retourne `'terminal'`, `'propagate'` ou `'deny'` pour un `ast.Call` recevant `arg_node`."""
    if call in state.checked_cmd_nodes:
        return "terminal"
    fn = call.func
    if isinstance(fn, ast.Name):
        if state.imp.has_wildcard_import or fn.id in state.imp.shadowed_names:
            return "deny"
        if fn.id == "open":
            is_file_arg = (not is_kw_arg and bool(call.args) and call.args[0] is arg_node) or (
                is_kw_arg and kw_name == "file"
            )
            if is_file_arg and _is_open_call_readonly(call, is_method=False, state=state):
                return "terminal"
            return "deny"
        if fn.id in {"print", "len", "compile"}:
            return "terminal"
        if fn.id in {"str", "repr", "sorted", "list", "tuple", "set", "reversed", "enumerate", "zip"}:
            return "propagate"
        if fn.id in state.imp.path_ctor_names:
            return "propagate"
        if fn.id in state.imp.json_load_fns:
            return "terminal"
        if fn.id in state.imp.re_func_names:
            return "terminal"
        if fn.id in state.imp.os_readonly_term_fns:
            return "terminal"
        if fn.id in state.imp.os_readonly_prop_fns:
            return "propagate"
        return "deny"

    if isinstance(fn, ast.Attribute):
        val = fn.value
        if (
            isinstance(val, ast.Attribute)
            and val.attr == "path"
            and isinstance(val.value, ast.Name)
            and val.value.id in state.imp.os_mod_names
            and val.value.id not in state.imp.shadowed_names
        ) or (
            isinstance(val, ast.Name)
            and val.id in state.imp.os_path_mod_names
            and val.id not in state.imp.shadowed_names
        ):
            if fn.attr in _OS_PATH_TERMINAL_FUNCS:
                return "terminal"
            if fn.attr in _OS_PATH_PROPAGATING_FUNCS:
                return "propagate"
            return "deny"
        if (
            isinstance(val, ast.Name)
            and val.id in state.imp.os_mod_names
            and val.id not in state.imp.shadowed_names
        ):
            if fn.attr in {"stat", "lstat", "access", "readlink"}:
                return "terminal"
            if fn.attr in {"fspath", "listdir", "scandir", "walk"}:
                return "propagate"
            return "deny"
        if (
            isinstance(val, ast.Name)
            and val.id in state.imp.pathlib_mod_names
            and val.id not in state.imp.shadowed_names
        ):
            if fn.attr in {"Path", "PurePath", "PosixPath", "WindowsPath"}:
                return "propagate"
            return "deny"
        if (
            isinstance(val, ast.Name)
            and val.id in state.imp.json_mod_names
            and val.id not in state.imp.shadowed_names
            and fn.attr in {"load", "loads"}
        ):
            return "terminal"
        if (
            isinstance(val, ast.Name)
            and val.id in state.imp.re_mod_names
            and val.id not in state.imp.shadowed_names
            and fn.attr in RE_PATTERN_FUNCS
        ):
            return "terminal"
    return "deny"


def _is_readonly_harness_usage(
    start_node: ast.AST,
    parent_map: Dict[ast.AST, ast.AST],
    var_loads: Dict[str, List[ast.Name]],
    state: _PyAstState,
) -> bool:
    """Vérifie fail-closed que `start_node` et les variables/boucles associées restent en lecture seule."""
    pending_nodes: List[ast.AST] = [start_node]
    seen_nodes: Set[ast.AST] = set()
    seen_vars: Set[str] = set()

    def _queue_target(target: ast.AST) -> bool:
        names = _collect_target_var_names(target)
        if names is None:
            return False
        for vname in names:
            if vname not in seen_vars:
                seen_vars.add(vname)
                pending_nodes.extend(var_loads.get(vname, ()))
        return True

    while pending_nodes:
        cur = pending_nodes.pop()
        while True:
            if cur in seen_nodes:
                break
            seen_nodes.add(cur)
            parent = parent_map.get(cur)
            if parent is None:
                break

            if isinstance(parent, ast.Attribute) and cur is parent.value:
                if not isinstance(parent.ctx, ast.Load):
                    return False
                grand = parent_map.get(parent)
                if isinstance(grand, ast.Call) and grand.func is parent:
                    attr = parent.attr
                    if attr in _PATH_READONLY_TERMINAL_METHODS:
                        break
                    if attr == "open":
                        if not _is_open_call_readonly(grand, is_method=True, state=state):
                            return False
                        break
                    if attr in _PATH_PROPAGATING_METHODS:
                        cur = grand
                        continue
                    return False
                if parent.attr in _PASSIVE_PATH_ATTRS:
                    cur = parent
                    continue
                return False

            if isinstance(parent, ast.keyword) and cur is parent.value:
                call = parent_map.get(parent)
                if not isinstance(call, ast.Call):
                    return False
                verdict = _classify_readonly_call(call, cur, True, parent.arg, state)
                if verdict == "deny":
                    return False
                if verdict == "terminal":
                    break
                cur = call
                continue

            if isinstance(parent, ast.Call):
                if cur is parent.func:
                    return False
                verdict = _classify_readonly_call(parent, cur, False, None, state)
                if verdict == "deny":
                    return False
                if verdict == "terminal":
                    break
                cur = parent
                continue

            if isinstance(parent, ast.BinOp):
                if isinstance(parent.op, (ast.Div, ast.Add)):
                    cur = parent
                    continue
                return False

            if isinstance(parent, (ast.FormattedValue, ast.JoinedStr, ast.Compare, ast.BoolOp, ast.UnaryOp)):
                cur = parent
                continue

            if isinstance(parent, ast.IfExp):
                if cur is parent.test:
                    break
                cur = parent
                continue

            if isinstance(parent, ast.Subscript):
                if cur is parent.value and isinstance(parent.ctx, ast.Load):
                    cur = parent
                    continue
                return False

            if isinstance(parent, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                if hasattr(parent, "ctx") and not isinstance(parent.ctx, ast.Load):
                    return False
                cur = parent
                continue

            if isinstance(parent, (ast.If, ast.While, ast.Assert, ast.Expr)):
                break

            if isinstance(parent, ast.NamedExpr):
                if isinstance(parent.target, ast.Name) and not _queue_target(parent.target):
                    return False
                cur = parent
                continue

            if isinstance(parent, ast.Assign):
                if cur is not parent.value:
                    return False
                for tgt in parent.targets:
                    if not _queue_target(tgt):
                        return False
                break

            if isinstance(parent, ast.AnnAssign):
                if cur is not parent.value or not _queue_target(parent.target):
                    return False
                break

            if isinstance(parent, ast.AugAssign):
                if not isinstance(parent.op, ast.Add) or not _queue_target(parent.target):
                    return False
                break

            if isinstance(parent, (ast.For, ast.AsyncFor)):
                if cur is not parent.iter or not _queue_target(parent.target):
                    return False
                break

            if isinstance(parent, ast.comprehension):
                if cur is parent.iter:
                    if not _queue_target(parent.target):
                        return False
                    break
                if cur in parent.ifs:
                    break
                return False

            if isinstance(parent, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                cur = parent
                continue

            if isinstance(parent, ast.withitem):
                if cur is not parent.context_expr:
                    return False
                if parent.optional_vars is not None and not _queue_target(parent.optional_vars):
                    return False
                break

            return False

    return True


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
            if isinstance(field_val, ast.AST) and not isinstance(field_val, skip_types):
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
            for c in _eval_static_seq_candidates(branch, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1):
                _add(c)
        return out
    if isinstance(node, ast.NamedExpr):
        return _eval_static_seq_candidates(node.value, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1)
    if isinstance(node, ast.Name):
        if node.id in cur_seq:
            _add(cur_seq[node.id])
        for alt in alt_seq.get(node.id, ()):
            _add(alt)
        return out
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in func_returns and not node.args and not node.keywords:
        for ret_expr in func_returns[node.func.id]:
            for c in _eval_static_seq_candidates(ret_expr, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1):
                _add(c)
        if out:
            return out
    _add(_eval_static_seq(node, cur_str, cur_seq, 0, shlex_mod_names, shlex_split_names, func_returns))
    return out


def _match_codecs_decode_call(
    node: ast.Call, codecs_mod_names: Optional[Set[str]] = None, codecs_decode_fns: Optional[Dict[str, str]] = None
) -> Optional[Tuple[ast.AST, Optional[ast.AST], str]]:
    """Retourne `(obj_arg, encoding_arg, mode)` si `node` appelle `codecs.{decode,encode}` (ou alias / `getattr`)."""
    mods, fns, fn = codecs_mod_names or {"codecs"}, codecs_decode_fns or {}, node.func
    mode: Optional[str] = None
    if isinstance(fn, ast.Name) and fn.id in fns:
        mode = fns[fn.id]
    elif isinstance(fn, ast.Attribute) and fn.attr in ("decode", "encode") and _is_named_mod_expr(fn.value, "codecs", mods):
        mode = fn.attr
    elif (
        isinstance(fn, ast.Call) and isinstance(fn.func, ast.Name) and fn.func.id == "getattr"
        and len(fn.args) >= 2 and isinstance(fn.args[1], ast.Constant)
        and fn.args[1].value in ("decode", "encode") and _is_named_mod_expr(fn.args[0], "codecs", mods)
    ):
        mode = str(fn.args[1].value)
    if mode not in ("decode", "encode"):
        return None
    kw = {k.arg: k.value for k in node.keywords if k.arg is not None}
    obj_arg = node.args[0] if node.args else kw.get("obj")
    enc_arg = node.args[1] if len(node.args) > 1 else kw.get("encoding")
    if obj_arg is None or len(node.args) > 3 or any(k not in ("obj", "encoding", "errors") for k in kw):
        return None
    return obj_arg, enc_arg, mode


def _decode_codecs_payload(raw: object, enc: Optional[str], mode: str = "decode") -> Optional[str]:
    """Décode statiquement un payload `codecs.{decode,encode}` pour les encodages texte/bytes sûrs."""
    enc_norm = (enc or "utf-8").lower()
    if mode == "encode":
        if not isinstance(raw, str):
            return None
        if enc_norm in ("rot_13", "rot-13"):
            return codecs.encode(raw, enc_norm)
        return raw if enc_norm in ("utf-8", "utf8", "ascii", "latin-1", "latin1") else None
    if enc_norm not in _SAFE_CODECS_ENCODINGS or not isinstance(raw, (str, bytes)):
        return None
    try:
        res = codecs.decode(raw, enc_norm)  # type: ignore[arg-type]
    except (ValueError, TypeError, LookupError):
        return None
    return _decode_bytes_text(res) if isinstance(res, bytes) else (res if isinstance(res, str) else None)


def _has_tainted_name(node: ast.AST, state: Optional[_PyAstState], scope: Optional[_ScopeState]) -> bool:
    if state is None and scope is None:
        return False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            if scope and (sub.id in scope.incomplete_vars or sub.id in scope.obf_tainted):
                return True
            if state and sub.id in state.dynamic_tainted_vars and not (scope and sub.id in scope.shadowed_alts):
                return True
    return False
