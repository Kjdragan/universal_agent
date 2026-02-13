import os
import sys
import glob
import json
import re
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
    # 1. Gather Sections
    sections_dir = Path(work_dir) / "work_products" / "_working" / "sections"
    output_path = Path(work_dir) / "work_products" / "report.html"
    outline_path = Path(work_dir) / "work_products" / "_working" / "outline.json"
    
    md_files = []
    
    # Text-based identifier for fallback
    import re
    def natural_sort_key(path):
        return [int(text) if text.isdigit() else text.lower() 
                for text in re.split(r'(\d+)', path.name)]

    try:
        if not sections_dir.exists():
            sys.stderr.write(f"Error: Sections directory not found at {sections_dir}\n")
            sys.exit(1)

        available_files = {f.name: f for f in sections_dir.glob("*.md")}
        if not available_files:
            sys.stderr.write(f"Error: No .md files found in {sections_dir}\n")
            sys.exit(1)
            
        print(f"DEBUG: Found {len(available_files)} files in {sections_dir}")
        for f in available_files:
            print(f" - {f}")

        if outline_path.exists():
            import json
            try:
                print(f"Loading outline from {outline_path}")
                data = json.loads(outline_path.read_text())
                
                # --- STRICT ORDERING ALGORITHM ---
                processed_sections = set()
                
                for section in data.get("sections", []):
                    sec_id = section.get("id")
                    processed_sections.add(sec_id)
                    
                    # Check 1: Exact
                    fname_exact = f"{sec_id}.md"
                    if fname_exact in available_files:
                        md_files.append(available_files[fname_exact])
                        print(f"  [+] Added {sec_id} (Exact: {fname_exact})")
                        continue
                    
                    # Check 2: Numbered Prefix
                    match = None
                    for fname, fpath in available_files.items():
                        if fname.endswith(f"_{sec_id}.md"):
                            match = fpath
                            break
                    
                    if match:
                        md_files.append(match)
                        print(f"  [+] Added {sec_id} (Prefix: {match.name})")
                        continue
                        
                    sys.stderr.write(f"  [!] Warning: Section file for '{sec_id}' not found in {list(available_files.keys())}.\n")
                
            except Exception as e:
                sys.stderr.write(f"Error reading outline: {e}. Falling back to natural sort.\n")
                md_files = sorted(sections_dir.glob("*.md"), key=natural_sort_key)
        else:
            print("No outline found. Using natural filename sort.")
            md_files = sorted(sections_dir.glob("*.md"), key=natural_sort_key)

        if not md_files:
            sys.stderr.write("Error: No section files matched outline or glob.\n")
            sys.exit(1)

    except Exception as e:
        sys.stderr.write(f"Critical Error in compile_report: {e}\n")
        sys.exit(1)
        
    full_md = ""
    for md_file in md_files:
        # Append content
        content = md_file.read_text(encoding="utf-8")
        full_md += f"\n\n{content}\n"
        
    # 2. Convert to HTML
    html_content = markdown.markdown(full_md, extensions=['tables', 'fenced_code', 'toc'])

    # 2b. Inject images from manifest if available
    html_content = _inject_manifest_images(html_content, work_dir)
    
    # 3. Resolve CSS
    css = THEMES.get(theme_name, THEMES["modern"])
    css += _IMAGE_CSS
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

# =============================================================================
# IMAGE MANIFEST INJECTION
# =============================================================================
_IMAGE_CSS = """
    .report-image { max-width: 100%; height: auto; border-radius: 8px; margin: 20px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
    .report-image-container { text-align: center; margin: 24px 0; }
    .report-image-caption { font-size: 0.85em; color: #666; margin-top: 8px; font-style: italic; }
"""

