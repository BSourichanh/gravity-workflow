#!/usr/bin/env python3
"""Read-only SPI-1 Gravity Workflow dashboard provider (`--view workflow --json`, 100% stdlib)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any, Optional, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
for _p in (str(PLUGIN_ROOT), str(PLUGIN_ROOT / "observability")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _shared.hook_runtime import configure_utf8_stdio, read_stdin_text  # noqa: E402
from _shared.path_safety import (  # noqa: E402
    ensure_no_symlink_or_junction_under,
    is_symlink_or_junction,
    safe_home_dir,
)

try:
    import metrics_signals as _metrics_signals  # type: ignore[import-not-found]
except Exception:
    _metrics_signals = None

MAX_EVENTS, MAX_RECENT_RUNS, MAX_PAYLOAD_BYTES = 50, 10, 450_000
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _default_workflow_fields() -> dict[str, Any]:
    return {
        "stage": "idle", "progress": "0/0", "mode": "evo", "level": "standard",
        "preset_profile": "standard", "suggested_profile": "standard", "suggested_reason": "",
        "role_models": {"plan": "flash", "build": "flash", "review": "pro"},
        "caveman": "lite", "caveman_locked": False, "guard_status": "ON",
        "nodes": {"planner": "", "builder": "", "reviewer": ""},
        "events": [], "waves": [], "tasks": [],
    }


def _short_digest(parts: Sequence[Any]) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _stat_token(path: Optional[Path]) -> str:
    if path is None:
        return "missing"
    if is_symlink_or_junction(path):
        return "symlink"
    try:
        st = path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "error"


def _workspace_slug_hash(workspace_root: Path) -> str:
    resolved = workspace_root.resolve()
    slug = _SLUG_RE.sub("-", resolved.name.lower()).strip("-")[:32] or "workspace"
    digest = hashlib.sha256(os.path.normcase(str(resolved)).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def _norm_ws_key(raw: str) -> str:
    try:
        return os.path.normcase(str(Path(raw).expanduser().resolve()))
    except OSError:
        return os.path.normcase(raw)


def _resolve_gravity_home_opt(gravity_home: Optional[Path] = None) -> Optional[Path]:
    if gravity_home is not None:
        return Path(gravity_home).expanduser().resolve()
    if env_g := os.environ.get("GRAVITY_HOME", "").strip():
        return Path(env_g).expanduser().resolve()
    return None


def _resolve_workflow_home(*, gravity_home: Optional[Path] = None, workflow_home: Optional[Path] = None) -> Path:
    if workflow_home is not None:
        return Path(workflow_home).expanduser().resolve()
    if env_wf := os.environ.get("GRAVITY_WORKFLOW_HOME", "").strip():
        return Path(env_wf).expanduser().resolve()
    if gravity_home is not None:
        return (Path(gravity_home).expanduser().resolve() / "workflow").resolve()
    if env_g := os.environ.get("GRAVITY_HOME", "").strip():
        return (Path(env_g).expanduser().resolve() / "workflow").resolve()
    return (safe_home_dir() / ".gemini" / "gravity" / "workflow").resolve()


def _resolve_registry_path(wf_home: Path, registry_path: Optional[Path] = None) -> Path:
    if registry_path is not None:
        return Path(registry_path).expanduser().resolve()
    if env_reg := os.environ.get("GRAVITY_WORKFLOW_REGISTRY_PATH", "").strip():
        return Path(env_reg).expanduser().resolve()
    return (wf_home / "live_workspaces.json").resolve()


def _read_json_ro(path: Path, warnings: list[str]) -> Any:
    if is_symlink_or_junction(path):
        warnings.append(f"Ignored symlink or junction JSON: {path.name}.")
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        warnings.append(f"Failed to read {path.name}: {exc}")
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
        warnings.append(f"UTF-8 BOM detected in {path.name}.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        warnings.append(f"Invalid JSON in {path.name}: {exc}")
        return None


def _load_registered_workspaces_ro(reg_file: Path, warnings: list[str]) -> list[dict[str, Any]]:
    if is_symlink_or_junction(reg_file.parent) or is_symlink_or_junction(reg_file):
        warnings.append(f"Ignored symlink or junction live_workspaces.json: {reg_file}.")
        return []
    if not reg_file.is_file() or (data := _read_json_ro(reg_file, warnings)) is None:
        return []
    if not isinstance(data, dict):
        warnings.append("Invalid structure in live_workspaces.json: expected JSON object.")
        return []
    if not isinstance(entries := data.get("workspaces"), list):
        warnings.append("Invalid 'workspaces' field in live_workspaces.json.")
        return []
    valid: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(raw_sp := entry.get("state_path"), str) or not raw_sp.strip():
            continue
        sp = Path(raw_sp.strip()).expanduser()
        if sp.name != "live_state.json":
            warnings.append(f"Ignored unsafe or non-live_state.json registry entry: {raw_sp!r}.")
            continue
        if is_symlink_or_junction(sp):
            warnings.append(f"Ignored symlink or junction live_state.json entry: {sp}.")
            continue
        ws_path = str(entry.get("workspace_path") or entry.get("workspace") or "").strip() or str(sp.parent)
        valid.append({
            "workspace_path": ws_path, "workspace": ws_path, "state_path": str(sp),
            "updated_at": str(entry.get("updated_at") or ""), "exists": sp.is_file(), "source": "registry",
        })
    valid.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return valid


def _scan_workspaces_dir_ro(wf_home: Path, warnings: list[str]) -> list[dict[str, Any]]:
    ws_dir = wf_home / "workspaces"
    if is_symlink_or_junction(ws_dir) or not ws_dir.is_dir():
        return []
    try:
        children = sorted(ws_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    found: list[dict[str, Any]] = []
    for sub in children:
        if is_symlink_or_junction(sub) or not sub.is_dir():
            continue
        state_file, profile_file, runs_file = sub / "live_state.json", sub / "profile.json", sub / "runs.jsonl"
        probe = next((p for p in (state_file, profile_file, runs_file) if not is_symlink_or_junction(p) and p.is_file()), None)
        if probe is None:
            continue
        ws_path = sub.name
        if not is_symlink_or_junction(profile_file) and profile_file.is_file():
            pdata = _read_json_ro(profile_file, warnings)
            if isinstance(pdata, dict) and isinstance(pdata.get("workspace"), str) and pdata["workspace"].strip():
                ws_path = pdata["workspace"].strip()
        try:
            mtime_ns = probe.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        has_state = not is_symlink_or_junction(state_file) and state_file.is_file()
        found.append({
            "workspace_path": ws_path, "workspace": ws_path,
            "state_path": str(state_file.resolve()) if has_state else str(state_file),
            "updated_at": "", "exists": has_state, "source": "scan", "_mtime_ns": mtime_ns,
        })
    found.sort(key=lambda item: (-int(item.get("_mtime_ns") or 0), str(item["workspace_path"])))
    for item in found:
        item.pop("_mtime_ns", None)
    return found


def _storage_dir_for_entry(entry: dict[str, Any], state_file: Path, wf_home: Path) -> Path:
    if ws_raw := str(entry.get("workspace_path") or "").strip():
        cand = Path(ws_raw).expanduser()
        if cand.is_dir() and not is_symlink_or_junction(cand):
            central = (wf_home / "workspaces" / _workspace_slug_hash(cand)).resolve()
            if central.is_dir():
                return central
    return state_file.parent


def _select_workspace(
    ws: str, workspaces: list[dict[str, Any]], wf_home: Path,
) -> tuple[Optional[dict[str, Any]], Optional[Path], Optional[Path]]:
    query = ws.strip()
    if not query:
        if not workspaces:
            return None, None, None
        sf = Path(workspaces[0]["state_path"]).expanduser()
        return workspaces[0], sf, _storage_dir_for_entry(workspaces[0], sf, wf_home)
    if query.isdigit() and 0 <= int(query) < len(workspaces):
        chosen = workspaces[int(query)]
        sf = Path(chosen["state_path"]).expanduser()
        return chosen, sf, _storage_dir_for_entry(chosen, sf, wf_home)
    norm_q = _norm_ws_key(query)
    for item in workspaces:
        ws_val, st_val = str(item.get("workspace_path") or ""), str(item.get("state_path") or "")
        if query in (ws_val, st_val) or (ws_val and _norm_ws_key(ws_val) == norm_q):
            sf = Path(st_val).expanduser()
            return item, sf, _storage_dir_for_entry(item, sf, wf_home)
    for item in workspaces:
        ws_val = str(item.get("workspace_path") or "")
        if ws_val and Path(ws_val).name.casefold() == query.casefold():
            sf = Path(item["state_path"]).expanduser()
            return item, sf, _storage_dir_for_entry(item, sf, wf_home)
    ws_dir = Path(query).expanduser()
    if ws_dir.is_dir() and not is_symlink_or_junction(ws_dir):
        resolved_ws = ws_dir.resolve()
        central_dir = (wf_home / "workspaces" / _workspace_slug_hash(resolved_ws)).resolve()
        central_state = central_dir / "live_state.json"
        local_state = resolved_ws / ".agents" / "gravity-workflow" / "observability" / "live_state.json"
        if central_state.is_file() and not is_symlink_or_junction(central_state):
            return {"workspace_path": str(resolved_ws), "workspace": str(resolved_ws), "state_path": str(central_state), "updated_at": "", "exists": True, "source": "central_fallback"}, central_state, central_dir
        if local_state.is_file() and not is_symlink_or_junction(local_state):
            return {"workspace_path": str(resolved_ws), "workspace": str(resolved_ws), "state_path": str(local_state), "updated_at": "", "exists": True, "source": "local_fallback"}, local_state, (central_dir if central_dir.is_dir() else local_state.parent)
        return {"workspace_path": str(resolved_ws), "workspace": str(resolved_ws), "state_path": str(central_state), "updated_at": "", "exists": False, "source": "unresolved"}, None, (central_dir if central_dir.is_dir() else None)
    return None, None, None


def _read_runs_summary_ro(runs_file: Optional[Path], warnings: list[str]) -> dict[str, Any]:
    empty: dict[str, Any] = {"count": 0, "latest": None, "recent": [], "runs_path": str(runs_file) if runs_file else ""}
    if runs_file is None or is_symlink_or_junction(runs_file) or not runs_file.is_file():
        if runs_file is not None and is_symlink_or_junction(runs_file):
            warnings.append(f"Ignored symlink or junction runs.jsonl: {runs_file.name}.")
        return empty
    try:
        text = runs_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append(f"Failed to read runs.jsonl: {exc}")
        return empty
    entries: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        if not (line := raw_line.lstrip("\ufeff").strip()):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            warnings.append(f"Ignored corrupted JSONL line {idx} in runs.jsonl.")
            continue
        if isinstance(obj, dict):
            entries.append(obj)
        else:
            warnings.append(f"Ignored non-object JSONL line {idx} in runs.jsonl.")
    return {"count": len(entries), "latest": entries[-1] if entries else None, "recent": entries[-MAX_RECENT_RUNS:], "runs_path": str(runs_file)}


def _coerce_workflow_payload(raw_state: Optional[dict[str, Any]], raw_profile: Optional[dict[str, Any]]) -> dict[str, Any]:
    out = _default_workflow_fields()
    if isinstance(raw_profile, dict):
        if isinstance(raw_profile.get("mode"), str) and raw_profile["mode"].strip():
            out["mode"] = raw_profile["mode"].strip()
        if isinstance(raw_profile.get("current_level"), str) and raw_profile["current_level"].strip():
            out["level"] = out["preset_profile"] = raw_profile["current_level"].strip()
        if isinstance(raw_profile.get("suggested_profile"), str) and raw_profile["suggested_profile"].strip():
            out["suggested_profile"] = raw_profile["suggested_profile"].strip()
        if isinstance(raw_profile.get("last_reason"), str):
            out["suggested_reason"] = raw_profile["last_reason"]
        if isinstance(raw_profile.get("caveman"), str) and raw_profile["caveman"].strip():
            out["caveman"] = raw_profile["caveman"].strip()
        if "caveman_locked" in raw_profile:
            out["caveman_locked"] = bool(raw_profile.get("caveman_locked"))
    if not isinstance(raw_state, dict):
        return out
    for str_key in ("stage", "progress", "mode", "level", "preset_profile", "suggested_profile", "suggested_reason", "caveman", "guard_status"):
        val = raw_state.get(str_key)
        if (isinstance(val, str) and val != "") or (str_key == "suggested_reason" and isinstance(val, str)):
            out[str_key] = val
    if "caveman_locked" in raw_state:
        out["caveman_locked"] = bool(raw_state.get("caveman_locked"))
    for dict_key in ("role_models", "nodes"):
        if isinstance(raw_d := raw_state.get(dict_key), dict):
            out[dict_key] = {**out[dict_key], **{k: v for k, v in raw_d.items() if isinstance(k, str) and isinstance(v, str)}}
    if isinstance(raw_state.get("events"), list):
        out["events"] = [e for e in raw_state["events"][-MAX_EVENTS:] if isinstance(e, dict)]
    for col_key in ("waves", "tasks"):
        if isinstance(raw_state.get(col_key), (list, dict)):
            out[col_key] = raw_state[col_key]
    return out


def _enforce_payload_budget(payload: dict[str, Any]) -> dict[str, Any]:
    def _size() -> int:
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if _size() < MAX_PAYLOAD_BYTES:
        return payload
    payload["truncated"] = True
    for key in ("events", "workspaces"):
        items = payload.get(key)
        while _size() >= MAX_PAYLOAD_BYTES and isinstance(items, list) and items:
            items = items[: max(0, len(items) // 2)]
            payload[key] = items
    if isinstance(runs_obj := payload.get("runs"), dict) and isinstance(runs_obj.get("recent"), list):
        while _size() >= MAX_PAYLOAD_BYTES and runs_obj["recent"]:
            runs_obj["recent"] = runs_obj["recent"][1:]
    return payload


def get_workflow_view(
    ws: str = "", since_signature: str = "", *,
    gravity_home: Optional[Path] = None, workflow_home: Optional[Path] = None, registry_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Return read-only SPI-1 Gravity Workflow live state, runs summary, and signal_v1 enrichment."""
    eff_gravity = _resolve_gravity_home_opt(gravity_home)
    wf_home = _resolve_workflow_home(gravity_home=eff_gravity, workflow_home=workflow_home)
    reg_file = _resolve_registry_path(wf_home, registry_path=registry_path)
    warnings: list[str] = []
    if is_symlink_or_junction(wf_home):
        warnings.append(f"Refused symlink or junction workflow_home: {wf_home}.")
        sig = _short_digest([ws.strip(), str(wf_home), "symlink"])
        if since_signature and since_signature == sig:
            return {"changed": False, "unchanged": True, "signature": sig}
        return {
            "changed": True, "unchanged": False, "signature": sig, "available": False,
            "workflow_home": str(wf_home), "registry_path": str(reg_file), "workspaces": [], "selected_workspace": None,
            **_default_workflow_fields(), "runs": {"count": 0, "latest": None, "recent": [], "runs_path": ""},
            "signal_v1": {"available": False, "sample_count": 0}, "warnings": warnings,
        }

    workspaces = _load_registered_workspaces_ro(reg_file, warnings) or _scan_workspaces_dir_ro(wf_home, warnings)
    selected_workspace, selected_state_file, storage_dir = _select_workspace(ws, workspaces, wf_home)
    if selected_state_file is not None and (selected_state_file.name != "live_state.json" or is_symlink_or_junction(selected_state_file)):
        warnings.append(f"Refused unsafe or symlinked live_state.json: {selected_state_file}.")
        selected_state_file = None

    profile_file = (storage_dir / "profile.json") if storage_dir is not None else None
    runs_file = (storage_dir / "runs.jsonl") if storage_dir is not None else None
    target_ws_str = str((selected_workspace or {}).get("workspace_path") or ws or "").strip()
    signal_v1: dict[str, Any] = (
        _metrics_signals.collect_metrics_signals(Path(target_ws_str), gravity_home=eff_gravity)
        if (_metrics_signals is not None and target_ws_str) else {"available": False, "sample_count": 0}
    )

    sig = _short_digest([
        ws.strip(), str(wf_home), _stat_token(reg_file), _stat_token(selected_state_file),
        _stat_token(profile_file), _stat_token(runs_file),
        signal_v1.get("available", False), signal_v1.get("sample_count", 0), len(workspaces),
    ])
    if since_signature and since_signature == sig:
        return {"changed": False, "unchanged": True, "signature": sig}

    raw_state: Optional[dict[str, Any]] = None
    if selected_state_file is not None and selected_state_file.is_file():
        loaded = _read_json_ro(selected_state_file, warnings)
        if isinstance(loaded, dict):
            raw_state = loaded
        elif loaded is not None:
            warnings.append(f"Invalid structure in {selected_state_file.name}: expected JSON object.")

    raw_profile: Optional[dict[str, Any]] = None
    if profile_file is not None and profile_file.is_file():
        if isinstance(loaded_p := _read_json_ro(profile_file, warnings), dict):
            raw_profile = loaded_p
    elif target_ws_str:
        local_p = Path(target_ws_str).expanduser() / ".agents" / "gravity-workflow" / "profile.json"
        if not is_symlink_or_junction(local_p) and local_p.is_file() and isinstance(loaded_p := _read_json_ro(local_p, warnings), dict):
            raw_profile = loaded_p

    runs_summary = _read_runs_summary_ro(runs_file, warnings)
    fields = _coerce_workflow_payload(raw_state, raw_profile)
    payload: dict[str, Any] = {
        "changed": True, "unchanged": False, "signature": sig,
        "available": bool(raw_state is not None or raw_profile is not None or runs_summary["count"] > 0),
        "workflow_home": str(wf_home), "registry_path": str(reg_file),
        "workspaces": workspaces, "selected_workspace": selected_workspace,
        **fields, "runs": runs_summary, "signal_v1": signal_v1, "warnings": warnings,
    }
    return _enforce_payload_budget(payload)


