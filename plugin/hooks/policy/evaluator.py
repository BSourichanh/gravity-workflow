#!/usr/bin/env python3
"""Orchestration de l'évaluation shell, wrappers, pipes, heredocs, workflows et décisions fail-closed PreToolUse."""

import base64
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

_HOOKS_DIR = Path(__file__).resolve().parent.parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from hook_runtime import first_str_arg, is_gravity_workspace, resolve_workspace_root  # noqa: E402

from .constants import (
    DB_CLIENT_HEADS,
    DB_DESTRUCTIVE_RE,
    DIR_READ_TOOLS,
    DOWNLOAD_CMD_HEADS,
    FILE_READ_TOOLS,
    FILE_WRITE_TOOLS,
    FROM_BASE64_RE,
    HEREDOC_PIPE_PRODUCER_HEADS,
    MAX_SUBSHELL_DEPTH,
    PIPE_EVAL_HEADS,
    PIPE_SHELL_HEADS,
    PS_DOWNLOAD_CMD_HEADS,
    PS_DOWNLOAD_IN_EXPR_RE,
    PS_PASSTHROUGH_HEADS,
    READ_PATH_KEYS,
    SENSITIVE_PATH_PATTERNS,
    SHELL_CMD_KEYS,
    SHELL_TOOLS,
    TEXT_OUTPUT_HEADS,
    WORKFLOW_TOOLS,
    WRITE_PATH_KEYS,
    XARGS_VALUE_OPTS,
)
from .git_and_fs import (
    _check_git_config_overrides,
    _has_unquoted_secret_glob,
    _is_db_destructive_segment,
    _is_destructive_rm_segment,
    _is_env_dump_segment,
    _is_git_force_push,
    _is_rm_rf_without_targets,
    _is_search_or_git_literal_segment,
    _segment_targets_harness,
    _segment_targets_secret,
)
from .inline_scripts import (
    _collect_ps_var_assignment,
    _extract_inline_spec,
    _extract_piped_literal_payload,
    _get_stdin_interpreter_kind,
    _inspect_inline_script_code,
    _inspect_stdin_payload,
)
from .paths import (
    _get_hooks_dir,
    _is_destructive_target,
    _is_secret_matching_glob,
    is_harness_protected,
    matches_any,
)
from .python_ast import _inspect_python_ast, inspect_inline_python_code
from .shell_parser import (
    _argv_to_shell_cmd,
    _extract_bash_herestring_payload,
    _extract_command_head_args_rich,
    _extract_posix_heredocs,
    _extract_redirection_targets,
    _find_unquoted_heredoc_ops,
    _is_python_head,
    _split_shell_chains,
    extract_command_head_and_args,
)


def _try_decode_ps_encoded_command(b64_token: str) -> Optional[str]:
    """Décode si possible un payload Base64 PowerShell `-EncodedCommand` (UTF-16LE puis UTF-8)."""
    cleaned = b64_token.strip().strip("\"'")
    if not cleaned:
        return None
    pad = (-len(cleaned)) % 4
    try:
        raw = base64.b64decode(cleaned + ("=" * pad), validate=False)
    except Exception:
        return None
    if not raw:
        return None
    if len(raw) % 2 == 0:
        try:
            decoded = raw.decode("utf-16le")
            if decoded and "\x00" not in decoded:
                return decoded
        except UnicodeDecodeError:
            pass
    try:
        decoded_u8 = raw.decode("utf-8")
        if decoded_u8:
            return decoded_u8
    except UnicodeDecodeError:
        pass
    return None


def _extract_xargs_inner_command(args: List[str]) -> str:
    """Extrait la commande interne passée à `xargs` en sautant les options `xargs`."""
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]
        if tok == "--":
            i += 1
            break
        if not tok.startswith("-") or tok == "-":
            break
        if tok in XARGS_VALUE_OPTS:
            i += 2
            continue
        if any(tok.startswith(opt + "=") for opt in XARGS_VALUE_OPTS if opt.startswith("--")):
            i += 1
            continue
        if len(tok) > 2 and tok[:2] in {"-I", "-i", "-L", "-l", "-n", "-P", "-s", "-d", "-E", "-e", "-a"}:
            i += 1
            continue
        i += 1
    if i >= n:
        return ""
    return _argv_to_shell_cmd(args[i:])


