"""PDF report generator using reportlab (pure Python, no system deps)."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from prof_data import get_profile

DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(__file__))
OUTPUT_DIR = os.path.join(DATA_DIR, "reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Base-14 Helvetica has no Cyrillic glyphs — register a Unicode-capable
# TTF and fall back to Helvetica only if it's unavailable (e.g. on Linux).
try:
    pdfmetrics.registerFont(TTFont("PDFSans", r"C:\Windows\Fonts\arial.ttf"))
    pdfmetrics.registerFont(TTFont("PDFSans-Bold", r"C:\Windows\Fonts\arialbd.ttf"))
    FONT_REGULAR = "PDFSans"
    FONT_BOLD = "PDFSans-Bold"
except Exception:
    FONT_REGULAR = "Helvetica"
    FONT_BOLD = "Helvetica-Bold"

ACCENT = colors.HexColor("#5B54EE")
ACCENT_LIGHT = colors.HexColor("#EDEEFF")
MUTED = colors.HexColor("#888888")
TEXT = colors.HexColor("#1a1a2e")
BG_CARD = colors.HexColor("#f5f4ff")


async def generate_pdf(result_id: int, tg_user_id: int, scores: dict, test_id: str = "ddo") -> str:
    out_path = os.path.join(OUTPUT_DIR, f"report_{result_id}.pdf")
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=20*mm,
        rightMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
    )

    styles = getSampleStyleSheet()
    style_h1 = ParagraphStyle("h1", fontSize=24, leading=30, fontName=FONT_BOLD,
                               textColor=TEXT, spaceAfter=4)
    style_sub = ParagraphStyle("sub", fontSize=12, fontName=FONT_REGULAR,
                                textColor=MUTED, spaceAfter=20)
    style_label = ParagraphStyle("label", fontSize=9, fontName=FONT_BOLD,
                                  textColor=MUTED, spaceAfter=8,
                                  letterSpacing=1.5)
    style_normal = ParagraphStyle("normal", fontSize=11, fontName=FONT_REGULAR,
                                   textColor=TEXT)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    max_score = sorted_scores[0][1] if sorted_scores else 1
    top3 = [name for name, _ in sorted_scores[:3]]

    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    header_data = [
        [Paragraph("<b><font color='#5B54EE'>Попробуй.</font></b>",
                   ParagraphStyle("logo", fontSize=22, fontName=FONT_BOLD)),
         Paragraph(f"Отчёт #{result_id}<br/>@poprobui_bot",
                   ParagraphStyle("meta", fontSize=9, fontName=FONT_REGULAR,
                                   textColor=MUTED, alignment=TA_RIGHT))]
    ]
    header_table = Table(header_data, colWidths=[100*mm, 70*mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, ACCENT),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 14))

    # ── Title ────────────────────────────────────────────────────────────────
    story.append(Paragraph("Твой профиль интересов", style_h1))
    story.append(Paragraph("Результаты профориентационного тестирования", style_sub))

    # ── Top 3 cards ─────────────────────────────────────────────────────────
    story.append(Paragraph("ТОП-3 НАПРАВЛЕНИЯ", style_label))
    ranks = ["Первое место", "Второе место", "Третье место"]
    top_data = [[
        Table(
            [[Paragraph(f"<font color='#888888' size='8'>{ranks[i]}</font>", style_normal)],
             [Paragraph(f"<b><font color='{'#ffffff' if i==0 else '#5B54EE'}'>{name}</font></b>",
                        ParagraphStyle("card_name", fontSize=13, leading=17, fontName=FONT_BOLD,
                                        alignment=TA_CENTER))]],
            colWidths=[55*mm]
        )
        for i, name in enumerate(top3)
    ]]
    top_table = Table(top_data, colWidths=[57*mm, 57*mm, 57*mm], hAlign="LEFT")
    top_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), ACCENT),
        ("BACKGROUND", (1, 0), (1, 0), BG_CARD),
        ("BACKGROUND", (2, 0), (2, 0), BG_CARD),
        ("ROUNDEDCORNERS", (0, 0), (-1, -1), [8, 8, 8, 8]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("INNERGRID", (0, 0), (-1, -1), 0, colors.white),
        ("BOX", (0, 0), (-1, -1), 0, colors.white),
    ]))
    story.append(top_table)
    story.append(Spacer(1, 20))

    # ── Bar chart ────────────────────────────────────────────────────────────
    story.append(Paragraph("ПРОФИЛЬ ПО ВСЕМ ШКАЛАМ", style_label))

    bar_rows = []
    for name, score in sorted_scores:
        pct = score / max_score if max_score else 0
        is_top = name in top3
        bar_color = ACCENT if is_top else colors.HexColor("#d0cdf7")
        name_style = ParagraphStyle(
            "bar_name", fontSize=11,
            fontName=FONT_BOLD if is_top else FONT_REGULAR,
            textColor=ACCENT if is_top else TEXT,
            alignment=TA_RIGHT,
        )
        # bar as a mini-table
        bar_width = 90 * mm
        filled = bar_width * pct
        bar_inner = Table(
            [[""]],
            colWidths=[filled if filled > 2 else 2],
            rowHeights=[8]
        )
        bar_inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), bar_color),
            ("ROUNDEDCORNERS", (0, 0), (0, 0), [4, 4, 4, 4]),
        ]))
        bar_outer = Table([[bar_inner]], colWidths=[bar_width], rowHeights=[8])
        bar_outer.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f0f0f5")),
            ("ROUNDEDCORNERS", (0, 0), (0, 0), [4, 4, 4, 4]),
            ("TOPPADDING", (0, 0), (0, 0), 0),
            ("BOTTOMPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ]))

        bar_rows.append([
            Paragraph(name, name_style),
            bar_outer,
            Paragraph(str(score), ParagraphStyle("score", fontSize=10,
                                                   textColor=MUTED, alignment=TA_RIGHT)),
        ])

    bar_table = Table(bar_rows, colWidths=[45*mm, 90*mm, 10*mm], hAlign="LEFT")
    bar_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(bar_table)

    # ── Profile: professions & salary ──────────────────────────────────────────
    top_type = top3[0] if top3 else None
    profile = get_profile(top_type, test_id) if top_type else None
    if profile:
        story.append(Spacer(1, 22))
        story.append(Paragraph("ПОДХОДЯЩИЕ ПРОФЕССИИ", style_label))
        story.append(Paragraph(profile["description"], style_normal))
        story.append(Spacer(1, 8))
        if profile.get("salary"):
            story.append(Paragraph(
                f"<b><font color='#16A34A'>Зарплаты в этой сфере: {profile['salary']}</font></b>",
                ParagraphStyle("salary", fontSize=11, fontName=FONT_REGULAR, spaceAfter=10)
            ))
        if profile.get("profs"):
            prof_text = "&nbsp;&nbsp;•&nbsp;&nbsp;".join(profile["profs"])
            story.append(Paragraph(
                prof_text,
                ParagraphStyle("profs", fontSize=11, fontName=FONT_BOLD,
                               textColor=ACCENT, leading=18)
            ))

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#eeeeee")))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Попробуй — профориентация для школьников и студентов · @poprobui_bot",
        ParagraphStyle("footer", fontSize=9, fontName=FONT_REGULAR,
                        textColor=MUTED, alignment=TA_CENTER)
    ))

    doc.build(story)
    return out_path
