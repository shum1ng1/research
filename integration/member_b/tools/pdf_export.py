"""A4 report rendering. The line chart and its labels are native PDF content."""
from pathlib import Path

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

_NAVY = (19, 40, 57)
_INK = (39, 54, 69)
_MUTED = (80, 101, 117)
_ACCENT = (23, 103, 128)
_GRID = (218, 229, 237)
_TINT = (239, 245, 249)


def _first_existing(paths):
    return next((path for path in paths if Path(path).is_file()), None)


def _table(pdf, rows):
    from fpdf.fonts import FontFace

    pdf.set_font("Body", size=9)
    with pdf.table(
        width=pdf.epw, col_widths=(32, 21, 47), line_height=5.2, padding=(1.2, 2.8),
        text_align=("LEFT", "LEFT", "LEFT"), borders_layout="HORIZONTAL_LINES",
        headings_style=FontFace(emphasis="BOLD", color=_NAVY, fill_color=_GRID),
        cell_fill_color=_TINT, cell_fill_mode="ROWS",
    ) as table:
        table.row(("Metric", "Value", "Period or basis"))
        for row in rows:
            table.row(row)
    pdf.ln(4)


def _price_chart(pdf, chart):
    x0, y0 = pdf.l_margin, pdf.get_y()
    x1, x2 = x0 + 20, x0 + pdf.epw - 5
    top, bottom = y0 + 12, y0 + 49
    pdf.set_fill_color(*_TINT)
    pdf.rect(x0, y0, pdf.epw, 59, style="F")
    pdf.set_font("Body", "B", 9)
    pdf.set_text_color(*_NAVY)
    pdf.set_xy(x1, y0 + 2)
    pdf.cell(x2 - x1, 6, chart["title"])
    pdf.set_font("Body", size=8)
    pdf.set_text_color(*_MUTED)
    pdf.set_draw_color(*_GRID)
    pdf.set_line_width(0.2)
    for fraction, value in chart["ticks"]:
        y = bottom - fraction * (bottom - top)
        pdf.line(x1, y, x2, y)
        pdf.set_xy(x0 + 1, y - 2.5)
        pdf.cell(x1 - x0 - 2, 5, f"{value:,.2f}", align="R")
    pdf.set_draw_color(*_ACCENT)
    pdf.set_line_width(0.45)
    pdf.polyline([(x1 + x * (x2 - x1), bottom - y * (bottom - top)) for x, y in chart["points"]])
    for label, align in ((chart["start"], "L"), (chart["end"], "R")):
        pdf.set_xy(x1, bottom + 2)
        pdf.cell(x2 - x1, 5, label, align=align)
    pdf.set_xy(x0, y0 + 61)


def export_a4_pdf(layout, destination):
    """Write an A4 draft and return the actual page count."""
    from fpdf import FPDF

    class ResearchPDF(FPDF):
        def footer(self):
            self.set_y(-13)
            self.set_draw_color(*_GRID)
            self.set_line_width(0.2)
            self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
            self.set_font("Body", size=8)
            self.set_text_color(*_MUTED)
            self.cell(self.epw * 0.8, 8, layout["title"])
            self.cell(self.epw * 0.2, 8, str(self.page_no()), align="R")

    regular, bold = _first_existing(FONT_CANDIDATES), _first_existing(BOLD_CANDIDATES)
    if not regular:
        raise RuntimeError("Install Arial or DejaVu Sans to export the A4 report.")
    pdf = ResearchPDF(format="A4")
    pdf.set_margins(17, 16, 17)
    pdf.set_auto_page_break(auto=True, margin=19)
    pdf.add_font("Body", "", regular)
    pdf.add_font("Body", "B", bold or regular)
    pdf.set_title(f"{layout['title']} | Equity research draft")
    pdf.add_page()

    pdf.set_text_color(*_ACCENT)
    pdf.set_font("Body", "B", 9)
    pdf.cell(0, 5, "EQUITY RESEARCH DRAFT", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_NAVY)
    pdf.set_font("Body", "B", 25)
    pdf.multi_cell(0, 11, layout["title"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_MUTED)
    pdf.set_font("Body", size=9.5)
    pdf.multi_cell(0, 6, layout["subtitle"], new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    def body(text, color=_INK, size=10):
        pdf.set_text_color(*color)
        pdf.set_font("Body", size=size)
        pdf.multi_cell(0, 5.4, text, align="L", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    for section in layout["sections"]:
        minimum = 75 if section["heading"] == "Financial trends" else 21
        if pdf.will_page_break(minimum):
            pdf.add_page()
        pdf.set_text_color(*_NAVY)
        pdf.set_font("Body", "B", 13)
        pdf.multi_cell(0, 7, section["heading"], new_x="LMARGIN", new_y="NEXT")
        for pid in section["paragraph_ids"]:
            block = layout["blocks"][pid]
            if block["kind"] == "table":
                pdf.set_text_color(*_INK)
                pdf.set_draw_color(*_GRID)
                pdf.set_line_width(0.2)
                _table(pdf, block["rows"])
            elif block["kind"] == "chart":
                if pdf.will_page_break(69):
                    pdf.add_page()
                _price_chart(pdf, block["chart"])
                body(layout["paragraphs"][pid], _MUTED, 8.5)
            else:
                body(layout["paragraphs"][pid])
        pdf.ln(2)

    if layout["references"]:
        if pdf.will_page_break(15):
            pdf.add_page()
        pdf.set_text_color(*_NAVY)
        pdf.set_font("Body", "B", 10)
        pdf.cell(0, 6, "Referenced documents", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_ACCENT)
        pdf.set_font("Body", size=9)
        for label, url in layout["references"]:
            pdf.multi_cell(0, 5, label, link=url, new_x="LMARGIN", new_y="NEXT")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(destination))
    return len(pdf.pages)
