"""End-to-end smoke test for the upload -> transcribe -> translate -> suggest
-> render pipeline.

Runs against a synthetic test video (see scripts/make_test_clip.sh) and fakes
the steps that talk to the network (Whisper's model download, and the two
Claude API calls - clip suggestion and caption translation) so this test
works offline. It still exercises every other line: FastAPI routes, the
background job runner, the JSON session store, and real ffmpeg cutting/
cropping/caption-burning.

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
    from services.blog_writer import BlogPostSuggestion, BlogPostSuggestions

    # --- fake the network-dependent steps ---
    def fake_transcribe(video_path, progress_cb=None):
        # Repeated to comfortably exceed suggest-clips' minimum-content
        # sanity check (calibrated for real multi-hour sessions) - a single
        # short sentence would be legitimately rejected as "too little
        # content to find clips in", which is the guard working as intended,
        # not something this fixture should need to work around by being
        # unrealistically tiny.
        text = (
            "Welcome everyone to this evening's session. Tonight I want to speak about "
            "the nature of stillness. When the mind becomes quiet, we begin to see things "
            "as they really are. "
        ) * 20
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
                    hook_hi="आज शाम सभी का स्वागत है।",
                    hook_en="Welcome everyone to this evening's session.",
                    hook_start_seconds=0.0,
                    hook_end_seconds=2.0,
                    youtube_title_hi="स्थिरता पर एक बात",
                    youtube_title_en="A Talk on Stillness",
                    instagram_caption_hi="आज रात का पहला विचार 🙏",
                    instagram_caption_en="Tonight's opening thought 🙏",
                    twitter_text_hi="स्थिरता की प्रकृति पर।",
                    twitter_text_en="On the nature of stillness.",
                    hashtags=["satsang", "meditation"],
                    tags=["stillness", "meditation-technique"],
                )
            ]
        )

    def fake_translate_segments(transcript):
        return [f"[EN] {seg.text}" for seg in transcript.segments]

    def fake_generate_blog_posts(transcript):
        return BlogPostSuggestions(
            posts=[
                BlogPostSuggestion(
                    title="On Stillness",
                    tags=["stillness"],
                    body=transcript.segments[0].text + "\n\n" + transcript.segments[1].text,
                    source_start_seconds=transcript.segments[0].start,
                    source_end_seconds=transcript.segments[-1].end,
                )
            ]
        )

    monkeypatch.setattr(main_module.transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(main_module.clip_suggester, "suggest_clips", fake_suggest)
    monkeypatch.setattr(main_module.translator, "translate_segments", fake_translate_segments)
    monkeypatch.setattr(main_module.blog_writer, "generate_blog_posts", fake_generate_blog_posts)

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
            files={"files": ("test_session.mp4", f, "video/mp4")},
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

    # 3. translate captions to English (faked translation call, real interpolation logic)
    res = client.post(f"/api/sessions/{session_id}/translate-captions")
    job = _wait_for_job(client, res.json()["job_id"])
    assert job["state"] == "done", job

    session = client.get(f"/api/sessions/{session_id}").json()
    caption_words_en = session["transcript"]["caption_words_en"]
    assert len(caption_words_en) > 0
    # fake_translate_segments prefixed each of the 2 segments with "[EN]" -
    # confirms build_english_caption_words really split per-segment translations
    # into individual timed words rather than treating each segment as one word.
    assert sum(1 for w in caption_words_en if w["word"].strip() == "[EN]") == 2

    # 4. suggest clips (faked)
    res = client.post(f"/api/sessions/{session_id}/suggest-clips")
    job = _wait_for_job(client, res.json()["job_id"])
    assert job["state"] == "done", job

    session = client.get(f"/api/sessions/{session_id}").json()
    assert len(session["clips"]) == 1
    clip = session["clips"][0]
    assert clip["hook_en"] == "Welcome everyone to this evening's session."
    assert clip["hook_hi"] == "आज शाम सभी का स्वागत है।"

    # 5. edit the clip via PATCH
    res = client.patch(
        f"/api/sessions/{session_id}/clips/{clip['id']}",
        json={"assignee": "Priya", "status": "claimed"},
    )
    assert res.status_code == 200
    assert res.json()["assignee"] == "Priya"

    # 6. render for the (only) primary angle - this is real ffmpeg, not faked
    primary_angle_id = session["angles"][0]["id"]
    res = client.post(
        f"/api/sessions/{session_id}/clips/{clip['id']}/render",
        json={"angle_ids": [primary_angle_id]},
    )
    job = _wait_for_job(client, res.json()["job_id"], timeout=120)
    assert job["state"] == "done", job

    session = client.get(f"/api/sessions/{session_id}").json()
    clip = session["clips"][0]
    assert clip["status"] == "done"
    assert set(clip["rendered_files"].keys()) == {primary_angle_id}

    data_dir = Path(os.environ["SATSANG_DATA_DIR"])
    out_path = data_dir / "sessions" / session_id / clip["rendered_files"][primary_angle_id]
    assert out_path.exists() and out_path.stat().st_size > 0

    # single output size everywhere now: vertical 9:16
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0", str(out_path),
        ],
        stdout=subprocess.PIPE, text=True, check=True,
    )
    w, h = map(int, probe.stdout.strip().split(","))
    assert (w, h) == (1080, 1920), (w, h)

    # captions are always burned in and should carry the English translation,
    # not the original transcript text
    srt_path = out_path.with_suffix(".srt")
    assert srt_path.exists()
    assert "[EN]" in srt_path.read_text(encoding="utf-8")

    # download endpoint should serve the same file
    res = client.get(f"/api/sessions/{session_id}/clips/{clip['id']}/download/{primary_angle_id}")
    assert res.status_code == 200
    assert len(res.content) == out_path.stat().st_size

    # 6b. full-session transcript exports
    res = client.get(f"/api/sessions/{session_id}/transcript.srt")
    assert res.status_code == 200 and len(res.text) > 0
    res = client.get(f"/api/sessions/{session_id}/transcript_en.srt")
    assert res.status_code == 200 and "[EN]" in res.text
    res = client.get(f"/api/sessions/{session_id}/transcript.docx")
    assert res.status_code == 200 and len(res.content) > 0
    res = client.get(f"/api/sessions/{session_id}/transcript_en.docx")
    assert res.status_code == 200 and len(res.content) > 0

    # 7. blog post generation (faked Claude call, real docx export)
    res = client.post(f"/api/sessions/{session_id}/generate-blog-posts")
    job = _wait_for_job(client, res.json()["job_id"])
    assert job["state"] == "done", job

    session = client.get(f"/api/sessions/{session_id}").json()
    assert len(session["blog_posts"]) == 1
    assert session["blog_posts"][0]["title"] == "On Stillness"

    res = client.get(f"/api/sessions/{session_id}/blog_posts.docx")
    assert res.status_code == 200 and len(res.content) > 0

    # 8. delete the session cleans up
    res = client.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