def _inject_manifest_images(html_content: str, work_dir) -> str:
    """Inject images from media/manifest.json into HTML at matching section headings."""
    manifest_path = Path(work_dir) / "work_products" / "media" / "manifest.json"
    if not manifest_path.exists():
        return html_content

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        images = manifest.get("images", [])
        if not images:
            return html_content
        print(f"Image manifest: {len(images)} images found")
    except Exception as e:
        print(f"Warning: Could not read image manifest: {e}")
        return html_content

    # Build lookup: section_hint -> list of image entries
    hint_map: dict[str, list] = {}
    unmatched: list = []
    for img in images:
        hint = (img.get("section_hint") or "").strip().lower()
        if hint:
            hint_map.setdefault(hint, []).append(img)
        else:
            unmatched.append(img)

    # For each image with a section_hint, find the best matching <h2> and inject after it
    for hint, img_list in hint_map.items():
        img_html = _build_image_html(img_list, work_dir)
        # Try to match hint against h2 id or text content
        # markdown toc extension generates id attributes on headings
        # Try matching by id first, then by fuzzy text match
        pattern = re.compile(
            r'(<h2[^>]*>.*?</h2>)',
            re.IGNORECASE | re.DOTALL
        )
        best_match = None
        best_score = 0
        for m in pattern.finditer(html_content):
            heading_text = re.sub(r'<[^>]+>', '', m.group(1)).lower()
            heading_id = re.search(r'id="([^"]+)"', m.group(1))
            heading_id_val = heading_id.group(1).lower() if heading_id else ""
            # Score: exact id match > substring in id > substring in text
            score = 0
            hint_normalized = re.sub(r'[^a-z0-9]', '', hint)
            id_normalized = re.sub(r'[^a-z0-9]', '', heading_id_val)
            text_normalized = re.sub(r'[^a-z0-9]', '', heading_text)
            if hint_normalized == id_normalized:
                score = 100
            elif hint_normalized in id_normalized:
                score = 80
            elif hint_normalized in text_normalized:
                score = 60
            elif any(word in text_normalized for word in hint_normalized.split() if len(word) > 3):
                score = 40
            if score > best_score:
                best_score = score
                best_match = m

        if best_match and best_score >= 40:
            insert_pos = best_match.end()
            html_content = html_content[:insert_pos] + img_html + html_content[insert_pos:]
            print(f"  Injected {len(img_list)} image(s) at section matching '{hint}' (score={best_score})")
        else:
            unmatched.extend(img_list)

    # Inject unmatched images (including header images) at the top of the report
    if unmatched:
        header_imgs = [i for i in unmatched if (i.get("section_hint") or "").lower() in ("header", "hero", "banner", "")]
        body_imgs = [i for i in unmatched if i not in header_imgs]

        if header_imgs:
            header_html = _build_image_html(header_imgs, work_dir)
            # Insert after the metadata div
            meta_match = re.search(r'(</div>)', html_content)
            if meta_match:
                insert_pos = meta_match.end()
                html_content = html_content[:insert_pos] + header_html + html_content[insert_pos:]
                print(f"  Injected {len(header_imgs)} header image(s) at top")

        if body_imgs:
            body_html = _build_image_html(body_imgs, work_dir)
            html_content += body_html
            print(f"  Appended {len(body_imgs)} unmatched image(s) at end")

    return html_content


def _build_image_html(img_list: list, work_dir) -> str:
    """Build HTML img tags for a list of image manifest entries."""
    parts = []
    for img in img_list:
        img_path = img.get("path", "")
        # Resolve to absolute path for file:// embedding
        abs_path = Path(work_dir) / img_path
        if not abs_path.exists():
            # Try as already-absolute path
            abs_path = Path(img_path)
        if not abs_path.exists():
            print(f"  Warning: Image not found: {img_path}")
            continue
        alt = img.get("alt_text", "Report image")
        caption = img.get("purpose", alt)
        parts.append(
            f'\n<div class="report-image-container">'
            f'<img src="file:///{abs_path.resolve()}" alt="{alt}" class="report-image">'
            f'<div class="report-image-caption">{caption}</div>'
            f'</div>\n'
        )
    return "".join(parts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile report from sections")
    parser.add_argument("--work-dir", required=True, help="Workspace directory containing _working/sections")
    parser.add_argument("--theme", default="modern", help="Theme name (modern, financial, creative)")
    parser.add_argument("--custom-css", help="Custom CSS string to inject")
    
    args = parser.parse_args()
    
    compile_report(args.work_dir, args.theme, args.custom_css)
