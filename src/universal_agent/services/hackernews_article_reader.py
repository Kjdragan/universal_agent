"""Reader-mode article extractor for the HN dashboard's preview overlay.

Many HN-linked sites (github.com, zed.dev, arxiv.org, ...) refuse to be
embedded in an iframe via ``X-Frame-Options: DENY`` or
``Content-Security-Policy: frame-ancestors 'none'``. The browser shows a
broken-document icon and the user sees a blank gray panel.

This service fetches the article server-side and returns extracted
title / byline / lead-image / markdown body. The frontend renders that
markdown inside the existing modal, sidestepping the embed block
entirely. Same data shape we'd get from a "reader mode" extractor.

Pure I/O + bs4 + markdownify. No third-party API, no MCP roundtrip,
no quota cost. Failures are returned as a structured error payload so
the frontend can fall back to the iframe (for sites that DO embed) or
the "Open in new tab" affordance.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
import httpx
from markdownify import markdownify as html_to_md

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 12.0
MAX_BYTES = 4 * 1024 * 1024  # 4 MiB cap so a giant page can't blow memory
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 "
    "UniversalAgent-HN-Reader/1.0"
)

# Tags that almost never carry article content; strip before extraction.
_NOISE_TAGS = (
    "script", "style", "noscript", "iframe", "form", "svg", "canvas",
    "nav", "aside", "header", "footer", "button", "input", "select",
    "textarea", "menu", "dialog",
)

# Class/id substrings that almost always mark non-content blocks.
_NOISE_PATTERNS = re.compile(
    r"(comment|sidebar|share|social|advert|promo|newsletter|subscribe|"
    r"related|recommend|popular|trending|footer|header|nav|menu|cookie|"
    r"banner|gdpr|consent|paywall|signup|login)",
    re.IGNORECASE,
)


def fetch_article(url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Fetch ``url`` and return reader-mode payload.

    Returns one of:
      - On success: ``{"ok": True, "title", "byline", "host",
        "lead_image_url", "content_md", "fetched_at", "source_url",
        "content_length"}``
      - On failure: ``{"ok": False, "error": "<reason>", "host",
        "source_url"}`` (frontend falls back to iframe/new-tab)
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "ok": False,
            "error": "invalid_url",
            "host": "",
            "source_url": url,
        }
    host = parsed.netloc.removeprefix("www.")

    try:
        html_text = _fetch_html(url, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 — surface clean error payload
        logger.info("HN reader: fetch failed for %s: %s", url, exc)
        return {
            "ok": False,
            "error": f"fetch_failed: {type(exc).__name__}: {exc}",
            "host": host,
            "source_url": url,
        }

    if not html_text:
        return {
            "ok": False,
            "error": "empty_response",
            "host": host,
            "source_url": url,
        }

    soup = BeautifulSoup(html_text, "html.parser")
    title = _extract_title(soup)
    byline = _extract_byline(soup)
    lead_image_url = _extract_lead_image(soup, base_url=url)
    content_html = _extract_main_content_html(soup)
    content_md = _html_to_markdown(content_html) if content_html else ""

    return {
        "ok": True,
        "title": title,
        "byline": byline,
        "host": host,
        "lead_image_url": lead_image_url,
        "content_md": content_md,
        "content_length": len(content_md),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_url": url,
    }


# ─── HTTP fetch ────────────────────────────────────────────────────────


def _fetch_html(url: str, *, timeout_s: float) -> str:
    """GET the URL with a browser-like UA; honor MAX_BYTES; return decoded text."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(
        headers=headers,
        timeout=timeout_s,
        follow_redirects=True,
        max_redirects=5,
    ) as client:
        resp = client.get(url)
    resp.raise_for_status()
    content_type = (resp.headers.get("content-type") or "").lower()
    if "html" not in content_type and "xml" not in content_type:
        # PDF / binary / image — reader mode can't help here.
        raise ValueError(f"non-html content-type: {content_type or 'unknown'}")
    raw = resp.content
    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]
    encoding = resp.encoding or "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


# ─── metadata extraction ───────────────────────────────────────────────


def _meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    """Return the first non-empty meta-tag value matching any of the property/name keys."""
    for n in names:
        # property= (Open Graph)
        tag = soup.find("meta", attrs={"property": n})
        if tag and isinstance(tag, Tag):
            v = tag.get("content")
            if isinstance(v, str) and v.strip():
                return v.strip()
        # name= (Twitter, classic)
        tag = soup.find("meta", attrs={"name": n})
        if tag and isinstance(tag, Tag):
            v = tag.get("content")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _extract_title(soup: BeautifulSoup) -> str:
    candidate = _meta(soup, "og:title", "twitter:title")
    if candidate:
        return candidate
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        return title_tag.get_text(strip=True)
    return ""


def _extract_byline(soup: BeautifulSoup) -> str:
    return _meta(soup, "author", "article:author", "twitter:creator", "byline") or ""


def _extract_lead_image(soup: BeautifulSoup, *, base_url: str) -> str:
    candidate = _meta(soup, "og:image", "twitter:image", "twitter:image:src")
    if not candidate:
        return ""
    # Resolve protocol-relative and root-relative URLs against the page URL.
    if candidate.startswith("//"):
        scheme = urlparse(base_url).scheme or "https"
        return f"{scheme}:{candidate}"
    if candidate.startswith("/"):
        parsed = urlparse(base_url)
        return f"{parsed.scheme}://{parsed.netloc}{candidate}"
    return candidate


# ─── content extraction ────────────────────────────────────────────────


def _extract_main_content_html(soup: BeautifulSoup) -> str:
    """Pick the most-likely article container, strip noise, return inner HTML."""
    for noise in soup.find_all(_NOISE_TAGS):
        noise.decompose()

    container: Optional[Tag] = None
    for selector in ("article", "main", '[role="main"]'):
        node = soup.select_one(selector)
        if isinstance(node, Tag):
            container = node
            break
    if container is None:
        # Fallback — pick the <body>, then strip likely-noisy descendants.
        body = soup.find("body")
        if isinstance(body, Tag):
            container = body
    if container is None:
        return ""

    # Drop descendants whose class/id strongly matches noise patterns.
    for el in list(container.find_all(True)):
        if not isinstance(el, Tag) or not getattr(el, "attrs", None):
            continue
        try:
            cls = el.get("class") or ""
            eid = el.get("id") or ""
        except (AttributeError, TypeError):
            continue
        attr_text = " ".join(
            (v if isinstance(v, str) else " ".join(v))
            for v in (cls, eid)
            if v
        )
        if attr_text and _NOISE_PATTERNS.search(attr_text):
            el.decompose()

    return container.decode_contents()


def _html_to_markdown(html: str) -> str:
    """Convert extracted HTML to markdown. Best-effort; never raises."""
    if not html.strip():
        return ""
    try:
        md = html_to_md(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "noscript"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("HN reader: html→md conversion failed: %s", exc)
        return ""
    # Collapse runs of blank lines for readable output.
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md
