from typing import Any, Optional
import os
import json
import asyncio
from pathlib import Path
from claude_agent_sdk import tool

from universal_agent.hooks import StdoutToEventStream
from universal_agent.tools.pdf_bridge import _ensure_playwright_chromium, _resolve_path

MERMAID_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
        mermaid.initialize({ startOnLoad: true, theme: 'default' });
    </script>
    <style>
        body { margin: 0; padding: 20px; background: white; }
        #graphDiv { display: inline-block; }
    </style>
</head>
<body>
    <div class="mermaid" id="graphDiv">
        TEMPLATE_CONTENT
    </div>
</body>
</html>
"""

async def _mermaid_to_image_impl(args: dict[str, Any]) -> dict[str, Any]:
    mermaid_code = args.get("mermaid_code")
    input_path = args.get("input_path")
    output_path = args.get("output_path")
    fmt = args.get("format", "png").lower()

    if not output_path:
        return {"content": [{"type": "text", "text": "Error: output_path is required."}]}
        
    output_resolved = _resolve_path(output_path)

    # Resolve inputs: prefer code, then file
    if not mermaid_code and input_path:
        input_resolved = _resolve_path(input_path)
        try:
            with open(input_resolved, "r") as f:
                mermaid_code = f.read()
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error reading input file: {e}"}]}
            
    if not mermaid_code:
        return {"content": [{"type": "text", "text": "Error: mermaid_code or input_path must be provided."}]}

    # Clean code: remove markdown fences if present
    mermaid_code = mermaid_code.strip()
    if mermaid_code.startswith("```"):
         lines = mermaid_code.split("\n")
         if len(lines) > 2:
             mermaid_code = "\n".join(lines[1:-1])
         else:
             mermaid_code = lines[1] if len(lines) > 1 else ""

    # Prepare HTML
    html_content = MERMAID_HTML_TEMPLATE.replace("TEMPLATE_CONTENT", mermaid_code)
    
    result_msg = ""
    # Use existing hook context or just run
    try:
        _ensure_playwright_chromium()
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html_content)
            
            # Wait for mermaid to render
            try:
                # Wait for the SVG to be generated inside the div
                await page.wait_for_selector("#graphDiv svg", timeout=5000)
            except Exception:
                    await browser.close()
                    return {"content": [{"type": "text", "text": "Error: Mermaid syntax error or timeout."}]}
            
            element = await page.query_selector("#graphDiv")
            
            if fmt == "pdf":
                await page.pdf(path=output_resolved, format="A4", print_background=True)
            else:
                await element.screenshot(path=output_resolved)
                
            await browser.close()
            result_msg = f"Mermaid diagram saved to {output_resolved}"
            
    except Exception as e:
        result_msg = f"Error rendering mermaid: {e}"

    return {"content": [{"type": "text", "text": result_msg}]}

@tool(
    name="mermaid_to_image",
    description="Convert Mermaid diagram code (or file) to an image (PNG) or PDF.",
    input_schema={
        "mermaid_code": str,
        "input_path": str,
        "output_path": str,
        "format": str,  # "png" or "pdf"
    },
)
async def mermaid_to_image(args: dict[str, Any]) -> dict[str, Any]:
    return await _mermaid_to_image_impl(args)
