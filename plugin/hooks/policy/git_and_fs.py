#!/usr/bin/env python3
"""Règles Git (force-push, config `-c`, `ext::`) et inspections `rm`, secrets, DB et harnais."""

import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .constants import (
    DB_CLIENT_HEADS,
    DB_DESTRUCTIVE_RE,
    ENV_DUMP_PATTERN,
    GIT_EXEC_CONFIG_KEYS,
    GIT_GLOBAL_VALUE_OPTS,
    GIT_LITERAL_SUBCMDS,
    HARNESS_ROOT_DESTRUCTIVE_HEADS,
    HARNESS_WRITE_OR_EXEC_HEADS,
    MAX_SUBSHELL_DEPTH,
    PATH_TOKEN_RE,
    PS_SEARCH_CMD_HEADS,
    PY_DB_EXEC_RE,
    SEARCH_CMD_HEADS,
    TEXT_OUTPUT_HEADS,
)
from .paths import (
    _is_active_plugin_root_path,
    _is_destructive_target,
    _is_secret_matching_glob,
    _token_hits_secret,
    is_harness_protected,
)
from .shell_parser import (
    _argv_to_shell_cmd,
    _extract_redirection_targets,
    _has_unquoted_redirection,
    _is_python_head,
    _normalize_cmd_name,
    _tokenize_shell_segment,
    extract_command_head_and_args,
)


def _parse_git_invocation(args: List[str]) -> Tuple[List[Tuple[str, str]], str, List[str]]:
    """Extrait `(flags_globaux, sous_commande, args_restants)` d'un appel `git`."""
    global_items: List[Tuple[str, str]] = []
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]
        if tok == "--":
            if i + 1 < n:
                return global_items, args[i + 1].lower(), args[i + 2 :]
            return global_items, "", []
        if tok in GIT_GLOBAL_VALUE_OPTS:
            if i + 1 < n:
                global_items.append((tok, args[i + 1]))
            i += 2
            continue
        if len(tok) > 2 and (tok.startswith("-C") or tok.startswith("-c")):
            global_items.append((tok[:2], tok[2:]))
            i += 1
            continue
        if tok.startswith("--") and "=" in tok:
            opt_name, opt_val = tok.split("=", 1)
            if opt_name in GIT_GLOBAL_VALUE_OPTS:
                global_items.append((opt_name, opt_val))
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return global_items, tok.lower(), args[i + 1 :]
    return global_items, "", []


def _parse_git_subcommand(args: List[str]) -> Tuple[str, List[str]]:
    """Extrait `(sous_commande, args_restants)` d'un appel `git` en sautant les flags globaux."""
    _, subcmd, sub_args = _parse_git_invocation(args)
    return subcmd, sub_args


