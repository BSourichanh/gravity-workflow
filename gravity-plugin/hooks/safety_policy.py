#!/usr/bin/env python3
"""Politique de sécurité pure pour PreToolUse (100 % stdlib, sans I/O sur stdout)."""

import ast
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from hook_runtime import first_str_arg  # noqa: E402

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

HARNESS_SELF_PROTECT_PATTERNS = [
    re.compile(
        r"(?:^hooks\.json$|(?:^|/)(?:\.agents|gravity-plugin|plugins/gravity|gravity)/hooks\.json$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|/)(?:\.agents/gravity|gravity-plugin|plugins/gravity|gravity)/hooks(?:/|$)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|/)(?:\.agents/rules/)?01-safety(?:-boundaries)?\.md$", re.IGNORECASE),
]

PATH_TOKEN_RE = re.compile(r"""[^\s"'`;&|()<>]+""")
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

ENV_DUMP_PATTERN = re.compile(
    r"(?:^|[;&|]\s*)(?:printenv\b|env\s*(?:$|[;&|>])|export\s+-p\b|declare\s+-x\b|"
    r"(?:Get-ChildItem|gci|ls|dir|Get-Item|gi|Get-Content|gc|cat)\s+Env:|Get-Variable\b)|"
    r"\[(?:System\.)?Environment\]::GetEnvironmentVariables\b",
    re.IGNORECASE,
)

DANGEROUS_CMDS = [
    (
        re.compile(
            r"\b(?:drop\s+(?:database|schema|table)|truncate\s+table|flushall|flushdb)\b",
            re.IGNORECASE,
        ),
        "Suppression massive de base de données bloquée.",
    ),
    (
        ENV_DUMP_PATTERN,
        "Affichage global des variables d'environnement bloqué (anti-fuite secrets).",
    ),
    (
        re.compile(
            r"(?:^|[\s/\"'=:(])\.env(?:\.(?!example\b|sample\b|template\b)[^\s\"'`,;)]*)?(?:$|[\s\"'`,;)])",
            re.IGNORECASE,
        ),
        "Accès shell au fichier .env bloqué (anti-contournement secrets).",
    ),
    (
        re.compile(
            r"(?:^|[\s/\"'=:(])(?:secrets/|[^\s\"'`,;)]*\.(?:pem|key|p12|pfx)(?:$|[\s\"'`,;)])|"
            r"(?:\.ssh/)?(?:id_rsa|id_ed25519|id_ecdsa|id_dsa)(?:$|[\s\"'`,;)]))",
            re.IGNORECASE,
        ),
        "Accès shell à un secret, certificat ou clé SSH bloqué.",
    ),
    (
        re.compile(
            r"\b(?:curl|wget)(?:\.exe)?\b[^\n]*?\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|python3?|py|pwsh|powershell)(?:\.exe)?\b",
            re.IGNORECASE,
        ),
        "Téléchargement et exécution directe via pipe shell bloqués.",
    ),
    (
        re.compile(
            r"\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n]*?\|\s*(?:iex|Invoke-Expression)\b",
            re.IGNORECASE,
        ),
        "Téléchargement et exécution PowerShell directe (`irm | iex`) bloqués.",
    ),
]

OPAQUE_ASK_CMDS = [
    (
        re.compile(r"\bxargs\b", re.IGNORECASE),
        "Exécution dynamique via `xargs` détectée : validation humaine requise.",
    ),
    (
        re.compile(
            r"\|\s*base64\b|FromBase64String",
            re.IGNORECASE,
        ),
        "Décodage ou pipe `base64` détecté : validation humaine requise.",
    ),
    (
        re.compile(r"\b(?:iex|Invoke-Expression)\b", re.IGNORECASE),
        "Évaluation PowerShell dynamique (`iex`/`Invoke-Expression`) détectée : validation humaine requise.",
    ),
    (
        re.compile(r"(?:^|[\s/\"'=:(])\.[a-z0-9_]*[*?]", re.IGNORECASE),
        "Globbing sur fichier caché (ex. `.e*`) détecté : validation humaine requise.",
    ),
]

INLINE_PY_PURE_MODULES = {
    "json",
    "re",
    "ast",
    "csv",
    "tomllib",
    "html",
    "difflib",
    "textwrap",
    "string",
    "unicodedata",
    "pprint",
    "fnmatch",
    "shlex",
    "hashlib",
    "hmac",
    "zlib",
    "math",
    "cmath",
    "decimal",
    "fractions",
    "statistics",
    "random",
    "collections",
    "itertools",
    "functools",
    "operator",
    "heapq",
    "bisect",
    "dataclasses",
    "enum",
    "typing",
    "copy",
    "datetime",
    "time",
    "calendar",
    "zoneinfo",
    "array",
}
INLINE_PY_PURE_SUBMODULES = {
    "collections.abc",
    "html.parser",
    "html.entities",
    "urllib.parse",
}
INLINE_PY_SAFE_SYS_ATTRS = {
    "version",
    "version_info",
    "platform",
    "executable",
    "maxsize",
    "byteorder",
    "prefix",
    "base_prefix",
    "stdin",
    "stdout",
    "stderr",
}
INLINE_PY_SAFE_OS_PATH_PURE_FUNCS = {
    "join",
    "normpath",
    "basename",
    "dirname",
    "splitext",
    "split",
    "relpath",
    "commonpath",
    "commonprefix",
    "normcase",
    "isabs",
}
INLINE_PY_SAFE_OS_PATH_FS_FUNCS = {
    "getsize",
    "exists",
    "isfile",
    "isdir",
    "islink",
    "getmtime",
}
INLINE_PY_SAFE_OS_PATH_FUNCS = (
    INLINE_PY_SAFE_OS_PATH_PURE_FUNCS | INLINE_PY_SAFE_OS_PATH_FS_FUNCS
)
INLINE_PY_SAFE_IO_EXPORTS = {"StringIO", "BytesIO"}
INLINE_PY_SAFE_PATHLIB_CLASSES = {
    "Path",
    "PurePath",
    "PosixPath",
    "WindowsPath",
    "PurePosixPath",
    "PureWindowsPath",
}
INLINE_PY_SAFE_READ_MODES = {"r", "rb", "rt", "br", "tr"}
INLINE_PY_FORBIDDEN_CALL_NAMES = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "breakpoint",
    "input",
    "memoryview",
}
INLINE_PY_FORBIDDEN_ATTRS = {
    "os",
    "sys",
    "io",
    "posix",
    "nt",
    "builtins",
    "subprocess",
    "shutil",
    "socket",
    "ctypes",
    "importlib",
    "threading",
    "multiprocessing",
    "signal",
    "webbrowser",
    "tempfile",
    "runpy",
    "code",
    "pickle",
    "marshal",
    "urllib",
    "http",
    "ssl",
    "inspect",
    "gc",
    "types",
    "winreg",
    "msvcrt",
    "fcntl",
    "pty",
    "platform",
    "glob",
    "system",
    "popen",
    "execv",
    "execl",
    "execve",
    "execvp",
    "execvpe",
    "spawnv",
    "spawnl",
    "fork",
    "kill",
    "rmtree",
    "unlink",
    "rmdir",
    "removedirs",
    "chmod",
    "lchmod",
    "chown",
    "write_text",
    "write_bytes",
    "mkdir",
    "touch",
    "rename",
    "symlink_to",
    "hardlink_to",
    "expanduser",
    "expandvars",
    "home",
    "parent",
    "parents",
    "root",
    "drive",
    "anchor",
    "with_name",
    "with_stem",
    "with_suffix",
    "joinpath",
    "urlopen",
    "getenv",
    "putenv",
    "unsetenv",
    "import_module",
    "eval",
    "exec",
    "posixpath",
    "ntpath",
    "genericpath",
    "linecache",
    "codecs",
    "tokenize",
    "warnings",
    "from_uri",
    "with_segments",
    "samefile",
}
INLINE_PY_EXTRA_SECRET_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".aws",
    ".gnupg",
    ".kube",
    ".docker",
}
INLINE_PY_SHELL_EXPAND_RE = re.compile(r"\$|`|%[A-Za-z_][A-Za-z0-9_]*%|\\\\\"")
INLINE_PY_HIDDEN_GLOB_RE = re.compile(
    r"(?:^|[/\\])\.[a-z0-9_]*[*?]|(?:^|[/\\])\.env", re.IGNORECASE
)


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
        resolved = candidate.resolve()
        active_hooks_dir = HOOKS_DIR.resolve()
        active_hooks_json = (HOOKS_DIR.parent / "hooks.json").resolve()
        return (
            resolved == active_hooks_json
            or resolved == active_hooks_dir
            or active_hooks_dir in resolved.parents
        )
    except (OSError, ValueError):
        return False


