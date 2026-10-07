"""Join several video files into one continuous file - for a single camera
angle whose recording got split across multiple files (e.g. the camera
stopped and was restarted mid-session). Free and local, via ffmpeg.

Tries a lossless stream-copy concat first (fast - the common case, since
parts from the same camera/session usually share codec settings); falls
back to a re-encoding concat if that fails, which can join mismatched
inputs (e.g. the camera's settings changed between stop and restart) at
the cost of a slower re-encode.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List


class ConcatFailed(Exception):
    pass


# A hung ffmpeg process would otherwise block the job - and the file it has
# open from being deleted (Windows won't unlink a file another process
# still has a handle on) - forever. The stream-copy join only remuxes (no
# decoding), so it should always be fast regardless of file size; the
# re-encode fallback has to actually decode+encode the full combined
# duration, so its budget scales with how much content there is instead of
# a fixed cap - but still capped at an absolute ceiling: an uncapped
# duration-scaled budget meant a ~2.5 hour session's re-encode fallback
# could legitimately run for 7+ hours before ever timing out, which in
# practice is indistinguishable from "stuck forever" to someone waiting on
# it. If a re-encode genuinely needs longer than the ceiling, something
# other than "it's just slow" is almost certainly wrong anyway.
PROBE_TIMEOUT_SECONDS = 120
STREAM_COPY_TIMEOUT_SECONDS = 300
MIN_REENCODE_TIMEOUT_SECONDS = 600
MAX_REENCODE_TIMEOUT_SECONDS = 2700  # 45 minutes
REENCODE_TIMEOUT_MULTIPLE = 3  # budget = this many times the real-time duration


def _run(cmd: List[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, returncode=124, stdout=f"ffmpeg timed out after {timeout:.0f}s")


def _probe_duration(path: Path) -> float:
    """Best-effort duration probe - returns 0.0 (rather than raising) for a
    file ffprobe can't read, since this is only used to sanity-check a
    concat result against expectations, not as a hard dependency."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _duration_matches(actual: float, expected: float) -> bool:
    # A little tolerance for container/rounding overhead on a legitimate
    # join; anything bigger means content actually went missing.
    return abs(actual - expected) <= max(2.0, expected * 0.05)


def _reencode_timeout_seconds(expected_duration: float) -> float:
    return min(
        max(expected_duration * REENCODE_TIMEOUT_MULTIPLE, MIN_REENCODE_TIMEOUT_SECONDS),
        MAX_REENCODE_TIMEOUT_SECONDS,
    )


def concat_videos(parts: List[Path], out_path: Path) -> None:
    """Join `parts` (in the given order) into one continuous video file at
    `out_path`. A single part is just moved into place - no re-encoding.
    The original `parts` files are only deleted once the join has actually
    succeeded, so a failure never loses the uploaded footage; the caller is
    free to retry or recover them."""
    if not parts:
        raise ConcatFailed("no video parts given")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if len(parts) == 1:
        if parts[0] != out_path:
            parts[0].replace(out_path)
        return

    tmp_out = out_path.with_name(f"{out_path.stem}.tmp{out_path.suffix}")
    list_file = out_path.with_name(f"{out_path.stem}_concat_list.txt")
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in parts), encoding="utf-8"
    )

    # Each part must itself be a readable video before attempting to join
    # them - an unreadable part can't be recovered by any join strategy, and
    # failing fast here (rather than letting a downstream ffmpeg call decide)
    # avoids a real trap: the concat demuxer's stream-copy path can exit 0
    # and produce a valid-looking but silently truncated file when one part
    # is unreadable - it just drops that segment instead of failing - and if
    # "expected duration" were computed by the same best-effort probe used
    # below, an unreadable part would contribute 0 to it too, making the
    # truncated output look correct. Checking readability explicitly first
    # closes that gap and names the actual bad file instead of dumping an
    # ffmpeg log.
    durations = []
    for p in parts:
        d = _probe_duration(p)
        if d <= 0:
            list_file.unlink(missing_ok=True)
            raise ConcatFailed(f"{p.name} is not a readable video file")
        durations.append(d)
    expected_duration = sum(durations)

    try:
        result = _run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            # Explicit stream selection, not just "-c copy": phone recordings
            # commonly carry extra tracks beyond picture + the real audio
            # (spatial/positional audio variants, embedded motion/gyro
            # metadata) and the exact count of these can differ between
            # parts even from the same camera/session (one extra metadata
            # track on one part was enough to corrupt a copy-everything
            # join in testing - wrong duration, non-monotonic timestamps).
            # This app only ever needs the picture and the real audio, so
            # there's no reason to carry the rest through at all.
            "-map", "0:v:0", "-map", "0:a:0",
            "-c", "copy", str(tmp_out),
        ], timeout=STREAM_COPY_TIMEOUT_SECONDS)
        ok = (
            result.returncode == 0
            and tmp_out.exists()
            and tmp_out.stat().st_size > 0
            and _duration_matches(_probe_duration(tmp_out), expected_duration)
        )
        if not ok:
            # Stream-copy concat requires matching codec parameters across
            # parts (or a part may simply be unreadable) - fall back to a
            # re-encoding join, which can handle parts that don't match
            # (e.g. the camera's settings changed between the stop and the
            # restart) and fails loudly rather than silently on a genuinely
            # broken part.
            inputs: List[str] = []
            for p in parts:
                inputs += ["-i", str(p)]
            streams = "".join(f"[{i}:v][{i}:a]" for i in range(len(parts)))
            filter_complex = f"{streams}concat=n={len(parts)}:v=1:a=1[v][a]"
            result2 = _run([
                "ffmpeg", "-y", *inputs,
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                str(tmp_out),
            ], timeout=_reencode_timeout_seconds(expected_duration))
            ok2 = (
                result2.returncode == 0
                and tmp_out.exists()
                and tmp_out.stat().st_size > 0
                and _duration_matches(_probe_duration(tmp_out), expected_duration)
            )
            if not ok2:
                raise ConcatFailed(
                    f"Could not join {len(parts)} video parts (tried a lossless "
                    f"join and a re-encoding join, both failed or produced a "
                    f"result shorter than expected - a part may be unreadable "
                    f"or corrupt):\n{result2.stdout[-4000:]}"
                )
        tmp_out.replace(out_path)
    except Exception:
        raise
    else:
        for p in parts:
            if p != out_path:
                p.unlink(missing_ok=True)
    finally:
        list_file.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
