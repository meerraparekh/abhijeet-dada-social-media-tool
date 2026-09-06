"""Satsang Clips - local web app for turning a long recorded session into
short YouTube / Instagram Reel / Twitter clips.

Run with: uvicorn main:app --reload --host 0.0.0.0 --port 8000
Then open http://localhost:8000 (or http://<your-lan-ip>:8000 from another
machine on the same network) in a browser - works the same on Mac and Windows
since it's just a browser hitting a local server.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import jobs
import store
from schemas import Clip, Session
from services import clip_suggester, clipper, transcribe

app = FastAPI(title="Satsang Clips")

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"

RAW_VIDEO_NAME = "raw.mp4"


# ---------- sessions ----------

@app.get("/api/sessions")
def list_sessions() -> List[Session]:
    return store.list_all()


@app.post("/api/sessions")
async def create_session(name: str, file: UploadFile) -> Session:
    session = Session(name=name)
    store.create(session)
    dest = store.session_dir(session.id) / RAW_VIDEO_NAME
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    session.video_filename = RAW_VIDEO_NAME
    store.save(session)
    return session


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> Session:
    session = store.load(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return session


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    store.delete(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/video")
def get_video(session_id: str):
    session = store.load(session_id)
    if not session or not session.video_filename:
        raise HTTPException(404, "no video for this session")
    return FileResponse(store.session_dir(session_id) / session.video_filename)


# ---------- transcription ----------

@app.post("/api/sessions/{session_id}/transcribe")
def start_transcription(session_id: str) -> dict:
    session = store.load(session_id)
    if not session or not session.video_filename:
        raise HTTPException(404, "session or video not found")

    video_path = str(store.session_dir(session_id) / session.video_filename)
    session.status = "transcribing"
    store.save(session)

    def work(progress_cb):
        transcript = transcribe.transcribe(video_path, progress_cb)
        s = store.load(session_id)
        s.transcript = transcript
        s.status = "transcribed"
        store.save(s)
        srt_path = store.session_dir(session_id) / "transcript.srt"
        srt_path.write_text(transcribe.to_srt(transcript), encoding="utf-8")

    job = jobs.start("transcribe", session_id, work)
    return {"job_id": job.id}


@app.get("/api/sessions/{session_id}/transcript.srt")
def download_srt(session_id: str):
    path = store.session_dir(session_id) / "transcript.srt"
    if not path.exists():
        raise HTTPException(404, "not transcribed yet")
    return FileResponse(path, filename="transcript.srt")


# ---------- clip suggestion ----------

@app.post("/api/sessions/{session_id}/suggest-clips")
def suggest_clips(session_id: str) -> dict:
    session = store.load(session_id)
    if not session or not session.transcript:
        raise HTTPException(400, "session must be transcribed first")

    session.status = "suggesting"
    store.save(session)

    def work(progress_cb):
        progress_cb("asking Claude for clip suggestions")
        suggestions = clip_suggester.suggest_clips(session.transcript)
        s = store.load(session_id)
        s.clips = [Clip(**c.model_dump()) for c in suggestions.clips]
        s.status = "ready"
        store.save(s)

    job = jobs.start("suggest_clips", session_id, work)
    return {"job_id": job.id}


# ---------- clip editing ----------

class ClipUpdate(BaseModel):
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    title: Optional[str] = None
    hook: Optional[str] = None
    youtube_title: Optional[str] = None
    instagram_caption: Optional[str] = None
    twitter_text: Optional[str] = None
    hashtags: Optional[List[str]] = None
    assignee: Optional[str] = None
    status: Optional[str] = None


@app.patch("/api/sessions/{session_id}/clips/{clip_id}")
def update_clip(session_id: str, clip_id: str, update: ClipUpdate) -> Clip:
    session = store.load(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    for clip in session.clips:
        if clip.id == clip_id:
            for k, v in update.model_dump(exclude_unset=True).items():
                setattr(clip, k, v)
            store.save(session)
            return clip
    raise HTTPException(404, "clip not found")


@app.post("/api/sessions/{session_id}/clips")
def add_clip(session_id: str, clip: Clip) -> Clip:
    session = store.load(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    session.clips.append(clip)
    store.save(session)
    return clip


@app.delete("/api/sessions/{session_id}/clips/{clip_id}")
def delete_clip(session_id: str, clip_id: str) -> dict:
    session = store.load(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    session.clips = [c for c in session.clips if c.id != clip_id]
    store.save(session)
    return {"ok": True}


# ---------- rendering ----------

@app.post("/api/sessions/{session_id}/clips/{clip_id}/render")
def render_clip(session_id: str, clip_id: str, platforms: List[str]) -> dict:
    session = store.load(session_id)
    if not session or not session.video_filename:
        raise HTTPException(404, "session or video not found")
    clip = next((c for c in session.clips if c.id == clip_id), None)
    if not clip:
        raise HTTPException(404, "clip not found")

    raw_video_path = store.session_dir(session_id) / session.video_filename
    words = session.transcript.words if session.transcript else []
    clips_dir = store.session_dir(session_id) / "clips"

    clip.status = "rendering"
    store.save(session)

    def work(progress_cb):
        s = store.load(session_id)
        c = next(x for x in s.clips if x.id == clip_id)
        for platform in platforms:
            progress_cb(f"rendering {platform}")
            out_path = clipper.render_clip_for_platform(raw_video_path, c, platform, words, clips_dir)
            c.rendered_files[platform] = str(out_path.relative_to(store.session_dir(session_id)))
        c.status = "done"
        store.save(s)

    job = jobs.start("render_clip", session_id, work)
    return {"job_id": job.id}


@app.get("/api/sessions/{session_id}/clips/{clip_id}/download/{platform}")
def download_clip(session_id: str, clip_id: str, platform: str):
    session = store.load(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    clip = next((c for c in session.clips if c.id == clip_id), None)
    if not clip or platform not in clip.rendered_files:
        raise HTTPException(404, "rendered clip not found")
    path = store.session_dir(session_id) / clip.rendered_files[platform]
    return FileResponse(path, filename=path.name)


# ---------- jobs ----------

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


# ---------- frontend ----------

app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
