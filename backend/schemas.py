"""Pydantic models: both the on-disk session shape and the Claude structured-output shape."""
from __future__ import annotations

import time
import uuid
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ClipStatus = Literal["suggested", "claimed", "rendering", "done"]
SessionStatus = Literal[
    "uploaded", "transcribing", "transcribed", "suggesting", "ready", "error"
]
SyncStatus = Literal["primary", "pending", "synced", "failed"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---- Claude structured-output schema (what we ask the API to return) ----

class ClipSuggestion(BaseModel):
    start_seconds: float = Field(description="Clip start time in seconds from the start of the session")
    end_seconds: float = Field(description="Clip end time in seconds from the start of the session")
    title: str = Field(description="Short internal label for this clip, not shown to viewers")
    # Social text is bilingual (Hindi + English) - the talk itself is in Hindi
    # (burned-in captions are a separate full English translation, handled by
    # services/translator.py), but the posted title/caption/tweet text is
    # wanted in both languages so either can be used depending on the audience.
    hook_hi: str = Field(description="The exact opening line, verbatim from the transcript, that makes this clip worth stopping to watch, in Hindi - not a paraphrase or invented line")
    hook_en: str = Field(description="Natural English translation of hook_hi (same quote, not independently reworded)")
    hook_start_seconds: float = Field(description="Real transcript timestamp where the hook_hi quote begins")
    hook_end_seconds: float = Field(description="Real transcript timestamp where the hook_hi quote ends")
    youtube_title_hi: str = Field(description="A YouTube-style title for this clip in Hindi, under 100 characters")
    youtube_title_en: str = Field(description="English translation of youtube_title_hi, under 100 characters")
    instagram_caption_hi: str = Field(description="A ready-to-post Instagram Reel caption in Hindi, 1-3 sentences")
    instagram_caption_en: str = Field(description="English translation of instagram_caption_hi")
    twitter_text_hi: str = Field(description="A ready-to-post tweet/X post text in Hindi, under 280 characters")
    twitter_text_en: str = Field(description="English translation of twitter_text_hi, under 280 characters")
    hashtags: List[str] = Field(description="3-8 relevant hashtags, without the # symbol - conventionally in English/roman script for discoverability even on Hindi-language posts")
    tags: List[str] = Field(description="2-5 short topical/thematic keywords in English describing what this moment is actually about, for internal organization and later use in blog posts - not social media hashtags")


class ClipSuggestions(BaseModel):
    clips: List[ClipSuggestion]


# ---- On-disk / API models ----

class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptWord(BaseModel):
    start: float
    end: float
    word: str


class Transcript(BaseModel):
    language: Optional[str] = None
    segments: List[TranscriptSegment] = Field(default_factory=list)
    words: List[TranscriptWord] = Field(default_factory=list)
    # English translation, word-level (interpolated within each segment's real
    # timing) - used for burned-in captions aimed at an English-speaking
    # audience. Empty until the "Translate captions" step has been run.
    caption_words_en: List[TranscriptWord] = Field(default_factory=list)
    # English translation, one per segment (same start/end as `segments`,
    # same order) - a readable sentence-level transcript for the English
    # SRT/Word-doc exports, as opposed to caption_words_en's word-level
    # interpolation used only for burning video captions. Empty until
    # "Translate captions" has been run.
    segments_en: List[TranscriptSegment] = Field(default_factory=list)


class Clip(BaseModel):
    id: str = Field(default_factory=new_id)
    start_seconds: float
    end_seconds: float
    title: str = ""
    hook_hi: str = ""
    hook_en: str = ""
    hook_start_seconds: Optional[float] = None
    hook_end_seconds: Optional[float] = None
    youtube_title_hi: str = ""
    youtube_title_en: str = ""
    instagram_caption_hi: str = ""
    instagram_caption_en: str = ""
    twitter_text_hi: str = ""
    twitter_text_en: str = ""
    hashtags: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    assignee: str = ""
    status: ClipStatus = "suggested"
    rendered_files: dict = Field(default_factory=dict)  # angle id -> relative file path


class VideoAngle(BaseModel):
    """One camera angle of the same recording. All timestamps elsewhere
    (clips, transcript, hook) are expressed on the primary angle's own
    timeline; every other angle carries an offset_seconds translating into
    its own timeline - see services/sync.py."""
    id: str = Field(default_factory=new_id)
    label: str  # e.g. "1x" / "2x" / "4x", or whatever the uploader calls it
    filename: str
    is_primary: bool = False
    offset_seconds: float = 0.0  # primary_time + offset_seconds = this angle's time
    duration_seconds: Optional[float] = None  # None until probed
    sync_status: SyncStatus = "pending"
    sync_error: Optional[str] = None


class BlogPost(BaseModel):
    """A topic-based English blog post generated strictly from the actual
    transcript content - not invented material (see services/blog_writer.py).
    source_seconds are the real transcript range it was drawn from, kept for
    traceability back to the recording."""
    id: str = Field(default_factory=new_id)
    title: str = ""
    tags: List[str] = Field(default_factory=list)
    body: str = ""
    source_start_seconds: float = 0.0
    source_end_seconds: float = 0.0


class Session(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    created_at: float = Field(default_factory=time.time)
    angles: List[VideoAngle] = Field(default_factory=list)
    status: SessionStatus = "uploaded"
    error: Optional[str] = None
    transcript: Optional[Transcript] = None
    clips: List[Clip] = Field(default_factory=list)
    blog_posts: List[BlogPost] = Field(default_factory=list)

    def primary_angle(self) -> Optional["VideoAngle"]:
        for a in self.angles:
            if a.is_primary:
                return a
        return self.angles[0] if self.angles else None


class JobStatus(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: str
    session_id: str
    state: Literal["running", "done", "error"] = "running"
    progress: str = ""
    error: Optional[str] = None
