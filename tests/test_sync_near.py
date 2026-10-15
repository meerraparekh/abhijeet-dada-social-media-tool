"""Integration test for estimate_offset_near - the narrow-search refinement
added after a real session confirmed estimate_offset_seconds' blind
file-start search can fail outright: if the cameras started recording far
enough apart in time, the first few minutes of each file are genuinely
different parts of the talk, so there's no match to find there at all.
estimate_offset_near instead searches narrowly around a rough, user-
eyeballed guess - this exercises the real ffmpeg-based extraction (the new
start_seconds support in particular) and the start-time translate-back
math end to end, not just the pure-numpy correlation already covered by
test_sync.py."""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services import sync  # noqa: E402

AUDIO_SAMPLE_RATE = 8000


def _bursty_carrier(n_samples: int, seed: int) -> np.ndarray:
    """Irregular on/off amplitude pattern - see tests/test_sync.py for why
    irregular (not periodic) timing matters for a clean correlation peak."""
    rng = np.random.default_rng(seed)
    carrier = rng.standard_normal(n_samples)
    pattern = np.zeros(n_samples)
    t = 0
    while t < n_samples:
        on = rng.integers(400, 1600)
        off = rng.integers(600, 2000)
        pattern[t : t + on] = 1.0
        t += on + off
    return carrier * pattern


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    pcm = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def _mux_into_mp4(wav_path: Path, mp4_path: Path, duration: float, color: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(wav_path),
            "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d={duration}",
            "-shortest", "-c:v", "libx264", "-c:a", "aac",
            str(mp4_path),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    )


def test_estimate_offset_near_recovers_offset_outside_the_blind_search_window(tmp_path):
    """Models the exact real-world failure: the camera for this angle
    started recording well before the content that matters, far outside
    estimate_offset_seconds' blind MAX_OFFSET_SECONDS search - but a rough
    guess (e.g. the user scrubbing both angles to the same moment by eye)
    lets the narrow search still find the precise answer."""
    true_offset = 260.0  # outside sync.MAX_OFFSET_SECONDS (180s)
    shared_pattern = _bursty_carrier(int(90 * AUDIO_SAMPLE_RATE), seed=5)
    # "other" angle's file: true_offset seconds of unrelated filler audio,
    # then the same real content as primary - i.e. the real content starts
    # true_offset seconds into this file, matching the schema's convention
    # (primary_time + offset == this angle's own file time).
    filler = _bursty_carrier(int(true_offset * AUDIO_SAMPLE_RATE), seed=99)
    other_full = np.concatenate([filler, shared_pattern])

    primary_wav = tmp_path / "primary.wav"
    other_wav = tmp_path / "other.wav"
    _write_wav(primary_wav, shared_pattern, AUDIO_SAMPLE_RATE)
    _write_wav(other_wav, other_full, AUDIO_SAMPLE_RATE)

    primary_mp4 = tmp_path / "primary.mp4"
    other_mp4 = tmp_path / "other.mp4"
    _mux_into_mp4(primary_wav, primary_mp4, duration=90, color="blue")
    _mux_into_mp4(other_wav, other_mp4, duration=true_offset + 90, color="green")

    # A rough, inexact guess - as if the user just eyeballed it - should
    # still resolve to a precise answer.
    rough_guess = true_offset + 8.0
    offset, confidence = sync.estimate_offset_near(primary_mp4, other_mp4, rough_guess)

    assert offset == pytest.approx(true_offset, abs=0.5)
    assert confidence >= sync.LOW_CONFIDENCE_THRESHOLD
