#!/usr/bin/env python3
"""Politique de sécurité pure pour PreToolUse (100 % stdlib, sans I/O sur stdout)."""

import ast
import base64
import fnmatch
import os
import posixpath
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from hook_runtime import first_str_arg, is_gravity_workspace, resolve_workspace_root  # noqa: E402

SHELL_TOOLS = {"run_command", "execute_command", "bash", "shell"}
FILE_WRITE_TOOLS = {
    "write_to_file",
    "write_file",
    "replace_file_content",
    "edit_file",
    "multi_replace_file_content",
}
FILE_READ_TOOLS = {"view_file", "read_file", "grep_search", "search_files"}
DIR_READ_TOOLS = {"list_dir", "find_by_name"}
WORKFLOW_TOOLS = {"run_workflow"}

WRITE_PATH_KEYS = (
    "TargetFile",
    "target_file",
    "AbsolutePath",
    "path",
    "file_path",
    "filename",
    "Path",
)
READ_PATH_KEYS = (
    "AbsolutePath",
    "path",
    "file_path",
    "filename",
    "TargetFile",
    "target_file",
    "SearchPath",
    "search_path",
    "Directory",
    "directory",
    "DirectoryPath",
    "SearchDirectory",
    "Pattern",
    "Path",
)
SHELL_CMD_KEYS = ("CommandLine", "command", "cmd", "script")

DESTRUCTIVE_RM_TARGETS = {
    "/",
    "/*",
    "~",
    "~/",
    "~/*",
    "$HOME",
    "$HOME/*",
    "*",
    ".",
    "./",
    "..",
    "../",
    "C:/",
    "C:/*",
}

SENSITIVE_PATH_PATTERNS = [
    re.compile(r"(?:^|/)\.env(?:\.(?!example$|sample$|template$)[^/]+)?$", re.IGNORECASE),
    re.compile(r"(?:^|/)secrets(?:/|$)", re.IGNORECASE),
    re.compile(r"\.(?:pem|key|p12|pfx)$", re.IGNORECASE),
    re.compile(r"(?:^|/)(?:id_rsa|id_ed25519|id_ecdsa|id_dsa)(?:\.pub)?$", re.IGNORECASE),
    re.compile(r"(?:^|/)\.ssh(?:/(?:id_[^/]+|authorized_keys|config))?$", re.IGNORECASE),
]

# Auto-protection ciblée sur le plugin actif/installé et le socle projet (jamais le dépôt source gravity-workflow-plugin/).
HARNESS_SELF_PROTECT_PATTERNS = [
    re.compile(
        r"(?:^hooks\.json$|(?:^|/)(?:\.agents|\.agents/plugins/gravity-workflow|plugins/gravity-workflow)/hooks\.json$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|/)(?:\.agents/gravity-workflow|\.agents/plugins/gravity-workflow|plugins/gravity-workflow)/hooks(?:/|$)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|/)(?:\.agents/rules/)?01-safety(?:-boundaries)?\.md$", re.IGNORECASE),
]

SECRET_GLOB_PROBES = (
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "cert.pem",
    "private.key",
    "cert.p12",
    "cert.pfx",
)
BENIGN_GLOB_PROBES = (
    "readme.txt",
    "main.py",
    "app.json",
    "notes.md",
    "index.ts",
    "makefile",
)

PATH_TOKEN_RE = re.compile(r"""[^\s"'`;&|()<>]+""")
QUOTED_LITERAL_RE = re.compile(r"""'([^'\r\n]*)'|"([^"\r\n]*)"|`([^`\r\n]*)`""")
MAX_SUBSHELL_DEPTH = 4

ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PYTHON_VERSIONED_HEAD_RE = re.compile(r"^python[23]\.\d+$")
PY_VERSION_OPT_RE = re.compile(r"^(?:-[23](?:\.\d+)?(?:-(?:32|64|arm64))?|-V:.+)$", re.IGNORECASE)

PYTHON_VALUE_OPTS = {"-W", "-X", "--check-hash-based-pycs"}
NODE_VALUE_OPTS = {
    "-r",
    "--require",
    "--import",
    "--loader",
    "--experimental-loader",
    "--conditions",
    "-C",
    "--env-file",
    "--env-file-if-exists",
    "--max-old-space-size",
    "--inspect",
    "--inspect-brk",
    "--title",
    "--redirect-warnings",
    "--cpu-prof-dir",
    "--heap-prof-dir",
}
RUBY_VALUE_OPTS = {
    "-I",
    "-r",
    "-C",
    "-E",
    "-F",
    "-K",
    "-T",
    "--encoding",
    "--external-encoding",
    "--internal-encoding",
}
PS_VALUE_OPTS = {
    "-executionpolicy",
    "-ep",
    "-inputformat",
    "-if",
    "-outputformat",
    "-of",
    "-windowstyle",
    "-w",
    "-configurationname",
    "-workingdirectory",
    "-wd",
    "-settingsfile",
    "-custompipename",
}
SUDO_VALUE_OPTS = {
    "-u",
    "-g",
    "-C",
    "-D",
    "-h",
    "-p",
    "-r",
    "-t",
    "-T",
    "-U",
    "--user",
    "--group",
    "--chdir",
    "--prompt",
    "--role",
    "--type",
    "--other-user",
}
ENV_VALUE_OPTS = {"-u", "-C", "--unset", "--chdir"}
GIT_GLOBAL_VALUE_OPTS = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
    "--exec-path",
}
XARGS_VALUE_OPTS = {
    "-I",
    "-i",
    "-L",
    "-l",
    "-n",
    "-P",
    "-s",
    "-d",
    "-E",
    "-e",
    "-a",
    "--arg-file",
    "--delimiter",
    "--eof",
    "--replace",
    "--max-lines",
    "--max-args",
    "--max-procs",
    "--max-chars",
    "--process-slot-var",
}

SEARCH_CMD_HEADS = {
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "ack",
    "findstr",
}
PS_SEARCH_CMD_HEADS = {"select-string", "sls"}
GIT_LITERAL_SUBCMDS = {
    "commit",
    "log",
    "grep",
    "tag",
    "notes",
    "shortlog",
    "rev-list",
}
GIT_EXEC_CONFIG_KEYS = {
    "core.sshcommand",
    "core.fsmonitor",
    "core.pager",
    "core.editor",
    "core.askpass",
    "core.alternaterefscommand",
    "credential.helper",
    "diff.external",
    "sequence.editor",
    "gpg.program",
    "gpg.ssh.program",
    "gpg.x509.program",
    "gpg.openpgp.program",
    "sendemail.sendmailcmd",
    "ssh.command",
    "uploadpack.packobjectshook",
}
PS_PASSTHROUGH_HEADS = {"out-string", "out-default"}
HEREDOC_PIPE_PRODUCER_HEADS = {"cat", "tee", "sed", "tr", "head", "tail"}
POSIX_HEREDOC_OP_RE = re.compile(r"(?<!<)<<(-?)\s*(?:'([^'\r\n]+)'|\"([^\"\r\n]+)\"|\\?([A-Za-z_][A-Za-z0-9_]*))")
DB_CLIENT_HEADS = {
    "psql",
    "mysql",
    "mariadb",
    "sqlite3",
    "sqlcmd",
    "redis-cli",
    "valkey-cli",
    "mongosh",
    "mongo",
    "clickhouse-client",
    "cqlsh",
    "isql",
    "osql",
    "sqlplus",
    "duckdb",
}
TEXT_OUTPUT_HEADS = {"echo", "printf", "write-output", "write-host"}
PY_DB_EXEC_RE = re.compile(
    r"\.(?:execute|executescript|executemany|flushall|flushdb)\s*\(",
    re.IGNORECASE,
)
DOWNLOAD_CMD_HEADS = {"curl", "wget"}
PS_DOWNLOAD_CMD_HEADS = {"iwr", "irm", "invoke-webrequest", "invoke-restmethod"}
PIPE_SHELL_HEADS = {"sh", "bash", "zsh", "dash", "pwsh", "powershell"}
PIPE_EVAL_HEADS = PIPE_SHELL_HEADS | {"iex", "invoke-expression", "eval", "node", "perl", "ruby"}

HARNESS_ROOT_DESTRUCTIVE_HEADS: Set[str] = {
    "rm",
    "mv",
    "rmdir",
    "rd",
    "del",
    "erase",
    "remove-item",
    "ri",
    "move-item",
    "mi",
    "rename-item",
    "rni",
}

HARNESS_WRITE_OR_EXEC_HEADS: Set[str] = {
    "rm",
    "mv",
    "cp",
    "chmod",
    "chown",
    "truncate",
    "tee",
    "python",
    "python3",
    "py",
    "perl",
    "ruby",
    "node",
    "remove-item",
    "ri",
    "del",
    "rd",
    "rmdir",
    "erase",
    "set-content",
    "out-file",
    "add-content",
    "ac",
    "sc",
    "clear-content",
    "clc",
    "move-item",
    "mi",
    "copy-item",
    "cpi",
    "copy",
    "rename-item",
    "rni",
    "new-item",
    "ni",
}

DB_DESTRUCTIVE_RE = re.compile(
    r"\b(?:drop\s+(?:database|schema|table)|truncate\s+table|flushall|flushdb)\b",
    re.IGNORECASE,
)
ENV_DUMP_PATTERN = re.compile(
    r"(?:^|[;&|{(]\s*)(?:printenv\b|env\s*(?:$|[;&|>})])|export\s+-p\b|declare\s+-x\b|"
    r"(?:Get-ChildItem|gci|ls|dir|Get-Item|gi|Get-Content|gc|cat)\s+(?:-(?:Path|LiteralPath)\s+)?Env:|Get-Variable\b)|"
    r"\[(?:System\.)?Environment\]::GetEnvironmentVariables\b",
    re.IGNORECASE,
)
PS_DOWNLOAD_IN_EXPR_RE = re.compile(
    r"\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b",
    re.IGNORECASE,
)
FROM_BASE64_RE = re.compile(r"\bFromBase64String\b", re.IGNORECASE)

