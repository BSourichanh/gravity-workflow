#!/usr/bin/env python3
"""Extraction et inspection des scripts inline (`python -c`, `node -e`, `perl -e`, `ruby -e`, sous-shells) et payloads stdin."""

from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple

from .constants import (
    MAX_SUBSHELL_DEPTH,
    NODE_VALUE_OPTS,
    PS_VALUE_OPTS,
    PY_VERSION_OPT_RE,
    PYTHON_VALUE_OPTS,
    RUBY_VALUE_OPTS,
    SENSITIVE_PATH_PATTERNS,
    TEXT_OUTPUT_HEADS,
)
from .paths import _is_active_plugin_root_path, _is_destructive_target, is_harness_protected, matches_any
from .python_ast import inspect_inline_python_code
from .shell_parser import (
    _argv_to_shell_cmd,
    _extract_command_head_args_rich,
    _is_python_head,
    _reconstruct_ps_single_unescaped,
    _tokenize_shell_segment_rich,
)


def _extract_inline_spec(
    head: str, rich_args: List[Tuple[str, List[Tuple[str, str]], bool]]
) -> Optional[Tuple[str, Set[int], List[str]]]:
    """Extrait `(kind, payload_arg_indices, payloads)` pour les interpréteurs/sous-shells inline."""
    args = [tok for tok, _, _ in rich_args]
    n = len(args)

    if _is_python_head(head):
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or not tok.startswith("-"):
                return None
            if tok in PYTHON_VALUE_OPTS:
                i += 2
                continue
            if tok.startswith("--") or PY_VERSION_OPT_RE.match(tok):
                i += 1
                continue
            short_flags = tok[1:]
            advance_extra = 0
            for k, ch in enumerate(short_flags):
                if ch == "c":
                    candidates: List[str] = []
                    if k == len(short_flags) - 1:
                        indices = {i + 1} if i + 1 < n else {i}
                        if i + 1 < n:
                            val, chunks, _ = rich_args[i + 1]
                            if val:
                                candidates.append(val)
                            ps_val = _reconstruct_ps_single_unescaped(chunks)
                            if ps_val and ps_val not in candidates:
                                candidates.append(ps_val)
                    else:
                        indices = {i}
                        tail = short_flags[k + 1 :]
                        if tail:
                            candidates.append(tail)
                    return "python", indices, candidates
                if ch == "m":
                    return None
                if ch in ("W", "X"):
                    if k == len(short_flags) - 1:
                        advance_extra = 1
                    break
            i += 1 + advance_extra
        return None

    if head == "node":
        indices: Set[int] = set()
        payloads: List[str] = []
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or not tok.startswith("-"):
                break
            if tok in NODE_VALUE_OPTS:
                i += 2
                continue
            if tok in ("-e", "-p", "--eval", "--print"):
                if i + 1 < n:
                    indices.add(i + 1)
                    payloads.append(args[i + 1])
                else:
                    indices.add(i)
                i += 2
                continue
            if tok.startswith(("--eval=", "--print=")):
                indices.add(i)
                payloads.append(tok.split("=", 1)[1])
                i += 1
                continue
            if not tok.startswith("--") and re.match(r"^-[A-Za-z]*[ep]", tok):
                if re.match(r"^-[A-Za-z]+$", tok) and tok[-1] in ("e", "p"):
                    if i + 1 < n:
                        indices.add(i + 1)
                        payloads.append(args[i + 1])
                    else:
                        indices.add(i)
                    i += 2
                else:
                    idx_ep = min(
                        (pos for pos in (tok.find("e"), tok.find("p")) if pos > 0),
                        default=-1,
                    )
                    indices.add(i)
                    if idx_ep > 0:
                        payloads.append(tok[idx_ep + 1 :])
                    i += 1
                continue
            i += 1
        return ("node", indices, payloads) if indices else None

    if head == "perl":
        indices = set()
        payloads = []
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or not tok.startswith("-"):
                break
            if tok == "-I":
                i += 2
                continue
            if tok.startswith("--"):
                i += 1
                continue
            if tok in ("-e", "-E"):
                if i + 1 < n:
                    indices.add(i + 1)
                    payloads.append(args[i + 1])
                else:
                    indices.add(i)
                i += 2
                continue
            short = tok[1:]
            matched = False
            for k, ch in enumerate(short):
                if ch in ("I", "M", "m", "F", "d", "D", "0", "C", "V"):
                    break
                if ch in ("e", "E"):
                    matched = True
                    if k == len(short) - 1:
                        if i + 1 < n:
                            indices.add(i + 1)
                            payloads.append(args[i + 1])
                        else:
                            indices.add(i)
                        i += 2
                    else:
                        indices.add(i)
                        payloads.append(short[k + 1 :])
                        i += 1
                    break
            if not matched:
                i += 1
        return ("perl", indices, payloads) if indices else None

    if head == "ruby":
        indices = set()
        payloads = []
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or not tok.startswith("-"):
                break
            if tok in RUBY_VALUE_OPTS:
                i += 2
                continue
            if tok.startswith("--") or tok[:2] in ("-I", "-r", "-C", "-E", "-F", "-K", "-T", "-W"):
                i += 1
                continue
            if tok == "-e":
                if i + 1 < n:
                    indices.add(i + 1)
                    payloads.append(args[i + 1])
                else:
                    indices.add(i)
                i += 2
                continue
            if tok.startswith("-e"):
                indices.add(i)
                payloads.append(tok[2:])
                i += 1
                continue
            if re.match(r"^-[A-Za-z]+$", tok) and "e" in tok[1:]:
                if tok.endswith("e"):
                    if i + 1 < n:
                        indices.add(i + 1)
                        payloads.append(args[i + 1])
                    else:
                        indices.add(i)
                    i += 2
                else:
                    indices.add(i)
                    payloads.append(tok[tok.index("e") + 1 :])
                    i += 1
                continue
            i += 1
        return ("ruby", indices, payloads) if indices else None

    if head in {"sh", "bash", "zsh", "dash"}:
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or (not tok.startswith("-") and not tok.startswith("+")):
                return None
            if tok in ("-o", "+o", "--rcfile", "--init-file"):
                i += 2
                continue
            if tok.startswith("--") or tok.startswith("+"):
                i += 1
                continue
            if tok == "-c" or (re.match(r"^-[A-Za-z]+$", tok) and "c" in tok[1:]):
                if i + 1 < n:
                    return "shell", {i + 1}, [args[i + 1]]
                return "shell", {i}, []
            i += 1
        return None

    if head in {"pwsh", "powershell"}:
        i = 0
        while i < n:
            tok = args[i]
            if tok == "--" or not tok.startswith("-"):
                return None
            low = tok.lower()
            low_flag = low.split(":", 1)[0]
            if low_flag in ("-f", "-file"):
                return None
            if low_flag in PS_VALUE_OPTS:
                i += 1 if ":" in low else 2
                continue
            if low_flag in {"-e", "-ec"} or (
                len(low_flag) >= 3 and "-encodedcommand".startswith(low_flag)
            ):
                if ":" in tok:
                    return "ps_encoded", {i}, [tok.split(":", 1)[1]]
                if i + 1 < n:
                    return "ps_encoded", {i + 1}, [args[i + 1]]
                return "ps_encoded", {i}, []
            if low_flag == "-c" or (len(low_flag) >= 3 and "-command".startswith(low_flag)):
                if ":" in tok:
                    return "ps_command", set(range(i, n)), [tok.split(":", 1)[1]]
                tail_args = args[i + 1 :]
                if not tail_args:
                    return "ps_command", {i}, [""]
                payload = tail_args[0] if len(tail_args) == 1 else _argv_to_shell_cmd(tail_args)
                return "ps_command", set(range(i + 1, n)), [payload]
            i += 1
        return None

    if head == "cmd":
        for i, tok in enumerate(args):
            low = tok.lower()
            if low in ("/c", "/k"):
                tail_args = args[i + 1 :]
                if not tail_args:
                    return "cmd", {i}, [""]
                payload = tail_args[0] if len(tail_args) == 1 else _argv_to_shell_cmd(tail_args)
                return "cmd", set(range(i + 1, n)), [payload]
            if re.match(r"^/[ck](?:[:\"'\s]|$)", low):
                tail = tok[2:].lstrip(":")
                rest_args = args[i + 1 :]
                rest = (
                    rest_args[0]
                    if len(rest_args) == 1
                    else (_argv_to_shell_cmd(rest_args) if rest_args else "")
                )
                payload = f"{tail} {rest}".strip() if rest else tail
                return "cmd", set(range(i, n)), [payload]
        return None

    return None


