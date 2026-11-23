#!/usr/bin/env python3
"""Segmentation shell, tokenisation PowerShell/POSIX, sous-expressions, heredocs et redirections."""

import posixpath
import re
import shlex
from typing import List, Optional, Tuple

from .constants import (
    ENV_ASSIGN_RE,
    ENV_VALUE_OPTS,
    MAX_SUBSHELL_DEPTH,
    POSIX_HEREDOC_OP_RE,
    PYTHON_VERSIONED_HEAD_RE,
    SUDO_VALUE_OPTS,
)


def _find_matching_paren(text: str, start: int) -> Tuple[str, int]:
    """Retourne `(corps_interne, index_apres_parenthese)` pour `$(...)` en respectant les guillemets."""
    stack = [{"single": False, "double": False}]
    j = start
    n = len(text)
    while j < n:
        ch = text[j]
        top = stack[-1]
        if top["single"]:
            if ch == "'":
                top["single"] = False
            j += 1
            continue
        if top["double"]:
            if ch in ("\\", "`") and j + 1 < n and text[j + 1] == '"':
                j += 2
                continue
            if ch == '"':
                top["double"] = False
                j += 1
                continue
            if ch == "$" and j + 1 < n and text[j + 1] == "(":
                stack.append({"single": False, "double": False})
                j += 2
                continue
            j += 1
            continue
        if ch == "'":
            top["single"] = True
            j += 1
            continue
        if ch == '"':
            top["double"] = True
            j += 1
            continue
        if ch == "$" and j + 1 < n and text[j + 1] == "(":
            stack.append({"single": False, "double": False})
            j += 2
            continue
        if ch == "(":
            stack.append({"single": False, "double": False})
            j += 1
            continue
        if ch == ")":
            stack.pop()
            if not stack:
                return text[start:j], j + 1
            j += 1
            continue
        j += 1
    return text[start:], n


def _find_matching_backtick(
    text: str, start: int, stop_on_unescaped_dquote: bool = False
) -> Optional[Tuple[str, int]]:
    """Retourne `(corps_interne, index_apres_backtick)` pour `` `...` `` ou None si non apparié."""
    j = start
    n = len(text)
    while j < n:
        ch = text[j]
        if ch == "\\" and j + 1 < n and text[j + 1] in ("`", '"'):
            j += 2
            continue
        if stop_on_unescaped_dquote and ch == '"':
            return None
        if ch == "`":
            return text[start:j], j + 1
        j += 1
    return None


def _match_ps_herestring(text: str, start: int) -> Optional[Tuple[str, str, int]]:
    """Retourne `(quote_char, corps_interne, index_apres)` pour `@'\\n...\\n'@` ou `@\"\\n...\\n\"@`."""
    if start + 1 >= len(text) or text[start] != "@" or text[start + 1] not in ("'", '"'):
        return None
    q = text[start + 1]
    n = len(text)
    pos = start + 2
    while pos < n and text[pos] in (" ", "\t"):
        pos += 1
    if pos < n and text[pos] == "\r":
        pos += 1
    if pos >= n or text[pos] != "\n":
        return None
    body_start = pos + 1
    close_tok = q + "@"
    if text[body_start : body_start + 2] == close_tok:
        after = body_start + 2
        if after == n or text[after].isspace() or text[after] in ";|&).,>+":
            return q, "", after
    match = re.search(rf"(?:\r?\n){re.escape(close_tok)}(?=$|[\s;|&).,>+])", text[body_start:])
    if not match:
        return None
    return q, text[body_start : body_start + match.start()], body_start + match.end()


def _extract_subexprs_from_text(text: str) -> List[str]:
    """Extrait les sous-expressions `$(...)` contenues dans une chaîne interpolée PowerShell."""
    subcmds: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "$" and i + 1 < n and text[i + 1] == "(":
            inner, end_idx = _find_matching_paren(text, i + 2)
            if inner.strip():
                subcmds.append(inner)
            i = end_idx
            continue
        i += 1
    return subcmds


