"""Tests for the trust_source bypass in csi_url_judge.enrich_urls.

Verifies that:
- trust_source=True bypasses the LLM judge for all pre-filter survivors.
- Pre-filter still runs and drops social/product/t.co URLs even with bypass.
- UA_CSI_TRUST_SOURCE_BYPASS_JUDGE=0 disables the bypass at runtime.
- trust_source=False (default) keeps the existing judge-gated behavior.

Background: ClaudeDevs/bcherny are curated official handles. Any URL they
post is intentional and IS the substance the CSI lane exists to capture.
The LLM judge was originally added to filter open-web crawl noise; for
intentional links from official handles it just drops the actual
documentation we exist to capture.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from universal_agent.services import csi_url_judge


def test_trust_source_bypasses_judge_and_marks_all_for_fetch(monkeypatch, tmp_path: Path):
    """trust_source=True → judge_urls is NOT called; all candidates worth_fetching."""
    judge_called = mock.Mock()
    monkeypatch.setattr(csi_url_judge, "judge_urls", judge_called)
    # Stub fetch so we don't actually network out.
    monkeypatch.setattr(
        csi_url_judge,
        "fetch_url_content",
        lambda url, category, output_dir, timeout=15: {
            "ok": True, "path": str(output_dir / "x.md"), "method": "stub", "chars": 100,
        },
    )

    urls = [
        "https://docs.claude.com/en/api/keyless-auth",
        "https://github.com/anthropics/anthropic-sdk-python",
        "https://anthropic.com/news/some-launch",
    ]
    records = csi_url_judge.enrich_urls(
        urls=urls,
        context="Today we're introducing keyless auth for the Claude Platform...",
        output_dir=tmp_path,
        trust_source=True,
    )

    judge_called.assert_not_called()
    assert len(records) == 3
    for r in records:
        assert r.worth_fetching is True
        assert r.category == "trusted_source"
        assert r.fetch_status == "fetched"


def test_trust_source_still_pre_filters_social_and_product(monkeypatch, tmp_path: Path):
    """Bypass does NOT skip Pass 1: t.co / social / product-app URLs still drop."""
    monkeypatch.setattr(csi_url_judge, "judge_urls", mock.Mock())
    monkeypatch.setattr(
        csi_url_judge,
        "fetch_url_content",
        lambda url, category, output_dir, timeout=15: {
            "ok": True, "path": str(output_dir / "x.md"), "method": "stub", "chars": 1,
        },
    )

    urls = [
        "https://t.co/h0LGCMAOH",                    # social_domain → dropped
        "https://twitter.com/some/post",             # social_domain → dropped
        "https://claude.ai/code",                    # product_app → dropped
        "https://docs.claude.com/en/api/auth",       # survives → fetched
    ]
    records = csi_url_judge.enrich_urls(
        urls=urls,
        context="Check the docs",
        output_dir=tmp_path,
        trust_source=True,
    )

    fetched = [r for r in records if r.fetch_status == "fetched"]
    filtered = [r for r in records if r.fetch_status == "filtered"]
    assert len(fetched) == 1
    assert fetched[0].url == "https://docs.claude.com/en/api/auth"
    assert len(filtered) == 3
    assert {r.skip_reason for r in filtered} == {"social_domain", "product_app_not_content"}


def test_trust_source_env_disable_falls_back_to_judge(monkeypatch, tmp_path: Path):
    """UA_CSI_TRUST_SOURCE_BYPASS_JUDGE=0 → bypass disabled, judge runs again."""
    judge_called = mock.Mock(return_value=[])
    monkeypatch.setattr(csi_url_judge, "judge_urls", judge_called)
    monkeypatch.setenv("UA_CSI_TRUST_SOURCE_BYPASS_JUDGE", "0")

    csi_url_judge.enrich_urls(
        urls=["https://docs.claude.com/en/api/auth"],
        context="ctx",
        output_dir=tmp_path,
        trust_source=True,
    )

    judge_called.assert_called_once()


def test_trust_source_default_false_preserves_legacy_behavior(monkeypatch, tmp_path: Path):
    """trust_source not passed → judge IS consulted (legacy general-purpose path)."""
    judge_called = mock.Mock(return_value=[])
    monkeypatch.setattr(csi_url_judge, "judge_urls", judge_called)

    csi_url_judge.enrich_urls(
        urls=["https://example.com/whatever"],
        context="ctx",
        output_dir=tmp_path,
    )

    judge_called.assert_called_once()