def is_harness_protected(path: str, ws_root: Optional[Path] = None) -> bool:
    """Retourne True si `path` cible un fichier protégé du harnais Gravity."""
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


def _match_ps_single_herestring(text: str, start: int) -> Optional[Tuple[str, int]]:
    """Retourne `(corps_interne, index_apres_herestring)` pour `@'\\n...\\n'@` ou None."""
    if text[start : start + 2] != "@'":
        return None
    n = len(text)
    pos = start + 2
    if pos < n and text[pos] == "\r":
        pos += 1
    if pos >= n or text[pos] != "\n":
        return None
    body_start = pos + 1
    if text[body_start : body_start + 2] == "'@":
        after = body_start + 2
        if after == n or text[after].isspace() or text[after] in ";|&)":
            return "", after
    match = re.search(r"(?:\r?\n)'@(?=$|[\s;|&)])", text[body_start:])
    if not match:
        return None
    return text[body_start : body_start + match.start()], body_start + match.end()


def split_shell_segments(
    cmd: str, _depth: int = 0, max_depth: int = MAX_SUBSHELL_DEPTH
) -> List[str]:
    """Découpe une commande shell par séparateurs hors guillemets et extrait `$(...)`/backticks."""
    segments: List[str] = []
    extracted_subcmds: List[str] = []
    current: List[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)

    def _flush() -> None:
        seg_text = "".join(current).strip()
        if seg_text:
            segments.append(seg_text)
        current.clear()

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

        if ch == "@" and cmd[i : i + 2] == "@'":
            hs = _match_ps_single_herestring(cmd, i)
            if hs is not None:
                _, end_idx = hs
                current.append(cmd[i:end_idx])
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
            _flush()
            i += 1
            continue
        if ch == "|":
            _flush()
            i += 2 if (i + 1 < n and cmd[i + 1] == "|") else 1
            continue
        if ch == "&":
            if i + 1 < n and cmd[i + 1] == "&":
                _flush()
                i += 2
                continue
            prev_ch = cmd[i - 1] if i > 0 else ""
            next_ch = cmd[i + 1] if i + 1 < n else ""
            is_redir = prev_ch in (">", "<") or next_ch == ">"
            is_leading_call_op = not "".join(current).strip()
            if not is_redir and not is_leading_call_op:
                _flush()
                i += 1
                continue

        current.append(ch)
        i += 1

    _flush()
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
            if ch == "\\" and i + 1 < n and segment[i + 1] == '"':
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
        if ch == "@" and segment[i : i + 2] == "@'":
            hs = _match_ps_single_herestring(segment, i)
            if hs is not None:
                body, end_idx = hs
                _flush_unquoted()
                has_token = True
                buf.append(body)
                chunks.append(("ps_single_herestring", body))
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


def _is_git_force_push(segment: str) -> bool:
    """Détecte `git ... push` avec `-f`/`--force`/`+ref` strictement dans `segment`."""
    head, args = extract_command_head_and_args(segment)
    if head != "git":
        return False

    i = 0
    n = len(args)
    subcmd = ""
    while i < n:
        tok = args[i]
        if tok == "--":
            if i + 1 < n:
                subcmd = args[i + 1].lower()
                i += 2
            break
        if tok in GIT_GLOBAL_VALUE_OPTS:
            i += 2
            continue
        if len(tok) > 2 and (tok.startswith("-C") or tok.startswith("-c")):
            i += 1
            continue
        if tok.startswith("-"):
            i += 1
            continue
        subcmd = tok.lower()
        i += 1
        break

    if subcmd != "push":
        return False

    after_double_dash = False
    for arg in args[i:]:
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


def _has_unquoted_redirection(segment: str) -> bool:
    """Retourne True si `segment` contient `>`/`>>` hors guillemets (hors `>&1`/`>&2`/`>&-`)."""
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
        if ch == "@" and segment[i : i + 2] == "@'":
            hs = _match_ps_single_herestring(segment, i)
            if hs is not None:
                _, end_idx = hs
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
        if ch == ">":
            if i + 1 < n and segment[i + 1] == "&":
                if i + 2 < n and (segment[i + 2].isdigit() or segment[i + 2] == "-"):
                    i += 3
                    continue
            return True
        i += 1
    return False


