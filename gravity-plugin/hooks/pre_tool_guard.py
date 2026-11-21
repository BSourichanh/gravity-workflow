#!/usr/bin/env python3
"""Hook PreToolUse fail-closed Gravity (stdlib Python 3, exit 0 garanti)."""

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))


def _fallback_emit(decision: str, reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": decision, "reason": reason}, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main() -> int:
    tool_name = "unknown"
    ws_root = Path.cwd()
    try:
        from hook_runtime import (
            configure_utf8_stdio,
            emit_json,
            first_not_none,
            read_stdin_text,
            record_live_event,
            resolve_workspace_root,
        )
        from safety_policy import evaluate_tool_call
    except Exception as exc:
        _fallback_emit(
            "force_ask",
            f"Erreur chargement politique hook ({type(exc).__name__}) : validation humaine requise.",
        )
        return 0

    configure_utf8_stdio()
    try:
        raw = read_stdin_text()
        if not raw:
            reason = "Entrée stdin vide sur PreToolUse (fail-closed)."
            record_live_event("force_ask", tool_name, reason, ws_root)
            emit_json({"decision": "force_ask", "reason": reason})
            return 0

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            reason = "Payload JSON racine non objet sur PreToolUse (fail-closed)."
            record_live_event("force_ask", tool_name, reason, ws_root)
            emit_json({"decision": "force_ask", "reason": reason})
            return 0

        ws_root = resolve_workspace_root(parsed, __file__)
        if isinstance(parsed.get("toolCall"), dict):
            tool_name = str(parsed["toolCall"].get("name", "unknown"))
        raw_cid = first_not_none(parsed.get("conversationId"), parsed.get("sessionId"))
        conv_id = str(raw_cid).strip() if isinstance(raw_cid, str) and raw_cid.strip() else None

        decision, reason = evaluate_tool_call(parsed, ws_root)
        record_live_event(decision, tool_name, reason, ws_root, conversation_id=conv_id)
        payload = {"decision": decision}
        if reason:
            payload["reason"] = reason
        emit_json(payload)
    except Exception as exc:
        reason = f"Erreur interne hook ({type(exc).__name__}) : validation humaine requise."
        record_live_event("force_ask", tool_name, reason, ws_root)
        emit_json({"decision": "force_ask", "reason": reason})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
