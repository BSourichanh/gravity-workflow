#!/usr/bin/env python3
"""Cross-platform installer and uninstaller for the Gravity Workflow Antigravity plugin (100% Python 3 stdlib)."""

import argparse
import errno
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent
PLUGIN_SOURCE = REPO_ROOT / "plugin"
WORKSPACE_TEMPLATE_DIR = REPO_ROOT / "workspace-template"
PLUGIN_NAME = "gravity-workflow"
LEGACY_PLUGIN_NAME = "gravity"
LEGACY_REGISTRY_PATH = Path.home() / ".gravity" / "live_workspaces.json"

EXEC_SCRIPTS: Tuple[str, ...] = (
    "scripts/validate.py",
    "scripts/init_workspace.py",
    "hooks/pre_tool_guard.py",
    "hooks/post_tool_format.py",
    "hooks/session_start_caveman.py",
    "hooks/stop_session_logger.py",
    "observability/statusline.py",
    "observability/profile_engine.py",
)

COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")


def has_real_python3() -> bool:
    """Return True if a real 'python3' binary is on PATH (ignoring WindowsApps alias)."""
    found = shutil.which("python3")
    if not found:
        return False
    if os.name == "nt" and "windowsapps" in found.lower():
        return False
    return True


def read_plugin_manifest(plugin_dir: Path) -> Optional[Dict[str, Any]]:
    """Safely read and parse plugin.json from a plugin directory."""
    if not plugin_dir.is_dir():
        return None
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_gravity_plugin_dir(plugin_dir: Path) -> bool:
    """Return True only when plugin_dir contains a Gravity Workflow plugin manifest."""
    manifest = read_plugin_manifest(plugin_dir)
    return bool(manifest is not None and manifest.get("name") == PLUGIN_NAME)


