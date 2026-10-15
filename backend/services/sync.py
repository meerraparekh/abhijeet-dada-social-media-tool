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
from typing import Tuple

import numpy as np
from scipy.signal import butter, correlate, sosfiltfilt

SAMPLE_RATE = 4000  # plenty for speech-timing alignment; keeps arrays small
WINDOW_SECONDS = 300  # only the first 5 minutes of each angle is needed to sync
MAX_OFFSET_SECONDS = 180  # angles are assumed to start within 3 minutes of each other
ENVELOPE_FRAME_SECONDS = 0.02  # 20ms RMS frames for the correlation signal
# Minimum confidence - how many standard deviations the best correlation
# peak clears what pure chance alone would be expected to produce given how
# many candidate lags were searched (see the extreme-value correction in
# _offset_from_signals) - below this, the correlation surface doesn't have
# a single clearly-dominant peak, a real sign the estimated offset may not
# reflect true alignment (ambiguous/noisy audio) rather than a precise
# cutoff validated against real recordings. Flagged as "low_confidence"
# rather than silently reported as "synced".
LOW_CONFIDENCE_THRESHOLD = 2.0
# A hung/stuck ffmpeg process (a malformed file, an exotic codec) would
# otherwise block the sync job - and its own session's folder from being
# deleted, since Windows won't unlink a file another process still has
# open - forever. Extracting 5 minutes of audio should never genuinely take
# this long even from a large source file; if it does, something's wrong.
FFMPEG_TIMEOUT_SECONDS = 300


class SyncFailed(Exception):
    pass


def _extract_mono_audio(video_path: Path, duration: float, start_seconds: float = 0.0) -> np.ndarray:
    """Decode `duration` seconds of a video's audio, starting at
    `start_seconds`, to mono float32 PCM at SAMPLE_RATE via ffmpeg. -ss
    comes after -i (accurate seek, not fast seek) since a narrow sync
    search depends on the window actually starting where it says it does -
    a fast-seek's rounding to the nearest keyframe would quietly bias the
    result by however far off that keyframe is."""
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-ss", str(start_seconds), "-t", str(duration),
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


def estimate_offset_seconds(primary_path: Path, other_path: Path) -> Tuple[float, float]:
    """Return (offset, confidence) where primary_time + offset == other
    angle's own time, for the same real moment in the recording. confidence
    is a peak-to-sidelobe ratio - how much the best-matching offset stands
    out from the surrounding correlation floor - for the caller to decide
    whether to trust it (see LOW_CONFIDENCE_THRESHOLD).

    This only works if the two WINDOW_SECONDS windows compared (the start
    of each file) actually cover the same real moment - true only if both
    cameras started recording within MAX_OFFSET_SECONDS of each other. A
    real session confirmed this assumption can be badly wrong: cameras set
    up well apart in time mean the first few minutes of each file are
    genuinely different parts of the talk, which no correlation method can
    "find" a match for since there isn't one in the window searched. See
    estimate_offset_near for that case."""
    a = _extract_mono_audio(primary_path, WINDOW_SECONDS)
    b = _extract_mono_audio(other_path, WINDOW_SECONDS)
    return _offset_from_signals(a, b, SAMPLE_RATE)


def estimate_offset_near(
    primary_path: Path,
    other_path: Path,
    approx_offset_seconds: float,
    search_radius_seconds: float = 45.0,
    window_seconds: float = 90.0,
) -> Tuple[float, float]:
    """Refine a rough, user-eyeballed offset guess into a precise one via a
    NARROW cross-correlation search around it, instead of estimate_offset_
    seconds' blind search from the start of each file. Two advantages once
    a rough anchor is known to be roughly right: (1) it works even when the
    cameras started recording far enough apart that the blind file-start
    search never had overlapping real content to find a match in at all,
    and (2) a narrower search space makes the confidence score far more
    discriminating (see _offset_from_signals' confidence correction, which
    scales with how many candidate lags are searched) - a real but modest
    peak that's swamped by chance over a wide blind search can stand out
    clearly once the search is this narrow."""
    primary_audio = _extract_mono_audio(primary_path, window_seconds, start_seconds=0.0)
    other_start = max(0.0, approx_offset_seconds - search_radius_seconds)
    other_audio = _extract_mono_audio(
        other_path, window_seconds + 2 * search_radius_seconds, start_seconds=other_start
    )
    offset_in_window, confidence = _offset_from_signals(
        primary_audio, other_audio, SAMPLE_RATE,
        max_offset_seconds=window_seconds + 2 * search_radius_seconds,
    )
    # other_audio's own array-local time 0 corresponds to real other-file
    # time `other_start` - translate the in-window offset back to a real
    # primary_time + offset == other_file_time offset.
    return other_start + offset_in_window, confidence


