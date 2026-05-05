"""Tests for the vault contradictions sweep (PR 13)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from universal_agent.services.vault_lint_contradictions import (
    CONFLICT_MARKERS,
    ContradictionFinding,
    ContradictionReport,
    LintPage,
    PairCandidate,
    _classify_severity,
    _find_markers,
    _load_pages,
    _shared_tags,
    _stem_overlap,
    analyze_pair,
    find_candidate_pairs,
    report_filename_for,
    run_contradiction_sweep,
    write_contradiction_report,
)


# ── Page loader ─────────────────────────────────────────────────────────────


def _write_entity(vault: Path, slug: str, *, title: str, body: str, tags: list[str]) -> Path:
    entities = vault / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    path = entities / f"{slug}.md"
    fm = yaml.safe_dump({"title": title, "tags": tags, "kind": "entity"}, sort_keys=False).strip()
    path.write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")
    return path


def _write_concept(vault: Path, slug: str, *, title: str, body: str, tags: list[str]) -> Path:
    concepts = vault / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    path = concepts / f"{slug}.md"
    fm = yaml.safe_dump({"title": title, "tags": tags, "kind": "concept"}, sort_keys=False).strip()
    path.write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")
    return path


def test_load_pages_reads_entities_and_concepts(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_entity(vault, "skills", title="Skills", body="Body A.", tags=["claude-code"])
    _write_concept(vault, "caching", title="Caching", body="Body B.", tags=["claude-code"])
    pages = _load_pages(vault)
    kinds = {p.kind for p in pages}
    assert kinds == {"entity", "concept"}
    titles = {p.title for p in pages}
    assert titles == {"Skills", "Caching"}


def test_load_pages_returns_empty_for_missing_dirs(tmp_path: Path):
    assert _load_pages(tmp_path / "no-vault") == []


def test_load_pages_handles_missing_frontmatter(tmp_path: Path):
    vault = tmp_path / "vault"
    entities = vault / "entities"
    entities.mkdir(parents=True)
    (entities / "raw.md").write_text("# Raw\n\nNo frontmatter here.\n", encoding="utf-8")
    pages = _load_pages(vault)
    assert len(pages) == 1
    assert pages[0].tags == ()


# ── Pair candidate generation ───────────────────────────────────────────────


def test_shared_tags_filters_too_generic():
    a = LintPage(rel_path="x", abs_path=Path("x"), kind="entity", title="X", tags=("claude-code", "foo"), body="")
    b = LintPage(rel_path="y", abs_path=Path("y"), kind="entity", title="Y", tags=("claude-code", "foo"), body="")
    shared = _shared_tags(a, b)
    # 'claude-code' is too generic; 'foo' survives.
    assert "claude-code" not in shared
    assert "foo" in shared


def test_stem_overlap_detects_word_match():
    a = LintPage(rel_path="x", abs_path=Path("memory-tool.md"), kind="entity", title="Memory Tool", tags=(), body="")
    b = LintPage(rel_path="y", abs_path=Path("memory-cache.md"), kind="entity", title="Memory Cache", tags=(), body="")
    assert _stem_overlap(a, b) is True


def test_stem_overlap_negative_case():
    a = LintPage(rel_path="x", abs_path=Path("skills.md"), kind="entity", title="Skills", tags=(), body="")
    b = LintPage(rel_path="y", abs_path=Path("hooks.md"), kind="entity", title="Hooks", tags=(), body="")
    assert _stem_overlap(a, b) is False


def test_find_candidate_pairs_pairs_within_kind_only(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_entity(vault, "skills", title="Skills", body="text", tags=["plugins"])
    _write_concept(vault, "skills", title="Skills (concept)", body="text", tags=["plugins"])
    pages = _load_pages(vault)
    pairs = find_candidate_pairs(pages)
    # One entity + one concept = no within-kind pair candidates.
    assert pairs == []


def test_find_candidate_pairs_uses_shared_tags(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_entity(vault, "a", title="A", body="text", tags=["plugins", "registry"])
    _write_entity(vault, "b", title="B", body="text", tags=["plugins"])
    _write_entity(vault, "c", title="C", body="text", tags=["unrelated"])
    pages = _load_pages(vault)
    pairs = find_candidate_pairs(pages)
    pair_titles = {tuple(sorted([p.a.title, p.b.title])) for p in pairs}
    assert ("A", "B") in pair_titles
    # A ↔ C and B ↔ C should NOT pair (no shared tags).
    assert ("A", "C") not in pair_titles


def test_find_candidate_pairs_uses_stem_overlap(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_entity(vault, "memory-tool", title="Memory Tool", body="text", tags=[])
    _write_entity(vault, "memory-cache", title="Memory Cache", body="text", tags=[])
    pages = _load_pages(vault)
    pairs = find_candidate_pairs(pages)
    assert len(pairs) >= 1
    assert any("stem_overlap" in p.overlap_reasons for p in pairs)


# ── Marker detection ────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [
    "This API was previously called register_skill(), but is now SkillRegistry.register().",
    "deprecated in 2.1.0",
    "However, the new pattern is preferred.",
    "supersedes the old approach",
])
def test_find_markers_detects_conflict_words(body):
    markers = _find_markers(body)
    assert len(markers) >= 1


def test_find_markers_returns_empty_for_neutral_text():
    assert _find_markers("This feature lets agents register reusable capabilities.") == []


def test_find_markers_returns_sorted_unique():
    body = "however, however, deprecated DEPRECATED"
    markers = _find_markers(body)
    assert markers == sorted(set(markers))


# ── analyze_pair ────────────────────────────────────────────────────────────


def _pair(a_body: str, b_body: str, *, kind: str = "entity") -> PairCandidate:
    a = LintPage(rel_path="a", abs_path=Path("a.md"), kind=kind, title="A", tags=("foo",), body=a_body)
    b = LintPage(rel_path="b", abs_path=Path("b.md"), kind=kind, title="B", tags=("foo",), body=b_body)
    return PairCandidate(a=a, b=b, overlap_reasons=("shared_tags:foo",))


def test_analyze_pair_returns_none_when_no_markers():
    assert analyze_pair(_pair("clean text", "also clean")) is None


def test_analyze_pair_flags_when_marker_present():
    finding = analyze_pair(_pair("This is the new way; the old API is deprecated.", "Standard approach."))
    assert finding is not None
    assert "deprecated" in finding.markers_in_a


def test_analyze_pair_severity_high_for_supersedes():
    finding = analyze_pair(_pair("This pattern supersedes the older approach.", "neutral text"))
    assert finding is not None
    assert finding.severity == "high"


def test_analyze_pair_severity_medium_for_three_or_more_markers():
    # Three low-signal markers but no high-signal — should be medium.
    body = "however, but the docs say previously this was the way."
    finding = analyze_pair(_pair(body, "neutral text"))
    assert finding is not None
    # 'however' + 'but ' + 'previously' = 3 markers, all low-signal → medium
    assert finding.severity == "medium"


def test_analyze_pair_severity_low_for_single_low_signal_marker():
    finding = analyze_pair(_pair("however, the docs say...", "neutral"))
    assert finding is not None
    assert finding.severity == "low"


def test_classify_severity_handles_empty_markers():
    """Defensive: classifier doesn't blow up on empty input."""
    assert _classify_severity([], []) == "low"


