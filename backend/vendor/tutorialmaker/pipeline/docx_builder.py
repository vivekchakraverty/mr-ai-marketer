"""Stage 9: assemble the .docx tutorial (text + captioned screenshots).

Layout follows answer-engine best practices: H1 title, an SEO metadata line
(slug / meta description / last-updated), an answer-first "Quick answer" block, the
intro, H2 step sections with captioned screenshots, an FAQ section, and a sources/
trust footer citing the source video.
"""
from __future__ import annotations

import datetime
import os

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

_GREY = RGBColor(0x66, 0x66, 0x66)
_LIGHT = RGBColor(0x88, 0x88, 0x88)


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _grey_line(doc, text: str, size: int = 9, color: RGBColor = _GREY):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.italic = True
    run.font.size = Pt(size)
    run.font.color.rgb = color
    return p


def build_docx(tutorial: dict, selected: dict[int, dict], captions: dict[int, str],
               out_path: str, source_url: str = "") -> str:
    """Write the tutorial to ``out_path`` and return it.

    ``tutorial`` = {title, slug, meta_description, answer, intro, steps, faqs};
    ``selected`` = {step_idx: {time, path}}; ``captions`` = {step_idx: caption}.
    """
    today = datetime.date.today().isoformat()
    doc = Document()
    doc.add_heading(tutorial["title"], level=0)

    # SEO metadata line (slug + last-updated) and meta description.
    meta_bits = []
    if tutorial.get("slug"):
        meta_bits.append(f"Slug: /{tutorial['slug']}/")
    meta_bits.append(f"Last updated: {today}")
    _grey_line(doc, "  •  ".join(meta_bits))
    if tutorial.get("meta_description"):
        _grey_line(doc, f"Meta description: {tutorial['meta_description']}")

    # Answer-first block (quotable by AI assistants).
    if tutorial.get("answer"):
        doc.add_heading("Quick answer", level=2)
        ans = doc.add_paragraph()
        ans.add_run(tutorial["answer"]).bold = True

    if tutorial.get("intro"):
        doc.add_paragraph(tutorial["intro"])

    # Step sections.
    for i, step in enumerate(tutorial["steps"]):
        doc.add_heading(f"{i + 1}. {step['heading']}", level=1)
        if step.get("body"):
            doc.add_paragraph(step["body"])

        sel = selected.get(i)
        if sel and os.path.exists(sel["path"]):
            pic_par = doc.add_paragraph()
            pic_par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic_par.add_run().add_picture(sel["path"], width=Inches(6))

            cap_par = doc.add_paragraph()
            cap_par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap_run = cap_par.add_run(f"{captions.get(i, '')}  ")
            cap_run.italic = True
            cap_run.font.size = Pt(9)
            ts_run = cap_par.add_run(f"(@ {_fmt_ts(sel['time'])})")
            ts_run.italic = True
            ts_run.font.size = Pt(9)
            ts_run.font.color.rgb = _LIGHT

    # FAQ section (visible Q&A — AEO/FAQPage-friendly).
    if tutorial.get("faqs"):
        doc.add_heading("Frequently asked questions", level=1)
        for f in tutorial["faqs"]:
            doc.add_heading(f["q"], level=2)
            doc.add_paragraph(f["a"])

    # Sources / trust footer (E-E-A-T: citation + freshness).
    doc.add_heading("Sources", level=1)
    if source_url:
        _grey_line(doc, f"Adapted from the source video: {source_url}", color=_GREY)
    _grey_line(doc, f"Generated and last updated: {today}.", color=_GREY)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path)
    return out_path
