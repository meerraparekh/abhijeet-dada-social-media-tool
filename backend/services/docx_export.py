"""Word document exports - full-session transcripts and blog posts - via
python-docx. Free and local, same as everything else in this pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import List

from docx import Document

from schemas import BlogPost, TranscriptSegment


def _mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def transcript_segments_to_docx(segments: List[TranscriptSegment], out_path: Path, title: str) -> Path:
    doc = Document()
    doc.add_heading(title, level=1)
    for seg in segments:
        p = doc.add_paragraph()
        ts_run = p.add_run(f"[{_mmss(seg.start)}] ")
        ts_run.bold = True
        p.add_run(seg.text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def blog_posts_to_docx(posts: List[BlogPost], out_path: Path, session_name: str) -> Path:
    doc = Document()
    doc.add_heading(session_name, level=1)
    for post in posts:
        doc.add_heading(post.title, level=2)
        meta = doc.add_paragraph()
        meta_run = meta.add_run(
            f"Source: {_mmss(post.source_start_seconds)}-{_mmss(post.source_end_seconds)}"
            + (f" | Tags: {', '.join(post.tags)}" if post.tags else "")
        )
        meta_run.italic = True
        for para in post.body.split("\n\n"):
            para = para.strip()
            if para:
                doc.add_paragraph(para)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
