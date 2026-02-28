"""
Convert a Scrapling Response page into a Markdown document.

Primary mode is content-focused output to minimize navigation/header/footer
noise in the final markdown.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse


def _clean(text: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", text or "").strip()


def _get_text(el: Any, selector: str) -> str:
    """Safe CSS text extraction from a page element."""
    try:
        result = el.css_first(f"{selector}::text")
        if result:
            return _clean(str(result))
    except Exception:
        pass
    return ""


def _get_all_text(el: Any, selector: str) -> list[str]:
    """Safe CSS multi-text extraction."""
    try:
        results = el.css(f"{selector}::text")
        return [_clean(str(r)) for r in results if _clean(str(r))]
    except Exception:
        return []


def _get_body_text(page: Any) -> str:
    """
    Best-effort extraction of the main body text.

    Tries common article/main content selectors, then falls back to
    the full page text.
    """
    content_selectors = [
        "article",
        "main",
        '[role="main"]',
        ".post-content",
        ".entry-content",
        ".article-body",
        ".content",
        "#content",
        "#main",
        "body",
    ]
    for sel in content_selectors:
        try:
            el = page.css_first(sel)
            if el:
                text = el.get_all_text(strip=True, separator="\n")
                if text and len(text) > 200:
                    return text
        except Exception:
            continue
    # Last-resort: full page text
    try:
        return page.get_all_text(strip=True, separator="\n")
    except Exception:
        return ""


_BOILERPLATE_PATTERNS = (
    r"^\s*(home|menu|navigation|main menu)\s*$",
    r"^\s*(sign in|log in|register|subscribe|newsletter)\s*$",
    r"^\s*(privacy policy|terms(?: of (?:use|service))?|cookie(?:s| policy)?)\s*$",
    r"^\s*(accept all|reject all|manage preferences)\s*$",
    r"^\s*(share|follow us|follow|advertisement|sponsored)\s*$",
    r"^\s*(skip to content|back to top)\s*$",
)
_BOILERPLATE_RE = re.compile("|".join(_BOILERPLATE_PATTERNS), re.IGNORECASE)


def _looks_like_boilerplate_line(line: str) -> bool:
    """Heuristic filter for nav/footer/cookie/social chrome."""
    if not line:
        return True
    if _BOILERPLATE_RE.match(line):
        return True
    if line.count("|") >= 3:
        return True
    words = line.split()
    if 1 <= len(words) <= 2:
        lowered = line.lower()
        if any(k in lowered for k in ("login", "subscribe", "cookie", "privacy", "terms")):
            return True
    return False


def _looks_like_content_line(line: str) -> bool:
    """Heuristic classifier for article-like text vs. navigation labels."""
    words = line.split()
    word_count = len(words)
    if word_count == 0:
        return False
    if len(line) >= 120:
        return True
    if word_count >= 10:
        return True
    if word_count >= 6 and re.search(r"[.!?:;]$", line):
        return True
    if re.search(r"\d", line) and word_count >= 5:
        return True
    # Menu-like single token lines are usually boilerplate.
    if word_count <= 4 and not re.search(r"[.!?:;]$", line):
        return False
    return word_count >= 5


def _clean_body_text(text: str) -> str:
    """Remove repeated and boilerplate lines while preserving paragraph flow."""
    if not text:
        return ""

    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    cleaned: list[str] = []
    seen_counts: dict[str, int] = {}
    kept_any = False
    for line in lines:
        if not line:
            if cleaned and cleaned[-1]:
                cleaned.append("")
            continue
        if _looks_like_boilerplate_line(line):
            continue
        seen_counts[line] = seen_counts.get(line, 0) + 1
        # Keep first occurrence for heavily duplicated UI strings.
        if seen_counts[line] > 1 and len(line) < 80:
            continue
        if kept_any and not _looks_like_content_line(line):
            continue
        kept_any = True
        cleaned.append(line)

    # Collapse repeated blank lines.
    compact: list[str] = []
    for ln in cleaned:
        if ln == "" and compact and compact[-1] == "":
            continue
        compact.append(ln)

    return "\n".join(compact).strip()


def _extract_links(page: Any, base_url: str) -> list[tuple[str, str]]:
    """Return list of (text, href) pairs for all <a> tags with non-empty href."""
    links: list[tuple[str, str]] = []
    try:
        anchors = page.css("a")
        for a in anchors:
            try:
                href = a.attrib.get("href", "").strip()
                if not href or href.startswith(("javascript:", "mailto:", "#")):
                    continue
                if not href.startswith(("http://", "https://")):
                    href = urljoin(base_url, href)
                text = _clean(a.get_all_text(strip=True))
                if not text:
                    text = href
                links.append((text, href))
            except Exception:
                continue
    except Exception:
        pass
    return links


def _extract_headings(page: Any) -> list[tuple[int, str]]:
    """Return list of (level, text) for all h1–h3 headings."""
    headings: list[tuple[int, str]] = []
    for level in range(1, 4):
        try:
            for el in page.css(f"h{level}"):
                text = _clean(el.get_all_text(strip=True))
                if text:
                    headings.append((level, text))
        except Exception:
            continue
    return headings


def page_to_markdown(
    page: Any,
    url: str,
    fetcher_level: str = "unknown",
    job_metadata: dict | None = None,
    clean_markdown: bool = True,
    include_structure: bool = False,
    include_links: bool = False,
) -> str:
    """
    Convert a Scrapling Response *page* to a Markdown string.

    Args:
        page: Scrapling Response object.
        url: The original URL that was fetched.
        fetcher_level: Name of the fetcher tier used (for metadata block).
        job_metadata: Optional dict of extra metadata from the JSON job file.

    Returns:
        A Markdown-formatted string.
    """
    scraped_at = datetime.now(timezone.utc).isoformat()
    domain = urlparse(url).netloc

    # --- Title ---
    title = (
        _get_text(page, "title")
        or _get_text(page, "h1")
        or domain
        or "Untitled"
    )

    # --- Status ---
    status_code = getattr(page, "status", None)
    status_str = f"{status_code}" if status_code else "unknown"

    # --- Meta description ---
    meta_desc = ""
    try:
        meta_el = page.css_first('meta[name="description"]')
        if meta_el:
            meta_desc = meta_el.attrib.get("content", "").strip()
    except Exception:
        pass

    # --- Headings ---
    headings = _extract_headings(page)

    # --- Body text ---
    body_text_raw = _get_body_text(page)
    body_text = _clean_body_text(body_text_raw) if clean_markdown else body_text_raw

    # --- Links ---
    links = _extract_links(page, url)

    # --- Build Markdown ---
    lines: list[str] = []

    lines.append(f"# {title}")
    lines.append("")
    lines.append("## Metadata")
    lines.append("")
    lines.append(f"- **URL**: {url}")
    lines.append(f"- **Domain**: {domain}")
    lines.append(f"- **Scraped at**: {scraped_at}")
    lines.append(f"- **HTTP Status**: {status_str}")
    lines.append(f"- **Fetcher tier**: {fetcher_level}")
    if meta_desc:
        lines.append(f"- **Description**: {meta_desc}")
    if job_metadata:
        for k, v in job_metadata.items():
            if k not in ("urls", "options"):
                lines.append(f"- **{k}**: {v}")
    lines.append("")

    if include_structure and headings:
        lines.append("## Page Structure")
        lines.append("")
        for level, text in headings:
            indent = "  " * (level - 1)
            lines.append(f"{indent}- {'#' * level} {text}")
        lines.append("")

    if body_text:
        lines.append("## Content")
        lines.append("")
        # Wrap body text: preserve paragraph breaks, truncate at 50k chars
        body_trimmed = body_text[:50_000]
        if len(body_text) > 50_000:
            body_trimmed += "\n\n*[Content truncated at 50,000 characters]*"
        lines.append(body_trimmed)
        lines.append("")

    if include_links and links:
        lines.append("## Links")
        lines.append("")
        # De-duplicate while preserving order
        seen: set[str] = set()
        for text, href in links:
            if href not in seen:
                seen.add(href)
                safe_text = text.replace("[", r"\[").replace("]", r"\]")
                lines.append(f"- [{safe_text}]({href})")
        lines.append("")

    return "\n".join(lines)
