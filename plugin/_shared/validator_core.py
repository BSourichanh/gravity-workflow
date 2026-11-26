"""Unified strict plugin validator engine for Gravity plugins (Python 3.10+ stdlib)."""

from __future__ import annotations

import argparse, ast, json, os, re, shlex, shutil, subprocess, sys, tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterable, Optional, Sequence, Union

from .atomic_io import write_json_atomic
from .hook_runtime import configure_utf8_stdio
from .path_safety import is_symlink_or_junction, is_within_root, safe_home_dir

UTF8_BOM = b"\xef\xbb\xbf"
OFFICIAL_PLUGIN_FIELDS = frozenset({"name", "displayName", "version", "description", "logo", "suggestedPrompts", "author", "homepage", "repository", "disabled"})
REQUIRED_PLUGIN_FIELDS = OFFICIAL_PLUGIN_FIELDS - {"disabled"}
REQUIRED_MODULE_FIELDS = frozenset({"schema_version", "id", "plugin_name", "role", "storage", "commands", "capabilities", "ui", "signals"})
ALLOWED_LOCAL_MODULES = frozenset({
    "_shared", "dashboard_provider", "core", "dashboard", "mcp", "registry", "init_env", "validate",
    "metrics", "bridge_links", "hook_runtime", "cli", "sync_tracking", "init_workspace", "io_utils",
    "discovery", "git_collector", "watches", "delta", "renderers", "validators",
})
FLAT_HOOK_EVENTS: tuple[str, ...] = ("SessionStart", "PreInvocation", "PostInvocation", "Stop")
GROUPED_HOOK_EVENTS: tuple[str, ...] = ("PreToolUse", "PostToolUse")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SVG_VIEWBOX_RE = re.compile(r"""viewBox\s*=\s*["']0\s+0\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)["']""")
FORBIDDEN_MOD_RE = re.compile(r"\bMod" + r"ule\s+0?\d\b", re.IGNORECASE)
SECRET_LEAK_RE = re.compile(r"(?:sk-" + r"live-|ghp_[A-Za-z0-9]{20,}|xox[baprs]-|AKIA[0-9A-Z]{16})")
PERSONAL_PATH_RE = re.compile(r"(?:[A-Za-z]:\\(?:Users|Documents)\\|[A-Za-z]:\\[^\\\s\"'`]+\\Documents\\|/Us" + r"ers/[^/\s\"'`]+/|/ho" + r"me/[^/\s\"'`]+/)", re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\n]+)\)")
MD_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)([^`\n]*?)\1")
IGNORED_LINK_PREFIXES = ("#", "http://", "https://", "mailto:", "customization://")
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")


class Report:
    """Collector for validation errors, warnings, and info messages."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def error(self, msg: str) -> None: self.errors.append(msg)
    def warn(self, msg: str) -> None: self.warnings.append(msg)
    def info(self, msg: str) -> None: self.infos.append(msg)
    def ok(self, strict: bool = False) -> bool: return not self.errors and (not strict or not self.warnings)
    def to_dict(self, strict: bool = False) -> dict[str, Any]: return {"ok": self.ok(strict), "strict": bool(strict), "errors": list(self.errors), "warnings": list(self.warnings), "infos": list(self.infos)}
    def to_json(self, strict: bool = False, *, indent: int = 2) -> str: return json.dumps(self.to_dict(strict), indent=indent, ensure_ascii=False)


@dataclass
class ValidatorConfig:
    """Configuration for unified Gravity plugin validation."""

    plugin_root: Path
    strict: bool = False
    fix_python_cmd: bool = False
    json_output: bool = False
    expected_plugin_name: Optional[str] = None
    expected_module_id: Optional[str] = None
    required_dirs: tuple[str, ...] = ()
    allowed_local_modules: frozenset[str] = ALLOWED_LOCAL_MODULES
    extra_forbidden_patterns: tuple[Any, ...] = ()
    require_module_contract: bool = True
    require_hooks_config: bool = False
    require_mcp_config: bool = False
    require_rules_agents: bool = True
    rules_warn_bytes: int = 600
    rules_max_bytes: int = 1000
    skill_max_bytes: int = 6000
    repo_agents_max_bytes: int = 2500
    is_portable_template: bool = False
    check_links: bool = True
    required_pretool_matchers: tuple[str, ...] = ()
    extra_check_fn: Optional[Callable[[Path, Report], None]] = None
    workspace_validator_fn: Optional[Callable[[Path, Report], None]] = None


def _safe_home_str() -> str:
    hp = safe_home_dir()
    return hp.as_posix() if (os.sep == "/" and isinstance(hp, PureWindowsPath)) else os.fspath(hp)

def _is_nonempty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())

def has_real_python3() -> bool:
    """Return True if a real 'python3' binary exists on PATH (ignoring WindowsApps stub)."""
    found = shutil.which("python3")
    return bool(found) and not (os.name == "nt" and "windowsapps" in found.lower())

def _detect_portable_python_cmd() -> str:
    py_bin = shutil.which("python")
    if py_bin and not (os.name == "nt" and "windowsapps" in py_bin.lower()):
        return "python"
    return "py -3" if shutil.which("py") else "python"

def _rewrite_python3_cmd(cmd: str, replacement: str) -> str:
    return replacement if cmd == "python3" else (f"{replacement} {cmd[len('python3 '):]}" if cmd.startswith("python3 ") else cmd)

def _resolve_plugin_file(root: Path, raw_rel: str) -> Optional[Path]:
    rel = raw_rel.replace("${PLUGIN_ROOT}/", "").replace("${PLUGIN_ROOT}\\", "").lstrip("/\\")
    if not rel:
        return None
    cand = root / rel
    res = cand.resolve()
    return None if (is_symlink_or_junction(cand) or not is_within_root(root, res) or not res.is_file()) else res


def check_utf8_no_bom_lf(
    path: Union[str, Path], label: str, report: Report, *, max_bytes: Optional[int] = None, warn_bytes: Optional[int] = None,
) -> Optional[bytes]:
    """Validate UTF-8 without BOM, LF line endings, and optional byte budgets."""
    if is_symlink_or_junction(p := Path(path)):
        report.error(f"{label} ne doit pas être un lien symbolique ou une jonction.")
        return None
    try:
        raw = p.read_bytes()
    except OSError as exc:
        report.error(f"{label} illisible : {exc}")
        return None
    payload = raw[len(UTF8_BOM) :] if raw.startswith(UTF8_BOM) else raw
    if raw.startswith(UTF8_BOM):
        report.error(f"{label} : BOM UTF-8 interdit (encoder en UTF-8 sans BOM).")
    if b"\r\n" in raw or b"\r" in raw:
        report.error(f"{label} : fins de ligne LF requises (CRLF/CR détecté).")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        report.error(f"{label} : encodage UTF-8 invalide ({exc}).")
    if max_bytes is not None and len(raw) > max_bytes:
        report.error(f"{label} dépasse {max_bytes} octets ({len(raw)} octets).")
    elif warn_bytes is not None and len(raw) > warn_bytes:
        report.warn(f"{label} dépasse la cible éditoriale de {warn_bytes} octets ({len(raw)} octets).")
    return payload

def _read_json_checked(path: Path, label: str, report: Report) -> Optional[dict[str, Any]]:
    if (payload := check_utf8_no_bom_lf(path, label, report)) is None:
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        report.error(f"{label} invalide : {exc}")
        return None
    if not isinstance(data, dict):
        report.error(f"{label} doit contenir un objet JSON.")
        return None
    return data

def check_stdlib_only(py_file: Union[str, Path], report: Report, *, allowed_local_modules: Optional[Iterable[str]] = None) -> None:
    """Fail-closed AST check enforcing 100% Python 3.10+ stdlib + allowed local modules."""
    if not (stdlib_raw := getattr(sys, "stdlib_module_names", None)):
        report.error("Python 3.10+ requis pour la validation stdlib (sys.stdlib_module_names indisponible).")
        return
    if is_symlink_or_junction(p := Path(py_file)):
        report.error(f"Lien symbolique ou jonction interdit : {p}")
        return
    allowed = set(stdlib_raw) | (set(ALLOWED_LOCAL_MODULES) if allowed_local_modules is None else set(allowed_local_modules))
    try:
        tree = compile(p.read_text(encoding="utf-8"), str(p), "exec", flags=ast.PyCF_ONLY_AST | getattr(ast, "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        report.error(f"Analyse Python impossible pour {p} : {exc}")
        return
    for node in ast.walk(tree):
        mods = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else ([node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else [])
        for mod in mods:
            if mod not in allowed:
                report.error(f"Import non stdlib {mod!r} détecté dans {p}")

def fix_python_cmd(plugin_root: Union[str, Path]) -> dict[str, Any]:
    """Atomically adapt 'python3' to 'python' or 'py -3' in hooks.json and mcp_config.json."""
    root = Path(plugin_root)
    if has_real_python3():
        return {"ok": True, "python_cmd": "python3", "changed": False, "fixed_files": [], "skipped_real_python3": True}
    py_cmd, fixed_files = _detect_portable_python_cmd(), []
    def _read_s(path: Path) -> Any:
        raw = path.read_bytes()
        return json.loads((raw[len(UTF8_BOM) :] if raw.startswith(UTF8_BOM) else raw).decode("utf-8"))
    def _rewrite_h(h: Any) -> bool:
        if isinstance(h, dict) and isinstance(h.get("command"), str) and (nc := _rewrite_python3_cmd(h["command"], py_cmd)) != h["command"]:
            h["command"] = nc
            return True
        return False
    if (hooks_json := root / "hooks.json").is_file():
        data, changed = _read_s(hooks_json), False
        for gcfg in data.values() if isinstance(data, dict) else ():
            for ev, val in gcfg.items() if isinstance(gcfg, dict) else ():
                if isinstance(val, list) and (ev in GROUPED_HOOK_EVENTS or ev in FLAT_HOOK_EVENTS):
                    for h in ([x for e in val if isinstance(e, dict) for x in (e.get("hooks") or ())] if ev in GROUPED_HOOK_EVENTS else val):
                        changed = _rewrite_h(h) or changed
        if changed:
            write_json_atomic(hooks_json, data, indent=2, ensure_ascii=False)
            fixed_files.append("hooks.json")
    if (mcp_json := root / "mcp_config.json").is_file():
        mdata, mchanged = _read_s(mcp_json), False
        for scfg in (mdata.get("mcpServers") or {}).values() if isinstance(mdata, dict) and isinstance(mdata.get("mcpServers"), dict) else ():
            if isinstance(scfg, dict) and scfg.get("command") == "python3":
                args = list(scfg.get("args") or [])
                scfg["command"] = "py" if py_cmd == "py -3" else py_cmd
                if py_cmd == "py -3" and (not args or args[0] != "-3"):
                    scfg["args"] = ["-3", *args]
                mchanged = True
        if mchanged:
            write_json_atomic(mcp_json, mdata, indent=2, ensure_ascii=False)
            fixed_files.append("mcp_config.json")
    return {"ok": True, "python_cmd": py_cmd, "changed": bool(fixed_files), "fixed_files": fixed_files, "skipped_real_python3": False}


def validate_plugin_manifest(root: Path, report: Report, *, expected_name: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Validate official 9-field Antigravity plugin.json (+ optional boolean disabled)."""
    if not (mpath := root / "plugin.json").is_file():
        report.error("plugin.json introuvable à la racine du plugin.")
        return None
    if (manifest := _read_json_checked(mpath, "plugin.json", report)) is None:
        return None
    if (unknown := sorted(set(manifest) - OFFICIAL_PLUGIN_FIELDS)):
        report.error(f"plugin.json : champs non officiels détectés : {unknown}")
    if (missing := sorted(REQUIRED_PLUGIN_FIELDS - set(manifest))):
        report.error(f"plugin.json : champs requis manquants : {missing}")
    if "disabled" in manifest and not isinstance(manifest["disabled"], bool):
        report.error("plugin.json : 'disabled' doit être un booléen.")
    name, ver, desc = manifest.get("name"), manifest.get("version"), manifest.get("description")
    author, homepage, repo = manifest.get("author"), manifest.get("homepage"), manifest.get("repository")
    checks = (
        (isinstance(name, str) and bool(KEBAB_RE.match(name)) and (not expected_name or name == expected_name), f"plugin.json : 'name' invalide ({name!r})."),
        (_is_nonempty_str(manifest.get("displayName")), "plugin.json : 'displayName' doit être une chaîne non vide."),
        (isinstance(ver, str) and bool(SEMVER_RE.match(ver.strip())), f"plugin.json : 'version' ({ver!r}) doit respecter SemVer."),
        (isinstance(desc, str) and bool(desc.strip()) and len(desc) <= 160, "plugin.json : 'description' vide ou > 160 caractères."),
        (_is_nonempty_str(author) or (isinstance(author, dict) and _is_nonempty_str(author.get("name"))), "plugin.json : 'author' invalide."),
        (isinstance(homepage, str) and homepage.strip().startswith(("http://", "https://")), "plugin.json : 'homepage' doit être une URL http(s)."),
        (_is_nonempty_str(repo) or (isinstance(repo, dict) and _is_nonempty_str(repo.get("url"))), "plugin.json : 'repository' invalide."),
    )
    for ok, msg in checks:
        if not ok:
            report.error(msg)
    prompts = manifest.get("suggestedPrompts")
    if not isinstance(prompts, list) or len(prompts) != 3 or not all(_is_nonempty_str(p) for p in prompts):
        report.error("plugin.json : exactement 3 'suggestedPrompts' non vides sont requis.")
    else:
        for idx, prompt in enumerate(prompts):
            if len(prompt) > 100:
                report.error(f"plugin.json : 'suggestedPrompts[{idx}]' > 100 caractères ({len(prompt)}).")
            st = prompt.lstrip()
            if st.startswith("/") and ((sep := " — " if " — " in st else (" - " if " - " in st else None)) is None or not st.split(sep, 1)[1].strip()):
                report.error(f"plugin.json : 'suggestedPrompts[{idx}]' doit suivre '/cmd — résumé'.")
    logo_rel = manifest.get("logo")
    if (logo_path := _resolve_plugin_file(root, logo_rel) if _is_nonempty_str(logo_rel) else None) is None:
        report.error(f"Logo introuvable ou hors racine : {logo_rel!r}")
    elif logo_path.suffix.lower() == ".svg":
        vb = SVG_VIEWBOX_RE.search(logo_path.read_text(encoding="utf-8", errors="replace"))
        if not vb or float(vb.group(1)) <= 0 or float(vb.group(1)) != float(vb.group(2)):
            report.error(f"Logo {logo_rel} : viewBox carré 1:1 non détecté.")
    return manifest


def _check_str_list(
    val: Any, label: str, report: Report, *, allow_empty: bool = False, prefix: str = "", pattern: Optional[re.Pattern[str]] = None, allow_star: bool = False,
) -> None:
    def _ok(item: Any) -> bool:
        if not _is_nonempty_str(item):
            return False
        if allow_star and item == "*":
            return True
        return (not prefix or item.startswith(prefix)) and (pattern is None or bool(pattern.match(item)))

    if not (isinstance(val, list) and (allow_empty or bool(val)) and all(_ok(x) for x in val)):
        report.error(f"config/module.json : '{label}' invalide.")


def _validate_spi_blocks(data: dict[str, Any], report: Report) -> None:
    """Validate optional SPI-1 (dashboard) and SPI-2 (capture_policy) blocks in module.json."""
    if "dashboard" in data:
        dash = data["dashboard"]
        if not isinstance(dash, dict):
            report.error("config/module.json : 'dashboard' doit être un objet.")
        else:
            ps = dash.get("provider_script")
            if not _is_nonempty_str(ps) or "\x00" in ps or ps.startswith(("/", "\\")) or ".." in ps.replace("\\", "/").split("/"):
                report.error("config/module.json : 'dashboard.provider_script' invalide.")
            _check_str_list(dash.get("views"), "dashboard.views", report, allow_empty=True)
            _check_str_list(dash.get("actions"), "dashboard.actions", report, allow_empty=True)
    if "capture_policy" in data:
        cp = data["capture_policy"]
        if not isinstance(cp, dict):
            report.error("config/module.json : 'capture_policy' doit être un objet.")
        else:
            _check_str_list(cp.get("commands"), "capture_policy.commands", report, allow_empty=True)
            _check_str_list(cp.get("exclude_subcommands"), "capture_policy.exclude_subcommands", report, allow_empty=True)
            if not isinstance(cp.get("consultative_bypass"), bool):
                report.error("config/module.json : 'capture_policy.consultative_bypass' doit être un booléen.")
            if not _is_nonempty_str(cp.get("label_prefix")):
                report.error("config/module.json : 'capture_policy.label_prefix' doit être une chaîne non vide.")


def validate_module_contract(
    target: Union[Path, str, dict[str, Any]], report: Report, *, expected_plugin_name: Optional[str] = None, expected_module_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Validate config/module.json v1 contract including SPI-1..3 schemas."""
    if isinstance(target, dict):
        data = target
    else:
        mpath = Path(target) / "config" / "module.json" if Path(target).is_dir() else Path(target)
        if not mpath.is_file():
            report.error("config/module.json introuvable.")
            return None
        if (data := _read_json_checked(mpath, "config/module.json", report)) is None:
            return None
    if (missing := sorted(REQUIRED_MODULE_FIELDS - set(data))):
        report.error(f"config/module.json : champs requis manquants : {missing}")
    sv, mod_id, pname, st, ui = data.get("schema_version"), data.get("id"), data.get("plugin_name"), data.get("storage"), data.get("ui")
    for ok, msg in (
        (type(sv) is int and sv == 1, f"config/module.json : 'schema_version' doit valoir 1 (obtenu {sv!r})."),
        (isinstance(mod_id, str) and bool(MODULE_ID_RE.match(mod_id)) and (not expected_module_id or mod_id == expected_module_id), f"config/module.json : 'id' invalide ({mod_id!r})."),
        (_is_nonempty_str(pname) and (not expected_plugin_name or pname == expected_plugin_name), f"config/module.json : 'plugin_name' invalide ({pname!r})."),
        (_is_nonempty_str(data.get("role")), "config/module.json : 'role' doit être une chaîne non vide."),
        (isinstance(st, dict) and isinstance(st.get("subdir"), str) and bool(MODULE_ID_RE.match(st["subdir"])) and _is_nonempty_str(st.get("env_override")), "config/module.json : 'storage' (subdir, env_override) invalide."),
        (isinstance(ui, dict) and _is_nonempty_str(ui.get("mode")) and _is_nonempty_str(ui.get("target")), "config/module.json : 'ui' (mode, target) invalide."),
    ):
        if not ok:
            report.error(msg)
    _check_str_list(data.get("commands"), "commands", report, prefix="/")
    _check_str_list(data.get("capabilities"), "capabilities", report)
    sig = data.get("signals")
    if not isinstance(sig, dict):
        report.error("config/module.json : 'signals' doit être un objet.")
    else:
        _check_str_list(sig.get("produces"), "signals.produces", report, allow_empty=True, pattern=KEBAB_RE)
        _check_str_list(sig.get("consumes"), "signals.consumes", report, allow_empty=True, pattern=KEBAB_RE, allow_star=True)
    _validate_spi_blocks(data, report)
    return data


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter (scalars, multiline >/| blocks, and lists)."""
    if not text.startswith("---") or len(parts := text.split("---", 2)) < 3:
        return {}, text
    meta, lines, i, list_key = {}, parts[1].splitlines(), 0, ""
    while i < len(lines):
        raw, st = lines[i], lines[i].strip()
        if st and not st.startswith("#"):
            if st.startswith("- ") and list_key:
                meta.setdefault(list_key, []).append(st[2:].strip().strip("\"'"))
            else:
                list_key = ""
                if ":" in st and not raw[:1].isspace():
                    key, val = (x.strip() for x in st.split(":", 1))
                    if val in (">", ">-", "|", "|-"):
                        block, fold, i = [], val.startswith(">"), i + 1
                        while i < len(lines) and (lines[i].strip() == "" or lines[i][:1].isspace()):
                            block.append("" if lines[i].strip() == "" else (lines[i].strip() if fold else lines[i].lstrip()))
                            i += 1
                        meta[key] = (" ".join(x for x in block if x) if fold else "\n".join(block)).strip()
                        continue
                    if val == "":
                        meta[key], list_key = [], key
                    else:
                        meta[key] = [x.strip().strip("\"'") for x in val[1:-1].split(",") if x.strip()] if (val.startswith("[") and val.endswith("]")) else val.strip("\"'")
        i += 1
    return meta, parts[2]

def validate_skills(root: Path, report: Report, *, max_bytes: int = 6000) -> None:
    """Validate skills/*/SKILL.md UTF-8 no-BOM LF, budget, frontmatter name & description <= 160c."""
    skills_dir = root / "skills"
    skill_dirs = [p for p in sorted(skills_dir.iterdir()) if p.is_dir()] if skills_dir.is_dir() else []
    if not skill_dirs:
        report.error("Dossier requis manquant ou vide : skills/")
        return
    for sdir in skill_dirs:
        skill_md, rel = sdir / "SKILL.md", f"skills/{sdir.name}/SKILL.md"
        if not skill_md.is_file():
            report.error(f"{rel} introuvable.")
            continue
        if (payload := check_utf8_no_bom_lf(skill_md, rel, report, max_bytes=max_bytes)) is None:
            continue
        txt = payload.decode("utf-8", errors="replace").lstrip()
        if not (meta := parse_frontmatter(txt)[0] if txt.startswith("---") else {}):
            report.error(f"{rel} : frontmatter YAML manquant ou invalide.")
            continue
        if meta.get("name") != sdir.name:
            report.error(f"{rel} : frontmatter 'name' doit être {sdir.name!r} (obtenu {meta.get('name')!r}).")
        desc = meta.get("description")
        if not isinstance(desc, str) or not desc.strip() or len(desc) > 160:
            report.error(f"{rel} : 'description' manquante, vide ou > 160 caractères ({len(desc) if isinstance(desc, str) else 0}).")


def _check_hook_handler(h: Any, label: str, base_root: Path, report: Report, *, require_flat: bool = False) -> None:
    if not isinstance(h, dict) or (require_flat and "hooks" in h):
        report.error(f"{label} : handler plat invalide.")
        return
    if "enabled" in h and not isinstance(h["enabled"], bool):
        report.error(f"{label} : 'enabled' doit être un booléen.")
    if not _is_nonempty_str(cmd := h.get("command")):
        report.error(f"{label} : 'command' doit être une chaîne non vide.")
        return
    try:
        tokens = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError:
        tokens = cmd.split()
    for py_rel in (tok.strip("\"'") for tok in tokens if tok.strip("\"'").endswith(".py")):
        if _resolve_plugin_file(base_root, py_rel) is None:
            report.error(f"{label} : script introuvable {py_rel!r}")


def validate_hooks_config(
    hooks_json: Path, report: Report, *, plugin_root: Optional[Path] = None, is_portable_template: bool = False, required_pretool_matchers: Sequence[str] = (),
) -> None:
    """Validate hooks.json (grouped/flat events, boolean 'enabled' incl. false, script paths)."""
    if not hooks_json.is_file():
        report.error(f"{hooks_json.name} introuvable.")
        return
    if not (data := _read_json_checked(hooks_json, hooks_json.name, report)):
        if data is not None:
            report.error(f"{hooks_json.name} doit être un objet JSON non vide.")
        return
    base_root, pre_matchers = plugin_root or hooks_json.parent, []
    for gname, gcfg in data.items():
        if not isinstance(gcfg, dict):
            report.error(f"{hooks_json.name} ({gname}) : groupe invalide.")
            continue
        if "enabled" in gcfg and not isinstance(gcfg["enabled"], bool):
            report.error(f"{hooks_json.name} ({gname}) : 'enabled' doit être un booléen.")
        for ev, val in gcfg.items():
            if ev not in GROUPED_HOOK_EVENTS and ev not in FLAT_HOOK_EVENTS:
                continue
            if not isinstance(val, list):
                report.error(f"{hooks_json.name} ({gname}.{ev}) doit être un tableau.")
                continue
            for item in val:
                lbl = f"{hooks_json.name} ({gname}.{ev})"
                if ev in FLAT_HOOK_EVENTS:
                    _check_hook_handler(item, lbl, base_root, report, require_flat=True)
                elif not isinstance(item, dict) or not isinstance(item.get("hooks"), list):
                    report.error(f"{lbl} doit utiliser le wrapper 'hooks: [...]'.")
                else:
                    if "enabled" in item and not isinstance(item["enabled"], bool):
                        report.error(f"{lbl} : 'enabled' doit être un booléen.")
                    if ev == "PreToolUse":
                        pre_matchers.append(str(item.get("matcher", "")))
                    for h in item["hooks"]:
                        _check_hook_handler(h, lbl, base_root, report)
    for req_tool in required_pretool_matchers:
        if req_tool not in "|".join(pre_matchers):
            report.warn(f"{hooks_json.name} : outil '{req_tool}' absent du matcher PreToolUse.")
    raw_txt = hooks_json.read_text(encoding="utf-8", errors="replace")
    if os.name == "nt" and ('"python3 ' in raw_txt or '"python3"' in raw_txt) and not has_real_python3():
        (report.info if is_portable_template else report.warn)(f"{hooks_json.name} portable ('python3') détecté sous Windows.")

def validate_mcp_config(root: Path, report: Report) -> None:
    """Validate mcp_config.json UTF-8 no-BOM LF, mcpServers structure, and script targets."""
    if not (mcp_json := root / "mcp_config.json").is_file():
        report.error("mcp_config.json introuvable à la racine du plugin.")
        return
    if (data := _read_json_checked(mcp_json, "mcp_config.json", report)) is None:
        return
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        report.error("mcp_config.json doit contenir un objet 'mcpServers' non vide.")
        return
    for sname, scfg in servers.items():
        if not isinstance(scfg, dict) or not _is_nonempty_str(scfg.get("command")) or not isinstance(scfg.get("args"), list):
            report.error(f"mcp_config.json ({sname}) : command/args invalide.")
            continue
        for arg in scfg["args"]:
            if isinstance(arg, str) and arg.endswith(".py") and _resolve_plugin_file(root, arg) is None:
                report.error(f"mcp_config.json ({sname}) : script introuvable {arg!r}")
    if os.name == "nt" and '"command": "python3"' in mcp_json.read_text(encoding="utf-8", errors="replace") and not has_real_python3():
        report.info("mcp_config.json portable ('python3') détecté sous Windows : adapté lors de l'installation.")

def _check_markdown_links(md_path: Path, label: str, report: Report) -> None:
    try:
        lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        report.error(f"Impossible de lire {label} : {exc}")
        return
    in_fence, fchar, flen = False, "", 0
    for line in lines:
        if (fm := MD_FENCE_RE.match(line)):
            marker = fm.group(1)
            if not in_fence:
                in_fence, fchar, flen = True, marker[0], len(marker)
                continue
            if marker[0] == fchar and len(marker) >= flen and line.strip() == marker:
                in_fence = False
                continue
        if not in_fence:
            for m in MD_LINK_RE.finditer(INLINE_CODE_RE.sub("", line)):
                rt = m.group(1).strip()
                t = rt[1 : rt.index(">")].strip() if rt.startswith("<") and ">" in rt else (rt.split()[0] if rt else "")
                if t and not t.lower().startswith(IGNORED_LINK_PREFIXES) and (rel := t.split("#", 1)[0].split("?", 1)[0]):
                    if not (md_path.parent / rel).resolve().exists():
                        report.error(f"Lien Markdown relatif cassé dans {label} : {t!r}")

def _scan_text_hygiene(root: Path, cfg: ValidatorConfig, report: Report) -> None:
    extra = [(str(it[0]), re.compile(it[1]) if isinstance(it[1], str) else it[1]) if isinstance(it, tuple) and len(it) == 2 else ("motif interdit", re.compile(it) if isinstance(it, str) else it) for it in cfg.extra_forbidden_patterns]
    for fpath in sorted(root.rglob("*")):
        if "__pycache__" in fpath.parts or ".git" in fpath.parts:
            continue
        if is_symlink_or_junction(fpath):
            report.error(f"Lien symbolique ou jonction interdit : {fpath}")
        elif fpath.is_file() and fpath.suffix.lower() in (".py", ".md", ".json", ".svg"):
            rel = fpath.relative_to(root).as_posix()
            if (payload := check_utf8_no_bom_lf(fpath, rel, report)) is not None:
                content = payload.decode("utf-8", errors="replace")
                for label, pat in (("Jargon interdit", FORBIDDEN_MOD_RE), ("Chemin personnel", PERSONAL_PATH_RE), ("Secret potentiel", SECRET_LEAK_RE), *extra):
                    if pat.search(content):
                        report.error(f"{label} détecté dans {rel}")


def validate_plugin_tree(cfg_or_root: Union[ValidatorConfig, Path, str], report: Optional[Report] = None) -> Report:
    """Run unified validation across plugin manifest, module contract, skills, rules, hooks, MCP, and stdlib."""
    cfg = cfg_or_root if isinstance(cfg_or_root, ValidatorConfig) else ValidatorConfig(plugin_root=Path(cfg_or_root))
    rep, root = report if report is not None else Report(), Path(cfg.plugin_root)
    if is_symlink_or_junction(root) or not root.is_dir():
        rep.error(f"Racine plugin invalide ou lien symbolique/jonction : {root}")
        return rep
    for sub in cfg.required_dirs:
        if not (root / sub).is_dir():
            rep.error(f"Dossier requis manquant : {sub}/")
    manifest = validate_plugin_manifest(root, rep, expected_name=cfg.expected_plugin_name)
    exp_pname = cfg.expected_plugin_name or (manifest.get("name") if isinstance(manifest, dict) else None)
    if cfg.require_module_contract or (root / "config" / "module.json").is_file():
        validate_module_contract(root, rep, expected_plugin_name=exp_pname, expected_module_id=cfg.expected_module_id)
    validate_skills(root, rep, max_bytes=cfg.skill_max_bytes)
    if rules_file := (root / "rules" / "AGENTS.md"):
        if cfg.require_rules_agents and not rules_file.is_file():
            rep.error("rules/AGENTS.md introuvable.")
        elif rules_file.is_file() and (rb := check_utf8_no_bom_lf(rules_file, "rules/AGENTS.md", rep, max_bytes=cfg.rules_max_bytes, warn_bytes=cfg.rules_warn_bytes)) is not None:
            if rb.decode("utf-8", errors="replace").lstrip().startswith("---"):
                rep.error("rules/AGENTS.md ne doit pas contenir de frontmatter YAML.")
    if root.name == "plugin" and (root.parent / "AGENTS.md").is_file():
        check_utf8_no_bom_lf(root.parent / "AGENTS.md", "AGENTS.md", rep, max_bytes=cfg.repo_agents_max_bytes)
    if cfg.require_hooks_config or (root / "hooks.json").is_file():
        validate_hooks_config(root / "hooks.json", rep, plugin_root=root, is_portable_template=cfg.is_portable_template, required_pretool_matchers=cfg.required_pretool_matchers)
    if cfg.require_mcp_config or (root / "mcp_config.json").is_file():
        validate_mcp_config(root, rep)
    if cfg.check_links:
        md_items = ([((root / "README.md"), "README.md")] if (root / "README.md").is_file() else []) + [(sk, sk.relative_to(root).as_posix()) for sk in (sorted((root / "skills").glob("*/SKILL.md")) if (root / "skills").is_dir() else ())]
        for md_p, lbl in md_items:
            _check_markdown_links(md_p, lbl, rep)
    for py_file in sorted(root.rglob("*.py")):
        if "__pycache__" not in py_file.parts:
            check_stdlib_only(py_file, rep, allowed_local_modules=cfg.allowed_local_modules)
    _scan_text_hygiene(root, cfg, rep)
    if cfg.extra_check_fn is not None:
        cfg.extra_check_fn(root, rep)
    return rep

def run_agy_plugin_validate(
    plugin_root: Union[str, Path], *, strict: bool = True, agy_bin: Optional[Union[str, Path]] = None, timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Run 'agy plugin validate', staging in a non-hidden $HOME temp dir under Snap confinement."""
    resolved_bin = os.fspath(agy_bin) if agy_bin is not None else (shutil.which("agy") or ("/snap/bin/agy" if os.path.isfile("/snap/bin/agy") else None))
    if not resolved_bin:
        return {"rc": 127, "ok": False, "stdout": "", "stderr": "'agy' CLI introuvable.", "staged": False, "agy_bin": None}
    src_abs = os.path.abspath(os.fspath(plugin_root))
    if is_symlink_or_junction(src_abs) or not os.path.isdir(src_abs):
        return {"rc": 2, "ok": False, "stdout": "", "stderr": f"Racine plugin invalide : {src_abs}", "staged": False, "agy_bin": resolved_bin}
    is_snap = "/snap/" in resolved_bin or "/snap/" in os.path.realpath(resolved_bin)
    has_hidden = any(part.startswith(".") for part in Path(src_abs).parts if part not in ("/", ""))
    tmp_base = os.path.abspath(tempfile.gettempdir())
    in_tmp = src_abs in ("/tmp", tmp_base) or src_abs.startswith(("/tmp/", tmp_base.rstrip(os.sep) + os.sep))
    stage_parent, target_dir, staged = None, src_abs, False
    try:
        if is_snap or has_hidden or in_tmp:
            stage_parent = tempfile.mkdtemp(prefix="gravity-validate-", dir=_safe_home_str())
            target_dir = os.path.join(stage_parent, "plugin")
            shutil.copytree(src_abs, target_dir, ignore=COPY_IGNORE)
            staged = True
        cmd = [resolved_bin, "plugin", "validate", target_dir] + (["--strict"] if strict else [])
        proc = subprocess.run(cmd, shell=False, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout_s, check=False)
        return {"rc": proc.returncode, "ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr, "staged": staged, "agy_bin": resolved_bin}
    finally:
        if stage_parent is not None:
            shutil.rmtree(stage_parent, ignore_errors=True)

def run_cli(base_cfg: Optional[ValidatorConfig] = None, argv: Optional[Sequence[str]] = None, *, description: str = "Validateur strict Gravity") -> int:
    """Unified CLI entry point supporting --strict, --plugin-root, --fix-python-cmd, --json, and --workspace."""
    configure_utf8_stdio()
    p = argparse.ArgumentParser(description=description)
    for flag, kw in (
        ("--strict", {"action": "store_true", "help": "Échoue sur les avertissements."}),
        ("--plugin-root", {"type": Path, "default": base_cfg.plugin_root if base_cfg else Path.cwd(), "help": "Racine du plugin."}),
        ("--fix-python-cmd", {"action": "store_true", "help": "Adapte 'python3' dans hooks.json / mcp_config.json."}),
        ("--json", {"action": "store_true", "help": "Sortie JSON."}),
    ):
        p.add_argument(flag, **kw)
    if base_cfg is not None and base_cfg.workspace_validator_fn is not None:
        p.add_argument("--workspace", type=Path, default=None, help="Audite un workspace utilisateur.")
    args = p.parse_args(argv)
    plugin_root = Path(os.path.abspath(os.path.expanduser(os.fspath(args.plugin_root))))
    if args.fix_python_cmd:
        res = fix_python_cmd(plugin_root)
        print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else ("[INFO]  'python3' réel détecté." if res["skipped_real_python3"] else (f"[FIXED] {', '.join(res['fixed_files'])} -> '{res['python_cmd']}'" if res["changed"] else f"[INFO]  Aucun changement dans {plugin_root}.")))
        return 0 if res["ok"] else 1
    strict, report, ws_arg = bool(args.strict or (base_cfg.strict if base_cfg else False)), Report(), getattr(args, "workspace", None)
    if ws_arg is not None and base_cfg is not None and base_cfg.workspace_validator_fn is not None:
        base_cfg.workspace_validator_fn(Path(os.path.abspath(os.path.expanduser(os.fspath(ws_arg)))), report)
        mode = f"Workspace ({ws_arg})"
    else:
        cfg = replace(base_cfg, plugin_root=plugin_root, strict=strict, json_output=bool(args.json)) if base_cfg else ValidatorConfig(plugin_root=plugin_root, strict=strict, json_output=bool(args.json))
        validate_plugin_tree(cfg, report)
        mode = "Plugin"
    if args.json:
        print(report.to_json(strict))
        return 0 if report.ok(strict) else 1
    for tag, items in (("[INFO] ", report.infos), ("[ERROR]", report.errors), ("[WARN] ", report.warnings)):
        for msg in items:
            print(f"{tag} {msg}")
    ok = report.ok(strict)
    print(f"[PASS] Validation {mode} réussie (0 erreur, {len(report.warnings)} avertissement)." if ok else f"[FAIL] Validation {mode} échouée ({len(report.errors)} erreur(s), {len(report.warnings)} avertissement(s)).")
    return 0 if ok else 1
