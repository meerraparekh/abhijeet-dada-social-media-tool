"""Ask Claude to read the transcript and propose short, self-contained clips.

This is the one step that costs real (tiny) money - everything else in the
pipeline is local and free. A ~2 hour session transcript is roughly
20-30K input tokens; Claude Sonnet 5 is plenty capable for "find the
quotable moments and draft captions" and is far cheaper than Opus for a
task like this - expect well under $0.15 per session.
"""
from __future__ import annotations

import anthropic

from config import CLAUDE_MODEL
from schemas import ClipSuggestions, Transcript

SYSTEM_PROMPT = """\
You help a spiritual teaching group turn a long recorded session into short social
media clips. You will be given a timestamped transcript of a ~2 hour talk.

Find 10-20 moments that work as standalone clips of roughly 45-120 seconds each:
- Each clip must be a complete thought, story, or teaching point - never start or
  end mid-sentence, and never require context from outside the clip to make sense.
- Prefer moments with a strong opening line in the first few seconds (a question,
  a surprising statement, a story hook) since that's what stops someone scrolling.
- Spread clips across the whole session rather than clustering them in one part.
- Do not overlap clips.
- start_seconds and end_seconds must be real timestamps taken from the transcript
  you were given, not estimates.
"""


def format_transcript_for_prompt(transcript: Transcript) -> str:
    lines = []
    for seg in transcript.segments:
        lines.append(f"[{_mmss(seg.start)}-{_mmss(seg.end)}] {seg.text}")
    return "\n".join(lines)


def _mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def suggest_clips(transcript: Transcript) -> ClipSuggestions:
    client = anthropic.Anthropic()
    transcript_text = format_transcript_for_prompt(transcript)

    # A real ~2 hour transcript can prompt Claude toward the higher end of the
    # 10-20 suggested clips, each with several caption fields - give it real
    # headroom so the JSON response doesn't get cut off mid-way.
    response = client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Timestamped transcript:\n\n{transcript_text}",
            }
        ],
        output_format=ClipSuggestions,
    )
    if response.parsed_output is None:
        raise RuntimeError(
            "Claude's response didn't finish as valid clip suggestions "
            f"(stop_reason={response.stop_reason!r}). This usually means the "
            "response was cut off before completing - try again, or if it "
            "keeps happening, the transcript may need to be shortened."
        )
    return response.parsed_output
