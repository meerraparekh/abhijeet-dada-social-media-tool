"""Locks down the cross-correlation sign convention used for multi-angle
audio sync - a flipped sign here would silently cut the wrong footage for
every non-primary angle, so this is tested directly against synthetic
signals with a known, constructed offset rather than trusted from the
math alone.

Also covers the envelope-based correlation (vs. raw waveform) introduced
after a real session's "synced" angles rendered completely unrelated
footage: different mic frequency response/room reverb between physically
separate cameras can distort the raw waveform enough to fool naive
cross-correlation, while the coarser amplitude-envelope "rhythm" of speech
survives much better."""
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.sync import LOW_CONFIDENCE_THRESHOLD, _offset_from_signals  # noqa: E402

SAMPLE_RATE = 1000


def _make_pair(n_samples: int, shift_samples: int, seed: int = 0):
    """Build (primary, other) where other[m] = primary[m - shift_samples]
    for valid m - i.e. the same real event at primary-sample (m - shift)
    appears at other-sample m, meaning other_file_time = primary_file_time
    + shift_samples/SAMPLE_RATE by construction."""
    rng = np.random.default_rng(seed)
    primary = rng.standard_normal(n_samples)
    other = np.zeros(n_samples)
    if shift_samples >= 0:
        other[shift_samples:] = primary[: n_samples - shift_samples]
    else:
        other[: n_samples + shift_samples] = primary[-shift_samples:]
    return primary, other


# Shifts chosen as exact multiples of the 20ms envelope frame (20 samples at
# this test's 1000Hz) so envelope quantization introduces no error here -
# precision below one frame isn't a design goal (clip boundaries have far
# more slack than that).
@pytest.mark.parametrize("shift_samples", [-1800, 0, 2500, 3000])
def test_offset_matches_known_shift(shift_samples):
    primary, other = _make_pair(20000, shift_samples)
    offset, confidence = _offset_from_signals(primary, other, SAMPLE_RATE)
    assert offset == pytest.approx(shift_samples / SAMPLE_RATE, abs=1e-6)
    # A clean, exact shifted copy should be an unambiguous match, not one
    # that would get flagged for manual review.
    assert confidence >= LOW_CONFIDENCE_THRESHOLD


def test_offset_with_shorter_other_angle():
    """Models an angle that stopped recording early: its audio array is
    shorter than the primary's, but the offset should still resolve
    correctly from the overlapping portion."""
    primary, other = _make_pair(20000, 3000)
    other_short = other[:12000]
    offset, confidence = _offset_from_signals(primary, other_short, SAMPLE_RATE)
    assert offset == pytest.approx(3000 / SAMPLE_RATE, abs=1e-6)
    assert confidence >= LOW_CONFIDENCE_THRESHOLD


def _bursty_carrier(n_samples: int, seed: int) -> np.ndarray:
    """White noise modulated by an on/off amplitude pattern with irregular
    (not periodic) burst/pause lengths - a crude stand-in for speech's
    bursts of energy (words) separated by pauses, which is the real timing
    information multi-camera sync actually needs to find. Irregular timing
    matters: an exactly-periodic pattern would correlate almost as well at
    every multiple of its period, diluting the one true peak - real speech
    doesn't repeat like that, so neither should this."""
    rng = np.random.default_rng(seed)
    carrier = rng.standard_normal(n_samples)
    pattern = np.zeros(n_samples)
    t = 0
    while t < n_samples:
        on = rng.integers(100, 400)
        off = rng.integers(150, 500)
        pattern[t : t + on] = 1.0
        t += on + off
    return carrier * pattern


def test_envelope_correlation_survives_differing_mic_coloration():
    """The real failure mode this is meant to fix: two cameras' mics don't
    hear the same waveform even at the true offset - different frequency
    response and room reverb per camera. Simulates that by independently
    distorting each channel (a different smoothing/coloring filter on one,
    a reverb-like delayed echo added to the other) after shifting - raw
    sample values no longer match at the true offset, but the underlying
    burst/pause rhythm (and therefore the RMS envelope) does."""
    n = 200000
    shift_samples = 2200
    # Build one bursty "true" signal, then derive both channels from it so
    # they share the real rhythm/timing but nothing else.
    true_signal = _bursty_carrier(n, seed=7)
    other_true = np.zeros(n)
    if shift_samples >= 0:
        other_true[shift_samples:] = true_signal[: n - shift_samples]

    # Simulate one camera's mic frequency response (a simple moving-average
    # lowpass smear) and the other's room reverb (a delayed, attenuated echo)
    # - independent, different distortions per channel, not a shared filter.
    smoothing_kernel = np.ones(3) / 3
    primary_distorted = np.convolve(true_signal, smoothing_kernel, mode="same")
    echo = np.zeros(n)
    echo[60:] = other_true[:-60] * 0.3
    other_distorted = other_true + echo

    offset, confidence = _offset_from_signals(primary_distorted, other_distorted, SAMPLE_RATE)
    assert offset == pytest.approx(shift_samples / SAMPLE_RATE, abs=0.05)
    assert confidence >= LOW_CONFIDENCE_THRESHOLD


def test_low_confidence_flagged_for_near_silent_audio():
    """Audio with essentially no distinctive structure (e.g. a long quiet
    stretch before a talk starts) shouldn't produce a confidently-wrong
    offset - the correlation floor is as noisy as the "peak", so confidence
    should come out low rather than clearing the threshold by chance."""
    rng = np.random.default_rng(11)
    tiny_noise_floor = 1e-6
    primary = rng.standard_normal(20000) * tiny_noise_floor
    other = rng.standard_normal(20000) * tiny_noise_floor
    offset, confidence = _offset_from_signals(primary, other, SAMPLE_RATE)
    assert confidence < LOW_CONFIDENCE_THRESHOLD
