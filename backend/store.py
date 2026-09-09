"""Simple JSON-file-backed session storage.

No database needed: each session gets a folder under data/sessions/<id>/ holding
the raw video, transcript, rendered clips, and a session.json with everything else.
This is a single-process local tool used by a handful of people, so a plain
in-process lock around read-modify-write is enough concurrency safety.
"""
import json
import threading
from pathlib import Path
from typing import List, Optional

from config import SESSIONS_DIR
from schemas import Session

_lock = threading.Lock()


def session_dir(session_id: str) -> Path:
    return SESSIONS_DIR / session_id


def _session_file(session_id: str) -> Path:
    return session_dir(session_id) / "session.json"


def create(session: Session) -> Session:
    session_dir(session.id).mkdir(parents=True, exist_ok=True)
    save(session)
    return session


def save(session: Session) -> None:
    with _lock:
        _session_file(session.id).write_text(
            session.model_dump_json(indent=2), encoding="utf-8"
        )


def load(session_id: str) -> Optional[Session]:
    path = _session_file(session_id)
    if not path.exists():
        return None
    return Session.model_validate_json(path.read_text(encoding="utf-8"))


def list_all() -> List[Session]:
    out = []
    for d in sorted(SESSIONS_DIR.iterdir(), reverse=True):
        f = d / "session.json"
        if f.exists():
            out.append(Session.model_validate_json(f.read_text(encoding="utf-8")))
    return out


def delete(session_id: str) -> None:
    import shutil

    d = session_dir(session_id)
    if d.exists():
        shutil.rmtree(d)
