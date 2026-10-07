"""Satsang Clips - local web app for turning a long recorded session into
short social media clips, full transcripts, and blog posts.

Run with: uvicorn main:app --reload --host 0.0.0.0 --port 8000
Then open http://localhost:8000 (or http://<your-lan-ip>:8000 from another
machine on the same network) in a browser - works the same on Mac and Windows
since it's just a browser hitting a local server.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, List, Optional, TypeVar

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
    video_concat,
)


def _reconcile_interrupted_jobs() -> None:
    """Background jobs live only in memory (jobs.py) - a server restart
    (including the --reload auto-restart on a code change) silently kills
    any job that was still running, but an angle's own sync_status is
    persisted and would otherwise be left showing "pending" forever: no job
    is actually working on it anymore, yet nothing in the UI offers a way
    to retry a "pending" angle (only a "failed" one has a Retry button) -
    and a "pending" angle can't be deleted either. At the moment the server
    starts, zero jobs have run yet, so any angle still marked "pending" is
    unambiguously orphaned from a previous run - flip it to "failed" so
    it's both retryable and deletable again."""
    for session in store.list_all():
        changed = False
        for angle in session.angles:
            if angle.sync_status == "pending":
                angle.sync_status = "failed"
                angle.sync_error = "Interrupted by a server restart - click Retry sync."
                changed = True
        if changed:
            store.save(session)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _reconcile_interrupted_jobs()
    yield


app = FastAPI(title="Satsang Clips", lifespan=_lifespan)

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


_T = TypeVar("_T")


def _retry_on_locked_file(fn: Callable[[], _T], attempts: int = 3, delay_seconds: float = 0.5) -> _T:
    """Deleting a video file right after the browser's own player stops
    showing it is a very common path (the "Delete session"/"Remove"
    buttons always act on whatever's currently displayed) - the frontend
    unloads the player first, but closing that connection server-side
    isn't necessarily instant, and another tab/window could still have it
    open too. A couple of short retries absorbs that without the caller
    needing to think about it; a lock that's still held after this many
    attempts is treated as a real "try again later" case."""
    for attempt in range(attempts):
        try:
            return fn()
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_seconds)


async def _save_upload_parts(session_id: str, prefix: str, files: List[UploadFile]) -> List[Path]:
    """Streams each uploaded file to its own part file on disk (so a split
    recording's pieces can be joined afterwards) and returns their paths, in
    the same order the files were given."""
    paths = []
    for i, f in enumerate(files):
        dest = store.session_dir(session_id) / f"{prefix}_part{i}.mp4"
        with open(dest, "wb") as out:
            while chunk := await f.read(1024 * 1024):
                out.write(chunk)
        paths.append(dest)
    return paths


def _concat_probe_and_sync(
    session_id: str,
    angle_id: str,
    part_paths: List[Path],
    final_path: Path,
    progress_cb,
    sync_against_primary: bool,
) -> None:
    """Join `part_paths` (one or more - several if this angle's recording
    was split across files) into `final_path`, probe its duration, and - for
    a non-primary angle - sync it against the primary angle's audio. Used
    both for a brand new angle and for appending more footage to an
    existing one."""
    s = store.load(session_id)
    a = next(x for x in s.angles if x.id == angle_id)

    try:
        if len(part_paths) > 1:
            progress_cb(f"joining {len(part_paths)} video parts for angle {a.label!r}")
        video_concat.concat_videos(part_paths, final_path)
    except video_concat.ConcatFailed as exc:
        a.sync_status = "failed"
        a.sync_error = str(exc)
        store.save(s)
        return

    a.filename = final_path.name
    try:
        a.duration_seconds = sync.probe_duration_seconds(final_path)
    except sync.SyncFailed as exc:
        a.sync_status = "failed"
        a.sync_error = str(exc)
        store.save(s)
        return

    if not sync_against_primary:
        a.sync_status = "primary"
        a.sync_error = None
        store.save(s)
        return

    progress_cb(f"syncing angle {a.label!r} against the primary angle")
    p = s.primary_angle()
    try:
        a.offset_seconds = sync.estimate_offset_seconds(
            store.session_dir(session_id) / p.filename, final_path
        )
        a.sync_status = "synced"
        a.sync_error = None
    except sync.SyncFailed as exc:
        a.sync_status = "failed"
        a.sync_error = str(exc)
    store.save(s)


# ---------- sessions ----------

@app.get("/api/sessions")
def list_sessions() -> List[Session]:
    return store.list_all()


