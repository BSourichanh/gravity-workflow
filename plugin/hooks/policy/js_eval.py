#!/usr/bin/env python3
"""Scanning lexical JS, suppression de commentaires et évaluation statique de chaînes/templates JS."""

import re
from typing import Dict, List, Optional, Set, Tuple


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
