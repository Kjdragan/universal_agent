#!/usr/bin/env python3
"""
Convert HTML file to PDF using Google Chrome/Chromium headless mode.
Usage: python convert_html_to_pdf.py <input_html_path> <output_pdf_path>
"""

import sys
import subprocess
import os
from pathlib import Path

def convert_html_to_pdf(html_path, pdf_path=None):
    """
    Convert HTML file to PDF using Chrome headless mode.

    Args:
        html_path: Path to input HTML file
        pdf_path: Path to output PDF file (optional, defaults to same name with .pdf)

    Returns:
        Path to generated PDF file
    """
    # Convert to absolute paths
    html_path = os.path.abspath(html_path)

    # Default PDF path if not provided
    if pdf_path is None:
        pdf_path = str(Path(html_path).with_suffix('.pdf'))
    else:
        pdf_path = os.path.abspath(pdf_path)

    # Verify HTML file exists
    if not os.path.exists(html_path):
        raise FileNotFoundError(f"HTML file not found: {html_path}")

    # Chrome command for headless PDF generation
    chrome_cmd = [
        '/usr/bin/google-chrome',
        '--headless',
        '--disable-gpu',
        '--print-to-pdf=' + pdf_path,
        '--no-margins',
        '--virtual-time-budget=5000',
        'file://' + html_path
    ]

    print(f"Converting {html_path} to PDF...")
    print(f"Output: {pdf_path}")

    try:
        # Run Chrome headless
        result = subprocess.run(
            chrome_cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            print(f"Chrome stderr: {result.stderr}")
            raise RuntimeError("Chrome conversion failed")

        # Verify PDF was created
        if not os.path.exists(pdf_path):
            raise RuntimeError("PDF file was not created")

        file_size = os.path.getsize(pdf_path)
        print(f"✅ PDF created successfully: {pdf_path}")
        print(f"   Size: {file_size:,} bytes ({file_size/1024/1024:.2f} MB)")

        return pdf_path

    except subprocess.TimeoutExpired:
        raise RuntimeError("Chrome conversion timed out after 30 seconds")
    except Exception as e:
        raise RuntimeError(f"PDF conversion failed: {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_html_to_pdf.py <input_html_path> [output_pdf_path]")
        sys.exit(1)

    html_input = sys.argv[1]
    pdf_output = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        convert_html_to_pdf(html_input, pdf_output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
