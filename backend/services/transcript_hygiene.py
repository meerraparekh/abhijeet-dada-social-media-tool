"""Filters out Whisper hallucinations before transcript text reaches Claude.

During unclear or near-silent audio, smaller Whisper models (this project
defaults to "base" for speed) sometimes fabricate plausible-looking text in
a random, unrelated language instead of correctly transcribing nothing - a
well-documented failure mode. For a Hindi/English spiritual talk, there is
no legitimate reason for Chinese, Japanese, or Korean script to appear; its
presence is a reliable signal that a segment is hallucinated garbage rather
than real content. Confirmed against a real session: a multi-minute stretch
of mixed Korean/Japanese/English nonsense was present during what was
almost certainly a quiet/unclear stretch of audio, and sending it to Claude
alongside the real transcript was the likely cause of clip suggestions
coming back completely empty for that session.
"""
from __future__ import annotations

import re

_CJK_PATTERN = re.compile(
    "["
    "一-鿿"  # CJK Unified Ideographs (Chinese)
    "぀-ゟ"  # Hiragana (Japanese)
    "゠-ヿ"  # Katakana (Japanese)
    "가-힣"  # Hangul syllables (Korean)
    "]"
)


def looks_hallucinated(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))
