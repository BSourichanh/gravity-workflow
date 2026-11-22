#!/usr/bin/env python3
"""Gravity Workflow Live Widget Sidecar (HTTP + SSE, anti-XSS, multi-workspace, 100 % Python 3 stdlib)."""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

_OBS_DIR = Path(__file__).resolve().parents[2] / "observability"
if str(_OBS_DIR) not in sys.path:
    sys.path.insert(0, str(_OBS_DIR))

import state_writer  # noqa: E402

PORT = int(os.environ.get("GRAVITY_WORKFLOW_WIDGET_PORT", "4789"))
RELATIVE_STATE = Path(".agents/gravity-workflow/observability/live_state.json")
_DEFAULT_REGISTRY_SENTINEL = state_writer.default_workflow_home() / "live_workspaces.json"
REGISTRY_PATH = _DEFAULT_REGISTRY_SENTINEL


def default_workflow_home() -> Path:
    """Résout le dossier centralisé Gravity Workflow ($GRAVITY_WORKFLOW_HOME > $GRAVITY_HOME/workflow > ~/.gemini/gravity/workflow)."""
    return state_writer.default_workflow_home()


def resolve_registry_path(registry_path: Optional[Path] = None) -> Path:
    """Résout live_workspaces.json via arg, monkeypatch REGISTRY_PATH ou state_writer.resolve_registry_path()."""
    if registry_path is not None:
        return Path(registry_path).expanduser().resolve()
    if REGISTRY_PATH is not None and REGISTRY_PATH != _DEFAULT_REGISTRY_SENTINEL:
        return Path(REGISTRY_PATH).expanduser()
    return state_writer.resolve_registry_path()


class GravityHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _workspace_from_state_path(state_path: Path) -> str:
    for parent in state_path.parents:
        if parent.name == ".agents":
            return str(parent.parent)
        if parent.name == "gravity-workflow" and parent.parent.name != ".agents":
            return str(parent.parent)
    return str(state_path.parent)


def _load_registered_workspaces() -> List[Dict[str, str]]:
    reg_path = resolve_registry_path()
    if not reg_path.is_file():
        return []
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
        entries = data.get("workspaces", []) if isinstance(data, dict) else []
        valid = [
            e
            for e in entries
            if isinstance(e, dict) and isinstance(e.get("state_path"), str)
        ]
        valid.sort(key=lambda e: str(e.get("updated_at", "")), reverse=True)
        return valid
    except Exception:
        return []


def resolve_state_path(ws_index: Optional[int] = None) -> Path:
    """Résout live_state.json via env, registre centralisé (~/.gemini/gravity/workflow/live_workspaces.json) ou cwd."""
    env_state = os.environ.get("GRAVITY_WORKFLOW_STATE_PATH", "").strip()
    if env_state and "${" not in env_state:
        return Path(env_state)

    env_ws = os.environ.get("GRAVITY_WORKFLOW_WORKSPACE", "").strip()
    if env_ws and "${" not in env_ws:
        return Path(env_ws) / RELATIVE_STATE

    registered = _load_registered_workspaces()
    if registered:
        if ws_index is not None and 0 <= ws_index < len(registered):
            return Path(registered[ws_index]["state_path"])
        return Path(registered[0]["state_path"])

    cwd = Path.cwd().resolve()
    for candidate_root in (cwd, *cwd.parents):
        candidate = candidate_root / RELATIVE_STATE
        if candidate.is_file():
            return candidate

    return cwd / RELATIVE_STATE


