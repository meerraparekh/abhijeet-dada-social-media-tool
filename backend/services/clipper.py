"""Cut, reframe, and caption-burn clips out of the raw session video using ffmpeg.

Everything here shells out to the system `ffmpeg` binary - free, cross-platform,
and already the standard tool for this. No paid transcoding service involved.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from schemas import Clip, TranscriptWord

# platform -> (output width, height, burn captions by default)
PRESETS = {
    "youtube": {"width": 1920, "height": 1080, "vertical": False, "burn_captions": False},
    "instagram_reel": {"width": 1080, "height": 1920, "vertical": True, "burn_captions": True},
    "twitter": {"width": 1280, "height": 720, "vertical": False, "burn_captions": True},
}

# Silence/gap removal defaults. A gap between spoken words shorter than
# MAX_GAP_SECONDS is left alone (natural pauses in speech); anything longer
# gets cut out, with a small padding kept on each side so words don't sound
# clipped.
DEFAULT_MAX_GAP_SECONDS = 0.6
DEFAULT_GAP_PADDING_SECONDS = 0.12


def _run(cmd: List[str], cwd: Path | None = None) -> None:
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(cwd) if cwd else None
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({' '.join(cmd)}):\n{result.stdout[-4000:]}")


def find_silence_gaps(
    words: List[TranscriptWord],
    start: float,
    end: float,
    max_gap: float = DEFAULT_MAX_GAP_SECONDS,
) -> List[Tuple[float, float]]:
    """Return the (start, end) gaps between words inside [start, end) that
    are longer than max_gap - the raw video's original timeline."""
    in_range = sorted(
        (w for w in words if w.end > start and w.start < end), key=lambda w: w.start
    )
    if not in_range:
        return []  # no word timing to work with - leave the range untouched
    gaps: List[Tuple[float, float]] = []
    prev_end = start
    for w in in_range:
        w_start = max(w.start, start)
        if w_start - prev_end > max_gap:
            gaps.append((prev_end, w_start))
        prev_end = max(prev_end, min(w.end, end))
    if end - prev_end > max_gap:
        gaps.append((prev_end, end))
    return gaps


def merge_and_pad_cut_ranges(
    start: float,
    end: float,
    cut_ranges: List[Tuple[float, float]],
    pad: float = DEFAULT_GAP_PADDING_SECONDS,
) -> List[Tuple[float, float]]:
    """Given arbitrary (possibly overlapping) ranges to cut out of
    [start, end) - silence gaps, AI-flagged filler words, or any future
    source - merge overlapping/adjacent cuts, shrink each by `pad` on both
    sides so words right at a cut boundary aren't clipped (a cut smaller
    than 2*pad is skipped entirely, since it's not worth the risk), and
    return the resulting kept stretches. Falls back to the whole
    [start, end) range untouched if there's nothing to cut."""
    clipped = sorted(
        (max(start, c[0]), min(end, c[1])) for c in cut_ranges if c[1] > start and c[0] < end and c[1] > c[0]
    )
    merged: List[List[float]] = []
    for cs, ce in clipped:
        if merged and cs <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], ce)
        else:
            merged.append([cs, ce])

    segments: List[Tuple[float, float]] = []
    cursor = start
    for cs, ce in merged:
        eff_start = cs + pad
        eff_end = ce - pad
        if eff_end <= eff_start:
            continue  # cut too small relative to padding to bother with
        if eff_start > cursor:
            segments.append((cursor, eff_start))
        cursor = max(cursor, eff_end)
    if end > cursor:
        segments.append((cursor, end))
    return segments or [(start, end)]


def compute_keep_segments(
    words: List[TranscriptWord],
    start: float,
    end: float,
    max_gap: float = DEFAULT_MAX_GAP_SECONDS,
    pad: float = DEFAULT_GAP_PADDING_SECONDS,
) -> List[Tuple[float, float]]:
    """Silence-only convenience wrapper around find_silence_gaps +
    merge_and_pad_cut_ranges, kept for callers that only care about gaps
    (see render_clip_for_platform for the general multi-source case)."""
    gaps = find_silence_gaps(words, start, end, max_gap=max_gap)
    return merge_and_pad_cut_ranges(start, end, gaps, pad=pad)