@app.post("/api/sessions")
async def create_session(name: str, files: List[UploadFile], label: str = "1x") -> Session:
    """Creates a session with its first (primary) camera angle. Every clip
    timestamp and the transcript itself are anchored to this angle's own
    timeline - additional angles are added afterwards via
    /api/sessions/{id}/angles and synced against this one.

    Accepts more than one file in case the primary recording itself was
    split (e.g. the camera stopped and was restarted) - parts are joined
    into one continuous file, in the order given."""
    if not files:
        raise HTTPException(400, "select at least one video file")
    session = Session(name=name)
    store.create(session)
    dest = store.session_dir(session.id) / RAW_VIDEO_NAME

    part_paths = await _save_upload_parts(session.id, "primary", files)
    try:
        video_concat.concat_videos(part_paths, dest)
    except video_concat.ConcatFailed as exc:
        store.delete(session.id)
        raise HTTPException(400, f"Could not process the uploaded video(s): {exc}")

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
    try:
        _retry_on_locked_file(lambda: store.delete(session_id))
    except OSError as exc:
        raise HTTPException(
            409,
            f"couldn't delete this session - one of its files may still be open in another "
            f"process (e.g. a sync or render still running). Try again in a moment. ({exc})",
        )
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
async def add_angle(session_id: str, files: List[UploadFile], label: str = "angle") -> VideoAngle:
    """Upload an additional camera angle of the same recording and kick off
    audio-based sync against the primary angle in the background. Angles
    commonly have different start times (and may stop recording early) -
    sync_status tracks whether that offset has been resolved yet.

    Accepts more than one file in case this angle's own recording was split
    (e.g. its camera stopped and was restarted) - parts are joined into one
    continuous file, in the order given, before syncing."""
    session = _get_session_or_404(session_id)
    primary = session.primary_angle()
    if not primary:
        raise HTTPException(400, "session has no primary video yet")
    if not files:
        raise HTTPException(400, "select at least one video file")

    angle = VideoAngle(label=label, filename="", sync_status="pending")
    part_paths = await _save_upload_parts(session_id, f"angle_{angle.id}", files)
    final_path = store.session_dir(session_id) / f"angle_{angle.id}.mp4"

    session.angles.append(angle)
    store.save(session)

    def work(progress_cb):
        _concat_probe_and_sync(
            session_id, angle.id, part_paths, final_path, progress_cb, sync_against_primary=True
        )

    jobs.start("add_angle", session_id, work)
    return angle


@app.post("/api/sessions/{session_id}/angles/{angle_id}/append")
async def append_angle_footage(session_id: str, angle_id: str, files: List[UploadFile]) -> dict:
    """Add more footage to an angle whose recording turned out to be split
    across several files - e.g. a camera stopped partway through and you
    only found the second file afterwards. The new part(s) are joined onto
    the angle's existing file, in the order given, and the angle is
    re-synced (for the primary angle, just re-probed - it doesn't sync
    against itself)."""
    session = _get_session_or_404(session_id)
    angle = _get_angle_or_404(session, angle_id)
    if not files:
        raise HTTPException(400, "select at least one video file")
    if angle.sync_status == "pending":
        raise HTTPException(400, "this angle is still being processed - wait for it to finish first")

    existing_path = store.session_dir(session_id) / angle.filename
    new_part_paths = await _save_upload_parts(session_id, f"angle_{angle_id}_append", files)
    all_parts = [existing_path] + new_part_paths
    final_path = existing_path  # keep the angle's existing filename

    angle.sync_status = "pending"
    store.save(session)

    def work(progress_cb):
        _concat_probe_and_sync(
            session_id, angle_id, all_parts, final_path, progress_cb,
            sync_against_primary=not angle.is_primary,
        )

    job = jobs.start("append_angle_footage", session_id, work)
    return {"job_id": job.id}


@app.delete("/api/sessions/{session_id}/angles/{angle_id}")
def delete_angle(session_id: str, angle_id: str) -> dict:
    session = _get_session_or_404(session_id)
    angle = _get_angle_or_404(session, angle_id)
    if angle.is_primary:
        raise HTTPException(400, "cannot delete the primary angle")
    if angle.sync_status == "pending":
        raise HTTPException(
            400,
            "this angle is still being processed - wait for it to finish (or fail) before removing it",
        )
    try:
        def _cleanup_angle_files() -> None:
            session_dir = store.session_dir(session_id)
            # Glob by angle id prefix rather than just angle.filename: a
            # concat that failed (e.g. timed out) never reaches the point
            # of recording a filename, leaving it empty - unlinking an
            # empty filename resolves to the session directory itself,
            # which Windows refuses to delete this way ("Access is
            # denied") rather than raising a normal "not found". The
            # original uploaded part files for a failed join are also left
            # on disk deliberately (so nothing already uploaded is lost on
            # failure) and need cleaning up here too, not just the final
            # filename, which never got created for them to replace.
            for f in session_dir.glob(f"angle_{angle_id}*"):
                f.unlink(missing_ok=True)

        _retry_on_locked_file(_cleanup_angle_files)
    except OSError as exc:
        raise HTTPException(
            409,
            f"couldn't delete this angle's video file - it may still be open in another "
            f"process (e.g. a sync still running). Try again in a moment. ({exc})",
        )
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