def _inspect_find_segment(
    args: List[str], ws_root: Optional[Path], _depth: int
) -> Tuple[Optional[str], Optional[str]]:
    """Inspecte `find` (`-name` sensibles avec `-exec` + évaluation récursive des blocs `-exec`/`-ok`)."""
    roots: List[str] = []
    name_patterns: List[str] = []
    exec_blocks: List[List[str]] = []

    i = 0
    n = len(args)
    seen_expr = False
    while i < n:
        tok = args[i]
        low = tok.lower()
        if not seen_expr and not tok.startswith("-") and tok not in ("(", "!", ","):
            roots.append(tok)
            i += 1
            continue
        seen_expr = True
        if low in ("-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename"):
            if i + 1 < n:
                name_patterns.append(args[i + 1])
            i += 2
            continue
        if low in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            block: List[str] = []
            while j < n and args[j] not in (";", "+"):
                block.append(args[j])
                j += 1
            if block:
                exec_blocks.append(block)
            i = j + 1
            continue
        i += 1

    if not exec_blocks:
        return None, None

    for pat in name_patterns:
        if _is_secret_matching_glob(pat) or matches_any(pat, SENSITIVE_PATH_PATTERNS):
            return (
                f"Recherche `find` avec `-exec` ciblant un fichier sensible (`{pat}`) bloquée.",
                None,
            )

    first_ask: Optional[str] = None
    destructive_root = next((r for r in roots if _is_destructive_target(r)), None)
    placeholder = destructive_root if (destructive_root and not name_patterns) else "file"

    if _depth < MAX_SUBSHELL_DEPTH:
        for block in exec_blocks:
            substituted = [placeholder if tok == "{}" else tok for tok in block]
            inner_cmd = _argv_to_shell_cmd(substituted)
            deny_reason, ask_reason = _evaluate_shell_command(inner_cmd, ws_root, _depth=_depth + 1)
            if deny_reason:
                return deny_reason, None
            if ask_reason and first_ask is None:
                first_ask = ask_reason

    return None, first_ask


