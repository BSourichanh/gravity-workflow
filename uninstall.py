#!/usr/bin/env python3
"""Cross-platform uninstaller and workspace cleaner for Gravity Workflow (100% Python 3 stdlib)."""

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import install  # noqa: E402

PLUGIN_NAME = install.PLUGIN_NAME
LEGACY_PLUGIN_NAME = install.LEGACY_PLUGIN_NAME
PLUGIN_SOURCE = install.PLUGIN_SOURCE
WORKSPACE_TEMPLATE_DIR = REPO_ROOT / "workspace-template"

_OBS_DIR = PLUGIN_SOURCE / "observability"
if str(_OBS_DIR) not in sys.path:
    sys.path.insert(0, str(_OBS_DIR))

import state_writer  # noqa: E402

DEFAULT_REGISTRY_PATH = state_writer.default_workflow_home() / "live_workspaces.json"
LEGACY_REGISTRY_PATH = Path.home() / ".gravity" / "live_workspaces.json"


def default_workflow_home() -> Path:
    """Resolve centralized Gravity Workflow storage ($GRAVITY_WORKFLOW_HOME > $GRAVITY_HOME/workflow > ~/.gemini/gravity/workflow)."""
    return state_writer.default_workflow_home()


def is_unsafe_workspace_target(ws_dir: Path) -> Optional[str]:
    """Return an error reason if ws_dir is unsafe to clean as a user workspace."""
    resolved = ws_dir.expanduser().resolve()
    home_resolved = Path.home().resolve()
    repo_resolved = REPO_ROOT.resolve()

    if resolved == resolved.parent:
        return f"Refusing unsafe filesystem root workspace: {resolved}"
    if resolved == home_resolved:
        return f"Refusing unsafe home directory workspace: {resolved}"
    if resolved == repo_resolved or repo_resolved in resolved.parents:
        return f"Refusing Gravity Workflow source repository path: {resolved}"
    return None


