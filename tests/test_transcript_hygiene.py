"""Regression test for a real production bug: a Whisper hallucination (a
stretch of Chinese/Japanese/Korean nonsense mixed into an otherwise Hindi
transcript, a known Whisper failure mode during unclear/near-silent audio)
reaching Claude's clip-suggestion prompt was the likely cause of a real
session's "Suggest clips" returning zero clips despite a full, complete
transcript."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from schemas import Transcript, TranscriptSegment  # noqa: E402
from services.clip_suggester import format_transcript_for_prompt  # noqa: E402
from services.transcript_hygiene import looks_hallucinated  # noqa: E402


def test_looks_hallucinated_flags_cjk_script():
    # an actual hallucinated line observed in production
    assert looks_hallucinated("저는制eの発値に対応して努力や考慮者に遭唄されない")


def test_looks_hallucinated_leaves_real_content_alone():
    assert not looks_hallucinated("Utske hisaam se Hari ka ek chagar")
    assert not looks_hallucinated("the nature of stillness")
    # Urdu/Arabic script is a different (milder) Whisper quirk, not CJK -
    # left alone here since it's at least plausibly-related content, unlike
    # Chinese/Japanese/Korean which has no legitimate reason to appear at all
    assert not looks_hallucinated("والملمنے کیوں؟")


def test_format_transcript_for_prompt_drops_hallucinated_lines():
    transcript = Transcript(
        language="hi",
        segments=[
            TranscriptSegment(start=0.0, end=5.0, text="Utske hisaam se Hari ka ek chagar"),
            TranscriptSegment(start=5.0, end=10.0, text="彼OOK試는 when you go maybe you feel compelled"),
            TranscriptSegment(start=10.0, end=15.0, text="samerat ki kyo bhumi ka ki"),
        ],
    )
    formatted = format_transcript_for_prompt(transcript)
    assert "Utske hisaam se Hari ka ek chagar" in formatted
    assert "samerat ki kyo bhumi ka ki" in formatted
    assert "彼" not in formatted and "OOK試" not in formatted
    # exactly the two real lines should survive, not the hallucinated one
    assert len(formatted.splitlines()) == 2


def test_translate_segments_replaces_hallucinated_text_without_dropping_the_slot(monkeypatch):
    """translate_segments must keep the translation list the exact same
    length/order as transcript.segments (build_english_caption_words zips
    them together) - so a hallucinated segment is swapped for a neutral
    placeholder in the prompt, never dropped."""
    import services.translator as translator_module
    from pydantic import BaseModel
    from typing import List

    transcript = Transcript(
        language="hi",
        segments=[
            TranscriptSegment(start=0.0, end=5.0, text="Utske hisaam se Hari ka ek chagar"),
            TranscriptSegment(start=5.0, end=10.0, text="彼OOK試는 when you go maybe you feel compelled"),
            TranscriptSegment(start=10.0, end=15.0, text="samerat ki kyo bhumi ka ki"),
        ],
    )

    captured = {}

    class _FakeResult(BaseModel):
        translations: List[str]

    def fake_call_structured(system, user_content, schema, result_model, max_tokens, progress_cb=None):
        captured["user_content"] = user_content
        return _FakeResult(translations=["one", "(inaudible)", "three"])

    monkeypatch.setattr(translator_module, "call_structured", fake_call_structured)

    result = translator_module.translate_segments(transcript)

    assert "彼" not in captured["user_content"]
    assert "(inaudible)" in captured["user_content"]
    assert "Utske hisaam se Hari ka ek chagar" in captured["user_content"]
    assert "samerat ki kyo bhumi ka ki" in captured["user_content"]
    assert len(result) == 3  # same length as transcript.segments, nothing dropped
