"""Pydantic models: both the on-disk session shape and the Claude structured-output shape."""
from __future__ import annotations

import time
import uuid
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Platform = Literal["youtube", "instagram_reel", "twitter"]
ClipStatus = Literal["suggested", "claimed", "rendering", "done"]
SessionStatus = Literal[
    "uploaded", "transcribing", "transcribed", "suggesting", "ready", "error"
]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---- Claude structured-output schema (what we ask the API to return) ----

class ClipSuggestion(BaseModel):
    start_seconds: float = Field(description="Clip start time in seconds from the start of the session")
    end_seconds: float = Field(description="Clip end time in seconds from the start of the session")
    title: str = Field(description="Short internal label for this clip, not shown to viewers")
    # Social text is bilingual (Hindi + English) - the talk itself is in Hindi,
    # and the burned-in on-screen captions stay in Hindi to match, but the
    # posted title/caption/tweet text is wanted in both languages so either
    # can be used depending on the audience.
    hook_hi: str = Field(description="The opening line or moment that makes this clip worth stopping to watch, in Hindi")
    hook_en: str = Field(description="English translation of hook_hi")
    youtube_title_hi: str = Field(description="A YouTube-style title for this clip in Hindi, under 100 characters")
    youtube_title_en: str = Field(description="English translation of youtube_title_hi, under 100 characters")
    instagram_caption_hi: str = Field(description="A ready-to-post Instagram Reel caption in Hindi, 1-3 sentences")
    instagram_caption_en: str = Field(description="English translation of instagram_caption_hi")
    twitter_text_hi: str = Field(description="A ready-to-post tweet/X post text in Hindi, under 280 characters")
    twitter_text_en: str = Field(description="English translation of twitter_text_hi, under 280 characters")
    hashtags: List[str] = Field(description="3-8 relevant hashtags, without the # symbol - conventionally in English/roman script for discoverability even on Hindi-language posts")


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


class Clip(BaseModel):
    id: str = Field(default_factory=new_id)
    start_seconds: float
    end_seconds: float
    title: str = ""
    hook_hi: str = ""
    hook_en: str = ""
    youtube_title_hi: str = ""
    youtube_title_en: str = ""
    instagram_caption_hi: str = ""
    instagram_caption_en: str = ""
    twitter_text_hi: str = ""
    twitter_text_en: str = ""
    hashtags: List[str] = Field(default_factory=list)
    assignee: str = ""
    status: ClipStatus = "suggested"
    rendered_files: dict = Field(default_factory=dict)  # platform -> relative file path


class Session(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    created_at: float = Field(default_factory=time.time)
    video_filename: Optional[str] = None
    status: SessionStatus = "uploaded"
    error: Optional[str] = None
    transcript: Optional[Transcript] = None
    clips: List[Clip] = Field(default_factory=list)


class JobStatus(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: str
    session_id: str
    state: Literal["running", "done", "error"] = "running"
    progress: str = ""
    error: Optional[str] = None