def _decode_js_escape(text: str, pos: int) -> Tuple[str, int]:
    """Décode une séquence d'échappement JS/script à `text[pos] == '\\\\'` (incluant `\\xHH`, `\\uHHHH`, `\\u{...}`)."""
    n = len(text)
    if pos + 1 >= n:
        return "\\", n
    nxt = text[pos + 1]
    if nxt == "x" and pos + 3 < n:
        hex_part = text[pos + 2 : pos + 4]
        if all(c in "0123456789abcdefABCDEF" for c in hex_part):
            return chr(int(hex_part, 16)), pos + 4
    if nxt == "u":
        if pos + 2 < n and text[pos + 2] == "{":
            end_brace = text.find("}", pos + 3)
            if end_brace != -1 and end_brace > pos + 3:
                hex_part = text[pos + 3 : end_brace]
                if all(c in "0123456789abcdefABCDEF" for c in hex_part):
                    cp = int(hex_part, 16)
                    if 0 <= cp <= 0x10FFFF:
                        return chr(cp), end_brace + 1
        elif pos + 5 < n:
            hex_part = text[pos + 2 : pos + 6]
            if all(c in "0123456789abcdefABCDEF" for c in hex_part):
                return chr(int(hex_part, 16)), pos + 6
    return nxt, pos + 2


def _scan_js_template_literal(code: str, start: int) -> Tuple[List[Tuple[str, str]], int]:
    """Parse un template literal JS à partir du caractère suivant `` ` `` en `[('text'|'expr', val), ...]`."""
    parts: List[Tuple[str, str]] = []
    text_buf: List[str] = []
    i = start
    n = len(code)

    def _flush_text() -> None:
        if text_buf:
            parts.append(("text", "".join(text_buf)))
            text_buf.clear()

    while i < n:
        ch = code[i]
        if ch == "\\" and i + 1 < n:
            decoded, next_i = _decode_js_escape(code, i)
            text_buf.append(decoded)
            i = next_i
            continue
        if ch == "`":
            _flush_text()
            return parts, i + 1
        if ch == "$" and i + 1 < n and code[i + 1] == "{":
            _flush_text()
            j = i + 2
            brace_depth = 1
            while j < n and brace_depth > 0:
                c = code[j]
                if c in ("'", '"', "`"):
                    adv = _advance_js_quoted_or_template(code, j)
                    j = adv if adv is not None else n
                    continue
                if c == "{":
                    brace_depth += 1
                elif c == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        break
                j += 1
            parts.append(("expr", code[i + 2 : j]))
            i = j + 1 if j < n else n
            continue
        text_buf.append(ch)
        i += 1

    _flush_text()
    return parts, n


def _advance_js_quoted_or_template(code: str, i: int) -> Optional[int]:
    """Si `code[i]` ouvre `'...'`, `\"...\"` ou `` `...` ``, retourne l'index juste après la fermeture."""
    ch = code[i]
    n = len(code)
    if ch in ("'", '"'):
        i += 1
        while i < n:
            if code[i] == "\\" and i + 1 < n:
                i += 2
                continue
            if code[i] == ch:
                return i + 1
            i += 1
        return n
    if ch == "`":
        _, end_i = _scan_js_template_literal(code, i + 1)
        return end_i
    return None