def _split_shell_chains(cmd: str) -> Tuple[List[List[str]], List[str]]:
    """Découpe une commande shell en chaînes de pipes `[[stage, ...], ...]` + sous-commandes `$(...)`/backticks."""
    chains: List[List[str]] = []
    current_chain: List[str] = []
    extracted_subcmds: List[str] = []
    current: List[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)

    def _flush_stage() -> None:
        stage_text = "".join(current).strip()
        if stage_text:
            current_chain.append(stage_text)
        current.clear()

    def _flush_chain() -> None:
        _flush_stage()
        if current_chain:
            chains.append(list(current_chain))
            current_chain.clear()

    while i < n:
        ch = cmd[i]
        if in_single:
            current.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            if ch in ("\\", "`") and i + 1 < n and cmd[i + 1] == '"':
                current.append(cmd[i : i + 2])
                i += 2
                continue
            if ch == '"':
                current.append(ch)
                in_double = False
                i += 1
                continue
            if ch == "$" and i + 1 < n and cmd[i + 1] == "(":
                inner, end_idx = _find_matching_paren(cmd, i + 2)
                current.append(cmd[i:end_idx])
                if inner.strip():
                    extracted_subcmds.append(inner)
                i = end_idx
                continue
            if ch == "`":
                bt = _find_matching_backtick(cmd, i + 1, stop_on_unescaped_dquote=True)
                if bt is not None:
                    inner, end_idx = bt
                    current.append(cmd[i:end_idx])
                    if inner.strip():
                        extracted_subcmds.append(inner)
                    i = end_idx
                    continue
            current.append(ch)
            i += 1
            continue

        if ch == "@":
            hs = _match_ps_herestring(cmd, i)
            if hs is not None:
                q_char, body, end_idx = hs
                current.append(cmd[i:end_idx])
                if q_char == '"':
                    extracted_subcmds.extend(_extract_subexprs_from_text(body))
                i = end_idx
                continue
        if ch == "'":
            in_single = True
            current.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            current.append(ch)
            i += 1
            continue
        if ch == "$" and i + 1 < n and cmd[i + 1] == "(":
            inner, end_idx = _find_matching_paren(cmd, i + 2)
            current.append(cmd[i:end_idx])
            if inner.strip():
                extracted_subcmds.append(inner)
            i = end_idx
            continue
        if ch == "`":
            bt = _find_matching_backtick(cmd, i + 1, stop_on_unescaped_dquote=False)
            if bt is not None:
                inner, end_idx = bt
                current.append(cmd[i:end_idx])
                if inner.strip():
                    extracted_subcmds.append(inner)
                i = end_idx
                continue
        if ch in ("\r", "\n", ";"):
            _flush_chain()
            i += 1
            continue
        if ch == "|":
            if i + 1 < n and cmd[i + 1] == "|":
                _flush_chain()
                i += 2
            else:
                _flush_stage()
                i += 1
            continue
        if ch == "&":
            if i + 1 < n and cmd[i + 1] == "&":
                _flush_chain()
                i += 2
                continue
            prev_ch = cmd[i - 1] if i > 0 else ""
            next_ch = cmd[i + 1] if i + 1 < n else ""
            is_redir = prev_ch in (">", "<") or next_ch == ">"
            is_leading_call_op = not "".join(current).strip()
            if not is_redir and not is_leading_call_op:
                _flush_chain()
                i += 1
                continue

        current.append(ch)
        i += 1

    _flush_chain()
    return chains, extracted_subcmds


def split_shell_segments(
    cmd: str, _depth: int = 0, max_depth: int = MAX_SUBSHELL_DEPTH
) -> List[str]:
    """Découpe une commande shell par séparateurs hors guillemets et extrait `$(...)`/backticks."""
    chains, extracted_subcmds = _split_shell_chains(cmd)
    segments: List[str] = []
    for chain in chains:
        segments.extend(chain)
    if _depth < max_depth:
        for sub in extracted_subcmds:
            segments.extend(split_shell_segments(sub, _depth + 1, max_depth=max_depth))
    return segments


