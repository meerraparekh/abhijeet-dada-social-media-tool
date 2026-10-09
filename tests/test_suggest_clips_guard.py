"""Regression test for a real production bug: "Suggest clips" could
silently "succeed" with zero clips when Whisper hallucinations (see
transcript_hygiene.py) left too little real content to work with after
filtering - the job showed state="done", error=null, with no indication
anything was wrong. Now a transcript that filters down to almost nothing
raises a clear, actionable error instead."""
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


def _wait_for_job(client, job_id, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] != "running":
            return job
        time.sleep(0.2)
    raise TimeoutError(f"job {job_id} did not finish in time")


def test_suggest_clips_errors_clearly_when_transcript_is_mostly_hallucinated(client):
    import store
    from schemas import Transcript, TranscriptSegment

    res = client.post(
        "/api/sessions", params={"name": "s"},
        files={"files": ("primary.mp4", b"not real but unused here", "video/mp4")},
    )
    session_id = res.json()["id"]

    # Models a session where Whisper hallucinated through most of a long
    # recording - only a token amount of real content survives filtering.
    segments = [TranscriptSegment(start=0.0, end=2.0, text="thoda sa asli content")]
    segments += [
        TranscriptSegment(start=float(i), end=float(i + 1), text="저는制eの発値に対応して努力")
        for i in range(1, 200)
    ]
    s = store.load(session_id)
    s.transcript = Transcript(language="hi", segments=segments)
    store.save(s)

    res = client.post(f"/api/sessions/{session_id}/suggest-clips")
    job = _wait_for_job(client, res.json()["job_id"])

    assert job["state"] == "error"
    assert "hallucinations" in job["error"]
    assert "WHISPER_MODEL_SIZE" in job["error"]

    # and clips must NOT have been silently left as an empty-but-"ready" list
    s = store.load(session_id)
    assert s.status != "ready"
