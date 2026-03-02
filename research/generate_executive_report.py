"""
Crypto Hedge Fund 2026 - Executive Report PDF Generator
Produces a polished, executive-grade PDF using ReportLab.
"""

import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm, inch
from reportlab.lib.colors import HexColor, Color, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable, Image, Frame, PageTemplate,
    BaseDocTemplate, NextPageTemplate, Flowable
)
from reportlab.graphics.shapes import Drawing, Rect, Line, String, Circle
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO

# ─── Color Palette (Dark navy executive theme) ───
NAVY       = HexColor('#0B1D3A')
DARK_NAVY  = HexColor('#071428')
NAVY_MID   = HexColor('#132D5E')
ACCENT     = HexColor('#C8A951')   # Gold accent
ACCENT2    = HexColor('#2E86AB')   # Teal accent for charts/tables
LIGHT_GOLD = HexColor('#E8D5A0')
TEXT_DARK  = HexColor('#1A1A2E')
TEXT_MID   = HexColor('#3D3D5C')
TEXT_LIGHT = HexColor('#6B6B8D')
BG_LIGHT   = HexColor('#F7F5F0')   # Warm off-white
BG_TABLE   = HexColor('#F0EDE4')
BG_TABLE2  = HexColor('#FAFAF7')
WHITE      = HexColor('#FFFFFF')
BORDER     = HexColor('#D4CFC2')
GREEN      = HexColor('#2D7D46')
RED_SOFT   = HexColor('#C0392B')
BLUE_SOFT  = HexColor('#2E86AB')

WIDTH, HEIGHT = A4
LEFT_MARGIN = 22 * mm
RIGHT_MARGIN = 22 * mm
TOP_MARGIN = 20 * mm
BOTTOM_MARGIN = 25 * mm
CONTENT_WIDTH = WIDTH - LEFT_MARGIN - RIGHT_MARGIN


# ─── Utility Flowables ───

class GoldRule(Flowable):
    """A thin horizontal gold line."""
    def __init__(self, width, thickness=0.75, color=ACCENT):
        Flowable.__init__(self)
        self.width = width
        self.height = thickness
        self.color = color

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.height)
        self.canv.line(0, 0, self.width, 0)


class NavyBar(Flowable):
    """A full-width navy bar with optional text."""
    def __init__(self, width, height=8*mm, text='', color=NAVY):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self.text = text
        self.color = color

    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        if self.text:
            self.canv.setFillColor(ACCENT)
            self.canv.setFont('Helvetica-Bold', 9)
            self.canv.drawString(4*mm, 2.5*mm, self.text)


class SectionDivider(Flowable):
    """A stylish section divider with gold accent."""
    def __init__(self, width):
        Flowable.__init__(self)
        self.width = width
        self.height = 6 * mm

    def draw(self):
        self.canv.setStrokeColor(ACCENT)
        self.canv.setLineWidth(1.5)
        self.canv.line(0, 3*mm, 30*mm, 3*mm)
        self.canv.setStrokeColor(BORDER)
        self.canv.setLineWidth(0.3)
        self.canv.line(32*mm, 3*mm, self.width, 3*mm)


class CalloutBox(Flowable):
    """A styled callout/highlight box."""
    def __init__(self, text, width, bg=BG_LIGHT, border_color=ACCENT, border_width=2):
        Flowable.__init__(self)
        self.text = text
        self.box_width = width
        self.bg = bg
        self.border_color = border_color
        self.border_width = border_width
        # Calculate height based on text
        style = ParagraphStyle('callout_temp', fontName='Helvetica', fontSize=9,
                               leading=13, textColor=TEXT_DARK)
        p = Paragraph(text, style)
        w, h = p.wrap(width - 16*mm, 1000)
        self.box_height = h + 10*mm
        self.height = self.box_height + 2*mm

    def draw(self):
        # Background
        self.canv.setFillColor(self.bg)
        self.canv.roundRect(0, 0, self.box_width, self.box_height, 3, fill=1, stroke=0)
        # Left border accent
        self.canv.setFillColor(self.border_color)
        self.canv.rect(0, 0, self.border_width, self.box_height, fill=1, stroke=0)
        # Text
        style = ParagraphStyle('callout_draw', fontName='Helvetica', fontSize=9,
                               leading=13, textColor=TEXT_DARK)
        p = Paragraph(self.text, style)
        w, h = p.wrap(self.box_width - 16*mm, 1000)
        p.drawOn(self.canv, 8*mm, self.box_height - h - 5*mm)


# ─── Styles ───

def get_styles():
    styles = {}

    styles['title'] = ParagraphStyle(
        'Title', fontName='Helvetica-Bold', fontSize=28, leading=34,
        textColor=WHITE, alignment=TA_LEFT, spaceAfter=4*mm
    )
    styles['subtitle'] = ParagraphStyle(
        'Subtitle', fontName='Helvetica', fontSize=13, leading=17,
        textColor=LIGHT_GOLD, alignment=TA_LEFT, spaceAfter=2*mm
    )
    styles['section'] = ParagraphStyle(
        'Section', fontName='Helvetica-Bold', fontSize=18, leading=22,
        textColor=NAVY, alignment=TA_LEFT, spaceBefore=6*mm, spaceAfter=3*mm
    )
    styles['subsection'] = ParagraphStyle(
        'Subsection', fontName='Helvetica-Bold', fontSize=13, leading=17,
        textColor=NAVY_MID, alignment=TA_LEFT, spaceBefore=5*mm, spaceAfter=2*mm
    )
    styles['subsubsection'] = ParagraphStyle(
        'SubSubSection', fontName='Helvetica-Bold', fontSize=11, leading=14,
        textColor=TEXT_DARK, alignment=TA_LEFT, spaceBefore=3*mm, spaceAfter=2*mm
    )
    styles['body'] = ParagraphStyle(
        'Body', fontName='Helvetica', fontSize=9.5, leading=14,
        textColor=TEXT_DARK, alignment=TA_JUSTIFY, spaceAfter=2*mm
    )
    styles['body_bold'] = ParagraphStyle(
        'BodyBold', fontName='Helvetica-Bold', fontSize=9.5, leading=14,
        textColor=TEXT_DARK, alignment=TA_LEFT, spaceAfter=2*mm
    )
    styles['bullet'] = ParagraphStyle(
        'Bullet', fontName='Helvetica', fontSize=9.5, leading=13.5,
        textColor=TEXT_DARK, alignment=TA_LEFT, spaceAfter=1.2*mm,
        leftIndent=8*mm, bulletIndent=3*mm
    )
    styles['bullet_gold'] = ParagraphStyle(
        'BulletGold', fontName='Helvetica', fontSize=9.5, leading=13.5,
        textColor=TEXT_DARK, alignment=TA_LEFT, spaceAfter=1.2*mm,
        leftIndent=8*mm, bulletIndent=3*mm,
        bulletColor=ACCENT, bulletFontName='Helvetica-Bold'
    )
    styles['caption'] = ParagraphStyle(
        'Caption', fontName='Helvetica-Oblique', fontSize=8, leading=10,
        textColor=TEXT_LIGHT, alignment=TA_LEFT, spaceAfter=3*mm
    )
    styles['toc_item'] = ParagraphStyle(
        'TOCItem', fontName='Helvetica', fontSize=10, leading=18,
        textColor=TEXT_DARK, alignment=TA_LEFT, leftIndent=4*mm
    )
    styles['toc_num'] = ParagraphStyle(
        'TOCNum', fontName='Helvetica-Bold', fontSize=10, leading=18,
        textColor=ACCENT, alignment=TA_LEFT
    )
    styles['disclaimer'] = ParagraphStyle(
        'Disclaimer', fontName='Helvetica', fontSize=7.5, leading=10,
        textColor=TEXT_LIGHT, alignment=TA_JUSTIFY
    )
    styles['footer'] = ParagraphStyle(
        'Footer', fontName='Helvetica', fontSize=7.5, leading=9,
        textColor=TEXT_LIGHT, alignment=TA_CENTER
    )
    styles['table_header'] = ParagraphStyle(
        'TableHeader', fontName='Helvetica-Bold', fontSize=8.5, leading=11,
        textColor=WHITE, alignment=TA_LEFT
    )
    styles['table_cell'] = ParagraphStyle(
        'TableCell', fontName='Helvetica', fontSize=8.5, leading=11,
        textColor=TEXT_DARK, alignment=TA_LEFT
    )
    styles['table_cell_bold'] = ParagraphStyle(
        'TableCellBold', fontName='Helvetica-Bold', fontSize=8.5, leading=11,
        textColor=TEXT_DARK, alignment=TA_LEFT
    )
    styles['kpi_value'] = ParagraphStyle(
        'KPIValue', fontName='Helvetica-Bold', fontSize=22, leading=26,
        textColor=NAVY, alignment=TA_CENTER
    )
    styles['kpi_label'] = ParagraphStyle(
        'KPILabel', fontName='Helvetica', fontSize=8, leading=10,
        textColor=TEXT_MID, alignment=TA_CENTER
    )
    return styles


