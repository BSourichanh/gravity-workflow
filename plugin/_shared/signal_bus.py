"""Versioned atomic signal bus (signal_v1) under $GRAVITY_HOME/signals/ (Python 3.10+ stdlib)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path, PosixPath, PureWindowsPath
import re
from typing import Any, Optional, Union

from .atomic_io import write_json_atomic
from .file_lock import exclusive_file_lock
from .path_safety import (
    ensure_no_symlink_or_junction_under,
    ensure_safe_dir,
    is_symlink_or_junction,
    safe_home_dir,
)

SIGNAL_SCHEMA_VERSION = 1
SIGNALS_SUBDIR = "signals"
GRAVITY_HOME_ENV = "GRAVITY_HOME"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _safe_home_str() -> str:
    hp = safe_home_dir()
    return hp.as_posix() if (os.sep == "/" and isinstance(hp, PureWindowsPath)) else os.fspath(hp)


def _expand_user_str(raw: Union[str, Path]) -> str:
    s = os.fspath(raw)
    if s == "~":
        return _safe_home_str()
    if s.startswith(("~/", "~\\")):
        return os.path.join(_safe_home_str(), s[2:])
    return os.path.expanduser(s)


def _to_path(raw: str) -> Path:
    return PosixPath(raw) if os.sep == "/" else Path(raw)


def validate_signal_slug(value: Any, *, kind: str = "slug") -> str:
    """Validate strict kebab-case signal topic/slug, rejecting traversal, slashes, NUL, and whitespace."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid signal {kind}: must be a non-empty kebab-case string.")
    if "\x00" in value or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"Unsafe path character or traversal in signal {kind}: {value!r}")
    if any(ch.isspace() for ch in value) or not SLUG_RE.match(value):
        raise ValueError(
            f"Invalid signal {kind} {value!r}: must match {SLUG_RE.pattern}."
        )
    return value


def resolve_signals_dir(gravity_home: Optional[Union[str, Path]] = None) -> Path:
    """Resolve $GRAVITY_HOME/signals (or ~/.gemini/gravity/signals), rejecting symlinks/junctions."""
    if gravity_home is not None:
        raw_home = os.fspath(gravity_home).strip()
        if not raw_home:
            raise ValueError("gravity_home must not be empty.")
        base_str = os.path.abspath(_expand_user_str(raw_home))
    else:
        env_home = os.environ.get(GRAVITY_HOME_ENV, "").strip()
        if env_home:
            base_str = os.path.abspath(_expand_user_str(env_home))
        else:
            base_str = os.path.abspath(os.path.join(_safe_home_str(), ".gemini", "gravity"))

    if is_symlink_or_junction(base_str):
        raise ValueError(f"Refusing symlink or junction GRAVITY_HOME: {base_str}")
    signals_str = os.path.join(base_str, SIGNALS_SUBDIR)
    if is_symlink_or_junction(signals_str):
        raise ValueError(f"Refusing symlink or junction signals directory: {signals_str}")
    return _to_path(signals_str)


