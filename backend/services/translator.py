"""Translate the Hindi transcript into English, for burned-in captions aimed
at a wider (English-speaking) audience.

Segment boundaries and timing come straight from Whisper and are already
accurate - only the text itself needs translating, so this asks Claude for
one English translation per segment (with full sentence context, for
quality) rather than trying to translate word-by-word, which can't
preserve per-word timing anyway since translated word order/count differs
from the original.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel

from schemas import Transcript, TranscriptWord
from services.claude_client import call_structured

SYSTEM_PROMPT = """\
You translate a spiritual talk's transcript from Hindi (with occasional English
words) into natural, fluent English, for burned-in video captions read by a
wide English-speaking audience.

You will be given a numbered list of transcript segments. Return exactly one
English translation per segment, in the same order, as a JSON array of
strings the same length as the input list. Translate meaning and natural tone
rather than word-for-word. Keep each translation a natural spoken-English
rendering of that segment - don't add commentary, notes, or merge/split
segments.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["translations"],
    "additionalProperties": False,
}


class _Translations(BaseModel):
    translations: List[str]


def translate_segments(transcript: Transcript) -> List[str]:
    """Returns one English translation string per transcript segment, in order."""
    numbered = "\n".join(f"{i + 1}. {seg.text}" for i, seg in enumerate(transcript.segments))
    result = call_structured(
        system=SYSTEM_PROMPT,
        user_content=f"Segments:\n\n{numbered}",
        schema=_SCHEMA,
        result_model=_Translations,
        # A full transcript's worth of translated text can be sizeable for a
        # long session - this is a lot more output than clip suggestions,
        # which only cover a handful of chosen moments.
        max_tokens=64000,
    )
    if len(result.translations) != len(transcript.segments):
        raise RuntimeError(
            f"Claude returned {len(result.translations)} translations for "
            f"{len(transcript.segments)} segments - counts must match exactly. "
            "Try again."
        )
    return result.translations


def build_english_caption_words(transcript: Transcript, translations: List[str]) -> List[TranscriptWord]:
    """Turn segment-level translations into word-level timing for caption
    burning, by spreading each segment's translated words evenly across that
    segment's real (Whisper-timed) start/end span. Not frame-accurate to the
    actual English words being "spoken" (there's no English audio to align
    to), but stays closely synced to the real Hindi speech's pacing since
    segment boundaries themselves are accurate."""
    words: List[TranscriptWord] = []
    for seg, translation in zip(transcript.segments, translations):
        seg_words = translation.split()
        if not seg_words:
            continue
        duration = max(seg.end - seg.start, 0.01)
        step = duration / len(seg_words)
        for i, w in enumerate(seg_words):
            words.append(
                TranscriptWord(start=seg.start + i * step, end=seg.start + (i + 1) * step, word=" " + w)
            )
    return words
