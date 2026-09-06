"""End-to-end smoke test for the upload -> transcribe -> suggest -> render pipeline.

Runs against a synthetic test video (see scripts/make_test_clip.sh) and fakes
the two steps that talk to the network (Whisper's model download, and the
Claude API call) so this test works offline. It still exercises every other
line: FastAPI routes, the background job runner, the JSON session store, and
real ffmpeg cutting/cropping/caption-burning.

Run with: SATSANG_TEST_VIDEO=/path/to/test_session.mp4 pytest tests/test_pipeline.py -v
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

TEST_VIDEO = os.environ.get("SATSANG_TEST_VIDEO", "/tmp/test_session.mp4")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SATSANG_DATA_DIR", str(tmp_path / "data"))

    # Reload config/store/main fresh so they pick up the tmp data dir.
    for mod in list(sys.modules):
        if mod in ("config", "store", "main", "jobs", "schemas") or mod.startswith("services"):
            sys.modules.pop(mod, None)

    import main as main_module
    from schemas import Transcript, TranscriptSegment, TranscriptWord, ClipSuggestion, ClipSuggestions

    # --- fake the network-dependent steps ---
    def fake_transcribe(video_path, progress_cb=None):
        text = (
            "Welcome everyone to this evening's session. Tonight I want to speak about "
            "the nature of stillness. When the mind becomes quiet, we begin to see things "
            "as they really are."
        )
        words_text = text.replace(".", "").split()
        n = len(words_text)
        duration = 20.0
        words = []
        segs = []
        step = duration / n
        for i, w in enumerate(words_text):
            words.append(TranscriptWord(start=i * step, end=(i + 1) * step, word=" " + w))
        # two segments for variety
        segs.append(TranscriptSegment(start=0.0, end=duration / 2, text=" ".join(words_text[: n // 2])))
        segs.append(TranscriptSegment(start=duration / 2, end=duration, text=" ".join(words_text[n // 2 :])))
        if progress_cb:
            progress_cb("fake transcription complete")
        return Transcript(language="en", segments=segs, words=words)

    def fake_suggest(transcript):
        last_word_end = transcript.words[-1].end
        return ClipSuggestions(
            clips=[
                ClipSuggestion(
                    start_seconds=0.0,
                    end_seconds=min(8.0, last_word_end),
                    title="Opening",
                    hook="Welcome everyone to this evening's session.",
                    youtube_title="A Talk on Stillness",
                    instagram_caption="Tonight's opening thought 🙏",
                    twitter_text="On the nature of stillness.",
                    hashtags=["satsang", "meditation"],
                )
            ]
        )

    monkeypatch.setattr(main_module.transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(main_module.clip_suggester, "suggest_clips", fake_suggest)

    from fastapi.testclient import TestClient

    return TestClient(main_module.app)


def _wait_for_job(client, job_id, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] != "running":
            return job
        time.sleep(0.2)
    raise TimeoutError(f"job {job_id} did not finish in time")


@pytest.mark.skipif(not Path(TEST_VIDEO).exists(), reason=f"no test video at {TEST_VIDEO}")
def test_full_pipeline(client):
    # 1. create session (upload)
    with open(TEST_VIDEO, "rb") as f:
        res = client.post(
            "/api/sessions",
            params={"name": "Test Session"},
            files={"file": ("test_session.mp4", f, "video/mp4")},
        )
    assert res.status_code == 200, res.text
    session = res.json()
    session_id = session["id"]
    assert session["status"] == "uploaded"

    # 2. transcribe (faked)
    res = client.post(f"/api/sessions/{session_id}/transcribe")
    job = _wait_for_job(client, res.json()["job_id"])
    assert job["state"] == "done", job

    session = client.get(f"/api/sessions/{session_id}").json()
    assert session["status"] == "transcribed"
    assert len(session["transcript"]["segments"]) == 2

    # 3. suggest clips (faked)
    res = client.post(f"/api/sessions/{session_id}/suggest-clips")
    job = _wait_for_job(client, res.json()["job_id"])
    assert job["state"] == "done", job

    session = client.get(f"/api/sessions/{session_id}").json()
    assert len(session["clips"]) == 1
    clip = session["clips"][0]
    assert clip["hook"] == "Welcome everyone to this evening's session."

    # 4. edit the clip via PATCH
    res = client.patch(
        f"/api/sessions/{session_id}/clips/{clip['id']}",
        json={"assignee": "Priya", "status": "claimed"},
    )
    assert res.status_code == 200
    assert res.json()["assignee"] == "Priya"

    # 5. render for all three platforms - this is real ffmpeg, not faked
    res = client.post(
        f"/api/sessions/{session_id}/clips/{clip['id']}/render",
        json=["youtube", "instagram_reel", "twitter"],
    )
    job = _wait_for_job(client, res.json()["job_id"], timeout=120)
    assert job["state"] == "done", job

    session = client.get(f"/api/sessions/{session_id}").json()
    clip = session["clips"][0]
    assert clip["status"] == "done"
    assert set(clip["rendered_files"].keys()) == {"youtube", "instagram_reel", "twitter"}

    data_dir = Path(os.environ["SATSANG_DATA_DIR"])
    expected_dims = {
        "youtube": (1920, 1080),
        "instagram_reel": (1080, 1920),
        "twitter": (1280, 720),
    }
    for platform, rel_path in clip["rendered_files"].items():
        out_path = data_dir / "sessions" / session_id / rel_path
        assert out_path.exists() and out_path.stat().st_size > 0

        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0", str(out_path),
            ],
            stdout=subprocess.PIPE, text=True, check=True,
        )
        w, h = map(int, probe.stdout.strip().split(","))
        assert (w, h) == expected_dims[platform], (platform, w, h)

        # download endpoint should serve the same file
        res = client.get(f"/api/sessions/{session_id}/clips/{clip['id']}/download/{platform}")
        assert res.status_code == 200
        assert len(res.content) == out_path.stat().st_size

    # 6. delete the session cleans up
    res = client.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
