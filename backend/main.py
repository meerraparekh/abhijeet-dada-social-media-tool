"""Satsang Clips - local web app for turning a long recorded session into
short social media clips, full transcripts, and blog posts.

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
from schemas import BlogPost, Clip, Session, VideoAngle
from services import (
    blog_writer,
    clip_suggester,
    clipper,
    docx_export,
    filler_detector,
    sync,
    transcribe,
    translator,
)

app = FastAPI(title="Satsang Clips")

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"

RAW_VIDEO_NAME = "raw.mp4"


def _get_session_or_404(session_id: str) -> Session:
    session = store.load(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return session


def _get_angle_or_404(session: Session, angle_id: str) -> VideoAngle:
    angle = next((a for a in session.angles if a.id == angle_id), None)
    if not angle:
        raise HTTPException(404, "angle not found")
    return angle


# ---------- sessions ----------

@app.get("/api/sessions")
def list_sessions() -> List[Session]:
    return store.list_all()


@app.post("/api/sessions")
async def create_session(name: str, file: UploadFile, label: str = "1x") -> Session:
    """Creates a session with its first (primary) camera angle. Every clip
    timestamp and the transcript itself are anchored to this angle's own
    timeline - additional angles are added afterwards via
    /api/sessions/{id}/angles and synced against this one."""
    session = Session(name=name)
    store.create(session)
    dest = store.session_dir(session.id) / RAW_VIDEO_NAME
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    duration = None
    try:
        duration = sync.probe_duration_seconds(dest)
    except sync.SyncFailed:
        pass  # non-fatal - duration is only used to clamp multi-angle rendering

    session.angles = [
        VideoAngle(
            label=label,
            filename=RAW_VIDEO_NAME,
            is_primary=True,
            offset_seconds=0.0,
            duration_seconds=duration,
            sync_status="primary",
        )
    ]
    store.save(session)
    return session


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> Session:
    return _get_session_or_404(session_id)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    store.delete(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/video")
def get_video(session_id: str, angle_id: Optional[str] = None):
    session = _get_session_or_404(session_id)
    angle = _get_angle_or_404(session, angle_id) if angle_id else session.primary_angle()
    if not angle:
        raise HTTPException(404, "no video for this session")
    return FileResponse(store.session_dir(session_id) / angle.filename)


# ---------- multi-angle upload + sync ----------

@app.post("/api/sessions/{session_id}/angles")
async def add_angle(session_id: str, file: UploadFile, label: str = "angle") -> VideoAngle:
    """Upload an additional camera angle of the same recording and kick off
    audio-based sync against the primary angle in the background. Angles
    commonly have different start times (and may stop recording early) -
    sync_status tracks whether that offset has been resolved yet."""
    session = _get_session_or_404(session_id)
    primary = session.primary_angle()
    if not primary:
        raise HTTPException(400, "session has no primary video yet")

    angle = VideoAngle(label=label, filename="", sync_status="pending")
    dest_name = f"angle_{angle.id}.mp4"
    dest = store.session_dir(session_id) / dest_name
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    angle.filename = dest_name

    try:
        angle.duration_seconds = sync.probe_duration_seconds(dest)
    except sync.SyncFailed as exc:
        angle.sync_status = "failed"
        angle.sync_error = str(exc)

    session.angles.append(angle)
    store.save(session)

    def work(progress_cb):
        progress_cb(f"syncing angle {angle.label!r} against the primary angle")
        s = store.load(session_id)
        a = next(x for x in s.angles if x.id == angle.id)
        p = s.primary_angle()
        try:
            primary_path = store.session_dir(session_id) / p.filename
            angle_path = store.session_dir(session_id) / a.filename
            a.offset_seconds = sync.estimate_offset_seconds(primary_path, angle_path)
            a.sync_status = "synced"
            a.sync_error = None
        except sync.SyncFailed as exc:
            a.sync_status = "failed"
            a.sync_error = str(exc)
        store.save(s)

    jobs.start("sync_angle", session_id, work)
    return angle


@app.delete("/api/sessions/{session_id}/angles/{angle_id}")
def delete_angle(session_id: str, angle_id: str) -> dict:
    session = _get_session_or_404(session_id)
    angle = _get_angle_or_404(session, angle_id)
    if angle.is_primary:
        raise HTTPException(400, "cannot delete the primary angle")
    (store.session_dir(session_id) / angle.filename).unlink(missing_ok=True)
    session.angles = [a for a in session.angles if a.id != angle_id]
    store.save(session)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/angles/{angle_id}/resync")
def resync_angle(session_id: str, angle_id: str) -> dict:
    """Retry sync for an angle that previously failed (e.g. a transient
    ffmpeg error), without re-uploading the file."""
    session = _get_session_or_404(session_id)
    angle = _get_angle_or_404(session, angle_id)
    if angle.is_primary:
        raise HTTPException(400, "primary angle doesn't need syncing")

    def work(progress_cb):
        progress_cb(f"re-syncing angle {angle.label!r}")
        s = store.load(session_id)
        a = next(x for x in s.angles if x.id == angle_id)
        p = s.primary_angle()
        try:
            a.offset_seconds = sync.estimate_offset_seconds(
                store.session_dir(session_id) / p.filename,
                store.session_dir(session_id) / a.filename,
            )
            a.sync_status = "synced"
            a.sync_error = None
        except sync.SyncFailed as exc:
            a.sync_status = "failed"
            a.sync_error = str(exc)
        store.save(s)

    job = jobs.start("sync_angle", session_id, work)
    return {"job_id": job.id}


# ---------- transcription ----------

@app.post("/api/sessions/{session_id}/transcribe")
def start_transcription(session_id: str) -> dict:
    session = _get_session_or_404(session_id)
    primary = session.primary_angle()
    if not primary:
        raise HTTPException(404, "session has no video to transcribe")

    video_path = str(store.session_dir(session_id) / primary.filename)
    session.status = "transcribing"
    store.save(session)

    def work(progress_cb):
        transcript = transcribe.transcribe(video_path, progress_cb)
        s = store.load(session_id)
        s.transcript = transcript
        s.status = "transcribed"
        store.save(s)

    job = jobs.start("transcribe", session_id, work)
    return {"job_id": job.id}


@app.get("/api/sessions/{session_id}/transcript.srt")
def download_srt_hi(session_id: str):
    session = _get_session_or_404(session_id)
    if not session.transcript:
        raise HTTPException(404, "not transcribed yet")
    return _text_response(session_id, transcribe.to_srt(session.transcript), "transcript_hindi.srt")


@app.get("/api/sessions/{session_id}/transcript_en.srt")
def download_srt_en(session_id: str):
    session = _get_session_or_404(session_id)
    if not session.transcript or not session.transcript.segments_en:
        raise HTTPException(404, "English translation not generated yet - run Translate captions first")
    return _text_response(
        session_id, transcribe.segments_to_srt(session.transcript.segments_en), "transcript_english.srt"
    )


@app.get("/api/sessions/{session_id}/transcript.docx")
def download_docx_hi(session_id: str):
    session = _get_session_or_404(session_id)
    if not session.transcript:
        raise HTTPException(404, "not transcribed yet")
    out_path = store.session_dir(session_id) / "exports" / "transcript_hindi.docx"
    docx_export.transcript_segments_to_docx(session.transcript.segments, out_path, f"{session.name} - Hindi Transcript")
    return FileResponse(out_path, filename="transcript_hindi.docx")


@app.get("/api/sessions/{session_id}/transcript_en.docx")
def download_docx_en(session_id: str):
    session = _get_session_or_404(session_id)
    if not session.transcript or not session.transcript.segments_en:
        raise HTTPException(404, "English translation not generated yet - run Translate captions first")
    out_path = store.session_dir(session_id) / "exports" / "transcript_english.docx"
    docx_export.transcript_segments_to_docx(
        session.transcript.segments_en, out_path, f"{session.name} - English Transcript"
    )
    return FileResponse(out_path, filename="transcript_english.docx")


def _text_response(session_id: str, text: str, filename: str):
    out_dir = store.session_dir(session_id) / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(text, encoding="utf-8")
    return FileResponse(out_path, filename=filename, media_type="text/plain")


# ---------- clip suggestion ----------

@app.post("/api/sessions/{session_id}/suggest-clips")
def suggest_clips(session_id: str) -> dict:
    session = _get_session_or_404(session_id)
    if not session.transcript:
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


# ---------- caption translation ----------

@app.post("/api/sessions/{session_id}/translate-captions")
def translate_captions(session_id: str) -> dict:
    session = _get_session_or_404(session_id)
    if not session.transcript:
        raise HTTPException(400, "session must be transcribed first")

    def work(progress_cb):
        progress_cb("asking Claude to translate the transcript to English")
        translations = translator.translate_segments(session.transcript)
        caption_words = translator.build_english_caption_words(session.transcript, translations)
        s = store.load(session_id)
        s.transcript.caption_words_en = caption_words
        s.transcript.segments_en = [
            seg.model_copy(update={"text": text})
            for seg, text in zip(s.transcript.segments, translations)
        ]
        store.save(s)

    job = jobs.start("translate_captions", session_id, work)
    return {"job_id": job.id}


# ---------- blog posts ----------

@app.post("/api/sessions/{session_id}/generate-blog-posts")
def generate_blog_posts(session_id: str) -> dict:
    session = _get_session_or_404(session_id)
    if not session.transcript:
        raise HTTPException(400, "session must be transcribed first")

    def work(progress_cb):
        progress_cb("asking Claude to write blog posts from the transcript")
        suggestions = blog_writer.generate_blog_posts(session.transcript)
        s = store.load(session_id)
        s.blog_posts = [BlogPost(**p.model_dump()) for p in suggestions.posts]
        store.save(s)

    job = jobs.start("generate_blog_posts", session_id, work)
    return {"job_id": job.id}


@app.get("/api/sessions/{session_id}/blog_posts.docx")
def download_blog_posts_docx(session_id: str):
    session = _get_session_or_404(session_id)
    if not session.blog_posts:
        raise HTTPException(404, "no blog posts generated yet")
    out_path = store.session_dir(session_id) / "exports" / "blog_posts.docx"
    docx_export.blog_posts_to_docx(session.blog_posts, out_path, session.name)
    return FileResponse(out_path, filename="blog_posts.docx")


# ---------- clip editing ----------

class ClipUpdate(BaseModel):
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    title: Optional[str] = None
    hook_hi: Optional[str] = None
    hook_en: Optional[str] = None
    hook_start_seconds: Optional[float] = None
    hook_end_seconds: Optional[float] = None
    youtube_title_hi: Optional[str] = None
    youtube_title_en: Optional[str] = None
    instagram_caption_hi: Optional[str] = None
    instagram_caption_en: Optional[str] = None
    twitter_text_hi: Optional[str] = None
    twitter_text_en: Optional[str] = None
    hashtags: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    assignee: Optional[str] = None
    status: Optional[str] = None


@app.patch("/api/sessions/{session_id}/clips/{clip_id}")
def update_clip(session_id: str, clip_id: str, update: ClipUpdate) -> Clip:
    session = _get_session_or_404(session_id)
    for clip in session.clips:
        if clip.id == clip_id:
            for k, v in update.model_dump(exclude_unset=True).items():
                setattr(clip, k, v)
            store.save(session)
            return clip
    raise HTTPException(404, "clip not found")


@app.post("/api/sessions/{session_id}/clips")
def add_clip(session_id: str, clip: Clip) -> Clip:
    session = _get_session_or_404(session_id)
    session.clips.append(clip)
    store.save(session)
    return clip


@app.delete("/api/sessions/{session_id}/clips/{clip_id}")
def delete_clip(session_id: str, clip_id: str) -> dict:
    session = _get_session_or_404(session_id)
    session.clips = [c for c in session.clips if c.id != clip_id]
    store.save(session)
    return {"ok": True}


# ---------- rendering ----------

class RenderRequest(BaseModel):
    angle_ids: List[str]
    remove_silence: bool = True
    # Off by default: unlike silence removal this makes a small Claude API
    # call per clip (a few cents at most, but real cost, unlike the free
    # local silence detection), so it's opt-in rather than automatic.
    remove_fillers: bool = False


@app.post("/api/sessions/{session_id}/clips/{clip_id}/render")
def render_clip(session_id: str, clip_id: str, req: RenderRequest) -> dict:
    session = _get_session_or_404(session_id)
    clip = next((c for c in session.clips if c.id == clip_id), None)
    if not clip:
        raise HTTPException(404, "clip not found")
    if not req.angle_ids:
        raise HTTPException(400, "select at least one angle to render")
    for angle_id in req.angle_ids:
        _get_angle_or_404(session, angle_id)

    words = session.transcript.words if session.transcript else []
    # Burned-in captions are the English translation (for a wider audience);
    # gap/silence detection still uses the original Hindi word timing, which
    # is the accurate source for where the real speech is. Falls back to the
    # Hindi words themselves if "Translate captions" hasn't been run yet.
    caption_words = (
        session.transcript.caption_words_en if session.transcript and session.transcript.caption_words_en else words
    )
    clips_dir = store.session_dir(session_id) / "clips"

    clip.status = "rendering"
    store.save(session)

    def work(progress_cb):
        s = store.load(session_id)
        c = next(x for x in s.clips if x.id == clip_id)

        extra_cut_ranges = None
        if req.remove_fillers:
            progress_cb("asking Claude to flag filler words/mistakes")
            extra_cut_ranges = filler_detector.detect_removable_spans(
                words, c.start_seconds, c.end_seconds
            )

        skipped = []
        for angle_id in req.angle_ids:
            angle = next(a for a in s.angles if a.id == angle_id)
            progress_cb(f"rendering angle {angle.label!r}")
            raw_video_path = store.session_dir(session_id) / angle.filename
            try:
                out_path = clipper.render_clip_for_angle(
                    raw_video_path, c, angle_id, words, clips_dir,
                    offset_seconds=angle.offset_seconds,
                    angle_duration=angle.duration_seconds,
                    remove_silence=req.remove_silence,
                    caption_words=caption_words,
                    extra_cut_ranges=extra_cut_ranges,
                )
            except clipper.ClipNotAvailable as exc:
                skipped.append(f"{angle.label}: {exc}")
                continue
            c.rendered_files[angle_id] = str(out_path.relative_to(store.session_dir(session_id)))

        if skipped and len(skipped) == len(req.angle_ids):
            c.status = "claimed"
            store.save(s)
            raise RuntimeError("; ".join(skipped))
        c.status = "done"
        if skipped:
            progress_cb("done, but skipped: " + "; ".join(skipped))
        store.save(s)

    job = jobs.start("render_clip", session_id, work)
    return {"job_id": job.id}


@app.get("/api/sessions/{session_id}/clips/{clip_id}/download/{angle_id}")
def download_clip(session_id: str, clip_id: str, angle_id: str):
    session = _get_session_or_404(session_id)
    clip = next((c for c in session.clips if c.id == clip_id), None)
    if not clip or angle_id not in clip.rendered_files:
        raise HTTPException(404, "rendered clip not found")
    path = store.session_dir(session_id) / clip.rendered_files[angle_id]
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
