"""Ask Claude to read the transcript and propose short, self-contained clips.

This is the one step that costs real (tiny) money - everything else in the
pipeline is local and free. A ~2 hour session transcript is roughly
20-30K input tokens; Claude Sonnet 5 is plenty capable for "find the
quotable moments and draft captions" and is far cheaper than Opus for a
task like this - expect well under $0.15 per session.
"""
from __future__ import annotations

import json
import time

import anthropic
import httpx2
import pydantic

from config import CLAUDE_MODEL
from schemas import ClipSuggestions, Transcript

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
                    "hook": {"type": "string"},
                    "youtube_title": {"type": "string"},
                    "instagram_caption": {"type": "string"},
                    "twitter_text": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "start_seconds",
                    "end_seconds",
                    "title",
                    "hook",
                    "youtube_title",
                    "instagram_caption",
                    "twitter_text",
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
media clips. You will be given a timestamped transcript of a ~2 hour talk.

Find 8-15 moments that work as standalone clips of roughly 45-120 seconds each:
- Each clip must be a complete thought, story, or teaching point - never start or
  end mid-sentence, and never require context from outside the clip to make sense.
- Prefer moments with a strong opening line in the first few seconds (a question,
  a surprising statement, a story hook) since that's what stops someone scrolling.
- Spread clips across the whole session rather than clustering them in one part.
- Do not overlap clips.
- start_seconds and end_seconds must be real timestamps taken from the transcript
  you were given, not estimates.

Keep every field concise - a short internal title, a one-line hook, a punchy
YouTube title, a 1-2 sentence Instagram caption, a tweet under 200 characters,
and 3-6 hashtags. Do not pad any field with extra commentary.
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


# Errors that plausibly mean "the connection dropped mid-stream" rather than
# "the request itself is invalid" - worth a couple of automatic retries
# rather than surfacing a scary traceback for what's often a one-off network
# hiccup (flaky wifi, a VPN reconnect, a proxy that doesn't love long-lived
# streaming connections).
_TRANSIENT_NETWORK_ERRORS = (
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    httpx2.RemoteProtocolError,
    httpx2.ReadError,
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
)


def suggest_clips(transcript: Transcript, max_attempts: int = 3) -> ClipSuggestions:
    client = anthropic.Anthropic()
    transcript_text = format_transcript_for_prompt(transcript)

    response = None
    for attempt in range(1, max_attempts + 1):
        try:
            # A real ~2 hour transcript can prompt Claude toward the higher end
            # of the requested clip count, each with several caption fields -
            # give it real headroom so the JSON response doesn't get cut off
            # mid-way. Also counts against this budget: Sonnet 5 thinks by
            # default before answering, which eats into the same token budget
            # as the JSON output itself - this is closer to an extraction/
            # classification task than a hard reasoning problem, so cap effort
            # to keep thinking from eating too much of that budget. A
            # max_tokens this high requires streaming - the SDK refuses a
            # plain (non-streaming) request it estimates could run past ~10
            # minutes.
            with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=32000,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": f"Timestamped transcript:\n\n{transcript_text}",
                    }
                ],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": _CLIP_SUGGESTIONS_SCHEMA},
                },
            ) as stream:
                response = stream.get_final_message()
            break
        except _TRANSIENT_NETWORK_ERRORS as exc:
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Lost connection to Claude {max_attempts} times in a row "
                    f"while waiting for clip suggestions ({exc!r}). This "
                    "usually means something on this network is dropping "
                    "long-running connections (flaky wifi, VPN, or a "
                    "firewall/antivirus proxy) rather than a problem with the "
                    "request itself - try a different network if it keeps "
                    "happening."
                ) from exc
            time.sleep(3 * attempt)

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(
            "Claude's response didn't include any clip suggestions text "
            f"(stop_reason={response.stop_reason!r})."
        )
    try:
        return ClipSuggestions.model_validate_json(text)
    except (json.JSONDecodeError, pydantic.ValidationError) as exc:
        raise RuntimeError(
            "Claude's response wasn't valid clip suggestions "
            f"(stop_reason={response.stop_reason!r}): {exc}. This usually "
            "means the response was cut off before completing - try again, "
            "or if it keeps happening, the transcript may need to be shortened."
        ) from exc
