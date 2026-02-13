from typing import Any
import os
import sys
import subprocess
from pathlib import Path
from claude_agent_sdk import tool

from universal_agent.hooks import StdoutToEventStream
from universal_agent.guardrails.workspace_guard import normalize_workspace_path


def _resolve_path(path_value: str) -> str:
    if not path_value:
        return path_value
    candidate = normalize_workspace_path(path_value, Path(os.getenv("CURRENT_SESSION_WORKSPACE") or "/"))
    if candidate.is_absolute():
        return str(candidate)
    workspace = os.getenv("CURRENT_SESSION_WORKSPACE")
    if workspace:
        return str(Path(workspace) / candidate)
    return str(candidate)


_PLAYWRIGHT_CHECKED = False


def _ensure_playwright_chromium() -> None:
    """Auto-install Playwright Chromium if missing. Runs once per process."""
    global _PLAYWRIGHT_CHECKED
    if _PLAYWRIGHT_CHECKED:
        return
    _PLAYWRIGHT_CHECKED = True

    cache_dir = Path.home() / ".cache" / "ms-playwright"
    if any(cache_dir.glob("chromium-*")) if cache_dir.exists() else False:
        return

    print("[pdf_bridge] Playwright Chromium not found — installing automatically...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            timeout=300,
            capture_output=True,
            text=True,
        )
        print("[pdf_bridge] Playwright Chromium installed successfully.")
    except Exception as e:
        print(f"[pdf_bridge] Warning: auto-install failed: {e}")


@tool(
    name="html_to_pdf",
    description=(
        "Convert an HTML file to PDF. Preferred path uses Chrome headless (Playwright). "
        "Falls back to WeasyPrint if Chromium is unavailable."
    ),
    input_schema={
        "html_path": str,
        "pdf_path": str,
    },
)
async def html_to_pdf_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    html_path = _resolve_path(args.get("html_path"))
    pdf_path = _resolve_path(args.get("pdf_path"))

    if not html_path or not pdf_path:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: html_path and pdf_path are required.",
                }
            ]
        }

    with StdoutToEventStream(prefix="[Local Toolkit]"):
        try:
            _ensure_playwright_chromium()

            # Preferred: Chrome headless via Playwright
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                await page.goto(f"file://{html_path}")
                await page.pdf(path=pdf_path, format="A4", print_background=True)
                await browser.close()
            result = f"PDF created (chrome headless): {pdf_path}"
        except Exception as playwright_error:
            # Fallback: WeasyPrint
            try:
                from weasyprint import HTML

                HTML(filename=html_path).write_pdf(pdf_path)
                result = (
                    f"PDF created (weasyprint fallback): {pdf_path} | "
                    f"playwright error: {playwright_error}"
                )
            except Exception as weasy_error:
                result = (
                    f"Error: failed to create PDF. "
                    f"playwright error: {playwright_error} | weasyprint error: {weasy_error}"
                )

    return {"content": [{"type": "text", "text": result}]}
