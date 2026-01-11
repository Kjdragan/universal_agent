#!/usr/bin/env python3
"""
Convert HTML reports to PDF using weasyprint
"""
import sys
from pathlib import Path

try:
    from weasyprint import HTML
except ImportError:
    print("weasyprint not available, using alternative approach")
    sys.exit(1)

def convert_html_to_pdf(html_path, pdf_path):
    """Convert an HTML file to PDF"""
    try:
        HTML(filename=html_path).write_pdf(pdf_path)
        print(f"✓ Converted: {html_path} -> {pdf_path}")
        return True
    except Exception as e:
        print(f"✗ Error converting {html_path}: {e}")
        return False

def main():
    workspace = Path("/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260107_153932/work_products")

    reports = [
        ("quantum_computing_report.html", "quantum_computing_report.pdf"),
        ("ai_report.html", "ai_report.pdf"),
        ("ev_report.html", "ev_report.pdf"),
    ]

    print("Converting HTML reports to PDF...\n")

    success_count = 0
    for html_file, pdf_file in reports:
        html_path = workspace / html_file
        pdf_path = workspace / pdf_file

        if html_path.exists():
            if convert_html_to_pdf(html_path, pdf_path):
                success_count += 1
        else:
            print(f"✗ HTML file not found: {html_path}")

    print(f"\n{success_count}/{len(reports)} reports converted successfully")
    return 0 if success_count == len(reports) else 1

if __name__ == "__main__":
    sys.exit(main())
