#!/usr/bin/env python3
"""Politique de sécurité pure pour PreToolUse (100 % stdlib, sans I/O sur stdout)."""

import ast
import posixpath
import re
import shlex
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

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

DANGEROUS_CMDS = [
    (
        re.compile(
            r"\bgit(?:\.exe)?\b(?:\s+(?:(?:-[Cc]\s+\S+)|(?:--(?:git-dir|work-tree|namespace|config-env)(?:=|\s+)\S+)|(?:-[A-Za-z0-9]+)|(?:--[a-z0-9-]+(?:=\S+)?)))*\s+push\b[^\n]*?(?:(?:\s|^)(?:-f|--force)(?:\s|$)|(?:\s|^)\+\S+)",
            re.IGNORECASE,
        ),
        "Force-push Git (-f / --force / +ref) bloqué.",
    ),
    (
        re.compile(
            r"\b(?:drop\s+(?:database|schema|table)|truncate\s+table|flushall|flushdb)\b",
            re.IGNORECASE,
        ),
        "Suppression massive de base de données bloquée.",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:printenv\b|env\s*(?:$|[;&|>])|export\s+-p\b|declare\s+-x\b|"
            r"(?:Get-ChildItem|gci|ls|dir|Get-Item|gi|Get-Content|gc|cat)\s+Env:|Get-Variable\b)|"
            r"\[(?:System\.)?Environment\]::GetEnvironmentVariables\b",
            re.IGNORECASE,
        ),
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
            r"(?:\b(?:rm|mv|cp|chmod|chown|truncate|tee|(?:python3?|py|perl|ruby|node)(?:\.exe)?|Remove-Item|ri|del|rd|Set-Content|Out-File|Add-Content|ac|sc|Clear-Content|clc|Move-Item|mi|Copy-Item|cpi|copy|Rename-Item|rni|New-Item|ni)\b|\bsed\s+-i\b|>{1,2})"
            r"[^\n]*?(?:(?:^|[\s\"'=:(])hooks\.json(?:$|[\s\"',;)])|(?:\.agents|gravity-plugin|plugins[/\\]gravity|gravity)(?:[/\\]|\./)+hooks\.json|(?:^|[\s\"'=:(/\\])(?:\.agents[/\\]gravity|gravity-plugin|plugins[/\\]gravity|gravity)(?:[/\\]|\./)+hooks(?:[/\\]|$)|01-safety(?:-boundaries)?\.md)",
            re.IGNORECASE,
        ),
        "Tentative de modification/suppression des garde-fous Gravity bloquée (auto-protection).",
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
        re.compile(r"\bfind\b[^\n]*?\s-(?:exec|execdir|ok)\b", re.IGNORECASE),
        "Exécution indirecte via `find -exec` détectée : validation humaine requise.",
    ),
    (
        re.compile(r"\bxargs\b", re.IGNORECASE),
        "Exécution dynamique via `xargs` détectée : validation humaine requise.",
    ),
    (
        re.compile(
            r"\bbase64\b[^\n]*?(?:-d|--decode)\b|\|\s*base64\b|FromBase64String",
            re.IGNORECASE,
        ),
        "Décodage ou pipe `base64` détecté : validation humaine requise.",
    ),
    (
        re.compile(r"(?:^|[;&|]\s*)eval\b", re.IGNORECASE),
        "Évaluation shell dynamique (`eval`) détectée : validation humaine requise.",
    ),
    (
        re.compile(
            r"\b(?:sh|bash|zsh|dash|pwsh|powershell|cmd)(?:\.exe)?\b[^\n]*?\s+(?:-c|-Command|/c|-(?:EncodedCommand|enc))\b",
            re.IGNORECASE,
        ),
        "Sous-shell inline ou encodé détecté : validation humaine requise.",
    ),
    (
        re.compile(r"\b(?:iex|Invoke-Expression)\b", re.IGNORECASE),
        "Évaluation PowerShell dynamique (`iex`/`Invoke-Expression`) détectée : validation humaine requise.",
    ),
    (
        re.compile(r"\b(?:python3?|py)(?:\.exe)?\b[^\n]*?\s+-c\b", re.IGNORECASE),
        "Exécution de code Python inline (`-c`) détectée : validation humaine requise.",
    ),
    (
        re.compile(r"\b(?:node|perl|ruby)(?:\.exe)?\b[^\n]*?\s+-e\b", re.IGNORECASE),
        "Exécution de code inline via interpréteur (`-e`) détectée : validation humaine requise.",
    ),
    (
        re.compile(r"(?:^|[\s/\"'=:(])\.[a-z0-9_]*[*?]", re.IGNORECASE),
        "Globbing sur fichier caché (ex. `.e*`) détecté : validation humaine requise.",
    ),
]


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


