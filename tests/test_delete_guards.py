"""Regression tests for a real bug hit in production: deleting an angle (or
session) while a background sync/upload job still has its video file open
crashed with a raw, unhandled OSError/PermissionError (Windows won't unlink
a file another process has a handle on) instead of a clear error - and
nothing stopped you from trying to delete an angle mid-processing in the
first place."""
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SATSANG_DATA_DIR", str(tmp_path / "data"))
    for mod in list(sys.modules):
        if mod in ("config", "store", "main", "jobs", "schemas") or mod.startswith("services"):
            sys.modules.pop(mod, None)
    import main as main_module
    from fastapi.testclient import TestClient
    return TestClient(main_module.app)


def _wait_for(predicate, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError("condition not met in time")


def test_cannot_delete_an_angle_still_being_processed(client, tmp_path):
    import store
    from schemas import VideoAngle

    res = client.post(
        "/api/sessions", params={"name": "s"},
        files={"files": ("primary.mp4", b"not real but unused here", "video/mp4")},
    )
    session_id = res.json()["id"]

    s = store.load(session_id)
    s.angles.append(VideoAngle(label="2x", filename="angle_x.mp4", sync_status="pending"))
    store.save(s)
    angle_id = s.angles[-1].id

    res = client.delete(f"/api/sessions/{session_id}/angles/{angle_id}")
    assert res.status_code == 400
    assert "still being processed" in res.json()["detail"]


def test_delete_angle_reports_a_locked_file_clearly_instead_of_crashing(client, monkeypatch):
    import store
    from schemas import VideoAngle

    res = client.post(
        "/api/sessions", params={"name": "s"},
        files={"files": ("primary.mp4", b"not real but unused here", "video/mp4")},
    )
    session_id = res.json()["id"]

    s = store.load(session_id)
    angle = VideoAngle(label="2x", filename="angle_x.mp4", sync_status="synced")
    s.angles.append(angle)
    store.save(s)
    (store.session_dir(session_id) / angle.filename).write_bytes(b"stand-in for a locked file")

    import pathlib

    def locked_unlink(self, missing_ok=False):
        raise PermissionError(
            32, "The process cannot access the file because it is being used by another process"
        )

    monkeypatch.setattr(pathlib.Path, "unlink", locked_unlink)

    res = client.delete(f"/api/sessions/{session_id}/angles/{angle.id}")
    assert res.status_code == 409
    assert "open in another process" in res.json()["detail"]

    # and the angle must NOT have been silently dropped from the session on
    # a failed delete
    s = store.load(session_id)
    assert any(a.id == angle.id for a in s.angles)


def test_delete_session_reports_a_locked_file_clearly_instead_of_crashing(client, monkeypatch):
    res = client.post(
        "/api/sessions", params={"name": "s"},
        files={"files": ("primary.mp4", b"not real but unused here", "video/mp4")},
    )
    session_id = res.json()["id"]

    import shutil

    def locked_rmtree(path, *a, **kw):
        raise PermissionError(
            32, "The process cannot access the file because it is being used by another process"
        )

    monkeypatch.setattr(shutil, "rmtree", locked_rmtree)

    res = client.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 409
    assert "open in another process" in res.json()["detail"]


def test_delete_session_retries_and_succeeds_if_the_lock_clears(client, monkeypatch):
    """The frontend now releases its own video connection before deleting,
    but the server-side teardown isn't necessarily instant - a transient
    lock (clears within a couple of retries) should succeed rather than
    surfacing an error the user would have to manually retry themselves."""
    res = client.post(
        "/api/sessions", params={"name": "s"},
        files={"files": ("primary.mp4", b"not real but unused here", "video/mp4")},
    )
    session_id = res.json()["id"]

    import shutil

    calls = {"n": 0}
    real_rmtree = shutil.rmtree

    def flaky_rmtree(path, *a, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real_rmtree(path, *a, **kw)

    monkeypatch.setattr(shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr("main.time.sleep", lambda _: None)  # keep the test fast

    res = client.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 200, res.text
    assert calls["n"] == 2
