#!/usr/bin/env python3
"""
Create PDF reports using reportlab
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.colors import HexColor
from pathlib import Path

def create_pdf_from_markdown(title, content, output_path):
    """Create a PDF from markdown-style content"""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=18
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=HexColor('#2c3e50'),
        spaceAfter=30,
        alignment=TA_CENTER,
    )

    heading2_style = ParagraphStyle(
        'CustomHeading2',
        parent=styles['Heading2'],
        fontSize=18,
        textColor=HexColor('#34495e'),
        spaceAfter=12,
    )

    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['BodyText'],
        fontSize=11,
        leading=16,
    )

    story = []
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 0.2*inch))

    # Parse content (simple line-based parsing)
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 0.1*inch))
        elif line.startswith('# '):
            story.append(Paragraph(line[2:], title_style))
        elif line.startswith('## '):
            story.append(Spacer(1, 0.15*inch))
            story.append(Paragraph(line[3:], heading2_style))
        else:
            # Convert basic markdown
            text = line
            text = text.replace('**', '<b>').replace('**', '</b>')
            text = text.replace('* ', '• ')
            story.append(Paragraph(text, body_style))

    doc.build(story)
    print(f"Created PDF: {output_path}")

def main():
    # This is a placeholder - actual content would be the HTML converted to markdown
    pass

if __name__ == "__main__":
    main()
