"""Generate English blog posts from a session's transcript.

Strictly restructures/translates what was actually said - never invents
facts, examples, or teachings not present in the transcript (the user was
explicit about this). A ~1.5-2 hour session has enough material for several
shorter, topic-based posts rather than one long dump.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from pydantic import BaseModel

from schemas import Transcript
from services.claude_client import call_structured
from services.clip_suggester import format_transcript_for_prompt

SYSTEM_PROMPT = """\
You turn a spiritual talk's transcript into written blog posts in English.
The talk itself is mostly in Hindi (with occasional English words); you will
be given a timestamped transcript of a ~1.5-2 hour session.

Identify the distinct topics, stories, parables, and question-and-answer
exchanges covered across the WHOLE session, and write one shorter blog post
per topic - not a single long post covering everything. A session this
length usually covers enough separate ground for somewhere around 5-15
posts, depending on how much distinct material it actually contains. Spread
posts across the whole session - don't stop early and leave the back half
uncovered.

CRITICAL - do not add anything of your own: every post must be strictly and
only a restructured, translated, readably-written version of what was
actually said in the transcript below. You may reorganize, condense, cut
repeated or redundant points, drop filler, and rephrase into clear written
prose (a raw transcript doesn't read well as a blog verbatim) - but never
introduce a fact, example, claim, or teaching that is not present in the
source transcript. If you are unsure whether something was really said,
leave it out rather than guess or embellish.

For each post, provide:
- title: a blog-style title in English
- tags: 2-5 short topical keywords in English (the same kind of vocabulary
  as clip tags - e.g. "surrender", "fear-of-death", "parable",
  "guru-disciple-relationship")
- body: the blog post itself, in English, as plain paragraphs separated by
  a blank line - natural written prose, not a transcript dump, but strictly
  faithful to the source
- source_start_seconds / source_end_seconds: the real transcript timestamp
  range this post's content is drawn from (if it draws from more than one
  place in the session, use the earliest start and latest end)
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "body": {"type": "string"},
                    "source_start_seconds": {"type": "number"},
                    "source_end_seconds": {"type": "number"},
                },
                "required": [
                    "title",
                    "tags",
                    "body",
                    "source_start_seconds",
                    "source_end_seconds",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["posts"],
    "additionalProperties": False,
}


class BlogPostSuggestion(BaseModel):
    title: str
    tags: List[str]
    body: str
    source_start_seconds: float
    source_end_seconds: float


class BlogPostSuggestions(BaseModel):
    posts: List[BlogPostSuggestion]


def generate_blog_posts(
    transcript: Transcript, progress_cb: Optional[Callable[[str], None]] = None
) -> BlogPostSuggestions:
    transcript_text = format_transcript_for_prompt(transcript)
    return call_structured(
        system=SYSTEM_PROMPT,
        user_content=f"Timestamped transcript:\n\n{transcript_text}",
        schema=_SCHEMA,
        result_model=BlogPostSuggestions,
        # Several full blog posts out of a ~2 hour transcript is a lot of
        # output text - same headroom as clip suggestion/translation to avoid
        # the max_tokens truncation failures hit earlier in this project.
        max_tokens=64000,
        progress_cb=progress_cb,
    )