SUBPROCESS_CALL_FUNCS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
    "getoutput",
    "getstatusoutput",
}
OS_SHELL_FUNCS = {"system", "popen"}
OS_EXEC_LIST_FUNCS = {"execv", "execvp", "execve", "execvpe"}
OS_EXEC_VAR_FUNCS = {"execl", "execlp", "execle", "execlpe"}
OS_SPAWN_LIST_FUNCS = {"spawnv", "spawnvp", "spawnve", "spawnvpe"}
OS_SPAWN_VAR_FUNCS = {"spawnl", "spawnlp", "spawnle", "spawnlpe"}
RE_PATTERN_FUNCS = {"compile", "search", "match", "fullmatch", "findall", "finditer", "sub", "subn", "split"}


def normalize_path(path: str) -> str:
    cleaned = path.replace("\\", "/").strip().strip("\"'")
    if not cleaned:
        return ""
    norm = posixpath.normpath(cleaned)
    if norm == ".":
        return ""
    if norm.startswith("./"):
        norm = norm[2:]
    return norm


def matches_any(path: str, patterns: List[re.Pattern]) -> bool:
    norm = normalize_path(path)
    if not norm:
        return False
    return any(p.search(norm) for p in patterns)


def _is_active_plugin_protected_path(path: str, ws_root: Optional[Path] = None) -> bool:
    """Vérifie si `path` pointe réellement vers le plugin Gravity actif (`hooks.json` ou `hooks/`)."""
    cleaned = str(path or "").strip().strip("\"'")
    if not cleaned or any(ch in cleaned for ch in "*?<>|\r\n\x00"):
        return False
    try:
        candidate = Path(cleaned).expanduser()
        if not candidate.is_absolute():
            base = ws_root if ws_root is not None else HOOKS_DIR.parent
            candidate = base / candidate
        resolved = Path(os.path.normcase(str(candidate.resolve())))
        active_hooks_dir = Path(os.path.normcase(str(HOOKS_DIR.resolve())))
        active_hooks_json = Path(os.path.normcase(str((HOOKS_DIR.parent / "hooks.json").resolve())))
        return (
            resolved == active_hooks_json
            or resolved == active_hooks_dir
            or active_hooks_dir in resolved.parents
        )
    except (OSError, ValueError):
        return False


def _is_active_plugin_root_path(path: str, ws_root: Optional[Path] = None) -> bool:
    """Vérifie si `path` cible la racine entière du plugin Gravity actif (pour bloquer rm/mv/rmtree)."""
    cleaned = str(path or "").strip().strip("\"'")
    if not cleaned or any(ch in cleaned for ch in "*?<>|\r\n\x00"):
        return False
    try:
        candidate = Path(cleaned).expanduser()
        if not candidate.is_absolute():
            base = ws_root if ws_root is not None else HOOKS_DIR.parent
            candidate = base / candidate
        resolved = Path(os.path.normcase(str(candidate.resolve())))
        active_root = Path(os.path.normcase(str(HOOKS_DIR.parent.resolve())))
        return resolved == active_root
    except (OSError, ValueError):
        return False


def is_harness_protected(path: str, ws_root: Optional[Path] = None) -> bool:
    """Retourne True si `path` cible un fichier protégé du harnais Gravity actif ou du workspace."""
    return matches_any(path, HARNESS_SELF_PROTECT_PATTERNS) or _is_active_plugin_protected_path(
        path, ws_root
    )


def _is_destructive_target(tok: str) -> bool:
    cleaned = tok.replace("\\", "/").strip().strip("\"'")
    if not cleaned:
        return False
    upper = cleaned.upper()
    if upper in {t.upper() for t in DESTRUCTIVE_RM_TARGETS}:
        return True
    if re.match(r"^[A-Za-z]:(?:/(?:\*)?)?$", cleaned):
        return True
    if ".." in cleaned.split("/"):
        return True
    return False


def _is_secret_matching_glob(raw_tok: str) -> bool:
    """Retourne True si le glob `raw_tok` cible spécifiquement un nom de fichier sensible."""
    cleaned = raw_tok.replace("\\", "/").strip().strip("\"'")
    if not cleaned or not any(ch in cleaned for ch in "*?["):
        return False
    base = posixpath.basename(cleaned.rstrip("/")).lower()
    if not base or not any(ch in base for ch in "*?["):
        return False
    if any(fnmatch.fnmatchcase(probe, base) for probe in BENIGN_GLOB_PROBES):
        return False
    return any(fnmatch.fnmatchcase(probe, base) for probe in SECRET_GLOB_PROBES)


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


def _extract_redirections(segment: str) -> List[Tuple[str, str]]:
    """Extrait les redirections `(direction, cible)` hors guillemets (hors `>&1`/`>&2`/`>&-` et `<<`)."""
    redirs: List[Tuple[str, str]] = []
    in_single = False
    in_double = False
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch in ("\\", "`") and i + 1 < n and segment[i + 1] == '"':
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "@":
            hs = _match_ps_herestring(segment, i)
            if hs is not None:
                _, _, end_idx = hs
                i = end_idx
                continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
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


def _is_search_or_git_literal_segment(head: str, args: List[str]) -> bool:
    """Retourne True pour les commandes de recherche/historique/message Git (littéraux non exécutés)."""
    if head in SEARCH_CMD_HEADS or head in PS_SEARCH_CMD_HEADS:
        return True
    if head == "git":
        subcmd, _ = _parse_git_subcommand(args)
        return subcmd in GIT_LITERAL_SUBCMDS
    return False


def _token_hits_secret(tok: str) -> bool:
    """Vérifie si un jeton de chemin (ou sous-composant `=`/`:`/`,`/`@`) cible un fichier sensible."""
    cleaned = tok.strip().strip("\"'")
    if not cleaned:
        return False
    raw_cands: List[str] = [cleaned, cleaned.strip(",:[]{}()<>")]
    if "=" in cleaned:
        rhs = cleaned.split("=", 1)[1]
        raw_cands.extend([rhs, rhs.strip(",:[]{}()<>\"'")])
    if ":" in cleaned:
        lhs, rhs = cleaned.split(":", 1)
        if not (len(lhs) == 1 and lhs.isalpha()):
            raw_cands.extend([rhs, rhs.strip(",:[]{}()<>\"'")])
    if "," in cleaned:
        for piece in cleaned.split(","):
            raw_cands.append(piece.strip(",:[]{}()<>\"'"))
    candidates: List[str] = []
    for cand in raw_cands:
        if not cand:
            continue
        candidates.append(cand)
        if cand.startswith("@") and len(cand) > 1:
            candidates.append(cand[1:].strip("\"'"))
    for cand in candidates:
        if cand and matches_any(cand, SENSITIVE_PATH_PATTERNS):
            return True
    return False


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


def _argv_to_shell_cmd(argv: List[str]) -> str:
    """Reconstruit une ligne de commande shell sûre à partir d'une liste argv."""
    parts: List[str] = []
    for arg in argv:
        if not arg:
            parts.append('""')
        elif any(ch.isspace() or ch in "\"'`;|&()<>" for ch in arg):
            escaped = arg.replace('"', '\\"')
            parts.append(f'"{escaped}"')
        else:
            parts.append(arg)
    return " ".join(parts)


def _ast_subscript_slice(node: ast.Subscript) -> ast.AST:
    """Retourne l'expression d'index d'un `ast.Subscript` (compatible Python 3.8+)."""
    idx_cls = getattr(ast, "Index", None)
    if idx_cls is not None and isinstance(node.slice, idx_cls):
        return node.slice.value
    return node.slice