def _strip_js_comments(code: str) -> str:
    """Supprime les commentaires JS (`//...`, `/*...*/`) hors chaînes, templates et regex littéraux."""
    out: List[str] = []
    i = 0
    n = len(code)
    last_sig = ""

    while i < n:
        ch = code[i]
        if ch in ("'", '"'):
            adv = _advance_js_quoted_or_template(code, i)
            end_q = adv if adv is not None else n
            out.append(code[i:end_q])
            last_sig = ch
            i = end_q
            continue
        if ch == "`":
            tpl_parts, end_idx = _scan_js_template_literal(code, i + 1)
            rebuilt_tpl = ["`"]
            for kind, val in tpl_parts:
                if kind == "text":
                    rebuilt_tpl.append(val.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${"))
                else:
                    rebuilt_tpl.append("${" + _strip_js_comments(val) + "}")
            rebuilt_tpl.append("`")
            out.append("".join(rebuilt_tpl))
            last_sig = "`"
            i = end_idx
            continue
        if ch == "/" and i + 1 < n and code[i + 1] == "/":
            out.append(" ")
            i += 2
            while i < n and code[i] not in ("\r", "\n"):
                i += 1
            continue
        if ch == "/" and i + 1 < n and code[i + 1] == "*":
            out.append(" ")
            end_c = code.find("*/", i + 2)
            i = (end_c + 2) if end_c != -1 else n
            continue
        if ch == "/" and (not last_sig or last_sig in "(=,:[!&|?{};\n"):
            j = i + 1
            in_cls = False
            while j < n and code[j] not in ("\r", "\n"):
                rc = code[j]
                if rc == "\\" and j + 1 < n:
                    j += 2
                    continue
                if rc == "[":
                    in_cls = True
                elif rc == "]":
                    in_cls = False
                elif rc == "/" and not in_cls:
                    j += 1
                    while j < n and code[j].isalpha():
                        j += 1
                    out.append(code[i:j])
                    last_sig = "/"
                    i = j
                    break
                j += 1
            else:
                out.append(ch)
                last_sig = ch
                i += 1
            continue
        out.append(ch)
        if not ch.isspace():
            last_sig = ch
        elif ch == "\n":
            last_sig = "\n"
        i += 1
    return "".join(out)


def _split_script_top_level(text: str, sep: str) -> List[str]:
    """Découpe une expression JS/Perl/Ruby sur `sep` uniquement au niveau 0 hors chaînes/templates."""
    items: List[str] = []
    start = 0
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        adv = _advance_js_quoted_or_template(text, i)
        if adv is not None:
            i = adv
            continue
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == sep:
            items.append(text[start:i])
            start = i + 1
        i += 1
    items.append(text[start:])
    return items


def _extract_balanced_delims(
    code: str, open_idx: int, open_ch: str, close_ch: str
) -> Optional[Tuple[str, int]]:
    """Extrait `(contenu_interne, index_apres_fermeture)` depuis `code[open_idx] == open_ch`."""
    if open_idx >= len(code) or code[open_idx] != open_ch:
        return None
    depth = 1
    i = open_idx + 1
    n = len(code)
    while i < n and depth > 0:
        adv = _advance_js_quoted_or_template(code, i)
        if adv is not None:
            i = adv
            continue
        ch = code[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return code[open_idx + 1 : i], i + 1
        i += 1
    return None


def _extract_balanced_parens(code: str, open_idx: int) -> Optional[Tuple[str, int]]:
    """Extrait `(contenu_interne, index_apres_parenthese)` depuis `code[open_idx] == '('`."""
    return _extract_balanced_delims(code, open_idx, "(", ")")


def _extract_balanced_brackets(code: str, open_idx: int) -> Optional[Tuple[str, int]]:
    """Extrait `(contenu_interne, index_apres_crochet)` depuis `code[open_idx] == '['`."""
    return _extract_balanced_delims(code, open_idx, "[", "]")


def _unescape_quoted_literal(raw: str) -> Optional[str]:
    """Extrait la valeur d'une chaîne `'...'` ou `\"...\"` complète (incluant `\\xHH`/`\\uHHHH`)."""
    s = raw.strip()
    if len(s) < 2 or s[0] not in ("'", '"') or s[-1] != s[0]:
        return None
    q = s[0]
    out: List[str] = []
    i = 1
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            decoded, next_i = _decode_js_escape(s, i)
            out.append(decoded)
            i = next_i
            continue
        if ch == q:
            return "".join(out) if i == n - 1 else None
        out.append(ch)
        i += 1
    return None


def _eval_js_str_expr(
    expr: str,
    js_str_vars: Dict[str, str],
    *,
    allow_dynamic_placeholder: bool = False,
    _depth: int = 0,
) -> Optional[str]:
    """Évalue statiquement une expression chaîne JS (littéraux, templates `${...}`, `+`, vars, `fromCharCode`, `.join`, `path.join`)."""
    if _depth > 8:
        return None
    s = expr.strip()
    if not s:
        return None
    while s.startswith("(") and s.endswith(")"):
        balanced = _extract_balanced_parens(s, 0)
        if balanced is not None and balanced[1] == len(s):
            s = balanced[0].strip()
        else:
            break

    plus_parts = _split_script_top_level(s, "+")
    if len(plus_parts) > 1:
        acc: List[str] = []
        for piece in plus_parts:
            val = _eval_js_str_expr(
                piece, js_str_vars, allow_dynamic_placeholder=allow_dynamic_placeholder, _depth=_depth + 1
            )
            if val is None:
                if allow_dynamic_placeholder:
                    acc.append("__dynamic__")
                else:
                    return None
            else:
                acc.append(val)
        return "".join(acc)

    unq = _unescape_quoted_literal(s)
    if unq is not None:
        return unq

    if s.startswith("`") and s.endswith("`"):
        tpl_parts, end_idx = _scan_js_template_literal(s, 1)
        if end_idx == len(s):
            acc = []
            for kind, val in tpl_parts:
                if kind == "text":
                    acc.append(val)
                else:
                    ev = _eval_js_str_expr(
                        val, js_str_vars, allow_dynamic_placeholder=allow_dynamic_placeholder, _depth=_depth + 1
                    )
                    if ev is None:
                        if allow_dynamic_placeholder:
                            acc.append("__dynamic__")
                        else:
                            return None
                    else:
                        acc.append(ev)
            return "".join(acc)

    if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", s) and s in js_str_vars:
        return js_str_vars[s]

    m_cc = re.match(r"^String\.fromCharCode\s*\((.*)\)$", s, re.DOTALL)
    if m_cc:
        chars: List[str] = []
        for item in _split_script_top_level(m_cc.group(1), ","):
            t = item.strip()
            if not t:
                continue
            try:
                num = int(t, 0)
            except ValueError:
                return None
            if not (0 <= num <= 0x10FFFF):
                return None
            chars.append(chr(num))
        return "".join(chars) if chars else None

    m_join = re.match(r"^\[(.*)\]\s*\.join\s*\((.*)\)$", s, re.DOTALL)
    if m_join:
        sep_raw = m_join.group(2).strip()
        sep = "," if not sep_raw else _eval_js_str_expr(sep_raw, js_str_vars, _depth=_depth + 1)
        if sep is not None:
            elts: List[str] = []
            for item in _split_script_top_level(m_join.group(1), ","):
                if not item.strip():
                    continue
                val = _eval_js_str_expr(
                    item, js_str_vars, allow_dynamic_placeholder=allow_dynamic_placeholder, _depth=_depth + 1
                )
                if val is None:
                    return None
                elts.append(val)
            return sep.join(elts)

    m_pjoin = re.match(r"^(?:path|posix|win32)(?:\.posix|\.win32)?\.join\s*\((.*)\)$", s, re.DOTALL)
    if m_pjoin:
        segs: List[str] = []
        for item in _split_script_top_level(m_pjoin.group(1), ","):
            if not item.strip():
                continue
            val = _eval_js_str_expr(
                item, js_str_vars, allow_dynamic_placeholder=allow_dynamic_placeholder, _depth=_depth + 1
            )
            if val is None:
                return None
            segs.append(val.replace("\\", "/"))
        if segs:
            return "/".join(segs)

    return None


def _parse_js_array_expr(
    expr: str, js_str_vars: Dict[str, str], js_arr_vars: Dict[str, List[str]]
) -> Optional[List[str]]:
    """Évalue un tableau JS `[...]` ou une variable tableau vers `List[str]`."""
    s = expr.strip()
    if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", s) and s in js_arr_vars:
        return list(js_arr_vars[s])
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        out: List[str] = []
        for item in _split_script_top_level(inner, ","):
            if not item.strip():
                continue
            val = _eval_js_str_expr(item, js_str_vars, allow_dynamic_placeholder=True)
            out.append(val if val is not None else "__dynamic__")
        return out
    return None


def _split_js_statements(code: str) -> List[str]:
    """Découpe un script JS en instructions simples (`;`, `\\n`, blocs `{ ... }`) hors chaînes/templates."""
    stmts: List[str] = []
    start = 0
    paren_depth = 0
    bracket_depth = 0
    i = 0
    n = len(code)
    while i < n:
        adv = _advance_js_quoted_or_template(code, i)
        if adv is not None:
            i = adv
            continue
        ch = code[i]
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif paren_depth == 0 and bracket_depth == 0 and ch in ";\r\n{}":
            seg = code[start:i].strip()
            if seg:
                stmts.append(seg)
            start = i + 1
        i += 1
    tail = code[start:].strip()
    if tail:
        stmts.append(tail)
    return stmts


def _collect_js_var_bindings(
    clean_code: str,
) -> Tuple[
    Dict[str, str],
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[List[str]]],
    Set[str],
]:
    """Collecte les déclarations (`const`/`let`/`var`, multi-vars `,`), réaffectations, `.push` et alias `process`."""
    js_str_vars: Dict[str, str] = {}
    js_arr_vars: Dict[str, List[str]] = {}
    js_str_alts: Dict[str, List[str]] = {}
    js_arr_alts: Dict[str, List[List[str]]] = {}
    js_process_aliases: Set[str] = {"process"}

    def _rec_str(name: str, val: str) -> None:
        js_str_vars[name] = val
        bucket = js_str_alts.setdefault(name, [])
        if val not in bucket and len(bucket) < 8:
            bucket.append(val)

    def _rec_arr(name: str, val: List[str]) -> None:
        snap = list(val)
        js_arr_vars[name] = snap
        bucket = js_arr_alts.setdefault(name, [])
        if snap not in bucket and len(bucket) < 8:
            bucket.append(snap)

    def _process_binding(lhs_name: str, rhs_expr: str) -> None:
        rhs = rhs_expr.strip()
        if not rhs:
            return
        if re.match(r"^(?:(?:globalThis|global)\.)?process$", rhs) or rhs in js_process_aliases:
            js_process_aliases.add(lhs_name)
            return
        s_val = _eval_js_str_expr(rhs, js_str_vars, allow_dynamic_placeholder=True)
        if s_val is not None:
            _rec_str(lhs_name, s_val)
            return
        arr_val = _parse_js_array_expr(rhs, js_str_vars, js_arr_vars)
        if arr_val is not None:
            _rec_arr(lhs_name, arr_val)

    stmts = _split_js_statements(clean_code)
    decl_head_re = re.compile(r"^(?:const|let|var)\s+(.+)$", re.DOTALL)
    assign_re = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*=(?![=>])\s*(.+)$", re.DOTALL)
    push_re = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)\.push\s*\((.*)\)$", re.DOTALL)

    for stmt in stmts:
        m_decl = decl_head_re.match(stmt)
        if m_decl:
            for item in _split_script_top_level(m_decl.group(1), ","):
                m_pair = assign_re.match(item.strip())
                if m_pair:
                    _process_binding(m_pair.group(1), m_pair.group(2))
            continue
        m_assign = assign_re.match(stmt)
        if m_assign:
            _process_binding(m_assign.group(1), m_assign.group(2))
            continue
        m_push = push_re.match(stmt)
        if m_push and m_push.group(1) in js_arr_vars:
            arr_name = m_push.group(1)
            for item in _split_script_top_level(m_push.group(2), ","):
                if not item.strip():
                    continue
                val = _eval_js_str_expr(item, js_str_vars, allow_dynamic_placeholder=True)
                js_arr_vars[arr_name].append(val if val is not None else "__dynamic__")
            _rec_arr(arr_name, js_arr_vars[arr_name])

    return js_str_vars, js_arr_vars, js_str_alts, js_arr_alts, js_process_aliases


def _expand_js_templates(
    code: str, js_str_vars: Dict[str, str]
) -> Tuple[str, List[str], List[str]]:
    """Remplace les template literals JS par des littéraux pliés et collecte littéraux + expressions `${...}`."""
    out: List[str] = []
    literals: List[str] = []
    embedded_exprs: List[str] = []
    i = 0
    n = len(code)

    def _collect_nested_exprs(parts: List[Tuple[str, str]]) -> None:
        for pkind, pval in parts:
            if pkind == "expr" and pval.strip():
                embedded_exprs.append(pval)
                j = 0
                while j < len(pval):
                    if pval[j] == "`":
                        sub_parts, j = _scan_js_template_literal(pval, j + 1)
                        _collect_nested_exprs(sub_parts)
                        continue
                    j += 1

    while i < n:
        ch = code[i]
        if ch in ("'", '"'):
            adv = _advance_js_quoted_or_template(code, i)
            end_q = adv if adv is not None else n
            out.append(code[i:end_q])
            i = end_q
            continue
        if ch == "`":
            tpl_parts, end_idx = _scan_js_template_literal(code, i + 1)
            _collect_nested_exprs(tpl_parts)
            raw_tpl = code[i:end_idx]
            resolved = _eval_js_str_expr(raw_tpl, js_str_vars, allow_dynamic_placeholder=True)
            if resolved is None:
                resolved = "".join(val if kind == "text" else "__dynamic__" for kind, val in tpl_parts)
            literals.append(resolved)
            safe_val = resolved.replace("\\", "\\\\").replace('"', '\\"')
            out.append(f'"{safe_val}"')
            i = end_idx
            continue
        out.append(ch)
        i += 1

    return "".join(out), literals, embedded_exprs


def _expand_ruby_interpolations(code: str) -> str:
    """Plie les interpolations Ruby simples `#{ 'lit' }` dans les chaînes double-quotes et backticks."""
    out: List[str] = []
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        if ch in ('"', "`"):
            q = ch
            out.append(q)
            i += 1
            while i < n and code[i] != q:
                if code[i] == "\\" and i + 1 < n:
                    out.append(code[i : i + 2])
                    i += 2
                    continue
                if code[i] == "#" and i + 1 < n and code[i + 1] == "{":
                    j = i + 2
                    depth = 1
                    while j < n and depth > 0:
                        if code[j] == "{":
                            depth += 1
                        elif code[j] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        j += 1
                    inner = code[i + 2 : j]
                    folded_inner, _ = _fold_inline_concat(inner)
                    unq = _unescape_quoted_literal(folded_inner)
                    if unq is not None:
                        out.append(unq.replace("\\", "\\\\").replace(q, "\\" + q))
                    else:
                        out.append(code[i : j + 1])
                    i = j + 1 if j < n else n
                    continue
                out.append(code[i])
                i += 1
            if i < n and code[i] == q:
                out.append(q)
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _tokenize_script_literals(code: str) -> List[Tuple[str, str, str]]:
    """Découpe un script JS/Perl/Ruby en jetons `('str', quote, valeur)` ou `('other', '', texte)`."""
    tokens: List[Tuple[str, str, str]] = []
    other: List[str] = []
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        if ch in ("'", '"', "`"):
            if other:
                tokens.append(("other", "", "".join(other)))
                other.clear()
            q = ch
            i += 1
            buf: List[str] = []
            while i < n and code[i] != q:
                if code[i] == "\\" and i + 1 < n:
                    decoded, next_i = _decode_js_escape(code, i)
                    buf.append(decoded)
                    i = next_i
                    continue
                buf.append(code[i])
                i += 1
            if i < n and code[i] == q:
                i += 1
            tokens.append(("str", q, "".join(buf)))
            continue
        other.append(ch)
        i += 1
    if other:
        tokens.append(("other", "", "".join(other)))
    return tokens


def _fold_inline_concat(code: str) -> Tuple[str, List[str]]:
    """Plie les concaténations de littéraux (`'.' + 'env'`, `'.' . 'env'`) et retourne `(code_plie, litteraux)`."""
    tokens = _tokenize_script_literals(code)
    merged: List[Tuple[str, str, str]] = []
    idx = 0
    while idx < len(tokens):
        kind, q, val = tokens[idx]
        if kind == "str":
            acc = val
            while (
                idx + 2 < len(tokens)
                and tokens[idx + 1][0] == "other"
                and tokens[idx + 1][2].strip() in ("+", ".")
                and tokens[idx + 2][0] == "str"
            ):
                acc += tokens[idx + 2][2]
                idx += 2
            merged.append(("str", q, acc))
        else:
            merged.append((kind, q, val))
        idx += 1

    rebuilt: List[str] = []
    literals: List[str] = []
    for kind, q, val in merged:
        if kind == "str":
            literals.append(val)
            escaped = val.replace("\\", "\\\\").replace(q, "\\" + q)
            rebuilt.append(f"{q}{escaped}{q}")
        else:
            rebuilt.append(val)
    return "".join(rebuilt), literals


def _inspect_inline_script_code(
    lang: str,
    code: str,
    ws_root: Optional[Path] = None,
    _depth: int = 0,
) -> Optional[Tuple[str, str]]:
    """Inspecte un script inline `node -e`, `perl -e` ou `ruby -e` pour menaces `deny` uniquement."""
    from .evaluator import _evaluate_shell_command

    js_str_vars: Dict[str, str] = {}
    js_arr_vars: Dict[str, List[str]] = {}
    js_str_alts: Dict[str, List[str]] = {}
    js_arr_alts: Dict[str, List[List[str]]] = {}
    js_process_aliases: Set[str] = {"process"}
    extra_literals: List[str] = []
    embedded_exprs: List[str] = []
    js_regexp_spans: List[Tuple[int, int]] = []
    clean_code = _strip_js_comments(code) if lang == "node" else code
    preprocessed = clean_code

    if lang == "node":
        (
            js_str_vars,
            js_arr_vars,
            js_str_alts,
            js_arr_alts,
            js_process_aliases,
        ) = _collect_js_var_bindings(clean_code)
        preprocessed, extra_literals, embedded_exprs = _expand_js_templates(clean_code, js_str_vars)
        for vals in js_str_alts.values():
            extra_literals.extend(vals)
        for m_re in re.finditer(r"\bRegExp\s*\(", preprocessed):
            bal_re = _extract_balanced_parens(preprocessed, m_re.end() - 1)
            if bal_re is not None:
                js_regexp_spans.append((m_re.end(), bal_re[1]))
        for m_call in re.finditer(r"[A-Za-z0-9_$]\s*\(", clean_code):
            call_head_slice = clean_code[max(0, m_call.start() - 6) : m_call.start() + 1]
            if call_head_slice.endswith("RegExp"):
                continue
            bal = _extract_balanced_parens(clean_code, m_call.end() - 1)
            if bal is None:
                continue
            for raw_arg in _split_script_top_level(bal[0], ","):
                ev_arg = _eval_js_str_expr(raw_arg, js_str_vars)
                if ev_arg:
                    extra_literals.append(ev_arg)
    elif lang == "ruby":
        preprocessed = _expand_ruby_interpolations(code)

    lit_scan_src = preprocessed
    if lang == "node" and js_regexp_spans:
        chars_buf = list(preprocessed)
        for sp_start, sp_end in js_regexp_spans:
            for k in range(sp_start, min(sp_end, len(chars_buf))):
                chars_buf[k] = " "
        lit_scan_src = "".join(chars_buf)

    folded, _ = _fold_inline_concat(preprocessed)
    _, folded_literals = _fold_inline_concat(lit_scan_src)

    if lang == "node":
        for p_alias in js_process_aliases:
            esc = re.escape(p_alias)
            if (
                re.search(rf"\b{esc}\.env\b", clean_code)
                or re.search(rf"\b{esc}\s*\[\s*(['\"])env\1\s*\]", folded)
                or re.search(rf"\{{\s*[^}}]*\benv\b[^}}]*\}}\s*=\s*{esc}\b", clean_code)
                or re.search(rf"\bReflect\.get\s*\(\s*{esc}\s*,\s*(['\"])env\1\s*\)", folded)
            ):
                return "deny", "Accès à `process.env` dans `node` inline bloqué."
            for m_idx in re.finditer(rf"\b{esc}\s*\[", clean_code):
                bal_b = _extract_balanced_brackets(clean_code, m_idx.end() - 1)
                if bal_b is None:
                    continue
                idx_expr = bal_b[0].strip()
                idx_cands: List[str] = []
                ev = _eval_js_str_expr(idx_expr, js_str_vars)
                if ev:
                    idx_cands.append(ev)
                if idx_expr in js_str_alts:
                    idx_cands.extend(js_str_alts[idx_expr])
                if any(v == "env" for v in idx_cands):
                    return "deny", "Accès indirect à `process.env` dans `node` inline bloqué."
    elif lang == "perl" and re.search(r"(?:\$ENV\s*\{|%ENV\b)", code):
        return "deny", "Accès à `%ENV`/`$ENV` dans `perl` inline bloqué."
    elif lang == "ruby" and (
        re.search(
            r"\bENV\s*(?:\[|\.(?:to_h|to_hash|each|fetch|values|keys|inspect|to_a)\b)", code
        )
        or re.search(r"\bconst_get\s*\(\s*(['\"])ENV\1\s*\)", folded)
    ):
        return "deny", "Accès à `ENV` dans `ruby` inline bloqué."

    raw_literals = [val for kind, _, val in _tokenize_script_literals(lit_scan_src) if kind == "str"]
    for lit in raw_literals + folded_literals + extra_literals:
        if not lit:
            continue
        if is_harness_protected(lit, ws_root):
            return "deny", f"Code `{lang}` inline ciblant les garde-fous Gravity bloqué."
        if matches_any(lit, SENSITIVE_PATH_PATTERNS):
            return "deny", f"Code `{lang}` inline ciblant un chemin sensible bloqué."

    if lang == "node":
        node_rm_re = re.compile(
            r"(?:\b(?:rmSync|rmdirSync|rm)|\[\s*(['\"])(?:rmSync|rmdirSync|rm)\1\s*\])\s*\("
        )
        for m in node_rm_re.finditer(folded):
            bal = _extract_balanced_parens(folded, m.end() - 1)
            if bal is None:
                continue
            call_args = _split_script_top_level(bal[0], ",")
            if not call_args:
                continue
            arg0 = call_args[0].strip()
            target_cands: List[str] = []
            t0 = _eval_js_str_expr(arg0, js_str_vars, allow_dynamic_placeholder=True)
            if t0:
                target_cands.append(t0)
            if arg0 in js_str_alts:
                target_cands.extend(js_str_alts[arg0])
            for target in target_cands:
                if is_harness_protected(target, ws_root) or _is_active_plugin_root_path(
                    target, ws_root
                ):
                    return "deny", "Suppression des garde-fous Gravity dans `node` inline bloquée."
                if _is_destructive_target(target):
                    return "deny", "Suppression récursive destructrice dans `node` inline bloquée."
    else:
        rm_call_patterns = {
            "perl": re.compile(r"\b(?:rmtree|remove_tree)\s*\(\s*(['\"])([^'\"]+)\1"),
            "ruby": re.compile(r"\b(?:rm_rf|rm_r|rmtree)\s*\(?\s*(['\"])([^'\"]+)\1"),
        }
        rm_re = rm_call_patterns.get(lang)
        if rm_re:
            for m in rm_re.finditer(folded):
                target = m.group(2)
                if is_harness_protected(target, ws_root) or _is_active_plugin_root_path(
                    target, ws_root
                ):
                    return "deny", f"Suppression des garde-fous Gravity dans `{lang}` inline bloquée."
                if _is_destructive_target(target):
                    return "deny", f"Suppression récursive destructrice dans `{lang}` inline bloquée."

    if _depth < MAX_SUBSHELL_DEPTH:
        shell_payloads: List[str] = []
        if lang == "node":
            node_exec_re = re.compile(
                r"(?:\b(?:execSync|exec|spawnSync|spawn|execFileSync|execFile)|"
                r"\[\s*(['\"])(?:execSync|exec|spawnSync|spawn|execFileSync|execFile)\1\s*\])\s*\("
            )
            for m in node_exec_re.finditer(folded):
                bal = _extract_balanced_parens(folded, m.end() - 1)
                if bal is None:
                    continue
                call_args = _split_script_top_level(bal[0], ",")
                if not call_args:
                    continue
                arg0 = call_args[0].strip()
                head_cands: List[str] = []
                h0 = _eval_js_str_expr(arg0, js_str_vars, allow_dynamic_placeholder=True)
                if h0:
                    head_cands.append(h0)
                if arg0 in js_str_alts:
                    for v in js_str_alts[arg0]:
                        if v not in head_cands:
                            head_cands.append(v)
                if not head_cands:
                    continue
                arr_cands: List[List[str]] = []
                if len(call_args) >= 2:
                    arg1 = call_args[1].strip()
                    a0 = _parse_js_array_expr(arg1, js_str_vars, js_arr_vars)
                    if a0 is not None:
                        arr_cands.append(a0)
                    if arg1 in js_arr_alts:
                        for av in js_arr_alts[arg1]:
                            if av not in arr_cands:
                                arr_cands.append(av)
                for cmd_head in head_cands:
                    if arr_cands:
                        for arr_args in arr_cands:
                            shell_payloads.append(_argv_to_shell_cmd([cmd_head] + arr_args))
                    else:
                        shell_payloads.append(cmd_head)
        elif lang in ("perl", "ruby"):
            for m in re.finditer(r"\b(?:system|exec)\s*\(", folded):
                bal = _extract_balanced_parens(folded, m.end() - 1)
                if bal is None:
                    continue
                raw_parts = _split_script_top_level(bal[0], ",")
                parsed_args: List[str] = []
                for rp in raw_parts:
                    unq = _unescape_quoted_literal(rp)
                    if unq is not None:
                        parsed_args.append(unq)
                if len(parsed_args) == 1:
                    shell_payloads.append(parsed_args[0])
                elif len(parsed_args) > 1:
                    shell_payloads.append(_argv_to_shell_cmd(parsed_args))
            for m in re.finditer(r"`([^`]+)`", folded):
                shell_payloads.append(m.group(1))
            if lang == "perl":
                for m in re.finditer(r"\bqx\s*([/({|])([^/)}|]+)[/)}|]", folded):
                    shell_payloads.append(m.group(2))
            if lang == "ruby":
                for m in re.finditer(r"%x\s*([/({\[])([^/)}\]]+)[/)}\]]", folded):
                    shell_payloads.append(m.group(2))

        for inner_cmd in shell_payloads:
            deny_reason, _ = _evaluate_shell_command(inner_cmd, ws_root, _depth=_depth + 1)
            if deny_reason:
                return "deny", deny_reason

        for expr_code in embedded_exprs:
            sub_res = _inspect_inline_script_code("node", expr_code, ws_root=ws_root, _depth=_depth + 1)
            if sub_res is not None and sub_res[0] == "deny":
                return sub_res

    return None


def _get_stdin_interpreter_kind(
    head: str, rich_args: List[Tuple[str, List[Tuple[str, str]], bool]]
) -> Optional[str]:
    """Retourne le type d'interpréteur (`python`|`node`|`perl`|`ruby`|`shell`) si le segment lit son code sur stdin."""
    if not head:
        return None
    args = [tok for tok, _, _ in rich_args]
    inline_spec = _extract_inline_spec(head, rich_args)
    if inline_spec is not None:
        spec_kind, _, spec_payloads = inline_spec
        if head in ("pwsh", "powershell") and spec_kind == "ps_command":
            if spec_payloads and spec_payloads[0].strip() == "-":
                return "shell"
        if spec_kind == "python":
            for p in spec_payloads:
                if re.search(r"\b(?:exec|eval|compile)\s*\(", p) and re.search(
                    r"\b(?:stdin|input\s*\(|open\s*\(\s*0\b)", p
                ):
                    return "python"
        if spec_kind == "node":
            for p in spec_payloads:
                if re.search(r"\b(?:eval|Function|runInThisContext)\b", p) and re.search(
                    r"(?:stdin|readFileSync\s*\(\s*0\b)", p
                ):
                    return "node"
        return None

    if _is_python_head(head):
        i = 0
        n = len(args)
        while i < n:
            tok = args[i]
            if tok == "-":
                return "python"
            if tok == "--":
                return "python" if (i + 1 < n and args[i + 1] == "-") else None
            if not tok.startswith("-"):
                return None
            if tok in PYTHON_VALUE_OPTS:
                i += 2
                continue
            if tok.startswith("--") or PY_VERSION_OPT_RE.match(tok):
                i += 1
                continue
            short = tok[1:]
            if "c" in short or "m" in short:
                return None
            advance_extra = 0
            for k, ch in enumerate(short):
                if ch in ("W", "X"):
                    if k == len(short) - 1:
                        advance_extra = 1
                    break
            i += 1 + advance_extra
        return "python"

    for interp_name, val_opts in (
        ("node", NODE_VALUE_OPTS),
        ("perl", {"-I"}),
        ("ruby", RUBY_VALUE_OPTS),
    ):
        if head == interp_name:
            i = 0
            n = len(args)
            while i < n:
                tok = args[i]
                if tok == "-":
                    return interp_name
                if tok == "--":
                    return interp_name if (i + 1 < n and args[i + 1] == "-") else None
                if not tok.startswith("-"):
                    return None
                if tok in val_opts:
                    i += 2
                    continue
                i += 1
            return interp_name

    if head in ("sh", "bash", "zsh", "dash"):
        i = 0
        n = len(args)
        saw_s = False
        while i < n:
            tok = args[i]
            if tok == "-":
                return "shell"
            if tok == "--":
                return "shell" if saw_s or (i + 1 < n and args[i + 1] == "-") else None
            if not tok.startswith("-") and not tok.startswith("+"):
                return "shell" if saw_s else None
            if tok in ("-o", "+o", "--rcfile", "--init-file"):
                i += 2
                continue
            if not tok.startswith("--") and tok.startswith("-") and "s" in tok[1:]:
                saw_s = True
            i += 1
        return "shell"

    if head in ("pwsh", "powershell"):
        i = 0
        n = len(args)
        while i < n:
            tok = args[i]
            if tok == "-":
                return "shell"
            if not tok.startswith("-"):
                return None
            low_flag = tok.lower().split(":", 1)[0]
            if low_flag in ("-f", "-file"):
                return None
            if low_flag in PS_VALUE_OPTS:
                i += 1 if ":" in tok else 2
                continue
            i += 1
        return "shell"

    if head in ("iex", "invoke-expression") and not args:
        return "shell"

    return None


def _inspect_stdin_payload(
    interp_kind: str,
    payload: str,
    ws_root: Optional[Path] = None,
    _depth: int = 0,
) -> Tuple[Optional[str], Optional[str]]:
    """Inspecte un script transmis via stdin (`@'...'@ | python -`, heredoc, `echo ... | sh`) contre les menaces `deny`."""
    from .evaluator import _evaluate_shell_command

    if not payload or not payload.strip():
        return None, None
    if interp_kind == "python":
        py_dec, py_reason = inspect_inline_python_code(payload, ws_root=ws_root, _depth=_depth)
        if py_dec == "deny":
            return py_reason or "Code Python transmis sur stdin bloqué.", None
        return None, None
    if interp_kind in ("node", "perl", "ruby"):
        res = _inspect_inline_script_code(interp_kind, payload, ws_root=ws_root, _depth=_depth)
        if res is not None and res[0] == "deny":
            return res[1], None
        return None, None
    if interp_kind == "shell" and _depth < MAX_SUBSHELL_DEPTH:
        deny_reason, _ = _evaluate_shell_command(payload, ws_root, _depth=_depth + 1)
        if deny_reason:
            return deny_reason, None
    return None, None


def _collect_ps_var_assignment(stage: str) -> Optional[Tuple[str, str, bool]]:
    """Reconnaît une affectation PowerShell simple `$var = @'...'@` ou `$var=@'...'@` -> `(var_low, val, is_herestring)`."""
    m = re.match(r"^\s*(\$[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", stage, re.DOTALL)
    if not m:
        return None
    var_name = m.group(1)
    rhs_tokens = _tokenize_shell_segment_rich(m.group(2))
    if len(rhs_tokens) == 1:
        val, chunks, _ = rhs_tokens[0]
        is_hs = len(chunks) == 1 and chunks[0][0] in (
            "ps_single_herestring",
            "ps_double_herestring",
        )
        is_quoted = bool(chunks) and all(
            ck in ("single", "double", "ps_single_herestring", "ps_double_herestring")
            for ck, _ in chunks
        )
        if is_hs or is_quoted:
            return var_name.lower(), val, is_hs
    return None


def _extract_piped_literal_payload(
    stage: str, ps_vars: Dict[str, str]
) -> Tuple[Optional[str], bool]:
    """Extrait `(payload, is_bare_herestring_or_var)` d'une étape productrice (`@'...'@`, `$var`, `echo`, `Write-Output`)."""
    stripped_stage = stage.strip()
    if stripped_stage.startswith("(") and stripped_stage.endswith(")"):
        inner_stage = stripped_stage[1:-1].strip()
        if inner_stage:
            stripped_stage = inner_stage
    rich_tokens = _tokenize_shell_segment_rich(stripped_stage)
    if not rich_tokens:
        return None, False

    if len(rich_tokens) == 1:
        tok, chunks, _ = rich_tokens[0]
        if len(chunks) == 1 and chunks[0][0] in ("ps_single_herestring", "ps_double_herestring"):
            return tok, True
        if tok.lower() in ps_vars:
            return ps_vars[tok.lower()], True
        if all(ck in ("single", "double") for ck, _ in chunks) and chunks:
            return tok, True

    head, rich_args, _ = _extract_command_head_args_rich(stage)
    if head in TEXT_OUTPUT_HEADS and rich_args:
        payload_parts: List[str] = []
        i = 0
        while i < len(rich_args):
            tok, _, _ = rich_args[i]
            low = tok.lower()
            if head == "echo" and i == 0 and tok in ("-n", "-e", "-E", "-ne", "-en"):
                i += 1
                continue
            if head in ("write-output", "write-host") and low in ("-inputobject", "-noenumerate"):
                i += 1
                continue
            if tok.lower() in ps_vars:
                payload_parts.append(ps_vars[tok.lower()])
            else:
                payload_parts.append(tok)
            i += 1
        if payload_parts:
            return " ".join(payload_parts), False
    return None, False
