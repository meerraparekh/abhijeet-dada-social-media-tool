"""Local, free transcription via faster-whisper.

Runs entirely on the machine that has the footage - no API cost, no upload of
raw video anywhere. First run downloads the chosen model from Hugging Face
(a few hundred MB) and caches it; after that it works offline.
"""
from __future__ import annotations

from typing import Callable, Optional

from config import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE, WHISPER_MODEL_SIZE
from schemas import Transcript, TranscriptSegment, TranscriptWord

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
        )
    return _model


def transcribe(video_path: str, progress_cb: Optional[Callable[[str], None]] = None) -> Transcript:
    """Transcribe a video/audio file. faster-whisper reads audio directly via ffmpeg,
    so no separate extraction step is needed."""
    model = _get_model()
    segments_iter, info = model.transcribe(
        video_path,
        word_timestamps=True,
        vad_filter=True,  # skip long silences, which a 2-hour recording usually has plenty of
    )

    segments = []
    words = []
    for i, seg in enumerate(segments_iter):
        segments.append(TranscriptSegment(start=seg.start, end=seg.end, text=seg.text.strip()))
        for w in seg.words or []:
            words.append(TranscriptWord(start=w.start, end=w.end, word=w.word))
        if progress_cb and i % 10 == 0:
            progress_cb(f"transcribed up to {seg.end:.0f}s")

    return Transcript(language=info.language, segments=segments, words=words)


def to_srt(transcript: Transcript) -> str:
    """Segment-level SRT, mainly useful as a human-readable transcript export."""
    lines = []
    for i, seg in enumerate(transcript.segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(seg.start)} --> {_srt_ts(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)


def _srt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
