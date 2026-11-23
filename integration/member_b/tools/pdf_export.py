"""A4 PDF export. Page count is the number of pages actually written."""
from __future__ import annotations

import os
import re
from pathlib import Path

_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
CJK_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)


def _first_existing(paths):
    for path in paths:
        if Path(path).is_file():
            return path
    return None


def export_a4_pdf(blocks, destination, image_path=None):
    """Write an A4 PDF and return the page count. Raises if no usable font exists.

    blocks are {"heading": text} or {"text": paragraph}. A markdown image in a
    paragraph is drawn from image_path instead of being printed as a link.
    """
    from fpdf import FPDF

    latin = _first_existing(FONT_CANDIDATES)
    cjk = _first_existing(CJK_CANDIDATES)
    if not latin and not cjk:
        raise RuntimeError("No local font found for A4 PDF. Install DejaVu or a CJK font.")
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    if latin:
        pdf.add_font("Body", "", latin)
        pdf.set_font("Body", size=11)
        if cjk:
            pdf.add_font("CJK", "", cjk)
            pdf.set_fallback_fonts(["CJK"])
    else:
        pdf.add_font("Body", "", cjk)
        pdf.set_font("Body", size=11)
    for block in blocks:
        heading = block.get("heading")
        if heading:
            pdf.set_font_size(14)
            pdf.multi_cell(0, 8, heading)
            pdf.set_font_size(11)
            pdf.ln(1)
            continue
        paragraph = block.get("text") or ""
        visible = _IMAGE.sub("", paragraph).strip()
        if visible:
            pdf.multi_cell(0, 7, visible)
            pdf.ln(2)
        if image_path and _IMAGE.search(paragraph):
            pdf.image(image_path, w=pdf.epw)
            pdf.ln(3)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    pdf.output(destination)
    return len(pdf.pages)
