"""Sync multiple camera angles of the same recording by cross-correlating
their audio tracks, so a clip's timestamps - found once in the primary
angle's transcript - can be translated into every other angle's own
timeline. Free and local: no cloud alignment service, just ffmpeg for audio
extraction and scipy for the correlation.

offset_seconds follows schemas.VideoAngle's convention:
    primary_time + offset_seconds == this angle's own file time
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from scipy.signal import correlate

SAMPLE_RATE = 4000  # plenty for speech-timing alignment; keeps arrays small
WINDOW_SECONDS = 300  # only the first 5 minutes of each angle is needed to sync
MAX_OFFSET_SECONDS = 180  # angles are assumed to start within 3 minutes of each other
# A hung/stuck ffmpeg process (a malformed file, an exotic codec) would
# otherwise block the sync job - and its own session's folder from being
# deleted, since Windows won't unlink a file another process still has
# open - forever. Extracting 5 minutes of audio should never genuinely take
# this long even from a large source file; if it does, something's wrong.
FFMPEG_TIMEOUT_SECONDS = 300


class SyncFailed(Exception):
    pass


def _extract_mono_audio(video_path: Path, duration: float) -> np.ndarray:
    """Decode the first `duration` seconds of a video's audio to mono
    float32 PCM at SAMPLE_RATE via ffmpeg."""
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-t", str(duration),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "f32le", "-",
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=FFMPEG_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        raise SyncFailed(
            f"ffmpeg audio extraction timed out after {FFMPEG_TIMEOUT_SECONDS}s for "
            f"{video_path.name} - the file may be corrupt or in an unusual format"
        )
    if result.returncode != 0:
        stderr = result.stderr[-2000:].decode(errors="replace")
        raise SyncFailed(f"ffmpeg audio extraction failed for {video_path.name}: {stderr}")
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    if audio.size == 0:
        raise SyncFailed(f"{video_path.name} has no decodable audio track")
    return audio


def probe_duration_seconds(video_path: Path) -> float:
    """Duration in seconds via ffprobe, used to clamp an angle whose
    recording stopped before the others rather than fabricating footage
    that isn't there."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=FFMPEG_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        raise SyncFailed(f"ffprobe timed out after {FFMPEG_TIMEOUT_SECONDS}s for {video_path.name}")
    if result.returncode != 0:
        stderr = result.stderr[-2000:].decode(errors="replace")
        raise SyncFailed(f"ffprobe failed for {video_path.name}: {stderr}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def estimate_offset_seconds(primary_path: Path, other_path: Path) -> float:
    """Return offset such that primary_time + offset == other angle's own
    time, for the same real moment in the recording."""
    a = _extract_mono_audio(primary_path, WINDOW_SECONDS)
    b = _extract_mono_audio(other_path, WINDOW_SECONDS)
    return _offset_from_signals(a, b, SAMPLE_RATE)


def _offset_from_signals(a: np.ndarray, b: np.ndarray, sample_rate: int) -> float:
    # Normalize so differing mic gain/volume between angles doesn't bias
    # the correlation toward whichever signal happens to be louder.
    a = (a - a.mean()) / (a.std() + 1e-8)
    b = (b - b.mean()) / (b.std() + 1e-8)

    corr = correlate(a, b, mode="full", method="fft")
    lags = np.arange(-(len(b) - 1), len(a))

    max_lag = int(MAX_OFFSET_SECONDS * sample_rate)
    in_range = np.abs(lags) <= max_lag
    if not in_range.any():
        raise SyncFailed("audio too short to search the expected offset range")

    corr_r = corr[in_range]
    lags_r = lags[in_range]
    best_idx = int(np.argmax(corr_r))
    best_lag = int(lags_r[best_idx])

    # Correlation convention validated against synthetic known-shift signals:
    # offset_seconds = -best_lag / sample_rate (see tests/test_sync.py).
    return -best_lag / sample_rate