def _check_git_config_overrides(
    args: List[str], ws_root: Optional[Path] = None, _depth: int = 0
) -> Tuple[Optional[str], Optional[str]]:
    """Inspecte les options `git -c key=value` et URLs `ext::` pouvant exécuter des commandes malveillantes."""
    from .evaluator import _evaluate_shell_command

    global_items, _, sub_args = _parse_git_invocation(args)
    for opt, val in global_items:
        if opt != "-c" or "=" not in val:
            continue
        cfg_key, cfg_val = val.split("=", 1)
        low_key = cfg_key.strip().lower()
        stripped_val = cfg_val.strip()
        if not stripped_val:
            continue
        if low_key == "core.hookspath":
            if is_harness_protected(stripped_val, ws_root):
                return "Redirection `git -c core.hooksPath` vers les garde-fous Gravity bloquée.", None
            continue
        if _depth >= MAX_SUBSHELL_DEPTH:
            continue
        if low_key.startswith("alias."):
            inner = (
                stripped_val[1:].strip()
                if stripped_val.startswith("!")
                else f"git {stripped_val}"
            )
            if inner:
                deny_reason, _ = _evaluate_shell_command(inner, ws_root, _depth=_depth + 1)
                if deny_reason:
                    return deny_reason, None
            continue
        is_exec_key = (
            low_key in GIT_EXEC_CONFIG_KEYS
            or (low_key.startswith("filter.") and low_key.endswith((".clean", ".smudge", ".process")))
            or (low_key.startswith("diff.") and low_key.endswith(".textconv"))
            or (low_key.startswith("merge.") and low_key.endswith(".driver"))
            or (low_key.startswith("credential.") and low_key.endswith(".helper"))
            or (low_key.startswith(("difftool.", "mergetool.", "browser.")) and low_key.endswith(".cmd"))
            or (low_key.startswith("remote.") and low_key.endswith((".uploadpack", ".receivepack")))
            or (low_key.startswith("submodule.") and low_key.endswith(".update") and stripped_val.startswith("!"))
        )
        if is_exec_key:
            inner = stripped_val[1:].strip() if stripped_val.startswith("!") else stripped_val
            if inner:
                deny_reason, _ = _evaluate_shell_command(inner, ws_root, _depth=_depth + 1)
                if deny_reason:
                    return deny_reason, None

    if _depth < MAX_SUBSHELL_DEPTH:
        for arg in sub_args:
            if arg.lower().startswith("ext::"):
                ext_raw = arg[5:].strip()
                if not ext_raw:
                    continue
                ext_cands: List[str] = [ext_raw]
                if "%" in ext_raw:
                    ext_argv: List[str] = []
                    cur_tok: List[str] = []
                    j = 0
                    while j < len(ext_raw):
                        c = ext_raw[j]
                        if c == "%" and j + 1 < len(ext_raw):
                            nxt = ext_raw[j + 1]
                            if nxt == " ":
                                cur_tok.append(" ")
                                j += 2
                                continue
                            if nxt == "%":
                                cur_tok.append("%")
                                j += 2
                                continue
                        if c.isspace():
                            if cur_tok:
                                ext_argv.append("".join(cur_tok))
                                cur_tok.clear()
                        else:
                            cur_tok.append(c)
                        j += 1
                    if cur_tok:
                        ext_argv.append("".join(cur_tok))
                    if ext_argv:
                        ext_cands.append(_argv_to_shell_cmd(ext_argv))
                for ext_cmd in ext_cands:
                    deny_reason, _ = _evaluate_shell_command(ext_cmd, ws_root, _depth=_depth + 1)
                    if deny_reason:
                        return deny_reason, None
    return None, None


def _is_git_force_push(segment: str) -> bool:
    """Détecte `git ... push` avec `-f`/`--force`/`+ref` strictement dans `segment`."""
    head, args = extract_command_head_and_args(segment)
    if head != "git":
        return False
    subcmd, sub_args = _parse_git_subcommand(args)
    if subcmd != "push":
        return False

    after_double_dash = False
    for arg in sub_args:
        if not after_double_dash:
            if arg == "--":
                after_double_dash = True
                continue
            low = arg.lower()
            if low in ("-f", "--force"):
                return True
            if re.match(r"^-[A-Za-z]+$", arg) and "f" in arg[1:]:
                return True
        if arg.startswith("+") and len(arg) > 1:
            return True
    return False


def _is_search_or_git_literal_segment(head: str, args: List[str]) -> bool:
    """Retourne True pour les commandes de recherche/historique/message Git (littéraux non exécutés)."""
    if head in SEARCH_CMD_HEADS or head in PS_SEARCH_CMD_HEADS:
        return True
    if head == "git":
        subcmd, _ = _parse_git_subcommand(args)
        return subcmd in GIT_LITERAL_SUBCMDS
    return False


def _extract_grep_inspectable_tokens(
    args: List[str], value_non_path_opts: Set[str]
) -> List[str]:
    """Extrait les arguments chemins/fichiers d'un appel `grep`/`rg`/`git grep` en sautant les motifs."""
    inspectable: List[str] = []
    i = 0
    after_dd = False
    has_pattern = False
    while i < len(args):
        tok = args[i]
        if not after_dd:
            if tok == "--":
                after_dd = True
                i += 1
                continue
            if tok in ("-e", "--regexp"):
                has_pattern = True
                i += 2
                continue
            if tok.startswith("--regexp="):
                has_pattern = True
                i += 1
                continue
            if tok in ("-f", "--file"):
                has_pattern = True
                if i + 1 < len(args):
                    inspectable.append(args[i + 1])
                i += 2
                continue
            if tok.startswith("--file="):
                has_pattern = True
                inspectable.append(tok.split("=", 1)[1])
                i += 1
                continue
            if tok.startswith("-f") and len(tok) > 2:
                has_pattern = True
                inspectable.append(tok[2:])
                i += 1
                continue
            if re.match(r"^-[A-Za-z]+$", tok):
                flags = tok[1:]
                if "f" in flags:
                    has_pattern = True
                    f_pos = flags.index("f")
                    if f_pos == len(flags) - 1:
                        if i + 1 < len(args):
                            inspectable.append(args[i + 1])
                        i += 2
                    else:
                        inspectable.append(flags[f_pos + 1 :])
                        i += 1
                    continue
                if "e" in flags:
                    has_pattern = True
                    e_pos = flags.index("e")
                    i += 2 if e_pos == len(flags) - 1 else 1
                    continue
            if tok in value_non_path_opts:
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
        if not has_pattern:
            has_pattern = True
            i += 1
            continue
        inspectable.append(tok)
        i += 1
    return inspectable