def _is_destructive_rm(cmd: str) -> bool:
    """Détecte `rm` récursif+forcé et `Remove-Item -Recurse -Force` sur cibles destructrices."""
    for segment in re.split(r"[;&|\n]+", cmd):
        seg = segment.strip()
        if not seg:
            continue
        seg_norm = seg.replace("\\", "/")
        try:
            tokens = shlex.split(seg_norm, posix=True)
        except ValueError:
            tokens = seg_norm.split()
        if not tokens:
            continue
        head = Path(tokens[0]).name.lower()
        if head == "sudo" and len(tokens) > 1:
            tokens = tokens[1:]
            head = Path(tokens[0]).name.lower()
        if head.endswith(".exe"):
            head = head[:-4]

        if head == "rm":
            has_r, has_f = False, False
            targets: List[str] = []
            for tok in tokens[1:]:
                if tok.startswith("--"):
                    if tok == "--recursive":
                        has_r = True
                    elif tok == "--force":
                        has_f = True
                elif tok.startswith("-") and len(tok) > 1:
                    flags = tok[1:].lower()
                    if "r" in flags:
                        has_r = True
                    if "f" in flags:
                        has_f = True
                else:
                    targets.append(tok)
            if has_r and has_f and any(_is_destructive_target(t) for t in targets):
                return True

        if head in ("remove-item", "ri", "del", "erase", "rmdir", "rd"):
            low = [t.lower() for t in tokens[1:]]
            has_r = any(t in ("-recurse", "-r", "/s") for t in low)
            has_f = any(t in ("-force", "-fo", "/q", "/f") for t in low)
            targets = [
                t
                for t in tokens[1:]
                if not t.startswith("-")
                and not (len(t) == 2 and t.startswith("/") and t[1].isalpha())
            ]
            if has_r and has_f and any(_is_destructive_target(t) for t in targets):
                return True
    return False


def _shell_targets_harness(cmd: str, ws_root: Optional[Path] = None) -> bool:
    """Détecte les chemins normalisés vers les fichiers protégés dans une commande shell."""
    write_hint = re.search(
        r"\b(?:rm|mv|cp|chmod|chown|truncate|tee|sed|(?:python3?|py|perl|ruby|node)(?:\.exe)?|Remove-Item|ri|del|rd|Set-Content|Out-File|Add-Content|ac|sc|Clear-Content|clc|Move-Item|mi|Copy-Item|cpi|copy|Rename-Item|rni|New-Item|ni)\b|>{1,2}",
        cmd,
        re.IGNORECASE,
    )
    if not write_hint:
        return False
    for token in PATH_TOKEN_RE.findall(cmd):
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
        if _is_destructive_rm(cmd):
            return "deny", "Suppression récursive destructrice bloquée."
        for pattern, message in DANGEROUS_CMDS:
            if pattern.search(cmd):
                return "deny", message
        if _shell_targets_harness(cmd, ws_root):
            return "deny", "Tentative de modification des garde-fous Gravity via chemin relatif bloquée."
        if _shell_targets_secret(cmd):
            return "deny", "Accès shell à un fichier sensible via chemin relatif bloqué."
        for pattern, message in OPAQUE_ASK_CMDS:
            if pattern.search(cmd):
                return "force_ask", message

    return "allow", None