def _check_segment_inline_or_wrapper(
    head: str,
    rich_args: List[Tuple[str, List[Tuple[str, str]], bool]],
    wrapped_in_sudo: bool,
    ws_root: Optional[Path] = None,
    _depth: int = 0,
    ps_vars: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Inspecte les interpréteurs inline, sous-shells, `git -c`, `xargs`, `find -exec`, `eval` et `iex`."""
    if not head:
        return None, None

    args = [tok for tok, _, _ in rich_args]
    if head == "git":
        return _check_git_config_overrides(args, ws_root=ws_root, _depth=_depth)

    inline_spec = _extract_inline_spec(head, rich_args)
    if inline_spec is not None:
        kind, _, payloads = inline_spec
        if ps_vars:
            resolved_payloads: List[str] = []
            for p in payloads:
                resolved_payloads.append(ps_vars.get(p.strip().lower(), p))
            payloads = resolved_payloads

        if kind == "python":
            for code in payloads:
                py_dec, py_reason = inspect_inline_python_code(
                    code, ws_root=ws_root, _depth=_depth
                )
                if py_dec == "deny":
                    return py_reason or "Code Python inline (`-c`) bloqué.", None
            if wrapped_in_sudo:
                return (
                    None,
                    "Exécution de code Python inline (`-c`) sous `sudo` : validation humaine requise.",
                )
            return None, None

        if kind in ("node", "perl", "ruby"):
            for code in payloads:
                res = _inspect_inline_script_code(kind, code, ws_root=ws_root, _depth=_depth)
                if res is not None and res[0] == "deny":
                    return res[1], None
            if wrapped_in_sudo:
                return None, f"Exécution `{kind}` inline sous `sudo` : validation humaine requise."
            return None, None

        if kind == "ps_encoded":
            for b64_payload in payloads:
                decoded = _try_decode_ps_encoded_command(b64_payload)
                if decoded and _depth < MAX_SUBSHELL_DEPTH:
                    deny_reason, _ = _evaluate_shell_command(decoded, ws_root, _depth=_depth + 1)
                    if deny_reason:
                        return deny_reason, None
            return (
                None,
                "Sous-shell PowerShell encodé (`-EncodedCommand`) détecté : validation humaine requise.",
            )

        first_ask: Optional[str] = None
        for inner_cmd in payloads:
            if inner_cmd and _depth < MAX_SUBSHELL_DEPTH:
                deny_reason, ask_reason = _evaluate_shell_command(
                    inner_cmd, ws_root, _depth=_depth + 1
                )
                if deny_reason:
                    return deny_reason, None
                if ask_reason and first_ask is None:
                    first_ask = ask_reason
        if wrapped_in_sudo:
            return None, f"Sous-shell inline `{head}` sous `sudo` : validation humaine requise."
        return None, first_ask

    if head == "xargs":
        inner_cmd = _extract_xargs_inner_command(args)
        if inner_cmd and _depth < MAX_SUBSHELL_DEPTH:
            deny_reason, ask_reason = _evaluate_shell_command(
                inner_cmd, ws_root, _depth=_depth + 1
            )
            if deny_reason:
                return deny_reason, None
            inner_head, inner_args = extract_command_head_and_args(inner_cmd)
            if _is_rm_rf_without_targets(inner_head, inner_args):
                return (
                    None,
                    "Suppression récursive dynamique via `xargs rm -rf` détectée : validation humaine requise.",
                )
            return None, ask_reason
        return None, None

    if head == "find":
        return _inspect_find_segment(args, ws_root, _depth)

    if head == "eval":
        inner_cmd = " ".join(args).strip()
        if inner_cmd and _depth < MAX_SUBSHELL_DEPTH:
            deny_reason, _ = _evaluate_shell_command(inner_cmd, ws_root, _depth=_depth + 1)
            if deny_reason:
                return deny_reason, None
        return None, "Évaluation shell dynamique (`eval`) détectée : validation humaine requise."

    if head in ("iex", "invoke-expression"):
        inner_cmd = " ".join(args).strip()
        if PS_DOWNLOAD_IN_EXPR_RE.search(inner_cmd):
            return "Téléchargement et exécution PowerShell directe (`iex (irm ...)`) bloqués.", None
        if inner_cmd and _depth < MAX_SUBSHELL_DEPTH:
            deny_reason, _ = _evaluate_shell_command(inner_cmd, ws_root, _depth=_depth + 1)
            if deny_reason:
                return deny_reason, None
        return (
            None,
            "Évaluation PowerShell dynamique (`iex`/`Invoke-Expression`) détectée : validation humaine requise.",
        )

    return None, None


def _is_base64_decode_stage(stage: str, head: str, args: List[str]) -> bool:
    """Retourne True si l'étape décode du Base64 (`base64 -d` ou `FromBase64String` hors recherche)."""
    if head == "base64":
        return any(
            arg in ("-d", "-D", "--decode")
            or (re.match(r"^-[A-Za-z]+$", arg) and ("d" in arg[1:] or "D" in arg[1:]))
            for arg in args
        )
    if not _is_search_or_git_literal_segment(head, args) and FROM_BASE64_RE.search(stage):
        return True
    return False


def _check_pipe_chain(
    stages: List[str],
    ws_root: Optional[Path] = None,
    ps_vars: Optional[Dict[str, str]] = None,
    _depth: int = 0,
) -> Tuple[Optional[str], Optional[str], Set[int]]:
    """Évalue les menaces inter-étapes dans une même chaîne de pipes (`curl|sh`, `irm|iex`, `@'...'@ | python -`, `base64 -d | sh`)."""
    consumed_literal_stages: Set[int] = set()
    if len(stages) < 2:
        return None, None, consumed_literal_stages

    stage_info: List[Tuple[str, str, List[Tuple[str, List[Tuple[str, str]], bool]], bool, List[str]]] = []
    for st in stages:
        h, ra, sudo_wrap = _extract_command_head_args_rich(st)
        a = [tok for tok, _, _ in ra]
        stage_info.append((st, h, ra, sudo_wrap, a))

    for idx, (_, h, _, _, _) in enumerate(stage_info[:-1]):
        downstream_heads = [dh for _, dh, _, _, _ in stage_info[idx + 1 :]]
        if h in DOWNLOAD_CMD_HEADS:
            if any(dh in PIPE_SHELL_HEADS or _is_python_head(dh) for dh in downstream_heads):
                return "Téléchargement et exécution directe via pipe shell bloqués.", None, consumed_literal_stages
        if h in PS_DOWNLOAD_CMD_HEADS:
            if any(dh in ("iex", "invoke-expression") for dh in downstream_heads):
                return "Téléchargement et exécution PowerShell directe (`irm | iex`) bloqués.", None, consumed_literal_stages

    first_ask: Optional[str] = None
    var_map = ps_vars or {}
    for idx, (st, src_h, _, _, _) in enumerate(stage_info[:-1]):
        payload, is_bare_literal = _extract_piped_literal_payload(st, var_map)
        if payload is None:
            continue
        target_idx = idx + 1
        while target_idx < len(stage_info) and stage_info[target_idx][1] in PS_PASSTHROUGH_HEADS:
            target_idx += 1
        if target_idx >= len(stage_info):
            continue
        _, dst_h, dst_ra, dst_sudo, _ = stage_info[target_idx]
        interp_kind = _get_stdin_interpreter_kind(dst_h, dst_ra)
        if interp_kind is not None:
            p_deny, p_ask = _inspect_stdin_payload(interp_kind, payload, ws_root=ws_root, _depth=_depth)
            if p_deny:
                return p_deny, None, consumed_literal_stages
            if p_ask and first_ask is None:
                first_ask = p_ask
            if dst_sudo and first_ask is None:
                first_ask = f"Exécution de code `{interp_kind}` via stdin sous `sudo` : validation humaine requise."
            if is_bare_literal or (src_h in TEXT_OUTPUT_HEADS and not _extract_redirection_targets(st)):
                consumed_literal_stages.add(idx)

    for idx, (st, h, _, _, a) in enumerate(stage_info[:-1]):
        if _is_base64_decode_stage(st, h, a):
            downstream_heads = [dh for _, dh, _, _, _ in stage_info[idx + 1 :]]
            if any(dh in PIPE_EVAL_HEADS or _is_python_head(dh) for dh in downstream_heads):
                if first_ask is None:
                    first_ask = "Décodage `base64` pipé vers un interpréteur détecté : validation humaine requise."

    return None, first_ask, consumed_literal_stages


def _evaluate_shell_command(
    cmd: str, ws_root: Optional[Path] = None, _depth: int = 0
) -> Tuple[Optional[str], Optional[str]]:
    """Évalue une commande shell et retourne `(hard_deny_reason, heuristic_ask_reason)` (`deny` prioritaire)."""
    first_ask: Optional[str] = None
    cleaned_cmd, posix_heredocs = _extract_posix_heredocs(cmd)
    for header_line, heredoc_body, op_index in posix_heredocs:
        h_chains, _ = _split_shell_chains(header_line)
        hd_targets: List[Tuple[List[str], int, str]] = []
        for h_chain in h_chains:
            for h_seg_idx, h_seg in enumerate(h_chain):
                for _ in _find_unquoted_heredoc_ops(h_seg):
                    hd_targets.append((h_chain, h_seg_idx, h_seg))
        matched_targets = (
            [hd_targets[op_index]] if op_index < len(hd_targets) else hd_targets
        )
        for h_chain, h_seg_idx, h_seg in matched_targets:
            seg_before_hd = h_seg.split("<<", 1)[0].strip()
            hd_head, hd_rich, hd_sudo = _extract_command_head_args_rich(seg_before_hd)
            interp_kind = _get_stdin_interpreter_kind(hd_head, hd_rich)
            if interp_kind is not None:
                hd_deny, hd_ask = _inspect_stdin_payload(
                    interp_kind, heredoc_body, ws_root=ws_root, _depth=_depth
                )
                if hd_deny:
                    return hd_deny, None
                if hd_ask and first_ask is None:
                    first_ask = hd_ask
                if hd_sudo and first_ask is None:
                    first_ask = f"Exécution de code `{interp_kind}` via heredoc sous `sudo` : validation humaine requise."
            elif hd_head in DB_CLIENT_HEADS and DB_DESTRUCTIVE_RE.search(heredoc_body):
                return "Suppression massive de base de données bloquée.", None
            elif hd_head in HEREDOC_PIPE_PRODUCER_HEADS and h_seg_idx + 1 < len(h_chain):
                for downstream_seg in h_chain[h_seg_idx + 1 :]:
                    ds_head, ds_rich, ds_sudo = _extract_command_head_args_rich(downstream_seg)
                    if ds_head in PS_PASSTHROUGH_HEADS or ds_head in HEREDOC_PIPE_PRODUCER_HEADS:
                        continue
                    ds_interp = _get_stdin_interpreter_kind(ds_head, ds_rich)
                    if ds_interp is not None:
                        ds_deny, ds_ask = _inspect_stdin_payload(
                            ds_interp, heredoc_body, ws_root=ws_root, _depth=_depth
                        )
                        if ds_deny:
                            return ds_deny, None
                        if ds_ask and first_ask is None:
                            first_ask = ds_ask
                        if ds_sudo and first_ask is None:
                            first_ask = f"Exécution de code `{ds_interp}` via heredoc pipé sous `sudo` : validation humaine requise."
                        break
                    if ds_head in DB_CLIENT_HEADS and DB_DESTRUCTIVE_RE.search(heredoc_body):
                        return "Suppression massive de base de données bloquée.", None
                    break

    chains, extracted_subcmds = _split_shell_chains(cleaned_cmd)
    ps_vars: Dict[str, str] = {}

    for chain in chains:
        is_herestring_var_assign = False
        if len(chain) == 1:
            ps_assign = _collect_ps_var_assignment(chain[0])
            if ps_assign is not None:
                var_key, var_val, is_hs = ps_assign
                ps_vars[var_key] = var_val
                if is_hs:
                    is_herestring_var_assign = True

        pipe_deny, pipe_ask, consumed_literal_stages = _check_pipe_chain(
            chain, ws_root=ws_root, ps_vars=ps_vars, _depth=_depth
        )
        if pipe_deny:
            return pipe_deny, None
        if pipe_ask and first_ask is None:
            first_ask = pipe_ask

        chain_heads = [extract_command_head_and_args(st)[0] for st in chain]
        has_db_client = any(h in DB_CLIENT_HEADS for h in chain_heads)

        for seg_idx, seg in enumerate(chain):
            if seg_idx in consumed_literal_stages or is_herestring_var_assign:
                continue
            head, rich_args, wrapped_in_sudo = _extract_command_head_args_rich(seg)
            args = [tok for tok, _, _ in rich_args]

            if _is_destructive_rm_segment(head, args):
                return "Suppression récursive destructrice bloquée.", None
            if _is_git_force_push(seg):
                return "Force-push Git (-f / --force / +ref) bloqué.", None
            if _is_db_destructive_segment(seg, head, args, has_db_client):
                return "Suppression massive de base de données bloquée.", None
            if _is_env_dump_segment(seg, head, args):
                return "Affichage global des variables d'environnement bloqué (anti-fuite secrets).", None
            if _segment_targets_harness(seg, head, rich_args, ws_root):
                return "Tentative de modification des garde-fous Gravity bloquée.", None
            if _segment_targets_secret(seg, head, rich_args):
                return "Accès shell à un fichier sensible ou secret bloqué.", None

            if "<<<" in seg:
                hs_payload = _extract_bash_herestring_payload(seg)
                if hs_payload:
                    before_hs = seg.split("<<<", 1)[0].strip()
                    b_head, b_rich, _ = _extract_command_head_args_rich(before_hs)
                    b_interp = _get_stdin_interpreter_kind(b_head, b_rich)
                    if b_interp is not None:
                        hs_deny, hs_ask = _inspect_stdin_payload(
                            b_interp, hs_payload, ws_root=ws_root, _depth=_depth
                        )
                        if hs_deny:
                            return hs_deny, None
                        if hs_ask and first_ask is None:
                            first_ask = hs_ask

            sub_deny, sub_ask = _check_segment_inline_or_wrapper(
                head, rich_args, wrapped_in_sudo, ws_root, _depth=_depth, ps_vars=ps_vars
            )
            if sub_deny:
                return sub_deny, None
            if sub_ask and first_ask is None:
                first_ask = sub_ask

            if _has_unquoted_secret_glob(head, rich_args) and first_ask is None:
                first_ask = "Globbing non quoté ciblant un fichier sensible (ex. `.e*`) détecté : validation humaine requise."

    if _depth < MAX_SUBSHELL_DEPTH:
        for sub in extracted_subcmds:
            sub_deny, sub_ask = _evaluate_shell_command(sub, ws_root, _depth=_depth + 1)
            if sub_deny:
                return sub_deny, None
            if sub_ask and first_ask is None:
                first_ask = sub_ask

    return None, first_ask


def _iter_string_leaves(obj: Any) -> List[str]:
    """Extrait récursivement toutes les valeurs chaînes contenues dans une structure JSON."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out: List[str] = []
        for v in obj.values():
            out.extend(_iter_string_leaves(v))
        return out
    if isinstance(obj, (list, tuple)):
        out = []
        for item in obj:
            out.extend(_iter_string_leaves(item))
        return out
    return []


def _collect_write_targets(args: dict) -> List[str]:
    """Collecte tous les chemins d'écriture dans `args` et ses sous-structures d'éditions."""
    targets: List[str] = []
    stack: List[Any] = [args]
    while stack:
        curr = stack.pop()
        if isinstance(curr, dict):
            for key, val in curr.items():
                if key in WRITE_PATH_KEYS:
                    if isinstance(val, str) and val.strip():
                        targets.append(val.strip())
                    elif isinstance(val, (list, tuple)):
                        for item in val:
                            if isinstance(item, str) and item.strip():
                                targets.append(item.strip())
                            elif isinstance(item, (dict, list, tuple)):
                                stack.append(item)
                elif isinstance(val, (dict, list, tuple)):
                    stack.append(val)
        elif isinstance(curr, (list, tuple)):
            for item in curr:
                if isinstance(item, (dict, list, tuple)):
                    stack.append(item)
    return targets


def inspect_workflow_code(
    code: str, ws_root: Optional[Path] = None
) -> Optional[Tuple[str, str]]:
    """Inspecte un script `run_workflow` pour secrets, `os.environ`, tamper harnais actif et actions destructrices."""
    dec, reason = _inspect_python_ast(
        code,
        ws_root,
        context_label="run_workflow",
        allow_top_level_await=True,
        _depth=0,
    )
    if dec == "allow":
        return None
    return dec, reason or "Script `run_workflow` bloqué ou escaladé."


def evaluate_tool_call(
    data: dict, workspace_root: Optional[Path] = None
) -> Tuple[str, Optional[str]]:
    """Évalue un appel d'outil PreToolUse et retourne `(decision, reason)`.

    - Les erreurs structurelles (`toolCall` / `args` invalides) restent fail-closed (`force_ask`) partout.
    - Les règles fermes (`deny`) s'appliquent globalement dans tous les dépôts.
    - Les heuristiques `force_ask` ne s'appliquent que dans les workspaces initialisés Gravity (`is_gravity_workspace`).
    """
    hooks_dir = _get_hooks_dir()
    if workspace_root is not None:
        ws_root = workspace_root
    elif isinstance(data, dict) and (data.get("cwd") or data.get("workspacePaths")):
        ws_root = resolve_workspace_root(data, str(hooks_dir / "safety_policy.py"))
    else:
        ws_root = hooks_dir.parent

    tool = data.get("toolCall")
    if not isinstance(tool, dict):
        return "force_ask", "Champ 'toolCall' absent ou invalide (fail-closed)."

    name = str(tool.get("name", ""))
    args = tool.get("args") or {}
    if not isinstance(args, dict):
        return "force_ask", f"Arguments invalides pour l'outil '{name}' (fail-closed)."

    gravity_ws = is_gravity_workspace(ws_root)

    if name in FILE_WRITE_TOOLS:
        for target in _collect_write_targets(args):
            if is_harness_protected(target, ws_root):
                return "deny", f"Auto-protection du harnais : écriture interdite sur {target}"
            if matches_any(target, SENSITIVE_PATH_PATTERNS):
                return "deny", f"Écriture interdite sur fichier sensible : {target}"

    elif name in FILE_READ_TOOLS or name in DIR_READ_TOOLS:
        candidate_paths = [
            str(args.get(k) or "")
            for k in READ_PATH_KEYS
            if k in args and isinstance(args[k], str)
        ]
        for inc_key in ("Includes", "includes", "glob", "globs"):
            val = args.get(inc_key)
            if isinstance(val, list):
                candidate_paths.extend(str(item) for item in val)
            elif isinstance(val, str):
                candidate_paths.append(val)

        for target in candidate_paths:
            if target and matches_any(target, SENSITIVE_PATH_PATTERNS):
                return "deny", f"Lecture/recherche interdite sur chemin sensible : {target}"

    elif name == "call_mcp_tool":
        for val in _iter_string_leaves(args):
            if is_harness_protected(val, ws_root):
                return "deny", f"Auto-protection du harnais via MCP bloquée : {val}"
            if matches_any(val, SENSITIVE_PATH_PATTERNS):
                return "deny", f"Accès MCP interdit sur chemin sensible : {val}"

    elif name in WORKFLOW_TOOLS:
        pending_ask: Optional[str] = None
        script_path = first_str_arg(args, ("ScriptPath", "script_path"))
        if script_path:
            if matches_any(script_path, SENSITIVE_PATH_PATTERNS):
                return "deny", f"Chemin `run_workflow` sensible interdit : {script_path}"
            if is_harness_protected(script_path, ws_root):
                return "deny", f"Auto-protection : chemin `run_workflow` interdit : {script_path}"
            cwd_arg = first_str_arg(args, ("Cwd", "cwd"))
            cwd_root = None
            if cwd_arg:
                raw_cwd = Path(cwd_arg)
                cwd_root = raw_cwd if raw_cwd.is_absolute() else (ws_root / raw_cwd)
            candidate_file = Path(script_path)
            if not candidate_file.is_absolute():
                search_bases: List[Path] = []
                if cwd_root is not None:
                    search_bases.append(cwd_root)
                search_bases.extend([ws_root, hooks_dir.parent])
                for base in search_bases:
                    probe = base / candidate_file
                    if probe.is_file():
                        candidate_file = probe
                        break
            if candidate_file.is_file():
                verdict = inspect_workflow_code(
                    candidate_file.read_text(encoding="utf-8", errors="replace"),
                    ws_root=ws_root,
                )
                if verdict:
                    if verdict[0] == "deny":
                        return verdict
                    pending_ask = verdict[1]
            else:
                pending_ask = (
                    f"Script `run_workflow` introuvable pour inspection statique ({script_path}) : "
                    "validation humaine requise."
                )

        inline_script = first_str_arg(args, ("Script", "script"))
        if inline_script:
            verdict = inspect_workflow_code(inline_script, ws_root=ws_root)
            if verdict:
                if verdict[0] == "deny":
                    return verdict
                if pending_ask is None:
                    pending_ask = verdict[1]

        if pending_ask and gravity_ws:
            return "force_ask", pending_ask

    elif name in SHELL_TOOLS:
        cmd = first_str_arg(args, SHELL_CMD_KEYS)
        deny_reason, ask_reason = _evaluate_shell_command(cmd, ws_root, _depth=0)
        if deny_reason:
            return "deny", deny_reason
        if ask_reason and gravity_ws:
            return "force_ask", ask_reason

    return "allow", None