def build_time_remap(
    keep_segments: List[Tuple[float, float]]
) -> Tuple[Callable[[float], float], float]:
    """Build a function mapping a timestamp in the raw video's original
    timeline to its position in the gap-removed output timeline, plus the
    resulting total output duration. Used to keep burned-in captions in sync
    after cutting out silence."""
    breakpoints = []
    cursor = 0.0
    for seg_start, seg_end in keep_segments:
        breakpoints.append((seg_start, seg_end, cursor))
        cursor += seg_end - seg_start
    total_duration = cursor

    def remap(t: float) -> float:
        for seg_start, seg_end, new_start in breakpoints:
            if seg_start <= t <= seg_end:
                return new_start + (t - seg_start)
            if t < seg_start:
                return new_start
        return total_duration

    return remap, total_duration


def _segment_index(t: float, keep_segments: List[Tuple[float, float]]) -> int:
    """Which keep_segment a raw-timeline timestamp falls into, or -1."""
    for i, (s, e) in enumerate(keep_segments):
        if s <= t <= e:
            return i
    return -1


def _words_to_srt(
    words: List[TranscriptWord],
    start: float,
    end: float,
    words_per_line: int = 4,
    remap: Optional[Callable[[float], float]] = None,
    keep_segments: Optional[List[Tuple[float, float]]] = None,
) -> str:
    """Pack word-level timestamps inside [start, end) into short caption lines.
    Without `remap`, times are simply re-based so 0.0 = clip start. With
    `remap` (used when footage has been cut, whether for silence or flagged
    filler/mistakes), times are translated into the shorter output timeline
    instead - and `keep_segments` must be passed too, both to drop any word
    that's actually been cut out of the video (a filler-word cut targets a
    real word, unlike a silence gap, so without this a removed "um" would
    still show up as caption text for footage that no longer exists) and so
    a caption line never straddles a cut point."""
    in_range = [w for w in words if w.start >= start and w.end <= end]
    if keep_segments is not None:
        # Check the word's midpoint, not its start - a cut's padding keeps a
        # thin sliver of audio right at the edges of a removed word so the
        # cut doesn't sound abrupt, which can leave a cut word's start just
        # inside a kept segment even though most of the word is gone.
        in_range = [
            w for w in in_range if _segment_index((w.start + w.end) / 2, keep_segments) != -1
        ]
    lines = []
    idx = 1
    chunk: List[TranscriptWord] = []

    def flush():
        nonlocal idx
        if not chunk:
            return
        raw_start = chunk[0].start
        raw_end = chunk[-1].end
        if remap:
            chunk_start = remap(raw_start)
            chunk_end = remap(raw_end)
        else:
            chunk_start = raw_start - start
            chunk_end = raw_end - start
        text = " ".join(w.word.strip() for w in chunk)
        lines.append(str(idx))
        lines.append(f"{_srt_ts(chunk_start)} --> {_srt_ts(chunk_end)}")
        lines.append(text)
        lines.append("")
        idx += 1

    for w in in_range:
        crosses_cut = keep_segments is not None and _segment_index(
            w.start, keep_segments
        ) != _segment_index(chunk[-1].end if chunk else w.start, keep_segments)
        if chunk and (len(chunk) >= words_per_line or crosses_cut):
            flush()
            chunk = []
        chunk.append(w)
    flush()

    return "\n".join(lines)


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _post_filters(preset: dict, srt_name: Optional[str]) -> List[str]:
    filters = []
    if preset["vertical"]:
        filters.append("crop=ih*9/16:ih")
    filters.append(f"scale={preset['width']}:{preset['height']}:force_original_aspect_ratio=decrease")
    filters.append(f"pad={preset['width']}:{preset['height']}:(ow-iw)/2:(oh-ih)/2:color=black")
    if srt_name:
        filters.append(
            f"subtitles={srt_name}:force_style='FontName=Arial,FontSize=20,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2'"
        )
    return filters