def _envelope(audio: np.ndarray, sample_rate: int) -> Tuple[np.ndarray, float]:
    """Band-limit to the frequency range carrying the most camera-
    independent speech timing information, then reduce to a short-frame RMS
    energy envelope. Real multi-camera audio - different mic frequency
    response, distance-dependent room reverb, PA-system coloration -
    frequently doesn't line up sample-for-sample on the raw waveform even
    at the true offset, which can make raw-waveform cross-correlation lock
    onto a confident-looking but wrong peak. The envelope instead tracks
    the rhythm of speech (onsets, pauses), which survives those differences
    far better. Returns (envelope, effective_sample_rate) - the envelope's
    own time resolution is coarser than the input audio's."""
    nyquist = sample_rate / 2
    low = min(300.0, nyquist * 0.1)
    high = min(1900.0, nyquist * 0.9)
    if high > low:
        sos = butter(4, [low, high], btype="band", fs=sample_rate, output="sos")
        audio = sosfiltfilt(sos, audio)

    frame_len = max(1, int(sample_rate * ENVELOPE_FRAME_SECONDS))
    n_frames = len(audio) // frame_len
    if n_frames < 2:
        # Too short to frame meaningfully - fall back to the raw signal
        # rather than returning something degenerate.
        return audio, float(sample_rate)
    trimmed = audio[: n_frames * frame_len]
    frames = trimmed.reshape(n_frames, frame_len)
    envelope = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    return envelope, sample_rate / frame_len


def _offset_from_signals(
    a: np.ndarray, b: np.ndarray, sample_rate: int, max_offset_seconds: float = MAX_OFFSET_SECONDS
) -> Tuple[float, float]:
    a_env, eff_rate = _envelope(a, sample_rate)
    b_env, _ = _envelope(b, sample_rate)

    # Normalize so differing mic gain/volume between angles doesn't bias
    # the correlation toward whichever signal happens to be louder.
    a_env = (a_env - a_env.mean()) / (a_env.std() + 1e-8)
    b_env = (b_env - b_env.mean()) / (b_env.std() + 1e-8)

    corr = correlate(a_env, b_env, mode="full", method="fft")
    lags = np.arange(-(len(b_env) - 1), len(a_env))

    max_lag = int(max_offset_seconds * eff_rate)
    in_range = np.abs(lags) <= max_lag
    if not in_range.any():
        raise SyncFailed("audio too short to search the expected offset range")

    corr_r = corr[in_range]
    lags_r = lags[in_range]
    best_idx = int(np.argmax(corr_r))
    best_lag = int(lags_r[best_idx])

    # Confidence: how far the best peak stands above the correlation floor
    # elsewhere in the search range (a sharp, well-isolated peak means the
    # audio had a genuinely distinctive, unambiguous alignment point; a flat
    # or noisy correlation surface means the "best" peak may just be noise).
    # The floor is measured excluding a window around the peak itself so the
    # peak doesn't inflate its own baseline.
    exclude_frames = int(2.0 * eff_rate)
    floor_mask = np.ones(len(corr_r), dtype=bool)
    floor_mask[max(0, best_idx - exclude_frames):min(len(corr_r), best_idx + exclude_frames)] = False
    floor_std = float(np.std(corr_r[floor_mask])) if floor_mask.any() else float(np.std(corr_r))
    raw_z = float(corr_r[best_idx] / (floor_std + 1e-8))

    # The more candidate lags searched, the higher a peak purely BY CHANCE
    # is expected to be - the max of N roughly-independent noise values
    # grows with N (extreme-value statistics), so a wider search window
    # makes a noise peak look more "confident" even with zero real signal.
    # Subtracting that chance expectation keeps the threshold meaningful
    # regardless of WINDOW_SECONDS/MAX_OFFSET_SECONDS - confirmed against
    # pure independent noise in tests/test_sync.py.
    expected_chance_z = float(np.sqrt(2 * np.log(max(len(corr_r), 2))))
    confidence = raw_z - expected_chance_z

    # Correlation convention validated against synthetic known-shift signals:
    # offset_seconds = -best_lag / effective_sample_rate (see tests/test_sync.py).
    return -best_lag / eff_rate, confidence
