#!/usr/bin/env python3
"""Hook Stop Gravity Workflow : journalisation centralisée `runs.jsonl` + adaptation consultative `evo`."""

import datetime
import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
OBS_DIR = HOOKS_DIR.parent / "observability"
for _p in (str(HOOKS_DIR), str(OBS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> int:
    try:
        from hook_runtime import (
            configure_utf8_stdio,
            emit_ok,
            first_not_none,
            read_stdin_text,
            resolve_workspace_root,
        )
        from profile_engine import (  # type: ignore[import-not-found]
            evolve_on_stop,
            get_active_preset,
            role_models_from_preset,
        )
        from state_writer import append_run_log  # type: ignore[import-not-found]
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
            event = {"raw_payload": event}

        ws_root = resolve_workspace_root(event, __file__)
        preset = get_active_preset(ws_root)
        entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": "Stop",
            "conversation_id": first_not_none(event.get("conversationId"), event.get("sessionId")),
            "model": event.get("modelName"),
            "preset_profile": str(preset.get("level") or "standard"),
            "role_models": role_models_from_preset(preset),
            "termination_reason": first_not_none(
                event.get("terminationReason"), event.get("stopReason")
            ),
            "execution_num": event.get("executionNum"),
            "fully_idle": event.get("fullyIdle"),
            "continuation_active": event.get("continuationActive"),
            "error": event.get("error"),
            "transcript_path": event.get("transcriptPath"),
            "cwd": str(ws_root),
        }

        append_run_log(ws_root, entry)
        evolve_on_stop(ws_root, entry)
    except Exception:
        pass
    emit_ok()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