def _is_safe_inline_path(
    raw_path: str, ws_root: Optional[Path] = None
) -> Tuple[str, Optional[str]]:
    """Valide qu'un chemin littéral dans `python -c` reste bénin et borné au workspace."""
    if is_harness_protected(raw_path, ws_root):
        return "deny", "Code Python inline (`-c`) ciblant les garde-fous Gravity bloqué."
    if matches_any(raw_path, SENSITIVE_PATH_PATTERNS):
        return "deny", "Code Python inline (`-c`) ciblant un chemin sensible bloqué."

    cleaned = raw_path.strip()
    if not cleaned or "\x00" in cleaned:
        return (
            "force_ask",
            "Chemin vide ou invalide dans `python -c` : validation humaine requise.",
        )

    norm = cleaned.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    if norm.startswith("~") or "~" in parts:
        return (
            "force_ask",
            "Accès au répertoire personnel (`~`) dans `python -c` : validation humaine requise.",
        )
    if ".." in parts:
        return (
            "force_ask",
            "Traversée de répertoire (`..`) dans `python -c` : validation humaine requise.",
        )
    for part in parts:
        if part.lower() in INLINE_PY_EXTRA_SECRET_NAMES:
            return (
                "force_ask",
                f"Accès à un chemin sensible caché (`{part}`) dans `python -c` : validation humaine requise.",
            )

    if re.match(r"^[A-Za-z]:(?![/\\])", norm):
        return (
            "force_ask",
            f"Chemin relatif au lecteur ambigu (`{cleaned}`) dans `python -c` : validation humaine requise.",
        )

    is_abs = norm.startswith("/") or bool(re.match(r"^[A-Za-z]:", norm))
    if is_abs:
        if ws_root is None:
            return (
                "force_ask",
                "Chemin absolu dans `python -c` : validation humaine requise.",
            )
        try:
            ws_res = ws_root.resolve()
            cand_res = Path(cleaned).resolve()
            if cand_res == ws_res or ws_res in cand_res.parents:
                return "allow", None
        except (OSError, ValueError):
            pass
        return (
            "force_ask",
            f"Accès fichier hors du workspace (`{cleaned}`) dans `python -c` : validation humaine requise.",
        )

    if ws_root is not None:
        try:
            ws_res = ws_root.resolve()
            cand_res = (ws_res / cleaned).resolve()
            if cand_res != ws_res and ws_res not in cand_res.parents:
                return (
                    "force_ask",
                    f"Résolution de chemin hors du workspace (`{cleaned}`) dans `python -c` : validation humaine requise.",
                )
        except (OSError, ValueError):
            return (
                "force_ask",
                f"Chemin non résoluble (`{cleaned}`) dans `python -c` : validation humaine requise.",
            )

    return "allow", None


def _resolve_static_str(node: ast.AST, const_str_vars: Dict[str, str]) -> Optional[str]:
    """Résout un nœud AST vers une chaîne statique (littéral ou constante module unique)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in const_str_vars:
        return const_str_vars[node.id]
    return None


def _collect_const_str_vars(
    tree: ast.Module, parent_map: Dict[ast.AST, ast.AST]
) -> Dict[str, str]:
    """Collecte les variables chaînes constantes liées une seule fois au niveau module."""
    bind_counts: Dict[str, int] = {}

    def _bump(name: str) -> None:
        if name:
            bind_counts[name] = bind_counts.get(name, 0) + 1

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            _bump(node.id)
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

    const_vars: Dict[str, str] = {}
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and parent_map.get(stmt) is tree
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            var_name = stmt.targets[0].id
            if bind_counts.get(var_name, 0) == 1:
                const_vars[var_name] = stmt.value.value
    return const_vars


def _has_starred_or_kwargs(call: ast.Call) -> bool:
    """Retourne True si l'appel utilise `*args` ou `**kwargs`."""
    return any(isinstance(a, ast.Starred) for a in call.args) or any(
        kw.arg is None for kw in call.keywords
    )


