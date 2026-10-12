"""Regression coverage for the manual sync-offset override: real multi-camera
audio (different mic quality/distance/room reverb per camera) can fool the
automatic cross-correlation sync into a confidently wrong offset - a real
session had two non-primary angles rendering completely different footage
than intended despite showing a "synced" offset. This is the escape hatch:
let the user set the offset by hand instead of only trusting the algorithm."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _make_clip(path: Path, duration: float, color: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d={duration}",
            "-shortest", "-c:v", "libx264", "-c:a", "aac",
            str(path),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    )


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
    import time
    start = time.time()
    while time.time() - start < timeout:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError("condition not met in time")


def _make_session_with_synced_angle(client, tmp_path):
    primary_file = tmp_path / "primary.mp4"
    _make_clip(primary_file, 4, "blue")
    with open(primary_file, "rb") as f:
        res = client.post(
            "/api/sessions", params={"name": "Offset test"},
            files={"files": ("primary.mp4", f, "video/mp4")},
        )
    session_id = res.json()["id"]

    angle_file = tmp_path / "angle.mp4"
    _make_clip(angle_file, 4, "green")
    with open(angle_file, "rb") as f:
        res = client.post(
            f"/api/sessions/{session_id}/angles", params={"label": "2x"},
            files={"files": ("angle.mp4", f, "video/mp4")},
        )
    angle_id = res.json()["id"]

    def settled():
        s = client.get(f"/api/sessions/{session_id}").json()
        a = next(x for x in s["angles"] if x["id"] == angle_id)
        return a if a["sync_status"] != "pending" else None

    _wait_for(settled)
    return session_id, angle_id


def test_manual_offset_overrides_the_automatic_estimate(client, tmp_path):
    session_id, angle_id = _make_session_with_synced_angle(client, tmp_path)

    res = client.patch(
        f"/api/sessions/{session_id}/angles/{angle_id}/offset",
        json={"offset_seconds": 12.34},
    )
    assert res.status_code == 200, res.text
    angle = res.json()
    assert angle["offset_seconds"] == pytest.approx(12.34)
    assert angle["sync_status"] == "synced"
    assert angle["sync_error"] is None

    # persisted, not just echoed back
    s = client.get(f"/api/sessions/{session_id}").json()
    a = next(x for x in s["angles"] if x["id"] == angle_id)
    assert a["offset_seconds"] == pytest.approx(12.34)


def test_manual_offset_can_recover_a_failed_sync(client, tmp_path):
    session_id, angle_id = _make_session_with_synced_angle(client, tmp_path)

    # simulate a failed auto-sync directly via the store
    import store
    s = store.load(session_id)
    a = next(x for x in s.angles if x.id == angle_id)
    a.sync_status = "failed"
    a.sync_error = "audio too short to search the expected offset range"
    store.save(s)

    res = client.patch(
        f"/api/sessions/{session_id}/angles/{angle_id}/offset",
        json={"offset_seconds": -5.0},
    )
    assert res.status_code == 200, res.text
    angle = res.json()
    assert angle["sync_status"] == "synced"
    assert angle["sync_error"] is None


def test_cannot_set_offset_on_the_primary_angle(client, tmp_path):
    primary_file = tmp_path / "primary.mp4"
    _make_clip(primary_file, 2, "blue")
    with open(primary_file, "rb") as f:
        res = client.post(
            "/api/sessions", params={"name": "s"},
            files={"files": ("primary.mp4", f, "video/mp4")},
        )
    session_id = res.json()["id"]
    primary_id = res.json()["angles"][0]["id"]

    res = client.patch(
        f"/api/sessions/{session_id}/angles/{primary_id}/offset",
        json={"offset_seconds": 1.0},
    )
    assert res.status_code == 400
