#!/usr/bin/env python3
"""Inspection des scripts inline (`node -e`, `perl -e`, `ruby -e`) et ré-exports rétrocompatibles."""

from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple

from .constants import MAX_SUBSHELL_DEPTH, SENSITIVE_PATH_PATTERNS
from .js_eval import (
    _advance_js_quoted_or_template,
    _collect_js_var_bindings,
    _decode_js_escape,
    _eval_js_str_expr,
    _expand_js_templates,
    _extract_balanced_brackets,
    _extract_balanced_delims,
    _extract_balanced_parens,
    _parse_js_array_expr,
    _scan_js_template_literal,
    _split_js_statements,
    _split_script_top_level,
    _strip_js_comments,
    _unescape_quoted_literal,
)
from .paths import _is_active_plugin_root_path, _is_destructive_target, is_harness_protected, matches_any
from .shell_parser import _argv_to_shell_cmd
from .stdin_inspect import (
    _collect_ps_var_assignment,
    _extract_inline_spec,
    _extract_piped_literal_payload,
    _get_stdin_interpreter_kind,
    _inspect_stdin_payload,
)

__all__ = [
    "_advance_js_quoted_or_template",
    "_collect_js_var_bindings",
    "_collect_ps_var_assignment",
    "_decode_js_escape",
    "_eval_js_str_expr",
    "_expand_js_templates",
    "_expand_ruby_interpolations",
    "_extract_balanced_brackets",
    "_extract_balanced_delims",
    "_extract_balanced_parens",
    "_extract_inline_spec",
    "_extract_piped_literal_payload",
    "_fold_inline_concat",
    "_get_stdin_interpreter_kind",
    "_inspect_inline_script_code",
    "_inspect_stdin_payload",
    "_parse_js_array_expr",
    "_scan_js_template_literal",
    "_split_js_statements",
    "_split_script_top_level",
    "_strip_js_comments",
    "_tokenize_script_literals",
    "_unescape_quoted_literal",
]


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