def _validate_inline_py_imports(
    tree: ast.Module,
) -> Tuple[
    Optional[Tuple[str, str]],
    Set[str],
    Set[str],
    Set[str],
    Set[str],
    Set[str],
    Set[str],
]:
    """Valide les imports de `python -c` contre l'allowlist stricte et collecte les symboles restreints."""
    restricted_sys_names: Set[str] = set()
    restricted_os_names: Set[str] = set()
    restricted_os_path_names: Set[str] = set()
    os_path_fs_func_names: Set[str] = set()
    restricted_pathlib_mod_names: Set[str] = set()
    path_ctor_names: Set[str] = set()

    def _fail(dec: str, msg: str) -> Tuple[
        Tuple[str, str], Set[str], Set[str], Set[str], Set[str], Set[str], Set[str]
    ]:
        return (
            (dec, msg),
            restricted_sys_names,
            restricted_os_names,
            restricted_os_path_names,
            os_path_fs_func_names,
            restricted_pathlib_mod_names,
            path_ctor_names,
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                bound = alias.asname or mod.split(".")[0]
                if mod in INLINE_PY_PURE_MODULES or mod in {
                    "collections.abc",
                    "html.parser",
                    "html.entities",
                }:
                    continue
                if mod == "urllib.parse":
                    if not alias.asname:
                        return _fail(
                            "force_ask",
                            "Code Python inline (`-c`) : utiliser `import urllib.parse as ...` ou `from urllib.parse import ...`.",
                        )
                    continue
                if mod == "sys":
                    restricted_sys_names.add(bound)
                    continue
                if mod == "os":
                    restricted_os_names.add(bound)
                    continue
                if mod == "os.path":
                    if alias.asname:
                        restricted_os_path_names.add(alias.asname)
                    else:
                        restricted_os_names.add("os")
                    continue
                if mod == "pathlib":
                    restricted_pathlib_mod_names.add(bound)
                    continue
                return _fail(
                    "force_ask",
                    f"Code Python inline (`-c`) : import `{mod}` non autorisé automatiquement — validation humaine requise.",
                )

        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level > 0 or not mod:
                return _fail(
                    "force_ask",
                    "Code Python inline (`-c`) : import relatif non autorisé automatiquement — validation humaine requise.",
                )
            for alias in node.names:
                if alias.name == "*":
                    return _fail(
                        "force_ask",
                        f"Code Python inline (`-c`) : import wildcard `from {mod} import *` non autorisé automatiquement.",
                    )
                bound = alias.asname or alias.name
                if mod in INLINE_PY_PURE_MODULES or mod in INLINE_PY_PURE_SUBMODULES:
                    if alias.name.startswith("_") or alias.name in INLINE_PY_FORBIDDEN_ATTRS:
                        return _fail(
                            "force_ask",
                            f"Code Python inline (`-c`) : symbole `{mod}.{alias.name}` non autorisé automatiquement — validation humaine requise.",
                        )
                    continue
                if mod == "pathlib":
                    if alias.name not in INLINE_PY_SAFE_PATHLIB_CLASSES:
                        return _fail(
                            "force_ask",
                            f"Code Python inline (`-c`) : `from pathlib import {alias.name}` non autorisé automatiquement — validation humaine requise.",
                        )
                    path_ctor_names.add(bound)
                    continue
                if mod == "sys":
                    if alias.name not in INLINE_PY_SAFE_SYS_ATTRS:
                        return _fail(
                            "force_ask",
                            f"Code Python inline (`-c`) : `from sys import {alias.name}` non autorisé automatiquement — validation humaine requise.",
                        )
                    continue
                if mod == "os.path":
                    if alias.name not in INLINE_PY_SAFE_OS_PATH_FUNCS:
                        return _fail(
                            "force_ask",
                            f"Code Python inline (`-c`) : `from os.path import {alias.name}` non autorisé automatiquement — validation humaine requise.",
                        )
                    if alias.name in INLINE_PY_SAFE_OS_PATH_FS_FUNCS:
                        os_path_fs_func_names.add(bound)
                    continue
                if mod == "os":
                    if alias.name == "environ":
                        return _fail(
                            "deny",
                            "Import de `os.environ` dans `python -c` bloqué.",
                        )
                    if alias.name == "path":
                        restricted_os_path_names.add(bound)
                        continue
                    return _fail(
                        "force_ask",
                        f"Code Python inline (`-c`) : `from os import {alias.name}` non autorisé automatiquement — validation humaine requise.",
                    )
                if mod == "io":
                    if alias.name not in INLINE_PY_SAFE_IO_EXPORTS:
                        return _fail(
                            "force_ask",
                            f"Code Python inline (`-c`) : `from io import {alias.name}` non autorisé automatiquement — validation humaine requise.",
                        )
                    continue
                return _fail(
                    "force_ask",
                    f"Code Python inline (`-c`) : import `{mod}` non autorisé automatiquement — validation humaine requise.",
                )

    return (
        None,
        restricted_sys_names,
        restricted_os_names,
        restricted_os_path_names,
        os_path_fs_func_names,
        restricted_pathlib_mod_names,
        path_ctor_names,
    )


def _check_inline_py_call(
    node: ast.Call,
    ws_root: Optional[Path],
    const_str_vars: Dict[str, str],
    restricted_os_names: Set[str],
    restricted_os_path_names: Set[str],
    os_path_fs_func_names: Set[str],
    restricted_pathlib_mod_names: Set[str],
    path_ctor_names: Set[str],
) -> Optional[Tuple[str, str]]:
    """Valide un nœud `ast.Call` pour les opérations I/O, `Path`, `os.path`, `.glob` et `.replace`."""
    fn = node.func
    kw_map = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}

    if isinstance(fn, ast.Name) and fn.id == "open":
        if _has_starred_or_kwargs(node):
            return (
                "force_ask",
                "Appel `open()` avec `*args`/`**kwargs` dans `python -c` : validation humaine requise.",
            )
        if len(node.args) >= 8 or "opener" in kw_map:
            return (
                "force_ask",
                "Paramètre `opener` dans `open()` dans `python -c` : validation humaine requise.",
            )
        if node.args and "file" in kw_map:
            return (
                "force_ask",
                "Arguments ambigus dans `open()` dans `python -c` : validation humaine requise.",
            )
        file_node = node.args[0] if node.args else kw_map.get("file")
        if file_node is None:
            return (
                "force_ask",
                "Appel `open()` sans cible explicite dans `python -c` : validation humaine requise.",
            )
        file_str = _resolve_static_str(file_node, const_str_vars)
        if file_str is None:
            return (
                "force_ask",
                "Chemin dynamique dans `open()` dans `python -c` : validation humaine requise.",
            )
        p_dec, p_reason = _is_safe_inline_path(file_str, ws_root)
        if p_dec != "allow":
            return p_dec, p_reason or "Chemin non autorisé dans `open()`."
        if len(node.args) >= 2 and "mode" in kw_map:
            return (
                "force_ask",
                "Argument `mode` ambigu dans `open()` dans `python -c` : validation humaine requise.",
            )
        mode_node = node.args[1] if len(node.args) >= 2 else kw_map.get("mode")
        if mode_node is not None:
            mode_str = _resolve_static_str(mode_node, const_str_vars)
            if mode_str is None or mode_str not in INLINE_PY_SAFE_READ_MODES:
                return (
                    "force_ask",
                    "Mode d'ouverture non lecture seule dans `open()` dans `python -c` : validation humaine requise.",
                )
        return None

    is_path_ctor = (isinstance(fn, ast.Name) and fn.id in path_ctor_names) or (
        isinstance(fn, ast.Attribute)
        and isinstance(fn.value, ast.Name)
        and fn.value.id in restricted_pathlib_mod_names
        and fn.attr in INLINE_PY_SAFE_PATHLIB_CLASSES
    )
    if is_path_ctor:
        if _has_starred_or_kwargs(node) or node.keywords:
            return (
                "force_ask",
                "Appel `Path()` avec `*args` ou paramètres nommés dans `python -c` : validation humaine requise.",
            )
        if not node.args:
            return (
                "force_ask",
                "Appel `Path()` sans argument explicite dans `python -c` : validation humaine requise.",
            )
        resolved_segs: List[str] = []
        for seg_idx, arg_node in enumerate(node.args):
            seg_str = _resolve_static_str(arg_node, const_str_vars)
            if seg_str is None:
                return (
                    "force_ask",
                    "Chemin dynamique dans `Path()` dans `python -c` : validation humaine requise.",
                )
            seg_dec, seg_reason = _is_safe_inline_path(seg_str, ws_root)
            if seg_dec != "allow":
                return seg_dec, seg_reason or "Chemin non autorisé dans `Path()`."
            if seg_idx > 0 and (
                seg_str.replace("\\", "/").startswith("/")
                or bool(re.match(r"^[A-Za-z]:", seg_str))
            ):
                return (
                    "force_ask",
                    "Segment absolu après le premier argument dans `Path()` dans `python -c` : validation humaine requise.",
                )
            resolved_segs.append(seg_str)
        joined_dec, joined_reason = _is_safe_inline_path(
            str(Path(*resolved_segs)), ws_root
        )
        if joined_dec != "allow":
            return joined_dec, joined_reason or "Chemin joint non autorisé dans `Path()`."
        return None

    is_os_path_fs_call = (
        (isinstance(fn, ast.Name) and fn.id in os_path_fs_func_names)
        or (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id in restricted_os_path_names
            and fn.attr in INLINE_PY_SAFE_OS_PATH_FS_FUNCS
        )
        or (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Attribute)
            and fn.value.attr == "path"
            and isinstance(fn.value.value, ast.Name)
            and fn.value.value.id in restricted_os_names
            and fn.attr in INLINE_PY_SAFE_OS_PATH_FS_FUNCS
        )
    )
    if is_os_path_fs_call:
        if _has_starred_or_kwargs(node):
            return (
                "force_ask",
                "Appel `os.path.*` avec `*args`/`**kwargs` dans `python -c` : validation humaine requise.",
            )
        p_node = (
            node.args[0]
            if node.args
            else kw_map.get("path") or kw_map.get("filename")
        )
        if p_node is None:
            return (
                "force_ask",
                "Appel `os.path.*` sans chemin explicite dans `python -c` : validation humaine requise.",
            )
        p_str = _resolve_static_str(p_node, const_str_vars)
        if p_str is None:
            return (
                "force_ask",
                "Chemin dynamique dans `os.path.*` dans `python -c` : validation humaine requise.",
            )
        p_dec, p_reason = _is_safe_inline_path(p_str, ws_root)
        if p_dec != "allow":
            return p_dec, p_reason or "Chemin non autorisé dans `os.path.*`."
        return None

    if isinstance(fn, ast.Attribute) and fn.attr == "open":
        if _has_starred_or_kwargs(node) or len(node.args) > 5 or "opener" in kw_map:
            return (
                "force_ask",
                "Paramètres non autorisés sur `.open()` dans `python -c` : validation humaine requise.",
            )
        if node.args and "mode" in kw_map:
            return (
                "force_ask",
                "Argument `mode` ambigu sur `.open()` dans `python -c` : validation humaine requise.",
            )
        mode_node = node.args[0] if node.args else kw_map.get("mode")
        if mode_node is not None:
            mode_str = _resolve_static_str(mode_node, const_str_vars)
            if mode_str is None or mode_str not in INLINE_PY_SAFE_READ_MODES:
                return (
                    "force_ask",
                    "Mode d'ouverture non lecture seule dans `.open()` dans `python -c` : validation humaine requise.",
                )
        return None

    if isinstance(fn, ast.Attribute) and fn.attr in ("glob", "rglob"):
        if _has_starred_or_kwargs(node):
            return (
                "force_ask",
                "Appel `.glob()`/`.rglob()` avec `*args`/`**kwargs` dans `python -c` : validation humaine requise.",
            )
        pat_node = node.args[0] if node.args else kw_map.get("pattern")
        pat_str = (
            _resolve_static_str(pat_node, const_str_vars)
            if pat_node is not None
            else None
        )
        if pat_str is None:
            return (
                "force_ask",
                "Motif dynamique dans `.glob()`/`.rglob()` dans `python -c` : validation humaine requise.",
            )
        if ".." in pat_str.replace("\\", "/").split("/") or INLINE_PY_HIDDEN_GLOB_RE.search(
            pat_str
        ):
            return (
                "force_ask",
                f"Motif sensible ou traversée dans `.{fn.attr}()` dans `python -c` : validation humaine requise.",
            )
        return None

    if isinstance(fn, ast.Attribute) and fn.attr == "replace":
        if _has_starred_or_kwargs(node) or len(node.args) < 2 or bool(node.keywords):
            return (
                "force_ask",
                "Appel `.replace()` ambigu (possible `Path.replace`) dans `python -c` : validation humaine requise.",
            )

    return None


