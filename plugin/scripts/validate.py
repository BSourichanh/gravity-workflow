#!/usr/bin/env python3
"""Validateur officiel Gravity Workflow (double mode: plugin & workspace, 100 % stdlib)."""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validators import Report, validate_plugin, validate_workspace  # noqa: E402
from validators.base import FLAT_HOOK_EVENTS, GROUPED_HOOK_EVENTS, has_real_python3  # noqa: E402


def _detect_portable_python_cmd() -> str:
    py_bin = shutil.which("python")
    if py_bin and not (os.name == "nt" and "windowsapps" in py_bin.lower()):
        return "python"
    if shutil.which("py"):
        return "py -3"
    return "python"


def _rewrite_python3_cmd(cmd: str, replacement: str) -> str:
    if cmd == "python3":
        return replacement
    if cmd.startswith("python3 "):
        return f"{replacement} {cmd[len('python3 '):]}"
    return cmd


def _fix_python_cmd(plugin_root: Path) -> int:
    if has_real_python3():
        print("[INFO]  Exécutable 'python3' réel détecté dans le PATH : aucune adaptation nécessaire.")
        return 0

    py_cmd = _detect_portable_python_cmd()
    updated_files = 0

    hooks_json = plugin_root / "hooks.json"
    if hooks_json.is_file():
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
        changed = False
        if isinstance(data, dict):
            for group_cfg in data.values():
                if not isinstance(group_cfg, dict):
                    continue
                for ev in GROUPED_HOOK_EVENTS:
                    for entry in group_cfg.get(ev, []) or []:
                        if not isinstance(entry, dict):
                            continue
                        for h in entry.get("hooks", []) or []:
                            if isinstance(h, dict) and isinstance(h.get("command"), str):
                                new_cmd = _rewrite_python3_cmd(h["command"], py_cmd)
                                if new_cmd != h["command"]:
                                    h["command"] = new_cmd
                                    changed = True
                for ev in FLAT_HOOK_EVENTS:
                    for h in group_cfg.get(ev, []) or []:
                        if isinstance(h, dict) and isinstance(h.get("command"), str):
                            new_cmd = _rewrite_python3_cmd(h["command"], py_cmd)
                            if new_cmd != h["command"]:
                                h["command"] = new_cmd
                                changed = True
        if changed:
            hooks_json.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"[FIXED] {hooks_json} -> '{py_cmd}'")
            updated_files += 1

    sidecar_json = plugin_root / "sidecars/live-widget/sidecar.json"
    if sidecar_json.is_file():
        sc_data = json.loads(sidecar_json.read_text(encoding="utf-8"))
        changed = False
        if isinstance(sc_data, dict) and sc_data.get("command") == "python3":
            parts = py_cmd.split()
            sc_data["command"] = parts[0]
            extra_args = parts[1:]
            if extra_args:
                existing = [str(a) for a in (sc_data.get("args") or [])]
                if existing[: len(extra_args)] != extra_args:
                    sc_data["args"] = extra_args + existing
            changed = True
        if changed:
            sidecar_json.write_text(
                json.dumps(sc_data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"[FIXED] {sidecar_json} -> '{py_cmd}'")
            updated_files += 1

    if updated_files == 0:
        print(f"[INFO]  Aucun fichier à adapter vers '{py_cmd}'.")
    return 0


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main() -> int:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Validateur Gravity Workflow")
    parser.add_argument("--strict", action="store_true", help="Échoue également sur les avertissements.")
    parser.add_argument("--workspace", type=Path, default=None, help="Audite un workspace utilisateur.")
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=SCRIPT_DIR.parent,
        help="Racine du plugin Gravity Workflow à auditer.",
    )
    parser.add_argument(
        "--fix-python-cmd",
        action="store_true",
        help="Adapte 'python3' vers 'python' ou 'py -3' dans hooks.json et sidecar.json sous Windows.",
    )
    args = parser.parse_args()

    plugin_root = args.plugin_root.resolve()
    if args.fix_python_cmd:
        return _fix_python_cmd(plugin_root)

    report = Report()
    if args.workspace is not None:
        validate_workspace(args.workspace.resolve(), report)
        mode = f"Workspace ({args.workspace})"
    else:
        validate_plugin(plugin_root, report)
        mode = "Plugin"

    for info in report.infos:
        print(f"[INFO]  {info}")
    for err in report.errors:
        print(f"[ERROR] {err}")
    for warn in report.warnings:
        print(f"[WARN]  {warn}")

    if report.ok(args.strict):
        print(f"[PASS] Validation {mode} réussie (0 erreur, {len(report.warnings)} avertissement).")
        return 0
    print(
        f"[FAIL] Validation {mode} échouée "
        f"({len(report.errors)} erreur(s), {len(report.warnings)} avertissement(s))."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
