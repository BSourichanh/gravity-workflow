#!/usr/bin/env python3
"""Tables statiques, expressions régulières et motifs de sécurité PreToolUse."""

import re
from typing import Set

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

# Auto-protection ciblée sur le plugin actif/installé et le socle projet (jamais le dépôt source plugin/).
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
