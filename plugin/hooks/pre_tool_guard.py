#!/usr/bin/env python3
"""Hook PreToolUse fail-closed Gravity (stdlib Python 3, exit 0 garanti)."""

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
OBS_DIR = PLUGIN_ROOT / "observability"
for _p in (str(PLUGIN_ROOT), str(HOOKS_DIR), str(OBS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _fallback_emit(decision: str, reason: str) -> None:
    sys.stdout.write(json.dumps({"decision": decision, "reason": reason}, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def record_live_event(
    decision: str,
    tool_name: str,
    reason: str | None,
    workspace_root: Path,
    conversation_id: str | None = None,
) -> None:
    """Enregistre les décisions bloquantes/escaladées dans live_state.json centralisé sans jamais échouer."""
    if decision not in ("deny", "ask", "force_ask"):
        return
    try:
        from state_writer import update_live_state  # type: ignore[import-not-found]

        event_payload: dict[str, object] = {
            "type": f"GUARD:{tool_name}",
            "decision": decision,
            "message": reason or "Action interceptée.",
        }
        if conversation_id:
            event_payload["conversation_id"] = conversation_id

        update_live_state(
            guard_status="ON",
            workspace_root=workspace_root,
            event=event_payload,
        )
    except Exception:
        # Ne jamais compromettre le contrat fail-closed exit 0
        pass


def main() -> int:
    tool_name = "unknown"
    ws_root = Path.cwd()
    try:
        from _shared.hook_runtime import (  # type: ignore[import-not-found]
            configure_utf8_stdio,
            emit_json,
            first_not_none,
            read_stdin_text,
            resolve_workspace_root,
        )
        from safety_policy import evaluate_tool_call  # type: ignore[import-not-found]
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

        ws_root = resolve_workspace_root(
            parsed,
            anchor_file=__file__,
            fallback_cwd=True,
            env_var="GRAVITY_WORKFLOW_WORKSPACE",
        ) or Path.cwd()
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
