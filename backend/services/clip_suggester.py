"""Ask Claude to read the transcript and propose short, self-contained clips.

This is one of the two steps that cost real (tiny) money - everything else
in the pipeline is local and free. A ~2 hour session transcript is roughly
20-30K input tokens; Claude Sonnet 5 is plenty capable for "find the
quotable moments and draft captions" and is far cheaper than Opus for a
task like this - expect well under $0.15 per session for this step.
"""
from __future__ import annotations

from schemas import ClipSuggestions, Transcript
from services.claude_client import call_structured

# Hand-written mirror of the ClipSuggestions/ClipSuggestion schema for the
# Messages API's structured-output format. Written out explicitly (rather
# than derived from the Pydantic model's own .model_json_schema()) since the
# API expects a single flat schema, not one with $ref/$defs indirection.
_CLIP_SUGGESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "title": {"type": "string"},
                    "hook_hi": {"type": "string"},
                    "hook_en": {"type": "string"},
                    "youtube_title_hi": {"type": "string"},
                    "youtube_title_en": {"type": "string"},
                    "instagram_caption_hi": {"type": "string"},
                    "instagram_caption_en": {"type": "string"},
                    "twitter_text_hi": {"type": "string"},
                    "twitter_text_en": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "start_seconds",
                    "end_seconds",
                    "title",
                    "hook_hi",
                    "hook_en",
                    "youtube_title_hi",
                    "youtube_title_en",
                    "instagram_caption_hi",
                    "instagram_caption_en",
                    "twitter_text_hi",
                    "twitter_text_en",
                    "hashtags",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You help a spiritual teaching group turn a long recorded session into short social
media clips. The talk itself is mostly in Hindi (with occasional English words),
and you will be given a timestamped transcript of a ~2 hour session in that mix.

Find 8-15 moments that work as standalone clips of roughly 45-120 seconds each:
- Each clip must be a complete thought, story, or teaching point - never start or
  end mid-sentence, and never require context from outside the clip to make sense.
- Prefer moments with a strong opening line in the first few seconds (a question,
  a surprising statement, a story hook) since that's what stops someone scrolling.
- Spread clips across the whole session rather than clustering them in one part.
- Do not overlap clips.
- start_seconds and end_seconds must be real timestamps taken from the transcript
  you were given, not estimates.

The on-screen captions burned into the video are handled separately (a full
English translation, not written by you) - what you ARE writing is the text
that goes in the post itself (title/caption/tweet), wanted in BOTH Hindi and
English so either can be used depending on the audience: for each of hook,
youtube_title, instagram_caption, and twitter_text, provide an "_hi" version
(natural Hindi, not a stiff literal translation) and an "_en" version (a
natural English rendering of the same content, not a word-for-word translation).

Keep every field concise - a short internal title, a one-line hook, a punchy
YouTube title, a 1-2 sentence Instagram caption, a tweet under 200 characters,
and 3-6 hashtags (hashtags in English/roman script, as is conventional even
for Hindi-language posts). Do not pad any field with extra commentary.
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
    transcript_text = format_transcript_for_prompt(transcript)
    # A real ~2 hour transcript can prompt Claude toward the higher end of
    # the requested clip count, each with several caption fields - give it
    # real headroom (32000) so the JSON response doesn't get cut off mid-way.
    return call_structured(
        system=SYSTEM_PROMPT,
        user_content=f"Timestamped transcript:\n\n{transcript_text}",
        schema=_CLIP_SUGGESTIONS_SCHEMA,
        result_model=ClipSuggestions,
        max_tokens=32000,
    )
