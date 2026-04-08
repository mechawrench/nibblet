#!/usr/bin/env python3
import contextlib
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


STATE_DIR_ENV = "NIBBLET_STATE_DIR"
MAX_ENTRIES = 8
MAX_ENTRY_LEN = 88
MAX_MSG_LEN = 23
SESSION_STALE_SECS = 6 * 60 * 60


def default_state_dir() -> Path:
    """Global default state directory shared by the hook and the bridge.

    Both processes need to agree on this, otherwise the hook writes session
    files where the bridge cannot see them. We deliberately do not derive
    this from the current working directory: the hook runs in the project
    cwd while the menu bar app launches from /, so a cwd-based default
    would always desync.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Nibblet"
    return Path.home() / ".nibblet"


def resolve_state_dir(explicit: str | None = None, cwd: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(STATE_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return default_state_dir().expanduser().resolve()


def ensure_state_dir(state_dir: Path) -> None:
    (state_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (state_dir / "decisions").mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def file_lock(state_dir: Path):
    ensure_state_dir(state_dir)
    lock_path = state_dir / ".lock"
    with open(lock_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def session_path(state_dir: Path, session_id: str) -> Path:
    digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()
    return state_dir / "sessions" / f"{digest}.json"


def new_session(session_id: str, cwd: str) -> dict[str, Any]:
    now = time.time()
    return {
        "session_id": session_id,
        "cwd": cwd,
        "active": True,
        "running": False,
        "waiting": False,
        "msg": "Claude connected",
        "entries": [],
        "tokens": 0,
        "prompt": None,
        "created_at": now,
        "updated_at": now,
    }


def truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def set_msg(session: dict[str, Any], text: str) -> None:
    session["msg"] = truncate(text, MAX_MSG_LEN)


def append_entry(session: dict[str, Any], text: str) -> None:
    text = truncate(text, MAX_ENTRY_LEN)
    if not text:
        return
    entries = [entry for entry in session.get("entries", []) if entry != text]
    entries.append(text)
    session["entries"] = entries[-MAX_ENTRIES:]


def update_session(
    state_dir: Path,
    session_id: str,
    cwd: str,
    mutator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    with file_lock(state_dir):
        path = session_path(state_dir, session_id)
        session = read_json(path) or new_session(session_id, cwd)
        if not session.get("cwd"):
            session["cwd"] = cwd
        mutator(session)
        session["updated_at"] = time.time()
        atomic_write_json(path, session)
        return session


def remove_session(state_dir: Path, session_id: str) -> None:
    with file_lock(state_dir):
        path = session_path(state_dir, session_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def list_sessions(state_dir: Path) -> list[dict[str, Any]]:
    with file_lock(state_dir):
        now = time.time()
        sessions: list[dict[str, Any]] = []
        for path in sorted((state_dir / "sessions").glob("*.json")):
            payload = read_json(path)
            if not isinstance(payload, dict):
                continue
            updated_at = float(payload.get("updated_at", 0) or 0)
            if updated_at and now - updated_at > SESSION_STALE_SECS:
                continue
            sessions.append(payload)
        return sessions


def write_decision(state_dir: Path, prompt_id: str, payload: dict[str, Any]) -> None:
    with file_lock(state_dir):
        atomic_write_json(state_dir / "decisions" / f"{prompt_id}.json", payload)


def take_decision(state_dir: Path, prompt_id: str) -> dict[str, Any] | None:
    with file_lock(state_dir):
        path = state_dir / "decisions" / f"{prompt_id}.json"
        payload = read_json(path)
        if payload is None:
            return None
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return payload
