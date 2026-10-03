"""Locks down the cross-correlation sign convention used for multi-angle
audio sync - a flipped sign here would silently cut the wrong footage for
every non-primary angle, so this is tested directly against synthetic
signals with a known, constructed offset rather than trusted from the
math alone."""
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.sync import _offset_from_signals  # noqa: E402

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


@pytest.mark.parametrize("shift_samples", [-1800, 0, 2500, 3000])
def test_offset_matches_known_shift(shift_samples):
    primary, other = _make_pair(20000, shift_samples)
    offset = _offset_from_signals(primary, other, SAMPLE_RATE)
    assert offset == pytest.approx(shift_samples / SAMPLE_RATE, abs=1e-6)


def test_offset_with_shorter_other_angle():
    """Models an angle that stopped recording early: its audio array is
    shorter than the primary's, but the offset should still resolve
    correctly from the overlapping portion."""
    primary, other = _make_pair(20000, 3000)
    other_short = other[:12000]
    offset = _offset_from_signals(primary, other_short, SAMPLE_RATE)
    assert offset == pytest.approx(3000 / SAMPLE_RATE, abs=1e-6)
