"""Research grounding subagent.

When a ClaudeDevs tweet (or any ingest) is about a real feature but the
linked sources are thin, missing, or absent, this module reaches into a
controlled allowlist of official sources to ground the analysis. The
classifier downstream consumes the grounding sources just like normal
linked sources from `csi_url_judge`.

Three core invariants per the v2 design (§6.3):

1. Allowlist priority — official Anthropic sources are searched first.
   General web is the last-resort fallback and is marked as such in
   provenance.
2. Tier gate — research only fires for tier >= 2 posts. Noise tweets
   never trigger spending.
3. No invention — when no grounding source is found, the function
   returns an empty list with a documented reason rather than fabricating
   content. Cody and Simone downstream are explicitly told never to
   invent API surface; this module honors the same contract.

PR 3 ships the gate and the deterministic-URL fetcher. LLM-driven
"which URL on docs.anthropic.com would describe X" is an upgrade path
for a follow-up — the gate and allowlist already work without it.

See docs/proactive_signals/claudedevs_intel_v2_design.md §6.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from universal_agent.services.csi_url_judge import EnrichmentRecord, fetch_url_content
from universal_agent.services.intel_lanes import (
    CLAUDE_CODE_LANE_KEY,
    LaneConfig,
    get_lane,
)

logger = logging.getLogger(__name__)


# Tier filter — same convention used elsewhere in CSI. Anything < TIER_GATE
# never triggers research. Tier 1 = noise, Tier 2 = kb_update,
# Tier 3 = strategic_follow_up, Tier 4 = demo_task.
DEFAULT_TIER_GATE = 2


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def tier_gate() -> int:
    """Configured tier floor. Override via UA_CSI_RESEARCH_TIER_GATE."""
    return _env_int("UA_CSI_RESEARCH_TIER_GATE", DEFAULT_TIER_GATE)


class TriggerReason(str, Enum):
    NO_LINKS = "no_links_in_post"
    THIN_LINKED_SOURCES = "linked_sources_thin"
    UNKNOWN_TERM = "term_not_in_vault"
    OPERATOR_FORCE = "operator_forced"


@dataclass(frozen=True)
class ResearchRequest:
    """A single research grounding request derived from a post."""

    post_id: str
    tier: int
    terms: tuple[str, ...]
    reasons: tuple[TriggerReason, ...]
    lane_slug: str = CLAUDE_CODE_LANE_KEY


@dataclass(frozen=True)
class ResearchSource:
    """One grounded source fetched from the allowlist (or skipped)."""

    url: str
    domain: str
    allowlist_rank: int  # lower = higher trust; -1 means general-web fallback
    fetched: bool
    content_path: str = ""
    content_chars: int = 0
    skip_reason: str = ""

    def to_enrichment_record(self) -> EnrichmentRecord:
        """Adapt to the shape the existing classifier consumes."""
        return EnrichmentRecord(
            url=self.url,
            category="documentation" if self.allowlist_rank >= 0 else "other",
            worth_fetching=True,
            reasoning=(
                f"research_grounded:rank={self.allowlist_rank}" if self.fetched
                else f"research_grounded_skipped:{self.skip_reason}"
            ),
            fetch_status="fetched" if self.fetched else "skipped",
            content_path=self.content_path,
            content_chars=self.content_chars,
            skip_reason=self.skip_reason,
        )


@dataclass(frozen=True)
class ResearchResult:
    """Aggregate output for one research request."""

    request: ResearchRequest
    sources: tuple[ResearchSource, ...] = field(default=tuple())
    skipped_reason: str = ""

    @property
    def fetched_count(self) -> int:
        return sum(1 for s in self.sources if s.fetched)


# ── Allowlist matching ───────────────────────────────────────────────────────

def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def allowlist_rank(url: str, allowlist: list[str]) -> int:
    """Return the priority index of a URL within the allowlist.

    Lower index = higher priority. Returns -1 if no entry matches (the
    general-web fallback bucket).
    """
    domain = _domain_of(url)
    path = urlparse(url).path or ""
    for index, pattern in enumerate(allowlist):
        pattern = pattern.strip().lower()
        if not pattern:
            continue
        # Allow either bare domain ("docs.anthropic.com") or domain+path
        # ("github.com/anthropics") forms.
        if "/" in pattern:
            host_part, _, path_part = pattern.partition("/")
            if domain == host_part and (
                path.lower().startswith("/" + path_part.rstrip("/"))
                or path.lower().rstrip("/") == "/" + path_part.rstrip("/")
            ):
                return index
        else:
            if domain == pattern or domain.endswith("." + pattern):
                return index
    return -1


def is_allowed(url: str, allowlist: list[str]) -> bool:
    return allowlist_rank(url, allowlist) >= 0


# ── Trigger decision ─────────────────────────────────────────────────────────

# Lightweight regex for "named feature / SDK term" extraction. Heuristic, not
# exhaustive — captures CamelCase and snake_case identifiers that are likely
# feature names. Real classification still belongs to the LLM classifier.
_TERM_PATTERN = re.compile(r"\b([A-Z][a-zA-Z0-9_]{2,}|[a-z][a-zA-Z0-9_]+_[a-zA-Z0-9_]+)\b")


def extract_candidate_terms(text: str) -> list[str]:
    """Extract probable feature / SDK terms from a post for grounding lookup.

    Conservative: prefers CamelCase identifiers and snake_case identifiers.
    Plain English words are filtered out via STOPWORDS.
    """
    if not text:
        return []
    candidates = _TERM_PATTERN.findall(text)
    # De-duplicate preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for term in candidates:
        if len(term) < 3:
            continue
        if term.lower() in _TERM_STOPWORDS:
            continue
        if term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


_TERM_STOPWORDS = frozenset(
    {
        "claude",
        "anthropic",
        "agents",
        "agent",
        "tool",
        "tools",
        "this",
        "that",
        "with",
        "from",
        "into",
        "what",
        "when",
        "have",
        "https",
        "http",
        "url",
        "post",
        "pull",
        "push",
        "github",
        "discord",
        "twitter",
        "reddit",
    }
)


def should_trigger_research(
    *,
    post: dict[str, Any],
    classifier_result: dict[str, Any] | None = None,
    existing_entity_names: set[str] | None = None,
    operator_force: bool = False,
) -> tuple[bool, list[TriggerReason]]:
    """Apply the §6.3 trigger logic.

    Returns (triggered, reasons). `triggered` is True when at least one
    legitimate reason fires AND the tier gate passes.
    """
    if operator_force:
        # Operator override bypasses tier gate.
        return True, [TriggerReason.OPERATOR_FORCE]

    tier = int(post.get("tier") or (classifier_result or {}).get("tier") or 0)
    if tier < tier_gate():
        return False, []

    reasons: list[TriggerReason] = []
    links = [str(link) for link in (post.get("links") or []) if str(link).strip()]
    if not links:
        reasons.append(TriggerReason.NO_LINKS)

    # "Thin sources" signal — the classifier is expected to mark this when
    # the linked content is short/uninformative.
    if classifier_result and bool(classifier_result.get("linked_sources_thin")):
        reasons.append(TriggerReason.THIN_LINKED_SOURCES)

    # Unknown-term signal — any candidate term that isn't already an entity
    # in the vault.
    if existing_entity_names is not None:
        terms = extract_candidate_terms(str(post.get("text") or ""))
        normalized_existing = {n.lower() for n in existing_entity_names}
        unknown = [t for t in terms if t.lower() not in normalized_existing]
        if unknown:
            reasons.append(TriggerReason.UNKNOWN_TERM)

    return bool(reasons), reasons


def build_research_request(
    *,
    post: dict[str, Any],
    classifier_result: dict[str, Any] | None,
    existing_entity_names: set[str] | None,
    operator_force: bool = False,
    lane_slug: str = CLAUDE_CODE_LANE_KEY,
) -> ResearchRequest | None:
    """Create a ResearchRequest if research should fire; else None."""
    triggered, reasons = should_trigger_research(
        post=post,
        classifier_result=classifier_result,
        existing_entity_names=existing_entity_names,
        operator_force=operator_force,
    )
    if not triggered:
        return None
    terms = tuple(extract_candidate_terms(str(post.get("text") or "")))
    return ResearchRequest(
        post_id=str(post.get("id") or "").strip(),
        tier=int(post.get("tier") or (classifier_result or {}).get("tier") or 0),
        terms=terms,
        reasons=tuple(reasons),
        lane_slug=lane_slug,
    )


# ── Candidate URL generation ────────────────────────────────────────────────
#
# Deterministic guesses for each term. Real "search docs.anthropic.com for
# this term" would be an LLM call or a search-API call; we ship deterministic
# guesses now so the gate + fetch path is exercised end-to-end. LLM-driven
# discovery is the upgrade path.

def candidate_urls_for_term(term: str, *, allowlist: list[str]) -> list[str]:
    """Generate deterministic candidate URLs for a term, restricted to the allowlist."""
    slug_dash = re.sub(r"[^a-zA-Z0-9]+", "-", term).strip("-").lower()
    slug_under = re.sub(r"[^a-zA-Z0-9]+", "_", term).strip("_").lower()

    raw_candidates: list[str] = []
    if "docs.anthropic.com" in [a.split("/", 1)[0].lower() for a in allowlist]:
        raw_candidates.extend(
            [
                f"https://docs.anthropic.com/en/docs/{slug_dash}",
                f"https://docs.anthropic.com/en/docs/agents-and-tools/{slug_dash}",
                f"https://docs.anthropic.com/en/release-notes/claude-code",
            ]
        )
    if any(a.startswith("github.com/anthropics") for a in allowlist):
        raw_candidates.extend(
            [
                f"https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md",
                f"https://github.com/anthropics/claude-agent-sdk-python",
                f"https://github.com/anthropics/claude-agent-sdk-typescript",
            ]
        )
    if any(a.startswith("anthropic.com/news") for a in allowlist):
        raw_candidates.append(f"https://www.anthropic.com/news")

    # Dedupe, preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for url in raw_candidates:
        if url in seen:
            continue
        if not is_allowed(url, allowlist):
            continue
        seen.add(url)
        out.append(url)
    return out


# ── Fetch ────────────────────────────────────────────────────────────────────


def execute_research(
    request: ResearchRequest,
    *,
    output_dir: Path,
    timeout: int = 15,
    lane: LaneConfig | None = None,
    max_sources: int = 6,
) -> ResearchResult:
    """Execute one research request: generate candidate URLs and fetch the allowed ones.

    The actual content fetching reuses `csi_url_judge.fetch_url_content`
    (defuddle → httpx → github API), so we get the same caps and behavior
    as normal linked-source fetching including DOC_STORAGE_MAX_CHARS.
    """
    if lane is None:
        try:
            lane = get_lane(request.lane_slug)
        except KeyError:
            return ResearchResult(
                request=request,
                skipped_reason=f"lane_unknown:{request.lane_slug}",
            )
    allowlist = list(lane.research_allowlist or [])
    if not allowlist:
        return ResearchResult(
            request=request,
            skipped_reason="empty_allowlist",
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[str] = []
    for term in request.terms or ():
        candidates.extend(candidate_urls_for_term(term, allowlist=allowlist))
    # Always add the allowlist top entries even if no term — useful for the
    # NO_LINKS trigger path.
    if not candidates and request.reasons:
        for entry in allowlist[:3]:
            host = entry.split("/", 1)[0]
            candidates.append(f"https://{host}")

    # Dedupe.
    seen: set[str] = set()
    deduped: list[str] = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    candidates = deduped[:max_sources]

    sources: list[ResearchSource] = []
    for url in candidates:
        rank = allowlist_rank(url, allowlist)
        try:
            result = fetch_url_content(
                url,
                category="documentation",
                output_dir=output_dir,
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning("research grounding fetch raised for %s: %s", url, exc)
            result = {"ok": False, "error": str(exc)}

        if result.get("ok"):
            sources.append(
                ResearchSource(
                    url=url,
                    domain=_domain_of(url),
                    allowlist_rank=rank,
                    fetched=True,
                    content_path=str(result.get("path", "")),
                    content_chars=int(result.get("chars", 0)),
                )
            )
        else:
            sources.append(
                ResearchSource(
                    url=url,
                    domain=_domain_of(url),
                    allowlist_rank=rank,
                    fetched=False,
                    skip_reason=str(result.get("error") or "fetch_failed"),
                )
            )

    return ResearchResult(request=request, sources=tuple(sources))
