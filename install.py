#!/usr/bin/env python3
"""Cross-platform installer and uninstaller for Gravity Workflow (delegates to _shared.installer_core)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent
PLUGIN_SOURCE = REPO_ROOT / "plugin"
WORKSPACE_TEMPLATE_DIR = REPO_ROOT / "workspace-template"
PLUGIN_NAME = "gravity-workflow"
LEGACY_PLUGIN_NAME = "gravity"
LEGACY_REGISTRY_PATH = Path.home() / ".gravity" / "live_workspaces.json"

if str(PLUGIN_SOURCE) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SOURCE))

from _shared import installer_core  # noqa: E402
from _shared.atomic_io import write_json_atomic  # noqa: E402
from _shared.path_safety import is_symlink_or_junction, unlink_or_rmdir_link  # noqa: E402

EXEC_SCRIPTS: tuple[str, ...] = (
    "scripts/validate.py",
    "scripts/init_workspace.py",
    "scripts/dashboard_provider.py",
    "hooks/pre_tool_guard.py",
    "hooks/post_tool_format.py",
    "hooks/session_start_caveman.py",
    "hooks/stop_session_logger.py",
    "observability/statusline.py",
    "observability/profile_engine.py",
    "workflows/patch_apply.py",
    "workflows/swarm_wave.py",
)

has_real_python3 = installer_core.has_real_python3
read_plugin_manifest = installer_core.read_plugin_manifest
is_unsafe_target = installer_core.is_unsafe_target


def is_gravity_plugin_dir(plugin_dir: Path) -> bool:
    """Return True only when plugin_dir contains a Gravity Workflow plugin manifest."""
    manifest = read_plugin_manifest(plugin_dir)
    return bool(manifest is not None and manifest.get("name") == PLUGIN_NAME)


def is_core_gravity_plugin_dir(plugin_dir: Path) -> bool:
    """Return True when plugin_dir contains Gravity Core (config/module.json id=='core')."""
    module_path = plugin_dir / "config" / "module.json"
    if not module_path.is_file():
        return False
    try:
        data = json.loads(module_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return isinstance(data, dict) and data.get("id") == "core"


def is_legacy_gravity_plugin_dir(plugin_dir: Path) -> bool:
    """Return True only for a legacy v0.1.x 'gravity' workflow plugin (never Gravity Core)."""
    manifest = read_plugin_manifest(plugin_dir)
    if manifest is None or manifest.get("name") != LEGACY_PLUGIN_NAME or is_core_gravity_plugin_dir(plugin_dir):
        return False
    return any(
        (plugin_dir / rel).exists()
        for rel in ("hooks/pre_tool_guard.py", "hooks/safety_policy.py", "skills/gravity")
    )


def resolve_legacy_plugin_dir(scope: str, workspace: Optional[Path], target: Optional[Path]) -> Optional[Path]:
    """Compute the legacy 'gravity' plugin path corresponding to the requested install target."""
    if target is not None:
        resolved = Path(os.path.abspath(str(target.expanduser())))
        return resolved.parent / LEGACY_PLUGIN_NAME if resolved.name == PLUGIN_NAME else None
    if scope == "global":
        return Path(os.path.abspath(str(Path.home() / ".gemini" / "config" / "plugins" / LEGACY_PLUGIN_NAME)))
    if workspace is not None:
        return Path(os.path.abspath(str(workspace.expanduser().resolve() / ".agents" / "plugins" / LEGACY_PLUGIN_NAME)))
    return None


def resolve_target_dir(scope: str, workspace: Optional[Path], target: Optional[Path]) -> Path:
    """Compute the installation directory for copy-based installs."""
    return installer_core.resolve_target_dir(scope, workspace, target, plugin_name=PLUGIN_NAME)


def _load_state_writer() -> Any:
    obs_dir = PLUGIN_SOURCE / "observability"
    if str(obs_dir) not in sys.path:
        sys.path.insert(0, str(obs_dir))
    if "state_writer" in sys.modules:
        return sys.modules["state_writer"]
    spec = importlib.util.spec_from_file_location("state_writer", obs_dir / "state_writer.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load state_writer from {obs_dir / 'state_writer.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["state_writer"] = module
    spec.loader.exec_module(module)
    return module


def default_workflow_home() -> Path:
    """Resolve centralized Gravity Workflow storage."""
    return _load_state_writer().default_workflow_home()


def resolve_registry_path(registry_path: Optional[Path] = None) -> Path:
    """Resolve the workspace registry path (~/.gemini/gravity/workflow/live_workspaces.json)."""
    return _load_state_writer().resolve_registry_path(registry_path).expanduser().resolve()


def resolve_legacy_registry_path(legacy_registry_path: Optional[Path] = None) -> Path:
    """Resolve the legacy v0.1.x registry path (~/.gravity/live_workspaces.json)."""
    if legacy_registry_path is not None:
        return Path(legacy_registry_path).expanduser().resolve()
    env_reg = os.environ.get("GRAVITY_WORKFLOW_LEGACY_REGISTRY_PATH", "").strip()
    return Path(env_reg).expanduser().resolve() if env_reg else (Path.home() / ".gravity" / "live_workspaces.json").resolve()


def is_unsafe_workspace_target(ws_dir: Path) -> Optional[str]:
    """Return an error reason if ws_dir is unsafe to clean as a user workspace."""
    resolved, home_res, repo_res = ws_dir.expanduser().resolve(), Path.home().resolve(), REPO_ROOT.resolve()
    if resolved == resolved.parent:
        return f"Refusing unsafe filesystem root workspace: {resolved}"
    if resolved == home_res:
        return f"Refusing unsafe home directory workspace: {resolved}"
    if resolved == repo_res or repo_res in resolved.parents or resolved in repo_res.parents:
        return f"Refusing Gravity Workflow source repository path: {resolved}"
    return None


def _remove_tree_or_symlink(path: Path) -> None:
    if is_symlink_or_junction(path):
        unlink_or_rmdir_link(path)
    elif path.exists():
        shutil.rmtree(path)


def _remove_dir_if_empty(directory: Path) -> bool:
    try:
        if directory.is_dir() and not is_symlink_or_junction(directory) and not any(directory.iterdir()):
            directory.rmdir()
            return True
    except OSError:
        pass
    return False


def _remove_registry_files(reg_path: Path, label: str, *, dry_run: bool = False) -> bool:
    lock_path = reg_path.with_suffix(".lock")
    if not any(p.exists() or is_symlink_or_junction(p) for p in (reg_path, lock_path)):
        return False
    if dry_run:
        print(f"[DRY-RUN] Remove {label}: {reg_path}")
        return True
    for p in (reg_path, lock_path):
        if p.exists() or is_symlink_or_junction(p):
            p.unlink()
    _remove_dir_if_empty(reg_path.parent)
    print(f"[OK]    Removed {label}: {reg_path}")
    return True


def detect_manual_config_entries(home_dir: Optional[Path] = None) -> dict[str, bool]:
    """Read-only inspection of Antigravity config files for manual Gravity Workflow entries."""
    base_home = Path(home_dir).expanduser().resolve() if home_dir is not None else Path.home().resolve()
    has_sidecar, has_legacy_sidecar, has_statusline = False, False, False
    cfg_p = base_home / ".gemini" / "config" / "config.json"
    if cfg_p.is_file():
        try:
            cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and isinstance(cfg.get("sidecars"), dict):
                has_sidecar = "gravity-workflow/live-widget" in cfg["sidecars"]
                has_legacy_sidecar = "gravity/live-widget" in cfg["sidecars"]
        except (OSError, ValueError):
            pass
    st_p = base_home / ".gemini" / "antigravity-cli" / "settings.json"
    if st_p.is_file():
        try:
            st = json.loads(st_p.read_text(encoding="utf-8"))
            if isinstance(st, dict) and "statusLine" in st:
                raw_sl = json.dumps(st["statusLine"]).lower()
                has_statusline = "statusline.py" in raw_sl or "gravity" in raw_sl
        except (OSError, ValueError):
            pass
    return {"sidecar": has_sidecar, "legacy_sidecar": has_legacy_sidecar, "statusline": has_statusline}


def prune_workspace_from_registry(ws_dir: Path, *, registry_path: Optional[Path] = None, dry_run: bool = False) -> bool:
    """Remove ws_dir entries from live_workspaces.json without deleting other workspaces."""
    reg_path = resolve_registry_path(registry_path)
    if not reg_path.is_file():
        return False
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get("workspaces"), list):
        return False
    ws_res = ws_dir.expanduser().resolve()
    kept, removed = [], 0
    for entry in data["workspaces"]:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        matched = False
        for key, check_parents in (("workspace", False), ("state_path", True)):
            raw = entry.get(key)
            if isinstance(raw, str) and raw.strip():
                try:
                    p_res = Path(raw).expanduser().resolve()
                    if p_res == ws_res or (check_parents and ws_res in p_res.parents):
                        matched = True
                        break
                except OSError:
                    pass
        if matched:
            removed += 1
        else:
            kept.append(entry)
    if removed == 0:
        return False
    if dry_run:
        print(f"[DRY-RUN] Prune {removed} entry(ies) for {ws_res} from {reg_path}")
        return True
    if not kept:
        reg_path.unlink(missing_ok=True)
        reg_path.with_suffix(".lock").unlink(missing_ok=True)
        _remove_dir_if_empty(reg_path.parent)
    else:
        write_json_atomic(reg_path, {"workspaces": kept}, indent=2, ensure_ascii=False)
    print(f"[OK]    Pruned {ws_res} from registry ({reg_path})")
    return True


def clean_registry(*, registry_path: Optional[Path] = None, dry_run: bool = False) -> int:
    """Safely remove the global workspace registry."""
    reg_path = resolve_registry_path(registry_path)
    if not _remove_registry_files(reg_path, "global Gravity Workflow registry", dry_run=dry_run):
        print(f"[INFO]  Global registry not found: {reg_path}")
    return 0


def clean_legacy_artifacts(
    scope: str = "global", workspace: Optional[Path] = None, target: Optional[Path] = None,
    *, legacy_registry_path: Optional[Path] = None, dry_run: bool = False,
) -> int:
    """Safely remove obsolete v0.1.x 'gravity' plugin and ~/.gravity/live_workspaces.json registry."""
    legacy_dir = resolve_legacy_plugin_dir(scope, workspace, target)
    if legacy_dir is not None and (legacy_dir.exists() or is_symlink_or_junction(legacy_dir)):
        if (unsafe := is_unsafe_target(legacy_dir, PLUGIN_SOURCE)):
            return installer_core._err(unsafe)
        if is_core_gravity_plugin_dir(legacy_dir):
            print(f"[INFO]  Preserving Gravity Core plugin at {legacy_dir}")
        elif not is_legacy_gravity_plugin_dir(legacy_dir):
            return installer_core._err(f"Refusing to remove non-legacy-Gravity directory: {legacy_dir}")
        elif dry_run:
            print(f"[DRY-RUN] Remove legacy Gravity plugin directory: {legacy_dir}")
        else:
            _remove_tree_or_symlink(legacy_dir)
            print(f"[OK]    Removed legacy 'gravity' plugin from {legacy_dir}")
    _remove_registry_files(resolve_legacy_registry_path(legacy_registry_path), "legacy Gravity registry", dry_run=dry_run)
    return 0


def _load_canonical_templates() -> dict[str, str]:
    templates_root = PLUGIN_SOURCE / "templates"
    mapping = {
        "AGENTS.md": "AGENTS.md", ".agents/mcp_config.json": ".agents/mcp_config.json",
        ".agents/rules/01-safety-boundaries.md": ".agents/rules/01-safety-boundaries.md.tmpl",
        ".agents/rules/02-python-stdlib.md": ".agents/rules/02-python-stdlib.md",
        ".agents/rules/03-api-architecture.md": ".agents/rules/03-api-architecture.md",
        ".agents/rules/04-release-checklist.md": ".agents/rules/04-release-checklist.md",
        ".agents/rules/05-git-versioning.md": ".agents/rules/05-git-versioning.md",
        ".agents/rules/06-engineering-principles.md": ".agents/rules/06-engineering-principles.md",
    }
    return {k: (templates_root / v).read_text(encoding="utf-8") for k, v in mapping.items()}


def clean_workspace(
    ws_dir: Path, *, purge_templates: bool = False, clean_legacy: bool = False,
    registry_path: Optional[Path] = None, dry_run: bool = False,
) -> int:
    """Remove Gravity Workflow runtime state from a workspace without deleting user-modified files."""
    ws = ws_dir.expanduser().resolve()
    if (unsafe := is_unsafe_workspace_target(ws)):
        return installer_core._err(unsafe)
    if not ws.is_dir():
        return installer_core._err(f"Workspace directory not found: {ws}")
    ws_plugin = ws / ".agents" / "plugins" / PLUGIN_NAME
    if (ws_plugin.exists() or is_symlink_or_junction(ws_plugin)) and not is_gravity_plugin_dir(ws_plugin):
        return installer_core._err(f"Refusing to remove non-Gravity Workflow directory: {ws_plugin}")
    leg_ws_plugin = ws / ".agents" / "plugins" / LEGACY_PLUGIN_NAME
    if clean_legacy and (leg_ws_plugin.exists() or is_symlink_or_junction(leg_ws_plugin)):
        if not is_core_gravity_plugin_dir(leg_ws_plugin) and not is_legacy_gravity_plugin_dir(leg_ws_plugin):
            return installer_core._err(f"Refusing to remove non-legacy-Gravity directory: {leg_ws_plugin}")

    changed = False
    for target_p, label in ((ws / ".agents" / "gravity-workflow", "workspace Gravity Workflow state"), (ws_plugin, "workspace Gravity Workflow plugin")):
        if target_p.exists() or is_symlink_or_junction(target_p):
            changed = True
            print(f"[DRY-RUN] Remove {label}: {target_p}") if dry_run else (_remove_tree_or_symlink(target_p), print(f"[OK]    Removed {target_p}"))

    leg_state = ws / ".agents" / "gravity"
    if leg_state.exists() or is_symlink_or_junction(leg_state):
        if clean_legacy:
            changed = True
            print(f"[DRY-RUN] Remove legacy workspace Gravity state: {leg_state}") if dry_run else (_remove_tree_or_symlink(leg_state), print(f"[OK]    Removed legacy {leg_state}"))
        else:
            print(f"[INFO]  Legacy workspace state detected at {leg_state}; pass --clean-legacy to remove it.")

    if leg_ws_plugin.exists() or is_symlink_or_junction(leg_ws_plugin):
        if is_core_gravity_plugin_dir(leg_ws_plugin):
            if clean_legacy:
                print(f"[INFO]  Preserving Gravity Core workspace plugin at {leg_ws_plugin}")
        elif clean_legacy:
            changed = True
            print(f"[DRY-RUN] Remove legacy workspace Gravity plugin: {leg_ws_plugin}") if dry_run else (_remove_tree_or_symlink(leg_ws_plugin), print(f"[OK]    Removed legacy {leg_ws_plugin}"))
        elif is_legacy_gravity_plugin_dir(leg_ws_plugin):
            print(f"[INFO]  Legacy workspace plugin detected at {leg_ws_plugin}; pass --clean-legacy to remove it.")

    if purge_templates:
        for rel_path, expected_raw in _load_canonical_templates().items():
            cand = ws / rel_path
            if not cand.is_file():
                continue
            if cand.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip() == expected_raw.replace("\r\n", "\n").strip():
                changed = True
                print(f"[DRY-RUN] Remove unmodified template file: {cand}") if dry_run else (cand.unlink(), print(f"[OK]    Removed unmodified template: {rel_path}"))
            else:
                print(f"[KEEP]  {rel_path} (modified by user)")

    if not dry_run:
        for sub in (ws / ".agents" / "rules", ws / ".agents" / "plugins", ws / ".agents"):
            _remove_dir_if_empty(sub)
    if prune_workspace_from_registry(ws, registry_path=registry_path, dry_run=dry_run):
        changed = True
    if not changed:
        print(f"[INFO]  No Gravity Workflow artifacts found in {ws}")
    elif not dry_run:
        print(f"[OK]    Workspace cleaned: {ws}")
    return 0


def check_uninstall_status(
    target_dir: Optional[Path], scope: str, method: str, clean_ws: Optional[Path],
    *, registry_path: Optional[Path] = None, legacy_registry_path: Optional[Path] = None,
    home_dir: Optional[Path] = None, legacy_dir: Optional[Path] = None,
) -> int:
    """Read-only inspection of installed plugin, workspace state, registry, and manual configs."""
    print(f"Gravity Workflow Uninstall Check (read-only)\n  Method           : {method} (scope={scope})")
    if target_dir is not None:
        if (unsafe := is_unsafe_target(target_dir, PLUGIN_SOURCE)):
            print(f"  Plugin target    : UNSAFE ({unsafe})")
        elif target_dir.exists() or is_symlink_or_junction(target_dir):
            ver = (read_plugin_manifest(target_dir) or {}).get("version", "unknown")
            print(f"  Plugin target    : installed at {target_dir} (v{ver})" if is_gravity_plugin_dir(target_dir) else f"  Plugin target    : occupied by non-Gravity Workflow directory ({target_dir})")
        else:
            print(f"  Plugin target    : not installed ({target_dir})")
    else:
        print(f"  Antigravity CLI  : {shutil.which('agy') or 'not found on PATH'}")
    if legacy_dir is not None and (legacy_dir.exists() or is_symlink_or_junction(legacy_dir)):
        ver = (read_plugin_manifest(legacy_dir) or {}).get("version", "unknown")
        if is_legacy_gravity_plugin_dir(legacy_dir):
            print(f"  Legacy plugin    : present at {legacy_dir} (v{ver})")
        elif is_core_gravity_plugin_dir(legacy_dir):
            print(f"  Core plugin      : present at {legacy_dir} (v{ver}, preserved)")
    reg_p, leg_reg = resolve_registry_path(registry_path), resolve_legacy_registry_path(legacy_registry_path)
    print(f"  Live registry    : {'present' if reg_p.is_file() else 'absent'} ({reg_p})")
    if leg_reg.is_file():
        print(f"  Legacy registry  : present ({leg_reg})")
    if clean_ws is not None:
        ws = clean_ws.expanduser().resolve()
        if (ws_unsafe := is_unsafe_workspace_target(ws)):
            print(f"  Workspace target : UNSAFE ({ws_unsafe})")
        elif not ws.is_dir():
            print(f"  Workspace target : missing ({ws})")
        else:
            print(f"  Workspace target : {ws} (.agents/gravity-workflow={'yes' if (ws / '.agents/gravity-workflow').exists() else 'no'}, ws-plugin={'yes' if (ws / '.agents/plugins' / PLUGIN_NAME).exists() else 'no'})")
    manual = detect_manual_config_entries(home_dir=home_dir)
    print(f"  config.json      : sidecar 'gravity-workflow/live-widget' {'detected (manual removal needed)' if manual['sidecar'] else 'not detected'}")
    if manual["legacy_sidecar"]:
        print("  config.json      : legacy sidecar 'gravity/live-widget' detected (manual removal needed)")
    print(f"  settings.json    : statusLine Gravity Workflow {'detected (manual removal needed)' if manual['statusline'] else 'not detected'}")
    return 0


def print_post_uninstall_notes(home_dir: Optional[Path] = None) -> None:
    """Remind the user to manually remove optional entries from Antigravity configs."""
    manual = detect_manual_config_entries(home_dir=home_dir)
    print("\nManual cleanup notes (never edited automatically by Gravity Workflow):")
    print(f"  - Antigravity App sidecar{' [detected]' if manual['sidecar'] else ''}: remove 'sidecars.\"gravity-workflow/live-widget\"' from ~/.gemini/config/config.json if present.")
    if manual["legacy_sidecar"]:
        print("  - Legacy App sidecar [detected]: remove 'sidecars.\"gravity/live-widget\"' from ~/.gemini/config/config.json.")
    print(f"  - Antigravity CLI /statusline{' [detected]' if manual['statusline'] else ''}: remove 'statusLine' Gravity Workflow entry from ~/.gemini/antigravity-cli/settings.json if present.")


def cleanup_legacy_plugin(plugin_src: Path, legacy_dir: Optional[Path], *, dry_run: bool = False) -> bool:
    """Safely remove an obsolete v0.1.x 'gravity' plugin directory after installing gravity-workflow."""
    if legacy_dir is None or (not legacy_dir.exists() and not is_symlink_or_junction(legacy_dir)):
        return False
    if (unsafe := is_unsafe_target(legacy_dir, plugin_src)):
        print(f"[WARN]  Skipping legacy plugin cleanup ({unsafe}).")
        return False
    if not is_legacy_gravity_plugin_dir(legacy_dir):
        return False
    if dry_run:
        print(f"[DRY-RUN] Remove legacy Gravity plugin directory: {legacy_dir}")
        return True
    try:
        _remove_tree_or_symlink(legacy_dir)
        print(f"[OK]    Removed legacy 'gravity' plugin from {legacy_dir}")
        return True
    except OSError as exc:
        if installer_core.is_lock_oserror(exc):
            print(f"[WARN]  Could not remove legacy 'gravity' plugin {legacy_dir} (in use: {exc}).")
            return False
        raise


def bootstrap_workspace(
    plugin_src: Path, target_dir: Optional[Path], method: str, ws_dir: Path,
    *, mode: str = "full", force_workspace: bool = False, skip_validate: bool = False, dry_run: bool = False,
) -> int:
    """Initialize and validate a Gravity Workflow project workspace."""
    ws = ws_dir.expanduser().resolve()
    base_dir = target_dir if method == "copy" and target_dir is not None and target_dir.is_dir() else plugin_src
    init_cmd = [sys.executable, "-B", str(base_dir / "scripts" / "init_workspace.py"), "--workspace", str(ws), "--mode", mode] + (["--force"] if force_workspace else [])
    val_cmd = [sys.executable, "-B", str(base_dir / "scripts" / "validate.py"), "--workspace", str(ws), "--strict"]
    if dry_run:
        print(f"[DRY-RUN] {' '.join(init_cmd)}")
        if not skip_validate:
            print(f"[DRY-RUN] {' '.join(val_cmd)}")
        return 0
    if not installer_core._proc_ok(installer_core._run_subprocess(init_cmd, dont_write_bytecode=True), f"Workspace initialization failed for {ws}."):
        return 1
    if not skip_validate and not installer_core._proc_ok(installer_core._run_subprocess(val_cmd, dont_write_bytecode=True), f"Workspace validation failed for {ws}."):
        return 1
    print(f"[OK]    Workspace ready: {ws} (mode={mode})")
    return 0


def _add_workflow_cli_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--init-workspace", type=Path, default=None, help="Initialize Gravity Workflow baseline in this workspace.")
    parser.add_argument("--mode", choices=("full", "quickstart"), default="full", help="Workspace initialization mode.")
    parser.add_argument("--force-workspace", action="store_true", help="Overwrite existing project files with --init-workspace.")
    parser.add_argument("--keep-legacy", action="store_true", help="Keep legacy v0.1.x 'gravity' plugin directory.")
    parser.add_argument("--clean-workspace", type=Path, default=None, help="Remove .agents/gravity-workflow/ and prune registry entry.")
    parser.add_argument("--purge-workspace-templates", action="store_true", help="With --clean-workspace, remove unmodified templates.")
    parser.add_argument("--workspace-only", action="store_true", help="Skip plugin removal and only run workspace/registry/legacy cleanup.")
    parser.add_argument("--clean-registry", action="store_true", help="Remove live_workspaces.json.")
    parser.add_argument("--clean-legacy", action="store_true", help="Remove legacy v0.1.x 'gravity' artifacts.")
    parser.add_argument("--all", action="store_true", help="With --uninstall, remove plugin, registries, and legacy artifacts.")


def _run_uninstall_extras(args: argparse.Namespace, _target_dir: Optional[Path]) -> int:
    clean_leg = bool(args.clean_legacy or args.all)
    if args.clean_workspace is not None and clean_workspace(args.clean_workspace, purge_templates=args.purge_workspace_templates, clean_legacy=clean_leg, dry_run=args.dry_run) != 0:
        return 1
    if (args.clean_registry or args.all) and clean_registry(dry_run=args.dry_run) != 0:
        return 1
    if clean_leg and clean_legacy_artifacts(scope=args.scope, workspace=args.workspace, target=args.target, dry_run=args.dry_run) != 0:
        return 1
    if not args.dry_run:
        if not args.workspace_only and args.clean_workspace is None and not args.clean_registry and not args.all:
            print("[NOTE]  Use 'python install.py --workspace-only --clean-workspace <dir> / --clean-registry' to clean project state.")
        print_post_uninstall_notes()
    return 0


def _dispatch_workflow_modes(args: argparse.Namespace, spec: installer_core.InstallSpec, target_dir: Optional[Path]) -> Optional[int]:
    if args.purge_workspace_templates and args.clean_workspace is None:
        return installer_core._err("--purge-workspace-templates requires --clean-workspace PATH.", 2)
    if args.workspace_only and args.all:
        return installer_core._err("--workspace-only cannot be combined with --all.", 2)
    if args.workspace_only and args.clean_workspace is None and not args.clean_registry and not args.clean_legacy:
        return installer_core._err("--workspace-only requires --clean-workspace, --clean-registry and/or --clean-legacy.", 2)
    is_uninst = bool(args.uninstall or args.workspace_only)
    if is_uninst and args.init_workspace is not None:
        return installer_core._err("--uninstall / --workspace-only cannot be combined with --init-workspace.", 2)
    if not is_uninst and (args.clean_workspace is not None or args.purge_workspace_templates or args.clean_registry or args.clean_legacy or args.all):
        return installer_core._err("Cleanup options require --uninstall or --workspace-only.", 2)
    legacy_dir = resolve_legacy_plugin_dir(args.scope, args.workspace, args.target) if args.method == "copy" else None
    if args.check:
        if is_uninst:
            return check_uninstall_status(target_dir, args.scope, args.method, args.clean_workspace, legacy_dir=legacy_dir)
        rc = installer_core.check_environment(spec, target_dir, args.scope, args.method, strict=args.strict)
        if legacy_dir is not None and (legacy_dir.exists() or is_symlink_or_junction(legacy_dir)):
            ver = (read_plugin_manifest(legacy_dir) or {}).get("version", "unknown")
            if is_legacy_gravity_plugin_dir(legacy_dir):
                print(f"  Legacy plugin    : detected at {legacy_dir} (v{ver}) [will be removed on install unless --keep-legacy]")
            elif is_core_gravity_plugin_dir(legacy_dir):
                print(f"  Core plugin      : detected at {legacy_dir} (v{ver}) [preserved]")
        return rc
    if args.workspace_only:
        return _run_uninstall_extras(args, target_dir)
    if args.uninstall and args.method == "copy" and target_dir is not None and is_symlink_or_junction(target_dir):
        if (unsafe := is_unsafe_target(target_dir, PLUGIN_SOURCE)):
            return installer_core._err(unsafe)
        if not is_gravity_plugin_dir(target_dir):
            return installer_core._err(f"Refusing to remove non-Gravity Workflow directory: {target_dir}")
        if args.dry_run:
            print(f"[DRY-RUN] Remove Gravity Workflow plugin symlink: {target_dir}")
            return 0
        _remove_tree_or_symlink(target_dir)
        print(f"[OK]    Removed Gravity Workflow plugin from {target_dir}")
        return _run_uninstall_extras(args, target_dir)
    return None


def _post_install_workflow(args: argparse.Namespace, _spec: installer_core.InstallSpec, target_dir: Optional[Path]) -> int:
    args._legacy_kept = False
    if args.method == "copy" and (legacy_dir := resolve_legacy_plugin_dir(args.scope, args.workspace, args.target)) is not None:
        if args.keep_legacy:
            if (legacy_dir.exists() or is_symlink_or_junction(legacy_dir)) and is_legacy_gravity_plugin_dir(legacy_dir):
                args._legacy_kept = True
                print(f"[INFO]  Keeping legacy 'gravity' plugin at {legacy_dir} (--keep-legacy).")
        else:
            cleanup_legacy_plugin(PLUGIN_SOURCE, legacy_dir, dry_run=args.dry_run)
    if args.init_workspace is not None:
        eff_skip = bool(args.skip_validate and not args.strict)
        return bootstrap_workspace(PLUGIN_SOURCE, target_dir, args.method, args.init_workspace, mode=args.mode, force_workspace=args.force_workspace, skip_validate=eff_skip, dry_run=args.dry_run)
    return 0


def _print_next_steps(target_dir: Optional[Path], args: argparse.Namespace) -> None:
    print("\nNext steps:")
    if target_dir is not None:
        print(f"  - Installed plugin location: {target_dir}")
    print("  - In Antigravity App or IDE, verify Gravity Workflow under Customizations -> Installed.")
    if args.init_workspace is not None:
        print(f"  - Initialized project workspace: {args.init_workspace}")
    else:
        print("  - Run: python install.py --init-workspace <target-project-dir>")
    print("  - Invoke /gravity dashboard (from 'gravity' Core) to view live workflow state.")
    if getattr(args, "_legacy_kept", False) or resolve_legacy_registry_path().is_file():
        print("  - Legacy migration: run 'python install.py --workspace-only --clean-legacy'.")


SPEC = installer_core.InstallSpec(
    plugin_name=PLUGIN_NAME,
    display_name="Gravity Workflow",
    plugin_source=PLUGIN_SOURCE,
    repo_root=REPO_ROOT,
    exec_scripts=EXEC_SCRIPTS,
    has_executable_manifest=True,
    installed_check_fn=is_gravity_plugin_dir,
    legacy_check_fn=is_legacy_gravity_plugin_dir,
    extra_cli_fn=_add_workflow_cli_flags,
    extra_dispatch_fn=_dispatch_workflow_modes,
    post_install_fn=_post_install_workflow,
    post_uninstall_fn=lambda args, _spec, tdir: _run_uninstall_extras(args, tdir),
    next_steps_fn=_print_next_steps,
    agy_strict_default=False,
    record_sources=True,
)


def run_plugin_validation(plugin_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run strict validator against a staged or installed plugin directory."""
    return installer_core.run_plugin_validation(plugin_dir, SPEC)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the Gravity Workflow installer and uninstaller CLI."""
    return installer_core.run_installer(SPEC, argv)


if __name__ == "__main__":
    raise SystemExit(main())
