"""Render a :class:`~src.guide.Guide` into a Word .docx with images + captions."""
from __future__ import annotations

from pathlib import Path

from . import config
from .guide import Guide


def export_docx(guide: Guide, out_path: str | Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    doc.add_heading(guide.title or "Step-by-Step Guide", level=0)

    if guide.intro:
        doc.add_paragraph(guide.intro)

    if guide.prerequisites:
        doc.add_heading("Prerequisites", level=1)
        for item in guide.prerequisites:
            doc.add_paragraph(item, style="List Bullet")

    doc.add_heading("Steps", level=1)

    figure_no = 0
    for i, step in enumerate(guide.steps, start=1):
        heading = step.heading.strip() if step.heading else ""
        doc.add_heading(f"Step {i}: {heading}" if heading else f"Step {i}", level=2)

        if step.text:
            doc.add_paragraph(step.text)

        if step.image_path and Path(step.image_path).exists():
            try:
                doc.add_picture(step.image_path, width=Inches(config.DOCX_IMAGE_WIDTH_INCHES))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            except Exception:
                pass  # skip an unreadable image rather than fail the export
            else:
                if step.caption:
                    figure_no += 1
                    caption_par = doc.add_paragraph()
                    caption_par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    run = caption_par.add_run(f"Figure {figure_no}: {step.caption}")
                    run.italic = True
                    run.font.size = Pt(9)

    doc.save(str(out_path))
    return out_path
