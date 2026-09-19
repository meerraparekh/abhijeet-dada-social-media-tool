"""Ask Claude to find filler words and obvious mistakes within a single
clip's transcript, so they can be cut out the same way silence gaps are -
reuses the same time-range-to-cut machinery in clipper.py.

Runs on one short clip's transcript (45-120s), not the whole session, so
it's a small, fast, cheap call - nothing like the scale of clip suggestion
or caption translation, which read/write the whole session.
"""
from __future__ import annotations

from typing import List, Tuple

from pydantic import BaseModel

from schemas import TranscriptWord
from services.claude_client import call_structured

SYSTEM_PROMPT = """\
You review a short clip (45-120 seconds) from a Hindi spiritual talk for
filler words, false starts, and obvious mistakes worth cutting to make the
clip tighter - the same way a podcast editor removes "um"s.

You will be given the clip's words with their timestamps. Return time ranges
to cut for:
- Filler words/sounds (um, uh, "matlab" used as a verbal tic, "so", "like",
  a word or short phrase repeated from a stutter or false restart)
- False starts immediately corrected ("we should - no wait, actually...")
  where the abandoned first attempt is redundant with what follows
- Obvious slips immediately self-corrected, where cutting the slip loses
  nothing from the meaning

Be conservative: only flag something you're genuinely confident is removable
filler or error - never meaningful content, a natural pause for emphasis, or
anything you're unsure about. A missed filler word costs nothing; wrongly
cutting real content ruins the clip. Returning an empty list is completely
fine if nothing in this clip qualifies.

start_seconds/end_seconds for each cut must exactly match the word
boundaries from the timestamps you were given, not estimates - and must fall
entirely within the clip's own range.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["start_seconds", "end_seconds", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cuts"],
    "additionalProperties": False,
}


class _Cut(BaseModel):
    start_seconds: float
    end_seconds: float
    reason: str


class _Cuts(BaseModel):
    cuts: List[_Cut]


def detect_removable_spans(
    words: List[TranscriptWord], clip_start: float, clip_end: float
) -> List[Tuple[float, float]]:
    """Returns (start, end) spans within [clip_start, clip_end) - the raw
    video's original timeline - that Claude flagged as filler words or
    obvious mistakes worth cutting. Empty list if there's nothing to flag or
    no word timing available for this range."""
    in_range = [w for w in words if w.start >= clip_start and w.end <= clip_end]
    if not in_range:
        return []
    lines = "\n".join(f"{w.start:.2f}-{w.end:.2f}: {w.word.strip()}" for w in in_range)
    result = call_structured(
        system=SYSTEM_PROMPT,
        user_content=f"Clip words:\n\n{lines}",
        schema=_SCHEMA,
        result_model=_Cuts,
        max_tokens=8000,
    )
    return [
        (c.start_seconds, c.end_seconds)
        for c in result.cuts
        if c.end_seconds > c.start_seconds and c.start_seconds >= clip_start and c.end_seconds <= clip_end
    ]
