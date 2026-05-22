"""Tests for the channel-enriched TOC + end-of-executive-summary positioning."""
from __future__ import annotations

from universal_agent.scripts.youtube_daily_digest import (
    VideoTranscriptPayload,
    _build_per_video_header,
    _extract_channel_from_meta,
    _extract_video_entries,
    _render_full_digest_html,
)


def _digest_md(blocks: list[str]) -> str:
    reduce_out = (
        "## Meta-Synthesis: Daily Digest\n\n"
        "### Cross-Video Themes\n\nThemes.\n\n"
        "### Learning Insights\n\nInsights.\n"
    )
    dispatch = (
        "## Tutorial Pipeline Dispatch\n\n"
        "- Dispatched 0 videos (synthetic test).\n"
    )
    per_video = "\n\n---\n\n".join(blocks)
    return (
        "# Daily YouTube Digest: Friday, 2026-05-22\n\n"
        f"{reduce_out}\n\n---\n\n"
        f"## Per-Video Retellings\n\n{per_video}\n\n"
        f"---\n\n{dispatch}\n"
    )


def _video_block(title: str, channel: str | None, duration_s: int, date: str, vid: str) -> str:
    meta: dict = {"duration": duration_s, "upload_date": date}
    if channel is not None:
        meta["channel"] = channel
    payload = VideoTranscriptPayload(
        video_id=vid, title=title, transcript_text="x", metadata=meta,
    )
    header = _build_per_video_header(payload)
    return (
        f"{header}\n"
        "### Retelling\n\nBody.\n\n"
        "### Thesis\nThesis.\n"
    )


# --- _extract_channel_from_meta -------------------------------------------


def test_channel_extractor_normal_case():
    line = "Cole Medin · 12:34 · May 19, 2026 · [watch ↗](https://youtu.be/abc)"
    assert _extract_channel_from_meta(line) == "Cole Medin"


def test_channel_extractor_returns_empty_when_first_is_duration():
    assert _extract_channel_from_meta("12:34 · May 19, 2026") == ""
    assert _extract_channel_from_meta("1:23:45 · May 19, 2026") == ""


def test_channel_extractor_returns_empty_when_first_is_date():
    assert _extract_channel_from_meta("May 19, 2026 · [watch ↗](...)") == ""


def test_channel_extractor_returns_empty_when_first_is_watch_link():
    assert _extract_channel_from_meta("[watch ↗](https://youtu.be/abc)") == ""


def test_channel_extractor_handles_empty():
    assert _extract_channel_from_meta("") == ""
    assert _extract_channel_from_meta("   ") == ""


def test_channel_extractor_preserves_channel_with_unicode_or_pipes():
    # Real channel name from the seed list: "Pyotr Kurzin | Geopolitics"
    line = "Pyotr Kurzin | Geopolitics · 12:34 · May 19, 2026"
    assert _extract_channel_from_meta(line) == "Pyotr Kurzin | Geopolitics"


# --- _extract_video_entries ----------------------------------------------


def test_video_entries_walks_per_video_section():
    md = (
        "## First Title\n\n"
        "<small>Channel A · 12:34 · May 19, 2026 · [watch ↗](...)</small>\n\n"
        "### Retelling\nBody.\n\n"
        "---\n\n"
        "## Second Title\n\n"
        "<small>Channel B · 5:00 · May 20, 2026 · [watch ↗](...)</small>\n\n"
        "### Retelling\nBody.\n"
    )
    entries = _extract_video_entries(md)
    assert entries == [("First Title", "Channel A"), ("Second Title", "Channel B")]


def test_video_entries_handles_missing_meta_strip():
    md = (
        "## Only Title No Meta\n\n"
        "### Retelling\nBody.\n"
    )
    entries = _extract_video_entries(md)
    assert entries == [("Only Title No Meta", "")]