HTML_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8" />
<title>Gravity Workflow Live Widget</title>
<style>
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; margin: 24px; }
  h1 { font-size: 1.2rem; color: #38bdf8; margin-bottom: 12px; }
  .toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
  .ws-select { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 6px; padding: 4px 8px; }
  .graph { display: flex; gap: 16px; align-items: center; margin-bottom: 24px; }
  .node { padding: 12px 18px; border-radius: 10px; border: 1px solid #334155; background: #1e293b; min-width: 130px; text-align: center; }
  .node.active { border-color: #38bdf8; box-shadow: 0 0 12px rgba(56,189,248,.35); }
  .node.done { border-color: #22c55e; }
  .node.fail { border-color: #ef4444; }
  .arrow { color: #64748b; font-weight: bold; }
  .panel { background: #1e293b; border-radius: 10px; padding: 16px; border: 1px solid #334155; }
  .event { font-family: monospace; font-size: .85rem; padding: 6px 0; border-bottom: 1px solid #334155; word-break: break-word; }
  .deny { color: #f87171; }
  .ask, .force_ask { color: #fbbf24; }
  .allow { color: #4ade80; }
</style>
</head>
<body>
  <h1>Gravity Workflow Live Widget — Équipe &amp; Garde-fous</h1>
  <div class="toolbar">
    <div id="meta" style="color:#94a3b8;">En attente de live_state.json...</div>
    <select id="ws-picker" class="ws-select" style="display:none;" aria-label="Workspace actif"></select>
  </div>
  <div class="graph">
    <div id="n-planner" class="node">Planner<br/><small>pro</small></div>
    <div class="arrow">→</div>
    <div id="n-builder" class="node">Builder<br/><small>branch</small></div>
    <div class="arrow">→</div>
    <div id="n-reviewer" class="node">Independent Reviewer<br/><small>pro</small></div>
  </div>
  <div class="panel">
    <strong>Fil des garde-fous &amp; événements</strong>
    <div id="feed"></div>
  </div>
<script>
const ALLOWED_NODE_STATES = new Set(['active', 'done', 'fail']);
const ALLOWED_DECISIONS = new Set(['allow', 'deny', 'ask', 'force_ask']);
const params = new URLSearchParams(window.location.search);
const wsParam = params.get('ws');
const eventsUrl = wsParam !== null ? `/events?ws=${encodeURIComponent(wsParam)}` : '/events';

const picker = document.getElementById('ws-picker');
let lastWorkspacesSig = '';
picker.addEventListener('change', () => {
  window.location.search = `?ws=${encodeURIComponent(picker.value)}`;
});

const es = new EventSource(eventsUrl);
es.onmessage = (e) => {
  const s = JSON.parse(e.data);
  const wsLabel = s.workspace ? `Workspace: ${s.workspace} | ` : '';
  const modeVal = s.mode || 'evo';
  const lvlVal = s.level || 'medium';
  const paceVal = s.pace || 'slow';
  const verifyVal = s.verification || 'safe';
  const cavVal = s.caveman || 'lite';
  const cavLock = s.caveman_locked ? '[lock]' : '(auto)';
  const modeText = modeVal === 'evo' ? `evo (${lvlVal})` : `${lvlVal} [verrouillé]`;
  document.getElementById('meta').textContent =
    `${wsLabel}Mode: ${modeText} · ${paceVal} · ${verifyVal} · caveman:${cavVal} ${cavLock} | Étape: ${s.stage || 'idle'} | Progression: ${s.progress || '0/8'} | Guard: ${s.guard_status || 'ON'}`;

  if (Array.isArray(s.workspaces) && s.workspaces.length > 1) {
    picker.style.display = 'inline-block';
    const wsSig = JSON.stringify(s.workspaces);
    if (wsSig !== lastWorkspacesSig) {
      lastWorkspacesSig = wsSig;
      picker.replaceChildren();
      s.workspaces.forEach((ws, idx) => {
        const opt = document.createElement('option');
        opt.value = String(idx);
        opt.textContent = String(ws.workspace || ws.state_path || idx);
        if (wsParam !== null ? String(idx) === wsParam : idx === 0) {
          opt.selected = true;
        }
        picker.appendChild(opt);
      });
    }
  } else {
    picker.style.display = 'none';
    lastWorkspacesSig = '';
  }

  ['planner', 'builder', 'reviewer'].forEach((k) => {
    const el = document.getElementById('n-' + k);
    const rawState = s.nodes && typeof s.nodes[k] === 'string' ? s.nodes[k] : '';
    el.className = ALLOWED_NODE_STATES.has(rawState) ? `node ${rawState}` : 'node';
  });

  const feed = document.getElementById('feed');
  feed.replaceChildren();
  const events = Array.isArray(s.events) ? s.events.slice(-15).reverse() : [];
  if (events.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'event';
    empty.textContent = 'Aucun événement enregistré.';
    feed.appendChild(empty);
    return;
  }

  events.forEach((ev) => {
    const row = document.createElement('div');
    const dec = typeof ev.decision === 'string' && ALLOWED_DECISIONS.has(ev.decision) ? ev.decision : '';
    row.className = dec ? `event ${dec}` : 'event';
    const ts = ev.ts ? String(ev.ts) : '';
    const evType = ev.type ? String(ev.type) : 'EVENT';
    const msg = ev.message ? String(ev.message) : '';
    row.textContent = `[${ts}] ${evType} — ${msg}`;
    feed.appendChild(row);
  });
};
</script>
</body>
</html>
"""


def _stat_sig(path: Path) -> Tuple[str, int, int]:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), -1, -1)


def _profile_path_for_state(state_path: Path) -> Path:
    if state_path.parent.name == "observability":
        return state_path.parent.parent / "profile.json"
    return Path(_workspace_from_state_path(state_path)) / ".agents/gravity-workflow/profile.json"


def _state_signature(state_path: Path) -> Tuple[Tuple[str, int, int], Tuple[str, int, int], Tuple[str, int, int]]:
    return (
        _stat_sig(state_path),
        _stat_sig(_profile_path_for_state(state_path)),
        _stat_sig(resolve_registry_path()),
    )


def read_state(ws_index: Optional[int] = None) -> str:
    state_path = resolve_state_path(ws_index)
    workspaces = _load_registered_workspaces()
    ws_dir = _workspace_from_state_path(state_path)
    payload: Dict[str, object] = {
        "stage": "idle",
        "progress": "0/8",
        "mode": "evo",
        "level": "medium",
        "pace": "slow",
        "verification": "safe",
        "caveman": "lite",
        "caveman_locked": False,
        "locked": False,
        "guard_status": "ON",
        "nodes": {},
        "events": [],
        "workspace": ws_dir,
        "workspaces": workspaces,
    }
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                payload.update(data)
                payload["workspaces"] = workspaces
        except Exception:
            pass
    profile_path = (
        state_path.parent.parent / "profile.json"
        if state_path.parent.name == "observability"
        else Path(ws_dir) / ".agents/gravity-workflow/profile.json"
    )
    if profile_path.is_file():
        try:
            pdata = json.loads(profile_path.read_text(encoding="utf-8"))
            if isinstance(pdata, dict):
                if isinstance(pdata.get("mode"), str):
                    payload["mode"] = pdata["mode"]
                if isinstance(pdata.get("current_level"), str):
                    payload["level"] = pdata["current_level"]
                if isinstance(pdata.get("pace"), str):
                    payload["pace"] = pdata["pace"]
                if isinstance(pdata.get("verification"), str):
                    payload["verification"] = pdata["verification"]
                if isinstance(pdata.get("caveman"), str):
                    payload["caveman"] = pdata["caveman"]
                if isinstance(pdata.get("caveman_locked"), bool):
                    payload["caveman_locked"] = pdata["caveman_locked"]
                if isinstance(pdata.get("locked"), bool):
                    payload["locked"] = pdata["locked"]
        except Exception:
            pass
    return json.dumps(payload, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'",
        )

    def _parse_ws_index(self, query: str) -> Optional[int]:
        qs = parse_qs(query)
        raw = qs.get("ws", [None])[0]
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _is_allowed_host(self) -> bool:
        host = str(self.headers.get("Host", "")).strip().lower()
        allowed = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost"}
        return host in allowed

    def do_GET(self) -> None:
        if not self._is_allowed_host():
            body = b"Forbidden"
            self.send_response(403)
            self._send_security_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        parsed = urlparse(self.path)
        ws_index = self._parse_ws_index(parsed.query)

        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self._send_security_headers()
            self.end_headers()
            return

        if parsed.path == "/health":
            state_path = resolve_state_path(ws_index)
            body = json.dumps(
                {
                    "status": "ok",
                    "state_path": str(state_path),
                    "workspace": _workspace_from_state_path(state_path),
                    "registered_workspaces": len(_load_registered_workspaces()),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/events":
            self.send_response(200)
            self._send_security_headers()
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last_sig: Optional[
                Tuple[Tuple[str, int, int], Tuple[str, int, int], Tuple[str, int, int]]
            ] = None
            last_payload: Optional[str] = None
            last_ping = time.time()
            try:
                while True:
                    state_path = resolve_state_path(ws_index)
                    sig = _state_signature(state_path)
                    if sig != last_sig or last_payload is None:
                        payload = read_state(ws_index)
                        last_sig = sig
                        if payload != last_payload:
                            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                            self.wfile.flush()
                            last_payload = payload
                            last_ping = time.time()
                            time.sleep(1)
                            continue
                    if time.time() - last_ping > 15:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        if parsed.path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self._send_security_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = b"Not Found"
        self.send_response(404)
        self._send_security_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = GravityHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
