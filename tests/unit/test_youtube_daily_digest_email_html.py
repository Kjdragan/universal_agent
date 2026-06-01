"""Pin the digest email layout contract: short summary body + attached report.

Operator history:
  * 2026-05-28: complaint that the email had no index — the digest was
    inlined into the email body with a clickable "Jump to a video" TOC.
  * 2026-05-31: that inline index proved unclickable in Gmail. Gmail strips
    ``id=`` attributes at render time (so the ``#anchor`` TOC links have no
    targets) and clips messages over ~102KB (the inlined ~130KB digest got
    truncated behind "View entire message"). Reverted: the email body is now
    the short meta-synthesis summary, and the clickable per-video index lives
    in the standalone HTML attachment, where anchors resolve in a browser.

These tests pin that production contract:

1. The production email body (``_render_email_body_html`` WITHOUT
   ``full_content``) is inline-styled, contains the meta-synthesis summary,
   and does NOT contain an inline per-video TOC.
2. The standalone attachment (``_render_full_digest_html``) carries the
   clickable per-video TOC whose ``href="#vN-slug"`` links resolve to matching
   ``<h2 id="vN-slug">`` anchors.
3. The inline-everything path is retained as a capability (for clients that
   honor in-message anchors, e.g. Apple Mail) but is not the shipped layout.
"""
from __future__ import annotations

import re

from universal_agent.scripts.youtube_daily_digest import (
    _render_email_body_html,
    _render_full_digest_html,
    _split_email_body_and_attachment,
)

SAMPLE_DIGEST = """# Daily YouTube Digest: Wednesday, 2026-05-28

## Meta-Synthesis: Daily Digest

Themes go here.

### Cross-Video Themes

**Theme A:** prose.

**Theme B:** prose.

---

## Per-Video Retellings

## First Video Title

<small>Channel One · 8:58 · May 27, 2026 · [watch ↗](https://www.youtube.com/watch?v=aaaaaaaaaaa)</small>

### Retelling
Body of first video.

### Thesis
First thesis.

---

## Second Video Title

<small>Channel Two · 12:19 · May 27, 2026 · [watch ↗](https://www.youtube.com/watch?v=bbbbbbbbbbb)</small>

### Retelling
Body of second video.

### Thesis
Second thesis.

---

## Tutorial Pipeline Dispatch

Footer content.
"""


def _render_body() -> str:
    """Render the email body exactly as the digest cron does (no full_content)."""
    body_md, _ = _split_email_body_and_attachment(SAMPLE_DIGEST)
    return _render_email_body_html(body_md, intro_html="<p>intro</p>")


def _render_attachment() -> str:
    _, attachment_md = _split_email_body_and_attachment(SAMPLE_DIGEST)
    return _render_full_digest_html(
        attachment_md, day_name="Wednesday", date_str="2026-05-28"
    )


# --- Production email body: short summary, no inline TOC -------------------


def test_production_body_has_no_inline_toc():
    html = _render_body()
    assert "Jump to a video" not in html, (
        "Email body must not carry an inline per-video TOC — it can't be "
        "clicked in Gmail. The index lives in the attached report."
    )


def test_production_body_renders_meta_synthesis():
    html = _render_body()
    assert "Meta-Synthesis" in html
    assert "Cross-Video Themes" in html
    # Per-video retellings must NOT be inlined in the body (they live in the
    # attachment); otherwise the body grows past Gmail's clip threshold.
    assert "First Video Title" not in html
    assert "Second Video Title" not in html


def test_production_body_stays_well_under_gmail_clip_threshold():
    html = _render_body()
    size = len(html.encode("utf-8"))
    assert size < 102_400, f"email body {size} bytes exceeds Gmail's ~102KB clip threshold"


def test_production_body_h2_and_p_have_inline_style():
    """Gmail strips <style>, so visual rules must be inlined per-element."""
    html = _render_body()
    for tag_name in ("h2", "p"):
        opens = re.findall(rf"<{tag_name}[^>]*>", html)
        assert opens, f"no {tag_name} elements rendered"
        styled = sum(1 for t in opens if "style=" in t)
        # Allow one exception (the caller-styled intro <p>).
        assert styled >= len(opens) - 1, (
            f"{tag_name} elements missing inline style: {len(opens) - styled} of {len(opens)}"
        )


# --- Attachment: the clickable index actually lives here -------------------


def test_attachment_carries_clickable_toc():
    html = _render_attachment()
    assert "Jump to a video" in html, "attachment must carry the per-video TOC"


def test_attachment_toc_links_resolve_to_h2_anchors():
    html = _render_attachment()
    toc_links = set(re.findall(r'href="#(v\d+-[^"]+)"', html))
    h2_ids = set(re.findall(r'<h2[^>]*id="(v\d+-[^"]+)"', html))
    assert toc_links, "no TOC links generated in attachment"
    assert h2_ids, "no per-video h2 anchors generated in attachment"
    assert toc_links == h2_ids, (
        f"attachment TOC links don't match h2 anchors: only in TOC={toc_links - h2_ids}, "
        f"only in h2={h2_ids - toc_links}"
    )


# --- Retained capability: inline-everything still works when requested -----


def test_inline_everything_path_still_supported():
    """The inline-full-digest layout is retained (Apple Mail honors anchors),
    even though the cron no longer uses it. Passing full_content opts in."""
    body_md, _ = _split_email_body_and_attachment(SAMPLE_DIGEST)
    html = _render_email_body_html(
        body_md, intro_html="<p>intro</p>", full_content=SAMPLE_DIGEST
    )
    assert "Jump to a video" in html
    toc_links = set(re.findall(r'href="#(v\d+-[^"]+)"', html))
    h2_ids = set(re.findall(r'<h2[^>]*id="(v\d+-[^"]+)"', html))
    assert toc_links and toc_links == h2_ids
