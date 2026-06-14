"""Unit tests for the Hacker News article reader (reader-mode extractor).

Covers metadata extraction, content extraction, noise stripping,
and graceful failure paths. Network is mocked via httpx.MockTransport
so tests are deterministic and offline.
"""
from __future__ import annotations

import httpx
import pytest

from universal_agent.services import hackernews_article_reader as reader

# ─── helpers ───────────────────────────────────────────────────────────


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Force httpx.Client(...) inside the reader to use a MockTransport."""
    transport = httpx.MockTransport(handler)
    real_init = httpx.Client.__init__

    def fake_init(self, *args, **kwargs):  # type: ignore[no-redef]
        kwargs["transport"] = transport
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", fake_init)


def _html_response(html: str, *, status: int = 200, content_type: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(status, headers={"content-type": content_type}, text=html)


# ─── happy-path extraction ─────────────────────────────────────────────


def test_extracts_title_byline_lead_image_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><head>
      <title>Fallback Title</title>
      <meta property="og:title" content="The Real Title" />
      <meta name="author" content="Jane Doe" />
      <meta property="og:image" content="https://cdn.example.com/cover.jpg" />
    </head><body>
      <header><nav>nav junk</nav></header>
      <article>
        <h1>The Real Title</h1>
        <p>First paragraph with <strong>bold</strong> and a <a href="https://x.com/y">link</a>.</p>
        <p>Second paragraph.</p>
        <pre><code>def hello(): pass</code></pre>
      </article>
      <footer>footer junk</footer>
    </body></html>
    """
    _patch_transport(monkeypatch, lambda req: _html_response(html))

    out = reader.fetch_article("https://example.com/post")

    assert out["ok"] is True
    assert out["title"] == "The Real Title"
    assert out["byline"] == "Jane Doe"
    assert out["host"] == "example.com"
    assert out["lead_image_url"] == "https://cdn.example.com/cover.jpg"
    assert "First paragraph" in out["content_md"]
    assert "Second paragraph" in out["content_md"]
    assert "**bold**" in out["content_md"]
    assert "[link](https://x.com/y)" in out["content_md"]
    assert "footer junk" not in out["content_md"]
    assert "nav junk" not in out["content_md"]
    assert out["content_length"] == len(out["content_md"])
    assert out["source_url"] == "https://example.com/post"
    assert out["fetched_at"]