def test_video_entries_skips_per_video_retellings_header():
    md = (
        "## Per-Video Retellings\n\n"
        "## Actual Video Title\n\n"
        "<small>Channel A · 12:34 · May 19, 2026 · [watch ↗](...)</small>\n\n"
        "### Retelling\nBody.\n"
    )
    entries = _extract_video_entries(md)
    assert entries == [("Actual Video Title", "Channel A")]


# --- End-to-end TOC positioning + rendering -------------------------------


def test_toc_lives_at_end_of_executive_summary():
    """TOC must render AFTER Meta-Synthesis and BEFORE Per-Video Retellings."""
    blocks = [
        _video_block("Vid One", "Cole Medin", 754, "20260519", "abc"),
        _video_block("Vid Two", "AICodeKing", 1500, "20260520", "def"),
    ]
    md = _digest_md(blocks)
    html = _render_full_digest_html(md, day_name="Friday", date_str="2026-05-22")

    meta_idx = html.index("Meta-Synthesis")
    toc_idx = html.index('<div class="digest-toc"')
    per_video_idx = html.index("Per-Video Retellings</h2>")

    assert meta_idx < toc_idx < per_video_idx


def test_toc_contains_channel_and_title():
    blocks = [
        _video_block("Building Agents With Mastra", "Cole Medin", 754, "20260519", "abc"),
        _video_block("9 Layers Pro Playbook", "AICodeKing", 1500, "20260520", "def"),
    ]
    md = _digest_md(blocks)
    html = _render_full_digest_html(md, day_name="Friday", date_str="2026-05-22")

    # Extract the TOC slice
    toc_start = html.index('<div class="digest-toc"')
    toc_end = html.index("Per-Video Retellings</h2>")
    toc = html[toc_start:toc_end]

    assert "Cole Medin" in toc
    assert "Building Agents With Mastra" in toc
    assert "AICodeKing" in toc
    assert "9 Layers Pro Playbook" in toc
    # CSS classes for the new spans
    assert "toc-channel" in toc
    assert "toc-title" in toc


def test_toc_gracefully_omits_channel_when_missing():
    """A video missing channel metadata still appears in TOC, just title-only."""
    blocks = [
        _video_block("Channel Known", "Cole Medin", 754, "20260519", "abc"),
        _video_block("Channel Missing", None, 600, "20260520", "def"),
    ]
    md = _digest_md(blocks)
    html = _render_full_digest_html(md, day_name="Friday", date_str="2026-05-22")

    toc_start = html.index('<div class="digest-toc"')
    toc_end = html.index("Per-Video Retellings</h2>")
    toc = html[toc_start:toc_end]

    # Both video titles present
    assert "Channel Known" in toc
    assert "Channel Missing" in toc
    # Exactly one channel span (the one we have data for)
    assert toc.count('class="toc-channel"') == 1


def test_toc_anchor_ids_align_with_h2_ids():
    blocks = [
        _video_block("First Vid", "AICodeKing", 754, "20260519", "abc"),
        _video_block("Second Vid", "Cole Medin", 1500, "20260520", "def"),
    ]
    md = _digest_md(blocks)
    html = _render_full_digest_html(md, day_name="Friday", date_str="2026-05-22")

    # Pull the TOC and per-video sections
    toc_start = html.index('<div class="digest-toc"')
    toc_end = html.index("Per-Video Retellings</h2>")
    toc = html[toc_start:toc_end]
    rest = html[toc_end:]

    # Confirm v1 and v2 anchors are referenced in TOC and defined later
    assert 'href="#v1-first-vid"' in toc
    assert 'href="#v2-second-vid"' in toc
    assert 'id="v1-first-vid"' in rest
    assert 'id="v2-second-vid"' in rest


def test_toc_renders_with_single_video_returns_empty_string():
    """Under 2 videos we don't bother with the TOC."""
    blocks = [_video_block("Only One", "Cole Medin", 754, "20260519", "abc")]
    md = _digest_md(blocks)
    html = _render_full_digest_html(md, day_name="Friday", date_str="2026-05-22")
    # The CSS block always contains `.digest-toc` selectors; check for the
    # body-side div instead.
    assert '<div class="digest-toc"' not in html
