"""Pin the inline-TOC email body contract for the YouTube daily digest.

Operator complaint (2026-05-28): "the email digest ... was in markdown, but
it's not rendered ... there should have been an index at the beginning of
the email." These tests verify that when `_render_email_body_html` is given
``full_content``, it produces:

1. Inline-styled HTML (Gmail strips <style> blocks in email bodies).
2. A clickable "Jump to a video" TOC at the top of the per-video section.
3. Each TOC link's ``href="#vN-slug"`` resolves to a matching
   ``<h2 id="vN-slug">`` on the per-video section so in-mail anchor clicks
   jump to the right place.
"""
from __future__ import annotations

import re

from universal_agent.scripts.youtube_daily_digest import (
    _render_email_body_html,
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


def _render() -> str:
    body_md, _ = _split_email_body_and_attachment(SAMPLE_DIGEST)
    return _render_email_body_html(
        body_md,
        intro_html='<p>intro</p>',
        full_content=SAMPLE_DIGEST,
    )


def test_inline_toc_present_with_jump_to_a_video_heading():
    html = _render()
    assert "Jump to a video" in html, "TOC heading missing"


def test_toc_links_resolve_to_per_video_h2_anchors():
    html = _render()
    toc_links = set(re.findall(r'href="#(v\d+-[^"]+)"', html))
    h2_ids = set(re.findall(r'<h2[^>]*id="(v\d+-[^"]+)"', html))
    assert toc_links, "no TOC links generated"
    assert h2_ids, "no per-video h2 anchors generated"
    assert toc_links == h2_ids, (
        f"TOC links don't match h2 anchors: only in TOC={toc_links - h2_ids}, "
        f"only in h2={h2_ids - toc_links}"
    )


def test_email_h2_elements_have_inline_style():
    html = _render()
    h2_tags = re.findall(r'<h2[^>]*>', html)
    assert h2_tags, "no h2 elements rendered"
    # Every h2 must carry a `style=` attribute — Gmail strips <style> blocks.
    for tag in h2_tags:
        assert "style=" in tag, f"h2 missing inline style: {tag}"


def test_email_h3_and_p_have_inline_style():
    html = _render()
    # Inline styles must be applied to h3 and p too (the per-video retellings
    # are mostly h3 sub-sections + p paragraphs).
    for tag_name in ("h3", "p"):
        opens = re.findall(rf'<{tag_name}[^>]*>', html)
        assert opens, f"no {tag_name} elements rendered"
        # Most of them should have inline style. Allow one exception (e.g.
        # the intro <p> which the caller styled itself).
        styled = sum(1 for t in opens if "style=" in t)
        assert styled >= len(opens) - 1, (
            f"{tag_name} elements missing inline style: {len(opens) - styled} of {len(opens)}"
        )


def test_toc_box_carries_inline_background_style():
    html = _render()
    # The TOC box needs its background/border inlined (Gmail strips classes).
    assert re.search(
        r'<div\s+style="background:#f6f8fa[^"]*">[^<]*<h2[^>]*>Jump to a video</h2>',
        html,
    ), "TOC box missing inline background style"


def test_legacy_callsite_without_full_content_still_works():
    """Backward compat: callers that don't pass `full_content` (old contract)
    still get a rendered HTML body, just without the TOC."""
    body_md, _ = _split_email_body_and_attachment(SAMPLE_DIGEST)
    html = _render_email_body_html(body_md, intro_html='<p>intro</p>')
    assert "Jump to a video" not in html, "TOC must NOT appear when full_content is omitted"
    assert "<h2" in html.lower(), "Meta-synthesis still renders without TOC"
