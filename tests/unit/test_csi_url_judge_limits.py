"""Tests for the v2 caps in csi_url_judge.

Verifies that:
- DOC_STORAGE_MAX_CHARS and DEFAULT_MAX_FETCH are env-configurable.
- build_linked_context returns full content by default (no 3K truncation).
- build_linked_context still honors an explicit positive max_content_chars.
- Records that are not in fetched state still get a category-only line.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def fresh_module(monkeypatch):
    """Reload the module so module-level constants pick up new env vars."""

    def _reload(env: dict[str, str] | None = None):
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        from universal_agent.services import csi_url_judge

        importlib.reload(csi_url_judge)
        return csi_url_judge

    return _reload


def test_default_caps_are_lifted_from_v1(fresh_module):
    mod = fresh_module()
    # v1 hard-coded 20K storage, 3K analysis, 3 fetches.
    # v2 defaults must be materially larger to accommodate full official docs.
    assert mod.DOC_STORAGE_MAX_CHARS >= 100_000
    assert mod.DEFAULT_MAX_FETCH >= 10


def test_doc_storage_cap_reads_env(fresh_module):
    mod = fresh_module({"UA_CSI_DOC_STORAGE_MAX_CHARS": "50000"})
    assert mod.DOC_STORAGE_MAX_CHARS == 50_000


def test_max_fetch_cap_reads_env(fresh_module):
    mod = fresh_module({"UA_CSI_MAX_FETCH_PER_POST": "20"})
    assert mod.DEFAULT_MAX_FETCH == 20


def test_invalid_env_value_falls_back_to_default(fresh_module):
    mod = fresh_module({"UA_CSI_DOC_STORAGE_MAX_CHARS": "not-a-number"})
    assert mod.DOC_STORAGE_MAX_CHARS == 200_000


def test_negative_env_value_falls_back_to_default(fresh_module):
    mod = fresh_module({"UA_CSI_DOC_STORAGE_MAX_CHARS": "-100"})
    assert mod.DOC_STORAGE_MAX_CHARS == 200_000


def test_build_linked_context_returns_full_content_by_default(fresh_module, tmp_path: Path):
    mod = fresh_module()
    long_body = "X" * 8000  # well past the v1 3K cap
    source_path = tmp_path / "src.md"
    source_path.write_text(long_body, encoding="utf-8")

    record = mod.EnrichmentRecord(
        url="https://docs.anthropic.com/whatever",
        category="documentation",
        fetch_status="fetched",
        content_path=str(source_path),
        content_chars=len(long_body),
    )

    ctx = mod.build_linked_context([record])

    assert "content=" in ctx, "default mode should emit full-content label, not excerpt"
    assert "X" * 8000 in ctx, "full content must survive the no-truncation default"
    assert "content_excerpt" not in ctx


def test_build_linked_context_truncates_when_explicit_cap_passed(fresh_module, tmp_path: Path):
    mod = fresh_module()
    long_body = "Y" * 8000
    source_path = tmp_path / "src.md"
    source_path.write_text(long_body, encoding="utf-8")

    record = mod.EnrichmentRecord(
        url="https://docs.anthropic.com/whatever",
        category="documentation",
        fetch_status="fetched",
        content_path=str(source_path),
        content_chars=len(long_body),
    )

    ctx = mod.build_linked_context([record], max_content_chars=500)

    assert "content_excerpt=" in ctx
    # Only 500 Y's should make it through.
    assert ctx.count("Y") == 500


def test_build_linked_context_zero_cap_is_treated_as_no_cap(fresh_module, tmp_path: Path):
    """Zero or negative explicit caps fall through to the no-truncation path.

    Avoids a footgun where a misconfigured 0 would silently emit empty content.
    """
    mod = fresh_module()
    body = "Z" * 1000
    source_path = tmp_path / "src.md"
    source_path.write_text(body, encoding="utf-8")

    record = mod.EnrichmentRecord(
        url="https://docs.anthropic.com/whatever",
        category="documentation",
        fetch_status="fetched",
        content_path=str(source_path),
        content_chars=len(body),
    )

    ctx = mod.build_linked_context([record], max_content_chars=0)
    assert "content=" in ctx
    assert "Z" * 1000 in ctx


def test_build_linked_context_emits_category_line_for_unfetched(fresh_module):
    mod = fresh_module()
    record = mod.EnrichmentRecord(
        url="https://example.com/something",
        category="documentation",
        worth_fetching=True,
        fetch_status="failed",
        skip_reason="HTTP 503",
    )

    ctx = mod.build_linked_context([record])

    assert "source_type=documentation" in ctx
    assert "status=failed" in ctx


def test_build_linked_context_skips_social_noise(fresh_module):
    mod = fresh_module()
    record = mod.EnrichmentRecord(
        url="https://t.co/abc",
        category="social_noise",
        fetch_status="filtered",
    )
    assert mod.build_linked_context([record]) == ""


def test_enrich_urls_uses_default_max_fetch_when_not_specified(fresh_module, monkeypatch):
    """enrich_urls with max_fetch=None should fall back to DEFAULT_MAX_FETCH."""
    mod = fresh_module({"UA_CSI_MAX_FETCH_PER_POST": "7"})

    captured: dict[str, int] = {}

    real_pre_filter = mod.pre_filter_urls

    def stub_pre_filter(urls):
        return real_pre_filter(urls)

    def stub_judge(urls, context):
        # Return one record per url, all worth fetching.
        return [
            mod.EnrichmentRecord(
                url=u,
                category="documentation",
                worth_fetching=True,
                fetch_status="pending",
            )
            for u in urls
        ]

    fetch_attempts = {"count": 0}

    def stub_fetch(url, category, output_dir, *, timeout):
        fetch_attempts["count"] += 1
        return {"ok": True, "path": "", "method": "stub", "chars": 0}

    monkeypatch.setattr(mod, "judge_urls", stub_judge)
    monkeypatch.setattr(mod, "fetch_url_content", stub_fetch)

    urls = [f"https://example.com/{i}" for i in range(20)]
    out = mod.enrich_urls(
        urls=urls,
        context="ctx",
        output_dir=Path("/tmp/notused"),
    )

    captured["fetched"] = sum(1 for r in out if r.fetch_status == "fetched")
    assert fetch_attempts["count"] == 7
    assert captured["fetched"] == 7