def _extract_hook_script_rel(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if not tokens:
        return ""
    exe_stem = Path(tokens[0]).stem.lower()
    if exe_stem.startswith("python") or exe_stem == "py":
        for tok in tokens[1:]:
            if tok in ("-m", "-c"):
                return ""
            if not tok.startswith("-"):
                return tok
        return ""
    return tokens[0]


def _check_hooks_health_ro(plugin_root: Path, warnings: list[str]) -> None:
    if is_symlink_or_junction(plugin_root):
        warnings.append(f"Refused symlink or junction plugin_root: {plugin_root}.")
        return
    hooks_path = plugin_root / "hooks.json"
    if is_symlink_or_junction(hooks_path) or not hooks_path.is_file():
        warnings.append(f"Missing or symlinked hooks.json: {hooks_path}.")
        return
    hooks_data = _read_json_ro(hooks_path, warnings)
    if hooks_data is None:
        return
    if not isinstance(hooks_data, dict):
        warnings.append("Invalid structure in hooks.json: expected JSON object.")
        return
    for hook_name, cfg in hooks_data.items():
        if not isinstance(cfg, dict):
            continue
        for ev_key, matchers in cfg.items():
            if ev_key == "enabled" or not isinstance(matchers, list):
                continue
            for matcher_block in matchers:
                if not isinstance(matcher_block, dict):
                    continue
                hook_list = matcher_block.get("hooks")
                if not isinstance(hook_list, list):
                    continue
                for hook_item in hook_list:
                    if not isinstance(hook_item, dict):
                        continue
                    cmd = hook_item.get("command")
                    if not isinstance(cmd, str) or not cmd.strip():
                        warnings.append(f"Empty hook command in hooks.json ({hook_name}/{ev_key}).")
                        continue
                    rel_script = _extract_hook_script_rel(cmd)
                    if not rel_script:
                        warnings.append(f"Unparseable hook command in hooks.json ({hook_name}/{ev_key}): {cmd!r}.")
                        continue
                    script_cand = plugin_root / rel_script
                    try:
                        resolved_script = ensure_no_symlink_or_junction_under(plugin_root, script_cand)
                    except (OSError, ValueError):
                        warnings.append(f"Unsafe or symlinked hook script in hooks.json ({hook_name}): {rel_script}.")
                        continue
                    if not resolved_script.is_file():
                        warnings.append(f"Missing hook script referenced in hooks.json ({hook_name}): {rel_script}.")


def _check_json_object_ro(path: Path, label: str, warnings: list[str]) -> bool:
    if is_symlink_or_junction(path):
        warnings.append(f"Ignored symlink or junction {label}: {path.name}.")
        return False
    if not path.is_file():
        return False
    data = _read_json_ro(path, warnings)
    if isinstance(data, dict):
        return True
    if data is not None:
        warnings.append(f"Invalid structure in {label}: expected JSON object.")
    return False


def get_health_view(
    *,
    gravity_home: Optional[Path] = None,
    workflow_home: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    plugin_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Return read-only SPI-1 health report for Gravity Workflow hooks and runtime state."""
    eff_plugin_root = Path(plugin_root).expanduser().resolve() if plugin_root is not None else PLUGIN_ROOT
    eff_gravity = _resolve_gravity_home_opt(gravity_home)
    wf_home = _resolve_workflow_home(gravity_home=eff_gravity, workflow_home=workflow_home)
    reg_file = _resolve_registry_path(wf_home, registry_path=registry_path)
    warnings: list[str] = []

    _check_hooks_health_ro(eff_plugin_root, warnings)

    live_count = 0
    if is_symlink_or_junction(wf_home):
        warnings.append(f"Refused symlink or junction workflow_home: {wf_home}.")
    elif wf_home.is_dir():
        if is_symlink_or_junction(reg_file.parent) or is_symlink_or_junction(reg_file):
            warnings.append(f"Ignored symlink or junction live_workspaces.json: {reg_file}.")
        elif reg_file.is_file():
            reg_data = _read_json_ro(reg_file, warnings)
            if reg_data is not None and (not isinstance(reg_data, dict) or not isinstance(reg_data.get("workspaces"), list)):
                warnings.append("Invalid structure in live_workspaces.json: expected JSON object with 'workspaces' list.")

        ws_dir = wf_home / "workspaces"
        if is_symlink_or_junction(ws_dir):
            warnings.append(f"Ignored symlink or junction workspaces directory: {ws_dir}.")
        elif ws_dir.is_dir():
            try:
                subdirs = sorted(ws_dir.iterdir(), key=lambda p: p.name)
            except OSError as exc:
                warnings.append(f"Failed to scan workspaces directory: {exc}")
                subdirs = []
            for sub in subdirs:
                if is_symlink_or_junction(sub):
                    warnings.append(f"Ignored symlink or junction workspace directory: {sub.name}.")
                    continue
                if not sub.is_dir():
                    continue
                if _check_json_object_ro(sub / "live_state.json", f"{sub.name}/live_state.json", warnings):
                    live_count += 1
                runs_file = sub / "runs.jsonl"
                if is_symlink_or_junction(runs_file) or runs_file.is_file():
                    _read_runs_summary_ro(runs_file, warnings)
                _check_json_object_ro(sub / "profile.json", f"{sub.name}/profile.json", warnings)

    status = "WARN" if warnings else "OK"
    summary = f"{len(warnings)} warning(s)" if warnings else f"hooks.json OK · {live_count} live state(s)"
    return {"ok": True, "status": status, "summary": summary, "warnings": warnings}


def _read_request_payload() -> tuple[dict[str, Any], dict[str, Any]]:
    if not (raw := read_stdin_text(timeout_s=0.5)):
        return {}, {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}, {}
    if not isinstance(parsed, dict):
        return {}, {}
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    ctx = parsed.get("context") if isinstance(parsed.get("context"), dict) else {}
    return dict(params), dict(ctx)


def _opt_path(val: Any) -> Optional[Path]:
    return val if isinstance(val, Path) else (Path(val.strip()) if isinstance(val, str) and val.strip() else None)


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Read-only SPI-1 Gravity Workflow dashboard provider.")
    parser.add_argument("--view", default="workflow")
    parser.add_argument("--workspace", "--ws", dest="workspace", default="")
    parser.add_argument("--since-signature", default="")
    parser.add_argument("--action", default="")
    parser.add_argument("--gravity-home", type=Path, default=None)
    parser.add_argument("--workflow-home", type=Path, default=None)
    parser.add_argument("--registry-path", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.action:
        out: dict[str, Any] = {"ok": False, "action": args.action, "error": f"Unsupported action for module 'workflow': {args.action}"}
    elif args.view not in ("workflow", "health", ""):
        out = {"changed": True, "unchanged": False, "available": False, "error": f"Unknown view for module 'workflow': {args.view!r}", "warnings": [f"Unknown view: {args.view!r}"]}
    else:
        params, ctx = _read_request_payload()
        eff_gravity = args.gravity_home or _opt_path(ctx.get("gravity_home"))
        eff_wf_home = args.workflow_home or _opt_path(ctx.get("workflow_home"))
        eff_reg_path = args.registry_path or _opt_path(ctx.get("registry_path"))
        if args.view == "health":
            out = get_health_view(
                gravity_home=eff_gravity,
                workflow_home=eff_wf_home,
                registry_path=eff_reg_path,
            )
        else:
            out = get_workflow_view(
                ws=args.workspace or str(params.get("ws") or params.get("workspace") or ctx.get("ws") or ""),
                since_signature=args.since_signature or str(params.get("since_signature") or ""),
                gravity_home=eff_gravity,
                workflow_home=eff_wf_home,
                registry_path=eff_reg_path,
            )
    sys.stdout.write(json.dumps(out, indent=None if args.json else 2, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
