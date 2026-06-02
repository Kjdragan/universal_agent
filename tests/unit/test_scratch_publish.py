"""Unit tests for the tailnet-scratchpad publish helper.

These never touch SSH or the real VPS: each test points the helper at a tiny fake
``publish_scratch.sh`` stand-in so we can exercise the success path, the failure
degradations (non-zero exit, missing script, unexpected output), and the slug
construction in isolation.
"""

from __future__ import annotations

from pathlib import Path
import stat

import pytest

from universal_agent.services import scratch_publish


def _write_fake_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "publish_scratch.sh"
    script.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def test_returns_url_on_success(tmp_path, monkeypatch):
    # Fake script echoes a scratchpad-shaped URL built from its slug + filename args,
    # which lets us assert the helper passed them through correctly.
    script = _write_fake_script(
        tmp_path,
        'echo "https://uaonvps.taildcc090.ts.net/scratch/$2/$(basename "$1")"',
    )
    monkeypatch.setattr(scratch_publish, "_publish_script", lambda: script)

    url = scratch_publish.publish_html_to_scratch(
        "<html><body>hi</body></html>", slug="yt-digest-2026-06-02", filename="d.html"
    )

    assert url is not None
    assert url.startswith("https://uaonvps.taildcc090.ts.net/scratch/yt-digest-2026-06-02-")
    assert url.endswith("/d.html")


def test_slug_gets_random_suffix_for_uniqueness(tmp_path, monkeypatch):
    script = _write_fake_script(
        tmp_path,
        'echo "https://uaonvps.taildcc090.ts.net/scratch/$2/$(basename "$1")"',
    )
    monkeypatch.setattr(scratch_publish, "_publish_script", lambda: script)

    url1 = scratch_publish.publish_html_to_scratch("<p>1</p>", slug="same")
    url2 = scratch_publish.publish_html_to_scratch("<p>2</p>", slug="same")

    assert url1 != url2, "repeated publishes with the same slug must not collide"


def test_returns_none_on_nonzero_exit(tmp_path, monkeypatch):
    script = _write_fake_script(tmp_path, 'echo "boom" >&2; exit 1')
    monkeypatch.setattr(scratch_publish, "_publish_script", lambda: script)

    assert scratch_publish.publish_html_to_scratch("<p>x</p>", slug="s") is None


def test_returns_none_when_script_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        scratch_publish, "_publish_script", lambda: tmp_path / "does-not-exist.sh"
    )

    assert scratch_publish.publish_html_to_scratch("<p>x</p>") is None


def test_returns_none_on_non_url_output(tmp_path, monkeypatch):
    script = _write_fake_script(tmp_path, 'echo "not-a-url"')
    monkeypatch.setattr(scratch_publish, "_publish_script", lambda: script)

    assert scratch_publish.publish_html_to_scratch("<p>x</p>", slug="s") is None


@pytest.mark.parametrize(
    "raw,expected_prefix",
    [
        ("yt digest!!", "yt-digest"),
        ("a/b\\c", "a-b-c"),
        ("...", "report"),
        ("", "report"),
    ],
)
def test_sanitize_slug(raw, expected_prefix):
    assert scratch_publish._sanitize_slug(raw) == expected_prefix
