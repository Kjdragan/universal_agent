"""Unit tests for the deterministic 'demo built' notifier (pure pieces only)."""

from __future__ import annotations

import json
from pathlib import Path

from universal_agent.services.demo_built_notifier import (
    _find_explainer_video,
    _read_manifest_url,
    compose_demo_built_email,
)


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
    assert "Watch the explainer" in html
    assert "demo_factory" in text


def test_compose_notes_video_pending_when_absent():
    subject, text, html = compose_demo_built_email(
        title="t", capability="c", build_engine="bespoke",
        video_url="", exhibit_url="", workspace_dir="/w", review_required=True,
    )
    assert "not rendered" in text.lower()
    assert "awaiting your review" in text.lower()  # curated note
    assert "Watch the explainer" not in html


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


def test_read_manifest_url(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"demo_id": "x", "exhibit_url": "https://ex/x.html"}), encoding="utf-8"
    )
    assert _read_manifest_url(tmp_path) == "https://ex/x.html"
    assert _read_manifest_url(tmp_path / "nope") == ""