def test_falls_back_to_h1_when_no_meta_title(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><article><h1>From H1</h1><p>body text here.</p></article></body></html>"
    _patch_transport(monkeypatch, lambda req: _html_response(html))

    out = reader.fetch_article("https://example.org/")
    assert out["ok"] is True
    assert out["title"] == "From H1"


def test_resolves_protocol_relative_lead_image(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """<html><head><meta property="og:image" content="//cdn.example.org/img.png"/>
              </head><body><article><p>x</p></article></body></html>"""
    _patch_transport(monkeypatch, lambda req: _html_response(html))

    out = reader.fetch_article("https://example.org/post")
    assert out["lead_image_url"] == "https://cdn.example.org/img.png"


def test_resolves_root_relative_lead_image(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """<html><head><meta property="og:image" content="/img/cover.png"/>
              </head><body><article><p>x</p></article></body></html>"""
    _patch_transport(monkeypatch, lambda req: _html_response(html))

    out = reader.fetch_article("https://blog.example.org/posts/42")
    assert out["lead_image_url"] == "https://blog.example.org/img/cover.png"


def test_strips_noise_classes_inside_article(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><body><article>
      <h1>Main</h1>
      <p>Real article content.</p>
      <div class="newsletter-signup"><p>Subscribe to our newsletter!</p></div>
      <div class="related-posts"><p>You might also like…</p></div>
      <p>More real content after the noise.</p>
    </article></body></html>
    """
    _patch_transport(monkeypatch, lambda req: _html_response(html))

    out = reader.fetch_article("https://blog.example.com/")
    assert out["ok"] is True
    assert "Real article content." in out["content_md"]
    assert "More real content" in out["content_md"]
    assert "Subscribe to our newsletter" not in out["content_md"]
    assert "You might also like" not in out["content_md"]


def test_falls_back_to_main_then_body(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <html><body>
      <main>
        <h1>Mainline</h1>
        <p>Body via main tag.</p>
      </main>
    </body></html>
    """
    _patch_transport(monkeypatch, lambda req: _html_response(html))
    out = reader.fetch_article("https://example.com/")
    assert out["ok"] is True
    assert "Body via main tag." in out["content_md"]


# ─── failure paths ─────────────────────────────────────────────────────


def test_invalid_url_returns_structured_error() -> None:
    out = reader.fetch_article("not-a-url")
    assert out["ok"] is False
    assert out["error"] == "invalid_url"
    assert out["source_url"] == "not-a-url"


def test_non_http_scheme_rejected() -> None:
    out = reader.fetch_article("javascript:alert(1)")
    assert out["ok"] is False
    assert out["error"] == "invalid_url"


def test_http_500_returns_fetch_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, text="boom"))

    out = reader.fetch_article("https://example.com/")
    assert out["ok"] is False
    assert out["error"].startswith("fetch_failed:")
    assert out["host"] == "example.com"


def test_non_html_content_type_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_transport(
        monkeypatch,
        lambda req: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"),
    )
    out = reader.fetch_article("https://example.com/whitepaper.pdf")
    assert out["ok"] is False
    assert "non-html" in out["error"]


def test_network_timeout_returns_fetch_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=req)

    _patch_transport(monkeypatch, raise_timeout)
    out = reader.fetch_article("https://slowsite.example/")
    assert out["ok"] is False
    assert "fetch_failed" in out["error"]


def test_empty_body_returns_extraction_with_blank_content(monkeypatch: pytest.MonkeyPatch) -> None:
    # 200 OK with valid HTML content-type but empty body — extraction yields
    # empty content_md but ok=True (no fetch error). Frontend treats empty
    # content as "couldn't extract" and falls through to iframe.
    _patch_transport(monkeypatch, lambda req: _html_response("<html><body></body></html>"))
    out = reader.fetch_article("https://example.com/")
    assert out["ok"] is True
    assert out["content_md"] == ""
    assert out["content_length"] == 0


def test_strict_max_bytes_truncates_huge_response(monkeypatch: pytest.MonkeyPatch) -> None:
    # Response just over MAX_BYTES — extraction should still succeed on the
    # truncated prefix without raising. Use a NODE-LIGHT fixture (a handful of
    # large prose paragraphs) rather than 600k tiny <p> elements: both exceed
    # MAX_BYTES so the byte-truncation in _fetch_html still fires, but the
    # post-truncation DOM here has ~100 nodes instead of ~500k, so parsing is
    # ~0.4s instead of ~56s (the latter tripped the 60s pytest-timeout). Same
    # behavior under test, just without the pathological node count.
    paragraph = "<p>" + ("The quick brown fox jumps over the lazy dog. " * 1000) + "</p>"
    big_html = "<html><body><article><h1>T</h1>" + (paragraph * 100) + "</article></body></html>"
    assert len(big_html) > reader.MAX_BYTES
    _patch_transport(monkeypatch, lambda req: _html_response(big_html))

    out = reader.fetch_article("https://example.com/")
    assert out["ok"] is True  # truncated but still parsed


# ─── iframe-blocked detection (frontend auto-fallback signal) ───────────


def _html_response_with_headers(html: str, extra_headers: dict[str, str]) -> httpx.Response:
    headers = {"content-type": "text/html; charset=utf-8"}
    headers.update(extra_headers)
    return httpx.Response(200, headers=headers, text=html)


def test_iframe_blocked_when_xfo_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Frame-Options: DENY means the iframe will blank — flag must be True."""
    html = "<html><body><article><p>content here</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(html, {"x-frame-options": "DENY"}),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["ok"] is True
    assert out["iframe_blocked_by_headers"] is True


def test_iframe_blocked_when_xfo_sameorigin(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-Frame-Options: SAMEORIGIN blocks us (we're never same-origin with linked sites)."""
    html = "<html><body><article><p>content</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(html, {"x-frame-options": "SAMEORIGIN"}),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is True


def test_iframe_blocked_when_csp_frame_ancestors_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """CSP frame-ancestors 'none' blocks all embedding."""
    html = "<html><body><article><p>content</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(
            html,
            {"content-security-policy": "default-src 'self'; frame-ancestors 'none'"},
        ),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is True


