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
            "iframe_blocked_by_headers": False,
        }
    host = parsed.netloc.removeprefix("www.")

    try:
        html_text, response_headers = _fetch_html(url, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 — surface clean error payload
        logger.info("HN reader: fetch failed for %s: %s", url, exc)
        return {
            "ok": False,
            "error": f"fetch_failed: {type(exc).__name__}: {exc}",
            "host": host,
            "source_url": url,
            # We don't know the headers (fetch failed) — but in the operator
            # UX, "fetch failed" already means "iframe also probably won't
            # work" because most fetch failures are 4xx/5xx that the iframe
            # would also surface. Mark as blocked so the UI doesn't auto-swap
            # to a likely-blank iframe.
            "iframe_blocked_by_headers": True,
        }

    if not html_text:
        return {
            "ok": False,
            "error": "empty_response",
            "host": host,
            "source_url": url,
            "iframe_blocked_by_headers": _is_iframe_blocked(response_headers),
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
        # If the upstream sets X-Frame-Options or a restrictive
        # Content-Security-Policy frame-ancestors directive, the operator's
        # browser will refuse to embed the URL in our preview iframe. The
        # frontend uses this flag to decide whether to auto-swap to
        # "Original" (iframe) when reader extraction returns empty/error
        # content, or to instead show the friendly "Open in new tab"
        # error panel. Catches the twitter.com / x.com / github.com case
        # where bs4 returns a noscript stub (empty content_md) and the
        # iframe would also blank out from XFO/CSP — without this flag
        # we'd silently swap to a blank iframe.
        "iframe_blocked_by_headers": _is_iframe_blocked(response_headers),
    }


# ─── HTTP fetch ────────────────────────────────────────────────────────


def _fetch_html(url: str, *, timeout_s: float) -> tuple[str, dict[str, str]]:
    """GET the URL with a browser-like UA; honor MAX_BYTES.

    Returns (decoded_html, response_headers_dict). The headers are returned
    separately so the caller can inspect anti-embedding signals like
    X-Frame-Options and Content-Security-Policy without re-fetching.
    """
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
    # Lower-case header keys for case-insensitive lookup downstream
    headers_dict = {k.lower(): v for k, v in resp.headers.items()}
    try:
        return raw.decode(encoding, errors="replace"), headers_dict
    except LookupError:
        return raw.decode("utf-8", errors="replace"), headers_dict


def _is_iframe_blocked(headers: dict[str, str]) -> bool:
    """Return True if the upstream server sends headers that block iframe embedding.

    We look at two headers:

    * ``X-Frame-Options`` (deprecated but widely used): values ``DENY``,
      ``SAMEORIGIN``, or ``ALLOW-FROM <uri>`` all block embedding from
      our origin (we're never same-origin with HN-linked sites and the
      ALLOW-FROM URI is never us).

    * ``Content-Security-Policy`` ``frame-ancestors`` directive: values
      ``'none'``, ``'self'``, or any host list that doesn't include
      our dashboard domain block embedding. We treat ANY frame-ancestors
      directive as blocking unless it explicitly allows ``*`` or our
      production hostname.

    False negatives (we say not-blocked but iframe still blanks) are
    acceptable — the user can still click "Open in new tab". False
    positives (we say blocked but iframe would have worked) are also
    acceptable — they just see the friendly error panel instead of a
    rendered iframe. The cost-of-error tradeoff favors over-blocking.
    """
    xfo = (headers.get("x-frame-options") or "").strip().lower()
    if xfo:
        # Any X-Frame-Options value blocks us in practice.
        return True

    csp = (headers.get("content-security-policy") or "").lower()
    if "frame-ancestors" not in csp:
        return False

    # Find the frame-ancestors directive value.
    for directive in csp.split(";"):
        d = directive.strip()
        if d.startswith("frame-ancestors"):
            value = d[len("frame-ancestors"):].strip()
            if not value:
                return True
            # Permissive forms: '*' or our production hostname.
            if "*" in value.split() or "app.clearspringcg.com" in value:
                return False
            # 'none', 'self', or any specific host list that doesn't
            # include us blocks embedding.
            return True
    return False


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
