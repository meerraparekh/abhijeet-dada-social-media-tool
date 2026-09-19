"""Unit test for filler/mistake detection - fakes the Claude call (same
pattern as clip suggestion/translation tests) and checks the plumbing:
building the prompt from word timing, and filtering the response to valid,
in-range cuts.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from schemas import TranscriptWord  # noqa: E402
from services import filler_detector  # noqa: E402


def test_detect_removable_spans_filters_to_valid_in_range_cuts(monkeypatch):
    words = [
        TranscriptWord(start=0.0, end=0.5, word=" um"),
        TranscriptWord(start=0.6, end=1.0, word=" so"),
        TranscriptWord(start=1.1, end=2.0, word=" real"),
        TranscriptWord(start=2.1, end=3.0, word=" content"),
    ]

    def fake_call_structured(**kwargs):
        return filler_detector._Cuts(
            cuts=[
                filler_detector._Cut(start_seconds=0.0, end_seconds=0.5, reason="filler"),
                filler_detector._Cut(start_seconds=0.6, end_seconds=1.0, reason="filler"),
                # Invalid: end before start - should be dropped.
                filler_detector._Cut(start_seconds=5.0, end_seconds=4.0, reason="bad"),
                # Invalid: outside the clip's range entirely - should be dropped.
                filler_detector._Cut(start_seconds=10.0, end_seconds=11.0, reason="out of range"),
            ]
        )

    monkeypatch.setattr(filler_detector, "call_structured", fake_call_structured)

    spans = filler_detector.detect_removable_spans(words, clip_start=0.0, clip_end=3.0)
    assert spans == [(0.0, 0.5), (0.6, 1.0)]


def test_detect_removable_spans_empty_with_no_words_in_range():
    spans = filler_detector.detect_removable_spans([], clip_start=0.0, clip_end=10.0)
    assert spans == []
