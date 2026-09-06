"""Minimal in-memory background job tracker.

This is a single-process local tool for a handful of editors, not a
production service - a thread per job plus an in-memory status dict is
enough, and avoids pulling in Celery/Redis/etc.
"""
import threading
import traceback
from typing import Callable, Dict

from schemas import JobStatus

_jobs: Dict[str, JobStatus] = {}
_lock = threading.Lock()


def start(kind: str, session_id: str, fn: Callable[[Callable[[str], None]], None]) -> JobStatus:
    job = JobStatus(kind=kind, session_id=session_id)
    with _lock:
        _jobs[job.id] = job

    def progress_cb(msg: str) -> None:
        with _lock:
            _jobs[job.id].progress = msg

    def run() -> None:
        try:
            fn(progress_cb)
            with _lock:
                _jobs[job.id].state = "done"
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            with _lock:
                _jobs[job.id].state = "error"
                _jobs[job.id].error = f"{exc}\n{traceback.format_exc()[-2000:]}"

    threading.Thread(target=run, daemon=True).start()
    return job


def get(job_id: str) -> JobStatus | None:
    with _lock:
        job = _jobs.get(job_id)
        return job.model_copy() if job else None