def _extract_secret_inspectable_tokens(
    segment: str,
    head: str,
    rich_args: List[Tuple[str, List[Tuple[str, str]], bool]],
) -> List[str]:
    """Retourne les jetons représentant potentiellement des chemins (hors motifs de recherche/messages Git)."""
    from .inline_scripts import _extract_inline_spec

    args = [tok for tok, _, _ in rich_args]
    inspectable: List[str] = list(_extract_redirection_targets(segment))

    if head == "git":
        global_items, subcmd, sub_args = _parse_git_invocation(args)
        for opt, val in global_items:
            if opt == "-c" and "=" in val:
                inspectable.append(val.split("=", 1)[1])
            elif val:
                inspectable.append(val)
        if subcmd in ("commit", "tag", "notes", "merge", "stash", "revert", "cherry-pick"):
            i = 0
            after_dd = False
            while i < len(sub_args):
                tok = sub_args[i]
                if not after_dd:
                    if tok == "--":
                        after_dd = True
                        i += 1
                        continue
                    if tok in ("-m", "--message", "--author", "--date"):
                        i += 2
                        continue
                    if tok.startswith(("-m", "--message=", "--author=", "--date=")):
                        i += 1
                        continue
                    if tok in ("-F", "--file"):
                        if i + 1 < len(sub_args):
                            inspectable.append(sub_args[i + 1])
                        i += 2
                        continue
                    if tok.startswith("--file="):
                        inspectable.append(tok.split("=", 1)[1])
                        i += 1
                        continue
                    if tok.startswith("-F") and len(tok) > 2:
                        inspectable.append(tok[2:])
                        i += 1
                        continue
                    if re.match(r"^-[A-Za-z]+$", tok):
                        flags = tok[1:]
                        if "F" in flags:
                            f_pos = flags.index("F")
                            if f_pos == len(flags) - 1:
                                if i + 1 < len(sub_args):
                                    inspectable.append(sub_args[i + 1])
                                i += 2
                            else:
                                inspectable.append(flags[f_pos + 1 :])
                                i += 1
                            continue
                        if "m" in flags:
                            m_pos = flags.index("m")
                            i += 2 if m_pos == len(flags) - 1 else 1
                            continue
                    if tok.startswith("-"):
                        i += 1
                        continue
                inspectable.append(tok)
                i += 1
            return inspectable

        if subcmd in ("log", "shortlog", "rev-list", "diff", "show"):
            i = 0
            after_dd = False
            while i < len(sub_args):
                tok = sub_args[i]
                if not after_dd:
                    if tok == "--":
                        after_dd = True
                        i += 1
                        continue
                    if tok in ("-S", "-G", "--grep", "--author", "--committer", "--format", "--pretty", "-n", "--max-count"):
                        i += 2
                        continue
                    if tok.startswith(("-S", "-G", "--grep=", "--author=", "--committer=", "--format=", "--pretty=")):
                        i += 1
                        continue
                    if re.match(r"^-[A-Za-z]+$", tok) and (tok.endswith("S") or tok.endswith("G")):
                        i += 2
                        continue
                    if tok.startswith("-"):
                        i += 1
                        continue
                inspectable.append(tok)
                i += 1
            return inspectable

        if subcmd == "grep":
            inspectable.extend(
                _extract_grep_inspectable_tokens(
                    sub_args, {"-A", "-B", "-C", "--max-depth", "--threads"}
                )
            )
            return inspectable

    if head in SEARCH_CMD_HEADS:
        inspectable.extend(
            _extract_grep_inspectable_tokens(
                args,
                {
                    "-m",
                    "-A",
                    "-B",
                    "-C",
                    "-j",
                    "-t",
                    "-T",
                    "--context",
                    "--after-context",
                    "--before-context",
                    "--max-count",
                    "--color",
                    "--encoding",
                    "--threads",
                    "--type",
                    "--type-not",
                },
            )
        )
        return inspectable

    if head in PS_SEARCH_CMD_HEADS:
        i = 0
        has_pattern = False
        while i < len(args):
            tok = args[i]
            low = tok.lower()
            if low in ("-pattern", "-p"):
                has_pattern = True
                i += 2
                continue
            if low.startswith(("-pattern:", "-p:")):
                has_pattern = True
                i += 1
                continue
            if low in ("-path", "-literalpath"):
                if i + 1 < len(args):
                    inspectable.append(args[i + 1])
                i += 2
                continue
            if low.startswith(("-path:", "-literalpath:")):
                inspectable.append(tok.split(":", 1)[1])
                i += 1
                continue
            if low in ("-context", "-encoding", "-culture"):
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            if not has_pattern:
                has_pattern = True
                i += 1
                continue
            inspectable.append(tok)
            i += 1
        return inspectable

    inline_spec = _extract_inline_spec(head, rich_args)
    if inline_spec is not None:
        _, payload_indices, _ = inline_spec
        for idx, tok in enumerate(args):
            if idx not in payload_indices:
                inspectable.append(tok)
        return inspectable

    inspectable.extend(args)
    for _, chunks, _ in rich_args:
        for kind, raw in chunks:
            if kind == "unquoted":
                for raw_tok in PATH_TOKEN_RE.findall(raw):
                    inspectable.append(raw_tok)
    return inspectable


