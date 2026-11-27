"""Shared cross-platform plugin installer engine for Gravity plugins (Python 3.10+ stdlib)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PosixPath, PureWindowsPath
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Optional, Sequence, Union

from .atomic_io import is_lock_oserror, write_json_atomic
from .file_lock import exclusive_file_lock
from .hook_runtime import configure_utf8_stdio
from .path_safety import ensure_safe_dir, is_symlink_or_junction, safe_home_dir, unlink_or_rmdir_link

COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")
CORE_INSTALL_FLAGS: tuple[str, ...] = (
    "--scope", "--workspace", "--target", "--method", "--force",
    "--skip-validate", "--strict", "--no-fix-python", "--dry-run", "--check", "--uninstall",
)


@dataclass
class InstallSpec:
    """Per-plugin configuration for the unified 11-flag Gravity installer."""

    plugin_name: str
    plugin_source: Union[str, Path]
    display_name: str = ""
    repo_root: Optional[Union[str, Path]] = None
    exec_scripts: tuple[str, ...] = ()
    has_executable_manifest: bool = False
    description: str = ""
    epilog: str = ""
    installed_check_fn: Optional[Callable[[Path], bool]] = None
    legacy_check_fn: Optional[Callable[[Path], bool]] = None
    extra_cli_fn: Optional[Callable[[argparse.ArgumentParser], None]] = None
    extra_dispatch_fn: Optional[Callable[[argparse.Namespace, "InstallSpec", Optional[Path]], Optional[int]]] = None
    post_install_fn: Optional[Callable[[argparse.Namespace, "InstallSpec", Optional[Path]], int]] = None
    post_uninstall_fn: Optional[Callable[[argparse.Namespace, "InstallSpec", Optional[Path]], int]] = None
    next_steps_fn: Optional[Callable[[Optional[Path], argparse.Namespace], None]] = None
    validate_cmd_builder: Optional[Callable[[Path], list[str]]] = None
    fix_python_cmd_builder: Optional[Callable[[Path], list[str]]] = None
    agy_strict_default: bool = True
    record_sources: bool = True


def _err(msg: str, code: int = 1) -> int:
    print(f"[ERROR] {msg}", file=sys.stderr)
    return code

def _to_path(raw: Union[str, Path]) -> Path:
    s = os.fspath(raw)
    return PosixPath(s) if os.sep == "/" else Path(s)

def _safe_home_str() -> str:
    hp = safe_home_dir()
    return hp.as_posix() if (os.sep == "/" and isinstance(hp, PureWindowsPath)) else os.fspath(hp)

def _expand_user_str(raw: Union[str, Path]) -> str:
    s = os.fspath(raw)
    return (_safe_home_str() if s == "~" else os.path.join(_safe_home_str(), s[2:])) if (s == "~" or s.startswith(("~/", "~\\"))) else os.path.expanduser(s)

def _read_json_dict(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None

def _read_bytes_opt(path_str: str) -> Optional[bytes]:
    try:
        with open(path_str, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def has_real_python3() -> bool:
    """Return True if a real 'python3' binary exists on PATH (ignoring WindowsApps stub)."""
    found = shutil.which("python3")
    return bool(found) and not (os.name == "nt" and "windowsapps" in found.lower())


def read_plugin_manifest(plugin_dir: Union[str, Path]) -> Optional[dict[str, Any]]:
    """Safely read and parse plugin.json from a plugin directory."""
    p_str = os.fspath(plugin_dir)
    return _read_json_dict(os.path.join(p_str, "plugin.json")) if os.path.isdir(p_str) else None


def read_module_contract(plugin_dir: Union[str, Path]) -> Optional[dict[str, Any]]:
    """Safely read config/module.json from a plugin directory."""
    return _read_json_dict(os.path.join(os.fspath(plugin_dir), "config", "module.json"))


def _is_installed_plugin(spec: InstallSpec, plugin_dir: Union[str, Path]) -> bool:
    if spec.installed_check_fn is not None:
        return bool(spec.installed_check_fn(_to_path(plugin_dir)))
    manifest = read_plugin_manifest(plugin_dir)
    return bool(manifest is not None and manifest.get("name") == spec.plugin_name)

def _is_legacy_plugin(spec: InstallSpec, plugin_dir: Union[str, Path]) -> bool:
    return bool(spec.legacy_check_fn is not None and spec.legacy_check_fn(_to_path(plugin_dir)))

def _is_recognized_target(spec: InstallSpec, plugin_dir: Union[str, Path]) -> bool:
    return _is_installed_plugin(spec, plugin_dir) or _is_legacy_plugin(spec, plugin_dir)


def is_unsafe_target(path: Union[str, Path], plugin_src: Union[str, Path]) -> Optional[str]:
    """Return an error reason if target path is unsafe to overwrite or remove."""
    resolved = os.path.realpath(os.path.abspath(os.fspath(path)))
    src_resolved = os.path.realpath(os.path.abspath(os.fspath(plugin_src)))
    home_resolved = os.path.realpath(os.path.abspath(_safe_home_str()))
    if resolved == os.path.dirname(resolved):
        return f"Refusing unsafe filesystem root target: {resolved}"
    if resolved == home_resolved:
        return f"Refusing unsafe home directory target: {resolved}"
    try:
        nested = os.path.commonpath([resolved, src_resolved]) in (resolved, src_resolved)
    except ValueError:
        nested = False
    return f"Refusing target inside or enclosing source plugin directory ({resolved} vs {src_resolved})" if (resolved == src_resolved or nested) else None

def _check_target_safety(target_dir: Union[str, Path], plugin_src: Union[str, Path]) -> Optional[str]:
    t_str = os.fspath(target_dir)
    return is_unsafe_target(t_str, plugin_src) or (f"Refusing symlink or junction target: {t_str}" if is_symlink_or_junction(t_str) else None)


def ensure_posix_exec_bits(plugin_dir: Union[str, Path], exec_scripts: Sequence[str]) -> None:
    """Ensure executable permissions (0755-equivalent) on POSIX platforms."""
    if os.name == "nt":
        return
    exec_mask = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    for rel_path in exec_scripts:
        script = os.path.join(os.fspath(plugin_dir), rel_path)
        if os.path.isfile(script) and not is_symlink_or_junction(script):
            os.chmod(script, os.stat(script).st_mode | exec_mask)


def resolve_target_dir(
    scope: str, workspace: Optional[Union[str, Path]], target: Optional[Union[str, Path]], *, plugin_name: str = "gravity",
) -> Path:
    """Compute the installation directory for copy installs, preserving final symlink."""
    if target is not None:
        return _to_path(os.path.abspath(_expand_user_str(target)))
    if scope == "global":
        return _to_path(os.path.abspath(os.path.join(_safe_home_str(), ".gemini", "config", "plugins", plugin_name)))
    if workspace is None:
        raise ValueError("--workspace is required when --scope is 'workspace'")
    return _to_path(os.path.abspath(os.path.join(os.path.realpath(os.path.abspath(_expand_user_str(workspace))), ".agents", "plugins", plugin_name)))


def _run_subprocess(cmd: list[str], *, dont_write_bytecode: bool = False, extra_env: Optional[dict[str, str]] = None, timeout: Optional[float] = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **({"PYTHONDONTWRITEBYTECODE": "1"} if dont_write_bytecode else {}), **(extra_env or {})} if (dont_write_bytecode or extra_env) else None
    return subprocess.run(cmd, env=env, shell=False, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, check=False)

def _proc_ok(proc: subprocess.CompletedProcess[str], err_msg: str) -> bool:
    if proc.stdout and proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr and proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)
    return True if proc.returncode == 0 else bool(_err(err_msg) and False)


def run_plugin_validation(plugin_dir: Union[str, Path], spec: Optional[InstallSpec] = None) -> subprocess.CompletedProcess[str]:
    """Run strict validator against a staged or installed plugin directory."""
    p_str = os.fspath(plugin_dir)
    cmd = spec.validate_cmd_builder(_to_path(p_str)) if (spec and spec.validate_cmd_builder) else [sys.executable, "-B", os.path.join(p_str, "scripts", "validate.py"), "--plugin-root", p_str, "--strict"]
    return _run_subprocess(cmd, dont_write_bytecode=True)


def run_fix_python_cmd(plugin_dir: Union[str, Path], spec: Optional[InstallSpec] = None) -> subprocess.CompletedProcess[str]:
    """Adapt 'python3' references to 'python' or 'py -3' on staged Windows copies."""
    p_str = os.fspath(plugin_dir)
    cmd = spec.fix_python_cmd_builder(_to_path(p_str)) if (spec and spec.fix_python_cmd_builder) else [sys.executable, "-B", os.path.join(p_str, "scripts", "validate.py"), "--plugin-root", p_str, "--fix-python-cmd"]
    return _run_subprocess(cmd, dont_write_bytecode=True)


def _best_effort_remove(path_str: str) -> bool:
    try:
        if is_symlink_or_junction(path_str):
            unlink_or_rmdir_link(path_str)
            return True
        if not os.path.isdir(path_str):
            if os.path.lexists(path_str):
                os.unlink(path_str)
            return True
        ok = all(_best_effort_remove(os.path.join(path_str, n)) for n in os.listdir(path_str))
        os.rmdir(path_str)
        return ok
    except OSError:
        return False

def _prune_stale_entries(src_dir: str, dst_dir: str) -> list[str]:
    locked, src_names = [], set(os.listdir(src_dir))
    for name in os.listdir(dst_dir):
        d_ent, s_ent = os.path.join(dst_dir, name), os.path.join(src_dir, name) if name in src_names else None
        d_link = is_symlink_or_junction(d_ent)
        d_dir = os.path.isdir(d_ent) and not d_link
        s_dir = s_ent is not None and os.path.isdir(s_ent) and not is_symlink_or_junction(s_ent)
        if s_ent is not None and d_dir and s_dir:
            locked.extend(_prune_stale_entries(s_ent, d_ent))
        elif s_ent is None or (d_dir != s_dir) or d_link:
            if not _best_effort_remove(d_ent):
                locked.append(d_ent)
    return locked

def _copy_file_in_place(src_path: str, dst_path: str) -> str:
    """Copy a file in place, tolerating a locked destination if its bytes are already identical."""
    if is_symlink_or_junction(src_path) or is_symlink_or_junction(dst_path):
        raise ValueError(f"Refusing symlink or junction in file copy: {src_path} -> {dst_path}")
    if os.path.dirname(dst_path):
        ensure_safe_dir(os.path.dirname(dst_path))
    try:
        return shutil.copy2(src_path, dst_path)
    except OSError as exc:
        if is_lock_oserror(exc) and os.path.isfile(dst_path) and not is_symlink_or_junction(dst_path):
            s_bytes = _read_bytes_opt(src_path)
            if s_bytes is not None and s_bytes == _read_bytes_opt(dst_path):
                return dst_path
        raise

def _sync_directory_in_place(src_dir: Union[str, Path], dst_dir: Union[str, Path]) -> list[str]:
    """Transactionally sync src_dir into dst_dir in-place with rollback on failure."""
    src_str, dst_str = os.fspath(src_dir), os.fspath(dst_dir)
    if is_symlink_or_junction(dst_str):
        raise ValueError(f"Refusing symlink or junction target directory: {dst_str}")
    ensure_safe_dir(dst_str)
    backups: list[tuple[str, bytes, Optional[int]]] = []
    created, conflicts = [], []
    conflict_root: Optional[str] = None

    def _stash(entry: str) -> None:
        nonlocal conflict_root
        conflict_root = conflict_root or tempfile.mkdtemp(prefix=".gravity-inplace-", dir=os.path.dirname(dst_str))
        saved = os.path.join(conflict_root, f"item-{len(conflicts) + 1}")
        os.rename(entry, saved)
        conflicts.append((entry, saved))

    def _sync_tree(cur_src: str, cur_dst: str) -> None:
        for name in sorted(os.listdir(cur_src)):
            s_ent, d_ent = os.path.join(cur_src, name), os.path.join(cur_dst, name)
            if is_symlink_or_junction(s_ent) or is_symlink_or_junction(d_ent):
                raise ValueError(f"Refusing symlink or junction path: {d_ent}")
            if os.path.isdir(s_ent):
                if os.path.lexists(d_ent) and not os.path.isdir(d_ent):
                    _stash(d_ent)
                if not os.path.lexists(d_ent):
                    os.makedirs(d_ent, exist_ok=True)
                    created.append(d_ent)
                _sync_tree(s_ent, d_ent)
            elif os.path.lexists(d_ent) and not os.path.isdir(d_ent):
                s_bytes, o_bytes = _read_bytes_opt(s_ent), _read_bytes_opt(d_ent)
                o_mode = stat.S_IMODE(os.stat(d_ent).st_mode) if o_bytes is not None else None
                if o_bytes is not None and o_bytes == s_bytes:
                    if os.name != "nt":
                        shutil.copymode(s_ent, d_ent)
                else:
                    if o_bytes is not None:
                        backups.append((d_ent, o_bytes, o_mode))
                    _copy_file_in_place(s_ent, d_ent)
            else:
                if os.path.isdir(d_ent):
                    _stash(d_ent)
                _copy_file_in_place(s_ent, d_ent)
                created.append(d_ent)

    try:
        try:
            _sync_tree(src_str, dst_str)
        except Exception:
            for p in reversed(created):
                _best_effort_remove(p)
            for mp, ob, om in reversed(backups):
                try:
                    with open(mp, "wb") as mf:
                        mf.write(ob)
                    if os.name != "nt" and om is not None:
                        os.chmod(mp, om)
                except OSError:
                    pass
            for orig, saved in reversed(conflicts):
                if os.path.lexists(orig):
                    _best_effort_remove(orig)
                if os.path.lexists(saved):
                    os.rename(saved, orig)
            raise
        return _prune_stale_entries(src_str, dst_str)
    finally:
        if conflict_root is not None:
            shutil.rmtree(conflict_root, ignore_errors=True)


def check_environment(
    spec: InstallSpec, target_dir: Optional[Union[str, Path]], scope: str, method: str, *, strict: bool = False,
) -> int:
    """Inspect prerequisites and target status without modifying anything."""
    display, src_ver = spec.display_name or spec.plugin_name, (read_plugin_manifest(spec.plugin_source) or {}).get("version", "unknown")
    print(f"{display} Environment Check\n  Platform: {platform.system()} ({os.name}) | Python: {sys.version.split()[0]}")
    if spec.has_executable_manifest:
        print(f"  Real 'python3'  : {'yes' if has_real_python3() else 'no (Windows copy install will auto-adapt)'}")
    print(f"  Antigravity CLI : {shutil.which('agy') or 'not found on PATH'}\n  Source plugin   : {os.fspath(spec.plugin_source)} (v{src_ver})\n  Selected method : {method} (scope={scope})")
    if target_dir is None:
        print("  Target status   : managed by 'agy plugin install'")
    else:
        t_str = os.fspath(target_dir)
        unsafe = _check_target_safety(t_str, spec.plugin_source)
        if unsafe:
            print(f"  Target status   : UNSAFE ({unsafe})")
        elif os.path.exists(t_str):
            iv = (read_plugin_manifest(t_str) or {}).get("version", "unknown")
            tag = "installed" if _is_installed_plugin(spec, t_str) else ("legacy install" if _is_legacy_plugin(spec, t_str) else "occupied by unrelated directory")
            print(f"  Target status   : {tag} at {t_str} (v{iv})")
        else:
            print(f"  Target status   : not installed ({t_str})")
    if strict:
        if not _proc_ok(run_plugin_validation(spec.plugin_source, spec), "Strict validation: FAILED"):
            return 1
        print("  Strict validation: PASSED (0 error, 0 warning)")
    return 0


def install_copy(
    spec: InstallSpec, target_dir: Union[str, Path], *, force: bool = False,
    no_fix_python: bool = False, skip_validate: bool = False, dry_run: bool = False,
) -> int:
    """Install or upgrade a Gravity plugin using staged validation, atomic swap, and rollback."""
    display, src_str, t_str = spec.display_name or spec.plugin_name, os.fspath(spec.plugin_source), os.fspath(target_dir)
    if (unsafe := _check_target_safety(t_str, src_str)):
        return _err(unsafe)
    src_ver = str((read_plugin_manifest(src_str) or {}).get("version", "unknown"))
    replacing, replacing_legacy = False, False
    if os.path.lexists(t_str):
        if os.path.isdir(t_str) and not is_symlink_or_junction(t_str) and not os.listdir(t_str):
            replacing = True
        elif not _is_recognized_target(spec, t_str):
            return _err(f"Target exists and is not a {display} plugin: {t_str}")
        else:
            replacing_legacy = _is_legacy_plugin(spec, t_str) and not _is_installed_plugin(spec, t_str)
            if not force:
                return _err(f"{display} v{(read_plugin_manifest(t_str) or {}).get('version', 'unknown')} already installed at {t_str} (source v{src_ver}); use --force.")
            replacing = True

    will_fix_py = bool(spec.has_executable_manifest and os.name == "nt" and not no_fix_python and not has_real_python3())
    if dry_run:
        act = "Replace legacy install" if replacing_legacy else ("Upgrade/reinstall" if replacing else "Install")
        print(f"[DRY-RUN] {act} {display} from {src_str} -> {t_str}\n[DRY-RUN] Set POSIX executable bits (0755)")
        if will_fix_py:
            print("[DRY-RUN] Run validate.py --fix-python-cmd on staged copy (Windows)")
        if not skip_validate:
            print("[DRY-RUN] Run validate.py --strict on staged copy before committing")
        return 0

    parent_dir = os.path.dirname(os.path.abspath(t_str))
    ensure_safe_dir(parent_dir)
    stage_parent = tempfile.mkdtemp(prefix=f".{spec.plugin_name}-stage-", dir=parent_dir)
    staged_dir, backup_parent = os.path.join(stage_parent, spec.plugin_name), None
    try:
        shutil.copytree(src_str, staged_dir, ignore=COPY_IGNORE)
        ensure_posix_exec_bits(staged_dir, spec.exec_scripts)
        if will_fix_py and not _proc_ok(run_fix_python_cmd(staged_dir, spec), "Failed to adapt python3 commands on staged copy."):
            return 1
        if not skip_validate and not _proc_ok(run_plugin_validation(staged_dir, spec), "Strict validation failed on staged plugin; aborting."):
            return 1
        if not replacing:
            shutil.move(staged_dir, t_str)
            print(f"[OK]    Installed {display} v{src_ver} at {t_str}")
            return 0
        backup_parent = tempfile.mkdtemp(prefix=f".{spec.plugin_name}-backup-", dir=parent_dir)
        backup_dir = os.path.join(backup_parent, spec.plugin_name)
        try:
            os.rename(t_str, backup_dir)
        except OSError as exc:
            if not is_lock_oserror(exc):
                raise
            shutil.rmtree(backup_parent, ignore_errors=True)
            backup_parent = None
            try:
                locked = _sync_directory_in_place(staged_dir, t_str)
            except (OSError, ValueError, shutil.Error) as sync_exc:
                return _err(f"In-place update failed: {sync_exc}")
            if locked:
                print(f"[WARN]  Skipped locked stale entries: {', '.join(os.path.basename(p) for p in locked)}")
            print(f"[OK]    Upgraded {display} v{src_ver} at {t_str} (in-place)")
            return 0
        try:
            shutil.move(staged_dir, t_str)
        except Exception as exc:
            if not os.path.lexists(t_str) and os.path.exists(backup_dir):
                shutil.move(backup_dir, t_str)
            return _err(f"Failed to move staged plugin into {t_str}: {exc}")
        shutil.rmtree(backup_parent, ignore_errors=True)
        backup_parent = None
        print(f"[OK]    Upgraded {display} v{src_ver} at {t_str}")
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)
        if backup_parent is not None and _is_installed_plugin(spec, t_str):
            shutil.rmtree(backup_parent, ignore_errors=True)
    return 0


def install_agy(spec: InstallSpec, *, dry_run: bool = False, strict: bool = False) -> int:
    """Validate and install plugin using the Antigravity CLI ('agy')."""
    display, src_str = spec.display_name or spec.plugin_name, os.fspath(spec.plugin_source)
    if not (agy_bin := shutil.which("agy")):
        return _err("'agy' CLI was not found on PATH.")
    val_cmd = [agy_bin, "plugin", "validate", src_str] + (["--strict"] if (spec.agy_strict_default or strict) else [])
    if dry_run:
        print(f"[DRY-RUN] {' '.join(val_cmd)}\n[DRY-RUN] {agy_bin} plugin install {src_str}")
        return 0
    if not _proc_ok(_run_subprocess(val_cmd), "'agy plugin validate' failed.") or not _proc_ok(_run_subprocess([agy_bin, "plugin", "install", src_str]), "'agy plugin install' failed."):
        return 1
    print(f"[OK]    Installed {display} via 'agy plugin install'.")
    if spec.has_executable_manifest and os.name == "nt" and not has_real_python3():
        print("[NOTE]  Windows without real 'python3' detected; run validate.py --fix-python-cmd on installed copy.")
    return 0


def uninstall_plugin(spec: InstallSpec, target_dir: Optional[Union[str, Path]], *, method: str = "copy", dry_run: bool = False) -> int:
    """Safely uninstall plugin without touching centralized $GRAVITY_HOME data."""
    display = spec.display_name or spec.plugin_name
    if method == "agy":
        if not (agy_bin := shutil.which("agy")):
            return _err("'agy' CLI was not found on PATH.")
        if dry_run:
            return print(f"[DRY-RUN] {agy_bin} plugin uninstall {spec.plugin_name}") or 0
        if not _proc_ok(_run_subprocess([agy_bin, "plugin", "uninstall", spec.plugin_name]), "'agy plugin uninstall' failed."):
            return 1
        return print(f"[OK]    Uninstalled {display} via 'agy plugin uninstall'.") or 0
    assert target_dir is not None
    t_str = os.fspath(target_dir)
    if (unsafe := _check_target_safety(t_str, spec.plugin_source)):
        return _err(unsafe)
    if not os.path.lexists(t_str):
        return _err(f"{display} is not installed at {t_str}.")
    if not _is_recognized_target(spec, t_str):
        return _err(f"Refusing to remove non-{spec.plugin_name} directory: {t_str}")
    if dry_run:
        return print(f"[DRY-RUN] Remove {display} plugin directory: {t_str}") or 0
    shutil.rmtree(t_str)
    return print(f"[OK]    Removed {display} plugin from {t_str} ($GRAVITY_HOME data preserved).") or 0


def record_source_provenance(
    spec: InstallSpec, *, method: str, target_dir: Optional[Union[str, Path]], gravity_home: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """Atomically record plugin repo provenance in $GRAVITY_HOME/core/sources.json."""
    env_home = os.environ.get("GRAVITY_HOME", "").strip()
    g_home = os.path.abspath(_expand_user_str(gravity_home)) if gravity_home is not None else (os.path.abspath(_expand_user_str(env_home)) if env_home else os.path.abspath(os.path.join(_safe_home_str(), ".gemini", "gravity")))
    core_dir = os.path.join(g_home, "core")
    sources_path, lock_path = os.path.join(core_dir, "sources.json"), os.path.join(core_dir, "sources.lock")
    for p in (g_home, core_dir, sources_path, lock_path):
        if is_symlink_or_junction(p):
            print(f"[WARN]  Skipping sources.json update due to symlink/junction: {p}", file=sys.stderr)
            return None
    ensure_safe_dir(core_dir)
    manifest = read_plugin_manifest(spec.plugin_source) or {}
    repo_root_str = os.path.abspath(os.fspath(spec.repo_root) if spec.repo_root is not None else os.path.dirname(os.path.abspath(os.fspath(spec.plugin_source))))
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    git_res: list[Optional[str]] = [None, None]
    for idx, g_args in enumerate((["symbolic-ref", "--short", "-q", "HEAD"], ["rev-parse", "HEAD"])):
        try:
            proc = _run_subprocess(["git", "-C", repo_root_str, *g_args], extra_env={"GIT_TERMINAL_PROMPT": "0"}, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                git_res[idx] = proc.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
    eff_target = os.path.abspath(os.fspath(target_dir)) if target_dir is not None else os.path.abspath(os.path.join(_safe_home_str(), ".gemini", "config", "plugins", spec.plugin_name))
    with exclusive_file_lock(lock_path, timeout_s=5.0):
        raw_obj = _read_json_dict(sources_path)
        sources = dict(raw_obj["sources"]) if (raw_obj and isinstance(raw_obj.get("sources"), dict)) else {}
        sources[spec.plugin_name] = {
            "plugin_name": spec.plugin_name, "repo_dir": repo_root_str, "repository": manifest.get("repository"),
            "install_method": method, "target_dir": eff_target, "git_branch": git_res[0], "git_sha": git_res[1],
            "installed_version": manifest.get("version"), "installed_at": now_iso,
        }
        write_json_atomic(sources_path, {"schema_version": 1, "updated_at": now_iso, "sources": sources}, indent=2, ensure_ascii=False)
    return _to_path(sources_path)


def build_parser(spec: InstallSpec) -> argparse.ArgumentParser:
    """Construct the 11-flag CLI parser for a Gravity plugin installer."""
    display = spec.display_name or spec.plugin_name
    parser = argparse.ArgumentParser(
        description=spec.description or f"Install, upgrade, validate, or uninstall the {display} Antigravity plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=spec.epilog or None,
    )
    parser.add_argument("--scope", choices=("global", "workspace"), default="global", help="Install globally or in a project.")
    parser.add_argument("--workspace", type=_to_path, default=None, help="Project workspace directory (--scope workspace).")
    parser.add_argument("--target", type=_to_path, default=None, help="Override exact plugin destination directory.")
    parser.add_argument("--method", choices=("copy", "agy"), default="copy", help="Installation backend: 'copy' or 'agy'.")
    parser.add_argument("--force", action="store_true", help="Upgrade, reinstall, or replace an existing install.")
    parser.add_argument("--skip-validate", action="store_true", help="Skip strict validation during copy install.")
    parser.add_argument("--strict", action="store_true", help="Enforce strict validation (overrides --skip-validate).")
    parser.add_argument("--no-fix-python", action="store_true", help="Skip Windows 'python3' adaptation.")
    parser.add_argument("--dry-run", action="store_true", help="Preview planned operations without modifying files.")
    parser.add_argument("--check", action="store_true", help="Inspect prerequisites and target status read-only.")
    parser.add_argument("--uninstall", action="store_true", help="Safely remove an installed plugin.")
    if spec.extra_cli_fn is not None:
        spec.extra_cli_fn(parser)
    return parser


def run_installer(spec: InstallSpec, argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point for the unified 11-flag Gravity installer CLI."""
    configure_utf8_stdio()
    if sys.version_info < (3, 10):
        return _err("Python 3.10+ is required to run install.py.")
    args = build_parser(spec).parse_args(argv)
    if args.scope == "workspace" and args.workspace is None:
        return _err("--workspace PATH is required when --scope is 'workspace'.", 2)
    if args.method == "agy" and (args.scope == "workspace" or args.target is not None):
        return _err("--method agy only supports --scope global without --target.", 2)
    src_str = os.fspath(spec.plugin_source)
    if not os.path.isdir(src_str) or not _is_installed_plugin(spec, src_str):
        return _err(f"Source plugin directory is missing or invalid: {src_str}")
    target_dir = resolve_target_dir(args.scope, args.workspace, args.target, plugin_name=spec.plugin_name) if args.method == "copy" else None
    if spec.extra_dispatch_fn is not None:
        custom_rc = spec.extra_dispatch_fn(args, spec, target_dir)
        if custom_rc is not None:
            return custom_rc
    if args.check:
        return check_environment(spec, target_dir, args.scope, args.method, strict=args.strict)
    if args.uninstall:
        rc = uninstall_plugin(spec, target_dir, method=args.method, dry_run=args.dry_run)
        return spec.post_uninstall_fn(args, spec, target_dir) if (rc == 0 and spec.post_uninstall_fn is not None) else rc
    eff_skip = bool(args.skip_validate and not args.strict)
    rc = install_agy(spec, dry_run=args.dry_run, strict=args.strict) if args.method == "agy" else install_copy(
        spec, target_dir, force=args.force, no_fix_python=args.no_fix_python, skip_validate=eff_skip, dry_run=args.dry_run,  # type: ignore[arg-type]
    )
    if rc != 0:
        return rc
    if not args.dry_run and spec.record_sources:
        record_source_provenance(spec, method=args.method, target_dir=target_dir)
    if spec.post_install_fn is not None and spec.post_install_fn(args, spec, target_dir) != 0:
        return 1
    if not args.dry_run and spec.next_steps_fn is not None:
        spec.next_steps_fn(target_dir, args)
    return 0
