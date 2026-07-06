"""Unit tests for the deterministic 'demo built' notifier (pure pieces only)."""

from __future__ import annotations

import json
from pathlib import Path

from universal_agent.services.demo_built_notifier import (
    _clearspring_builder_present,
    _find_explainer_video,
    _find_run_screencast,
    _known_video_path,
    _read_manifest_url,
    compose_demo_built_email,
)

# Unique marker for the actual explainer link/button (the fallback notes also
# contain "Explainer video", so we key off the retitled button phrase).
_EXPLAINER_LINK_MARKER = "how it was built and what it does"


def test_compose_includes_video_link_when_present():
    subject, text, html = compose_demo_built_email(
        title="Phoenix right-sizing", capability="model right-sizing",
        build_engine="demo_factory", video_url="https://uaonvps.example/scratch/v/x.mp4",
        exhibit_url="https://uaonvps.example/scratch/e/x.html",
        workspace_dir="/opt/ua/ws/demo-x", review_required=False,
    )
    assert "Demo built" in subject and "Phoenix right-sizing" in subject
    assert "https://uaonvps.example/scratch/v/x.mp4" in text
    assert "https://uaonvps.example/scratch/v/x.mp4" in html
    assert _EXPLAINER_LINK_MARKER in html  # retitled explainer button
    assert "demo_factory" in text


def test_compose_includes_both_videos_explainer_before_screencast():
    """Both videos link in the SAME email, explainer first, screencast second."""
    subject, text, html = compose_demo_built_email(
        title="Phoenix right-sizing", capability="model right-sizing",
        build_engine="demo_factory", video_url="https://uaonvps.example/scratch/v/x.mp4",
        exhibit_url="", workspace_dir="/w", review_required=False,
        screencast_url="https://uaonvps.example/scratch/s/x.mp4",
    )
    # Both URLs present in text and html.
    assert "https://uaonvps.example/scratch/v/x.mp4" in text
    assert "https://uaonvps.example/scratch/s/x.mp4" in text
    assert "https://uaonvps.example/scratch/v/x.mp4" in html
    assert "https://uaonvps.example/scratch/s/x.mp4" in html
    # Both labels present, retitled explainer + new screencast line.
    assert _EXPLAINER_LINK_MARKER in html
    assert "the demo actually running" in html
    assert "the demo actually running" in text
    # Order: explainer link before screencast link (text and html).
    assert text.index("scratch/v/x.mp4") < text.index("scratch/s/x.mp4")
    assert html.index("scratch/v/x.mp4") < html.index("scratch/s/x.mp4")


def test_compose_omits_screencast_line_when_absent():
    """No screencast_url and no screencast status → the line is omitted entirely."""
    subject, text, html = compose_demo_built_email(
        title="t", capability="c", build_engine="demo_factory",
        video_url="https://uaonvps.example/scratch/v/x.mp4", exhibit_url="",
        workspace_dir="/w", review_required=False,
    )
    assert "the demo actually running" not in html
    assert "the demo actually running" not in text
    assert "Screen capture" not in html
    assert "Screen capture" not in text


def test_compose_notes_screencast_found_unpublished():
    subject, text, html = compose_demo_built_email(
        title="t", capability="c", build_engine="demo_factory",
        video_url="", exhibit_url="", workspace_dir="/w", review_required=False,
        screencast_status="found_unpublished",
        screencast_local_path="/w/demo-x/run_screencast.mp4",
    )
    assert "not yet published" in text.lower()
    assert "/w/demo-x/run_screencast.mp4" in text
    assert "/w/demo-x/run_screencast.mp4" in html
    assert "the demo actually running" not in html  # no live link, only the note


def test_compose_notes_not_installed_when_no_status_given():
    """Default state (no filesystem signal): ClearSpring isn't on this host."""
    subject, text, html = compose_demo_built_email(
        title="t", capability="c", build_engine="bespoke",
        video_url="", exhibit_url="", workspace_dir="/w", review_required=True,
    )
    assert "not rendered" in text.lower()
    assert "not installed" in text.lower()
    assert "awaiting your review" in text.lower()  # curated note
    assert _EXPLAINER_LINK_MARKER not in html


