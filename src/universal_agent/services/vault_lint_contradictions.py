"""Vault contradictions sweep (PR 13).

Periodic (monthly) lint pass that scans entity/concept pages for
potential contradictions between pages and writes a report to
`vault/lint/contradictions-YYYY-MM-DD.md`. Reports only — never modifies
pages. Per the v2 design (§4.3), contradictions are expected to be rare
because the Memex pass is append-dominant; the sweep exists as a safety
net for the rare REVISE that actually conflicts with a related page.

Two layers:

  1. Heuristic candidate generation. Pairs entity/concept pages whose
     tags or stems overlap (cheap deterministic; finds the probable
     suspects without LLM call).
  2. Pair analysis. For each candidate pair, look for textual conflict
     markers ("however", "but", "supersedes", "deprecated") + content
     overlap on key terms. Optional LLM upgrade path is left as a
     follow-up PR — this module ships the heuristic floor so the
     sweep produces useful output without LLM access.

Output goes to `lint/contradictions-YYYY-MM-DD.md`. Existing
`lint_vault()` (wiki/core.py) handles structural lint and stays
unchanged.

See docs/proactive_signals/claudedevs_intel_v2_design.md §4.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

logger = logging.getLogger(__name__)


# Words that signal an authored conflict between two pages OR within
# a page's prose. Conservative — we want false positives reviewable by
# a human, not silently flagged as actionable.
CONFLICT_MARKERS = (
    "however",
    "but ",
    "in contrast",
    "contradicts",
    "contradicting",
    "supersedes",
    "superseded",
    "deprecated",
    "obsolete",
    "replaced by",
    "no longer",
    "previously",
    "old api",
    "old behavior",
)


# ── Page records ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LintPage:
    rel_path: str
    abs_path: Path
    kind: str  # "entity" | "concept"
    title: str
    tags: tuple[str, ...]
    body: str

    @property
    def stem(self) -> str:
        return self.abs_path.stem


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, parts[2].lstrip("\n")


_KIND_DIR = {"entity": "entities", "concept": "concepts"}


def _load_pages(vault_path: Path, *, kinds: Iterable[str] = ("entity", "concept")) -> list[LintPage]:
    pages: list[LintPage] = []
    for kind in kinds:
        directory = vault_path / _KIND_DIR.get(kind, kind + "s")
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                continue
            meta, body = _split_frontmatter(raw)
            tags_raw = meta.get("tags") or []
            if not isinstance(tags_raw, list):
                tags_raw = []
            tags = tuple(str(t).strip().lower() for t in tags_raw if str(t).strip())
            title = str(meta.get("title") or path.stem.replace("-", " ").title())
            rel = path.relative_to(vault_path).as_posix()
            pages.append(
                LintPage(
                    rel_path=rel,
                    abs_path=path,
                    kind=kind,
                    title=title,
                    tags=tags,
                    body=body,
                )
            )
    return pages


# ── Candidate pair generation ───────────────────────────────────────────────


@dataclass(frozen=True)
class PairCandidate:
    """Two pages that look related enough to inspect for contradiction."""

    a: LintPage
    b: LintPage
    overlap_reasons: tuple[str, ...]


def _shared_tags(a: LintPage, b: LintPage) -> set[str]:
    common = set(a.tags) & set(b.tags)
    # Drop overly generic tags — these would pair every page with every page.
    too_generic = {"claude-code", "claude-devs", "external", ""}
    return common - too_generic


def _stem_overlap(a: LintPage, b: LintPage) -> bool:
    """Stem of one page appears as a word in the other's title."""
    a_stem_words = set(re.split(r"[-_]", a.stem.lower()))
    b_stem_words = set(re.split(r"[-_]", b.stem.lower()))
    a_title_words = set(re.findall(r"[a-z0-9]+", a.title.lower()))
    b_title_words = set(re.findall(r"[a-z0-9]+", b.title.lower()))
    return bool((a_stem_words & b_title_words) | (b_stem_words & a_title_words))


def find_candidate_pairs(pages: list[LintPage]) -> list[PairCandidate]:
    """Pair pages whose tags or stems overlap. Cheap deterministic prefilter."""
    out: list[PairCandidate] = []
    for i, a in enumerate(pages):
        for b in pages[i + 1 :]:
            if a.kind != b.kind:
                continue  # Only compare entity-with-entity, concept-with-concept.
            reasons: list[str] = []
            shared = _shared_tags(a, b)
            if shared:
                reasons.append(f"shared_tags:{','.join(sorted(shared))}")
            if _stem_overlap(a, b):
                reasons.append("stem_overlap")
            if reasons:
                out.append(PairCandidate(a=a, b=b, overlap_reasons=tuple(reasons)))
    return out


# ── Pair analysis (heuristic) ───────────────────────────────────────────────


@dataclass(frozen=True)
class ContradictionFinding:
    pair: PairCandidate
    markers_in_a: tuple[str, ...]
    markers_in_b: tuple[str, ...]
    severity: str  # "low" | "medium" | "high"

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_a": self.pair.a.rel_path,
            "page_b": self.pair.b.rel_path,
            "title_a": self.pair.a.title,
            "title_b": self.pair.b.title,
            "overlap_reasons": list(self.pair.overlap_reasons),
            "markers_in_a": list(self.markers_in_a),
            "markers_in_b": list(self.markers_in_b),
            "severity": self.severity,
        }


