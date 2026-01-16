import os
import sys
import glob
import markdown
import argparse
from pathlib import Path
from datetime import datetime

# =============================================================================
# THEME DEFINITIONS
# =============================================================================
THEMES = {
    "modern": """
        body { font-family: 'Inter', sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; }
        h1 { color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        h2 { color: #34495e; margin-top: 30px; }
        blockquote { border-left: 4px solid #3498db; padding-left: 15px; color: #555; }
        .metadata { color: #7f8c8d; font-size: 0.9em; margin-bottom: 30px; }
    """,
    "financial": """
        body { font-family: 'Georgia', serif; line-height: 1.5; color: #111; max-width: 800px; margin: 0 auto; padding: 40px; background-color: #fcfcfc; }
        h1 { font-family: 'Arial', sans-serif; color: #003366; text-transform: uppercase; letter-spacing: 1px; border-bottom: 3px solid #003366; }
        h2 { color: #003366; border-bottom: 1px solid #ccc; padding-bottom: 5px; }
        p { text-align: justify; }
        table { border-collapse: collapse; width: 100%; margin: 20px 0; font-family: 'Arial', sans-serif; font-size: 0.9em; }
        th { background-color: #003366; color: white; padding: 8px; text-align: left; }
        td { border: 1px solid #ddd; padding: 8px; }
        tr:nth-child(even) { background-color: #f2f2f2; }
    """,
    "creative": """
        body { font-family: 'Helvetica Neue', sans-serif; line-height: 1.7; color: #444; max-width: 900px; margin: 0 auto; padding: 20px; background: #fff; }
        h1 { font-weight: 300; font-size: 3em; color: #222; text-align: center; margin-bottom: 50px; }
        h2 { font-weight: 600; color: #e74c3c; margin-top: 40px; }
        img { max-width: 100%; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        a { color: #e74c3c; text-decoration: none; }
    """
}

def compile_report(work_dir, theme_name="modern", custom_css=None):
    """
    Compile markdown sections into a single HTML report.
    """
    sections_dir = Path(work_dir) / "work_products" / "_working" / "sections"
    output_path = Path(work_dir) / "work_products" / "report.html"
    
    if not sections_dir.exists():
        print(f"Error: Sections directory not found at {sections_dir}")
        sys.exit(1)
        
    # 1. Gather Sections
    md_files = sorted(sections_dir.glob("*.md"))
    if not md_files:
        print("Error: No section files found.")
        sys.exit(1)
        
    full_md = ""
    for md_file in md_files:
        # Extract title from filename (e.g. "1_Executive_Summary.md" -> "Executive Summary")
        # Assuming filenames might be sorted like 1.md or section_1.md
        # For simplicity, we just concatenate content
        content = md_file.read_text(encoding="utf-8")
        full_md += f"\n\n{content}\n"
        
    # 2. Convert to HTML
    html_content = markdown.markdown(full_md, extensions=['tables', 'fenced_code', 'toc'])
    
    # 3. Resolve CSS
    css = THEMES.get(theme_name, THEMES["modern"])
    if custom_css:
        css += f"\n/* Custom Overrides */\n{custom_css}"
        
    # 4. Construct Full HTML
    report_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Research Report</title>
    <style>
    {css}
    </style>
</head>
<body>
    <div class="report-content">
        <div class="metadata">
            Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}
        </div>
        {html_content}
    </div>
</body>
</html>"""

    output_path.write_text(report_html, encoding="utf-8")
    print(f"Success: Report generated at {output_path}")
    return str(output_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile report from sections")
    parser.add_argument("--work-dir", required=True, help="Workspace directory containing _working/sections")
    parser.add_argument("--theme", default="modern", help="Theme name (modern, financial, creative)")
    parser.add_argument("--custom-css", help="Custom CSS string to inject")
    
    args = parser.parse_args()
    
    compile_report(args.work_dir, args.theme, args.custom_css)