def _eval_static_int(node: Optional[ast.AST]) -> Optional[int]:
    """Évalue un entier littéral AST (positif ou négatif), sans booléens."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    return None


def _eval_static_seq(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Optional[Dict[str, List[Optional[str]]]] = None,
    _depth: int = 0,
    shlex_mod_names: Optional[Set[str]] = None,
    shlex_split_names: Optional[Set[str]] = None,
    func_returns: Optional[Dict[str, List[ast.AST]]] = None,
) -> Optional[List[Optional[str]]]:
    """Évalue statiquement une expression AST vers une liste de chaînes (variables, `+`, `*unpack`, slices, compréhensions, `reversed`, `shlex.split`, `.split`)."""
    if _depth > 8:
        return None
    if isinstance(node, ast.NamedExpr):
        return _eval_static_seq(
            node.value,
            const_vars,
            const_seq_vars,
            _depth + 1,
            shlex_mod_names,
            shlex_split_names,
            func_returns,
        )
    if isinstance(node, ast.Name):
        if const_seq_vars and node.id in const_seq_vars:
            return list(const_seq_vars[node.id])
        return None
    if isinstance(node, (ast.List, ast.Tuple)):
        items: List[Optional[str]] = []
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                unpacked = _eval_static_seq(
                    elt.value,
                    const_vars,
                    const_seq_vars,
                    _depth + 1,
                    shlex_mod_names,
                    shlex_split_names,
                    func_returns,
                )
                if unpacked is not None:
                    items.extend(unpacked)
                else:
                    items.append(None)
            else:
                items.append(_eval_static_str(elt, const_vars, const_seq_vars, _depth + 1, func_returns))
        return items
    if isinstance(node, ast.Subscript):
        slice_node = _ast_subscript_slice(node)
        if isinstance(slice_node, ast.Slice):
            seq = _eval_static_seq(
                node.value,
                const_vars,
                const_seq_vars,
                _depth + 1,
                shlex_mod_names,
                shlex_split_names,
                func_returns,
            )
            if seq is not None:
                lo = _eval_static_int(slice_node.lower) if slice_node.lower is not None else None
                hi = _eval_static_int(slice_node.upper) if slice_node.upper is not None else None
                st = _eval_static_int(slice_node.step) if slice_node.step is not None else None
                if (
                    (slice_node.lower is None or lo is not None)
                    and (slice_node.upper is None or hi is not None)
                    and (slice_node.step is None or (st is not None and st != 0))
                ):
                    return list(seq[slice(lo, hi, st)])
    if isinstance(node, ast.ListComp) and len(node.generators) == 1:
        gen = node.generators[0]
        if not gen.ifs and isinstance(gen.target, ast.Name):
            src_seq = _eval_static_seq(
                gen.iter,
                const_vars,
                const_seq_vars,
                _depth + 1,
                shlex_mod_names,
                shlex_split_names,
                func_returns,
            )
            if src_seq is not None:
                if isinstance(node.elt, ast.Name) and node.elt.id == gen.target.id:
                    return list(src_seq)
                comp_out: List[Optional[str]] = []
                for item in src_seq:
                    if item is None:
                        comp_out.append(None)
                    else:
                        local_str = dict(const_vars)
                        local_str[gen.target.id] = item
                        comp_out.append(
                            _eval_static_str(node.elt, local_str, const_seq_vars, _depth + 1, func_returns)
                        )
                return comp_out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _eval_static_seq(
            node.left,
            const_vars,
            const_seq_vars,
            _depth + 1,
            shlex_mod_names,
            shlex_split_names,
            func_returns,
        )
        right = _eval_static_seq(
            node.right,
            const_vars,
            const_seq_vars,
            _depth + 1,
            shlex_mod_names,
            shlex_split_names,
            func_returns,
        )
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in ("list", "tuple") and len(node.args) == 1 and not node.keywords:
            return _eval_static_seq(
                node.args[0],
                const_vars,
                const_seq_vars,
                _depth + 1,
                shlex_mod_names,
                shlex_split_names,
                func_returns,
            )
        if isinstance(fn, ast.Name) and fn.id == "reversed" and len(node.args) == 1 and not node.keywords:
            seq = _eval_static_seq(
                node.args[0],
                const_vars,
                const_seq_vars,
                _depth + 1,
                shlex_mod_names,
                shlex_split_names,
                func_returns,
            )
            if seq is not None:
                return list(reversed(seq))
        if (
            isinstance(fn, ast.Name)
            and func_returns
            and fn.id in func_returns
            and not node.args
            and not node.keywords
        ):
            for ret_expr in func_returns[fn.id]:
                seq = _eval_static_seq(
                    ret_expr,
                    const_vars,
                    const_seq_vars,
                    _depth + 1,
                    shlex_mod_names,
                    shlex_split_names,
                    func_returns,
                )
                if seq is not None:
                    return seq
        is_shlex_split = (
            isinstance(fn, ast.Name)
            and shlex_split_names is not None
            and fn.id in shlex_split_names
        ) or (
            isinstance(fn, ast.Attribute)
            and fn.attr == "split"
            and isinstance(fn.value, ast.Name)
            and fn.value.id in (shlex_mod_names or {"shlex"})
        )
        if is_shlex_split and len(node.args) >= 1:
            raw_s = _eval_static_str(node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns)
            if raw_s is not None:
                try:
                    return list(shlex.split(raw_s))
                except ValueError:
                    return raw_s.split()
        if isinstance(fn, ast.Attribute) and fn.attr == "split" and len(node.args) <= 1 and not node.keywords:
            recv = _eval_static_str(fn.value, const_vars, const_seq_vars, _depth + 1, func_returns)
            if recv is not None:
                if not node.args:
                    return list(recv.split())
                sep = _eval_static_str(node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns)
                if sep:
                    return list(recv.split(sep))
    return None


def _eval_static_str(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Optional[Dict[str, List[Optional[str]]]] = None,
    _depth: int = 0,
    func_returns: Optional[Dict[str, List[ast.AST]]] = None,
) -> Optional[str]:
    """Évalue statiquement une expression AST vers une chaîne (constantes, `+`, f-strings, slices, `.join`, `chr`, `Path`/`os.path.join`)."""
    if _depth > 8:
        return None
    if isinstance(node, ast.NamedExpr):
        return _eval_static_str(node.value, const_vars, const_seq_vars, _depth + 1, func_returns)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in const_vars:
        return const_vars[node.id]
    if isinstance(node, ast.Subscript):
        idx_node = _ast_subscript_slice(node)
        if isinstance(idx_node, ast.Slice):
            base_s = _eval_static_str(node.value, const_vars, const_seq_vars, _depth + 1, func_returns)
            if base_s is not None:
                lo = _eval_static_int(idx_node.lower) if idx_node.lower is not None else None
                hi = _eval_static_int(idx_node.upper) if idx_node.upper is not None else None
                st = _eval_static_int(idx_node.step) if idx_node.step is not None else None
                if (
                    (idx_node.lower is None or lo is not None)
                    and (idx_node.upper is None or hi is not None)
                    and (idx_node.step is None or (st is not None and st != 0))
                ):
                    return base_s[slice(lo, hi, st)]
        idx_val = _eval_static_int(idx_node)
        if idx_val is not None:
            seq = _eval_static_seq(node.value, const_vars, const_seq_vars, _depth + 1, func_returns=func_returns)
            if seq is not None:
                try:
                    return seq[idx_val]
                except IndexError:
                    return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _eval_static_str(node.left, const_vars, const_seq_vars, _depth + 1, func_returns)
        right = _eval_static_str(node.right, const_vars, const_seq_vars, _depth + 1, func_returns)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _eval_static_str(node.left, const_vars, const_seq_vars, _depth + 1, func_returns)
        right = _eval_static_str(node.right, const_vars, const_seq_vars, _depth + 1, func_returns)
        if left is not None and right is not None:
            return f"{left.rstrip('/\\\\')}/{right}"
        return None
    if isinstance(node, ast.JoinedStr):
        parts: List[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            elif isinstance(val, ast.FormattedValue) and val.format_spec is None:
                resolved = _eval_static_str(val.value, const_vars, const_seq_vars, _depth + 1, func_returns)
                if resolved is None:
                    return None
                parts.append(resolved)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.Call):
        fn = node.func
        if (
            isinstance(fn, ast.Name)
            and func_returns
            and fn.id in func_returns
            and not node.args
            and not node.keywords
        ):
            for ret_expr in func_returns[fn.id]:
                val = _eval_static_str(ret_expr, const_vars, const_seq_vars, _depth + 1, func_returns)
                if val is not None:
                    return val
        if isinstance(fn, ast.Name) and fn.id == "chr" and len(node.args) == 1 and not node.keywords:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, int) and not isinstance(arg0.value, bool):
                if 0 <= arg0.value <= 0x10FFFF:
                    return chr(arg0.value)
            return None
        if isinstance(fn, ast.Attribute) and fn.attr == "join" and len(node.args) == 1 and not node.keywords:
            sep = _eval_static_str(fn.value, const_vars, const_seq_vars, _depth + 1, func_returns)
            if sep is not None:
                seq_vals = _eval_static_seq(
                    node.args[0], const_vars, const_seq_vars, _depth + 1, func_returns=func_returns
                )
                if seq_vals is not None and all(v is not None for v in seq_vals):
                    return sep.join(v for v in seq_vals if v is not None)
        if (
            (isinstance(fn, ast.Name) and fn.id in ("Path", "PurePath", "PosixPath", "WindowsPath"))
            or (
                isinstance(fn, ast.Attribute)
                and fn.attr in ("Path", "PurePath", "PosixPath", "WindowsPath", "join")
                and (
                    fn.attr != "join"
                    or (isinstance(fn.value, ast.Attribute) and fn.value.attr == "path")
                    or (isinstance(fn.value, ast.Name) and fn.value.id in ("path", "posixpath", "ntpath"))
                )
            )
        ):
            if node.args and not node.keywords:
                segs: List[str] = []
                for arg in node.args:
                    val = _eval_static_str(arg, const_vars, const_seq_vars, _depth + 1, func_returns)
                    if val is None:
                        return None
                    segs.append(val)
                return "/".join(s.replace("\\", "/") for s in segs)
    return None


def _eval_static_str_candidates(
    node: ast.AST,
    cur_str: Dict[str, str],
    cur_seq: Dict[str, List[Optional[str]]],
    alt_str: Dict[str, List[str]],
    func_returns: Dict[str, List[ast.AST]],
    _depth: int = 0,
) -> List[str]:
    """Retourne les chaînes statiques candidates d'une expression AST (y compris `IfExp`, branches, helpers)."""
    if _depth > 6:
        return []
    out: List[str] = []

    def _add(val: Optional[str]) -> None:
        if val is not None and val not in out and len(out) < 8:
            out.append(val)

    if isinstance(node, ast.IfExp):
        for branch in (node.body, node.orelse):
            for c in _eval_static_str_candidates(branch, cur_str, cur_seq, alt_str, func_returns, _depth + 1):
                _add(c)
        return out
    if isinstance(node, ast.NamedExpr):
        return _eval_static_str_candidates(node.value, cur_str, cur_seq, alt_str, func_returns, _depth + 1)
    if isinstance(node, ast.Name):
        if node.id in cur_str:
            _add(cur_str[node.id])
        for alt in alt_str.get(node.id, ()):
            _add(alt)
        return out
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in func_returns
        and not node.args
        and not node.keywords
    ):
        for ret_expr in func_returns[node.func.id]:
            for c in _eval_static_str_candidates(ret_expr, cur_str, cur_seq, alt_str, func_returns, _depth + 1):
                _add(c)
        if out:
            return out
    _add(_eval_static_str(node, cur_str, cur_seq, 0, func_returns))
    return out


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
            for c in _eval_static_seq_candidates(
                branch, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1
            ):
                _add(c)
        return out
    if isinstance(node, ast.NamedExpr):
        return _eval_static_seq_candidates(
            node.value, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1
        )
    if isinstance(node, ast.Name):
        if node.id in cur_seq:
            _add(cur_seq[node.id])
        for alt in alt_seq.get(node.id, ()):
            _add(alt)
        return out
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in func_returns
        and not node.args
        and not node.keywords
    ):
        for ret_expr in func_returns[node.func.id]:
            for c in _eval_static_seq_candidates(
                ret_expr, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns, _depth + 1
            ):
                _add(c)
        if out:
            return out
    _add(
        _eval_static_seq(
            node, cur_str, cur_seq, 0, shlex_mod_names, shlex_split_names, func_returns
        )
    )
    return out


