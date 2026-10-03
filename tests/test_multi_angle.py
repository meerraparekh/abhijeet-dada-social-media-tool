"""End-to-end render test for multi-angle clipping: a clip's timestamps are
always on the primary angle's timeline, and render_clip_for_angle must
translate them correctly into a second angle's own (offset) file, including
clamping/rejecting a clip that runs past an angle that stopped recording
early. Complements test_sync.py (which tests offset *estimation* in
isolation) by testing offset *application* against real ffmpeg output.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from schemas import Clip, TranscriptWord  # noqa: E402
from services.clipper import ClipNotAvailable, render_clip_for_angle  # noqa: E402

TEST_VIDEO = os.environ.get("SATSANG_TEST_VIDEO", "/tmp/test_session.mp4")


def _words(*starts_ends):
    return [TranscriptWord(start=s, end=e, word=f" w{i}") for i, (s, e) in enumerate(starts_ends)]


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ],
        stdout=subprocess.PIPE, text=True, check=True,
    )
    return float(result.stdout.strip())


def _make_delayed_angle(tmp_path: Path, delay_seconds: float) -> Path:
    """A copy of TEST_VIDEO whose audio (and therefore its own meaningful
    "start") is pushed `delay_seconds` later - i.e. this angle started
    recording `delay_seconds` after the primary, matching
    VideoAngle.offset_seconds' convention (primary_time + offset == this
    angle's own time)."""
    out_path = tmp_path / f"angle_delay_{delay_seconds}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(TEST_VIDEO),
            "-af", f"adelay={int(delay_seconds * 1000)}|{int(delay_seconds * 1000)}",
            "-c:v", "copy", "-c:a", "aac",
            str(out_path),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    )
    return out_path


@pytest.mark.skipif(not Path(TEST_VIDEO).exists(), reason=f"no test video at {TEST_VIDEO}")
def test_render_clip_for_angle_applies_offset(tmp_path):
    words = _words((0.0, 1.0), (1.2, 2.0), (2.1, 3.0), (3.2, 4.0), (4.1, 5.0))
    clip = Clip(start_seconds=1.0, end_seconds=4.0, title="t")  # 3s clip

    delayed = _make_delayed_angle(tmp_path, delay_seconds=3.0)
    angle_duration = _probe_duration(delayed)

    out_path = render_clip_for_angle(
        delayed, clip, "angle2", words, tmp_path / "out",
        offset_seconds=3.0, angle_duration=angle_duration, remove_silence=False,
    )
    assert out_path.exists()
    # the clip itself is 3s of (continuous, no-gap) footage regardless of
    # which angle it was cut from - the offset only changes *where* in the
    # file it's cut from, not the resulting clip's own length.
    assert _probe_duration(out_path) == pytest.approx(3.0, abs=0.5)


@pytest.mark.skipif(not Path(TEST_VIDEO).exists(), reason=f"no test video at {TEST_VIDEO}")
def test_render_clip_for_angle_raises_when_angle_stopped_too_early(tmp_path):
    words = _words((0.0, 1.0), (1.2, 2.0), (2.1, 3.0))
    # Clip starts at primary-time 20s; this angle (offset 0) only has 5
    # seconds of footage - the clip is entirely beyond what it recorded.
    clip = Clip(start_seconds=20.0, end_seconds=23.0, title="t")

    with pytest.raises(ClipNotAvailable):
        render_clip_for_angle(
            Path(TEST_VIDEO), clip, "short_angle", words, tmp_path / "out",
            offset_seconds=0.0, angle_duration=5.0, remove_silence=False,
        )


@pytest.mark.skipif(not Path(TEST_VIDEO).exists(), reason=f"no test video at {TEST_VIDEO}")
def test_render_clip_for_angle_clamps_partial_overlap(tmp_path):
    # Clip spans primary-time 8s-14s; this angle (offset 0) only has
    # footage up to 11s - the render should clamp to what's actually there
    # (roughly 3s) rather than raising, since part of the clip IS available.
    words = _words((8.0, 9.0), (9.2, 10.0), (10.2, 11.0))
    clip = Clip(start_seconds=8.0, end_seconds=14.0, title="t")

    out_path = render_clip_for_angle(
        Path(TEST_VIDEO), clip, "short_angle", words, tmp_path / "out",
        offset_seconds=0.0, angle_duration=11.0, remove_silence=False,
    )
    assert out_path.exists()
    assert _probe_duration(out_path) == pytest.approx(3.0, abs=0.5)
