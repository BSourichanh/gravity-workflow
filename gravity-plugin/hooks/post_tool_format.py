#!/usr/bin/env python3
"""Hook PostToolUse Gravity : auto-formatage non bloquant (exit 0 + stdout `{}` garanti)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

FORMATTERS = {
    ".py": [["ruff", "format"], ["black", "--quiet"]],
    ".js": [["prettier", "--write"]],
    ".ts": [["prettier", "--write"]],
    ".tsx": [["prettier", "--write"]],
    ".jsx": [["prettier", "--write"]],
    ".json": [["prettier", "--write"]],
    ".md": [["prettier", "--write"]],
    ".yml": [["prettier", "--write"]],
    ".yaml": [["prettier", "--write"]],
}

TARGET_PATH_KEYS = (
    "TargetFile",
    "target_file",
    "AbsolutePath",
    "path",
    "file_path",
    "filename",
    "Path",
)


def find_formatter(ext: str) -> Optional[List[str]]:
    for candidate in FORMATTERS.get(ext.lower(), []):
        if shutil.which(candidate[0]):
            return candidate
    return None


def _has_prettier_config(ws_root: Path) -> bool:
    """Vérifie la présence d'une configuration Prettier explicite dans le workspace."""
    if not ws_root.is_dir():
        return False
    for pattern in (".prettierrc*", "prettier.config.*"):
        for candidate in ws_root.glob(pattern):
            if candidate.is_file():
                return True
    pkg = ws_root / "package.json"
    if pkg.is_file():
        try:
            pkg_data = json.loads(pkg.read_text(encoding="utf-8"))
            if isinstance(pkg_data, dict) and "prettier" in pkg_data:
                return True
        except Exception:
            pass
    return False


def main() -> int:
    try:
        from hook_runtime import (
            configure_utf8_stdio,
            emit_ok,
            first_str_arg,
            read_stdin_text,
            resolve_workspace_root,
        )
    except Exception:
        sys.stdout.write("{}\n")
        sys.stdout.flush()
        return 0

    configure_utf8_stdio()
    try:
        raw = read_stdin_text()
        if not raw:
            emit_ok()
            return 0

        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("error"):
            emit_ok()
            return 0

        tool = data.get("toolCall")
        args = tool.get("args") if isinstance(tool, dict) else None
        if not isinstance(args, dict):
            emit_ok()
            return 0

        target_raw = first_str_arg(args, TARGET_PATH_KEYS)
        if not target_raw:
            emit_ok()
            return 0

        ws_root = resolve_workspace_root(data, __file__)
        try:
            prof_path = ws_root / ".agents/gravity/profile.json"
            if prof_path.is_file():
                pdata = json.loads(prof_path.read_text(encoding="utf-8"))
                if isinstance(pdata, dict) and pdata.get("verification") == "unsafe":
                    emit_ok()
                    return 0
        except Exception:
            pass

        target_path = Path(target_raw)
        if not target_path.is_absolute():
            target_path = ws_root / target_path
        if ".agents" in target_path.parts or not target_path.is_file():
            emit_ok()
            return 0

        cmd_base = find_formatter(target_path.suffix)
        if cmd_base:
            if cmd_base[0] == "prettier" and not _has_prettier_config(ws_root):
                emit_ok()
                return 0
            subprocess.run(
                cmd_base + [str(target_path)],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
    except Exception:
        pass
    emit_ok()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
