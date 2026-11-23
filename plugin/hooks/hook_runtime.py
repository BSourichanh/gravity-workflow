#!/usr/bin/env python3
"""Utilitaires communs d'exécution des hooks Antigravity (100 % stdlib Python 3)."""

import contextlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


def configure_utf8_stdio() -> None:
    """Force l'encodage UTF-8 sur stdin/stdout/stderr (évite les erreurs cp1252/protojson sous Windows)."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def read_stdin_text(timeout_s: float = 1.0) -> str:
    """Lit stdin en toute sécurité sans bloquer si aucun flux n'est pipé."""
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    buf: List[str] = []

    def _reader() -> None:
        try:
            buf.append(sys.stdin.read())
        except Exception:
            pass

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()
    reader_thread.join(timeout=timeout_s)
    return buf[0].strip() if buf else ""


def emit_json(payload: Dict[str, Any]) -> None:
    """Émet un objet JSON ASCII-safe sur une ligne et vide stdout."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def emit_ok() -> None:
    """Contrat officiel PostToolUse et Stop : objet JSON vide `{}`."""
    sys.stdout.write("{}\n")
    sys.stdout.flush()


def resolve_workspace_root(data: Optional[dict], anchor_file: str = __file__) -> Path:
    """Résout le workspace du projet cible depuis stdin (`cwd` / `workspacePaths`), env ou `cwd`."""
    if isinstance(data, dict):
        cwd = data.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return Path(cwd.strip())
        ws_paths = data.get("workspacePaths")
        if isinstance(ws_paths, list) and ws_paths and isinstance(ws_paths[0], str):
            return Path(ws_paths[0])
    env_ws = os.environ.get("GRAVITY_WORKFLOW_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws)
    return Path.cwd()


def first_str_arg(args: dict, keys: Tuple[str, ...]) -> str:
    """Retourne la première valeur chaîne non vide trouvée parmi `keys`."""
    for key in keys:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def first_not_none(*values: Any) -> Any:
    """Retourne le premier argument non nul (utile pour normaliser les champs camelCase)."""
    for val in values:
        if val is not None:
            return val
    return None


@contextlib.contextmanager
def _fallback_lock(_lock_path: Path) -> Iterator[None]:
    yield


def get_lock_fn():
    """Charge `exclusive_file_lock` depuis observability/state_writer.py avec repli sûr."""
    try:
        obs_dir = Path(__file__).resolve().parent.parent / "observability"
        if str(obs_dir) not in sys.path:
            sys.path.insert(0, str(obs_dir))
        from state_writer import exclusive_file_lock  # type: ignore[import-not-found]

        return exclusive_file_lock
    except Exception:
        return _fallback_lock


def is_gravity_workspace(ws_root: Path) -> bool:
    """Retourne True uniquement si le workspace est initialisé avec le socle Gravity Workflow."""
    return (
        (ws_root / ".agents/gravity-workflow/profile.json").is_file()
        or (ws_root / ".agents/gravity-workflow/PROGRESS.md").is_file()
    )


def record_live_event(
    decision: str,
    tool_name: str,
    reason: Optional[str],
    workspace_root: Path,
    conversation_id: Optional[str] = None,
) -> None:
    """Enregistre les décisions bloquantes/escaladées dans live_state.json centralisé sans jamais échouer."""
    if decision not in ("deny", "ask", "force_ask"):
        return
    try:
        obs_dir = Path(__file__).resolve().parent.parent / "observability"
        if str(obs_dir) not in sys.path:
            sys.path.insert(0, str(obs_dir))
        from state_writer import update_live_state  # type: ignore[import-not-found]

        event_payload: Dict[str, Any] = {
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