def _tokenize_shell_segment_rich(
    segment: str,
) -> List[Tuple[str, List[Tuple[str, str]], bool]]:
    """Tokenise un segment shell en conservant `(valeur, chunks, unterminated)`."""
    tokens: List[Tuple[str, List[Tuple[str, str]], bool]] = []
    buf: List[str] = []
    chunks: List[Tuple[str, str]] = []
    unquoted_buf: List[str] = []
    has_token = False
    unterminated = False
    in_single = False
    in_double = False
    raw_start = 0
    i = 0
    n = len(segment)

    def _flush_unquoted() -> None:
        if unquoted_buf:
            chunks.append(("unquoted", "".join(unquoted_buf)))
            unquoted_buf.clear()

    def _flush_token() -> None:
        nonlocal has_token, unterminated
        _flush_unquoted()
        if has_token:
            tokens.append(("".join(buf), list(chunks), unterminated))
            buf.clear()
            chunks.clear()
            has_token = False
            unterminated = False

    while i < n:
        ch = segment[i]
        if in_single:
            if ch == "'":
                chunks.append(("single", segment[raw_start:i]))
                in_single = False
            else:
                buf.append(ch)
            i += 1
            continue
        if in_double:
            if ch in ("\\", "`") and i + 1 < n and segment[i + 1] == '"':
                buf.append('"')
                i += 2
                continue
            if ch == '"':
                chunks.append(("double", segment[raw_start:i]))
                in_double = False
            else:
                buf.append(ch)
            i += 1
            continue

        if ch.isspace():
            _flush_token()
            i += 1
            continue
        if ch == "@":
            hs = _match_ps_herestring(segment, i)
            if hs is not None:
                q_char, body, end_idx = hs
                _flush_unquoted()
                has_token = True
                buf.append(body)
                kind = "ps_single_herestring" if q_char == "'" else "ps_double_herestring"
                chunks.append((kind, body))
                i = end_idx
                continue
        if ch == "'":
            _flush_unquoted()
            in_single = True
            has_token = True
            raw_start = i + 1
            i += 1
            continue
        if ch == '"':
            _flush_unquoted()
            in_double = True
            has_token = True
            raw_start = i + 1
            i += 1
            continue

        has_token = True
        buf.append(ch)
        unquoted_buf.append(ch)
        i += 1

    if in_single:
        unterminated = True
        chunks.append(("single", segment[raw_start:]))
    elif in_double:
        unterminated = True
        chunks.append(("double", segment[raw_start:]))
    _flush_token()
    return tokens


def _tokenize_shell_segment(segment: str) -> List[str]:
    """Tokenise un segment shell en préservant les backslashes Windows hors guillemets."""
    return [tok for tok, _, _ in _tokenize_shell_segment_rich(segment)]


def _reconstruct_ps_single_unescaped(chunks: List[Tuple[str, str]]) -> Optional[str]:
    """Reconstruit la valeur PowerShell si le token comporte des quotes simples doublées `''`."""
    if len(chunks) < 2:
        return None
    has_adjacent_single = any(
        chunks[idx][0] == "single" and chunks[idx + 1][0] == "single"
        for idx in range(len(chunks) - 1)
    )
    if not has_adjacent_single:
        return None
    out: List[str] = []
    for idx, (kind, raw) in enumerate(chunks):
        if idx > 0 and kind == "single" and chunks[idx - 1][0] == "single":
            out.append("'")
        out.append(raw)
    return "".join(out)


def _normalize_cmd_name(tok: str) -> str:
    """Normalise un nom d'exécutable (slashs, casse, extension `.exe`)."""
    cleaned = tok.replace("\\", "/").strip()
    if not cleaned:
        return ""
    head = posixpath.basename(cleaned).lower()
    if head.endswith(".exe"):
        head = head[:-4]
    return head


def _extract_command_head_args_rich(
    segment: str,
) -> Tuple[str, List[Tuple[str, List[Tuple[str, str]], bool]], bool]:
    """Extrait `(tete, rich_args, wrapped_in_sudo)` en déballant `VAR=val`, `&`, `sudo`, `env`, `nohup`, `time`, `command`."""
    rich_tokens = _tokenize_shell_segment_rich(segment)
    if not rich_tokens:
        return "", [], False

    tokens = [tok for tok, _, _ in rich_tokens]
    wrapped_in_sudo = False
    idx = 0
    n = len(tokens)
    while idx < n:
        tok = tokens[idx]
        if tok == "&":
            idx += 1
            continue
        if tok.startswith("&") and len(tok) > 1:
            tokens[idx] = tok[1:]
            raw_tok, raw_chunks, raw_unterm = rich_tokens[idx]
            new_chunks = list(raw_chunks)
            if (
                new_chunks
                and new_chunks[0][0] == "unquoted"
                and new_chunks[0][1].startswith("&")
            ):
                trimmed = new_chunks[0][1][1:]
                if trimmed:
                    new_chunks[0] = ("unquoted", trimmed)
                else:
                    new_chunks.pop(0)
            rich_tokens[idx] = (raw_tok[1:], new_chunks, raw_unterm)
            continue
        if ENV_ASSIGN_RE.match(tok):
            idx += 1
            continue

        norm = _normalize_cmd_name(tok)
        if norm == "nohup":
            if idx + 1 < n:
                idx += 1
                continue
            break
        if norm in ("time", "command"):
            j = idx + 1
            while j < n:
                opt = tokens[j]
                if opt == "--":
                    j += 1
                    break
                if opt == "-p":
                    j += 1
                    continue
                break
            if j < n:
                idx = j
                continue
            break
        if norm == "sudo":
            wrapped_in_sudo = True
            j = idx + 1
            while j < n:
                opt = tokens[j]
                if opt == "--":
                    j += 1
                    break
                if opt in SUDO_VALUE_OPTS:
                    j += 2
                    continue
                if opt.startswith("-") and len(opt) > 1:
                    j += 1
                    continue
                break
            if j < n:
                idx = j
                continue
            break
        if norm == "env":
            j = idx + 1
            while j < n:
                opt = tokens[j]
                if opt == "--":
                    j += 1
                    break
                if opt in ENV_VALUE_OPTS:
                    j += 2
                    continue
                if opt.startswith("-") and len(opt) > 1:
                    j += 1
                    continue
                if ENV_ASSIGN_RE.match(opt):
                    j += 1
                    continue
                break
            if j < n:
                idx = j
                continue
            return "env", rich_tokens[idx + 1 :], wrapped_in_sudo
        break

    if idx >= n:
        return "", [], wrapped_in_sudo
    head = _normalize_cmd_name(tokens[idx])
    return head, rich_tokens[idx + 1 :], wrapped_in_sudo