def is_core_gravity_plugin_dir(plugin_dir: Path) -> bool:
    """Return True when plugin_dir contains the Gravity Core plugin (config/module.json id=='core')."""
    module_path = plugin_dir / "config" / "module.json"
    if not module_path.is_file():
        return False
    try:
        data = json.loads(module_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Fail-safe: never mistake an unreadable config/module.json plugin for a legacy workflow plugin.
        return True
    return isinstance(data, dict) and data.get("id") == "core"


def is_legacy_gravity_plugin_dir(plugin_dir: Path) -> bool:
    """Return True only for a legacy v0.1.x 'gravity' workflow plugin (never Gravity Core)."""
    manifest = read_plugin_manifest(plugin_dir)
    if manifest is None or manifest.get("name") != LEGACY_PLUGIN_NAME:
        return False
    if is_core_gravity_plugin_dir(plugin_dir):
        return False
    legacy_markers = (
        plugin_dir / "hooks" / "pre_tool_guard.py",
        plugin_dir / "hooks" / "safety_policy.py",
        plugin_dir / "skills" / "gravity",
    )
    return any(marker.exists() for marker in legacy_markers)


def resolve_legacy_plugin_dir(
    scope: str, workspace: Optional[Path], target: Optional[Path]
) -> Optional[Path]:
    """Compute the legacy 'gravity' plugin path corresponding to the requested install target."""
    if target is not None:
        resolved_target = Path(os.path.abspath(str(target.expanduser())))
        if resolved_target.name == PLUGIN_NAME:
            return resolved_target.parent / LEGACY_PLUGIN_NAME
        return None
    if scope == "global":
        return Path(
            os.path.abspath(
                str(Path.home() / ".gemini" / "config" / "plugins" / LEGACY_PLUGIN_NAME)
            )
        )
    if workspace is not None:
        return Path(
            os.path.abspath(
                str(workspace.expanduser().resolve() / ".agents" / "plugins" / LEGACY_PLUGIN_NAME)
            )
        )
    return None


def is_unsafe_target(path: Path, plugin_src: Path) -> Optional[str]:
    """Return an error reason if target path is unsafe to overwrite or remove."""
    resolved = path.resolve()
    src_resolved = plugin_src.resolve()
    home_resolved = Path.home().resolve()

    if resolved == resolved.parent:
        return f"Refusing unsafe filesystem root target: {resolved}"
    if resolved == home_resolved:
        return f"Refusing unsafe home directory target: {resolved}"
    if resolved == src_resolved or src_resolved in resolved.parents or resolved in src_resolved.parents:
        return (
            f"Refusing target inside or enclosing source plugin directory "
            f"({resolved} vs {src_resolved})"
        )
    return None


def ensure_posix_exec_bits(plugin_dir: Path) -> None:
    """Ensure executable permissions (0755-equivalent) on POSIX platforms."""
    if os.name == "nt":
        return
    exec_mask = (
        stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )
    for rel_path in EXEC_SCRIPTS:
        script = plugin_dir / rel_path
        if script.is_file():
            current_mode = script.stat().st_mode
            script.chmod(current_mode | exec_mask)


def resolve_target_dir(
    scope: str, workspace: Optional[Path], target: Optional[Path]
) -> Path:
    """Compute the installation directory for copy-based installs (preserving final symlink)."""
    if target is not None:
        return Path(os.path.abspath(str(target.expanduser())))
    if scope == "global":
        return Path(
            os.path.abspath(str(Path.home() / ".gemini" / "config" / "plugins" / PLUGIN_NAME))
        )
    if workspace is None:
        raise ValueError("--workspace is required when --scope is 'workspace'")
    return Path(
        os.path.abspath(
            str(workspace.expanduser().resolve() / ".agents" / "plugins" / PLUGIN_NAME)
        )
    )


def _run_subprocess(
    cmd: List[str],
    cwd: Optional[Path] = None,
    *,
    dont_write_bytecode: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command capturing UTF-8 text output without raising on non-zero exit."""
    env = None
    if dont_write_bytecode:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def _print_process_output(proc: subprocess.CompletedProcess) -> None:
    """Print captured stdout/stderr streams if non-empty."""
    if proc.stdout and proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr and proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)


def run_plugin_validation(plugin_dir: Path) -> subprocess.CompletedProcess:
    """Run Gravity Workflow's strict validator against a staged or installed plugin directory."""
    validator = plugin_dir / "scripts" / "validate.py"
    return _run_subprocess(
        [sys.executable, "-B", str(validator), "--plugin-root", str(plugin_dir), "--strict"],
        dont_write_bytecode=True,
    )


def run_fix_python_cmd(plugin_dir: Path) -> subprocess.CompletedProcess:
    """Adapt 'python3' references to 'python' or 'py -3' on Windows copies."""
    validator = plugin_dir / "scripts" / "validate.py"
    return _run_subprocess(
        [sys.executable, "-B", str(validator), "--plugin-root", str(plugin_dir), "--fix-python-cmd"],
        dont_write_bytecode=True,
    )


def print_next_steps(
    target_dir: Optional[Path],
    init_ws_dir: Optional[Path],
    *,
    legacy_kept: bool = False,
) -> None:
    """Print concise post-install instructions without touching config.json."""
    print("\nNext steps:")
    if target_dir is not None:
        print(f"  - Installed plugin location: {target_dir}")
    print("  - In Antigravity App or IDE, verify Gravity Workflow under Customizations -> Installed.")
    if init_ws_dir is not None:
        print(f"  - Initialized project workspace: {init_ws_dir}")
        print("  - Open that project in Antigravity and invoke /gravity-workflow to start a session.")
    else:
        print("  - Open any project in Antigravity and run /gravity-workflow to initialize it,")
        print("    or run: python install.py --init-workspace <target-project-dir>")
    print(
        "\nUnified Dashboard (Antigravity App MCP App):\n"
        "  - Invoke /gravity dashboard (from the 'gravity' Core plugin) to view live workflow state.\n"
        "  - Note: the legacy HTTP sidecar (port 4789) is retired; if an older entry remains in\n"
        "    ~/.gemini/config/config.json, manually remove 'sidecars.\"gravity-workflow/live-widget\"'."
    )
    if legacy_kept or LEGACY_REGISTRY_PATH.is_file():
        print(
            "\nMigration note (from v0.1.x 'gravity'):\n"
            "  - In existing initialized projects, rename .agents/gravity/ -> .agents/gravity-workflow/\n"
            "    (or re-run /gravity-workflow init).\n"
            "  - Remove any manual 'gravity/live-widget' or 'gravity-workflow/live-widget' entry\n"
            "    from ~/.gemini/config/config.json, and optionally run:\n"
            "      python install.py --workspace-only --clean-legacy"
        )


def check_environment(
    plugin_src: Path,
    target_dir: Optional[Path],
    scope: str,
    method: str,
    legacy_dir: Optional[Path] = None,
    *,
    strict: bool = False,
) -> int:
    """Inspect prerequisites and current install status without modifying anything."""
    src_manifest = read_plugin_manifest(plugin_src) or {}
    src_version = src_manifest.get("version", "unknown")
    agy_bin = shutil.which("agy")
    real_py3 = has_real_python3()

    print("Gravity Workflow Environment Check")
    print(f"  Platform         : {platform.system()} {platform.release()} ({os.name})")
    print(f"  Python           : {sys.version.split()[0]} ({sys.executable})")
    print(f"  Real 'python3'   : {'yes' if real_py3 else 'no (Windows copy install will auto-adapt)'}")
    print(f"  Antigravity CLI  : {agy_bin if agy_bin else 'not found on PATH'}")
    print(f"  Source plugin    : {plugin_src} (v{src_version})")
    print(f"  Selected method  : {method} (scope={scope})")

    if target_dir is not None:
        unsafe_reason = is_unsafe_target(target_dir, plugin_src)
        if unsafe_reason:
            print(f"  Target status    : UNSAFE ({unsafe_reason})")
        elif target_dir.exists():
            if is_gravity_plugin_dir(target_dir):
                inst_manifest = read_plugin_manifest(target_dir) or {}
                inst_version = inst_manifest.get("version", "unknown")
                print(f"  Target status    : installed at {target_dir} (v{inst_version})")
            else:
                print(f"  Target status    : occupied by non-Gravity Workflow directory ({target_dir})")
        else:
            print(f"  Target status    : not installed ({target_dir})")
    else:
        print("  Target status    : managed by 'agy plugin install'")

    if legacy_dir is not None and (legacy_dir.exists() or legacy_dir.is_symlink()):
        if is_legacy_gravity_plugin_dir(legacy_dir):
            leg_manifest = read_plugin_manifest(legacy_dir) or {}
            leg_ver = leg_manifest.get("version", "unknown")
            print(
                f"  Legacy plugin    : detected at {legacy_dir} (v{leg_ver}) "
                "[will be removed on install unless --keep-legacy]"
            )
        elif is_core_gravity_plugin_dir(legacy_dir):
            core_manifest = read_plugin_manifest(legacy_dir) or {}
            core_ver = core_manifest.get("version", "unknown")
            print(f"  Core plugin      : detected at {legacy_dir} (v{core_ver}) [preserved]")
    if LEGACY_REGISTRY_PATH.is_file():
        print(f"  Legacy registry  : present ({LEGACY_REGISTRY_PATH})")
    if strict:
        val_proc = run_plugin_validation(plugin_src)
        if val_proc.returncode != 0:
            print("  Strict validation: FAILED", file=sys.stderr)
            _print_process_output(val_proc)
            return 1
        print("  Strict validation: PASSED (0 error, 0 warning)")
    return 0


def _is_symlink_or_junction(path: Path) -> bool:
    """Return True when path is a symlink or a Windows NTFS junction."""
    if path.is_symlink():
        return True
    is_junc = getattr(path, "is_junction", None)
    return bool(callable(is_junc) and is_junc())


def _unlink_or_rmdir_link(path: Path) -> None:
    """Remove a symlink or Windows junction without following its target."""
    try:
        path.unlink()
    except OSError:
        path.rmdir()


def _is_empty_dir(path: Path) -> bool:
    """Return True when path is a regular empty directory."""
    return path.is_dir() and not _is_symlink_or_junction(path) and not any(path.iterdir())


def _is_lock_oserror(exc: OSError) -> bool:
    """Return True when an OSError indicates a locked directory/file (Windows WinError 5/32/33 or EBUSY)."""
    if isinstance(exc, PermissionError):
        return True
    winerr = getattr(exc, "winerror", None)
    if winerr in (5, 32, 33):
        return True
    return exc.errno in (errno.EACCES, errno.EPERM, errno.EBUSY)


def _best_effort_remove_tree(path: Path) -> bool:
    """Remove a directory tree, skipping locked entries; return True if path was fully removed."""
    if _is_symlink_or_junction(path):
        try:
            _unlink_or_rmdir_link(path)
            return True
        except OSError:
            return False
    if not path.is_dir():
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            return False
    all_removed = True
    for child in list(path.iterdir()):
        if not _best_effort_remove_tree(child):
            all_removed = False
    try:
        path.rmdir()
        return all_removed
    except OSError:
        return False


def _prune_stale_entries(src_dir: Path, dst_dir: Path) -> List[Path]:
    """Remove entries in dst_dir that do not exist in src_dir (best-effort on locked entries)."""
    locked_stale: List[Path] = []
    src_entries = {entry.name: entry for entry in src_dir.iterdir()}
    for entry in list(dst_dir.iterdir()):
        src_match = src_entries.get(entry.name)
        entry_is_link = _is_symlink_or_junction(entry)
        entry_is_dir = entry.is_dir() and not entry_is_link
        src_is_dir = (
            src_match is not None
            and src_match.is_dir()
            and not _is_symlink_or_junction(src_match)
        )
        if src_match is None or (entry_is_dir != src_is_dir) or entry_is_link:
            if entry_is_dir:
                if not _best_effort_remove_tree(entry):
                    locked_stale.append(entry)
            elif entry_is_link:
                try:
                    _unlink_or_rmdir_link(entry)
                except OSError as exc:
                    if _is_lock_oserror(exc):
                        locked_stale.append(entry)
                    else:
                        raise
            else:
                try:
                    entry.unlink(missing_ok=True)
                except OSError as exc:
                    if _is_lock_oserror(exc):
                        locked_stale.append(entry)
                    else:
                        raise
        elif entry_is_dir and src_is_dir and src_match is not None:
            locked_stale.extend(_prune_stale_entries(src_match, entry))
    return locked_stale


def _copy_file_in_place(src_path: str, dst_path: str) -> str:
    """Copy a file in place, tolerating a locked destination only if its bytes are already identical."""
    src_file = Path(src_path)
    dst_file = Path(dst_path)
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        return shutil.copy2(str(src_file), str(dst_file))
    except OSError as exc:
        if _is_lock_oserror(exc) and dst_file.is_file():
            try:
                if src_file.read_bytes() == dst_file.read_bytes():
                    return str(dst_file)
            except OSError:
                pass
        raise


def _sync_directory_in_place(src_dir: Path, dst_dir: Path) -> List[Path]:
    """Update dst_dir contents in-place with rollback on copy failure, pruning stale entries only on success."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    modified_backups: List[Tuple[Path, bytes, Optional[int]]] = []
    created_files: List[Path] = []
    created_dirs: List[Path] = []
    moved_conflicts: List[Tuple[Path, Path]] = []
    conflict_root: Optional[Path] = None
    conflict_seq = 0

    def _stash_conflict(entry: Path) -> None:
        nonlocal conflict_root, conflict_seq
        if conflict_root is None:
            conflict_root = Path(
                tempfile.mkdtemp(prefix=".gravity-workflow-inplace-", dir=str(dst_dir.parent))
            )
        conflict_seq += 1
        saved = conflict_root / f"item-{conflict_seq}"
        os.rename(str(entry), str(saved))
        moved_conflicts.append((entry, saved))

    def _sync_tree(cur_src: Path, cur_dst: Path) -> None:
        for src_entry in sorted(cur_src.iterdir(), key=lambda p: p.name):
            dst_entry = cur_dst / src_entry.name
            src_is_dir = src_entry.is_dir() and not _is_symlink_or_junction(src_entry)
            if src_is_dir:
                if dst_entry.exists() or _is_symlink_or_junction(dst_entry):
                    if not (dst_entry.is_dir() and not _is_symlink_or_junction(dst_entry)):
                        _stash_conflict(dst_entry)
                        dst_entry.mkdir(parents=True, exist_ok=True)
                        created_dirs.append(dst_entry)
                else:
                    dst_entry.mkdir(parents=True, exist_ok=True)
                    created_dirs.append(dst_entry)
                _sync_tree(src_entry, dst_entry)
                continue

            if dst_entry.exists() or _is_symlink_or_junction(dst_entry):
                if _is_symlink_or_junction(dst_entry) or dst_entry.is_dir():
                    _stash_conflict(dst_entry)
                    _copy_file_in_place(str(src_entry), str(dst_entry))
                    created_files.append(dst_entry)
                    continue

                src_bytes = src_entry.read_bytes()
                orig_bytes: Optional[bytes] = None
                orig_mode: Optional[int] = None
                try:
                    orig_bytes = dst_entry.read_bytes()
                    orig_mode = stat.S_IMODE(dst_entry.stat().st_mode)
                except OSError:
                    orig_bytes = None
                if orig_bytes is not None and orig_bytes == src_bytes:
                    if os.name != "nt":
                        try:
                            shutil.copymode(str(src_entry), str(dst_entry))
                        except OSError:
                            pass
                    continue
                if orig_bytes is not None:
                    modified_backups.append((dst_entry, orig_bytes, orig_mode))
                _copy_file_in_place(str(src_entry), str(dst_entry))
            else:
                _copy_file_in_place(str(src_entry), str(dst_entry))
                created_files.append(dst_entry)

    try:
        try:
            _sync_tree(src_dir, dst_dir)
        except Exception:
            for created_file in reversed(created_files):
                try:
                    created_file.unlink(missing_ok=True)
                except OSError:
                    pass
            for created_dir in reversed(created_dirs):
                try:
                    created_dir.rmdir()
                except OSError:
                    pass
            for mod_path, orig_bytes, orig_mode in reversed(modified_backups):
                try:
                    if mod_path.is_file() and mod_path.read_bytes() == orig_bytes:
                        continue
                except OSError:
                    pass
                try:
                    mod_path.write_bytes(orig_bytes)
                    if os.name != "nt" and orig_mode is not None:
                        mod_path.chmod(orig_mode)
                except OSError:
                    pass
            for orig_dst, saved_path in reversed(moved_conflicts):
                try:
                    if orig_dst.exists() or _is_symlink_or_junction(orig_dst):
                        _best_effort_remove_tree(orig_dst)
                    if saved_path.exists() or _is_symlink_or_junction(saved_path):
                        os.rename(str(saved_path), str(orig_dst))
                except OSError:
                    pass
            raise
        return _prune_stale_entries(src_dir, dst_dir)
    finally:
        if conflict_root is not None:
            shutil.rmtree(conflict_root, ignore_errors=True)


def install_copy(
    plugin_src: Path,
    target_dir: Path,
    *,
    force: bool = False,
    no_fix_python: bool = False,
    skip_validate: bool = False,
    dry_run: bool = False,
) -> int:
    """Install or upgrade the Gravity Workflow plugin using a staged copy, atomic swap, in-place lock fallback, and rollback."""
    unsafe_reason = is_unsafe_target(target_dir, plugin_src)
    if unsafe_reason:
        print(f"[ERROR] {unsafe_reason}", file=sys.stderr)
        return 1

    src_manifest = read_plugin_manifest(plugin_src) or {}
    src_version = str(src_manifest.get("version", "unknown"))

    replacing = False
    if target_dir.exists() or target_dir.is_symlink():
        if _is_empty_dir(target_dir):
            replacing = True
        elif not is_gravity_plugin_dir(target_dir):
            print(
                f"[ERROR] Target path exists and is not a Gravity Workflow plugin: {target_dir}\n"
                "Refusing to overwrite non-Gravity Workflow files.",
                file=sys.stderr,
            )
            return 1
        else:
            inst_manifest = read_plugin_manifest(target_dir) or {}
            inst_version = str(inst_manifest.get("version", "unknown"))
            if not force:
                print(
                    f"[ERROR] Gravity Workflow v{inst_version} is already installed at {target_dir} "
                    f"(source is v{src_version}).\n"
                    "Re-run with --force to upgrade or reinstall.",
                    file=sys.stderr,
                )
                return 1
            replacing = True

    will_fix_py = os.name == "nt" and not no_fix_python and not has_real_python3()

    if dry_run:
        action = "Upgrade/reinstall" if replacing else "Install"
        print(f"[DRY-RUN] {action} plugin from {plugin_src} -> {target_dir}")
        print("[DRY-RUN] Set POSIX executable bits (0755) on scripts when on Linux/macOS")
        if will_fix_py:
            print("[DRY-RUN] Run validate.py --fix-python-cmd on staged copy (Windows)")
        if not skip_validate:
            print("[DRY-RUN] Run validate.py --strict on staged copy before committing")
        return 0

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(
        tempfile.mkdtemp(prefix=".gravity-workflow-stage-", dir=str(target_dir.parent))
    )
    staged_dir = stage_parent / PLUGIN_NAME
    backup_parent: Optional[Path] = None

    try:
        shutil.copytree(plugin_src, staged_dir, ignore=COPY_IGNORE)
        ensure_posix_exec_bits(staged_dir)

        if will_fix_py:
            fix_proc = run_fix_python_cmd(staged_dir)
            if fix_proc.returncode != 0:
                print("[ERROR] Failed to adapt python3 commands on staged copy.", file=sys.stderr)
                _print_process_output(fix_proc)
                return 1
            print("[OK]    Adapted 'python3' references on installed copy for Windows.")

        if not skip_validate:
            val_proc = run_plugin_validation(staged_dir)
            if val_proc.returncode != 0:
                print(
                    "[ERROR] Strict validation failed on staged plugin; aborting without modifying target.",
                    file=sys.stderr,
                )
                _print_process_output(val_proc)
                return 1
            print("[OK]    Strict plugin validation passed.")

        if replacing:
            backup_parent = Path(
                tempfile.mkdtemp(prefix=".gravity-workflow-backup-", dir=str(target_dir.parent))
            )
            backup_dir = backup_parent / PLUGIN_NAME
            try:
                os.rename(str(target_dir), str(backup_dir))
            except OSError as exc:
                if not _is_lock_oserror(exc):
                    raise
                shutil.rmtree(backup_parent, ignore_errors=True)
                backup_parent = None
                if _is_symlink_or_junction(target_dir):
                    print(
                        f"[ERROR] Target symlink/junction is locked and cannot be replaced safely: {target_dir} ({exc})",
                        file=sys.stderr,
                    )
                    return 1
                print(
                    "[INFO]  Target directory is in use by a running process; updating files in place."
                )
                try:
                    locked_stale = _sync_directory_in_place(staged_dir, target_dir)
                except (OSError, shutil.Error) as sync_exc:
                    print(
                        f"[ERROR] In-place update failed because a target file is locked: {sync_exc}\n"
                        "Stop any running Gravity Workflow process and re-run with --force.",
                        file=sys.stderr,
                    )
                    return 1
                if locked_stale:
                    skipped = ", ".join(str(p.name) for p in locked_stale)
                    print(f"[WARN]  Skipped locked stale entries during in-place sync: {skipped}")
                print(f"[OK]    Upgraded Gravity Workflow v{src_version} at {target_dir} (in-place)")
            else:
                try:
                    os.rename(str(staged_dir), str(target_dir))
                except Exception as exc:
                    print(
                        f"[ERROR] Failed to move staged plugin into {target_dir}: {exc}",
                        file=sys.stderr,
                    )
                    if (
                        not target_dir.exists()
                        and not _is_symlink_or_junction(target_dir)
                        and (backup_dir.exists() or _is_symlink_or_junction(backup_dir))
                    ):
                        try:
                            os.rename(str(backup_dir), str(target_dir))
                        except OSError:
                            shutil.move(str(backup_dir), str(target_dir))
                    return 1
                shutil.rmtree(backup_parent, ignore_errors=True)
                backup_parent = None
                print(f"[OK]    Upgraded Gravity Workflow v{src_version} at {target_dir}")
        else:
            shutil.move(str(staged_dir), str(target_dir))
            print(f"[OK]    Installed Gravity Workflow v{src_version} at {target_dir}")
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)
        if backup_parent is not None and is_gravity_plugin_dir(target_dir):
            shutil.rmtree(backup_parent, ignore_errors=True)

    return 0


def install_agy(plugin_src: Path, *, dry_run: bool = False, strict: bool = False) -> int:
    """Validate and install Gravity Workflow using the Antigravity CLI ('agy')."""
    agy_bin = shutil.which("agy")
    if not agy_bin:
        print(
            "[ERROR] 'agy' CLI was not found on PATH.\n"
            "Use the default copy installer instead: python install.py",
            file=sys.stderr,
        )
        return 1

    val_cmd = [agy_bin, "plugin", "validate", str(plugin_src)]
    if strict:
        val_cmd.append("--strict")

    if dry_run:
        print(f"[DRY-RUN] {' '.join(val_cmd)}")
        print(f"[DRY-RUN] {agy_bin} plugin install {plugin_src}")
        return 0

    val_proc = _run_subprocess(val_cmd)
    _print_process_output(val_proc)
    if val_proc.returncode != 0:
        print("[ERROR] 'agy plugin validate' failed.", file=sys.stderr)
        return 1

    inst_proc = _run_subprocess([agy_bin, "plugin", "install", str(plugin_src)])
    _print_process_output(inst_proc)
    if inst_proc.returncode != 0:
        print("[ERROR] 'agy plugin install' failed.", file=sys.stderr)
        return 1

    print("[OK]    Installed Gravity Workflow via 'agy plugin install'.")
    if os.name == "nt" and not has_real_python3():
        print(
            "[NOTE]  Windows without real 'python3' detected: if hooks fail, run\n"
            "        'python plugin/scripts/validate.py --plugin-root <installed-dir> --fix-python-cmd'\n"
            "        against your installed copy."
        )
    return 0


def bootstrap_workspace(
    plugin_src: Path,
    target_dir: Optional[Path],
    method: str,
    ws_dir: Path,
    *,
    mode: str = "full",
    force_workspace: bool = False,
    skip_validate: bool = False,
    dry_run: bool = False,
) -> int:
    """Initialize and validate a Gravity Workflow project workspace."""
    resolved_ws = ws_dir.expanduser().resolve()
    base_dir = (
        target_dir
        if method == "copy" and target_dir is not None and target_dir.is_dir()
        else plugin_src
    )
    init_script = base_dir / "scripts" / "init_workspace.py"
    validator_script = base_dir / "scripts" / "validate.py"

    init_cmd = [
        sys.executable,
        "-B",
        str(init_script),
        "--workspace",
        str(resolved_ws),
        "--mode",
        mode,
    ]
    if force_workspace:
        init_cmd.append("--force")

    val_cmd = [
        sys.executable,
        "-B",
        str(validator_script),
        "--workspace",
        str(resolved_ws),
        "--strict",
    ]

    if dry_run:
        print(f"[DRY-RUN] {' '.join(init_cmd)}")
        if not skip_validate:
            print(f"[DRY-RUN] {' '.join(val_cmd)}")
        return 0

    init_proc = _run_subprocess(init_cmd, dont_write_bytecode=True)
    _print_process_output(init_proc)
    if init_proc.returncode != 0:
        print(f"[ERROR] Workspace initialization failed for {resolved_ws}.", file=sys.stderr)
        return 1

    if not skip_validate:
        val_proc = _run_subprocess(val_cmd, dont_write_bytecode=True)
        _print_process_output(val_proc)
        if val_proc.returncode != 0:
            print(f"[ERROR] Workspace validation failed for {resolved_ws}.", file=sys.stderr)
            return 1

    print(f"[OK]    Workspace ready: {resolved_ws} (mode={mode})")
    return 0


def _remove_tree_or_symlink(path: Path) -> None:
    """Remove a directory tree, symlink, or Windows junction cleanly."""
    if _is_symlink_or_junction(path):
        _unlink_or_rmdir_link(path)
    elif path.exists():
        shutil.rmtree(path)


def cleanup_legacy_plugin(
    plugin_src: Path,
    legacy_dir: Optional[Path],
    *,
    dry_run: bool = False,
) -> bool:
    """Safely remove an obsolete v0.1.x 'gravity' plugin directory after installing gravity-workflow."""
    if legacy_dir is None:
        return False
    if not legacy_dir.exists() and not _is_symlink_or_junction(legacy_dir):
        return False
    unsafe_reason = is_unsafe_target(legacy_dir, plugin_src)
    if unsafe_reason:
        print(f"[WARN]  Skipping legacy plugin cleanup ({unsafe_reason}).")
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
        if _is_lock_oserror(exc):
            print(
                f"[WARN]  Could not remove legacy 'gravity' plugin {legacy_dir} (in use: {exc}).\n"
                "        Remove it manually or run 'python install.py --workspace-only --clean-legacy'."
            )
            return False
        raise


def _load_state_writer() -> Any:
    """Load plugin/observability/state_writer.py lazily using stdlib importlib."""
    obs_dir = PLUGIN_SOURCE / "observability"
    if str(obs_dir) not in sys.path:
        sys.path.insert(0, str(obs_dir))
    if "state_writer" in sys.modules:
        return sys.modules["state_writer"]
    sw_path = obs_dir / "state_writer.py"
    spec = importlib.util.spec_from_file_location("state_writer", sw_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load state_writer from {sw_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["state_writer"] = module
    spec.loader.exec_module(module)
    return module


def default_workflow_home() -> Path:
    """Resolve centralized Gravity Workflow storage ($GRAVITY_WORKFLOW_HOME > $GRAVITY_HOME/workflow > ~/.gemini/gravity/workflow)."""
    return _load_state_writer().default_workflow_home()


def resolve_registry_path(registry_path: Optional[Path] = None) -> Path:
    """Resolve the Live Widget workspace registry path (~/.gemini/gravity/workflow/live_workspaces.json)."""
    return _load_state_writer().resolve_registry_path(registry_path).expanduser().resolve()


def resolve_legacy_registry_path(legacy_registry_path: Optional[Path] = None) -> Path:
    """Resolve the legacy v0.1.x registry path (~/.gravity/live_workspaces.json)."""
    if legacy_registry_path is not None:
        return Path(legacy_registry_path).expanduser().resolve()
    env_reg = os.environ.get("GRAVITY_WORKFLOW_LEGACY_REGISTRY_PATH", "").strip()
    if env_reg:
        return Path(env_reg).expanduser().resolve()
    return LEGACY_REGISTRY_PATH.resolve()


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
    """Load canonical starter file contents from plugin/templates/."""
    templates_root = PLUGIN_SOURCE / "templates"
    template_sources = {
        "AGENTS.md": "AGENTS.md",
        ".agents/mcp_config.json": ".agents/mcp_config.json",
        ".agents/rules/01-safety-boundaries.md": ".agents/rules/01-safety-boundaries.md.tmpl",
        ".agents/rules/02-python-stdlib.md": ".agents/rules/02-python-stdlib.md",
        ".agents/rules/03-api-architecture.md": ".agents/rules/03-api-architecture.md",
        ".agents/rules/04-release-checklist.md": ".agents/rules/04-release-checklist.md",
        ".agents/rules/05-git-versioning.md": ".agents/rules/05-git-versioning.md",
        ".agents/rules/06-engineering-principles.md": ".agents/rules/06-engineering-principles.md",
    }
    loaded: Dict[str, str] = {}
    for target_rel, src_rel in template_sources.items():
        src_path = templates_root / src_rel
        if not src_path.is_file():
            raise RuntimeError(f"Unable to load canonical template from {src_path}")
        loaded[target_rel] = src_path.read_text(encoding="utf-8")
    return loaded


def _atomic_write_json(target: Path, payload: Dict[str, Any]) -> None:
    """Atomically write JSON with UTF-8 encoding and fsync."""
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
    """Remove directory only if it is a regular empty directory."""
    try:
        if _is_empty_dir(directory):
            directory.rmdir()
            return True
    except OSError:
        pass
    return False


def _remove_registry_files(reg_path: Path, label: str, *, dry_run: bool = False) -> bool:
    """Safely remove a registry JSON file, its .lock sibling, and an empty parent dir."""
    lock_path = reg_path.with_suffix(".lock")
    exists = (
        reg_path.exists()
        or _is_symlink_or_junction(reg_path)
        or lock_path.exists()
        or _is_symlink_or_junction(lock_path)
    )
    if not exists:
        return False
    if dry_run:
        print(f"[DRY-RUN] Remove {label}: {reg_path}")
        return True
    if reg_path.exists() or _is_symlink_or_junction(reg_path):
        reg_path.unlink()
    if lock_path.exists() or _is_symlink_or_junction(lock_path):
        lock_path.unlink()
    _remove_dir_if_empty(reg_path.parent)
    print(f"[OK]    Removed {label}: {reg_path}")
    return True


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
    if not _remove_registry_files(reg_path, "global Gravity Workflow registry", dry_run=dry_run):
        print(f"[INFO]  Global registry not found: {reg_path}")
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
    legacy_dir = resolve_legacy_plugin_dir(scope, workspace, target)
    if legacy_dir is not None and (legacy_dir.exists() or _is_symlink_or_junction(legacy_dir)):
        unsafe_reason = is_unsafe_target(legacy_dir, PLUGIN_SOURCE)
        if unsafe_reason:
            print(f"[ERROR] {unsafe_reason}", file=sys.stderr)
            return 1
        if is_core_gravity_plugin_dir(legacy_dir):
            print(f"[INFO]  Preserving Gravity Core plugin at {legacy_dir}")
        elif not is_legacy_gravity_plugin_dir(legacy_dir):
            print(
                f"[ERROR] Refusing to remove non-legacy-Gravity directory: {legacy_dir}",
                file=sys.stderr,
            )
            return 1
        elif dry_run:
            print(f"[DRY-RUN] Remove legacy Gravity plugin directory: {legacy_dir}")
        else:
            _remove_tree_or_symlink(legacy_dir)
            print(f"[OK]    Removed legacy 'gravity' plugin from {legacy_dir}")

    leg_reg = resolve_legacy_registry_path(legacy_registry_path)
    _remove_registry_files(leg_reg, "legacy Gravity registry", dry_run=dry_run)
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
    if ws_plugin_dir.exists() or _is_symlink_or_junction(ws_plugin_dir):
        if not is_gravity_plugin_dir(ws_plugin_dir):
            print(
                f"[ERROR] Refusing to remove non-Gravity Workflow directory: {ws_plugin_dir}",
                file=sys.stderr,
            )
            return 1

    legacy_ws_plugin_dir = resolved_ws / ".agents" / "plugins" / LEGACY_PLUGIN_NAME
    if clean_legacy and (legacy_ws_plugin_dir.exists() or _is_symlink_or_junction(legacy_ws_plugin_dir)):
        if not is_core_gravity_plugin_dir(
            legacy_ws_plugin_dir
        ) and not is_legacy_gravity_plugin_dir(legacy_ws_plugin_dir):
            print(
                f"[ERROR] Refusing to remove non-legacy-Gravity directory: {legacy_ws_plugin_dir}",
                file=sys.stderr,
            )
            return 1

    gravity_state_dir = resolved_ws / ".agents" / "gravity-workflow"
    legacy_state_dir = resolved_ws / ".agents" / "gravity"
    changed = False

    if gravity_state_dir.exists() or _is_symlink_or_junction(gravity_state_dir):
        changed = True
        if dry_run:
            print(f"[DRY-RUN] Remove workspace Gravity Workflow state: {gravity_state_dir}")
        else:
            _remove_tree_or_symlink(gravity_state_dir)
            print(f"[OK]    Removed {gravity_state_dir}")

    if ws_plugin_dir.exists() or _is_symlink_or_junction(ws_plugin_dir):
        changed = True
        if dry_run:
            print(f"[DRY-RUN] Remove workspace Gravity Workflow plugin: {ws_plugin_dir}")
        else:
            _remove_tree_or_symlink(ws_plugin_dir)
            print(f"[OK]    Removed {ws_plugin_dir}")

    if legacy_state_dir.exists() or _is_symlink_or_junction(legacy_state_dir):
        if clean_legacy:
            changed = True
            if dry_run:
                print(f"[DRY-RUN] Remove legacy workspace Gravity state: {legacy_state_dir}")
            else:
                _remove_tree_or_symlink(legacy_state_dir)
                print(f"[OK]    Removed legacy {legacy_state_dir}")
        else:
            print(
                f"[INFO]  Legacy workspace state detected at {legacy_state_dir}; "
                "pass --clean-legacy to remove it."
            )

    if legacy_ws_plugin_dir.exists() or _is_symlink_or_junction(legacy_ws_plugin_dir):
        if is_core_gravity_plugin_dir(legacy_ws_plugin_dir):
            if clean_legacy:
                print(f"[INFO]  Preserving Gravity Core workspace plugin at {legacy_ws_plugin_dir}")
        elif clean_legacy:
            changed = True
            if dry_run:
                print(f"[DRY-RUN] Remove legacy workspace Gravity plugin: {legacy_ws_plugin_dir}")
            else:
                _remove_tree_or_symlink(legacy_ws_plugin_dir)
                print(f"[OK]    Removed legacy {legacy_ws_plugin_dir}")
        elif is_legacy_gravity_plugin_dir(legacy_ws_plugin_dir):
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
        unsafe_reason = is_unsafe_target(target_dir, PLUGIN_SOURCE)
        if unsafe_reason:
            print(f"  Plugin target    : UNSAFE ({unsafe_reason})")
        elif target_dir.exists() or _is_symlink_or_junction(target_dir):
            if is_gravity_plugin_dir(target_dir):
                manifest = read_plugin_manifest(target_dir) or {}
                ver = manifest.get("version", "unknown")
                print(f"  Plugin target    : installed at {target_dir} (v{ver})")
            else:
                print(f"  Plugin target    : occupied by non-Gravity Workflow directory ({target_dir})")
        else:
            print(f"  Plugin target    : not installed ({target_dir})")
    else:
        agy_bin = shutil.which("agy")
        print(f"  Antigravity CLI  : {agy_bin if agy_bin else 'not found on PATH'}")

    if legacy_dir is not None and (legacy_dir.exists() or _is_symlink_or_junction(legacy_dir)):
        if is_legacy_gravity_plugin_dir(legacy_dir):
            leg_manifest = read_plugin_manifest(legacy_dir) or {}
            leg_ver = leg_manifest.get("version", "unknown")
            print(f"  Legacy plugin    : present at {legacy_dir} (v{leg_ver})")
        elif is_core_gravity_plugin_dir(legacy_dir):
            core_manifest = read_plugin_manifest(legacy_dir) or {}
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


def uninstall_plugin(
    plugin_src: Path,
    target_dir: Optional[Path],
    *,
    method: str = "copy",
    dry_run: bool = False,
    show_cleanup_hint: bool = True,
) -> int:
    """Safely uninstall Gravity Workflow from the target location or via 'agy'."""
    if method == "agy":
        agy_bin = shutil.which("agy")
        if not agy_bin:
            print("[ERROR] 'agy' CLI was not found on PATH.", file=sys.stderr)
            return 1
        if dry_run:
            print(f"[DRY-RUN] {agy_bin} plugin uninstall {PLUGIN_NAME}")
            return 0
        proc = _run_subprocess([agy_bin, "plugin", "uninstall", PLUGIN_NAME])
        _print_process_output(proc)
        if proc.returncode != 0:
            print("[ERROR] 'agy plugin uninstall' failed.", file=sys.stderr)
            return 1
        print("[OK]    Uninstalled Gravity Workflow via 'agy plugin uninstall'.")
        if show_cleanup_hint:
            print(
                "[NOTE]  Use 'python install.py --workspace-only --clean-workspace <dir> / --clean-registry' to clean "
                "project state or ~/.gemini/gravity/workflow/, and remove any manual Gravity Workflow entries from "
                "~/.gemini/config/config.json or ~/.gemini/antigravity-cli/settings.json."
            )
        return 0

    assert target_dir is not None
    unsafe_reason = is_unsafe_target(target_dir, plugin_src)
    if unsafe_reason:
        print(f"[ERROR] {unsafe_reason}", file=sys.stderr)
        return 1

    if not target_dir.exists() and not _is_symlink_or_junction(target_dir):
        print(f"[ERROR] Gravity Workflow is not installed at {target_dir}.", file=sys.stderr)
        return 1

    if not is_gravity_plugin_dir(target_dir):
        print(
            f"[ERROR] Refusing to remove non-Gravity Workflow directory: {target_dir}",
            file=sys.stderr,
        )
        return 1

    if dry_run:
        print(f"[DRY-RUN] Remove Gravity Workflow plugin directory: {target_dir}")
        return 0

    _remove_tree_or_symlink(target_dir)
    print(f"[OK]    Removed Gravity Workflow plugin from {target_dir}")
    if show_cleanup_hint:
        print(
            "[NOTE]  Use 'python install.py --workspace-only --clean-workspace <dir> / --clean-registry' to clean "
            "project state or ~/.gemini/gravity/workflow/, and remove any manual Gravity Workflow entries from "
            "~/.gemini/config/config.json or ~/.gemini/antigravity-cli/settings.json."
        )
    return 0


def _run_uninstall(args: argparse.Namespace, target_dir: Optional[Path]) -> int:
    """Execute unified uninstall, workspace cleanup, registry pruning, and legacy cleanup."""
    if not args.workspace_only:
        rc = uninstall_plugin(
            PLUGIN_SOURCE,
            target_dir,
            method=args.method,
            dry_run=args.dry_run,
            show_cleanup_hint=False,
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
        if (
            not args.workspace_only
            and args.clean_workspace is None
            and not args.clean_registry
            and not args.all
        ):
            print(
                "[NOTE]  Use 'python install.py --workspace-only --clean-workspace <dir> / --clean-registry' "
                "to clean project state or ~/.gemini/gravity/workflow/."
            )
        print_post_uninstall_notes()

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser for the Gravity Workflow installer and uninstaller."""
    parser = argparse.ArgumentParser(
        description="Install, upgrade, validate, or uninstall the Gravity Workflow Antigravity plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python install.py --check\n"
            "  python install.py\n"
            "  python install.py --force\n"
            "  python install.py --scope workspace --workspace /path/to/project\n"
            "  python install.py --init-workspace /path/to/project --mode quickstart\n"
            "  python install.py --uninstall\n"
            "  python install.py --uninstall --check --clean-workspace /path/to/project\n"
            "  python install.py --workspace-only --clean-workspace /path/to/project\n"
            "  python install.py --workspace-only --clean-workspace /path/to/project --purge-workspace-templates\n"
            "  python install.py --workspace-only --clean-legacy --clean-workspace /path/to/project\n"
            "  python install.py --uninstall --all --clean-workspace /path/to/project"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=("global", "workspace"),
        default="global",
        help="Install or uninstall globally (~/.gemini/config/plugins/gravity-workflow) or inside a project (.agents/plugins/gravity-workflow).",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Project workspace directory (required when --scope workspace).",
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
        help="Installation/uninstallation backend: 'copy' (default, App/IDE/CLI) or 'agy' (delegates to agy CLI).",
    )
    parser.add_argument(
        "--init-workspace",
        type=Path,
        default=None,
        help="Also initialize Gravity Workflow's 13-file project baseline in this workspace after installing.",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "quickstart"),
        default="full",
        help="Workspace initialization mode passed to init_workspace.py (default: full).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Upgrade or reinstall when Gravity Workflow already exists at the target plugin directory.",
    )
    parser.add_argument(
        "--force-workspace",
        action="store_true",
        help="Overwrite existing project files when used with --init-workspace.",
    )
    parser.add_argument(
        "--keep-legacy",
        action="store_true",
        help="Keep an existing legacy v0.1.x 'gravity' plugin directory instead of removing it after install.",
    )
    parser.add_argument(
        "--no-fix-python",
        action="store_true",
        help="Skip automatic Windows 'python3' -> 'python'/'py -3' adaptation on the installed copy.",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip strict validation during install and workspace initialization.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enforce strict validation (enabled by default; overrides --skip-validate and validates source in --check).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview planned operations without modifying files or running mutating commands.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Inspect environment prerequisites and target status (or uninstall status with --uninstall / --workspace-only) without modifying anything.",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Safely remove an installed Gravity Workflow plugin and optional workspace/registry/legacy artifacts.",
    )
    parser.add_argument(
        "--clean-workspace",
        type=Path,
        default=None,
        help="With --uninstall or --workspace-only, remove .agents/gravity-workflow/ (and .agents/plugins/gravity-workflow/) and prune its registry entry.",
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
        help="With --uninstall or --workspace-only, remove ~/.gemini/gravity/workflow/live_workspaces.json (and empty parent directory).",
    )
    parser.add_argument(
        "--clean-legacy",
        action="store_true",
        help="With --uninstall or --workspace-only, remove legacy v0.1.x 'gravity' plugin, ~/.gravity/live_workspaces.json and workspace .agents/gravity/.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="With --uninstall, uninstall the targeted plugin and remove both current and legacy registries/artifacts (+ --clean-workspace if provided).",
    )
    return parser


def _configure_stdio() -> None:
    """Force stdout/stderr to UTF-8 so Windows cp1252 consoles never crash."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the Gravity Workflow cross-platform installer and uninstaller CLI."""
    _configure_stdio()
    if sys.version_info < (3, 8):
        print("[ERROR] Python 3.8+ is required to run install.py.", file=sys.stderr)
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

    is_uninstall_mode = bool(args.uninstall or args.workspace_only)

    if is_uninstall_mode and args.init_workspace is not None:
        print(
            "[ERROR] --uninstall / --workspace-only cannot be combined with --init-workspace.",
            file=sys.stderr,
        )
        return 2

    if not is_uninstall_mode and (
        args.clean_workspace is not None
        or args.purge_workspace_templates
        or args.clean_registry
        or args.clean_legacy
        or args.all
    ):
        print(
            "[ERROR] Cleanup options (--clean-workspace, --purge-workspace-templates, "
            "--clean-registry, --clean-legacy, --all) require --uninstall or --workspace-only.",
            file=sys.stderr,
        )
        return 2

    if not PLUGIN_SOURCE.is_dir() or not is_gravity_plugin_dir(PLUGIN_SOURCE):
        print(
            f"[ERROR] Source plugin directory is missing or invalid: {PLUGIN_SOURCE}",
            file=sys.stderr,
        )
        return 1

    target_dir: Optional[Path] = None
    legacy_dir: Optional[Path] = None
    if args.method == "copy":
        target_dir = resolve_target_dir(args.scope, args.workspace, args.target)
        legacy_dir = resolve_legacy_plugin_dir(args.scope, args.workspace, args.target)

    if args.check:
        if is_uninstall_mode:
            return check_uninstall_status(
                target_dir,
                args.scope,
                args.method,
                args.clean_workspace,
                legacy_dir=legacy_dir,
            )
        return check_environment(
            PLUGIN_SOURCE,
            target_dir,
            args.scope,
            args.method,
            legacy_dir=legacy_dir,
            strict=args.strict,
        )

    if is_uninstall_mode:
        return _run_uninstall(args, target_dir)

    effective_skip_validate = bool(args.skip_validate and not args.strict)

    if args.method == "agy":
        rc = install_agy(PLUGIN_SOURCE, dry_run=args.dry_run, strict=args.strict)
    else:
        assert target_dir is not None
        rc = install_copy(
            PLUGIN_SOURCE,
            target_dir,
            force=args.force,
            no_fix_python=args.no_fix_python,
            skip_validate=effective_skip_validate,
            dry_run=args.dry_run,
        )

    if rc != 0:
        return rc

    legacy_kept = False
    if args.method == "copy" and legacy_dir is not None:
        if args.keep_legacy:
            if (legacy_dir.exists() or legacy_dir.is_symlink()) and is_legacy_gravity_plugin_dir(
                legacy_dir
            ):
                legacy_kept = True
                print(f"[INFO]  Keeping legacy 'gravity' plugin at {legacy_dir} (--keep-legacy).")
        else:
            cleanup_legacy_plugin(PLUGIN_SOURCE, legacy_dir, dry_run=args.dry_run)

    if args.init_workspace is not None:
        ws_rc = bootstrap_workspace(
            PLUGIN_SOURCE,
            target_dir,
            args.method,
            args.init_workspace,
            mode=args.mode,
            force_workspace=args.force_workspace,
            skip_validate=effective_skip_validate,
            dry_run=args.dry_run,
        )
        if ws_rc != 0:
            return ws_rc

    if not args.dry_run:
        print_next_steps(target_dir, args.init_workspace, legacy_kept=legacy_kept)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
