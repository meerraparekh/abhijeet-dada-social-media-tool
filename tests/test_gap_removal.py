"""Tests for automatic silence/gap removal.

Two layers: pure-Python unit tests for the segment-selection and
timestamp-remapping logic (fast, no ffmpeg needed), and an end-to-end
render test against the synthetic test video that confirms a clip with an
injected long gap actually comes out shorter.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from schemas import Clip, TranscriptWord  # noqa: E402
from services.clipper import (  # noqa: E402
    build_time_remap,
    compute_keep_segments,
    render_clip_for_platform,
)

TEST_VIDEO = os.environ.get("SATSANG_TEST_VIDEO", "/tmp/test_session.mp4")


def _words(*starts_ends):
    return [TranscriptWord(start=s, end=e, word=f" w{i}") for i, (s, e) in enumerate(starts_ends)]


def test_compute_keep_segments_no_gaps_stays_single_segment():
    words = _words((0.0, 1.0), (1.1, 2.0), (2.05, 3.0))
    segments = compute_keep_segments(words, 0.0, 3.0, max_gap=0.6)
    assert segments == [(0.0, 3.0)]


def test_compute_keep_segments_splits_on_long_gap():
    # A 5-second gap in the middle should get cut out, leaving two segments.
    words = _words((0.0, 1.0), (1.2, 2.0), (7.0, 8.0), (8.1, 9.0))
    segments = compute_keep_segments(words, 0.0, 9.0, max_gap=0.6, pad=0.1)
    assert len(segments) == 2
    first, second = segments
    # First segment covers the opening words plus padding, well short of the gap.
    assert first[0] == pytest.approx(0.0)
    assert first[1] < 3.0
    # Second segment starts near the third word, not at 0, and reaches the end.
    assert second[0] > 6.0
    assert second[1] == pytest.approx(9.0)


def test_compute_keep_segments_falls_back_with_no_words():
    segments = compute_keep_segments([], 10.0, 20.0)
    assert segments == [(10.0, 20.0)]


def test_build_time_remap_shrinks_total_duration():
    keep_segments = [(0.0, 2.0), (7.0, 9.0)]  # 4s kept out of a 9s span
    remap, total = build_time_remap(keep_segments)
    assert total == pytest.approx(4.0)
    assert remap(0.0) == pytest.approx(0.0)
    assert remap(1.0) == pytest.approx(1.0)
    # A timestamp inside the removed gap collapses to the boundary.
    assert remap(5.0) == pytest.approx(2.0)
    # A timestamp in the second kept segment continues right after the first.
    assert remap(7.5) == pytest.approx(2.5)
    assert remap(9.0) == pytest.approx(4.0)


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ],
        stdout=subprocess.PIPE, text=True, check=True,
    )
    return float(result.stdout.strip())


@pytest.mark.skipif(not Path(TEST_VIDEO).exists(), reason=f"no test video at {TEST_VIDEO}")
def test_gap_removal_shortens_the_rendered_clip(tmp_path):
    # Words spanning 0-3s and 8-11s of the clip - an injected 5s silent gap
    # in the middle that should get cut out.
    words = _words((0.0, 0.5), (0.6, 1.0), (1.2, 1.8), (2.0, 3.0), (8.0, 8.6), (8.8, 9.5), (10.0, 11.0))
    clip = Clip(start_seconds=0.0, end_seconds=11.0, title="t")

    out_dir = tmp_path / "clips"
    out_path = render_clip_for_platform(
        Path(TEST_VIDEO), clip, "youtube", words, out_dir, remove_silence=True
    )
    duration_with_removal = _probe_duration(out_path)

    out_dir2 = tmp_path / "clips_no_removal"
    out_path2 = render_clip_for_platform(
        Path(TEST_VIDEO), clip, "youtube", words, out_dir2, remove_silence=False
    )
    duration_without_removal = _probe_duration(out_path2)

    # The un-cut render should be close to the full 11s span; the gap-removed
    # one should be meaningfully shorter (most of the ~5s gap cut out).
    assert duration_without_removal == pytest.approx(11.0, abs=0.5)
    assert duration_with_removal < duration_without_removal - 3.0