# ─── Table Builder ───

def build_table(headers, rows, col_widths=None, stripe=True):
    """Build a styled executive table."""
    s = get_styles()

    header_cells = [Paragraph(h, s['table_header']) for h in headers]
    data = [header_cells]

    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            style = s['table_cell_bold'] if i == 0 else s['table_cell']
            cells.append(Paragraph(str(cell), style))
        data.append(cells)

    if col_widths is None:
        n = len(headers)
        col_widths = [CONTENT_WIDTH / n] * n

    t = Table(data, colWidths=col_widths, repeatRows=1)

    style_commands = [
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8.5),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 4*mm),
        ('TOPPADDING', (0, 0), (-1, 0), 3*mm),
        ('LEFTPADDING', (0, 0), (-1, -1), 3*mm),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3*mm),
        # Body
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8.5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 3*mm),
        ('TOPPADDING', (0, 1), (-1, -1), 2.5*mm),
        # Grid
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, ACCENT),
        ('LINEBELOW', (0, 1), (-1, -2), 0.3, BORDER),
        ('LINEBELOW', (0, -1), (-1, -1), 0.5, NAVY),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]

    if stripe:
        for i in range(1, len(data)):
            if i % 2 == 0:
                style_commands.append(('BACKGROUND', (0, i), (-1, i), BG_TABLE))
            else:
                style_commands.append(('BACKGROUND', (0, i), (-1, i), BG_TABLE2))

    t.setStyle(TableStyle(style_commands))
    return t


# ─── KPI Row ───

def build_kpi_row(kpis, width=CONTENT_WIDTH):
    """Build a row of KPI cards. kpis = [(value, label), ...]"""
    s = get_styles()
    n = len(kpis)
    card_w = (width - (n-1)*3*mm) / n

    cards = []
    for val, label in kpis:
        inner = Table(
            [[Paragraph(val, s['kpi_value'])],
             [Paragraph(label, s['kpi_label'])]],
            colWidths=[card_w],
            rowHeights=[12*mm, 6*mm]
        )
        inner.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BG_LIGHT),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (0, 0), 4*mm),
            ('BOTTOMPADDING', (0, -1), (0, -1), 3*mm),
            ('LINEBELOW', (0, 0), (0, 0), 1.5, ACCENT),
            ('BOX', (0, 0), (-1, -1), 0.3, BORDER),
            ('ROUNDEDCORNERS', [3, 3, 3, 3]),
        ]))
        cards.append(inner)

    row_data = [cards]
    widths = [card_w + (3*mm if i < n-1 else 0) for i in range(n)]
    row_table = Table(row_data, colWidths=widths)
    row_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return row_table


# ─── Page Templates ───

def cover_page(canvas, doc):
    """Draw the cover page background."""
    canvas.saveState()
    # Full navy background
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)

    # Gold accent bar at top
    canvas.setFillColor(ACCENT)
    canvas.rect(0, HEIGHT - 4*mm, WIDTH, 4*mm, fill=1, stroke=0)

    # Subtle geometric pattern (diagonal lines)
    canvas.setStrokeColor(HexColor('#0F2548'))
    canvas.setLineWidth(0.3)
    for i in range(0, int(WIDTH + HEIGHT), 20):
        canvas.line(i, HEIGHT, i - HEIGHT, 0)

    # Dark overlay on bottom half for text readability
    canvas.setFillColor(Color(0.04, 0.08, 0.16, alpha=0.7))
    canvas.rect(0, 0, WIDTH, HEIGHT * 0.55, fill=1, stroke=0)

    # Gold line separator
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(1.5)
    y_line = HEIGHT * 0.56
    canvas.line(LEFT_MARGIN, y_line, WIDTH - RIGHT_MARGIN, y_line)

    # Small gold diamond accent
    cx = LEFT_MARGIN + 12*mm
    cy = y_line
    canvas.setFillColor(ACCENT)
    p = canvas.beginPath()
    p.moveTo(cx, cy + 3*mm)
    p.lineTo(cx + 3*mm, cy)
    p.lineTo(cx, cy - 3*mm)
    p.lineTo(cx - 3*mm, cy)
    p.close()
    canvas.drawPath(p, fill=1, stroke=0)

    canvas.restoreState()


def content_page(canvas, doc):
    """Draw header/footer on content pages."""
    canvas.saveState()

    # Thin navy bar at top
    canvas.setFillColor(NAVY)
    canvas.rect(0, HEIGHT - 8*mm, WIDTH, 8*mm, fill=1, stroke=0)
    # Gold accent line under navy bar
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(0.75)
    canvas.line(0, HEIGHT - 8*mm, WIDTH, HEIGHT - 8*mm)

    # Header text
    canvas.setFillColor(LIGHT_GOLD)
    canvas.setFont('Helvetica', 7)
    canvas.drawString(LEFT_MARGIN, HEIGHT - 5.5*mm, 'CRYPTO HEDGE FUND LAUNCH PLAN 2026')
    canvas.drawRightString(WIDTH - RIGHT_MARGIN, HEIGHT - 5.5*mm, 'CONFIDENTIAL')

    # Footer
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.3)
    canvas.line(LEFT_MARGIN, 15*mm, WIDTH - RIGHT_MARGIN, 15*mm)

    canvas.setFillColor(TEXT_LIGHT)
    canvas.setFont('Helvetica', 7)
    canvas.drawString(LEFT_MARGIN, 10*mm, 'Jurisdiction Analysis, Strategy Framework & Operational Blueprint')
    canvas.drawRightString(WIDTH - RIGHT_MARGIN, 10*mm, f'Page {doc.page}')

    # Gold accent dot in footer
    canvas.setFillColor(ACCENT)
    canvas.circle(WIDTH/2, 10.5*mm, 1.2*mm, fill=1, stroke=0)

    canvas.restoreState()


# ─── Main Document Builder ───