def render_clip_for_platform(
    raw_video_path: Path,
    clip: Clip,
    platform: str,
    words: List[TranscriptWord],
    out_dir: Path,
    remove_silence: bool = True,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
    caption_words: Optional[List[TranscriptWord]] = None,
    extra_cut_ranges: Optional[List[Tuple[float, float]]] = None,
) -> Path:
    """`words` (the original-language, real per-word timing from Whisper) is
    used to decide where the silence gaps are - that's the accurate source
    for what's actually silence. `caption_words` (defaults to `words` if not
    given) is what actually gets burned in as on-screen text - pass the
    English translation's interpolated word timing here to caption in
    English while still cutting gaps based on the real Hindi speech timing.
    `extra_cut_ranges` are additional (start, end) spans to cut regardless of
    remove_silence - e.g. AI-flagged filler words/mistakes - combined with
    the silence gaps into one set of cuts."""
    preset = PRESETS[platform]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{clip.id}_{platform}.mp4"
    caption_words = caption_words if caption_words is not None else words

    cut_ranges: List[Tuple[float, float]] = list(extra_cut_ranges or [])
    if remove_silence:
        cut_ranges += find_silence_gaps(words, clip.start_seconds, clip.end_seconds, max_gap=max_gap_seconds)
    keep_segments = (
        merge_and_pad_cut_ranges(clip.start_seconds, clip.end_seconds, cut_ranges)
        if cut_ranges
        else [(clip.start_seconds, clip.end_seconds)]
    )

    srt_path = None
    if preset["burn_captions"] and caption_words:
        srt_path = out_dir / f"{clip.id}_{platform}.srt"
        if len(keep_segments) > 1:
            remap, _ = build_time_remap(keep_segments)
            srt_text = _words_to_srt(
                caption_words, clip.start_seconds, clip.end_seconds, remap=remap, keep_segments=keep_segments
            )
        else:
            srt_text = _words_to_srt(caption_words, clip.start_seconds, clip.end_seconds)
        srt_path.write_text(srt_text, encoding="utf-8")

    # Reference the subtitle file by its bare filename and run ffmpeg with its
    # working directory set to out_dir, rather than embedding the absolute
    # path in the filter string. The subtitles filter's mini-language treats
    # ":" as a field separator, and an absolute Windows path's drive-letter
    # colon (C:\...) is a well-known source of "unable to parse" failures
    # there even when escaped - a bare filename has no colons or backslashes
    # to trip over.
    srt_name = srt_path.name if srt_path else None

    if len(keep_segments) == 1:
        # No gaps worth cutting - the simple single-segment path.
        seg_start, seg_end = keep_segments[0]
        vf = ",".join(_post_filters(preset, srt_name))
        cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_video_path),
            "-ss", str(seg_start),
            "-to", str(seg_end),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ]
    else:
        # Multiple kept stretches - trim and concatenate each one in a single
        # ffmpeg pass, then apply crop/scale/pad/captions to the result.
        parts = []
        concat_refs = []
        for i, (seg_start, seg_end) in enumerate(keep_segments):
            parts.append(f"[0:v]trim=start={seg_start}:end={seg_end},setpts=PTS-STARTPTS[v{i}]")
            parts.append(f"[0:a]atrim=start={seg_start}:end={seg_end},asetpts=PTS-STARTPTS[a{i}]")
            concat_refs.append(f"[v{i}][a{i}]")
        parts.append(f"{''.join(concat_refs)}concat=n={len(keep_segments)}:v=1:a=1[vcat][acat]")
        parts.append(f"[vcat]{','.join(_post_filters(preset, srt_name))}[vout]")
        filter_complex = ";".join(parts)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_video_path),
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[acat]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ]

    _run(cmd, cwd=out_dir)
    return out_path
