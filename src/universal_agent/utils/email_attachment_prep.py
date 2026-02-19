from __future__ import annotations

import html
import os
from pathlib import Path


_TRUTHY = {"1", "true", "yes", "on"}


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _render_markdown_to_html(markdown_text: str, title: str) -> str:
    """Render markdown to HTML with safe fallback when markdown package is absent."""
    body_html: str
    try:
        import markdown as md  # type: ignore

        body_html = md.markdown(
            markdown_text,
            extensions=["tables", "fenced_code", "toc"],
        )
    except Exception:
        escaped = html.escape(markdown_text, quote=False)
        body_html = f"<pre>{escaped}</pre>"

    safe_title = html.escape(title, quote=True)
    return (
        "<!DOCTYPE html>"
        "<html lang=\"en\">"
        "<head>"
        "<meta charset=\"UTF-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
        f"<title>{safe_title}</title>"
        "</head>"
        "<body>"
        f"{body_html}"
        "</body>"
        "</html>"
    )


def prepare_attachment_for_composio_upload(
    path: str,
    *,
    tool_slug: str,
    toolkit_slug: str,
) -> tuple[str, dict[str, str]]:
    """
    Prepare attachments before Composio upload.

    Current behavior:
    - For Gmail markdown attachments, generate and upload a rendered HTML sibling file.
      This avoids sending raw markdown that many clients display poorly.
    """
    source = Path(path).resolve()
    info: dict[str, str] = {}

    render_markdown = _env_truthy("UA_GMAIL_RENDER_MARKDOWN_ATTACHMENTS", default=True)
    is_gmail = toolkit_slug.strip().lower() == "gmail"
    is_gmail_send = tool_slug.strip().upper() == "GMAIL_SEND_EMAIL"
    is_markdown = source.suffix.lower() in {".md", ".markdown"}

    if render_markdown and is_gmail and is_gmail_send and is_markdown:
        markdown_text = source.read_text(encoding="utf-8", errors="replace")
        rendered_name = f"{source.stem}_rendered_for_email.html"
        rendered_path = source.with_name(rendered_name)
        rendered_html = _render_markdown_to_html(markdown_text, title=source.stem)
        rendered_path.write_text(rendered_html, encoding="utf-8")

        info["rendered_from_markdown"] = "true"
        info["original_local_path"] = str(source)
        info["rendered_local_path"] = str(rendered_path)
        return str(rendered_path), info

    return str(source), info