def build_report():
    output_path = os.path.join(os.path.dirname(__file__),
                               'Crypto_Hedge_Fund_2026_Executive_Report.pdf')

    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN,
        topMargin=TOP_MARGIN + 10*mm,
        bottomMargin=BOTTOM_MARGIN,
        title='Crypto Hedge Fund Launch Plan 2026',
        author='Strategic Advisory',
        subject='Jurisdiction Analysis, Strategy Framework & Operational Blueprint',
    )

    # Page templates
    cover_frame = Frame(LEFT_MARGIN, BOTTOM_MARGIN, CONTENT_WIDTH,
                        HEIGHT - TOP_MARGIN - BOTTOM_MARGIN, id='cover')
    content_frame = Frame(LEFT_MARGIN, BOTTOM_MARGIN,
                          CONTENT_WIDTH, HEIGHT - TOP_MARGIN - BOTTOM_MARGIN - 10*mm,
                          id='content')

    doc.addPageTemplates([
        PageTemplate(id='Cover', frames=cover_frame, onPage=cover_page),
        PageTemplate(id='Content', frames=content_frame, onPage=content_page),
    ])

    s = get_styles()
    story = []

    # ═══════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════

    story.append(Spacer(1, HEIGHT * 0.28))
    story.append(Paragraph('CRYPTO HEDGE FUND', s['title']))
    story.append(Paragraph('LAUNCH PLAN 2026', s['title']))
    story.append(Spacer(1, 4*mm))
    story.append(GoldRule(80*mm, 1.5, ACCENT))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph('Jurisdiction Analysis, Strategy Framework<br/>&amp; Operational Blueprint', s['subtitle']))
    story.append(Spacer(1, 12*mm))

    cover_meta_style = ParagraphStyle(
        'CoverMeta', fontName='Helvetica', fontSize=9.5, leading=15,
        textColor=HexColor('#8899BB'), alignment=TA_LEFT
    )
    story.append(Paragraph('Version 2.0  |  Research-Verified  |  Expanded Edition', cover_meta_style))
    story.append(Paragraph(f'Date: {datetime.now().strftime("%B %d, %Y")}', cover_meta_style))
    story.append(Paragraph('Classification: CONFIDENTIAL', cover_meta_style))

    story.append(NextPageTemplate('Content'))
    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # TABLE OF CONTENTS
    # ═══════════════════════════════════════════

    story.append(Paragraph('TABLE OF CONTENTS', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 4*mm))

    toc_items = [
        ('01', 'Executive Summary'),
        ('02', 'UAE (Dubai - VARA)'),
        ('03', 'United Kingdom (FCA)'),
        ('04', 'Cayman Islands (CIMA)'),
        ('05', 'Switzerland (FINMA)'),
        ('06', 'Singapore (MAS)'),
        ('07', 'Jurisdiction Comparison Matrix'),
        ('08', 'Strategy Framework'),
        ('09', 'Backtesting Framework'),
        ('10', 'Institutional Infrastructure & Custody'),
        ('11', 'Risk Management Framework'),
        ('12', 'Counterparty Risk Management'),
        ('13', 'Tax Optimization Strategies'),
        ('14', 'Emerging Trends (2026+)'),
        ('15', 'Recommended Structure & Implementation'),
        ('16', 'Cost Analysis & Budget'),
        ('17', 'Risk Disclosure'),
    ]

    for num, title in toc_items:
        toc_row = Table(
            [[Paragraph(f'<font color="#{ACCENT.hexval()[2:]}">{num}</font>', s['toc_num']),
              Paragraph(title, s['toc_item'])]],
            colWidths=[12*mm, CONTENT_WIDTH - 12*mm]
        )
        toc_row.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1*mm),
            ('LINEBELOW', (0, 0), (-1, -1), 0.2, BORDER),
        ]))
        story.append(toc_row)

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 1. EXECUTIVE SUMMARY
    # ═══════════════════════════════════════════

    story.append(Paragraph('01 &nbsp; EXECUTIVE SUMMARY', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph(
        'This report provides a comprehensive, research-verified blueprint for launching a '
        'multi-strategy cryptocurrency hedge fund in 2026. It evaluates five jurisdictions across '
        'regulatory readiness, tax treatment, operational costs, and institutional credibility, '
        'while detailing strategy frameworks and operational guidance for institutional-grade fund management.',
        s['body']
    ))
    story.append(Spacer(1, 3*mm))

    # KPI Cards
    story.append(build_kpi_row([
        ('$4T+', 'Digital Asset Ecosystem'),
        ('900+', 'Crypto Hedge Funds'),
        ('$70-90B', 'Crypto HF AUM'),
        ('$250B', 'Bitcoin ETP AUM'),
    ]))
    story.append(Spacer(1, 5*mm))

    story.append(Paragraph('Fund Concept', s['subsection']))
    bullets = [
        'Multi-strategy cryptocurrency hedge fund with quantitative core',
        '5-10% gold/precious metals allocation for macro hedging',
        'Institutional-grade compliance, custody, and risk management',
        'Target: Sharpe > 1.2 (market-neutral book), Max Drawdown &lt; 25%',
    ]
    for b in bullets:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {b}', s['bullet_gold']))

    story.append(Spacer(1, 3*mm))
    story.append(CalloutBox(
        '<b>Primary Recommendation:</b> Cayman-domiciled fund with UAE-based operations. '
        'Singapore optional for Asia investor expansion. UK suited for long-term 2027+ '
        'regulatory integration strategy.',
        CONTENT_WIDTH, bg=HexColor('#EDE8D8'), border_color=ACCENT
    ))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Industry Context (2025-2026)', s['subsection']))
    story.append(Paragraph(
        'The crypto hedge fund industry has matured significantly post-2022. Bitcoin ETPs attracted '
        'over $36 billion in net inflows in 2024, with total AUM approaching $250 billion. Major '
        'traditional hedge funds including Millennium, Tudor, and D.E. Shaw have entered the space. '
        'The digital asset ecosystem now represents a $4 trillion market. However, the February 2026 '
        'Bitcoin drawdown nearing 40% demonstrates that significant volatility and risk remain inherent '
        'to the asset class.',
        s['body']
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 2. UAE (DUBAI - VARA)
    # ═══════════════════════════════════════════

    story.append(Paragraph('02 &nbsp; UAE (DUBAI - VARA)', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph(
        'VARA (Virtual Assets Regulatory Authority) is the world\'s first independent regulator '
        'specifically for virtual assets. It offers a crypto-native regulatory framework with '
        'eight distinct licensed activity categories. As of March 2026, 42 entities hold full VASP licences.',
        s['body']
    ))

    story.append(Paragraph('Licensing Process', s['subsection']))
    story.append(Paragraph(
        'VARA employs a two-stage licensing process, confirmed by the regulator:',
        s['body']
    ))
    story.append(Paragraph('<bullet>&bull;</bullet> <b>Stage 1 - Approval to Incorporate (ATI):</b> Submit IDQ, business plan, beneficial owners, pay initial fees. No VA activities permitted.', s['bullet']))
    story.append(Paragraph('<bullet>&bull;</bullet> <b>Stage 2 - Full VASP Licence:</b> Submit full documentation, interviews, pay remaining fees. May include operational conditions.', s['bullet']))
    story.append(Paragraph('<bullet>&bull;</bullet> <b>Realistic Timeline:</b> 6-12 months (industry estimate of 4-7 months is optimistic for new entrants).', s['bullet']))

    story.append(Paragraph('Capital Requirements (Corrected)', s['subsection']))
    story.append(Paragraph(
        '<i>Note: The original report stated AED 250,000+ for management. Research confirms the correct '
        'figure is AED 280,000 with a VARA-licensed custodian, or AED 500,000 without.</i>',
        s['caption']
    ))

    story.append(build_table(
        ['VA Activity', 'With VARA Custodian', 'Without Custodian'],
        [
            ['Advisory Services', 'AED 100,000', 'AED 100,000'],
            ['Broker-Dealer', 'AED 400,000', 'AED 600,000'],
            ['Custody Services', 'AED 600,000+', 'AED 600,000+'],
            ['Exchange Services', 'AED 800,000', 'AED 1,500,000'],
            ['Management & Investment', 'AED 280,000', 'AED 500,000'],
            ['Transfer & Settlement', 'AED 500,000+', 'AED 500,000+'],
        ],
        col_widths=[CONTENT_WIDTH*0.38, CONTENT_WIDTH*0.31, CONTENT_WIDTH*0.31]
    ))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Tax Treatment (Critical Correction)', s['subsection']))
    story.append(CalloutBox(
        '<b>Key Finding:</b> The UAE is <b>not</b> a 0% corporate tax jurisdiction. A 9% federal '
        'corporate tax applies to all businesses (effective June 2023). Free Zone entities may achieve '
        '0% on qualifying income only, subject to strict substance requirements. Personal income tax '
        'remains 0%. Fund management fees from non-Free Zone clients are likely taxable at 9%.',
        CONTENT_WIDTH, bg=HexColor('#FFF3E0'), border_color=HexColor('#E65100')
    ))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('Alternative Free Zones', s['subsection']))
    story.append(build_table(
        ['Feature', 'VARA (Dubai)', 'DIFC', 'ADGM (Abu Dhabi)'],
        [
            ['Framework', 'Purpose-built (2022)', 'Partial (2021)', 'Comprehensive (2018)'],
            ['Legal System', 'UAE civil law', 'English common law', 'English common law'],
            ['Licensed Crypto Firms', '42+', 'Limited', 'Growing'],
            ['Independent Courts', 'No', 'Yes', 'Yes'],
            ['Best For', 'Fast crypto launch', 'TradFi credibility', 'Institutional crypto'],
        ],
        col_widths=[CONTENT_WIDTH*0.22, CONTENT_WIDTH*0.26, CONTENT_WIDTH*0.26, CONTENT_WIDTH*0.26]
    ))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('Estimated Year-1 Budget: USD 490,000 - 845,000', s['body_bold']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 3. UNITED KINGDOM (FCA)
    # ═══════════════════════════════════════════

    story.append(Paragraph('03 &nbsp; UNITED KINGDOM (FCA)', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph(
        'The UK is building a comprehensive FSMA-based crypto regulatory framework, but the full regime '
        'does not commence until October 25, 2027. The legislative instrument (SI 2026/102) was made by '
        'Parliament on February 4, 2026. The FCA has published 12+ consultation and discussion papers.',
        s['body']
    ))

    story.append(Paragraph('Implementation Timeline', s['subsection']))
    story.append(build_table(
        ['Date', 'Milestone', 'Status'],
        [
            ['Oct 2023', 'Crypto financial promotions regime live', 'Active'],
            ['Jan 2026', 'CARF tax reporting takes effect', 'Active'],
            ['Feb 2026', 'SI 2026/102 made by Parliament', 'Complete'],
            ['Sep 30, 2026', 'Application gateway opens', 'Upcoming'],
            ['Feb 28, 2027', 'Application gateway closes', 'Upcoming'],
            ['Oct 25, 2027', 'Full FSMA crypto regime commences', 'Upcoming'],
        ],
        col_widths=[CONTENT_WIDTH*0.2, CONTENT_WIDTH*0.55, CONTENT_WIDTH*0.25]
    ))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Full Authorisation Requirements', s['subsection']))
    reqs = [
        '<b>Part 4A Permissions</b> - FSMA authorisation for each regulated activity',
        '<b>SM&amp;CR</b> - Senior Managers &amp; Certification Regime (CEO, Finance, Compliance, MLRO)',
        '<b>Consumer Duty</b> - Dedicated crypto guidance published (GC26/2, February 2026)',
        '<b>Prudential Standards</b> - Capital, liquidity, and wind-down requirements (CP25/42)',
        '<b>Market Abuse Regime</b> - Insider dealing, market manipulation provisions (CP25/41)',
    ]
    for r in reqs:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {r}', s['bullet']))

    story.append(Spacer(1, 2*mm))
    story.append(Paragraph('UK Tax Treatment', s['subsection']))
    story.append(build_table(
        ['Tax', 'Treatment'],
        [
            ['Corporation Tax', '25% on trading profits'],
            ['Capital Gains', 'Chargeable under corporation tax'],
            ['VAT', 'Crypto-fiat exchange generally exempt'],
            ['Stamp Duty', 'Cryptoassets NOT subject to SDRT'],
            ['Non-UK Investors', 'Generally not subject to UK tax on gains'],
        ],
        col_widths=[CONTENT_WIDTH*0.3, CONTENT_WIDTH*0.7]
    ))
    story.append(Spacer(1, 3*mm))

    story.append(CalloutBox(
        '<b>Brexit Impact:</b> The UK\'s post-Brexit position is a double-edged sword. Regulatory '
        'sovereignty enables bespoke crypto regulation, but the ~3-year gap behind EU MiCA (fully in '
        'force since December 2024) and loss of passporting create real competitive challenges.',
        CONTENT_WIDTH
    ))

    story.append(Spacer(1, 2*mm))
    story.append(Paragraph('Estimated Year-1 Cost: GBP 500,000 - 1,500,000+', s['body_bold']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 4. CAYMAN ISLANDS (CIMA)
    # ═══════════════════════════════════════════

    story.append(Paragraph('04 &nbsp; CAYMAN ISLANDS (CIMA)', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph(
        'The Cayman Islands is the undisputed global leader for hedge fund domiciliation, hosting an '
        'estimated 60-70% of the world\'s hedge fund assets. As of Q4 2025, CIMA regulates 30,598 '
        'total funds. The vast majority of institutional crypto hedge funds use Cayman-domiciled vehicles.',
        s['body']
    ))

    # KPI row
    story.append(build_kpi_row([
        ('30,598', 'Total Regulated Funds'),
        ('0%', 'Income / Corp / CG Tax'),
        ('3-6 Mo', 'Typical Setup Time'),
        ('$100-300K', 'Setup Cost'),
    ]))
    story.append(Spacer(1, 5*mm))

    story.append(Paragraph('Fund Registration Categories', s['subsection']))
    story.append(build_table(
        ['Category', 'Min. Investment', 'Key Feature'],
        [
            ['Registered Fund (Most Common)', 'US$100,000/investor', 'CIMA-registered admin; annual audit'],
            ['Administered Fund', 'None', 'Licensed Cayman administrator required'],
            ['Licensed Fund', 'None', 'CIMA approves operators; fit & proper'],
            ['Private Fund (closed-end)', 'N/A', '17,722 registered (Q4 2025)'],
            ['Limited Investor Fund', 'Max 15 investors', 'Simplified registration'],
        ],
        col_widths=[CONTENT_WIDTH*0.35, CONTENT_WIDTH*0.25, CONTENT_WIDTH*0.4]
    ))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('VASP Regime Update (April 2025)', s['subsection']))
    story.append(CalloutBox(
        '<b>Critical Development:</b> Full VASP licensing commenced April 1, 2025. Virtual asset '
        'custody and trading platform services now require a full VASP licence. However, a Cayman fund '
        'that merely <i>invests in</i> virtual assets is regulated under the Mutual Funds Act - NOT the '
        'VASP Act. The VASP Act targets entities providing VA services to others.',
        CONTENT_WIDTH, bg=HexColor('#E8F5E9'), border_color=GREEN
    ))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('Common Fund Structures', s['subsection']))
    structures = [
        '<b>Master-Feeder</b> (most common): Cayman Master + Offshore Feeder + US Delaware Feeder',
        '<b>Segregated Portfolio Company (SPC)</b>: Multi-strategy with ring-fenced portfolios',
        '<b>Standalone</b>: Simple exempted company for single-strategy funds',
        '<b>Limited Partnership (ELP)</b>: Common for venture/PE-style crypto funds',
    ]
    for st in structures:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {st}', s['bullet']))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        '<b>Key Challenge:</b> Banking remains the most significant practical obstacle. Account opening '
        'can take 3-6 months and is often the bottleneck in fund launch. Start banking discussions early.',
        s['body']
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 5. SWITZERLAND (FINMA)
    # ═══════════════════════════════════════════

    story.append(Paragraph('05 &nbsp; SWITZERLAND (FINMA)', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph(
        'Switzerland offers maximum institutional prestige through FINMA supervision and access to the '
        'deep Crypto Valley ecosystem. The CHF 1.74 trillion Swiss fund market grew 10% in 2025. Two '
        'FINMA-licensed crypto banks (Sygnum and AMINA) provide institutional-grade digital asset services.',
        s['body']
    ))

    story.append(Paragraph('Key Regulatory Developments', s['subsection']))
    devs = [
        '<b>L-QIF (March 2024)</b> - Limited Qualified Investor Fund: No FINMA product approval required; '
        'faster time-to-market; competitive with Luxembourg RAIF',
        '<b>DLT Act (August 2021)</b> - Legal certainty for tokenized securities; bankruptcy segregation '
        'for custodied crypto assets; DLT Trading Facility licence',
        '<b>Berne Financial Services Agreement (January 2026)</b> - Enhanced cross-border access with UK',
        '<b>Sygnum Bank</b> - Achieved unicorn status January 2025; >CHF 4.5B AuA',
        '<b>AMINA Bank</b> - First crypto banking group with EU MiCA licence (via Austrian subsidiary)',
    ]
    for d in devs:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {d}', s['bullet']))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('Tax Correction', s['subsection']))
    story.append(CalloutBox(
        '<b>Important Nuance:</b> The original report claimed "favorable capital gains treatment in many '
        'cantons." This applies only to <b>individual private investors</b> on personal holdings. '
        'Corporate entities and fund management companies pay corporate income tax (11.9% in Zug to '
        '19.7% in Zurich). Swiss withholding tax of 35% on fund distributions adds complexity.',
        CONTENT_WIDTH, bg=HexColor('#FFF3E0'), border_color=HexColor('#E65100')
    ))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('Canton-Level Corporate Tax Rates', s['subsection']))
    story.append(build_table(
        ['Canton', 'Effective Rate', 'Notes'],
        [
            ['Zug', '~11.9%', 'Lowest; Crypto Valley HQ; crypto-friendly'],
            ['Nidwalden', '~11.9%', 'Very competitive'],
            ['Lucerne', '~12.2%', 'Competitive; near Crypto Valley'],
            ['Geneva', '~13.9%', 'International finance hub'],
            ['Zurich', '~19.7%', 'Higher but deep talent/infrastructure'],
        ],
        col_widths=[CONTENT_WIDTH*0.2, CONTENT_WIDTH*0.2, CONTENT_WIDTH*0.6]
    ))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        '<b>Strategic Tip:</b> Swiss manager entity for prestige + Liechtenstein fund vehicle for '
        'EU distribution via AIFMD passporting is a common dual structure.',
        s['body']
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 6. SINGAPORE (MAS)
    # ═══════════════════════════════════════════

    story.append(Paragraph('06 &nbsp; SINGAPORE (MAS)', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph(
        'Singapore offers strong regulatory clarity through MAS, the world\'s #3 financial centre. '
        'The Variable Capital Company (VCC) structure and Section 13O/13U tax incentives make it a '
        'compelling choice for Asia-Pacific focused crypto funds. MAS manages approximately S$5.4 '
        'trillion in assets under management.',
        s['body']
    ))

    story.append(Paragraph('Licensing Framework', s['subsection']))
    story.append(build_table(
        ['License Type', 'Description', 'Min. Capital'],
        [
            ['LFMC (A/I)', 'Accredited/institutional investors only', 'S$250,000'],
            ['LFMC (Retail)', 'Can manage for retail investors', 'S$1,000,000'],
            ['RFMC', 'Max 30 investors, AUM &lt;S$250M', 'S$250,000'],
            ['MPI (if handling DPTs)', 'DPT services under PS Act', 'S$250,000'],
        ],
        col_widths=[CONTENT_WIDTH*0.25, CONTENT_WIDTH*0.45, CONTENT_WIDTH*0.3]
    ))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph('<i>Practical processing time for crypto fund managers: 9-18 months.</i>', s['caption']))

    story.append(Paragraph('Tax Incentive Schemes (Corrected)', s['subsection']))
    story.append(CalloutBox(
        '<b>Key Correction:</b> Singapore has no capital gains tax, but active trading profits of a '
        'crypto hedge fund are likely treated as taxable income at 17%. The real tax advantage comes '
        'from <b>Section 13O</b> (min S$10M AUM, 2 professionals, S$200K local spending) and '
        '<b>Section 13U</b> (min S$50M AUM, 3 professionals, S$500K local spending), which provide '
        '<b>full tax exemption</b> on qualifying fund income.',
        CONTENT_WIDTH, bg=HexColor('#E8F5E9'), border_color=GREEN
    ))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('VCC Structure Advantages', s['subsection']))
    vcc_items = [
        'Sub-fund segregation with legally ring-fenced assets and liabilities',
        'Variable capital - issue/redeem shares without shareholder approval',
        'Eligible for Section 13O/13U tax exemptions',
        'Register of members not publicly available (confidentiality)',
        'Foreign funds can re-domicile to Singapore as a VCC',
        'One umbrella VCC can house multiple strategies as separate sub-funds',
    ]
    for v in vcc_items:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {v}', s['bullet']))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('Estimated Year-1 Cost: S$500,000 - 1,200,000', s['body_bold']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 7. JURISDICTION COMPARISON
    # ═══════════════════════════════════════════

    story.append(Paragraph('07 &nbsp; JURISDICTION COMPARISON MATRIX', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    cw = CONTENT_WIDTH
    story.append(build_table(
        ['Factor', 'UAE', 'UK', 'Cayman', 'Switzerland', 'Singapore'],
        [
            ['Regulator', 'VARA', 'FCA', 'CIMA', 'FINMA', 'MAS'],
            ['Timeline', '6-12 mo', 'Oct 2027', '3-6 mo', '6-18 mo', '9-18 mo'],
            ['Personal Tax', '0%', 'Up to 45%', '0%', 'Varies', '0-22%'],
            ['Corporate Tax', '9% (0% FZ)', '25%', '0%', '11.9-19.7%', '17% (0% w/ 13O/U)'],
            ['Setup Cost', '$490-845K', 'GBP 500K-1.5M', '$100-300K', 'CHF 200-500K', 'S$500K-1.2M'],
            ['Annual Cost', '$300-600K', 'GBP 300-800K', '$110-350K', 'CHF 150-400K', 'S$450K-1.1M'],
            ['Banking', 'Challenging', 'Good', 'Challenging', 'Excellent', 'Good (licensed)'],
            ['Credibility', 'Growing', 'Very High', 'Highest', 'Very High', 'High'],
            ['Best For', 'Ops hub', '2027+ play', 'Fund domicile', 'Prestige', 'Asia gateway'],
        ],
        col_widths=[cw*0.16, cw*0.16, cw*0.16, cw*0.17, cw*0.18, cw*0.17]
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 8. STRATEGY FRAMEWORK
    # ═══════════════════════════════════════════

    story.append(Paragraph('08 &nbsp; STRATEGY FRAMEWORK', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Multi-Strategy Quantitative Model', s['subsection']))
    story.append(Paragraph(
        'The dominant institutional approach employs multiple uncorrelated alpha sources to '
        'achieve consistent risk-adjusted returns across market regimes.',
        s['body']
    ))

    story.append(build_table(
        ['Strategy', 'Description', 'Expected Return', 'Risk'],
        [
            ['Basis / Cash-and-Carry', 'Long spot, short perps; earn funding', '5-15% ann.', 'Low-Med'],
            ['Cross-Exchange Arb', 'Price differences across venues', '3-10% ann.', 'Low'],
            ['CeFi-DeFi Rate Arb', 'On-chain vs OTC rate differentials', '5-12% ann.', 'Medium'],
            ['Momentum / Trend', 'Systematic trend following', '15-40% ann.', 'High'],
            ['DeFi Yield', 'Staking, lending, LP provision', '3-12% ann.', 'Medium'],
            ['Event-Driven', 'Upgrades, airdrops, unlocks', 'Variable', 'Medium'],
            ['Funding Rate Harvest', 'Pure funding rate capture', '8-20% ann.', 'Low-Med'],
            ['MEV Capture', 'DEX arb, liquidations', 'Variable', 'High'],
        ],
        col_widths=[CONTENT_WIDTH*0.25, CONTENT_WIDTH*0.35, CONTENT_WIDTH*0.2, CONTENT_WIDTH*0.2]
    ))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Current DeFi Yield Landscape', s['subsection']))
    story.append(build_table(
        ['Source', 'Yield Range', 'Risk Level'],
        [
            ['Staked Stablecoins (sUSDe, sDAI)', '4-12%', 'Medium'],
            ['Native Staking - ETH', '~3.0-3.5%', 'Low'],
            ['Native Staking - SOL', '~7.3%', 'Low-Medium'],
            ['Lending (USDC on Aave)', '3-8%', 'Medium'],
            ['Tokenized Treasuries (BUIDL)', '4-5%', 'Low'],
            ['Liquid Staking (stETH, JitoSOL)', '3-7.5%', 'Medium'],
        ],
        col_widths=[CONTENT_WIDTH*0.45, CONTENT_WIDTH*0.25, CONTENT_WIDTH*0.3]
    ))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Performance Targets (Adjusted)', s['subsection']))
    story.append(CalloutBox(
        '<b>Sharpe Ratio:</b> Target > 1.2 for market-neutral book; > 0.8 for blended portfolio. '
        'Basis trade strategies have historically delivered Sharpe 1.5-3.0 in favorable environments. '
        'Bitcoin long-term Sharpe is ~0.8-1.0.<br/><br/>'
        '<b>Max Drawdown:</b> Target &lt; 25% under normal conditions. Requires >50% market-neutral '
        'allocation. Bitcoin drew down ~40% in 2025-26. Include CVaR as additional tail-risk metric.<br/><br/>'
        '<b>Gold Allocation (5-10%):</b> Reasonable hedge with ~0.1-0.2 long-term correlation to BTC. '
        'Consider alternatives: BTC puts, T-bills (~4-5%), VIX instruments.',
        CONTENT_WIDTH
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 9. BACKTESTING FRAMEWORK
    # ═══════════════════════════════════════════

    story.append(Paragraph('09 &nbsp; BACKTESTING FRAMEWORK', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(build_table(
        ['Phase', 'Requirements', 'Key Considerations'],
        [
            ['1. Rule Definition', 'Explicit entry/exit rules; position sizing; stop-losses',
             'No discretionary overrides; Kelly or fixed fractional sizing'],
            ['2. Data Sourcing', 'OHLCV/tick-level; exchange-specific; on-chain',
             'Kaiko, Coin Metrics, Glassnode, Amberdata, Dune'],
            ['3. Simulation', 'Slippage (10-50bps); fees; funding; gas costs',
             'Market impact; withdrawal delays; liquidation modeling'],
            ['4. Validation', 'Walk-forward; Monte Carlo; regime-aware',
             'Bootstrap resampling; cross-cycle validation'],
            ['5. Paper Trading', 'Minimum 1-3 months; real-time conditions',
             'Define explicit success criteria before going live'],
        ],
        col_widths=[CONTENT_WIDTH*0.2, CONTENT_WIDTH*0.4, CONTENT_WIDTH*0.4]
    ))

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('Key Data Providers', s['subsection']))
    story.append(build_table(
        ['Provider', 'Specialty'],
        [
            ['Kaiko', 'Institutional market data, order books, trade data (100+ exchanges)'],
            ['Coin Metrics', 'Network data, CMBI indices, reference rates'],
            ['Glassnode', 'On-chain analytics, UTXO analysis, network health'],
            ['CryptoCompare / CCData', 'Aggregated pricing, OHLCV data'],
            ['Amberdata', 'DeFi data, DEX analytics'],
            ['Dune Analytics', 'On-chain SQL queries, dashboards'],
            ['DefiLlama', 'DeFi TVL tracking, yield data'],
        ],
        col_widths=[CONTENT_WIDTH*0.3, CONTENT_WIDTH*0.7]
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 10. INSTITUTIONAL INFRASTRUCTURE
    # ═══════════════════════════════════════════

    story.append(Paragraph('10 &nbsp; INSTITUTIONAL INFRASTRUCTURE &amp; CUSTODY', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Custody Solutions', s['subsection']))
    story.append(build_table(
        ['Provider', 'Technology', 'Key Differentiator'],
        [
            ['Fireblocks', 'MPC-based', '2,000+ enterprise clients; market leader'],
            ['Copper', 'MPC + ClearLoop', 'Off-exchange settlement; ISO/SOC2'],
            ['BitGo', 'Multi-sig', 'Qualified custodian; insurance coverage'],
            ['Coinbase Prime', 'Integrated', 'Largest US-regulated; exchange + custody'],
            ['Galaxy / GK8', 'Proprietary', 'Full-service institution; prime brokerage'],
            ['Anchorage Digital', 'OCC chartered', 'Only federally chartered crypto bank'],
        ],
        col_widths=[CONTENT_WIDTH*0.22, CONTENT_WIDTH*0.2, CONTENT_WIDTH*0.58]
    ))

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('Prime Brokerage Services', s['subsection']))
    services = [
        '<b>Copper ClearLoop:</b> Trade on exchanges without moving assets from secure MPC custody',
        '<b>Galaxy One:</b> Margin financing, execution, custody, lending, derivatives',
        '<b>Fireblocks Network:</b> Settlement network connecting 2,000+ institutions',
        '<b>FalconX:</b> Prime brokerage, credit, and institutional trading',
        '<b>Traditional entrants:</b> Cantor Fitzgerald, Goldman Sachs, Morgan Stanley entering crypto',
    ]
    for sv in services:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {sv}', s['bullet']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 11. RISK MANAGEMENT
    # ═══════════════════════════════════════════

    story.append(Paragraph('11 &nbsp; RISK MANAGEMENT FRAMEWORK', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Core Risk Metrics', s['subsection']))
    story.append(build_table(
        ['Metric', 'Target', 'Rationale'],
        [
            ['Sharpe Ratio', '> 1.2 (MN); > 0.8 (blended)', 'Risk-adjusted return target'],
            ['Max Drawdown', '< 25% (normal)', 'Requires heavy hedging; stress may exceed'],
            ['VaR (99%, 1-day)', '< 3% of NAV', 'Value at Risk threshold'],
            ['CVaR (99%, 1-day)', '< 5% of NAV', 'Tail risk measure'],
            ['Gross Leverage', '< 3x', 'Position sizing discipline'],
            ['Net Exposure', '-20% to +40%', 'Directional bias limits'],
            ['Single Position', '< 10% of NAV', 'Concentration risk control'],
        ],
        col_widths=[CONTENT_WIDTH*0.25, CONTENT_WIDTH*0.3, CONTENT_WIDTH*0.45]
    ))

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('DeFi-Specific Risk Assessment (Galaxy SeC FiT PrO Framework)', s['subsection']))
    story.append(Paragraph(
        'Galaxy Digital published a comprehensive risk rating framework in August 2025 with six weighted '
        'domains for evaluating DeFi protocols. Composite scores range from 16 (highest risk) to 100 (lowest):',
        s['body']
    ))
    story.append(build_table(
        ['Domain', 'Weight', 'Key Assessment Areas'],
        [
            ['Security', '20%', 'Infrastructure, key management, smart contract audits'],
            ['Compliance', '15%', 'Legal entity identification, regulatory compatibility'],
            ['Financial', '15%', 'Transaction history, PnL reporting, maturity'],
            ['Technology', '15%', 'Availability, wallet infrastructure, MFA'],
            ['Protocol', '20%', 'Governance, incidents, leverage, oracles, team'],
            ['Operations', '15%', 'Whitelisting, monitoring, withdrawal limits'],
        ],
        col_widths=[CONTENT_WIDTH*0.18, CONTENT_WIDTH*0.12, CONTENT_WIDTH*0.7]
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 12. COUNTERPARTY RISK
    # ═══════════════════════════════════════════

    story.append(Paragraph('12 &nbsp; COUNTERPARTY RISK MANAGEMENT', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Lessons from the 2022 Collapse', s['subsection']))
    story.append(Paragraph(
        'The 2022 crypto lending market collapse destroyed the largest CeFi lenders (Genesis with a '
        '$14.6B loan book, Celsius, BlockFi, Voyager) - representing 40% of the entire crypto lending '
        'market. The CeFi lending market collapsed 82%. Root causes included poor asset-liability '
        'management, unsecured lending, toxic collateral acceptance, and inadequate risk controls.',
        s['body']
    ))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('Post-FTX Improvements', s['subsection']))
    improvements = [
        'Off-exchange settlement (Copper ClearLoop, Fireblocks) reduces exchange counterparty risk',
        'Overcollateralized lending becoming the industry standard',
        'DeFi lending share grew from 34% to 63% of total market (transparent, algorithmic risk)',
        'CeFi consolidation around better-managed entities (top 3 hold 89% market share)',
        'Crypto lending market recovered to $36.5B by Q4 2024',
    ]
    for imp in improvements:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {imp}', s['bullet']))

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph('Counterparty Risk Framework for New Fund', s['subsection']))
    story.append(build_table(
        ['Control', 'Standard'],
        [
            ['Exchange Exposure Limits', 'Max 20% of NAV on any single exchange'],
            ['Off-Exchange Settlement', 'Use ClearLoop/Fireblocks where available'],
            ['Custody Segregation', 'Assets in qualified custody, not on exchange'],
            ['Reconciliation', 'Daily verification across all venues'],
            ['Credit Assessment', 'Regular review of exchange/lender solvency'],
            ['Venue Diversification', 'Spread across 3-5+ exchanges'],
            ['Real-Time Monitoring', 'Automated alerts for unusual behavior'],
            ['Stress Testing', 'Model simultaneous failure of largest counterparty'],
        ],
        col_widths=[CONTENT_WIDTH*0.35, CONTENT_WIDTH*0.65]
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 13. TAX OPTIMIZATION
    # ═══════════════════════════════════════════

    story.append(Paragraph('13 &nbsp; TAX OPTIMIZATION STRATEGIES', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(build_table(
        ['Investor Type', 'Recommended Structure', 'Tax Benefit'],
        [
            ['US Taxable', 'Cayman Master + Delaware Feeder', 'Pass-through taxation'],
            ['US Tax-Exempt', 'Cayman Master + Offshore Feeder', 'UBTI blocker'],
            ['European (EU)', 'Cayman Fund + Liechtenstein RAIF', 'AIFMD passporting'],
            ['Asian Institutional', 'Cayman Fund + Singapore VCC', '13O/13U exemption'],
            ['Middle East', 'Cayman Fund + UAE ops entity', '0% personal; 0-9% corp'],
            ['Swiss', 'Cayman Fund + Swiss L-QIF', 'Tax-transparent structure'],
        ],
        col_widths=[CONTENT_WIDTH*0.25, CONTENT_WIDTH*0.4, CONTENT_WIDTH*0.35]
    ))

    story.append(Spacer(1, 4*mm))
    story.append(CalloutBox(
        '<b>Global Tax Reporting (CARF):</b> The OECD Crypto-Asset Reporting Framework is being '
        'implemented across multiple jurisdictions. The UK enacted CARF effective January 2026. '
        'UAE consulted on implementation in October 2025. Cayman implementation is expected. '
        'Professional tax structuring is essential as the landscape evolves rapidly.',
        CONTENT_WIDTH
    ))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 14. EMERGING TRENDS
    # ═══════════════════════════════════════════

    story.append(Paragraph('14 &nbsp; EMERGING TRENDS (2026+)', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Tokenized Funds &amp; On-Chain Structures', s['subsection']))
    story.append(Paragraph(
        'The tokenized Treasury market surged 545% to $5.6 billion by April 2025, with BlackRock\'s '
        'BUIDL commanding almost half the segment. Galaxy launched "Tokenized GLXY" on Solana in '
        'September 2025. The GENIUS Act (stablecoin legislation) was signed into law in July 2025.',
        s['body']
    ))

    story.append(Paragraph('AI/ML Integration', s['subsection']))
    story.append(Paragraph(
        'Galaxy published research on "Agentic Capital Markets" and "Agentic Payments" in early 2026. '
        'AI agents are being deployed for trade execution, risk monitoring, on-chain analytics, and '
        'sentiment analysis. ML models power price prediction, regime detection, anomaly detection in '
        'DeFi protocols, and MEV optimization.',
        s['body']
    ))

    story.append(Paragraph('Institutional Convergence', s['subsection']))
    convergence = [
        'Major hedge funds (Millennium, Tudor, D.E. Shaw) hold Bitcoin ETPs',
        'Cantor Fitzgerald launching Bitcoin financing business',
        'Banks entering crypto post-SAB-121 rescission',
        'Traditional prime brokers (Goldman, Morgan Stanley) offering crypto exposure',
        'DeFi lending now 63% of total crypto lending market (up from 34%)',
    ]
    for c in convergence:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {c}', s['bullet']))

    story.append(Paragraph('Regulatory Convergence', s['subsection']))
    regs = [
        'UK FSMA crypto regime launching October 2027',
        'EU MiCA fully in force since December 2024',
        'Global CARF implementation for tax reporting',
        'MAS stablecoin framework (first comprehensive globally)',
        'FATF continued monitoring of crypto AML/CFT compliance',
    ]
    for r in regs:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {r}', s['bullet']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 15. RECOMMENDED STRUCTURE & IMPLEMENTATION
    # ═══════════════════════════════════════════

    story.append(Paragraph('15 &nbsp; RECOMMENDED STRUCTURE &amp; IMPLEMENTATION', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Primary: Cayman + UAE', s['subsection']))
    story.append(build_table(
        ['Component', 'Jurisdiction', 'Entity Type', 'Purpose'],
        [
            ['Fund Vehicle', 'Cayman Islands', 'Registered Mutual Fund', 'Investor-facing; 0% tax; institutional credibility'],
            ['Operations', 'UAE (Dubai)', 'VARA VASP Licence', 'Investment management; execution; trading'],
            ['Custody', 'Multi-jurisdiction', 'Fireblocks / Copper', 'Institutional-grade MPC custody'],
        ],
        col_widths=[CONTENT_WIDTH*0.18, CONTENT_WIDTH*0.2, CONTENT_WIDTH*0.25, CONTENT_WIDTH*0.37]
    ))

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('Implementation Timeline', s['subsection']))
    story.append(build_table(
        ['Phase', 'Timeline', 'Key Activities'],
        [
            ['1. Foundation', 'Months 1-3', 'Engage legal counsel; submit VARA IDQ; draft fund docs; start banking'],
            ['2. Licensing', 'Months 3-6', 'VARA ATI; CIMA registration; setup office; onboard staff'],
            ['3. Operational', 'Months 6-9', 'Complete VASP licence; AML framework; paper trading (1-3 months)'],
            ['4. Launch', 'Months 9-12', 'First close; live trading; monitoring systems active'],
            ['5. Growth', 'Months 12-24', 'Second close; apply UK FCA (Sep 2026); evaluate Singapore'],
        ],
        col_widths=[CONTENT_WIDTH*0.18, CONTENT_WIDTH*0.18, CONTENT_WIDTH*0.64]
    ))

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('Optional Expansions', s['subsection']))
    expansions = [
        '<b>Singapore (Asia-Pacific):</b> CMS License + VCC structure + Section 13O/13U. Timeline: 9-18 months.',
        '<b>UK (2027+):</b> FSMA authorisation. Apply during gateway window Sep 30, 2026 - Feb 28, 2027.',
        '<b>Switzerland (Maximum Prestige):</b> FINMA licence + L-QIF. Optional Liechtenstein for EU passporting.',
    ]
    for ex in expansions:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {ex}', s['bullet']))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 16. COST ANALYSIS
    # ═══════════════════════════════════════════

    story.append(Paragraph('16 &nbsp; COST ANALYSIS &amp; BUDGET', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph('Year-1 Budget: Recommended Structure (Cayman + UAE)', s['subsection']))
    story.append(build_table(
        ['Item', 'Low Estimate (USD)', 'High Estimate (USD)'],
        [
            ['Cayman Fund Setup', '', ''],
            ['  Legal counsel', '$75,000', '$150,000'],
            ['  CIMA fees + registered office', '$15,000', '$25,000'],
            ['  Admin, directors, audit, AML', '$70,000', '$155,000'],
            ['UAE Operations', '', ''],
            ['  VARA licence + supervision', '$81,500', '$81,500'],
            ['  Free zone + office + capital', '$106,000', '$191,000'],
            ['  Legal + compliance + insurance', '$82,000', '$163,500'],
            ['  Staff (3-4 persons)', '$218,000', '$409,000'],
            ['Infrastructure', '', ''],
            ['  Custody + data + trading', '$70,000', '$205,000'],
            ['', '', ''],
            ['TOTAL YEAR-1', '$743,000', '$1,405,000'],
        ],
        col_widths=[CONTENT_WIDTH*0.44, CONTENT_WIDTH*0.28, CONTENT_WIDTH*0.28]
    ))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph('Ongoing Annual Costs', s['subsection']))
    story.append(build_kpi_row([
        ('$110-250K', 'Cayman Annual'),
        ('$350-600K', 'UAE Operations'),
        ('$60-150K', 'Infrastructure'),
        ('$520K-1M', 'Total Annual'),
    ]))

    story.append(PageBreak())

    # ═══════════════════════════════════════════
    # 17. RISK DISCLOSURE
    # ═══════════════════════════════════════════

    story.append(Paragraph('17 &nbsp; RISK DISCLOSURE', s['section']))
    story.append(SectionDivider(CONTENT_WIDTH))
    story.append(Spacer(1, 3*mm))

    story.append(Paragraph(
        'Cryptocurrency and hedge fund strategies involve significant risks. Prospective investors '
        'should carefully consider the following risk factors before making any investment decision:',
        s['body']
    ))
    story.append(Spacer(1, 2*mm))

    risks = [
        ('<b>Market Risk:</b> Extreme volatility inherent to digital assets. Bitcoin has experienced '
         'drawdowns of 40-83% from peak values.'),
        ('<b>Liquidity Risk:</b> Crypto markets can become illiquid during stress events. Validator '
         'exit queues, DEX pool imbalances, and exchange outages can prevent timely liquidation.'),
        ('<b>Counterparty Risk:</b> Exchange failures, custodian insolvency, and prime broker default '
         'are material risks, as demonstrated by FTX, Genesis, and Celsius in 2022.'),
        ('<b>Regulatory Risk:</b> Rapidly evolving regulations across multiple jurisdictions may '
         'adversely affect fund operations, strategy viability, or investor returns.'),
        ('<b>Technology Risk:</b> Smart contract exploits, oracle manipulation, MEV attacks, and '
         'infrastructure failures can result in partial or total loss of assets.'),
        ('<b>Operational Risk:</b> Key person dependency, system failures, and human error.'),
        ('<b>Tax Risk:</b> Changing tax treatment of cryptoassets across jurisdictions; CARF '
         'implementation; OECD Pillar Two implications.'),
        ('<b>Capital Loss:</b> Potential for total loss of invested capital.'),
    ]
    for risk in risks:
        story.append(Paragraph(f'<bullet>&bull;</bullet> {risk}', s['bullet']))

    story.append(Spacer(1, 6*mm))
    story.append(GoldRule(CONTENT_WIDTH, 0.75, ACCENT))
    story.append(Spacer(1, 4*mm))

    story.append(Paragraph(
        'IMPORTANT: Past performance is not indicative of future results. This document is for '
        'informational purposes only and does not constitute legal, tax, or investment advice. '
        'Professional legal, tax, and compliance advice from qualified advisors in each relevant '
        'jurisdiction is essential before proceeding with any fund launch.',
        s['disclaimer']
    ))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        'This report was compiled from primary regulatory sources including VARA (vara.ae), '
        'FCA (fca.org.uk), CIMA (cima.ky), FINMA (finma.ch), MAS (mas.gov.sg), and industry '
        'research from Galaxy Digital, Fireblocks, and Copper. All claims have been verified '
        'against official regulatory publications where available. See full source list in the '
        'companion reference document.',
        s['disclaimer']
    ))

    story.append(Spacer(1, 15*mm))

    # Final branding bar
    story.append(NavyBar(CONTENT_WIDTH, 10*mm, 'CRYPTO HEDGE FUND LAUNCH PLAN 2026  |  CONFIDENTIAL'))

    # ═══════════════════════════════════════════
    # BUILD
    # ═══════════════════════════════════════════

    doc.build(story)
    print(f'\nPDF generated successfully: {output_path}')
    print(f'File size: {os.path.getsize(output_path) / 1024:.0f} KB')


if __name__ == '__main__':
    build_report()
