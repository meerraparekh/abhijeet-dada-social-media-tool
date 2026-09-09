"""Cut, reframe, and caption-burn clips out of the raw session video using ffmpeg.

Everything here shells out to the system `ffmpeg` binary - free, cross-platform,
and already the standard tool for this. No paid transcoding service involved.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

from schemas import Clip, TranscriptWord

# platform -> (output width, height, burn captions by default)
PRESETS = {
    "youtube": {"width": 1920, "height": 1080, "vertical": False, "burn_captions": False},
    "instagram_reel": {"width": 1080, "height": 1920, "vertical": True, "burn_captions": True},
    "twitter": {"width": 1280, "height": 720, "vertical": False, "burn_captions": True},
}


def _run(cmd: List[str], cwd: Path | None = None) -> None:
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(cwd) if cwd else None
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({' '.join(cmd)}):\n{result.stdout[-4000:]}")


def _words_to_srt(words: List[TranscriptWord], start: float, end: float, words_per_line: int = 4) -> str:
    """Pack word-level timestamps inside [start, end) into short caption lines,
    re-based to the clip's own timeline (so 0.0 = clip start)."""
    in_range = [w for w in words if w.start >= start and w.end <= end]
    lines = []
    idx = 1
    for i in range(0, len(in_range), words_per_line):
        chunk = in_range[i : i + words_per_line]
        if not chunk:
            continue
        chunk_start = chunk[0].start - start
        chunk_end = chunk[-1].end - start
        text = " ".join(w.word.strip() for w in chunk)
        lines.append(str(idx))
        lines.append(f"{_srt_ts(chunk_start)} --> {_srt_ts(chunk_end)}")
        lines.append(text)
        lines.append("")
        idx += 1
    return "\n".join(lines)


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_clip_for_platform(
    raw_video_path: Path,
    clip: Clip,
    platform: str,
    words: List[TranscriptWord],
    out_dir: Path,
) -> Path:
    preset = PRESETS[platform]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{clip.id}_{platform}.mp4"

    filters = []
    if preset["vertical"]:
        # Center-crop to 9:16, then scale/pad to the target resolution.
        filters.append("crop=ih*9/16:ih")
    filters.append(f"scale={preset['width']}:{preset['height']}:force_original_aspect_ratio=decrease")
    filters.append(
        f"pad={preset['width']}:{preset['height']}:(ow-iw)/2:(oh-ih)/2:color=black"
    )

    srt_path = None
    if preset["burn_captions"] and words:
        srt_path = out_dir / f"{clip.id}_{platform}.srt"
        srt_path.write_text(
            _words_to_srt(words, clip.start_seconds, clip.end_seconds), encoding="utf-8"
        )
        # Reference the subtitle file by its bare filename and run ffmpeg with
        # its working directory set to out_dir, rather than embedding the
        # absolute path in the filter string. The subtitles filter's mini
        # -language treats ":" as a field separator, and an absolute Windows
        # path's drive-letter colon (C:\...) is a well-known source of
        # "unable to parse" failures there even when escaped - a bare
        # filename has no colons or backslashes to trip over.
        filters.append(
            f"subtitles={srt_path.name}:force_style='FontName=Arial,FontSize=20,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2'"
        )

    vf = ",".join(filters)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(raw_video_path),
        "-ss", str(clip.start_seconds),
        "-to", str(clip.end_seconds),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ]
    _run(cmd, cwd=out_dir)
    return out_path
