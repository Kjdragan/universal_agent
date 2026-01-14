#!/usr/bin/env python3
"""Convert HTML report to PDF using Google Chrome headless"""

import subprocess
import sys
import os

# File paths
html_file = "/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260114_110139/work_products/russia_ukraine_war_report_jan2026.html"
pdf_file = "/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260114_110139/work_products/russia_ukraine_war_report_jan2026.pdf"

# Convert HTML to PDF using Google Chrome headless
try:
    result = subprocess.run([
        "google-chrome",
        "--headless",
        "--disable-gpu",
        "--print-to-pdf=" + pdf_file,
        "--no-margins",
        "--virtual-time-budget=5000",
        "file://" + html_file
    ], check=True, capture_output=True, text=True)

    if os.path.exists(pdf_file):
        print(f"✓ PDF successfully created: {pdf_file}")
        print(f"  File size: {os.path.getsize(pdf_file)} bytes")
    else:
        print("✗ PDF file was not created")
        sys.exit(1)

except subprocess.CalledProcessError as e:
    print(f"✗ Error converting HTML to PDF: {e}")
    print(f"  stderr: {e.stderr}")
    sys.exit(1)
except FileNotFoundError:
    print("✗ google-chrome command not found. Please install Google Chrome.")
    sys.exit(1)