def extract_command_head_and_args(segment: str) -> Tuple[str, List[str]]:
    """Extrait `(tete_normalisee, args)` en déballant `VAR=val`, `&`, `sudo`, `env`, `nohup`, `time`, `command`."""
    head, rich_args, _ = _extract_command_head_args_rich(segment)
    return head, [tok for tok, _, _ in rich_args]


def _is_python_head(head: str) -> bool:
    return head in {"python", "python3", "py"} or bool(PYTHON_VERSIONED_HEAD_RE.match(head))


def _skip_quoted_char(text: str, i: int, in_single: bool, in_double: bool) -> Tuple[bool, bool, Optional[int]]:
    """Avance l'index si `text[i]` est à l'intérieur d'une quote simple/double shell."""
    ch = text[i]
    n = len(text)
    if in_single:
        return (ch != "'"), False, i + 1
    if in_double:
        if ch in ("\\", "`") and i + 1 < n and text[i + 1] == '"':
            return False, True, i + 2
        return False, (ch != '"'), i + 1
    if ch == "'":
        return True, False, i + 1
    if ch == '"':
        return False, True, i + 1
    return False, False, None


def _extract_redirections(segment: str) -> List[Tuple[str, str]]:
    """Extrait les redirections `(direction, cible)` hors guillemets (hors `>&1`/`>&2`/`>&-` et `<<`)."""
    redirs: List[Tuple[str, str]] = []
    in_single = False
    in_double = False
    i = 0
    n = len(segment)
    while i < n:
        in_single, in_double, nxt = _skip_quoted_char(segment, i, in_single, in_double)
        if nxt is not None:
            i = nxt
            continue
        ch = segment[i]
        if ch == "@":
            hs = _match_ps_herestring(segment, i)
            if hs is not None:
                i = hs[2]
                continue
        if ch in (">", "<"):
            if ch == ">" and i + 1 < n and segment[i + 1] == "&":
                if i + 2 < n and (segment[i + 2].isdigit() or segment[i + 2] == "-"):
                    i += 3
                    continue
            if ch == "<" and i + 1 < n and segment[i + 1] == "<":
                i += 2
                continue
            j = i + 1
            if j < n and segment[j] == ch:
                j += 1
            if j < n and segment[j] == "&":
                j += 1
            while j < n and segment[j].isspace():
                j += 1
            if j >= n:
                redirs.append((ch, ""))
                break
            if segment[j] in ("'", '"'):
                q = segment[j]
                j += 1
                start = j
                while j < n and segment[j] != q:
                    if q == '"' and segment[j] in ("\\", "`") and j + 1 < n and segment[j + 1] == '"':
                        j += 2
                        continue
                    j += 1
                redirs.append((ch, segment[start:j]))
                i = j + 1
                continue
            start = j
            while j < n and not segment[j].isspace() and segment[j] not in ";|&()<>":
                j += 1
            redirs.append((ch, segment[start:j]))
            i = j
            continue
        i += 1
    return redirs


