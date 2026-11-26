#!/usr/bin/env python3
"""Extraction de spécifications inline et inspection des payloads transmis sur stdin/pipes/here-strings."""

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
    TEXT_OUTPUT_HEADS,
)
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
    from .inline_scripts import _inspect_inline_script_code

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
