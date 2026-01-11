#!/usr/bin/env python3
"""
Convert all HTML reports to PDF using weasyprint
"""
from pathlib import Path
from weasyprint import HTML, CSS

# Work products directory
work_dir = Path("/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260107_123725/work_products")

# Find all HTML files
html_files = sorted(work_dir.glob("topic_*.html"))

print(f"Found {len(html_files)} HTML files to convert")

for html_file in html_files:
    # Generate PDF filename
    pdf_file = html_file.with_suffix('.pdf')

    # Convert HTML to PDF
    try:
        HTML(filename=str(html_file)).write_pdf(str(pdf_file))
        print(f"✓ Converted: {html_file.name} -> {pdf_file.name}")
    except Exception as e:
        print(f"✗ Error converting {html_file.name}: {e}")

print(f"\n✅ Conversion complete!")
