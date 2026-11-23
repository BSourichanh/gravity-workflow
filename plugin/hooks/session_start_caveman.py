#!/usr/bin/env python3
"""Hook SessionStart Gravity : injecte le protocole Caveman sur tout dépôt (exit 0 garanti)."""

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
OBS_DIR = PLUGIN_ROOT / "observability"

for _p in (str(HOOKS_DIR), str(OBS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    try:
        from hook_runtime import (
            configure_utf8_stdio,
            emit_json,
            emit_ok,
            read_stdin_text,
            resolve_workspace_root,
        )
        from caveman_protocol import build_session_start_prompt  # type: ignore[import-not-found]
        from profile_engine import load_profile  # type: ignore[import-not-found]
    except Exception:
        sys.stdout.write("{}\n")
        sys.stdout.flush()
        return 0

    configure_utf8_stdio()
    try:
        raw = read_stdin_text()
        if raw:
            try:
                event = json.loads(raw)
            except Exception:
                emit_ok()
                return 0
        else:
            event = {}
        if not isinstance(event, dict):
            emit_ok()
            return 0

        ws_root = resolve_workspace_root(event, __file__)
        profile = load_profile(ws_root)
        level = str(profile.get("caveman", "lite")).strip().lower()
        locked = bool(profile.get("caveman_locked", False))
        if level == "off":
            emit_ok()
            return 0

        prompt = build_session_start_prompt(level, locked=locked)
        if not prompt:
            emit_ok()
            return 0

        emit_json(
            {
                "injectSteps": [
                    {
                        "systemMessage": {
                            "systemMessage": prompt,
                        }
                    }
                ]
            }
        )
    except Exception:
        emit_ok()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