def test_iframe_blocked_when_csp_frame_ancestors_self(monkeypatch: pytest.MonkeyPatch) -> None:
    """CSP frame-ancestors 'self' blocks us (we're never same-origin)."""
    html = "<html><body><article><p>content</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(
            html,
            {"content-security-policy": "frame-ancestors 'self'"},
        ),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is True


def test_iframe_blocked_when_csp_lists_other_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """frame-ancestors with a host list that doesn't include us blocks us."""
    html = "<html><body><article><p>content</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(
            html,
            {"content-security-policy": "frame-ancestors *.theverge.com vox.com"},
        ),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is True


def test_iframe_NOT_blocked_when_csp_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    """frame-ancestors * means anyone can embed — NOT blocked."""
    html = "<html><body><article><p>content</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(
            html,
            {"content-security-policy": "frame-ancestors *"},
        ),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is False


def test_iframe_NOT_blocked_when_csp_includes_our_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """If frame-ancestors explicitly allows our prod host, NOT blocked."""
    html = "<html><body><article><p>content</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(
            html,
            {"content-security-policy": "frame-ancestors app.clearspringcg.com"},
        ),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is False


def test_iframe_NOT_blocked_when_no_xfo_or_csp(monkeypatch: pytest.MonkeyPatch) -> None:
    """No anti-embed headers → iframe should work → NOT blocked."""
    html = "<html><body><article><p>plain content, no XFO</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(html, {}),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is False


def test_iframe_blocked_when_csp_has_other_directives_but_no_frame_ancestors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CSP without frame-ancestors doesn't block embedding."""
    html = "<html><body><article><p>content</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        lambda req: _html_response_with_headers(
            html,
            {"content-security-policy": "default-src 'self'; script-src 'self'"},
        ),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is False


def test_iframe_blocked_marker_set_to_true_on_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On fetch failure (5xx, timeout) we conservatively mark iframe_blocked=True.

    Rationale: most fetch failures from our backend (DNS, 5xx, Akamai 403,
    timeouts) suggest the iframe load from the user's browser will also
    fail. Better to show the friendly error panel than auto-swap to a
    likely-blank iframe.
    """
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, text="server error"))
    out = reader.fetch_article("https://example.com/")
    assert out["ok"] is False
    assert out["iframe_blocked_by_headers"] is True


def test_invalid_url_iframe_blocked_marker_is_false() -> None:
    """An invalid URL never goes to fetch — iframe_blocked stays False (and it
    doesn't matter; the frontend won't try to iframe an invalid URL anyway)."""
    out = reader.fetch_article("not-a-url")
    assert out["ok"] is False
    assert out["iframe_blocked_by_headers"] is False


def test_xfo_header_check_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real-world servers send X-Frame-Options in mixed case (e.g. 'X-Frame-Options: deny')."""
    html = "<html><body><article><p>content</p></article></body></html>"
    _patch_transport(
        monkeypatch,
        # Lower-case header from server (httpx normalizes case but real responses vary)
        lambda req: _html_response_with_headers(html, {"X-Frame-Options": "deny"}),
    )
    out = reader.fetch_article("https://example.com/")
    assert out["iframe_blocked_by_headers"] is True