def _has_unquoted_redirection(segment: str) -> bool:
    """Retourne True si `segment` contient `>`/`>>` hors guillemets (hors `>&1`/`>&2`/`>&-`)."""
    return any(direction == ">" for direction, _ in _extract_redirections(segment))


def _extract_redirection_targets(segment: str) -> List[str]:
    """Extrait les cibles de redirection (`>`, `>>`, `<`, `2>`) hors guillemets dans `segment`."""
    return [target for _, target in _extract_redirections(segment) if target]


def _argv_to_shell_cmd(argv: List[str]) -> str:
    """Reconstruit une commande shell quotée de manière sûre à partir d'une liste `argv`."""
    parts: List[str] = []
    for arg in argv:
        if any(ch.isspace() or ch in "\"'`;|&()<>\\$" for ch in arg) or not arg:
            parts.append(shlex.quote(arg))
        else:
            parts.append(arg)
    return " ".join(parts)


def _find_unquoted_heredoc_ops(line: str) -> List[Tuple[bool, str]]:
    """Extrait tous les opérateurs heredoc POSIX `<<`/`<<-` hors guillemets sur une ligne (hors `<<<`)."""
    ops: List[Tuple[bool, str]] = []
    in_sq = False
    in_dq = False
    i = 0
    n = len(line)
    while i < n:
        in_sq, in_dq, nxt = _skip_quoted_char(line, i, in_sq, in_dq)
        if nxt is not None:
            i = nxt
            continue
        if line[i] == "<" and i + 1 < n and line[i + 1] == "<":
            if i + 2 < n and line[i + 2] == "<":
                i += 3
                continue
            m = POSIX_HEREDOC_OP_RE.match(line, i)
            if m:
                strip_tabs = bool(m.group(1))
                delim = m.group(2) or m.group(3) or m.group(4) or ""
                if delim:
                    ops.append((strip_tabs, delim))
                i = m.end()
                continue
            i += 2
            continue
        i += 1
    return ops


def _extract_posix_heredocs(cmd: str) -> Tuple[str, List[Tuple[str, str, int]]]:
    """Extrait tous les heredocs POSIX `<< 'EOF'\\n...\\nEOF` (y compris multiples par ligne) sans altérer `@'...'@`."""
    if "<<" not in cmd or "\n" not in cmd:
        return cmd, []

    lines = cmd.splitlines()
    out_lines: List[str] = []
    heredocs: List[Tuple[str, str, int]] = []
    i = 0
    n = len(lines)
    ps_hs_close: Optional[str] = None

    while i < n:
        line = lines[i]
        if ps_hs_close is not None:
            out_lines.append(line)
            if line.startswith(ps_hs_close) and (
                len(line) == 2 or line[2].isspace() or line[2] in ";|&).,>+"
            ):
                ps_hs_close = None
            i += 1
            continue

        stripped_r = line.rstrip(" \t")
        if stripped_r.endswith("@'") or stripped_r.endswith('@"'):
            ps_hs_close = stripped_r[-1] + "@"
            out_lines.append(line)
            i += 1
            continue

        ops = _find_unquoted_heredoc_ops(line)
        if ops:
            j = i + 1
            extracted_for_line: List[Tuple[str, str, int]] = []
            for op_idx, (strip_tabs, delim) in enumerate(ops):
                body_lines: List[str] = []
                found_end = False
                k = j
                while k < n:
                    cand = lines[k].lstrip("\t") if strip_tabs else lines[k]
                    if cand == delim:
                        found_end = True
                        break
                    body_lines.append(lines[k])
                    k += 1
                if found_end:
                    extracted_for_line.append((line, "\n".join(body_lines), op_idx))
                    j = k + 1
                else:
                    break
            if extracted_for_line:
                out_lines.append(line)
                heredocs.extend(extracted_for_line)
                i = j
                continue

        out_lines.append(line)
        i += 1

    return "\n".join(out_lines), heredocs


def _extract_bash_herestring_payload(segment: str) -> Optional[str]:
    """Extrait le payload d'un here-string bash `<<< \"...\"` hors guillemets."""
    in_sq = False
    in_dq = False
    i = 0
    n = len(segment)
    while i < n:
        in_sq, in_dq, nxt = _skip_quoted_char(segment, i, in_sq, in_dq)
        if nxt is not None:
            i = nxt
            continue
        if segment[i : i + 3] == "<<<":
            tail = segment[i + 3 :].strip()
            if not tail:
                return None
            toks = _tokenize_shell_segment_rich(tail)
            return toks[0][0] if toks else None
        i += 1
    return None