def _find_markers(body: str) -> list[str]:
    lower = body.lower()
    return sorted({m for m in CONFLICT_MARKERS if m in lower})


def _classify_severity(markers_a: list[str], markers_b: list[str]) -> str:
    """Severity based on which markers appear and how concentrated."""
    high_signal_markers = {"contradicts", "supersedes", "superseded", "replaced by", "no longer"}
    if any(m in high_signal_markers for m in markers_a) or any(m in high_signal_markers for m in markers_b):
        return "high"
    total = len(markers_a) + len(markers_b)
    if total >= 3:
        return "medium"
    return "low"


def analyze_pair(pair: PairCandidate) -> ContradictionFinding | None:
    """Heuristic check for contradiction signals.

    Returns None if neither page carries any conflict markers — pairs
    that overlap topically but read as consistent are common (related
    concepts, sibling entities) and shouldn't appear in the report.
    """
    markers_a = _find_markers(pair.a.body)
    markers_b = _find_markers(pair.b.body)
    if not markers_a and not markers_b:
        return None
    return ContradictionFinding(
        pair=pair,
        markers_in_a=tuple(markers_a),
        markers_in_b=tuple(markers_b),
        severity=_classify_severity(markers_a, markers_b),
    )


# ── Sweep orchestration ─────────────────────────────────────────────────────


@dataclass
class ContradictionReport:
    vault_path: str
    generated_at: str
    pages_scanned: int
    candidate_pair_count: int
    findings: list[ContradictionFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vault_path": self.vault_path,
            "generated_at": self.generated_at,
            "pages_scanned": self.pages_scanned,
            "candidate_pair_count": self.candidate_pair_count,
            "finding_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_contradiction_sweep(vault_path: Path) -> ContradictionReport:
    """Walk entity + concept pages; flag suspect pairs."""
    if not vault_path.exists():
        return ContradictionReport(
            vault_path=str(vault_path),
            generated_at=_now_iso(),
            pages_scanned=0,
            candidate_pair_count=0,
            findings=[],
        )
    pages = _load_pages(vault_path)
    pairs = find_candidate_pairs(pages)
    findings: list[ContradictionFinding] = []
    for pair in pairs:
        finding = analyze_pair(pair)
        if finding is not None:
            findings.append(finding)
    return ContradictionReport(
        vault_path=str(vault_path),
        generated_at=_now_iso(),
        pages_scanned=len(pages),
        candidate_pair_count=len(pairs),
        findings=findings,
    )


# ── Report writer ──────────────────────────────────────────────────────────


def report_filename_for(date_str: str | None = None) -> str:
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"contradictions-{date_str}.md"


def write_contradiction_report(vault_path: Path, report: ContradictionReport) -> Path:
    """Persist the report to vault/lint/contradictions-YYYY-MM-DD.md."""
    lint_dir = vault_path / "lint"
    lint_dir.mkdir(parents=True, exist_ok=True)
    target = lint_dir / report_filename_for()
    lines = [
        "# Vault Contradictions Sweep",
        "",
        f"- vault: `{report.vault_path}`",
        f"- generated_at: `{report.generated_at}`",
        f"- pages_scanned: {report.pages_scanned}",
        f"- candidate_pair_count: {report.candidate_pair_count}",
        f"- finding_count: {len(report.findings)}",
        "",
    ]
    if not report.findings:
        lines.extend(
            [
                "_No contradiction candidates detected this pass._",
                "",
                "This is the expected outcome for an append-dominant vault — the v2 design "
                "(§4.2) anticipates ~5% REVISE rate. If this report is empty for many "
                "consecutive months while the vault is growing, the sweep is doing its job.",
            ]
        )
    else:
        # Group findings by severity for review priority.
        for severity in ("high", "medium", "low"):
            severity_findings = [f for f in report.findings if f.severity == severity]
            if not severity_findings:
                continue
            lines.append(f"## Severity: `{severity}` ({len(severity_findings)})")
            lines.append("")
            for finding in severity_findings:
                lines.append(
                    f"### `{finding.pair.a.rel_path}` ↔ `{finding.pair.b.rel_path}`"
                )
                lines.append("")
                lines.append(f"- titles: \"{finding.pair.a.title}\" vs \"{finding.pair.b.title}\"")
                lines.append(f"- overlap_reasons: `{', '.join(finding.pair.overlap_reasons)}`")
                if finding.markers_in_a:
                    lines.append(f"- markers in A: `{', '.join(finding.markers_in_a)}`")
                if finding.markers_in_b:
                    lines.append(f"- markers in B: `{', '.join(finding.markers_in_b)}`")
                lines.append("")
        lines.append(
            "_Reports only — review pages by hand to decide whether the markers indicate a real "
            "contradiction. The Memex pass should NOT auto-fix these._"
        )
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target
