"""Regression test for a bug this project's own earlier fix introduced: a
background sync job lives only in memory (jobs.py), so a server restart (or
the --reload auto-restart on a code change) silently kills any job still
running - but the angle's own sync_status is persisted and was left showing
"pending" forever. Nothing in the UI offers a way to retry a "pending"
angle (only "failed" has a Retry button), and a "pending" angle can't be
deleted either (see test_delete_guards.py) - so an interrupted sync left
the angle permanently stuck with no way out. main._reconcile_interrupted_jobs
runs at startup and flips any "pending" angle to "failed" (since at startup
time no job has run yet, "pending" can only mean orphaned), making it both
retryable and deletable again."""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


@pytest.fixture()
def main_module(tmp_path, monkeypatch):
    monkeypatch.setenv("SATSANG_DATA_DIR", str(tmp_path / "data"))
    for mod in list(sys.modules):
        if mod in ("config", "store", "main", "jobs", "schemas") or mod.startswith("services"):
            sys.modules.pop(mod, None)
    import main as m
    return m


def test_reconcile_flips_stuck_pending_angles_to_failed(main_module):
    import store
    from schemas import Session, VideoAngle

    s = Session(name="s")
    s.angles = [
        VideoAngle(label="1x", filename="raw.mp4", is_primary=True, sync_status="primary"),
        VideoAngle(label="2x", filename="angle_a.mp4", sync_status="pending"),
        VideoAngle(label="4x", filename="angle_b.mp4", sync_status="synced", offset_seconds=1.0),
    ]
    store.create(s)

    main_module._reconcile_interrupted_jobs()

    reloaded = store.load(s.id)
    by_label = {a.label: a for a in reloaded.angles}
    assert by_label["1x"].sync_status == "primary"  # untouched
    assert by_label["4x"].sync_status == "synced"  # untouched
    assert by_label["2x"].sync_status == "failed"
    assert "restart" in by_label["2x"].sync_error.lower()


def test_reconciled_angle_is_then_deletable_and_retryable(main_module):
    import store
    from schemas import Session, VideoAngle
    from fastapi.testclient import TestClient

    s = Session(name="s")
    s.angles = [
        VideoAngle(label="1x", filename="raw.mp4", is_primary=True, sync_status="primary"),
        VideoAngle(label="2x", filename="angle_a.mp4", sync_status="pending"),
    ]
    store.create(s)
    stuck_angle_id = s.angles[1].id

    main_module._reconcile_interrupted_jobs()

    client = TestClient(main_module.app)
    res = client.delete(f"/api/sessions/{s.id}/angles/{stuck_angle_id}")
    assert res.status_code == 200, res.text


def test_app_startup_actually_runs_the_reconcile(main_module):
    """Confirms the handler is really wired into app startup, not just
    correct in isolation."""
    import store
    from schemas import Session, VideoAngle
    from fastapi.testclient import TestClient

    s = Session(name="s")
    s.angles = [VideoAngle(label="2x", filename="angle_a.mp4", sync_status="pending")]
    store.create(s)

    with TestClient(main_module.app):
        pass  # entering/exiting the context manager runs startup/shutdown

    reloaded = store.load(s.id)
    assert reloaded.angles[0].sync_status == "failed"
