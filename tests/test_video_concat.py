"""Tests for joining a split recording's parts into one continuous file -
both the common lossless stream-copy path and the re-encoding fallback for
parts with mismatched codec/resolution (e.g. the camera's settings changed
between a stop and restart)."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.video_concat import ConcatFailed, concat_videos  # noqa: E402


def _make_clip(path: Path, duration: float, color: str, resolution: str = "320x240", vcodec: str = "libx264", acodec: str = "aac") -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-f", "lavfi", "-i", f"color=c={color}:s={resolution}:d={duration}",
            "-shortest", "-c:v", vcodec, "-c:a", acodec,
            str(path),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    )


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, text=True, check=True,
    )
    return float(result.stdout.strip())


def test_single_part_is_just_moved_into_place(tmp_path):
    src = tmp_path / "src.mp4"
    _make_clip(src, 2, "blue")
    out = tmp_path / "out.mp4"

    concat_videos([src], out)

    assert out.exists()
    assert not src.exists()


def test_matching_parts_join_via_fast_stream_copy(tmp_path):
    part1 = tmp_path / "part1.mp4"
    part2 = tmp_path / "part2.mp4"
    _make_clip(part1, 3, "blue")
    _make_clip(part2, 3, "red")
    out = tmp_path / "out.mp4"

    concat_videos([part1, part2], out)

    assert out.exists()
    assert _probe_duration(out) == pytest.approx(6.0, abs=0.5)
    # successful join deletes the source parts
    assert not part1.exists()
    assert not part2.exists()
    # no leftover temp/list files
    assert list(tmp_path.glob("out.tmp*")) == []
    assert list(tmp_path.glob("out_concat_list.txt")) == []


def test_mismatched_parts_fall_back_to_reencode(tmp_path):
    part1 = tmp_path / "part1.mp4"
    part2 = tmp_path / "part2.mp4"
    _make_clip(part1, 2, "blue", resolution="320x240", vcodec="libx264", acodec="aac")
    _make_clip(part2, 2, "green", resolution="640x480", vcodec="mpeg4", acodec="mp3")
    out = tmp_path / "out.mp4"

    concat_videos([part1, part2], out)

    assert out.exists()
    assert _probe_duration(out) == pytest.approx(4.0, abs=0.5)
    assert not part1.exists()
    assert not part2.exists()


def test_failed_concat_preserves_the_original_parts(tmp_path):
    good = tmp_path / "good.mp4"
    broken = tmp_path / "broken.mp4"
    _make_clip(good, 2, "blue")
    broken.write_bytes(b"not a real video file")
    out = tmp_path / "should_not_exist.mp4"

    with pytest.raises(ConcatFailed):
        concat_videos([good, broken], out)

    # nothing lost on failure - the caller can retry or recover
    assert good.exists()
    assert broken.exists()
    assert not out.exists()
    assert list(tmp_path.glob("should_not_exist*")) == []


def test_appending_to_an_existing_file_reuses_its_path(tmp_path):
    """Models the "append more footage to an angle later" flow: the first
    part IS the destination path itself, with new footage appended after
    it - matches how main.py calls concat_videos for an existing angle."""
    existing = tmp_path / "angle.mp4"
    _make_clip(existing, 2, "blue")
    new_part = tmp_path / "angle_append_part0.mp4"
    _make_clip(new_part, 2, "red")

    concat_videos([existing, new_part], existing)

    assert existing.exists()
    assert _probe_duration(existing) == pytest.approx(4.0, abs=0.5)
    assert not new_part.exists()