def _collect_static_bindings(
    tree: ast.Module,
    shlex_mod_names: Set[str],
    shlex_split_names: Set[str],
) -> Tuple[Dict[str, str], Dict[str, List[Optional[str]]], Dict[str, ast.AST]]:
    """Collecte les constantes chaînes et listes/tuples assignées une seule fois dans l'AST."""
    bind_counts: Dict[str, int] = {}
    load_counts: Dict[str, int] = {}
    mutated_vars: Set[str] = set()

    def _bump(name: str) -> None:
        if name:
            bind_counts[name] = bind_counts.get(name, 0) + 1

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                _bump(node.id)
            elif isinstance(node.ctx, ast.Load):
                load_counts[node.id] = load_counts.get(node.id, 0) + 1
        elif isinstance(node, ast.arg):
            _bump(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            _bump(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                _bump(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _bump(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for nm in node.names:
                _bump(nm)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("append", "extend", "insert", "pop", "clear", "remove") and isinstance(
                node.func.value, ast.Name
            ):
                mutated_vars.add(node.func.value.id)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            if isinstance(node.value, ast.Name):
                mutated_vars.add(node.value.id)

    assign_pairs: List[Tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assign_pairs.append((node.targets[0].id, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assign_pairs.append((node.target.id, node.value))

    const_vars: Dict[str, str] = {}
    const_seq_vars: Dict[str, List[Optional[str]]] = {}
    single_use_defs: Dict[str, ast.AST] = {}
    for _ in range(3):
        changed = False
        for var_name, rhs in assign_pairs:
            if bind_counts.get(var_name, 0) != 1 or var_name in mutated_vars:
                continue
            if var_name not in const_vars and var_name not in const_seq_vars:
                s_val = _eval_static_str(rhs, const_vars, const_seq_vars)
                if s_val is not None:
                    const_vars[var_name] = s_val
                    if load_counts.get(var_name, 0) == 1:
                        single_use_defs[var_name] = rhs
                    changed = True
                    continue
                seq_val = _eval_static_seq(
                    rhs, const_vars, const_seq_vars, 0, shlex_mod_names, shlex_split_names
                )
                if seq_val is not None:
                    const_seq_vars[var_name] = seq_val
                    if load_counts.get(var_name, 0) == 1:
                        single_use_defs[var_name] = rhs
                    changed = True
        if not changed:
            break
    return const_vars, const_seq_vars, single_use_defs


def _collect_const_str_vars(tree: ast.Module) -> Dict[str, str]:
    """Collecte les constantes chaînes assignées une seule fois dans l'AST."""
    const_vars, _, _ = _collect_static_bindings(tree, {"shlex"}, set())
    return const_vars


def _mark_subtree(node: ast.AST, target_set: Set[ast.AST]) -> None:
    for child in ast.walk(node):
        target_set.add(child)


def _iter_stmt_expr_nodes(stmt: ast.AST) -> List[ast.AST]:
    """Retourne les nœuds d'expressions appartenant directement à `stmt` (sans descendre dans les blocs imbriqués)."""
    out: List[ast.AST] = []
    skip_types = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
    )

    def _visit(node: ast.AST) -> None:
        out.append(node)
        for field_name, field_val in ast.iter_fields(node):
            if node is stmt and field_name in ("body", "orelse", "handlers", "finalbody"):
                continue
            if isinstance(field_val, ast.AST):
                if isinstance(field_val, skip_types):
                    continue
                _visit(field_val)
            elif isinstance(field_val, list):
                for item in field_val:
                    if isinstance(item, ast.AST) and not isinstance(item, skip_types):
                        _visit(item)

    _visit(stmt)
    return out


def _resolve_dynamic_import_module(
    node: ast.AST,
    const_vars: Dict[str, str],
    const_seq_vars: Dict[str, List[Optional[str]]],
    importlib_mod_names: Set[str],
    import_module_fns: Set[str],
) -> Optional[str]:
    """Retourne le nom de module statique d'un appel `__import__('...')` ou `importlib.import_module('...')`."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    fn = node.func
    is_dyn_imp = (
        (isinstance(fn, ast.Name) and (fn.id == "__import__" or fn.id in import_module_fns))
        or (
            isinstance(fn, ast.Attribute)
            and fn.attr == "import_module"
            and isinstance(fn.value, ast.Name)
            and fn.value.id in importlib_mod_names
        )
    )
    if not is_dyn_imp:
        return None
    return _eval_static_str(node.args[0], const_vars, const_seq_vars)


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
        if is_harness_protected(lit, ws_root):
            return "deny", f"Script `{context_label}` ciblant les garde-fous Gravity bloqué."
        if matches_any(lit, SENSITIVE_PATH_PATTERNS):
            return "deny", f"Script `{context_label}` ciblant un chemin sensible bloqué."
    return None


def _inspect_python_ast(
    code: str,
    ws_root: Optional[Path] = None,
    *,
    context_label: str = "python -c",
    allow_top_level_await: bool = False,
    _depth: int = 0,
) -> Tuple[str, Optional[str]]:
    """Détecteur AST chirurgical focalisé sur les menaces `deny` (secrets, `os.environ`, harnais actif, rmtree/subprocess destructeurs)."""
    try:
        if allow_top_level_await:
            flags = getattr(ast, "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0)
            tree = (
                ast.parse(code, mode="exec", type_comments=False)
                if not flags
                else compile(code, f"<{context_label}>", "exec", flags=ast.PyCF_ONLY_AST | flags)
            )
        else:
            tree = ast.parse(code, mode="exec", type_comments=False)
    except (SyntaxError, ValueError, TypeError):
        raw_deny = _raw_code_hard_deny_check(code, ws_root, context_label)
        if raw_deny is not None:
            return raw_deny
        if context_label == "run_workflow":
            return (
                "force_ask",
                "Script `run_workflow` non analysable par AST : validation humaine requise.",
            )
        return "allow", None

    # Collecter les alias importés pour subprocess / os / re / shlex / shutil / importlib
    os_mod_names: Set[str] = {"os", "posix", "nt"}
    subprocess_mod_names: Set[str] = {"subprocess"}
    re_mod_names: Set[str] = {"re"}
    re_func_names: Set[str] = set()
    shlex_mod_names: Set[str] = {"shlex"}
    shutil_mod_names: Set[str] = {"shutil"}
    importlib_mod_names: Set[str] = {"importlib"}
    import_module_fns: Set[str] = set()
    shlex_split_names: Set[str] = set()
    rmtree_func_names: Set[str] = set()
    os_shell_func_names: Set[str] = set()
    os_exec_list_names: Set[str] = set()
    os_exec_var_names: Set[str] = set()
    os_spawn_list_names: Set[str] = set()
    os_spawn_var_names: Set[str] = set()
    subprocess_func_names: Set[str] = set()
    func_returns: Dict[str, List[ast.AST]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if alias.name in ("os", "posix", "nt"):
                    os_mod_names.add(bound)
                elif alias.name == "subprocess":
                    subprocess_mod_names.add(bound)
                elif alias.name == "re":
                    re_mod_names.add(bound)
                elif alias.name == "shlex":
                    shlex_mod_names.add(bound)
                elif alias.name == "shutil":
                    shutil_mod_names.add(bound)
                elif alias.name == "importlib":
                    importlib_mod_names.add(bound)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                if node.module in ("os", "posix", "nt"):
                    if alias.name in ("environ", "environb"):
                        return "deny", f"Import de `os.environ` dans `{context_label}` bloqué."
                    if alias.name in OS_SHELL_FUNCS:
                        os_shell_func_names.add(bound)
                    elif alias.name in OS_EXEC_LIST_FUNCS:
                        os_exec_list_names.add(bound)
                    elif alias.name in OS_EXEC_VAR_FUNCS:
                        os_exec_var_names.add(bound)
                    elif alias.name in OS_SPAWN_LIST_FUNCS:
                        os_spawn_list_names.add(bound)
                    elif alias.name in OS_SPAWN_VAR_FUNCS:
                        os_spawn_var_names.add(bound)
                elif node.module == "shutil" and alias.name == "rmtree":
                    rmtree_func_names.add(bound)
                elif node.module == "subprocess" and alias.name in SUBPROCESS_CALL_FUNCS:
                    subprocess_func_names.add(bound)
                elif node.module == "shlex" and alias.name == "split":
                    shlex_split_names.add(bound)
                elif node.module == "importlib" and alias.name == "import_module":
                    import_module_fns.add(bound)
                elif node.module == "re" and alias.name in RE_PATTERN_FUNCS:
                    re_func_names.add(bound)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.args.args and not node.args.posonlyargs and not node.args.kwonlyargs:
                rets = [
                    sub.value
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Return) and sub.value is not None
                ]
                if rets:
                    func_returns.setdefault(node.name, []).extend(rets[:4])

    const_vars, const_seq_vars, single_use_defs = _collect_static_bindings(
        tree, shlex_mod_names, shlex_split_names
    )

    checked_cmd_nodes: Set[ast.AST] = set()
    exempt_regex_nodes: Set[ast.AST] = set()
    resolved_expr_strs: Dict[ast.AST, List[str]] = {}
    alt_str: Dict[str, List[str]] = {}
    alt_seq: Dict[str, List[List[Optional[str]]]] = {}
    var_contributors: Dict[str, List[ast.AST]] = {}
    benign_cmd_name_nodes: Set[ast.Name] = set()
    mutator_receiver_names: Set[ast.Name] = set()

    def _record_alt_str(name: str, val: str) -> None:
        bucket = alt_str.setdefault(name, [])
        if val not in bucket and len(bucket) < 8:
            bucket.append(val)

    def _record_alt_seq(name: str, val: List[Optional[str]]) -> None:
        bucket = alt_seq.setdefault(name, [])
        snap = list(val)
        if snap not in bucket and len(bucket) < 8:
            bucket.append(snap)

    def _add_resolved_str(node_key: ast.AST, val: str) -> None:
        bucket = resolved_expr_strs.setdefault(node_key, [])
        if val not in bucket and len(bucket) < 8:
            bucket.append(val)

    def _bind_var(
        target_name: str,
        rhs_node: ast.AST,
        cur_str: Dict[str, str],
        cur_seq: Dict[str, List[Optional[str]]],
        cur_subproc_mods: Set[str],
        cur_os_mods: Set[str],
        cur_rmtree_fns: Set[str],
        cur_os_shell_fns: Set[str],
        cur_subproc_fns: Set[str],
    ) -> None:
        var_contributors.setdefault(target_name, []).append(rhs_node)
        dyn_mod = _resolve_dynamic_import_module(
            rhs_node, cur_str, cur_seq, importlib_mod_names, import_module_fns
        )
        if dyn_mod == "subprocess":
            cur_subproc_mods.add(target_name)
        elif dyn_mod in ("os", "posix", "nt"):
            cur_os_mods.add(target_name)
        elif isinstance(rhs_node, ast.Attribute):
            if rhs_node.attr == "rmtree":
                cur_rmtree_fns.add(target_name)
            elif rhs_node.attr in OS_SHELL_FUNCS:
                cur_os_shell_fns.add(target_name)
            elif rhs_node.attr in SUBPROCESS_CALL_FUNCS and (
                (isinstance(rhs_node.value, ast.Name) and rhs_node.value.id in cur_subproc_mods)
                or _resolve_dynamic_import_module(
                    rhs_node.value, cur_str, cur_seq, importlib_mod_names, import_module_fns
                )
                == "subprocess"
            ):
                cur_subproc_fns.add(target_name)
        elif (
            isinstance(rhs_node, ast.Call)
            and isinstance(rhs_node.func, ast.Name)
            and rhs_node.func.id == "getattr"
            and len(rhs_node.args) >= 2
        ):
            for g_attr in _eval_static_str_candidates(
                rhs_node.args[1], cur_str, cur_seq, alt_str, func_returns
            ):
                if g_attr == "rmtree":
                    cur_rmtree_fns.add(target_name)
                elif g_attr in OS_SHELL_FUNCS:
                    cur_os_shell_fns.add(target_name)
                elif g_attr in SUBPROCESS_CALL_FUNCS:
                    cur_subproc_fns.add(target_name)

        str_cands = _eval_static_str_candidates(rhs_node, cur_str, cur_seq, alt_str, func_returns)
        if str_cands:
            cur_str[target_name] = str_cands[0]
            cur_seq.pop(target_name, None)
            for c in str_cands:
                _record_alt_str(target_name, c)
            return
        seq_cands = _eval_static_seq_candidates(
            rhs_node, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns
        )
        if seq_cands:
            cur_seq[target_name] = list(seq_cands[0])
            cur_str.pop(target_name, None)
            for sc in seq_cands:
                _record_alt_seq(target_name, sc)
            return
        cur_str.pop(target_name, None)
        cur_seq.pop(target_name, None)

    def _inspect_call(
        node: ast.Call,
        cur_str: Dict[str, str],
        cur_seq: Dict[str, List[Optional[str]]],
        cur_subproc_mods: Set[str],
        cur_os_mods: Set[str],
        cur_rmtree_fns: Set[str],
        cur_os_shell_fns: Set[str],
        cur_subproc_fns: Set[str],
    ) -> Optional[Tuple[str, str]]:
        fn = node.func
        kw_map = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}

        # Motifs regex littéraux dans re.compile / re.search / from re import search (évite faux positif sur r'\.env')
        is_re_call = (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id in re_mod_names
            and fn.attr in RE_PATTERN_FUNCS
        ) or (isinstance(fn, ast.Name) and fn.id in re_func_names)
        if is_re_call:
            pat_node = node.args[0] if node.args else kw_map.get("pattern")
            if isinstance(pat_node, ast.Constant) and isinstance(pat_node.value, str):
                exempt_regex_nodes.add(pat_node)
            elif isinstance(pat_node, ast.Name) and pat_node.id in single_use_defs:
                exempt_regex_nodes.add(pat_node)
                _mark_subtree(single_use_defs[pat_node.id], exempt_regex_nodes)

        # getattr(obj, 'environ' | 'rmtree' | 'system' | 'popen')
        if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
            for attr_str in _eval_static_str_candidates(
                node.args[1], cur_str, cur_seq, alt_str, func_returns
            ):
                if attr_str in ("environ", "environb"):
                    return "deny", f"Accès dynamique à `environ` dans `{context_label}` bloqué."

        # Résolution dynamique getattr(shutil, 'rmtree')(...) ou getattr(os, 'system')(...)
        resolved_dyn_attrs: List[str] = []
        if (
            isinstance(fn, ast.Call)
            and isinstance(fn.func, ast.Name)
            and fn.func.id == "getattr"
            and len(fn.args) >= 2
        ):
            resolved_dyn_attrs = _eval_static_str_candidates(
                fn.args[1], cur_str, cur_seq, alt_str, func_returns
            )

        dyn_recv_mod = (
            _resolve_dynamic_import_module(
                fn.value, cur_str, cur_seq, importlib_mod_names, import_module_fns
            )
            if isinstance(fn, ast.Attribute)
            else None
        )

        # 1a. shutil.rmtree(...) destructeur
        is_rmtree = (
            (isinstance(fn, ast.Name) and fn.id in cur_rmtree_fns)
            or (isinstance(fn, ast.Attribute) and fn.attr == "rmtree")
            or ("rmtree" in resolved_dyn_attrs)
        )
        if is_rmtree:
            target_node = node.args[0] if node.args else kw_map.get("path")
            if target_node is not None:
                for target_str in _eval_static_str_candidates(
                    target_node, cur_str, cur_seq, alt_str, func_returns
                ):
                    if is_harness_protected(target_str, ws_root) or _is_active_plugin_root_path(
                        target_str, ws_root
                    ):
                        return "deny", f"Suppression des garde-fous Gravity via `rmtree` dans `{context_label}` bloquée."
                    if _is_destructive_target(target_str):
                        return "deny", f"Suppression récursive destructrice `shutil.rmtree` dans `{context_label}` bloquée."

        # 1b. os.system / os.popen
        is_os_shell = (
            (isinstance(fn, ast.Name) and fn.id in cur_os_shell_fns)
            or (isinstance(fn, ast.Attribute) and fn.attr in OS_SHELL_FUNCS)
            or any(a in OS_SHELL_FUNCS for a in resolved_dyn_attrs)
        )
        if is_os_shell and _depth < MAX_SUBSHELL_DEPTH:
            shell_arg = node.args[0] if node.args else (kw_map.get("command") or kw_map.get("cmd"))
            if shell_arg is not None:
                str_cands = _eval_static_str_candidates(
                    shell_arg, cur_str, cur_seq, alt_str, func_returns
                )
                if str_cands:
                    for cmd_str in str_cands:
                        deny_reason, _ = _evaluate_shell_command(cmd_str, ws_root, _depth=_depth + 1)
                        if deny_reason:
                            return "deny", deny_reason
                    _mark_subtree(shell_arg, checked_cmd_nodes)
                    if isinstance(shell_arg, ast.Name):
                        benign_cmd_name_nodes.add(shell_arg)
                        if shell_arg.id in single_use_defs:
                            _mark_subtree(single_use_defs[shell_arg.id], checked_cmd_nodes)

        # 1c. subprocess.{run, Popen, call, check_call, check_output, getoutput, getstatusoutput}
        is_subproc = (
            (isinstance(fn, ast.Name) and fn.id in cur_subproc_fns)
            or (
                isinstance(fn, ast.Attribute)
                and fn.attr in SUBPROCESS_CALL_FUNCS
                and (
                    (isinstance(fn.value, ast.Name) and fn.value.id in cur_subproc_mods)
                    or dyn_recv_mod == "subprocess"
                )
            )
            or any(a in SUBPROCESS_CALL_FUNCS for a in resolved_dyn_attrs)
        )
        if is_subproc and _depth < MAX_SUBSHELL_DEPTH:
            cmd_node = node.args[0] if node.args else kw_map.get("args")
            if cmd_node is not None:
                str_cands = _eval_static_str_candidates(
                    cmd_node, cur_str, cur_seq, alt_str, func_returns
                )
                seq_cands = _eval_static_seq_candidates(
                    cmd_node, cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns
                )
                for cmd_str in str_cands:
                    deny_reason, _ = _evaluate_shell_command(cmd_str, ws_root, _depth=_depth + 1)
                    if deny_reason:
                        return "deny", deny_reason
                for seq_vals in seq_cands:
                    if not seq_vals:
                        continue
                    argv = [val if val is not None else "__dynamic__" for val in seq_vals]
                    reconstructed = _argv_to_shell_cmd(argv)
                    deny_reason, _ = _evaluate_shell_command(
                        reconstructed, ws_root, _depth=_depth + 1
                    )
                    if deny_reason:
                        return "deny", deny_reason
                if str_cands or seq_cands:
                    if isinstance(cmd_node, (ast.List, ast.Tuple)):
                        for elt in cmd_node.elts:
                            if _eval_static_str(elt, cur_str, cur_seq, 0, func_returns) is not None:
                                _mark_subtree(elt, checked_cmd_nodes)
                    elif isinstance(cmd_node, ast.Name):
                        benign_cmd_name_nodes.add(cmd_node)
                        if cmd_node.id in single_use_defs:
                            def_rhs = single_use_defs[cmd_node.id]
                            if isinstance(def_rhs, (ast.List, ast.Tuple)):
                                for elt in def_rhs.elts:
                                    if _eval_static_str(elt, cur_str, cur_seq, 0, func_returns) is not None:
                                        _mark_subtree(elt, checked_cmd_nodes)
                            else:
                                _mark_subtree(def_rhs, checked_cmd_nodes)
                            _mark_subtree(cmd_node, checked_cmd_nodes)
                    elif isinstance(cmd_node, ast.Call) or str_cands:
                        _mark_subtree(cmd_node, checked_cmd_nodes)

        # 1d. os.exec* / os.spawn* (vérifie à la fois argv et [exe_path] + argv[1:] contre argv[0] usurpé)
        if _depth < MAX_SUBSHELL_DEPTH:
            argv_candidates: List[List[str]] = []
            is_os_attr = isinstance(fn, ast.Attribute) and (
                (isinstance(fn.value, ast.Name) and fn.value.id in cur_os_mods)
                or dyn_recv_mod in ("os", "posix", "nt")
            )
            exec_list_hit = (is_os_attr and fn.attr in OS_EXEC_LIST_FUNCS) or (
                isinstance(fn, ast.Name) and fn.id in os_exec_list_names
            )
            spawn_list_hit = (is_os_attr and fn.attr in OS_SPAWN_LIST_FUNCS) or (
                isinstance(fn, ast.Name) and fn.id in os_spawn_list_names
            )
            exec_var_hit = (is_os_attr and fn.attr in OS_EXEC_VAR_FUNCS) or (
                isinstance(fn, ast.Name) and fn.id in os_exec_var_names
            )
            spawn_var_hit = (is_os_attr and fn.attr in OS_SPAWN_VAR_FUNCS) or (
                isinstance(fn, ast.Name) and fn.id in os_spawn_var_names
            )
            if exec_list_hit and len(node.args) >= 2:
                exe_cands = _eval_static_str_candidates(
                    node.args[0], cur_str, cur_seq, alt_str, func_returns
                )
                for seq_vals in _eval_static_seq_candidates(
                    node.args[1], cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns
                ):
                    if seq_vals:
                        argv = [v if v is not None else "__dynamic__" for v in seq_vals]
                        argv_candidates.append(argv)
                        for exe_s in exe_cands:
                            argv_candidates.append([exe_s] + argv[1:])
            elif spawn_list_hit and len(node.args) >= 3:
                exe_cands = _eval_static_str_candidates(
                    node.args[1], cur_str, cur_seq, alt_str, func_returns
                )
                for seq_vals in _eval_static_seq_candidates(
                    node.args[2], cur_str, cur_seq, alt_seq, shlex_mod_names, shlex_split_names, func_returns
                ):
                    if seq_vals:
                        argv = [v if v is not None else "__dynamic__" for v in seq_vals]
                        argv_candidates.append(argv)
                        for exe_s in exe_cands:
                            argv_candidates.append([exe_s] + argv[1:])
            elif exec_var_hit and len(node.args) >= 2:
                exe_cands = _eval_static_str_candidates(
                    node.args[0], cur_str, cur_seq, alt_str, func_returns
                )
                argv = [
                    _eval_static_str(e, cur_str, cur_seq, 0, func_returns) or "__dynamic__"
                    for e in node.args[1:]
                ]
                argv_candidates.append(argv)
                for exe_s in exe_cands:
                    argv_candidates.append([exe_s] + argv[1:])
            elif spawn_var_hit and len(node.args) >= 3:
                exe_cands = _eval_static_str_candidates(
                    node.args[1], cur_str, cur_seq, alt_str, func_returns
                )
                argv = [
                    _eval_static_str(e, cur_str, cur_seq, 0, func_returns) or "__dynamic__"
                    for e in node.args[2:]
                ]
                argv_candidates.append(argv)
                for exe_s in exe_cands:
                    argv_candidates.append([exe_s] + argv[1:])
            for argv in argv_candidates:
                reconstructed = _argv_to_shell_cmd(argv)
                deny_reason, _ = _evaluate_shell_command(
                    reconstructed, ws_root, _depth=_depth + 1
                )
                if deny_reason:
                    return "deny", deny_reason

        # 1e. eval/exec sur chaîne statique
        if isinstance(fn, ast.Name) and fn.id in ("eval", "exec") and node.args and _depth < MAX_SUBSHELL_DEPTH:
            code_cands = _eval_static_str_candidates(
                node.args[0], cur_str, cur_seq, alt_str, func_returns
            )
            if code_cands:
                for inner_code in code_cands:
                    sub_dec, sub_reason = _inspect_python_ast(
                        inner_code,
                        ws_root,
                        context_label=context_label,
                        allow_top_level_await=allow_top_level_await,
                        _depth=_depth + 1,
                    )
                    if sub_dec == "deny":
                        return sub_dec, sub_reason or f"Code dynamique `{fn.id}` bloqué."
                _mark_subtree(node.args[0], checked_cmd_nodes)
                if isinstance(node.args[0], ast.Name):
                    benign_cmd_name_nodes.add(node.args[0])
                    if node.args[0].id in single_use_defs:
                        _mark_subtree(single_use_defs[node.args[0].id], checked_cmd_nodes)

        return None

    def _walk_stmts(
        stmts: List[ast.stmt],
        cur_str: Dict[str, str],
        cur_seq: Dict[str, List[Optional[str]]],
        cur_subproc_mods: Set[str],
        cur_os_mods: Set[str],
        cur_rmtree_fns: Set[str],
        cur_os_shell_fns: Set[str],
        cur_subproc_fns: Set[str],
    ) -> Optional[Tuple[str, str]]:
        for stmt in stmts:
            # 1. Inspecter les appels, walrus (`:=`) et expressions de l'instruction courante
            expr_nodes = _iter_stmt_expr_nodes(stmt)
            for enode in expr_nodes:
                if isinstance(enode, ast.NamedExpr) and isinstance(enode.target, ast.Name):
                    _bind_var(
                        enode.target.id,
                        enode.value,
                        cur_str,
                        cur_seq,
                        cur_subproc_mods,
                        cur_os_mods,
                        cur_rmtree_fns,
                        cur_os_shell_fns,
                        cur_subproc_fns,
                    )
            for enode in expr_nodes:
                if isinstance(enode, ast.Call):
                    verdict = _inspect_call(
                        enode,
                        cur_str,
                        cur_seq,
                        cur_subproc_mods,
                        cur_os_mods,
                        cur_rmtree_fns,
                        cur_os_shell_fns,
                        cur_subproc_fns,
                    )
                    if verdict is not None:
                        return verdict
                if isinstance(enode, (ast.Name, ast.BinOp, ast.JoinedStr, ast.Call, ast.Subscript)):
                    if isinstance(enode, ast.Name) and not isinstance(enode.ctx, ast.Load):
                        continue
                    for s_val in _eval_static_str_candidates(
                        enode, cur_str, cur_seq, alt_str, func_returns
                    ):
                        if s_val:
                            _add_resolved_str(enode, s_val)

            # 2. Mettre à jour les liaisons statiques locales (Assign multi-cibles/unpacking/subscript, AnnAssign, AugAssign, .append/.extend/.insert)
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        _bind_var(
                            tgt.id,
                            stmt.value,
                            cur_str,
                            cur_seq,
                            cur_subproc_mods,
                            cur_os_mods,
                            cur_rmtree_fns,
                            cur_os_shell_fns,
                            cur_subproc_fns,
                        )
                    elif isinstance(tgt, (ast.Tuple, ast.List)) and isinstance(
                        stmt.value, (ast.Tuple, ast.List)
                    ) and len(tgt.elts) == len(stmt.value.elts):
                        for t_elt, v_elt in zip(tgt.elts, stmt.value.elts):
                            if isinstance(t_elt, ast.Name):
                                _bind_var(
                                    t_elt.id,
                                    v_elt,
                                    cur_str,
                                    cur_seq,
                                    cur_subproc_mods,
                                    cur_os_mods,
                                    cur_rmtree_fns,
                                    cur_os_shell_fns,
                                    cur_subproc_fns,
                                )
                    elif isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                        var_name = tgt.value.id
                        if var_name in cur_seq:
                            idx_val = _eval_static_int(_ast_subscript_slice(tgt))
                            if idx_val is not None:
                                mutator_receiver_names.add(tgt.value)
                                var_contributors.setdefault(var_name, []).append(stmt.value)
                                new_val = _eval_static_str(stmt.value, cur_str, cur_seq, 0, func_returns)
                                try:
                                    cur_seq[var_name][idx_val] = new_val
                                    _record_alt_seq(var_name, cur_seq[var_name])
                                except IndexError:
                                    pass
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                _bind_var(
                    stmt.target.id,
                    stmt.value,
                    cur_str,
                    cur_seq,
                    cur_subproc_mods,
                    cur_os_mods,
                    cur_rmtree_fns,
                    cur_os_shell_fns,
                    cur_subproc_fns,
                )
            elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name) and isinstance(stmt.op, ast.Add):
                var_name = stmt.target.id
                var_contributors.setdefault(var_name, []).append(stmt.value)
                if var_name in cur_str:
                    rhs_s = _eval_static_str(stmt.value, cur_str, cur_seq, 0, func_returns)
                    if rhs_s is not None:
                        cur_str[var_name] = cur_str[var_name] + rhs_s
                        _record_alt_str(var_name, cur_str[var_name])
                    else:
                        cur_str.pop(var_name, None)
                elif var_name in cur_seq:
                    rhs_seq = _eval_static_seq(
                        stmt.value, cur_str, cur_seq, 0, shlex_mod_names, shlex_split_names, func_returns
                    )
                    if rhs_seq is not None:
                        cur_seq[var_name] = cur_seq[var_name] + rhs_seq
                    else:
                        cur_seq[var_name] = cur_seq[var_name] + [None]
                    _record_alt_seq(var_name, cur_seq[var_name])

            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                call = stmt.value
                if (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in cur_seq
                ):
                    target_var = call.func.value.id
                    if call.func.attr == "append" and len(call.args) == 1:
                        mutator_receiver_names.add(call.func.value)
                        var_contributors.setdefault(target_var, []).append(call.args[0])
                        cur_seq[target_var].append(
                            _eval_static_str(call.args[0], cur_str, cur_seq, 0, func_returns)
                        )
                        _record_alt_seq(target_var, cur_seq[target_var])
                    elif call.func.attr == "extend" and len(call.args) == 1:
                        mutator_receiver_names.add(call.func.value)
                        var_contributors.setdefault(target_var, []).append(call.args[0])
                        ext_vals = _eval_static_seq(
                            call.args[0], cur_str, cur_seq, 0, shlex_mod_names, shlex_split_names, func_returns
                        )
                        if ext_vals is not None:
                            cur_seq[target_var].extend(ext_vals)
                        else:
                            cur_seq[target_var].append(None)
                        _record_alt_seq(target_var, cur_seq[target_var])
                    elif call.func.attr == "insert" and len(call.args) == 2:
                        idx_val = _eval_static_int(call.args[0])
                        if idx_val is not None:
                            mutator_receiver_names.add(call.func.value)
                            var_contributors.setdefault(target_var, []).append(call.args[1])
                            cur_seq[target_var].insert(
                                idx_val,
                                _eval_static_str(call.args[1], cur_str, cur_seq, 0, func_returns),
                            )
                            _record_alt_seq(target_var, cur_seq[target_var])

            # 3. Descendre dans les blocs imbriqués (sans écraser les branches `if`/`else`/`try`/`except`)
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                verdict = _walk_stmts(
                    stmt.body,
                    dict(cur_str),
                    {k: list(v) for k, v in cur_seq.items()},
                    set(cur_subproc_mods),
                    set(cur_os_mods),
                    set(cur_rmtree_fns),
                    set(cur_os_shell_fns),
                    set(cur_subproc_fns),
                )
                if verdict is not None:
                    return verdict
            elif isinstance(stmt, (ast.If, ast.Try)):
                branch_lists: List[List[ast.stmt]] = []
                if stmt.body:
                    branch_lists.append(list(stmt.body))
                if getattr(stmt, "orelse", None):
                    branch_lists.append(list(stmt.orelse))
                for handler in getattr(stmt, "handlers", None) or []:
                    if getattr(handler, "body", None):
                        branch_lists.append(list(handler.body))
                base_str = dict(cur_str)
                base_seq = {k: list(v) for k, v in cur_seq.items()}
                for b_stmts in branch_lists:
                    b_str = dict(base_str)
                    b_seq = {k: list(v) for k, v in base_seq.items()}
                    b_sub_m = set(cur_subproc_mods)
                    b_os_m = set(cur_os_mods)
                    b_rm = set(cur_rmtree_fns)
                    b_sh = set(cur_os_shell_fns)
                    b_sf = set(cur_subproc_fns)
                    verdict = _walk_stmts(
                        b_stmts, b_str, b_seq, b_sub_m, b_os_m, b_rm, b_sh, b_sf
                    )
                    if verdict is not None:
                        return verdict
                    cur_subproc_mods.update(b_sub_m)
                    cur_os_mods.update(b_os_m)
                    cur_rmtree_fns.update(b_rm)
                    cur_os_shell_fns.update(b_sh)
                    cur_subproc_fns.update(b_sf)
                    for k, v in b_str.items():
                        cur_str[k] = v
                        _record_alt_str(k, v)
                    for k, v in b_seq.items():
                        cur_seq[k] = list(v)
                        _record_alt_seq(k, v)
                final_stmts = getattr(stmt, "finalbody", None)
                if isinstance(final_stmts, list) and final_stmts:
                    verdict = _walk_stmts(
                        final_stmts,
                        cur_str,
                        cur_seq,
                        cur_subproc_mods,
                        cur_os_mods,
                        cur_rmtree_fns,
                        cur_os_shell_fns,
                        cur_subproc_fns,
                    )
                    if verdict is not None:
                        return verdict
            else:
                for attr in ("body", "orelse", "finalbody"):
                    sub_stmts = getattr(stmt, attr, None)
                    if isinstance(sub_stmts, list) and sub_stmts:
                        verdict = _walk_stmts(
                            sub_stmts,
                            cur_str,
                            cur_seq,
                            cur_subproc_mods,
                            cur_os_mods,
                            cur_rmtree_fns,
                            cur_os_shell_fns,
                            cur_subproc_fns,
                        )
                        if verdict is not None:
                            return verdict
        return None

    # Passe 1 : parcours séquentiel par blocs (rmtree, subprocess, os.system/popen/exec/spawn, eval/exec, re.*)
    pass1_verdict = _walk_stmts(
        list(tree.body),
        dict(const_vars),
        {k: list(v) for k, v in const_seq_vars.items()},
        set(subprocess_mod_names),
        set(os_mod_names),
        set(rmtree_func_names),
        set(os_shell_func_names),
        set(subprocess_func_names),
    )
    if pass1_verdict is not None:
        return pass1_verdict

    # Exempter les contributions de variables utilisées uniquement dans des appels subprocess/os.system déjà validés
    other_loaded_vars: Set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node not in benign_cmd_name_nodes
            and node not in mutator_receiver_names
        ):
            other_loaded_vars.add(node.id)
    for cmd_name_node in benign_cmd_name_nodes:
        if cmd_name_node.id not in other_loaded_vars:
            checked_cmd_nodes.add(cmd_name_node)
            for contrib in var_contributors.get(cmd_name_node.id, ()):
                _mark_subtree(contrib, checked_cmd_nodes)

    # Passe 2 : accès `environ`, chemins sensibles et auto-protection du harnais actif
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "environb"):
            return "deny", f"Accès à `environ` dans `{context_label}` bloqué."
        if isinstance(node, ast.Subscript):
            slice_node = _ast_subscript_slice(node)
            key_cands = list(resolved_expr_strs.get(slice_node, ()))
            fallback_key = _eval_static_str(slice_node, const_vars, const_seq_vars, 0, func_returns)
            if fallback_key and fallback_key not in key_cands:
                key_cands.append(fallback_key)
            if any(k in ("environ", "environb") for k in key_cands):
                val_node = node.value
                is_env_dict = (
                    (isinstance(val_node, ast.Attribute) and val_node.attr == "__dict__")
                    or (
                        isinstance(val_node, ast.Call)
                        and isinstance(val_node.func, ast.Name)
                        and val_node.func.id == "vars"
                    )
                )
                if is_env_dict:
                    return "deny", f"Accès indirect à `environ` dans `{context_label}` bloqué."
        if node in checked_cmd_nodes or node in exempt_regex_nodes:
            continue
        if isinstance(node, (ast.Constant, ast.Name, ast.BinOp, ast.JoinedStr, ast.Call, ast.Subscript)):
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                continue
            if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
                continue
            vals = list(resolved_expr_strs.get(node, ()))
            fallback_val = _eval_static_str(node, const_vars, const_seq_vars, 0, func_returns)
            if fallback_val and fallback_val not in vals:
                vals.append(fallback_val)
            for val in vals:
                if is_harness_protected(val, ws_root):
                    return "deny", f"Script `{context_label}` ciblant les garde-fous Gravity bloqué."
                if matches_any(val, SENSITIVE_PATH_PATTERNS):
                    return "deny", f"Script `{context_label}` ciblant un chemin sensible bloqué."

    return "allow", None


def inspect_inline_python_code(
    code: str, ws_root: Optional[Path] = None, _depth: int = 0
) -> Tuple[str, Optional[str]]:
    """Inspecte un script `python -c` pour menaces `deny` (secrets, `os.environ`, harnais actif, rmtree/subprocess destructeurs)."""
    return _inspect_python_ast(
        code,
        ws_root,
        context_label="python -c",
        allow_top_level_await=False,
        _depth=_depth,
    )


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


def _strip_js_comments(code: str) -> str:
    """Supprime les commentaires JS (`//...`, `/*...*/`) hors chaînes, templates et regex littéraux."""
    out: List[str] = []
    i = 0
    n = len(code)
    in_sq = False
    in_dq = False
    last_sig = ""

    while i < n:
        ch = code[i]
        if in_sq:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == "'":
                in_sq = False
                last_sig = "'"
            i += 1
            continue
        if in_dq:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == '"':
                in_dq = False
                last_sig = '"'
            i += 1
            continue
        if ch == "'":
            in_sq = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_dq = True
            out.append(ch)
            i += 1
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
            # Littéral regex JS `/pattern/flags`
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
            in_sq = False
            in_dq = False
            while j < n and brace_depth > 0:
                c = code[j]
                if in_sq:
                    if c == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if c == "'":
                        in_sq = False
                    j += 1
                    continue
                if in_dq:
                    if c == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if c == '"':
                        in_dq = False
                    j += 1
                    continue
                if c == "'":
                    in_sq = True
                    j += 1
                    continue
                if c == '"':
                    in_dq = True
                    j += 1
                    continue
                if c == "`":
                    _, j = _scan_js_template_literal(code, j + 1)
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


def _split_script_top_level(text: str, sep: str) -> List[str]:
    """Découpe une expression JS/Perl/Ruby sur `sep` uniquement au niveau 0 hors chaînes/templates."""
    items: List[str] = []
    start = 0
    depth = 0
    in_sq = False
    in_dq = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_sq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_dq = False
            i += 1
            continue
        if ch == "'":
            in_sq = True
            i += 1
            continue
        if ch == '"':
            in_dq = True
            i += 1
            continue
        if ch == "`":
            _, i = _scan_js_template_literal(text, i + 1)
            continue
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
    in_sq = False
    in_dq = False
    i = open_idx + 1
    n = len(code)
    while i < n and depth > 0:
        ch = code[i]
        if in_sq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_dq = False
            i += 1
            continue
        if ch == "'":
            in_sq = True
            i += 1
            continue
        if ch == '"':
            in_dq = True
            i += 1
            continue
        if ch == "`":
            _, i = _scan_js_template_literal(code, i + 1)
            continue
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
    """Extrait la valeur d'une chaîne `'...'` ou `"..."` complète (incluant `\\xHH`/`\\uHHHH`)."""
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

    # Concaténation `+` au niveau supérieur
    plus_parts = _split_script_top_level(s, "+")
    if len(plus_parts) > 1:
        acc: List[str] = []
        for piece in plus_parts:
            val = _eval_js_str_expr(
                piece,
                js_str_vars,
                allow_dynamic_placeholder=allow_dynamic_placeholder,
                _depth=_depth + 1,
            )
            if val is None:
                if allow_dynamic_placeholder:
                    acc.append("__dynamic__")
                else:
                    return None
            else:
                acc.append(val)
        return "".join(acc)

    # Chaîne simple ou double
    unq = _unescape_quoted_literal(s)
    if unq is not None:
        return unq

    # Template literal JS `...${...}...`
    if s.startswith("`") and s.endswith("`"):
        tpl_parts, end_idx = _scan_js_template_literal(s, 1)
        if end_idx == len(s):
            acc = []
            for kind, val in tpl_parts:
                if kind == "text":
                    acc.append(val)
                else:
                    ev = _eval_js_str_expr(
                        val,
                        js_str_vars,
                        allow_dynamic_placeholder=allow_dynamic_placeholder,
                        _depth=_depth + 1,
                    )
                    if ev is None:
                        if allow_dynamic_placeholder:
                            acc.append("__dynamic__")
                        else:
                            return None
                    else:
                        acc.append(ev)
            return "".join(acc)

    # Identifiant de variable JS statique
    if re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", s) and s in js_str_vars:
        return js_str_vars[s]

    # String.fromCharCode(46, ...)
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

    # ['.', 'env'].join('')
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
                    item,
                    js_str_vars,
                    allow_dynamic_placeholder=allow_dynamic_placeholder,
                    _depth=_depth + 1,
                )
                if val is None:
                    return None
                elts.append(val)
            return sep.join(elts)

    # path.join(...) / posix.join(...)
    m_pjoin = re.match(r"^(?:path|posix|win32)(?:\.posix|\.win32)?\.join\s*\((.*)\)$", s, re.DOTALL)
    if m_pjoin:
        segs: List[str] = []
        for item in _split_script_top_level(m_pjoin.group(1), ","):
            if not item.strip():
                continue
            val = _eval_js_str_expr(
                item,
                js_str_vars,
                allow_dynamic_placeholder=allow_dynamic_placeholder,
                _depth=_depth + 1,
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
    in_sq = False
    in_dq = False
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        if in_sq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_dq = False
            i += 1
            continue
        if ch == "'":
            in_sq = True
            i += 1
            continue
        if ch == '"':
            in_dq = True
            i += 1
            continue
        if ch == "`":
            _, i = _scan_js_template_literal(code, i + 1)
            continue
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
    in_sq = False
    in_dq = False

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
        if in_sq:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == '"':
                in_dq = False
            i += 1
            continue
        if ch == "'":
            in_sq = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_dq = True
            out.append(ch)
            i += 1
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

    rebuilt_parts: List[str] = []
    literals: List[str] = []
    for kind, q, val in merged:
        if kind == "str":
            literals.append(val)
            safe_val = val.replace("\\", "\\\\").replace(q, "\\" + q)
            rebuilt_parts.append(f"{q}{safe_val}{q}")
        else:
            rebuilt_parts.append(val)
    return "".join(rebuilt_parts), literals


def _inspect_inline_script_code(
    lang: str, code: str, ws_root: Optional[Path] = None, _depth: int = 0
) -> Optional[Tuple[str, str]]:
    """Inspecte un script inline `node -e`, `perl -e` ou `ruby -e` pour menaces `deny` uniquement."""
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

    # Masquer les motifs littéraux passés à `new RegExp(...)` pour éviter faux positifs sur `\\.env`
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

    # Suppressions récursives destructrices natives (fs.rmSync('/'), fs['rmSync']('/'), FileUtils.rm_rf('/'), rmtree('/'))
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

    # Appels de sous-processus / commandes shell imbriqués
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

        # Inspecter les expressions JS imbriquées dans les template literals `${...}`
        for expr_code in embedded_exprs:
            sub_res = _inspect_inline_script_code("node", expr_code, ws_root=ws_root, _depth=_depth + 1)
            if sub_res is not None and sub_res[0] == "deny":
                return sub_res

    return None


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

    # Exfiltration de secrets via `find ... -name "*.env" -exec ...`
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

    if head == "node":
        i = 0
        n = len(args)
        while i < n:
            tok = args[i]
            if tok == "-":
                return "node"
            if tok == "--":
                return "node" if (i + 1 < n and args[i + 1] == "-") else None
            if not tok.startswith("-"):
                return None
            if tok in NODE_VALUE_OPTS:
                i += 2
                continue
            i += 1
        return "node"

    if head == "perl":
        i = 0
        n = len(args)
        while i < n:
            tok = args[i]
            if tok == "-":
                return "perl"
            if tok == "--":
                return "perl" if (i + 1 < n and args[i + 1] == "-") else None
            if not tok.startswith("-"):
                return None
            if tok == "-I":
                i += 2
                continue
            i += 1
        return "perl"

    if head == "ruby":
        i = 0
        n = len(args)
        while i < n:
            tok = args[i]
            if tok == "-":
                return "ruby"
            if tok == "--":
                return "ruby" if (i + 1 < n and args[i + 1] == "-") else None
            if not tok.startswith("-"):
                return None
            if tok in RUBY_VALUE_OPTS:
                i += 2
                continue
            i += 1
        return "ruby"

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


def _find_unquoted_heredoc_ops(line: str) -> List[Tuple[bool, str]]:
    """Extrait tous les opérateurs heredoc POSIX `<<`/`<<-` hors guillemets sur une ligne (hors `<<<`)."""
    ops: List[Tuple[bool, str]] = []
    in_sq = False
    in_dq = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_sq:
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if ch in ("\\", "`") and i + 1 < n and line[i + 1] == '"':
                i += 2
                continue
            if ch == '"':
                in_dq = False
            i += 1
            continue
        if ch == "'":
            in_sq = True
            i += 1
            continue
        if ch == '"':
            in_dq = True
            i += 1
            continue
        if ch == "<" and i + 1 < n and line[i + 1] == "<":
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
    """Extrait le payload d'un here-string bash `<<< "..."` hors guillemets."""
    in_sq = False
    in_dq = False
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if in_sq:
            if ch == "'":
                in_sq = False
            i += 1
            continue
        if in_dq:
            if ch in ("\\", "`") and i + 1 < n and segment[i + 1] == '"':
                i += 2
                continue
            if ch == '"':
                in_dq = False
            i += 1
            continue
        if ch == "'":
            in_sq = True
            i += 1
            continue
        if ch == '"':
            in_dq = True
            i += 1
            continue
        if segment[i : i + 3] == "<<<":
            tail = segment[i + 3 :].strip()
            if not tail:
                return None
            toks = _tokenize_shell_segment_rich(tail)
            return toks[0][0] if toks else None
        i += 1
    return None


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

        # kind in ("shell", "ps_command", "cmd")
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

    # 1. Hard deny : curl/wget | shell/python et irm/iwr | iex
    for idx, (_, h, _, _, _) in enumerate(stage_info[:-1]):
        downstream_heads = [dh for _, dh, _, _, _ in stage_info[idx + 1 :]]
        if h in DOWNLOAD_CMD_HEADS:
            if any(dh in PIPE_SHELL_HEADS or _is_python_head(dh) for dh in downstream_heads):
                return "Téléchargement et exécution directe via pipe shell bloqués.", None, consumed_literal_stages
        if h in PS_DOWNLOAD_CMD_HEADS:
            if any(dh in ("iex", "invoke-expression") for dh in downstream_heads):
                return "Téléchargement et exécution PowerShell directe (`irm | iex`) bloqués.", None, consumed_literal_stages

    # 2. Hard deny : PowerShell here-string / echo / variable pipé vers interpréteur stdin (`@'...'@ | python -`, etc.)
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

    # 3. Heuristic force_ask : base64 -d / FromBase64String | shell/interpreter/iex/eval
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
    if workspace_root is not None:
        ws_root = workspace_root
    elif isinstance(data, dict) and (data.get("cwd") or data.get("workspacePaths")):
        ws_root = resolve_workspace_root(data, __file__)
    else:
        ws_root = HOOKS_DIR.parent

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
                search_bases.extend([ws_root, HOOKS_DIR.parent])
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
