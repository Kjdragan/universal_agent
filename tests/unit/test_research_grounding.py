"""Tests for the research grounding subagent (PR 3).

Covers:
- Tier gate (research <2 never fires unless operator_force).
- Trigger reasons (NO_LINKS, THIN_LINKED_SOURCES, UNKNOWN_TERM, OPERATOR_FORCE).
- Allowlist priority ranking.
- Allowlist enforcement (general-web URLs marked rank=-1).
- Candidate URL generation per term.
- execute_research wiring (with the network fetcher stubbed).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from universal_agent.services import research_grounding as rg
from universal_agent.services.research_grounding import (
    ResearchRequest,
    ResearchResult,
    TriggerReason,
    allowlist_rank,
    build_research_request,
    candidate_urls_for_term,
    execute_research,
    extract_candidate_terms,
    is_allowed,
    should_trigger_research,
    tier_gate,
)

# ── Tier gate ────────────────────────────────────────────────────────────────


def test_tier_gate_default_is_2():
    assert tier_gate() == 2


def test_tier_gate_reads_env(monkeypatch):
    monkeypatch.setenv("UA_CSI_RESEARCH_TIER_GATE", "3")
    # tier_gate() re-reads the env each call, so no module reload needed.
    assert rg.tier_gate() == 3


def test_tier_1_does_not_trigger_even_with_no_links():
    triggered, reasons = should_trigger_research(
        post={"id": "1", "tier": 1, "links": [], "text": "Some Feature_Name announced"},
    )
    assert triggered is False
    assert reasons == []


def test_tier_2_triggers_when_no_links():
    triggered, reasons = should_trigger_research(
        post={"id": "2", "tier": 2, "links": [], "text": "Just a tweet"},
    )
    assert triggered is True
    assert TriggerReason.NO_LINKS in reasons


def test_thin_linked_sources_signal():
    triggered, reasons = should_trigger_research(
        post={"id": "3", "tier": 3, "links": ["https://x.com/a"], "text": "..."},
        classifier_result={"linked_sources_thin": True},
    )
    assert triggered is True
    assert TriggerReason.THIN_LINKED_SOURCES in reasons


def test_unknown_term_triggers():
    triggered, reasons = should_trigger_research(
        post={
            "id": "4",
            "tier": 2,
            "links": ["https://docs.anthropic.com/foo"],
            "text": "We just announced ProjectMemoryTool support.",
        },
        existing_entity_names=set(),
    )
    assert triggered is True
    assert TriggerReason.UNKNOWN_TERM in reasons


def test_known_terms_do_not_trigger_unknown_term_signal():
    triggered, reasons = should_trigger_research(
        post={
            "id": "5",
            "tier": 2,
            "links": ["https://docs.anthropic.com/foo"],
            "text": "Skills now support cross-project references.",
        },
        existing_entity_names={"Skills"},
    )
    assert TriggerReason.UNKNOWN_TERM not in reasons


def test_operator_force_bypasses_tier_gate():
    triggered, reasons = should_trigger_research(
        post={"id": "6", "tier": 0, "links": [], "text": ""},
        operator_force=True,
    )
    assert triggered is True
    assert reasons == [TriggerReason.OPERATOR_FORCE]


# ── Allowlist ─────────────────────────────────────────────────────────────────

ALLOW = [
    "docs.anthropic.com",
    "github.com/anthropics",
    "anthropic.com/news",
    "anthropic.com/engineering",
]


def test_allowlist_ranks_official_docs_highest():
    assert allowlist_rank("https://docs.anthropic.com/en/docs/skills", ALLOW) == 0
    assert allowlist_rank("https://github.com/anthropics/claude-code", ALLOW) == 1
    assert allowlist_rank("https://www.anthropic.com/news/something", ALLOW) == 2


def test_allowlist_rejects_off_allowlist():
    assert allowlist_rank("https://example.com/article", ALLOW) == -1
    assert is_allowed("https://example.com", ALLOW) is False


def test_allowlist_path_aware_match():
    """github.com without /anthropics should NOT match the github.com/anthropics entry."""
    assert allowlist_rank("https://github.com/openai/codex", ALLOW) == -1


def test_allowlist_handles_subdomain_only_for_bare_entries():
    # 'docs.anthropic.com' bare entry must not accidentally match 'anthropic.com'.
    bare = ["docs.anthropic.com"]
    assert allowlist_rank("https://docs.anthropic.com/x", bare) == 0
    assert allowlist_rank("https://www.anthropic.com/news", bare) == -1


# ── Term extraction ─────────────────────────────────────────────────────────


def test_extract_candidate_terms_camelcase():
    terms = extract_candidate_terms("Try the new MemoryTool and ManagedAgents features.")
    assert "MemoryTool" in terms
    assert "ManagedAgents" in terms


def test_extract_candidate_terms_snake_case():
    terms = extract_candidate_terms("Use the new tool_use_v2 API surface.")
    assert any("tool_use" in t.lower() for t in terms)


def test_extract_candidate_terms_filters_stopwords():
    terms = extract_candidate_terms("Anthropic announced something with claude tools today.")
    # 'Anthropic' is in the stopword list (lowercased) so should be filtered.
    assert all(t.lower() not in {"anthropic", "claude", "tool", "tools"} for t in terms)


# ── Candidate URL generation ─────────────────────────────────────────────────


def test_candidate_urls_includes_docs_anthropic_for_term():
    urls = candidate_urls_for_term("MemoryTool", allowlist=ALLOW)
    assert any("docs.anthropic.com" in u and "memorytool" in u.lower() for u in urls)


def test_candidate_urls_includes_changelog():
    urls = candidate_urls_for_term("AnyTerm", allowlist=ALLOW)
    assert any("CHANGELOG.md" in u for u in urls)


def test_candidate_urls_filtered_by_allowlist():
    """A term should not produce URLs outside the supplied allowlist."""
    minimal_allow = ["docs.anthropic.com"]
    urls = candidate_urls_for_term("Skills", allowlist=minimal_allow)
    for url in urls:
        assert "docs.anthropic.com" in url


# ── End-to-end execute_research with stubbed fetcher ─────────────────────────


def test_build_research_request_returns_none_when_not_triggered():
    req = build_research_request(
        post={"id": "1", "tier": 1, "links": [], "text": "noise"},
        classifier_result=None,
        existing_entity_names=None,
    )
    assert req is None


def test_build_research_request_returns_request_when_triggered():
    req = rg.build_research_request(
        post={
            "id": "1",
            "tier": 2,
            "links": [],
            "text": "Announcing the new SuperFeature for Claude Code.",
        },
        classifier_result=None,
        existing_entity_names=None,
    )
    # Use the module-level class symbol so this test is robust against
    # importlib.reload() in earlier tests reassigning the class object.
    assert isinstance(req, rg.ResearchRequest)
    assert req.tier == 2
    assert rg.TriggerReason.NO_LINKS in req.reasons


def test_execute_research_uses_fetcher_and_records_results(monkeypatch, tmp_path: Path):
    fetched_urls: list[str] = []

    def stub_fetch(url, category, output_dir, *, timeout):
        fetched_urls.append(url)
        if "docs.anthropic.com" in url:
            return {"ok": True, "path": str(output_dir / "stub.md"), "method": "stub", "chars": 1234}
        return {"ok": False, "error": "stubbed_failure"}

    monkeypatch.setattr(rg, "fetch_url_content", stub_fetch)

    req = ResearchRequest(
        post_id="t1",
        tier=2,
        terms=("MemoryTool",),
        reasons=(TriggerReason.NO_LINKS,),
    )
    out = execute_research(req, output_dir=tmp_path / "research")
    assert isinstance(out, ResearchResult)
    assert out.fetched_count >= 1
    assert any("docs.anthropic.com" in s.url for s in out.sources if s.fetched)
    # Each source must carry a defined allowlist_rank, never lying about origin.
    for s in out.sources:
        assert s.allowlist_rank >= 0  # all candidates from candidate_urls are allowlisted


def test_execute_research_handles_empty_allowlist(tmp_path: Path):
    """A lane with no allowlist returns a skip reason, never a fabricated source."""

    class _StubLane:
        research_allowlist: list[str] = []

    req = ResearchRequest(
        post_id="t1",
        tier=2,
        terms=("MemoryTool",),
        reasons=(TriggerReason.NO_LINKS,),
    )
    out = execute_research(req, output_dir=tmp_path / "research", lane=_StubLane())
    assert out.fetched_count == 0
    assert out.skipped_reason == "empty_allowlist"


def test_research_source_to_enrichment_record_round_trip():
    src = rg.ResearchSource(
        url="https://docs.anthropic.com/x",
        domain="docs.anthropic.com",
        allowlist_rank=0,
        fetched=True,
        content_path="/tmp/x.md",
        content_chars=100,
    )
    record = src.to_enrichment_record()
    assert str(record.url) == src.url
    assert record.fetch_status == "fetched"
    assert record.content_chars == 100
    assert record.worth_fetching is True


def test_skipped_research_source_round_trip():
    src = rg.ResearchSource(
        url="https://docs.anthropic.com/missing",
        domain="docs.anthropic.com",
        allowlist_rank=0,
        fetched=False,
        skip_reason="HTTP 404",
    )
    record = src.to_enrichment_record()
    assert record.fetch_status == "skipped"
    assert "HTTP 404" in record.skip_reason


# ── @-mention / hashtag filtering (regression for 2026-05-17 hallucination) ──


def test_extract_candidate_terms_strips_at_mentions():
    """@OniricSunset in a tweet must never produce a candidate term.

    Before the fix the CamelCase regex consumed handles like
    ``OniricSunset`` because ``\\b`` doesn't include ``@``, and downstream
    URL synthesis emitted ``docs.anthropic.com/en/docs/oniricsunset``.
    """
    text = "@OniricSunset @ClaudeDevs Fix going out tomorrow."
    terms = extract_candidate_terms(text)
    lowered = {t.lower() for t in terms}
    assert "oniricsunset" not in lowered
    assert "claudedevs" not in lowered


def test_extract_candidate_terms_strips_hashtags():
    text = "#ClaudeCode is shipping #AgentSDK with #ToolStreaming"
    terms = extract_candidate_terms(text)
    lowered = {t.lower() for t in terms}
    assert "claudecode" not in lowered
    assert "agentsdk" not in lowered
    assert "toolstreaming" not in lowered


def test_extract_candidate_terms_strips_urls():
    text = "Read the docs at https://example.com/SomeBigPath for details"
    terms = extract_candidate_terms(text)
    # The path slug must never become a research term.
    assert "SomeBigPath" not in terms


def test_extract_candidate_terms_respects_excluded_handles():
    """Bare CamelCase matching a polled handle must be excluded."""
    text = "Bcherny shipped a new release with NewCoolFeature"
    terms = extract_candidate_terms(text, excluded_handles={"bcherny"})
    lowered = {t.lower() for t in terms}
    assert "bcherny" not in lowered
    # Legit features still pass through.
    assert any(t == "NewCoolFeature" for t in terms)


def test_extract_candidate_terms_keeps_legitimate_camelcase():
    """Real product terms must still be extracted after handle stripping."""
    text = "@bcherny We shipped MemoryTool and FileExplorer for the Sonnet release"
    terms = extract_candidate_terms(text)
    assert "MemoryTool" in terms
    assert "FileExplorer" in terms
    assert "bcherny" not in {t.lower() for t in terms}


def test_build_research_request_excludes_handles_from_synthesis(monkeypatch):
    """The handle-stripping must flow through build_research_request."""
    request = build_research_request(
        post={
            "id": "p1",
            "tier": 4,
            "links": [],
            "text": "@OniricSunset @ClaudeDevs Fix going out tomorrow.",
        },
        classifier_result=None,
        existing_entity_names=set(),
        excluded_handles={"bcherny", "claudedevs"},
    )
    assert request is not None
    lowered = {t.lower() for t in request.terms}
    assert "oniricsunset" not in lowered
    assert "claudedevs" not in lowered


# ── SPA-404 shell detection ──────────────────────────────────────────────────


def _make_spa_shell_file(tmp_path: Path, name: str = "shell.md") -> Path:
    body = (
        "# Source: https://docs.anthropic.com/en/docs/missing\n"
        "<!DOCTYPE html><html class='h-screen' data-theme=\"claude\" data-mode='auto'>"
        "<head><link rel='stylesheet' href='/_next/static/css/foo.css'/>"
        "</head><body></body></html>\n"
    )
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_is_spa_404_shell_detects_docs_anthropic_shell(tmp_path):
    path = _make_spa_shell_file(tmp_path)
    assert rg._is_spa_404_shell(content_path=str(path), content_chars=path.stat().st_size)


def test_is_spa_404_shell_accepts_long_real_docs(tmp_path):
    real_doc = tmp_path / "real.md"
    real_doc.write_text(
        "# Claude Code Release Notes\n\n"
        + "This is a substantial release note describing concrete changes. "
        * 200,
        encoding="utf-8",
    )
    assert not rg._is_spa_404_shell(
        content_path=str(real_doc), content_chars=real_doc.stat().st_size
    )


def test_is_spa_404_shell_handles_missing_file(tmp_path):
    assert not rg._is_spa_404_shell(content_path="", content_chars=1000)
    assert not rg._is_spa_404_shell(content_path=str(tmp_path / "nope.md"), content_chars=1000)


# ── Raw-HTML dump detection (regression for 200KB httpx fallback bug) ────────


def test_is_spa_404_shell_detects_large_raw_html_dump(tmp_path):
    """200KB raw HTML — what httpx writes when defuddle is unavailable."""
    path = tmp_path / "dump.md"
    body = (
        "# Source: https://docs.anthropic.com/en/release-notes/claude-code\n\n"
        "<!DOCTYPE html>\n"
        "<html lang='en' data-color-mode='auto'>\n"
        "<head>\n"
        + "\n".join(
            f"<link rel='stylesheet' href='https://example.com/static/{i}.css'/>"
            for i in range(200)
        )
        + "\n</head>\n<body>"
        + ("legitimate prose " * 5000)
        + "</body></html>\n"
    )
    path.write_text(body, encoding="utf-8")
    assert rg._is_spa_404_shell(content_path=str(path), content_chars=path.stat().st_size)


def test_is_spa_404_shell_detects_html_via_tag_density(tmp_path):
    """No DOCTYPE but a flood of <link>/<script>/<meta> tags up top."""
    path = tmp_path / "dense.md"
    header_tags = "\n".join(
        f"<link rel='stylesheet' href='/a/{i}.css'/>" for i in range(20)
    )
    body = f"# Source: https://github.com/x/y\n\n{header_tags}\n\n" + ("a " * 2000)
    path.write_text(body, encoding="utf-8")
    assert rg._is_spa_404_shell(content_path=str(path), content_chars=path.stat().st_size)


def test_is_spa_404_shell_accepts_legitimate_markdown(tmp_path):
    """Real defuddle-extracted markdown must pass through unmolested."""
    path = tmp_path / "real.md"
    path.write_text(
        "# Source: https://docs.anthropic.com/en/release-notes/claude-code\n\n"
        "## v2.1.139 — 2026-05-11\n\n"
        "- Added the `claude agents` subcommand for session-roster management.\n"
        "- Fixed an issue where the agent panel would lose focus on resize.\n\n"
        + ("Detailed release-note prose. " * 200),
        encoding="utf-8",
    )
    assert not rg._is_spa_404_shell(content_path=str(path), content_chars=path.stat().st_size)


def test_looks_like_raw_html_unit():
    assert rg._looks_like_raw_html("# Source: x\n\n<!DOCTYPE html>\n<html>\n")
    assert rg._looks_like_raw_html("<HTML lang='en'><head><title>x</title></head>")
    # Markdown that mentions ``<html>`` only inline (backticked, not the literal
    # opening tag form ``<html `` or ``<html>`` at line start) should not flag.
    assert not rg._looks_like_raw_html(
        "# Real Docs\n\nSome prose about the HTML5 spec; nothing to see here.\n"
    )
    assert not rg._looks_like_raw_html("")


def test_execute_research_drops_spa_shell_sources(tmp_path, monkeypatch):
    """When fetch returns a SPA shell, the source must be marked skipped."""
    request = ResearchRequest(
        post_id="p1",
        tier=4,
        terms=("CoolFeature",),
        reasons=(TriggerReason.UNKNOWN_TERM,),
    )

    written_files: list[Path] = []

    def fake_fetch_url_content(url, category, output_dir, *, timeout=15):  # noqa: ARG001
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = _make_spa_shell_file(output_dir, name=f"shell_{len(written_files)}.md")
        written_files.append(path)
        return {"ok": True, "path": str(path), "chars": path.stat().st_size}

    monkeypatch.setattr(rg, "fetch_url_content", fake_fetch_url_content)

    out = execute_research(request, output_dir=tmp_path, max_sources=2)
    for source in out.sources:
        assert source.fetched is False
        assert source.skip_reason == "spa_404_shell"
    # The shell artifacts must be cleaned off disk so the vault writer can't
    # pick them up later.
    for path in written_files:
        assert not path.exists()