# ── End-to-end sweep ────────────────────────────────────────────────────────


def test_run_contradiction_sweep_handles_missing_vault(tmp_path: Path):
    report = run_contradiction_sweep(tmp_path / "no-vault")
    assert report.pages_scanned == 0
    assert report.candidate_pair_count == 0
    assert report.findings == []


def test_run_contradiction_sweep_reports_clean_vault(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_entity(vault, "skills", title="Skills", body="Feature description.", tags=["plugins"])
    _write_entity(vault, "hooks", title="Hooks", body="Another feature.", tags=["execution"])
    report = run_contradiction_sweep(vault)
    assert report.pages_scanned == 2
    assert report.findings == []  # No pair overlap, no contradictions.


def test_run_contradiction_sweep_finds_contradiction(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_entity(
        vault,
        "skills",
        title="Skills",
        body="Use SkillRegistry.register(). The previous register_skill() pattern is deprecated.",
        tags=["plugins"],
    )
    _write_entity(
        vault,
        "skills-quickstart",
        title="Skills Quickstart",
        body="Quickstart uses SkillRegistry.register().",
        tags=["plugins"],
    )
    report = run_contradiction_sweep(vault)
    assert len(report.findings) >= 1
    finding = report.findings[0]
    assert "deprecated" in finding.markers_in_a or "deprecated" in finding.markers_in_b


# ── Report writing ──────────────────────────────────────────────────────────


def test_report_filename_uses_today():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert report_filename_for() == f"contradictions-{today}.md"


def test_report_filename_explicit_date():
    assert report_filename_for("2026-01-15") == "contradictions-2026-01-15.md"


def test_write_report_creates_lint_dir_and_file(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    report = ContradictionReport(
        vault_path=str(vault),
        generated_at="2026-05-05T12:00:00+00:00",
        pages_scanned=5,
        candidate_pair_count=3,
        findings=[],
    )
    target = write_contradiction_report(vault, report)
    assert target.exists()
    assert target.parent.name == "lint"
    text = target.read_text(encoding="utf-8")
    assert "Vault Contradictions Sweep" in text
    assert "pages_scanned: 5" in text
    assert "No contradiction candidates" in text


def test_write_report_groups_by_severity(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()

    def _finding(severity: str) -> ContradictionFinding:
        a = LintPage(rel_path=f"entities/a.md", abs_path=Path("a.md"), kind="entity", title="A", tags=("foo",), body="")
        b = LintPage(rel_path=f"entities/b.md", abs_path=Path("b.md"), kind="entity", title="B", tags=("foo",), body="")
        return ContradictionFinding(
            pair=PairCandidate(a=a, b=b, overlap_reasons=("shared_tags:foo",)),
            markers_in_a=("deprecated",),
            markers_in_b=(),
            severity=severity,
        )

    report = ContradictionReport(
        vault_path=str(vault),
        generated_at="t",
        pages_scanned=2,
        candidate_pair_count=1,
        findings=[_finding("high"), _finding("low"), _finding("medium")],
    )
    target = write_contradiction_report(vault, report)
    text = target.read_text(encoding="utf-8")
    # Severity sections in priority order.
    assert text.index("Severity: `high`") < text.index("Severity: `medium`")
    assert text.index("Severity: `medium`") < text.index("Severity: `low`")


def test_write_report_does_not_error_on_repeat_call(tmp_path: Path):
    """Same-day sweep run twice must not blow up — second call overwrites the first."""
    vault = tmp_path / "vault"
    vault.mkdir()
    report = ContradictionReport(
        vault_path=str(vault), generated_at="t", pages_scanned=0, candidate_pair_count=0, findings=[]
    )
    write_contradiction_report(vault, report)
    target = write_contradiction_report(vault, report)
    assert target.exists()


def test_conflict_markers_constant_includes_critical_terms():
    """Regression guard — these specific markers are the most informative
    signals; dropping any is a behavior change worth catching."""
    must_include = {"deprecated", "supersedes", "no longer", "however"}
    assert must_include.issubset(set(CONFLICT_MARKERS))
