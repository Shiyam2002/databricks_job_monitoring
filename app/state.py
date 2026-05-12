"""
Persistent monitoring state backed by a local JSON file.

Stores two things:
  last_checked  — ISO-8601 UTC timestamp of the last successful check per key
  alerted_ids   — set of already-alerted IDs per key (capped at 1000)

JSON structure:
{
  "last_checked": {"job_failures": "2026-05-12T10:00:00Z", ...},
  "alerted_ids":  {"job_failures": ["run_id_1", ...], ...}
}

STATE_FILE_PATH env var controls the file location.
Default: monitoring_state.json in the current working directory.

Note: on Databricks Apps (serverless), the filesystem is ephemeral — state resets
on cold start, which means the fallback look-back window (60 min) applies. This is
acceptable: at worst, a burst of re-alerts fires once after a restart.
"""
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_ALERTED_IDS = 1000
_lock = threading.Lock()


def _state_path() -> Path:
    return Path(os.environ.get("STATE_FILE_PATH", "monitoring_state.json"))


def _load() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state file %s: %s — using empty state", p, exc)
    return {"last_checked": {}, "alerted_ids": {}}


def _save(state: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(p)  # atomic on both POSIX and Windows (os.replace / MoveFileExW)
    except OSError as exc:
        logger.error("Could not write state file %s: %s", p, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def get_last_checked(key: str, fallback_minutes: int = 60) -> str:
    """
    Returns the ISO-8601 UTC timestamp of the last successful check for `key`.
    On first run (no record), returns now - fallback_minutes so we catch recent
    failures without flooding with old history.
    """
    with _lock:
        state = _load()
    ts = state.get("last_checked", {}).get(key)
    if ts:
        return ts
    fallback = datetime.now(timezone.utc) - timedelta(minutes=fallback_minutes)
    return fallback.strftime("%Y-%m-%dT%H:%M:%SZ")


def set_last_checked(key: str) -> None:
    """
    Records (now - 30s) as the last successful check time for `key`.
    The 30-second buffer covers clock skew between the app server and Databricks
    warehouse, ensuring we never miss a record that landed just before the query ran.
    """
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _lock:
        state = _load()
        state.setdefault("last_checked", {})[key] = ts
        _save(state)


def get_alerted_ids(key: str) -> set[str]:
    """Returns the set of IDs already alerted for `key`."""
    with _lock:
        state = _load()
    return set(state.get("alerted_ids", {}).get(key, []))


def add_alerted_ids(key: str, ids: set[str]) -> None:
    """
    Persists new IDs into the alerted set for `key`.
    Caps the list at _MAX_ALERTED_IDS, dropping the oldest entries first.
    """
    with _lock:
        state = _load()
        state.setdefault("alerted_ids", {})
        existing = state["alerted_ids"].get(key, [])
        new_entries = [i for i in ids if i not in set(existing)]
        combined = existing + new_entries
        if len(combined) > _MAX_ALERTED_IDS:
            combined = combined[-_MAX_ALERTED_IDS:]
        state["alerted_ids"][key] = combined
        _save(state)
