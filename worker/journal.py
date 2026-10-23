"""The 'journal': one row per support thread, kept in a JSON file.

In the real system this is a Postgres table read by a BI dashboard. Here it is a file
under .data/ so the service tasks have an observable side effect you can cat.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / ".data"
JOURNAL = DATA_DIR / "journal.json"
DIGEST = DATA_DIR / "monday-digest.json"
_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _save(path: Path, obj) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def upsert(thread_id: str, **fields) -> dict:
    with _lock:
        rows = _load(JOURNAL, {})
        row = rows.setdefault(thread_id, {"threadId": thread_id, "reminders": 0})
        row.update(fields)
        row["updatedAt"] = now()
        _save(JOURNAL, rows)
        return dict(row)


def get(thread_id: str) -> dict | None:
    with _lock:
        return _load(JOURNAL, {}).get(thread_id)


def all_rows() -> list[dict]:
    with _lock:
        return list(_load(JOURNAL, {}).values())


def add_to_digest(entry: dict) -> None:
    with _lock:
        items = _load(DIGEST, [])
        items.append({**entry, "addedAt": now()})
        _save(DIGEST, items)


def digest() -> list[dict]:
    with _lock:
        return _load(DIGEST, [])


def reset() -> None:
    with _lock:
        for p in (JOURNAL, DIGEST):
            if p.exists():
                p.unlink()