def _segment_targets_secret(
    segment: str,
    head: str,
    rich_args: List[Tuple[str, List[Tuple[str, str]], bool]],
) -> bool:
    """Détecte un accès shell à un fichier sensible (`.env`, `secrets/`, clés SSH, `.pem`/`.key`)."""
    for tok in _extract_secret_inspectable_tokens(segment, head, rich_args):
        if _token_hits_secret(tok):
            return True
    return False


def _parse_posix_rm(args: List[str]) -> Tuple[bool, bool, List[str]]:
    """Extrait `(has_recursive, has_force, targets)` d'un appel `rm` POSIX."""
    has_r, has_f = False, False
    targets: List[str] = []
    after_double_dash = False
    for tok in args:
        if not after_double_dash:
            if tok == "--":
                after_double_dash = True
                continue
            if tok.startswith("--"):
                if tok == "--recursive":
                    has_r = True
                elif tok == "--force":
                    has_f = True
                continue
            if tok.startswith("-") and len(tok) > 1:
                flags = tok[1:].lower()
                if "r" in flags:
                    has_r = True
                if "f" in flags:
                    has_f = True
                continue
        targets.append(tok)
    return has_r, has_f, targets


def _is_destructive_rm_segment(head: str, args: List[str]) -> bool:
    """Détecte `rm -rf` ou `Remove-Item`/`del`/`rd -Recurse -Force` sur cible destructrice."""
    if head == "rm":
        has_r, has_f, targets = _parse_posix_rm(args)
        return has_r and has_f and any(_is_destructive_target(t) for t in targets)

    if head in ("remove-item", "ri", "del", "erase", "rmdir", "rd"):
        low = [t.lower() for t in args]
        has_r = any(t in ("-recurse", "-r", "/s") for t in low)
        has_f = any(t in ("-force", "-fo", "/q", "/f") for t in low)
        targets: List[str] = []
        for t in args:
            lt = t.lower()
            if lt.startswith(("-path:", "-literalpath:")):
                targets.append(t.split(":", 1)[1])
            elif not t.startswith("-") and not (len(t) == 2 and t.startswith("/") and t[1].isalpha()):
                targets.append(t)
        return has_r and has_f and any(_is_destructive_target(t) for t in targets)

    return False


def _is_rm_rf_without_targets(head: str, args: List[str]) -> bool:
    """Retourne True pour `rm -rf` sans cible explicite ou avec placeholder `{}` (ex. après `xargs`)."""
    if head != "rm":
        return False
    has_r, has_f, targets = _parse_posix_rm(args)
    return has_r and has_f and (not targets or any(t == "{}" for t in targets))


