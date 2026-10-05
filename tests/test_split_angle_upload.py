"""End-to-end API test for uploading a split recording (several files for
one angle) and for appending more footage to an angle after the fact -
covers the actual FastAPI wiring (multi-file upload params, the background
concat+probe+sync job, the /append endpoint), on top of the lower-level
video_concat unit tests and the sync offset-estimation tests."""
import subprocess
import sys
import time
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
    start = time.time()
    while time.time() - start < timeout:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise TimeoutError("condition not met in time")


def test_primary_upload_accepts_split_recording(client, tmp_path):
    part1 = tmp_path / "part1.mp4"
    part2 = tmp_path / "part2.mp4"
    _make_clip(part1, 2, "blue")
    _make_clip(part2, 2, "red")

    with open(part1, "rb") as f1, open(part2, "rb") as f2:
        res = client.post(
            "/api/sessions",
            params={"name": "Split Primary"},
            files=[
                ("files", ("part1.mp4", f1, "video/mp4")),
                ("files", ("part2.mp4", f2, "video/mp4")),
            ],
        )
    assert res.status_code == 200, res.text
    session = res.json()
    primary = session["angles"][0]
    assert primary["sync_status"] == "primary"
    assert primary["duration_seconds"] == pytest.approx(4.0, abs=0.5)


def test_add_angle_accepts_split_recording_and_syncs(client, tmp_path):
    primary_file = tmp_path / "primary.mp4"
    _make_clip(primary_file, 6, "blue")
    with open(primary_file, "rb") as f:
        res = client.post(
            "/api/sessions", params={"name": "Multi-part angle"},
            files={"files": ("primary.mp4", f, "video/mp4")},
        )
    session_id = res.json()["id"]

    part1 = tmp_path / "angle_part1.mp4"
    part2 = tmp_path / "angle_part2.mp4"
    _make_clip(part1, 3, "green")
    _make_clip(part2, 3, "yellow")
    with open(part1, "rb") as f1, open(part2, "rb") as f2:
        res = client.post(
            f"/api/sessions/{session_id}/angles",
            params={"label": "2x"},
            files=[
                ("files", ("angle_part1.mp4", f1, "video/mp4")),
                ("files", ("angle_part2.mp4", f2, "video/mp4")),
            ],
        )
    assert res.status_code == 200, res.text
    angle_id = res.json()["id"]

    def settled():
        s = client.get(f"/api/sessions/{session_id}").json()
        a = next(x for x in s["angles"] if x["id"] == angle_id)
        return a if a["sync_status"] != "pending" else None

    angle = _wait_for(settled)
    assert angle["sync_status"] == "synced", angle
    assert angle["duration_seconds"] == pytest.approx(6.0, abs=0.5)


def test_append_footage_grows_an_existing_angle(client, tmp_path):
    primary_file = tmp_path / "primary.mp4"
    _make_clip(primary_file, 6, "blue")
    with open(primary_file, "rb") as f:
        res = client.post(
            "/api/sessions", params={"name": "Append test"},
            files={"files": ("primary.mp4", f, "video/mp4")},
        )
    session_id = res.json()["id"]

    angle_part1 = tmp_path / "angle1.mp4"
    _make_clip(angle_part1, 3, "green")
    with open(angle_part1, "rb") as f:
        res = client.post(
            f"/api/sessions/{session_id}/angles", params={"label": "2x"},
            files={"files": ("angle1.mp4", f, "video/mp4")},
        )
    angle_id = res.json()["id"]

    def settled():
        s = client.get(f"/api/sessions/{session_id}").json()
        a = next(x for x in s["angles"] if x["id"] == angle_id)
        return a if a["sync_status"] != "pending" else None

    angle = _wait_for(settled)
    assert angle["sync_status"] == "synced"
    assert angle["duration_seconds"] == pytest.approx(3.0, abs=0.5)

    # the camera stopped - more footage for the same angle turns up later
    angle_part2 = tmp_path / "angle2.mp4"
    _make_clip(angle_part2, 3, "yellow")
    with open(angle_part2, "rb") as f:
        res = client.post(
            f"/api/sessions/{session_id}/angles/{angle_id}/append",
            files={"files": ("angle2.mp4", f, "video/mp4")},
        )
    assert res.status_code == 200, res.text

    angle = _wait_for(settled)
    assert angle["sync_status"] == "synced", angle
    assert angle["duration_seconds"] == pytest.approx(6.0, abs=0.5)

    # appending while a sync is already pending is rejected, not queued
    angle_part3 = tmp_path / "angle3.mp4"
    _make_clip(angle_part3, 1, "purple")
    with open(angle_part3, "rb") as f1, open(angle_part3, "rb") as f2:
        res1 = client.post(
            f"/api/sessions/{session_id}/angles/{angle_id}/append",
            files={"files": ("angle3.mp4", f1, "video/mp4")},
        )
        assert res1.status_code == 200
        res2 = client.post(
            f"/api/sessions/{session_id}/angles/{angle_id}/append",
            files={"files": ("angle3.mp4", f2, "video/mp4")},
        )
        assert res2.status_code == 400