def inspect_inline_python_code(
    code: str, ws_root: Optional[Path] = None
) -> Tuple[str, Optional[str]]:
    """Analyse statiquement un script `python -c` selon une allowlist fail-closed."""
    if re.search(r"\bos\.environ\b", code):
        return "deny", "Accès à `os.environ` dans `python -c` bloqué."

    try:
        tree = ast.parse(code, mode="exec", type_comments=False)
    except (SyntaxError, ValueError, TypeError):
        return (
            "force_ask",
            "Code Python inline (`-c`) non analysable par AST : validation humaine requise.",
        )

    parent_map: Dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent

    # Passe 1 : blocage ferme (`deny`) prioritaire
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if is_harness_protected(node.value, ws_root):
                return "deny", "Code Python inline (`-c`) ciblant les garde-fous Gravity bloqué."
            if matches_any(node.value, SENSITIVE_PATH_PATTERNS):
                return "deny", "Code Python inline (`-c`) ciblant un chemin sensible bloqué."
        elif isinstance(node, ast.Attribute) and node.attr == "environ":
            return "deny", "Accès à `environ` dans `python -c` bloqué."
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            if any(alias.name == "environ" for alias in node.names):
                return "deny", "Import de `os.environ` dans `python -c` bloqué."
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "environ"
        ):
            return "deny", "Accès dynamique à `environ` dans `python -c` bloqué."

    # Passe 2 : constantes module & allowlist d'imports
    const_str_vars = _collect_const_str_vars(tree, parent_map)
    (
        import_err,
        restricted_sys_names,
        restricted_os_names,
        restricted_os_path_names,
        os_path_fs_func_names,
        restricted_pathlib_mod_names,
        path_ctor_names,
    ) = _validate_inline_py_imports(tree)
    if import_err is not None:
        return import_err

    # Passe 3 : vérification complète de l'AST
    has_pathlib = bool(restricted_pathlib_mod_names or path_ctor_names)
    validated_sys_attr_nodes: Set[ast.AST] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id.startswith("__") and node.id != "__name__":
                return (
                    "force_ask",
                    f"Code Python inline (`-c`) : symbole interne `{node.id}` non autorisé automatiquement — validation humaine requise.",
                )
            if node.id in INLINE_PY_FORBIDDEN_CALL_NAMES:
                return (
                    "force_ask",
                    f"Code Python inline (`-c`) : appel ou référence `{node.id}` non autorisé automatiquement — validation humaine requise.",
                )
            parent = parent_map.get(node)
            if node.id in restricted_sys_names:
                if (
                    isinstance(parent, ast.Attribute)
                    and parent.value is node
                    and parent.attr in INLINE_PY_SAFE_SYS_ATTRS
                    and not isinstance(parent.ctx, (ast.Store, ast.Del))
                ):
                    validated_sys_attr_nodes.add(parent)
                else:
                    return (
                        "force_ask",
                        f"Code Python inline (`-c`) : usage de `sys` hors attributs lecture seule — validation humaine requise.",
                    )
            elif node.id in restricted_os_names:
                gp = parent_map.get(parent) if parent is not None else None
                ok_os = (
                    isinstance(parent, ast.Attribute)
                    and parent.value is node
                    and parent.attr == "path"
                    and isinstance(gp, ast.Attribute)
                    and gp.value is parent
                    and gp.attr in INLINE_PY_SAFE_OS_PATH_FUNCS
                )
                if ok_os and gp is not None and gp.attr in INLINE_PY_SAFE_OS_PATH_FS_FUNCS:
                    ggp = parent_map.get(gp)
                    ok_os = isinstance(ggp, ast.Call) and ggp.func is gp
                if not ok_os:
                    return (
                        "force_ask",
                        "Code Python inline (`-c`) : usage de `os` hors `os.path.*` autorisé — validation humaine requise.",
                    )
            elif node.id in restricted_os_path_names:
                ok_op = (
                    isinstance(parent, ast.Attribute)
                    and parent.value is node
                    and parent.attr in INLINE_PY_SAFE_OS_PATH_FUNCS
                )
                if ok_op and isinstance(parent, ast.Attribute) and parent.attr in INLINE_PY_SAFE_OS_PATH_FS_FUNCS:
                    gp = parent_map.get(parent)
                    ok_op = isinstance(gp, ast.Call) and gp.func is parent
                if not ok_op:
                    return (
                        "force_ask",
                        "Code Python inline (`-c`) : usage non direct de `os.path` — validation humaine requise.",
                    )
            elif node.id in os_path_fs_func_names:
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    return (
                        "force_ask",
                        f"Code Python inline (`-c`) : référence indirecte à `{node.id}` — validation humaine requise.",
                    )
            elif node.id in restricted_pathlib_mod_names:
                gp = parent_map.get(parent) if parent is not None else None
                ok_pl = (
                    isinstance(parent, ast.Attribute)
                    and parent.value is node
                    and parent.attr in INLINE_PY_SAFE_PATHLIB_CLASSES
                    and isinstance(gp, ast.Call)
                    and gp.func is parent
                )
                if not ok_pl:
                    return (
                        "force_ask",
                        "Code Python inline (`-c`) : usage de `pathlib` hors constructeur direct — validation humaine requise.",
                    )
            elif node.id in path_ctor_names or node.id == "open":
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    return (
                        "force_ask",
                        f"Code Python inline (`-c`) : référence indirecte à `{node.id}` — validation humaine requise.",
                    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node in validated_sys_attr_nodes:
                continue
            if node.attr.startswith("_") and node.attr != "__version__":
                return (
                    "force_ask",
                    f"Code Python inline (`-c`) : attribut interne `.{node.attr}` non autorisé automatiquement — validation humaine requise.",
                )
            if node.attr in INLINE_PY_FORBIDDEN_ATTRS:
                return (
                    "force_ask",
                    f"Code Python inline (`-c`) : attribut sensible `.{node.attr}` non autorisé automatiquement — validation humaine requise.",
                )
            if node.attr in ("open", "glob", "rglob"):
                parent = parent_map.get(node)
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    return (
                        "force_ask",
                        f"Code Python inline (`-c`) : référence indirecte à `.{node.attr}` — validation humaine requise.",
                    )

        elif has_pathlib and isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            is_num_const = (
                isinstance(right, ast.Constant)
                and isinstance(right.value, (int, float))
                and not isinstance(right.value, bool)
            )
            is_num_call = (
                isinstance(right, ast.Call)
                and isinstance(right.func, ast.Name)
                and right.func.id in ("len", "int", "float", "sum", "max", "min")
            )
            if not is_num_const and not is_num_call:
                seg = _resolve_static_str(right, const_str_vars)
                if seg is None:
                    return (
                        "force_ask",
                        "Code Python inline (`-c`) : jonction `/` dynamique avec `pathlib` importé — validation humaine requise.",
                    )
                if seg.replace("\\", "/").startswith("/") or bool(
                    re.match(r"^[A-Za-z]:", seg)
                ):
                    return (
                        "force_ask",
                        "Code Python inline (`-c`) : segment absolu après `/` — validation humaine requise.",
                    )
                seg_dec, seg_reason = _is_safe_inline_path(seg, ws_root)
                if seg_dec != "allow":
                    return (
                        seg_dec,
                        seg_reason or "Segment non autorisé après `/` dans `python -c`.",
                    )

        elif isinstance(node, ast.Call):
            call_err = _check_inline_py_call(
                node,
                ws_root,
                const_str_vars,
                restricted_os_names,
                restricted_os_path_names,
                os_path_fs_func_names,
                restricted_pathlib_mod_names,
                path_ctor_names,
            )
            if call_err is not None:
                return call_err

    return "allow", None


def _extract_inline_py_payload(
    rich_args: List[Tuple[str, List[Tuple[str, str]], bool]],
    arg_idx: int,
    flag_pos: int,
    short_flags: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Extrait de façon stricte le code Python passé à `-c` si le quoting shell est sûr."""
    if flag_pos == len(short_flags) - 1:
        if arg_idx + 1 >= len(rich_args):
            return (
                None,
                "Exécution de code Python inline (`-c`) sans code explicite : validation humaine requise.",
            )
        tok_val, chunks, unterminated = rich_args[arg_idx + 1]
        if unterminated:
            return (
                None,
                "Guillemets non fermés dans le code Python inline (`-c`) : validation humaine requise.",
            )
        if len(chunks) != 1:
            return (
                None,
                "Quoting multiple ou ambigu dans le code Python inline (`-c`) : validation humaine requise.",
            )
        kind, raw_content = chunks[0]
        if kind in ("double", "unquoted") and INLINE_PY_SHELL_EXPAND_RE.search(raw_content):
            return (
                None,
                "Interpolation shell potentielle détectée dans `python -c` : validation humaine requise.",
            )
        if not tok_val.strip():
            return (
                None,
                "Code Python inline (`-c`) vide : validation humaine requise.",
            )
        return tok_val, None

    _, chunks, unterminated = rich_args[arg_idx]
    if unterminated:
        return (
            None,
            "Guillemets non fermés dans le code Python inline (`-c`) : validation humaine requise.",
        )
    inline_tail = short_flags[flag_pos + 1 :]
    if len(chunks) == 1 and chunks[0][0] == "unquoted":
        if INLINE_PY_SHELL_EXPAND_RE.search(inline_tail):
            return (
                None,
                "Interpolation shell potentielle détectée dans `python -c` : validation humaine requise.",
            )
    elif (
        len(chunks) == 2
        and chunks[0][0] == "unquoted"
        and chunks[0][1].endswith("c")
    ):
        kind, raw_content = chunks[1]
        if kind in ("double", "unquoted") and INLINE_PY_SHELL_EXPAND_RE.search(raw_content):
            return (
                None,
                "Interpolation shell potentielle détectée dans `python -c` : validation humaine requise.",
            )
    else:
        return (
            None,
            "Quoting multiple ou ambigu dans le code Python inline (`-c`) : validation humaine requise.",
        )
    if not inline_tail.strip():
        return (
            None,
            "Code Python inline (`-c`) vide : validation humaine requise.",
        )
    return inline_tail, None


def _check_segment_inline_exec(
    segment: str, ws_root: Optional[Path] = None
) -> Optional[Tuple[str, str]]:
    """Inspecte les flags d'exécution inline / dynamique d'un segment unique."""
    head, rich_args, wrapped_in_sudo = _extract_command_head_args_rich(segment)
    if not head:
        return None

    args = [tok for tok, _, _ in rich_args]
    n = len(args)
    if _is_python_head(head):
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or not tok.startswith("-"):
                break
            if tok in PYTHON_VALUE_OPTS:
                i += 2
                continue
            if tok.startswith("--"):
                i += 1
                continue
            if PY_VERSION_OPT_RE.match(tok):
                i += 1
                continue
            short_flags = tok[1:]
            advance_extra = 0
            for k, ch in enumerate(short_flags):
                if ch == "c":
                    if wrapped_in_sudo:
                        return (
                            "force_ask",
                            "Exécution de code Python inline (`-c`) sous `sudo` : validation humaine requise.",
                        )
                    code, payload_err = _extract_inline_py_payload(
                        rich_args, i, k, short_flags
                    )
                    if payload_err is not None or code is None:
                        return (
                            "force_ask",
                            payload_err
                            or "Exécution de code Python inline (`-c`) détectée : validation humaine requise.",
                        )
                    py_dec, py_reason = inspect_inline_python_code(code, ws_root=ws_root)
                    if py_dec == "allow":
                        return None
                    return (
                        py_dec,
                        py_reason
                        or "Exécution de code Python inline (`-c`) détectée : validation humaine requise.",
                    )
                if ch == "m":
                    return None
                if ch in ("W", "X"):
                    if k == len(short_flags) - 1:
                        advance_extra = 1
                    break
            i += 1 + advance_extra
        return None

    if head == "node":
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or not tok.startswith("-"):
                break
            if tok in NODE_VALUE_OPTS:
                i += 2
                continue
            if (
                tok in ("-e", "-p", "--eval", "--print")
                or tok.startswith("--eval=")
                or tok.startswith("--print=")
                or (not tok.startswith("--") and (tok.startswith("-e") or tok.startswith("-p")))
                or (re.match(r"^-[A-Za-z]+$", tok) and ("e" in tok[1:] or "p" in tok[1:]))
            ):
                return (
                    "force_ask",
                    "Exécution de code inline via interpréteur (`-e`) détectée : validation humaine requise.",
                )
            i += 1
        return None

    if head == "perl":
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
                return (
                    "force_ask",
                    "Exécution de code inline via interpréteur (`-e`) détectée : validation humaine requise.",
                )
            for ch in tok[1:]:
                if ch in ("I", "M", "m", "F", "d", "D", "0", "C", "V"):
                    break
                if ch in ("e", "E"):
                    return (
                        "force_ask",
                        "Exécution de code inline via interpréteur (`-e`) détectée : validation humaine requise.",
                    )
            i += 1
        return None

    if head == "ruby":
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or not tok.startswith("-"):
                break
            if tok in RUBY_VALUE_OPTS:
                i += 2
                continue
            if tok.startswith("--"):
                i += 1
                continue
            if tok[:2] in ("-I", "-r", "-C", "-E", "-F", "-K", "-T", "-W"):
                i += 1
                continue
            if tok == "-e" or tok.startswith("-e") or (
                re.match(r"^-[A-Za-z]+$", tok) and "e" in tok[1:]
            ):
                return (
                    "force_ask",
                    "Exécution de code inline via interpréteur (`-e`) détectée : validation humaine requise.",
                )
            i += 1
        return None

    if head in {"sh", "bash", "zsh", "dash"}:
        i = 0
        while i < n:
            tok = args[i]
            if tok in ("-", "--") or (not tok.startswith("-") and not tok.startswith("+")):
                break
            if tok in ("-o", "+o", "--rcfile", "--init-file"):
                i += 2
                continue
            if tok.startswith("--") or tok.startswith("+"):
                i += 1
                continue
            if tok == "-c" or (re.match(r"^-[A-Za-z]+$", tok) and "c" in tok[1:]):
                return (
                    "force_ask",
                    "Sous-shell inline ou encodé détecté : validation humaine requise.",
                )
            i += 1
        return None

    if head in {"pwsh", "powershell"}:
        i = 0
        while i < n:
            tok = args[i]
            if tok == "--" or not tok.startswith("-"):
                break
            low = tok.lower()
            low_flag = low.split(":", 1)[0]
            if low_flag in ("-f", "-file"):
                break
            if low_flag in PS_VALUE_OPTS:
                i += 1 if ":" in low else 2
                continue
            if low_flag in {"-c", "-e", "-ec"} or (
                len(low_flag) >= 3
                and (
                    "-command".startswith(low_flag)
                    or "-encodedcommand".startswith(low_flag)
                )
            ):
                return (
                    "force_ask",
                    "Sous-shell inline ou encodé détecté : validation humaine requise.",
                )
            i += 1
        return None

    if head == "cmd":
        for tok in args:
            low = tok.lower()
            if low in ("/c", "/k") or re.match(r"^/[ck](?:[:\"'\s]|$)", low):
                return (
                    "force_ask",
                    "Sous-shell inline ou encodé détecté : validation humaine requise.",
                )
        return None

    if head == "find":
        if any(arg.lower() in {"-exec", "-execdir", "-ok", "-okdir"} for arg in args):
            return (
                "force_ask",
                "Exécution indirecte via `find -exec` détectée : validation humaine requise.",
            )
        return None

    if head == "eval":
        return (
            "force_ask",
            "Évaluation shell dynamique (`eval`) détectée : validation humaine requise.",
        )

    if head == "base64":
        if any(
            arg in ("-d", "-D", "--decode")
            or (re.match(r"^-[A-Za-z]+$", arg) and ("d" in arg[1:] or "D" in arg[1:]))
            for arg in args
        ):
            return (
                "force_ask",
                "Décodage ou pipe `base64` détecté : validation humaine requise.",
            )
        return None

    return None


def _is_destructive_rm(cmd: str, segments: Optional[List[str]] = None) -> bool:
    """Détecte `rm` récursif+forcé et `Remove-Item -Recurse -Force` sur cibles destructrices."""
    segs = segments if segments is not None else split_shell_segments(cmd)
    for seg in segs:
        head, args = extract_command_head_and_args(seg)
        if not head:
            continue

        if head == "rm":
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
            if has_r and has_f and any(_is_destructive_target(t) for t in targets):
                return True

        if head in ("remove-item", "ri", "del", "erase", "rmdir", "rd"):
            low = [t.lower() for t in args]
            has_r = any(t in ("-recurse", "-r", "/s") for t in low)
            has_f = any(t in ("-force", "-fo", "/q", "/f") for t in low)
            targets = [
                t
                for t in args
                if not t.startswith("-")
                and not (len(t) == 2 and t.startswith("/") and t[1].isalpha())
            ]
            if has_r and has_f and any(_is_destructive_target(t) for t in targets):
                return True
    return False


def _shell_targets_harness(
    cmd: str, ws_root: Optional[Path] = None, segments: Optional[List[str]] = None
) -> bool:
    """Détecte par segment une écriture, exécution ou redirection visant un fichier protégé."""
    segs = segments if segments is not None else split_shell_segments(cmd)
    for seg in segs:
        head, args = extract_command_head_and_args(seg)
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
            continue

        candidates: List[str] = []
        seen: Set[str] = set()
        for raw_tok in _tokenize_shell_segment(seg) + PATH_TOKEN_RE.findall(seg):
            for part in (raw_tok, raw_tok.strip(",:[]{}()")):
                if part and part not in seen:
                    seen.add(part)
                    candidates.append(part)
                if "=" in part:
                    rhs = part.split("=", 1)[1]
                    for sub in (rhs, rhs.strip(",:[]{}()")):
                        if sub and sub not in seen:
                            seen.add(sub)
                            candidates.append(sub)
                if "," in part:
                    for piece in part.split(","):
                        cleaned_piece = piece.strip(",:[]{}()\"'")
                        if cleaned_piece and cleaned_piece not in seen:
                            seen.add(cleaned_piece)
                            candidates.append(cleaned_piece)

        for token in candidates:
            if is_harness_protected(token, ws_root):
                return True
    return False


def _shell_targets_secret(cmd: str) -> bool:
    """Détecte les chemins normalisés (ex. foo/../.env) dans une commande shell."""
    for token in PATH_TOKEN_RE.findall(cmd):
        if matches_any(token, SENSITIVE_PATH_PATTERNS):
            return True
    return False


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
    """Inspecte un script `run_workflow` pour fuite de secrets, tamper harnais ou exec opaque."""
    if re.search(
        r"\bos\.environ\b|['\"][^'\"]*(?:\.env(?:\.[a-z0-9_]+)?|secrets/|\.ssh/|id_rsa|id_ed25519|\.pem|\.key)['\"]",
        code,
        re.IGNORECASE,
    ):
        return "deny", "Script `run_workflow` ciblant un secret ou `os.environ` bloqué."
    if re.search(
        r"['\"](?:[^'\"]*[/\\])?(?:(?:\.agents|gravity-plugin|plugins[/\\]gravity|gravity)[/\\])?hooks\.json['\"]"
        r"|['\"](?:[^'\"]*[/\\])?(?:\.agents[/\\]gravity|gravity-plugin|plugins[/\\]gravity|gravity)[/\\]hooks(?:[/\\][^'\"]*)?['\"]"
        r"|['\"][^'\"]*01-safety(?:-boundaries)?\.md['\"]",
        code,
        re.IGNORECASE,
    ):
        return "deny", "Script `run_workflow` ciblant les garde-fous Gravity bloqué."

    flags = getattr(ast, "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0)
    try:
        tree = (
            ast.parse(code, mode="exec", type_comments=False)
            if not flags
            else compile(code, "<workflow>", "exec", flags=ast.PyCF_ONLY_AST | flags)
        )
    except SyntaxError:
        return "force_ask", "Script `run_workflow` non analysable par AST : validation humaine requise."

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if is_harness_protected(node.value, ws_root):
                return "deny", "Script `run_workflow` ciblant les garde-fous Gravity bloqué."
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top_mod = alias.name.split(".")[0]
                if top_mod in ("subprocess", "shutil", "ctypes", "importlib"):
                    return "force_ask", f"Import `{top_mod}` dans `run_workflow` : validation humaine requise."
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_mod = node.module.split(".")[0]
            if top_mod in ("subprocess", "shutil", "ctypes", "importlib"):
                return "force_ask", f"Import `{top_mod}` dans `run_workflow` : validation humaine requise."
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                if fn.id in ("eval", "exec", "getenv", "__import__"):
                    return "force_ask", f"Appel dynamique `{fn.id}()` dans `run_workflow` : validation humaine requise."
                if (
                    fn.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                ):
                    attr_name = node.args[1].value
                    if attr_name == "environ":
                        return "deny", "Accès dynamique à `environ` dans `run_workflow` bloqué."
                    if attr_name in (
                        "system",
                        "popen",
                        "execv",
                        "execl",
                        "execve",
                        "spawnv",
                        "rmtree",
                        "unlink",
                        "chmod",
                        "getenv",
                        "remove",
                        "replace",
                        "rmdir",
                        "removedirs",
                    ):
                        return (
                            "force_ask",
                            f"Accès dynamique `getattr(..., {attr_name!r})` dans `run_workflow` : validation humaine requise.",
                        )
            if isinstance(fn, ast.Attribute):
                if fn.attr in (
                    "system",
                    "popen",
                    "execv",
                    "execl",
                    "execve",
                    "spawnv",
                    "rmtree",
                    "unlink",
                    "chmod",
                    "getenv",
                ):
                    return "force_ask", f"Appel système `.{fn.attr}()` dans `run_workflow` : validation humaine requise."
                if (
                    isinstance(fn.value, ast.Name)
                    and fn.value.id == "os"
                    and fn.attr in ("remove", "replace", "rmdir", "removedirs")
                ):
                    return "force_ask", f"Appel `os.{fn.attr}()` dans `run_workflow` : validation humaine requise."
    return None


def evaluate_tool_call(
    data: dict, workspace_root: Optional[Path] = None
) -> Tuple[str, Optional[str]]:
    """Évalue un appel d'outil PreToolUse et retourne `(decision, reason)`."""
    ws_root = workspace_root if workspace_root is not None else HOOKS_DIR.parent
    tool = data.get("toolCall")
    if not isinstance(tool, dict):
        return "force_ask", "Champ 'toolCall' absent ou invalide (fail-closed)."

    name = str(tool.get("name", ""))
    args = tool.get("args") or {}
    if not isinstance(args, dict):
        return "force_ask", f"Arguments invalides pour l'outil '{name}' (fail-closed)."

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
                    return verdict
            else:
                return (
                    "force_ask",
                    f"Script `run_workflow` introuvable pour inspection statique ({script_path}) : validation humaine requise.",
                )

        inline_script = first_str_arg(args, ("Script", "script"))
        if inline_script:
            verdict = inspect_workflow_code(inline_script, ws_root=ws_root)
            if verdict:
                return verdict

    elif name in SHELL_TOOLS:
        cmd = first_str_arg(args, SHELL_CMD_KEYS)
        segments = split_shell_segments(cmd)
        if _is_destructive_rm(cmd, segments=segments):
            return "deny", "Suppression récursive destructrice bloquée."
        if any(_is_git_force_push(seg) for seg in segments):
            return "deny", "Force-push Git (-f / --force / +ref) bloqué."
        for pattern, message in DANGEROUS_CMDS:
            if pattern.search(cmd) or (
                pattern is ENV_DUMP_PATTERN and any(pattern.search(seg) for seg in segments)
            ):
                return "deny", message
        if _shell_targets_harness(cmd, ws_root, segments=segments):
            return "deny", "Tentative de modification des garde-fous Gravity via chemin relatif bloquée."
        if _shell_targets_secret(cmd):
            return "deny", "Accès shell à un fichier sensible via chemin relatif bloqué."
        for seg in segments:
            ask_res = _check_segment_inline_exec(seg, ws_root=ws_root)
            if ask_res:
                return ask_res
        for pattern, message in OPAQUE_ASK_CMDS:
            if pattern.search(cmd):
                return "force_ask", message

    return "allow", None