def _segment_targets_harness(
    seg: str,
    head: str,
    rich_args: List[Tuple[str, List[Tuple[str, str]], bool]],
    ws_root: Optional[Path] = None,
) -> bool:
    """Détecte une écriture, exécution ou redirection visant un fichier protégé du harnais actif."""
    from .inline_scripts import _extract_inline_spec

    args = [tok for tok, _, _ in rich_args]
    is_sed_inplace = False
    if head == "sed":
        for arg in args:
            if arg == "--":
                break
            if (
                arg == "-i"
                or arg.startswith("-i")
                or arg == "--in-place"
                or arg.startswith("--in-place=")
                or (re.match(r"^-[A-Za-z]+$", arg) and "i" in arg[1:])
            ):
                is_sed_inplace = True
                break

    writes_or_execs = (
        head in HARNESS_WRITE_OR_EXEC_HEADS
        or _is_python_head(head)
        or is_sed_inplace
        or _has_unquoted_redirection(seg)
    )
    if not writes_or_execs:
        return False

    inline_spec = _extract_inline_spec(head, rich_args)
    raw_candidates: List[str] = list(_extract_redirection_targets(seg))
    if inline_spec is not None:
        _, payload_indices, _ = inline_spec
        for idx, tok in enumerate(args):
            if idx not in payload_indices:
                raw_candidates.append(tok)
    else:
        raw_candidates.extend(_tokenize_shell_segment(seg))
        raw_candidates.extend(PATH_TOKEN_RE.findall(seg))

    candidates: List[str] = []
    seen: Set[str] = set()
    for raw_tok in raw_candidates:
        for part in (raw_tok, raw_tok.strip(",:[]{}()<>")):
            if part and part not in seen:
                seen.add(part)
                candidates.append(part)
            if "=" in part:
                rhs = part.split("=", 1)[1]
                for sub in (rhs, rhs.strip(",:[]{}()<>")):
                    if sub and sub not in seen:
                        seen.add(sub)
                        candidates.append(sub)
            if "," in part:
                for piece in part.split(","):
                    cleaned_piece = piece.strip(",:[]{}()<>\"'")
                    if cleaned_piece and cleaned_piece not in seen:
                        seen.add(cleaned_piece)
                        candidates.append(cleaned_piece)

    check_active_root = head in HARNESS_ROOT_DESTRUCTIVE_HEADS
    return any(
        is_harness_protected(token, ws_root)
        or (check_active_root and _is_active_plugin_root_path(token, ws_root))
        for token in candidates
    )


def _is_env_dump_segment(segment: str, head: str, args: List[str]) -> bool:
    """Détecte un dump des variables d'environnement dans un segment hors commandes de recherche/Git."""
    if _is_search_or_git_literal_segment(head, args):
        return False
    if head in ("printenv", "get-variable"):
        return True
    if head == "env":
        return True
    if head == "export" and any(a == "-p" for a in args):
        return True
    if head == "declare" and any(a == "-x" or (a.startswith("-") and "x" in a[1:]) for a in args):
        return True
    return bool(ENV_DUMP_PATTERN.search(segment))


def _is_db_destructive_segment(
    segment: str, head: str, args: List[str], has_db_client_in_chain: bool
) -> bool:
    """Détecte une commande SQL/Redis destructive sans faux positif sur recherche/commit/echo/test Python."""
    if not DB_DESTRUCTIVE_RE.search(segment):
        return False
    if has_db_client_in_chain or head in DB_CLIENT_HEADS:
        return True
    if any(_normalize_cmd_name(tok) in DB_CLIENT_HEADS for tok in args):
        return True
    if _is_search_or_git_literal_segment(head, args):
        return False
    if head in TEXT_OUTPUT_HEADS:
        return False
    if _is_python_head(head) and not PY_DB_EXEC_RE.search(segment):
        return False
    return True


def _has_unquoted_secret_glob(
    head: str, rich_args: List[Tuple[str, List[Tuple[str, str]], bool]]
) -> bool:
    """Détecte un glob non quoté ciblant un fichier sensible (ex. `cat .e*`, jamais `grep \".*\"` ni `ls .git*`)."""
    from .inline_scripts import _extract_inline_spec

    args = [tok for tok, _, _ in rich_args]
    if _is_search_or_git_literal_segment(head, args):
        return False
    inline_spec = _extract_inline_spec(head, rich_args)
    payload_indices = inline_spec[1] if inline_spec is not None else set()
    for idx, (tok, chunks, _) in enumerate(rich_args):
        if idx in payload_indices:
            continue
        has_unquoted_wild = any(
            kind == "unquoted" and any(ch in raw for ch in "*?[")
            for kind, raw in chunks
        )
        if has_unquoted_wild and _is_secret_matching_glob(tok):
            return True
    return False