def test_compose_notes_found_unpublished():
    subject, text, html = compose_demo_built_email(
        title="t", capability="c", build_engine="demo_factory",
        video_url="", exhibit_url="", workspace_dir="/w", review_required=False,
        video_status="found_unpublished", video_local_path="/w/video/x-explainer.mp4",
    )
    assert "not yet published" in text.lower()
    assert "/w/video/x-explainer.mp4" in text
    assert "/w/video/x-explainer.mp4" in html
    assert _EXPLAINER_LINK_MARKER not in html


def test_compose_notes_rendering_in_background():
    subject, text, html = compose_demo_built_email(
        title="t", capability="c", build_engine="demo_factory",
        video_url="", exhibit_url="", workspace_dir="/w", review_required=False,
        video_status="rendering", video_local_path="/w/video/x-explainer.mp4",
    )
    assert "rendering in background" in text.lower()
    assert "/w/video/x-explainer.mp4" in text
    assert "/w/video/x-explainer.mp4" in html
    assert _EXPLAINER_LINK_MARKER not in html


def test_known_video_path():
    assert _known_video_path(Path("/ws"), "demo-x") == Path("/ws/video/demo-x-explainer.mp4")


def test_clearspring_builder_present_via_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLEARSPRING_DIR", raising=False)
    # Isolate from the real dev host's ~/lrepos/clearspring-studio (the default
    # fallback checks Path.home(), which honors $HOME on POSIX).
    fake_home = tmp_path / "empty_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    assert _clearspring_builder_present() is False  # nothing set up under this fake home

    cs_dir = tmp_path / "clearspring-studio"
    (cs_dir / "scripts").mkdir(parents=True)
    (cs_dir / "scripts" / "build_demo_video.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLEARSPRING_DIR", str(cs_dir))
    assert _clearspring_builder_present() is True


def test_find_explainer_skips_musiconly_sidecar(tmp_path: Path):
    vd = tmp_path / "demo-x" / "video"
    vd.mkdir(parents=True)
    (vd / "x-explainer.mp4").write_bytes(b"x" * 500)
    (vd / "x-explainer.musiconly.mp4").write_bytes(b"x" * 999)  # bigger, but must be ignored
    found = _find_explainer_video(tmp_path)
    assert found is not None
    assert found.name == "x-explainer.mp4"


def test_find_explainer_returns_none_when_absent(tmp_path: Path):
    (tmp_path / "readme.md").write_text("no video here", encoding="utf-8")
    assert _find_explainer_video(tmp_path) is None


def test_find_run_screencast_prefers_largest(tmp_path: Path):
    (tmp_path / "demo-x" / "video").mkdir(parents=True)
    (tmp_path / "demo-x" / "run_screencast.mp4").write_bytes(b"x" * 400)
    # A second hit in a subdir, larger — should win.
    (tmp_path / "demo-x" / "extra").mkdir()
    (tmp_path / "demo-x" / "extra" / "run_screencast.mp4").write_bytes(b"x" * 900)
    found = _find_run_screencast(tmp_path)
    assert found is not None
    assert found.name == "run_screencast.mp4"
    assert found.stat().st_size == 900


def test_find_run_screencast_returns_none_when_absent(tmp_path: Path):
    (tmp_path / "run_explainer.mp4").write_bytes(b"x" * 100)  # not the screencast
    assert _find_run_screencast(tmp_path) is None


def test_read_manifest_url(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"demo_id": "x", "exhibit_url": "https://ex/x.html"}), encoding="utf-8"
    )
    assert _read_manifest_url(tmp_path) == "https://ex/x.html"
    assert _read_manifest_url(tmp_path / "nope") == ""
