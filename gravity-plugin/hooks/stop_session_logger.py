#!/usr/bin/env python3
"""Hook Stop Gravity : journalisation sécurisée dans .agents/gravity/observability/runs.jsonl."""

import datetime
import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

REL_LOG_PATH = Path(".agents/gravity/observability/runs.jsonl")


def main() -> int:
    try:
        from hook_runtime import (
            configure_utf8_stdio,
            emit_ok,
            first_not_none,
            get_lock_fn,
            is_gravity_workspace,
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
        event = json.loads(raw) if raw else {}
        if not isinstance(event, dict):
            event = {"raw_payload": event}

        ws_root = resolve_workspace_root(event, __file__)
        if not is_gravity_workspace(ws_root):
            emit_ok()
            return 0
        log_path = ws_root / REL_LOG_PATH
        entry = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": "Stop",
            "conversation_id": first_not_none(event.get("conversationId"), event.get("sessionId")),
            "model": event.get("modelName"),
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

        log_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fn = get_lock_fn()
        with lock_fn(log_path.with_suffix(".lock")):
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()

        try:
            obs_dir = HOOKS_DIR.parent / "observability"
            if str(obs_dir) not in sys.path:
                sys.path.insert(0, str(obs_dir))
            from profile_engine import evolve_on_stop  # type: ignore[import-not-found]

            evolve_on_stop(ws_root, entry)
        except Exception:
            pass
    except Exception:
        pass
    emit_ok()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