def _validate_signal_envelope(
    data: Any,
    *,
    expected_topic: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return data if it conforms to signal_v1 schema, otherwise None."""
    if not isinstance(data, dict):
        return None
    if type(data.get("schema_version")) is not int or data.get("schema_version") != SIGNAL_SCHEMA_VERSION:
        return None
    topic = data.get("topic")
    if not isinstance(topic, str) or not SLUG_RE.match(topic):
        return None
    if expected_topic is not None and topic != expected_topic:
        return None
    producer = data.get("producer")
    if not isinstance(producer, str) or not producer.strip() or "\x00" in producer:
        return None
    emitted_at = data.get("emitted_at")
    if not isinstance(emitted_at, str) or not emitted_at.strip() or "\x00" in emitted_at:
        return None
    sample_count = data.get("sample_count")
    if type(sample_count) is not int or sample_count < 0:
        return None
    window_size = data.get("window_size")
    if type(window_size) is not int or window_size < 0:
        return None
    if not isinstance(data.get("payload"), dict):
        return None
    return data


def _read_signal_file(path: Path, expected_topic: str) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return _validate_signal_envelope(parsed, expected_topic=expected_topic)


def publish_signal(
    topic: str,
    slug: str,
    producer: str,
    payload: dict[str, Any],
    *,
    sample_count: int = 0,
    window_size: int = 0,
    emitted_at: Optional[str] = None,
    gravity_home: Optional[Union[str, Path]] = None,
    timeout_s: float = 5.0,
) -> Path:
    """Atomically publish a signal_v1 envelope under an exclusive file lock."""
    clean_topic = validate_signal_slug(topic, kind="topic")
    clean_slug = validate_signal_slug(slug, kind="slug")
    if not isinstance(producer, str) or not producer.strip() or "\x00" in producer:
        raise ValueError("Signal producer must be a non-empty string.")
    if not isinstance(payload, dict):
        raise ValueError("Signal payload must be a dict.")
    if type(sample_count) is not int or sample_count < 0:
        raise ValueError("sample_count must be a non-negative integer.")
    if type(window_size) is not int or window_size < 0:
        raise ValueError("window_size must be a non-negative integer.")
    if emitted_at is None:
        emitted_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif isinstance(emitted_at, str) and emitted_at.strip() and "\x00" not in emitted_at:
        emitted_ts = emitted_at.strip()
    else:
        raise ValueError("emitted_at must be a non-empty ISO-8601 string or None.")

    signals_dir = resolve_signals_dir(gravity_home)
    topic_dir = signals_dir / clean_topic
    target_path = topic_dir / f"{clean_slug}.json"
    lock_path = topic_dir / f".{clean_slug}.lock"

    ensure_no_symlink_or_junction_under(signals_dir, target_path)
    ensure_no_symlink_or_junction_under(signals_dir, lock_path)
    ensure_safe_dir(topic_dir, root=signals_dir)

    envelope: dict[str, Any] = {
        "schema_version": SIGNAL_SCHEMA_VERSION,
        "topic": clean_topic,
        "producer": producer.strip(),
        "emitted_at": emitted_ts,
        "sample_count": sample_count,
        "window_size": window_size,
        "payload": payload,
    }

    with exclusive_file_lock(lock_path, timeout_s=timeout_s):
        ensure_no_symlink_or_junction_under(signals_dir, target_path)
        write_json_atomic(
            target_path,
            envelope,
            indent=2,
            sort_keys=False,
            ensure_ascii=False,
            reject_symlink=True,
        )
    return target_path


def read_signal(
    topic: str,
    slug: str,
    *,
    gravity_home: Optional[Union[str, Path]] = None,
) -> Optional[dict[str, Any]]:
    """Read and validate a signal_v1 envelope; raises on traversal/symlinks, returns None if missing/invalid."""
    clean_topic = validate_signal_slug(topic, kind="topic")
    clean_slug = validate_signal_slug(slug, kind="slug")
    signals_dir = resolve_signals_dir(gravity_home)
    target_path = signals_dir / clean_topic / f"{clean_slug}.json"
    ensure_no_symlink_or_junction_under(signals_dir, target_path)
    return _read_signal_file(target_path, clean_topic)


def list_topic_signals(
    topic: str,
    *,
    gravity_home: Optional[Union[str, Path]] = None,
) -> list[dict[str, Any]]:
    """List valid signal_v1 envelopes for a topic, rejecting symlinks/junctions and path traversal."""
    clean_topic = validate_signal_slug(topic, kind="topic")
    signals_dir = resolve_signals_dir(gravity_home)
    topic_dir = signals_dir / clean_topic
    ensure_no_symlink_or_junction_under(signals_dir, topic_dir)
    if not topic_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for entry in sorted(topic_dir.iterdir(), key=lambda p: p.name):
        if is_symlink_or_junction(entry):
            raise ValueError(f"Refusing symlink or junction in signal topic dir: {entry}")
        if entry.is_file() and entry.suffix == ".json" and SLUG_RE.match(entry.stem):
            envelope = _read_signal_file(entry, clean_topic)
            if envelope is not None:
                results.append(envelope)
    return results