def _load_canonical_templates() -> Dict[str, str]:
    """Load canonical starter file contents from plugin/scripts/init_workspace.py."""
    init_path = PLUGIN_SOURCE / "scripts" / "init_workspace.py"
    spec = importlib.util.spec_from_file_location("gravity_workflow_init_workspace_tpl", init_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load template definitions from {init_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        "AGENTS.md": str(module.AGENTS_MD),
        ".agents/mcp_config.json": str(module.MCP_CONFIG_JSON),
        ".agents/rules/01-safety-boundaries.md": str(module.RULE_01_SAFETY),
        ".agents/rules/02-python-stdlib.md": str(module.RULE_02_PYTHON),
        ".agents/rules/03-api-architecture.md": str(module.RULE_03_API),
        ".agents/rules/04-release-checklist.md": str(module.RULE_04_RELEASE),
        ".agents/rules/05-git-versioning.md": str(module.RULE_05_GIT_VERSIONING),
    }


def resolve_registry_path(registry_path: Optional[Path] = None) -> Path:
    """Resolve the Live Widget workspace registry path (~/.gemini/gravity/workflow/live_workspaces.json)."""
    return state_writer.resolve_registry_path(registry_path).expanduser().resolve()


def resolve_legacy_registry_path(legacy_registry_path: Optional[Path] = None) -> Path:
    """Resolve the legacy v0.1.x registry path (~/.gravity/live_workspaces.json)."""
    if legacy_registry_path is not None:
        return Path(legacy_registry_path).expanduser().resolve()
    env_reg = os.environ.get("GRAVITY_WORKFLOW_LEGACY_REGISTRY_PATH", "").strip()
    if env_reg:
        return Path(env_reg).expanduser().resolve()
    return LEGACY_REGISTRY_PATH.resolve()


def _atomic_write_json(target: Path, payload: Dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.stem}_", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_fh:
            json.dump(payload, tmp_fh, indent=2, ensure_ascii=False)
            tmp_fh.write("\n")
            tmp_fh.flush()
            os.fsync(tmp_fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _remove_dir_if_empty(directory: Path) -> bool:
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            return True
    except OSError:
        pass
    return False


def detect_manual_config_entries(home_dir: Optional[Path] = None) -> Dict[str, bool]:
    """Read-only inspection of Antigravity config files for manual Gravity Workflow entries."""
    base_home = Path(home_dir).expanduser().resolve() if home_dir is not None else Path.home().resolve()
    has_sidecar = False
    has_legacy_sidecar = False
    has_statusline = False

    config_path = base_home / ".gemini" / "config" / "config.json"
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and isinstance(cfg.get("sidecars"), dict):
                has_sidecar = "gravity-workflow/live-widget" in cfg["sidecars"]
                has_legacy_sidecar = "gravity/live-widget" in cfg["sidecars"]
        except (OSError, ValueError):
            pass

    settings_path = base_home / ".gemini" / "antigravity-cli" / "settings.json"
    if settings_path.is_file():
        try:
            st = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(st, dict) and "statusLine" in st:
                raw_sl = json.dumps(st["statusLine"]).lower()
                has_statusline = "statusline.py" in raw_sl or "gravity" in raw_sl
        except (OSError, ValueError):
            pass

    return {
        "sidecar": has_sidecar,
        "legacy_sidecar": has_legacy_sidecar,
        "statusline": has_statusline,
    }


def prune_workspace_from_registry(
    ws_dir: Path,
    *,
    registry_path: Optional[Path] = None,
    dry_run: bool = False,
) -> bool:
    """Remove ws_dir entries from ~/.gemini/gravity/workflow/live_workspaces.json without deleting others."""
    reg_path = resolve_registry_path(registry_path)
    if not reg_path.is_file():
        return False

    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    if not isinstance(data, dict) or not isinstance(data.get("workspaces"), list):
        return False

    ws_resolved = ws_dir.expanduser().resolve()
    kept: List[Any] = []
    removed = 0
    for entry in data["workspaces"]:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        matched = False
        raw_ws = entry.get("workspace")
        if isinstance(raw_ws, str) and raw_ws.strip():
            try:
                if Path(raw_ws).expanduser().resolve() == ws_resolved:
                    matched = True
            except OSError:
                pass
        raw_state = entry.get("state_path")
        if not matched and isinstance(raw_state, str) and raw_state.strip():
            try:
                state_resolved = Path(raw_state).expanduser().resolve()
                if state_resolved == ws_resolved or ws_resolved in state_resolved.parents:
                    matched = True
            except OSError:
                pass
        if matched:
            removed += 1
        else:
            kept.append(entry)

    if removed == 0:
        return False

    if dry_run:
        print(f"[DRY-RUN] Prune {removed} entry(ies) for {ws_resolved} from {reg_path}")
        return True

    if not kept:
        reg_path.unlink(missing_ok=True)
        reg_path.with_suffix(".lock").unlink(missing_ok=True)
        _remove_dir_if_empty(reg_path.parent)
    else:
        _atomic_write_json(reg_path, {"workspaces": kept})
    print(f"[OK]    Pruned {ws_resolved} from registry ({reg_path})")
    return True


def clean_registry(
    *,
    registry_path: Optional[Path] = None,
    dry_run: bool = False,
) -> int:
    """Safely remove the global Live Widget workspace registry (~/.gemini/gravity/workflow/live_workspaces.json)."""
    reg_path = resolve_registry_path(registry_path)
    lock_path = reg_path.with_suffix(".lock")
    exists = (
        reg_path.exists()
        or reg_path.is_symlink()
        or lock_path.exists()
        or lock_path.is_symlink()
    )
    if not exists:
        print(f"[INFO]  Global registry not found: {reg_path}")
        return 0

    if dry_run:
        print(f"[DRY-RUN] Remove global Gravity Workflow registry: {reg_path}")
        return 0

    if reg_path.exists() or reg_path.is_symlink():
        reg_path.unlink()
    if lock_path.exists() or lock_path.is_symlink():
        lock_path.unlink()
    _remove_dir_if_empty(reg_path.parent)
    print(f"[OK]    Removed global Gravity Workflow registry: {reg_path}")
    return 0


def clean_legacy_artifacts(
    scope: str = "global",
    workspace: Optional[Path] = None,
    target: Optional[Path] = None,
    *,
    legacy_registry_path: Optional[Path] = None,
    dry_run: bool = False,
) -> int:
    """Safely remove obsolete v0.1.x 'gravity' plugin and ~/.gravity/live_workspaces.json registry."""
    legacy_dir = install.resolve_legacy_plugin_dir(scope, workspace, target)
    if legacy_dir is not None and (legacy_dir.exists() or legacy_dir.is_symlink()):
        unsafe_reason = install.is_unsafe_target(legacy_dir, PLUGIN_SOURCE)
        if unsafe_reason:
            print(f"[ERROR] {unsafe_reason}", file=sys.stderr)
            return 1
        if install.is_core_gravity_plugin_dir(legacy_dir):
            print(f"[INFO]  Preserving Gravity Core plugin at {legacy_dir}")
        elif not install.is_legacy_gravity_plugin_dir(legacy_dir):
            print(
                f"[ERROR] Refusing to remove non-legacy-Gravity directory: {legacy_dir}",
                file=sys.stderr,
            )
            return 1
        elif dry_run:
            print(f"[DRY-RUN] Remove legacy Gravity plugin directory: {legacy_dir}")
        else:
            install._remove_tree_or_symlink(legacy_dir)
            print(f"[OK]    Removed legacy 'gravity' plugin from {legacy_dir}")

    leg_reg = resolve_legacy_registry_path(legacy_registry_path)
    leg_lock = leg_reg.with_suffix(".lock")
    reg_exists = (
        leg_reg.exists()
        or leg_reg.is_symlink()
        or leg_lock.exists()
        or leg_lock.is_symlink()
    )
    if reg_exists:
        if dry_run:
            print(f"[DRY-RUN] Remove legacy Gravity registry: {leg_reg}")
        else:
            if leg_reg.exists() or leg_reg.is_symlink():
                leg_reg.unlink()
            if leg_lock.exists() or leg_lock.is_symlink():
                leg_lock.unlink()
            _remove_dir_if_empty(leg_reg.parent)
            print(f"[OK]    Removed legacy Gravity registry: {leg_reg}")
    return 0


def clean_workspace(
    ws_dir: Path,
    *,
    purge_templates: bool = False,
    clean_legacy: bool = False,
    registry_path: Optional[Path] = None,
    dry_run: bool = False,
) -> int:
    """Remove Gravity Workflow runtime state from a workspace without deleting user-modified files."""
    resolved_ws = ws_dir.expanduser().resolve()
    unsafe_reason = is_unsafe_workspace_target(resolved_ws)
    if unsafe_reason:
        print(f"[ERROR] {unsafe_reason}", file=sys.stderr)
        return 1

    if not resolved_ws.is_dir():
        print(f"[ERROR] Workspace directory not found: {resolved_ws}", file=sys.stderr)
        return 1

    ws_plugin_dir = resolved_ws / ".agents" / "plugins" / PLUGIN_NAME
    if ws_plugin_dir.exists() or ws_plugin_dir.is_symlink():
        if not install.is_gravity_plugin_dir(ws_plugin_dir):
            print(
                f"[ERROR] Refusing to remove non-Gravity Workflow directory: {ws_plugin_dir}",
                file=sys.stderr,
            )
            return 1

    legacy_ws_plugin_dir = resolved_ws / ".agents" / "plugins" / LEGACY_PLUGIN_NAME
    if clean_legacy and (legacy_ws_plugin_dir.exists() or legacy_ws_plugin_dir.is_symlink()):
        if not install.is_core_gravity_plugin_dir(
            legacy_ws_plugin_dir
        ) and not install.is_legacy_gravity_plugin_dir(legacy_ws_plugin_dir):
            print(
                f"[ERROR] Refusing to remove non-legacy-Gravity directory: {legacy_ws_plugin_dir}",
                file=sys.stderr,
            )
            return 1

    gravity_state_dir = resolved_ws / ".agents" / "gravity-workflow"
    legacy_state_dir = resolved_ws / ".agents" / "gravity"
    changed = False

    if gravity_state_dir.exists() or gravity_state_dir.is_symlink():
        changed = True
        if dry_run:
            print(f"[DRY-RUN] Remove workspace Gravity Workflow state: {gravity_state_dir}")
        else:
            install._remove_tree_or_symlink(gravity_state_dir)
            print(f"[OK]    Removed {gravity_state_dir}")

    if ws_plugin_dir.exists() or ws_plugin_dir.is_symlink():
        changed = True
        if dry_run:
            print(f"[DRY-RUN] Remove workspace Gravity Workflow plugin: {ws_plugin_dir}")
        else:
            install._remove_tree_or_symlink(ws_plugin_dir)
            print(f"[OK]    Removed {ws_plugin_dir}")

    if legacy_state_dir.exists() or legacy_state_dir.is_symlink():
        if clean_legacy:
            changed = True
            if dry_run:
                print(f"[DRY-RUN] Remove legacy workspace Gravity state: {legacy_state_dir}")
            else:
                install._remove_tree_or_symlink(legacy_state_dir)
                print(f"[OK]    Removed legacy {legacy_state_dir}")
        else:
            print(
                f"[INFO]  Legacy workspace state detected at {legacy_state_dir}; "
                "pass --clean-legacy to remove it."
            )

    if legacy_ws_plugin_dir.exists() or legacy_ws_plugin_dir.is_symlink():
        if install.is_core_gravity_plugin_dir(legacy_ws_plugin_dir):
            if clean_legacy:
                print(f"[INFO]  Preserving Gravity Core workspace plugin at {legacy_ws_plugin_dir}")
        elif clean_legacy:
            changed = True
            if dry_run:
                print(f"[DRY-RUN] Remove legacy workspace Gravity plugin: {legacy_ws_plugin_dir}")
            else:
                install._remove_tree_or_symlink(legacy_ws_plugin_dir)
                print(f"[OK]    Removed legacy {legacy_ws_plugin_dir}")
        elif install.is_legacy_gravity_plugin_dir(legacy_ws_plugin_dir):
            print(
                f"[INFO]  Legacy workspace plugin detected at {legacy_ws_plugin_dir}; "
                "pass --clean-legacy to remove it."
            )

    if purge_templates:
        canonical = _load_canonical_templates()
        for rel_path, expected_raw in canonical.items():
            candidate = resolved_ws / rel_path
            if not candidate.is_file():
                continue
            current_norm = candidate.read_text(encoding="utf-8", errors="replace").replace(
                "\r\n", "\n"
            ).strip()
            expected_norm = expected_raw.replace("\r\n", "\n").strip()
            if current_norm == expected_norm:
                changed = True
                if dry_run:
                    print(f"[DRY-RUN] Remove unmodified template file: {candidate}")
                else:
                    candidate.unlink()
                    print(f"[OK]    Removed unmodified template: {rel_path}")
            else:
                print(f"[KEEP]  {rel_path} (modified by user)")

    if not dry_run:
        _remove_dir_if_empty(resolved_ws / ".agents" / "rules")
        _remove_dir_if_empty(resolved_ws / ".agents" / "plugins")
        _remove_dir_if_empty(resolved_ws / ".agents")

    if prune_workspace_from_registry(resolved_ws, registry_path=registry_path, dry_run=dry_run):
        changed = True

    if not changed:
        print(f"[INFO]  No Gravity Workflow artifacts found in {resolved_ws}")
    elif not dry_run:
        print(f"[OK]    Workspace cleaned: {resolved_ws}")

    return 0


def check_uninstall_status(
    target_dir: Optional[Path],
    scope: str,
    method: str,
    clean_ws: Optional[Path],
    *,
    registry_path: Optional[Path] = None,
    legacy_registry_path: Optional[Path] = None,
    home_dir: Optional[Path] = None,
    legacy_dir: Optional[Path] = None,
) -> int:
    """Read-only inspection of installed plugin, workspace state, registry, and manual configs."""
    print("Gravity Workflow Uninstall Check (read-only)")
    print(f"  Method           : {method} (scope={scope})")

    if target_dir is not None:
        unsafe_reason = install.is_unsafe_target(target_dir, PLUGIN_SOURCE)
        if unsafe_reason:
            print(f"  Plugin target    : UNSAFE ({unsafe_reason})")
        elif target_dir.exists() or target_dir.is_symlink():
            if install.is_gravity_plugin_dir(target_dir):
                manifest = install.read_plugin_manifest(target_dir) or {}
                ver = manifest.get("version", "unknown")
                print(f"  Plugin target    : installed at {target_dir} (v{ver})")
            else:
                print(f"  Plugin target    : occupied by non-Gravity Workflow directory ({target_dir})")
        else:
            print(f"  Plugin target    : not installed ({target_dir})")
    else:
        agy_bin = shutil.which("agy")
        print(f"  Antigravity CLI  : {agy_bin if agy_bin else 'not found on PATH'}")

    if legacy_dir is not None and (legacy_dir.exists() or legacy_dir.is_symlink()):
        if install.is_legacy_gravity_plugin_dir(legacy_dir):
            leg_manifest = install.read_plugin_manifest(legacy_dir) or {}
            leg_ver = leg_manifest.get("version", "unknown")
            print(f"  Legacy plugin    : present at {legacy_dir} (v{leg_ver})")
        elif install.is_core_gravity_plugin_dir(legacy_dir):
            core_manifest = install.read_plugin_manifest(legacy_dir) or {}
            core_ver = core_manifest.get("version", "unknown")
            print(f"  Core plugin      : present at {legacy_dir} (v{core_ver}, preserved)")

    reg_path = resolve_registry_path(registry_path)
    if reg_path.is_file():
        print(f"  Live registry    : present ({reg_path})")
    else:
        print(f"  Live registry    : absent ({reg_path})")

    leg_reg = resolve_legacy_registry_path(legacy_registry_path)
    if leg_reg.is_file():
        print(f"  Legacy registry  : present ({leg_reg})")

    if clean_ws is not None:
        resolved_ws = clean_ws.expanduser().resolve()
        ws_unsafe = is_unsafe_workspace_target(resolved_ws)
        if ws_unsafe:
            print(f"  Workspace target : UNSAFE ({ws_unsafe})")
        elif not resolved_ws.is_dir():
            print(f"  Workspace target : missing ({resolved_ws})")
        else:
            has_state = (resolved_ws / ".agents" / "gravity-workflow").exists()
            has_ws_plugin = (resolved_ws / ".agents" / "plugins" / PLUGIN_NAME).exists()
            has_legacy_state = (resolved_ws / ".agents" / "gravity").exists()
            print(
                f"  Workspace target : {resolved_ws} "
                f"(.agents/gravity-workflow={'yes' if has_state else 'no'}, "
                f"ws-plugin={'yes' if has_ws_plugin else 'no'}"
                f"{', legacy=.agents/gravity' if has_legacy_state else ''})"
            )

    manual = detect_manual_config_entries(home_dir=home_dir)
    print(
        f"  config.json      : sidecar 'gravity-workflow/live-widget' "
        f"{'detected (manual removal needed)' if manual['sidecar'] else 'not detected'}"
    )
    if manual["legacy_sidecar"]:
        print("  config.json      : legacy sidecar 'gravity/live-widget' detected (manual removal needed)")
    print(
        f"  settings.json    : statusLine Gravity Workflow "
        f"{'detected (manual removal needed)' if manual['statusline'] else 'not detected'}"
    )
    return 0


def print_post_uninstall_notes(home_dir: Optional[Path] = None) -> None:
    """Remind the user to manually remove optional entries from Antigravity configs."""
    manual = detect_manual_config_entries(home_dir=home_dir)
    print("\nManual cleanup notes (never edited automatically by Gravity Workflow):")
    sidecar_status = " [detected]" if manual["sidecar"] else ""
    legacy_sidecar_status = " [detected]" if manual["legacy_sidecar"] else ""
    statusline_status = " [detected]" if manual["statusline"] else ""
    print(
        f"  - Antigravity App sidecar{sidecar_status}: remove 'sidecars.\"gravity-workflow/live-widget\"' "
        "from ~/.gemini/config/config.json if present."
    )
    if manual["legacy_sidecar"]:
        print(
            f"  - Legacy App sidecar{legacy_sidecar_status}: remove 'sidecars.\"gravity/live-widget\"' "
            "from ~/.gemini/config/config.json."
        )
    print(
        f"  - Antigravity CLI /statusline{statusline_status}: remove 'statusLine' Gravity Workflow entry "
        "from ~/.gemini/antigravity-cli/settings.json if present."
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for the Gravity Workflow uninstaller."""
    parser = argparse.ArgumentParser(
        description="Safely uninstall the Gravity Workflow plugin, workspace state, and Live Widget registry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python uninstall.py --check\n"
            "  python uninstall.py --dry-run\n"
            "  python uninstall.py\n"
            "  python uninstall.py --scope workspace --workspace /path/to/project\n"
            "  python uninstall.py --workspace-only --clean-workspace /path/to/project\n"
            "  python uninstall.py --workspace-only --clean-workspace /path/to/project --purge-workspace-templates\n"
            "  python uninstall.py --workspace-only --clean-legacy --clean-workspace /path/to/project\n"
            "  python uninstall.py --all --clean-workspace /path/to/project"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=("global", "workspace"),
        default="global",
        help="Uninstall global plugin (~/.gemini/config/plugins/gravity-workflow) or workspace plugin (.agents/plugins/gravity-workflow).",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Project workspace directory (required when --scope is 'workspace').",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Override exact plugin destination directory (incompatible with --method agy).",
    )
    parser.add_argument(
        "--method",
        choices=("copy", "agy"),
        default="copy",
        help="Uninstallation backend: 'copy' (default) or 'agy' (delegates to agy CLI).",
    )
    parser.add_argument(
        "--clean-workspace",
        type=Path,
        default=None,
        help="Remove .agents/gravity-workflow/ (and .agents/plugins/gravity-workflow/) from this project and prune its registry entry.",
    )
    parser.add_argument(
        "--purge-workspace-templates",
        action="store_true",
        help="With --clean-workspace, also remove AGENTS.md, mcp_config.json and .agents/rules/* only if unmodified.",
    )
    parser.add_argument(
        "--workspace-only",
        action="store_true",
        help="Skip plugin removal and only run --clean-workspace, --clean-registry and/or --clean-legacy.",
    )
    parser.add_argument(
        "--clean-registry",
        action="store_true",
        help="Remove ~/.gemini/gravity/workflow/live_workspaces.json (and empty parent directory).",
    )
    parser.add_argument(
        "--clean-legacy",
        action="store_true",
        help="Also remove legacy v0.1.x 'gravity' plugin, ~/.gravity/live_workspaces.json and workspace .agents/gravity/.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Uninstall the targeted plugin and remove both current and legacy registries/artifacts (+ --clean-workspace if provided).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Inspect Gravity Workflow installation, workspace state, registry, and manual config entries without modifying anything.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview planned removals without modifying any files.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the Gravity Workflow cross-platform uninstaller CLI."""
    if sys.version_info < (3, 8):
        print("[ERROR] Python 3.8+ is required to run uninstall.py.", file=sys.stderr)
        return 1

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.scope == "workspace" and args.workspace is None:
        print("[ERROR] --workspace PATH is required when --scope is 'workspace'.", file=sys.stderr)
        return 2

    if args.method == "agy" and (args.scope == "workspace" or args.target is not None):
        print(
            "[ERROR] --method agy only supports --scope global without --target.",
            file=sys.stderr,
        )
        return 2

    if args.purge_workspace_templates and args.clean_workspace is None:
        print(
            "[ERROR] --purge-workspace-templates requires --clean-workspace PATH.",
            file=sys.stderr,
        )
        return 2

    if args.workspace_only and args.all:
        print("[ERROR] --workspace-only cannot be combined with --all.", file=sys.stderr)
        return 2

    if (
        args.workspace_only
        and args.clean_workspace is None
        and not args.clean_registry
        and not args.clean_legacy
    ):
        print(
            "[ERROR] --workspace-only requires --clean-workspace, --clean-registry and/or --clean-legacy.",
            file=sys.stderr,
        )
        return 2

    target_dir: Optional[Path] = None
    legacy_dir: Optional[Path] = None
    if args.method == "copy":
        target_dir = install.resolve_target_dir(args.scope, args.workspace, args.target)
        legacy_dir = install.resolve_legacy_plugin_dir(args.scope, args.workspace, args.target)

    if args.check:
        return check_uninstall_status(
            target_dir,
            args.scope,
            args.method,
            args.clean_workspace,
            legacy_dir=legacy_dir,
        )

    if not args.workspace_only:
        rc = install.uninstall_plugin(
            PLUGIN_SOURCE,
            target_dir,
            method=args.method,
            dry_run=args.dry_run,
        )
        if rc != 0:
            return rc

    clean_legacy = bool(args.clean_legacy or args.all)

    if args.clean_workspace is not None:
        ws_rc = clean_workspace(
            args.clean_workspace,
            purge_templates=args.purge_workspace_templates,
            clean_legacy=clean_legacy,
            dry_run=args.dry_run,
        )
        if ws_rc != 0:
            return ws_rc

    if args.clean_registry or args.all:
        reg_rc = clean_registry(dry_run=args.dry_run)
        if reg_rc != 0:
            return reg_rc

    if clean_legacy:
        leg_rc = clean_legacy_artifacts(
            scope=args.scope,
            workspace=args.workspace,
            target=args.target,
            dry_run=args.dry_run,
        )
        if leg_rc != 0:
            return leg_rc

    if not args.dry_run:
        print_post_uninstall_notes()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
