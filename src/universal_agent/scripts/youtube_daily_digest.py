#!/usr/bin/env python3
"""Daily YouTube Digest Engine

This script runs autonomously on a schedule (via UA Cron Service). It:
1. Selects today's dedicated playlist (e.g. "Monday Digest" on Mondays).
2. Extracts transcripts using residential proxies (with graceful fallback).
3. Synthesizes a compressed retelling + meta-analysis via the standard
   Anthropic-compatible ZAI inference path.
4. Saves the markdown artifact to the daily_digests workspace.
5. Emits the digest as a CSI record so it appears in the CSI Feed dashboard
   and can be processed by the proactive signal pipeline.
6. Saves a ranked tutorial-candidate decision artifact.
7. Dispatches the top code-implementation prospects to the YouTube tutorial pipeline.
8. Saves a repopulate pocket for the processed videos.
9. Deletes processed videos from the playlist (clean inbox pattern).

Playlist IDs are stored in Infisical as <DAY>_YT_PLAYLIST:
  MONDAY_YT_PLAYLIST, TUESDAY_YT_PLAYLIST, ..., SUNDAY_YT_PLAYLIST

"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import html as html_module
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
from typing import Any
from urllib import error, request
import uuid

import markdown

# Fix python path for local execution if needed
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from anthropic import AsyncAnthropic

from universal_agent.infisical_loader import (
    initialize_runtime_secrets,
    upsert_infisical_secret,
)
from universal_agent.rate_limiter import ZAIRateLimiter
from universal_agent.services.agentmail_service import AgentMailService
from universal_agent.services.digest_delivery_reminder import (
    send_digest_delivery_reminder,
)
from universal_agent.services.email_tags import ActionTag, KindTag
from universal_agent.services.scratch_publish import (
    publish_html_to_scratch,
    scratch_back_link_html,
)
from universal_agent.services.youtube_playlist_manager import (
    YouTubeAPIError,
    YouTubeOAuthError,
    add_playlist_item,
    create_playlist,
    delete_playlist,
    get_playlist_items,
    get_playlist_metadata,
    remove_playlist_item,
)
from universal_agent.utils.model_resolution import resolve_model
from universal_agent.youtube_ingest import ingest_youtube_transcript

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
POCKET_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Map-reduce pipeline defaults (all overridable via env var)
# ---------------------------------------------------------------------------
# Pipeline selector: "map_reduce" (default, per-video retell + meta-synthesis)
#                    "single_call" (legacy one-LLM-call shape; kept for fallback + A/B)
DIGEST_PIPELINE_DEFAULT = "map_reduce"
# Map-step model defaults. We hardcode glm-4.5-air (haiku-equivalent on Z.AI) for
# the map step so the choice stays explicit and local rather than tracking the
# global haiku tier (haiku -> glm-4.5-air, operator-locked; see
# model_resolution.py:11-30). Probe 2026-05-18 showed glm-4.5-air runs well at
# concurrency 1-3 for digest-shape prompts and ~30% faster per call than
# glm-5-turbo. Both models share the same account-level Fair-Usage-Policy
# throttle (Z.AI error 1313) when concurrency >= 5, so concurrency caps below
# are conservative.
DIGEST_MAP_MODEL_DEFAULT = "glm-4.5-air"
# 3 -> 1 (2026-06-13, storm-avoidance pass). The map step was the worst single
# 429 source in prod (~81% reject over 12h): any concurrency self-overlaps, and
# ZAI Fair-Usage is concurrency-driven — a ZAI call sent while another is in
# flight rejects ~77% vs ~10% with nothing else in flight. The digest is a
# once-daily batch, so sequential (concurrency 1) costs trivial wall-clock and
# eliminates the self-collision. Raise UA_YOUTUBE_DIGEST_MAP_CONCURRENCY only if
# a measured need appears. (A deeper fix — routing the bespoke _zai_call_with_retry
# through the per-tier AIMD limiter — is a separate follow-up.)
DIGEST_MAP_CONCURRENCY_DEFAULT = 1
DIGEST_MAP_TIMEOUT_SECONDS_DEFAULT = 180
DIGEST_MAP_MAX_TOKENS_DEFAULT = 6000  # raised from 4000 alongside the 50% retell target — longest videos in the playlist need ~5300 tokens of headroom
DIGEST_MAP_TRANSCRIPT_CHAR_LIMIT_DEFAULT = 50_000
# Reduce-step model defaults. Reduce sees only titles + per-video classifications
# + thesis lines (no full retellings), so context stays small.
DIGEST_REDUCE_MODEL_TIER_DEFAULT = "opus"  # → glm-5.1 today
DIGEST_REDUCE_TIMEOUT_SECONDS_DEFAULT = 600
DIGEST_REDUCE_MAX_TOKENS_DEFAULT = 8000


@dataclass
class VideoTranscriptPayload:
    """Per-video data passed into both pipelines.

    `transcript_text` is empty when only metadata was available — the map step
    will handle that case by emitting a metadata-only classification.

    `metadata` carries the fields ingest_youtube_transcript already fetches from
    the YouTube Data API (channel, duration, upload_date, …). It's threaded
    through so the per-video email section can show "Channel · 12:34 · 2026-05-19"
    instead of just the bare video ID."""
    video_id: str
    title: str
    transcript_text: str
    is_metadata_only: bool = False
    original_item: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MapResult:
    """Output of a single map-step call (one video → one retell + classification).

    `retries` / `rate_limit_hits` / `last_error_class` are populated for every
    call (success or failure) so the comparison harness can correlate model
    choice + concurrency level with the rate-limit error surface.
    """
    video_id: str
    title: str
    retell_markdown: str
    thesis_line: str
    classification: dict[str, Any]
    error: str | None = None
    elapsed_seconds: float = 0.0
    map_model: str = ""
    retries: int = 0  # number of retry attempts before success (0 if first call worked)
    rate_limit_hits: int = 0  # how many of those retries were due to 429/FUP
    last_error_class: str = ""  # class name of last (or final-failing) exception



SYNTHESIS_PROMPT = """You are an expert technical researcher and knowledge synthesizer.
You are given the transcripts and metadata of several YouTube videos that the user queued up to watch today.
Instead of watching them, the user relies on you to provide a "Compressed Retelling" and "Daily Digest".

For each video, provide:
1. A concise, dense summary of the core thesis and key facts.
2. Any actionable advice or technical insights.
3. A priority ranking (e.g., High/Medium/Low Value) based on the depth of information.
4. A tutorial pipeline decision:
   - code_implementation_prospect=true only when the transcript or metadata shows concrete software/code/build/configuration work that can produce runnable implementation artifacts.
   - code_implementation_prospect=false for concept-only commentary, news, strategy, high-level education, product positioning, interviews, or videos that lack enough concrete implementation steps.
   - concept_only=true when the video is useful for understanding but should not be sent to a code implementation tutorial pipeline.

Sort all video sections from highest value to lowest value.

Finally, provide a "Meta-Synthesis" section at the top that identifies any cross-video themes,
learning insights, or neglected opportunities across the entire playlist.

If a video contains an excellent technical tutorial that should become a runnable code implementation,
call it out explicitly as a "TUTORIAL PIPELINE TRIGGER" candidate. Do not use that trigger for concept-only videos.

After the markdown digest, include exactly one fenced JSON block labeled youtube_digest_decisions.
This JSON block is for automation only and will be stripped from the human-facing email.
The JSON must have this shape:
```youtube_digest_decisions
{
  "ranked_videos": [
    {
      "rank": 1,
      "video_id": "string",
      "title": "string",
      "value_score": 0,
      "value_tier": "high|medium|low",
      "code_implementation_prospect": true,
      "concept_only": false,
      "tutorial_candidate": true,
      "recommended_tutorial_mode": "explainer_plus_code|concept_only|none",
      "evidence_quality": "transcript|metadata_only|mixed",
      "reason": "short reason grounded in the transcript or metadata"
    }
  ]
}
```
Rules for the JSON:
- ranked_videos must be sorted by value_score descending.
- tutorial_candidate may be true only when code_implementation_prospect is true. The automation will dispatch the highest-value code_implementation_prospect videos even if tutorial_candidate is false.
- recommended_tutorial_mode must be explainer_plus_code for dispatched code prospects.
- Use metadata_only evidence_quality when transcript text was unavailable.
- Include every input video exactly once.

Here are the videos:
"""


# ---------------------------------------------------------------------------
# Map-step prompt: produces a per-video retelling + classification.
#
# Output contract (strict):
#   1. Markdown "## <title>" heading
#   2. ### Retelling section (50% length retelling, NOT a summary)
#   3. ### Actionable Insights section (bullets)
#   4. ### Thesis (single short sentence — fed to the reducer for cross-video themes)
#   5. ```per_video_classification JSON fenced block (parsed by code, stripped from email)
#
# The map LLM does NOT see other videos and is told NOT to draw cross-video themes —
# that's the reducer's job.
#
# ===========================================================================
# SCORING RUBRIC ROADMAP (recorded 2026-05-20 after the saturation regression)
# ===========================================================================
# The 2026-05-20 WEDNESDAY dry-run smoke surfaced score saturation: 12 of 29
# videos got value_score=95 from the map LLM. With 8 candidates tied at 95
# and top_n=4, tutorial dispatch becomes effectively arbitrary among ties.
# The discriminator we built (demo-worthiness gate on value_score >= 70) only
# works if scores actually spread across 0-100.
#
# Improvement plan (live tiers vs deferred tiers):
#
#   Tier A — TIGHTEN RUBRIC IN PROMPT (CURRENT STATE)
#     Inline anchored buckets + disqualifier caps + explicit "do not default"
#     guidance directly in this RETELL_PROMPT.  Lowest cost; works within the
#     existing single-call map step.  See the SCORING RUBRIC section below.
#     - 2026-05-20: anchored buckets + caps shipped (killed the 95-clustering
#       where 12/29 videos scored 95).
#     - 2026-05-30: WITHIN-BAND additive scoring added after a NEW saturation
#       point surfaced — the FRIDAY digest scored 6 videos at exactly 85 (top
#       of the 75-89 band), so static top_n=4 dropped two buildable talks by
#       arbitrary tie-order.  Root cause is structural: the map step scores
#       each video IN ISOLATION (no cross-video view), so "spread across the
#       batch" is impossible per-call and coarse bands collapse to round
#       anchors (95, then 85).  Fix: compute the score from additive per-video
#       signals (start at band floor, add for named files/mechanism/code) so
#       two different videos land on different numbers.  Paired with dynamic
#       top_n tie-extension in `_select_tutorial_dispatch_candidates` so any
#       residual ties at the cutoff all dispatch (up to a 2x ceiling) instead
#       of being split arbitrarily.  Measure the 75-89 distribution over the
#       next 1-2 weeks; if it re-clusters, that is the Tier B/C trigger.
#
#   Tier B — EXTRACT RUBRIC TO ITS OWN FILE (deferred)
#     Move the scoring section into `youtube_scoring_rubric.md` in the repo;
#     RETELL_PROMPT includes it verbatim via .format().  Single editable
#     source-of-truth for what scores mean; each rubric change becomes a
#     reviewable PR.  Trigger condition: do Tier A first and measure score
#     distribution across 1-2 weeks before committing to file extraction.
#
#   Tier C — LABELED OUTCOMES STORE (deferred)
#     New schema capturing per-video downstream outcomes:
#       - did Cody produce a runnable demo for this dispatched video?
#         (already available in `manifest.json` + `run_output.txt`)
#       - did the operator find the dispatched video worth watching?
#         (would need a thumbs-up/down surface on the dashboard tile)
#       - did any *rejected* video later turn out to be high-value?
#     Wire `cody-work-evaluator` outcomes into this store.  Trigger: after
#     1-2 weeks of Tier A data shows whether scoring is still saturating.
#
#   Tier D — RUBRIC-TUNER AGENT (deferred, depends on B + C)
#     A weekly Claude Code session that reads the labeled outcomes store,
#     identifies miscalls (high score → useless demo, low score → great
#     video), and proposes rubric edits as a PR for operator review.  THIS
#     is where a CC skill genuinely fits: a meta-skill that tunes the
#     rubric.  NOT a "ranking skill that learns" — the ranking stays a
#     deterministic SDK call; the learning loop is the offline tuner that
#     edits this prompt.
#
# Don't promote a tier without measurement: rubric churn without a baseline
# makes regressions hard to diagnose.  Doc 99 mirrors this roadmap for
# operator visibility.
# ===========================================================================
# ---------------------------------------------------------------------------
RETELL_PROMPT = """You are a technical knowledge synthesizer. You will be given the transcript and metadata for ONE YouTube video. Produce a "Compressed Retelling" that lets the reader skip watching the video while preserving its substance.

REQUIREMENTS:
- Length target: roughly 50% of the original transcript length. Be substantial, NOT a short summary. If the transcript is ~3000 words, your retelling should be ~1500 words. Err toward more detail rather than less — preserve every concrete example, number, named tool, and "this works because" explanation.
- Reproduce the speaker's argument in your own words, preserving the order and structure of their reasoning.
- Preserve all specific examples, numbers, quoted phrases, library/tool names, file paths, code snippets, product names, and "this works because..." explanations. These details are what distinguish a retelling from a summary.
- Do NOT add commentary, opinion, cross-references to other videos, or business context.
- If the transcript text is "[Metadata-only — transcript unavailable]", produce only a one-paragraph note based on the title and stop. Mark the classification's `evidence_quality` as `metadata_only` and `reason` explaining that no transcript was available.

REQUIRED OUTPUT FORMAT (markdown — do not deviate):

## <video title>

**Video ID:** <video_id>

### Retelling
<the 50% length retelling>

### Actionable Insights
- <concrete thing a reader could do>
- <another bullet>

### Thesis
<one short sentence — the core claim or core takeaway of the video, 15-30 words>

```per_video_classification
{
  "video_id": "<video_id>",
  "value_score": <integer 0-100>,
  "value_tier": "<high|medium|low>",
  "code_implementation_prospect": <true|false>,
  "concept_only": <true|false>,
  "evidence_quality": "<transcript|metadata_only|mixed>",
  "reason": "<short reason grounded in the transcript>"
}
```

SCORING RUBRIC — READ THIS CAREFULLY BEFORE ASSIGNING value_score.

`value_score` is a 0-100 integer rating THIS video against an idealized
"buildable technical tutorial." You are scoring ONE video in isolation — you
cannot see the others — so do not try to "spread" scores across a batch.
Instead, COMPUTE the score from the concrete features actually present in the
transcript (see "Within-band scoring" below) rather than parking on a round
band-anchor. Most videos do not earn 90+, and a strong-but-codeless explainer
is NOT automatically an 85. Defaulting to 95 OR 85 because "it's a solid AI
video and has a transcript" is WRONG.

Anchored buckets (pick the band by best fit, then refine WITHIN the band — see
"Within-band scoring" below — so two genuinely different videos rarely land on
the exact same number):

  - 90-100: CONCRETE BUILD TUTORIAL. Contains named files, specific commands,
    visible code or config, and a reproducible setup the reader could follow
    end-to-end.  Examples that earn 90+: "Master 80% of Claude Code with 15
    Things" listing each tool with worked examples; a video that types
    `pip install X` then walks through a specific .py file line-by-line.
    If you can't point to specific code/config artifacts in the transcript,
    it does NOT earn 90+.

  - 75-89: STRONG TECHNICAL EXPLAINER. Named tools, named systems, mechanism
    explanations ("this works because..."), but missing reproducible
    code/config.  A rigorous architecture talk; a hands-on demo that shows
    output without showing the code paths to reproduce it.

  - 60-74: EDUCATIONAL EXPLAINER. Speaker teaches a real technical concept
    with named systems, but no code is shown and no specific implementation
    details are given.  A "how X works" explainer at the conceptual level.

  - 40-59: SURVEY / OVERVIEW / OPINION. Talks ABOUT a technical topic without
    teaching how to do it.  Industry commentary, panel discussions, news
    roundups, "the future of AI" pieces.  Worth watching for context, not
    for implementation.

  - 20-39: SALES / MARKETING / PROMOTIONAL even if AI-themed.  "Make $X with
    AI," "Anyone can start a $1k business," affiliate-link pitches, "this
    one tool changed everything" framing.

  - 0-19: OFF-TOPIC, CLICKBAIT, OR LOW-SIGNAL.  Unrelated to the operator's
    technical domain; pure entertainment; deceptive framing.

WITHIN-BAND SCORING (compute the exact number; do NOT park on a round value
like 85 or 95). Pick the band, START AT ITS FLOOR, and add points only for
concrete signals actually present in the transcript. Never sit at a band's
ceiling unless every listed signal is genuinely present:

  - 90-100 band: start at 90. +3 if named runnable files/commands appear
    verbatim; +3 if the setup is reproducible end-to-end from the transcript
    alone; +4 if it walks specific code paths line-by-line. A talk that only
    *mentions* code without showing it does not clear 90 — it belongs in 75-89.

  - 75-89 band: start at 75. +4 if it names 3+ specific tools/systems/products;
    +5 if it explains MECHANISM ("this works because…"), not just outcomes;
    +5 if it shows partial code/config/architecture (but not reproducible).
    85 is the TOP of this band — reserve it for explainers that are nearly
    buildable, NOT as a default for "solid technical video."

  - 60-74 / 40-59 / 20-39 bands: start at the floor and add up toward the band
    ceiling based on how much concrete, named, technically-actionable substance
    is present versus generic commentary.

DISQUALIFIER CAPS (apply BEFORE the bucket choice; the cap wins):

  - Sales-pitch framing in title OR opening minutes ("Make $X with...",
    "Anyone can start...", "$1M Solo Business," etc.): CAP at 50.  This
    catches the failure mode where a high-energy promotional video about
    AI tooling gets mistaken for a tutorial.

  - View-count or "going viral" focus / influencer-meta content: CAP at 50.

  - Affiliate links or promotional URLs are the dominant call-to-action:
    CAP at 50.

  - Transcript text is "[Metadata-only — transcript unavailable]": CAP at 30
    AND set evidence_quality="metadata_only".  Without a transcript you
    cannot verify any of the above signals, so we score conservatively.

TIER MUST AGREE WITH SCORE:
  - value_tier="high"   requires value_score >= 75
  - value_tier="medium" requires 50 <= value_score < 75
  - value_tier="low"    requires value_score < 50

CODE IMPLEMENTATION PROSPECT:
  - `code_implementation_prospect` is TRUE only if the video would be
    valuable as input to an automated build agent — i.e. contains enough
    concrete instructions (specific tools, commands, config, file paths)
    that an agent could attempt to reproduce the result.  Concept explainers,
    sales pitches, and news roundups are FALSE even if they're high-quality.

INPUT FOLLOWS:
"""


# ---------------------------------------------------------------------------
# Reduce-step prompt: sees only titles + classifications + thesis lines (NOT
# full retellings), produces the meta-synthesis (cross-video themes, learning
# insights, neglected opportunities) and the final ranked `youtube_digest_decisions`
# JSON block. Python code assembles the final markdown by sandwiching the
# retellings between the meta-synthesis and the JSON block.
# ---------------------------------------------------------------------------
REDUCE_PROMPT = """You are a senior technical analyst preparing a "Daily YouTube Digest" for a busy operator. You have ALREADY received per-video retellings from a map step; do NOT re-summarize the videos. Your job is ONLY meta-synthesis across them.

Below is the structured roll-up of every video in today's digest (titles, one-sentence theses, value scores, tiers, evidence quality, and the map-step's reasoning). Use this as your input to spot patterns across videos.

Produce exactly the following markdown, in this order — and NOTHING ELSE (no H1 title, no JSON blocks, no per-video sections):

1. `## Meta-Synthesis: Daily Digest`
2. `### Cross-Video Themes` — 3-7 themes that appear across multiple videos. Each theme is one short paragraph naming the relevant videos.
3. `### Learning Insights` — 2-5 non-obvious technical insights that wouldn't be apparent from any single video.
4. `### Neglected Opportunities` — 1-3 gaps (topics that should have been discussed but weren't).

The H1 title and the ranked-decisions JSON block are constructed deterministically by code from the map-step output — do NOT emit either.

DAY_NAME: {day_name}
DATE: {date_str}
VIDEO COUNT: {video_count}

PER-VIDEO ROLL-UP:
{rollup_json}
"""


# ---------------------------------------------------------------------------
# LLM synthesis
# ---------------------------------------------------------------------------

def _build_anthropic_client_for_zai(timeout_seconds: float) -> AsyncAnthropic:
    """Build a configured AsyncAnthropic client. Routes to Z.AI proxy if the
    configured key looks like a ZAI key; otherwise falls through to the SDK
    default (api.anthropic.com).
    """
    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or ZAI_API_KEY configured")
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "max_retries": 0,
        "timeout": float(timeout_seconds),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    elif os.getenv("ZAI_API_KEY") and api_key == os.getenv("ZAI_API_KEY"):
        kwargs["base_url"] = "https://api.z.ai/api/anthropic"
    return AsyncAnthropic(**kwargs)


async def _aclose_client(client: Any) -> None:
    """Best-effort close of an AsyncAnthropic client *inside its own event loop*.

    Each synthesis step (`_map_retell_videos`, `_reduce_meta_synthesize`,
    `_generate_digest_content_single_call`) builds an AsyncAnthropic client and
    runs under its own `asyncio.run(...)`.  If the client is never closed, its
    underlying httpx connection pool outlives the loop; when GC later finalizes
    the pool — typically during the *next* `asyncio.run(...)` (e.g. the email
    send) — httpx schedules `aclose()` on the already-closed synthesis loop and
    asyncio's default handler logs a spurious

        ERROR - Task exception was never retrieved
        ... RuntimeError: Event loop is closed

    on every digest run.  Closing here, while the loop is still live, prevents
    that.  Best-effort by design: a cleanup failure (or a test double lacking a
    `close` method) must never mask the real synthesis result or exception.
    """
    close = getattr(client, "close", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:  # noqa: BLE001 — cleanup is best-effort, never fatal
        logger.debug("AsyncAnthropic client close failed (non-fatal): %s", exc)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect Z.AI rate-limit / Fair-Usage-Policy responses worth retrying.

    Z.AI returns:
      - HTTP 429 with various messages ("Too many requests", "high concurrency")
      - HTTP 429 with FUP code 1313 ("Fair Usage Policy", "request frequency has been limited")
    All of the above are retry-eligible.
    """
    err_str = str(exc).lower()
    return (
        "429" in err_str
        or "too many requests" in err_str
        or "high concurrency" in err_str
        or "fair usage policy" in err_str
        or "1313" in err_str
    )


async def _zai_call_with_retry(
    *,
    client: AsyncAnthropic,
    model: str,
    prompt: str,
    max_tokens: int,
    context: str,
    max_retries: int = 5,
    temperature: float = 0.4,
    stats_out: dict[str, Any] | None = None,
) -> str:
    """Send one messages.create() call to Z.AI with the global rate limiter
    and a 429/FUP-aware retry loop. Returns the concatenated text response.

    All non-rate-limit errors are re-raised immediately.

    If `stats_out` is provided, it is populated in-place with:
      - retries: int (number of retry attempts; 0 if first call succeeded)
      - rate_limit_hits: int (subset of retries triggered by 429/FUP)
      - last_error_class: str (class name of the most recent exception, "" if none)
    The caller can use these to correlate model+concurrency choices with the
    actual rate-limit error surface, especially in the A/B comparison harness.
    """
    limiter = ZAIRateLimiter.get_instance()
    last_error: Exception | None = None
    rate_limit_hits = 0
    for attempt in range(max_retries):
        async with limiter.acquire(context):
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=[{"role": "user", "content": prompt}],
                )
                await limiter.record_success()
                text = "".join(getattr(block, "text", "") for block in response.content).strip()
                if not text:
                    raise RuntimeError(f"LLM returned an empty response (context={context})")
                if stats_out is not None:
                    stats_out["retries"] = attempt
                    stats_out["rate_limit_hits"] = rate_limit_hits
                    stats_out["last_error_class"] = ""
                return text
            except Exception as exc:
                last_error = exc
                if not _is_rate_limit_error(exc):
                    if stats_out is not None:
                        stats_out["retries"] = attempt
                        stats_out["rate_limit_hits"] = rate_limit_hits
                        stats_out["last_error_class"] = type(exc).__name__
                    raise
                rate_limit_hits += 1
                await limiter.record_429(context)
                if attempt >= max_retries - 1:
                    break
                delay = limiter.get_backoff(attempt)
                logger.warning(
                    "ZAI rate-limited at context=%s; retrying in %.1fs (attempt %d/%d): %s",
                    context,
                    delay,
                    attempt + 1,
                    max_retries,
                    str(exc)[:200],
                )
                await asyncio.sleep(delay)
    if stats_out is not None:
        stats_out["retries"] = max_retries - 1
        stats_out["rate_limit_hits"] = rate_limit_hits
        stats_out["last_error_class"] = type(last_error).__name__ if last_error else ""
    raise RuntimeError(
        f"ZAI call failed after {max_retries} attempts at context={context}: {last_error}"
    )


# ---------------------------------------------------------------------------
# Map / Reduce pipeline primitives
# ---------------------------------------------------------------------------

_PER_VIDEO_CLASSIFICATION_PATTERN = re.compile(
    r"```per_video_classification\s*(.*?)\s*```",
    flags=re.DOTALL | re.IGNORECASE,
)
_THESIS_LINE_PATTERN = re.compile(
    r"### Thesis\s*\n+(.+?)(?:\n{2,}|\Z)",
    flags=re.DOTALL,
)


def _parse_map_output(raw_text: str, *, video_id: str, fallback_title: str) -> dict[str, Any]:
    """Parse a single map-step LLM response into (clean_markdown, thesis, classification).

    On any parse failure, returns a best-effort classification so the digest
    can still proceed (the reducer ranks low-confidence entries near the
    bottom and the dispatch gate further filters them out).
    """
    # Extract classification block
    classification: dict[str, Any] = {}
    match = _PER_VIDEO_CLASSIFICATION_PATTERN.search(raw_text)
    if match:
        try:
            classification = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.warning(
                "Map step %s: failed to parse per_video_classification JSON: %s",
                video_id,
                exc,
            )
            classification = {}
    if not isinstance(classification, dict):
        classification = {}
    classification.setdefault("video_id", video_id)
    classification.setdefault("value_score", 0)
    classification.setdefault("value_tier", "unknown")
    classification.setdefault("code_implementation_prospect", False)
    classification.setdefault("concept_only", True)
    classification.setdefault("evidence_quality", "unknown")
    classification.setdefault("reason", "")

    # Extract thesis
    thesis = ""
    thesis_match = _THESIS_LINE_PATTERN.search(raw_text)
    if thesis_match:
        thesis = thesis_match.group(1).strip()
    if not thesis:
        thesis = classification.get("reason") or fallback_title

    # Strip the classification block from the markdown (we'll re-emit it from
    # the reducer's structured output, not from per-video raw blocks).
    clean_markdown = _PER_VIDEO_CLASSIFICATION_PATTERN.sub("", raw_text).strip()

    return {
        "retell_markdown": clean_markdown,
        "thesis_line": thesis,
        "classification": classification,
    }


def _format_duration_seconds(seconds: int | None) -> str:
    """Format a duration in seconds as h:mm:ss (or m:ss when under an hour)."""
    if not seconds or seconds <= 0:
        return ""
    s = int(seconds)
    hours, remainder = divmod(s, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_upload_date(raw: str | None) -> str:
    """Convert YYYYMMDD or ISO 8601 date strings to a friendly 'Mon DD, YYYY'."""
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    # YouTube Data API path produces YYYYMMDD; tolerate ISO dates too.
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        try:
            from datetime import datetime as _dt
            return _dt.strptime(digits[:8], "%Y%m%d").strftime("%b %-d, %Y")
        except (ValueError, OSError):
            pass
    return s


def _build_per_video_header(video: VideoTranscriptPayload) -> str:
    """Build a deterministic per-video header block.

    Output:
        ## <title>
        <small> Channel · 12:34 · May 19, 2026 · [watch ↗](https://youtu.be/<id>) </small>

    We render the metadata strip as a single line of small text rather than a
    table so it stays compact in both rendered HTML and plaintext.
    """
    md = video.metadata or {}
    parts: list[str] = []
    channel = str(md.get("channel") or "").strip()
    if channel:
        parts.append(channel)
    duration = _format_duration_seconds(md.get("duration"))
    if duration:
        parts.append(duration)
    pub = _format_upload_date(md.get("upload_date"))
    if pub:
        parts.append(pub)
    parts.append(f"[watch ↗](https://www.youtube.com/watch?v={video.video_id})")
    meta_line = " · ".join(parts)

    return f"## {video.title}\n\n<small>{meta_line}</small>\n"


_FIRST_H2_PATTERN = re.compile(r"^\s*##\s+[^\n]*\n*", flags=re.MULTILINE)


_VIDEO_ID_LINE_PATTERN = re.compile(r"^\s*\*\*Video ID:\*\*[^\n]*\n?", flags=re.MULTILINE)


def _inject_video_header(retell_markdown: str, video: VideoTranscriptPayload) -> str:
    """Replace the LLM's `## <title>` line with our deterministic header (title +
    metadata strip). Also drop the LLM's `**Video ID:**` line — the watch link in
    the metadata strip already exposes the ID, and keeping both creates dead
    duplication in every per-video card.

    If the LLM didn't emit an H2 (rare), prepend our header so the section is
    still well-formed."""
    header = _build_per_video_header(video)
    new_md, n = _FIRST_H2_PATTERN.subn(header + "\n", retell_markdown, count=1)
    if n == 0:
        new_md = header + "\n" + retell_markdown
    return _VIDEO_ID_LINE_PATTERN.sub("", new_md, count=1)


async def _retell_one_video(
    *,
    client: AsyncAnthropic,
    model: str,
    video: VideoTranscriptPayload,
    max_tokens: int,
    transcript_char_limit: int,
) -> MapResult:
    """One map call: turn one video's transcript into a Compressed Retelling +
    structured classification. Errors are caught and converted into a
    metadata-only MapResult so the digest run can finish even if one or two
    videos fail."""
    started = datetime.now(timezone.utc)
    transcript_text = video.transcript_text or ""
    if transcript_text and len(transcript_text) > transcript_char_limit:
        transcript_text = transcript_text[: transcript_char_limit] + "... [TRUNCATED]"
    if video.is_metadata_only or not transcript_text:
        transcript_text = "[Metadata-only — transcript unavailable]"

    prompt_body = (
        f"TITLE: {video.title}\n"
        f"VIDEO ID: {video.video_id}\n"
        f"TRANSCRIPT:\n{transcript_text}\n"
    )
    prompt = RETELL_PROMPT + prompt_body
    context = f"youtube_digest_map:{video.video_id}"

    stats: dict[str, Any] = {}
    try:
        raw_text = await _zai_call_with_retry(
            client=client,
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            context=context,
            stats_out=stats,
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        parsed = _parse_map_output(raw_text, video_id=video.video_id, fallback_title=video.title)
        retell_markdown = _inject_video_header(parsed["retell_markdown"], video)
        return MapResult(
            video_id=video.video_id,
            title=video.title,
            retell_markdown=retell_markdown,
            thesis_line=parsed["thesis_line"],
            classification=parsed["classification"],
            elapsed_seconds=elapsed,
            map_model=model,
            retries=int(stats.get("retries", 0)),
            rate_limit_hits=int(stats.get("rate_limit_hits", 0)),
            last_error_class=str(stats.get("last_error_class", "")),
        )
    except Exception as exc:
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        logger.error("Map step failed for %s after %.1fs: %s", video.video_id, elapsed, exc)
        header = _build_per_video_header(video)
        fallback_md = (
            f"{header}\n"
            f"### Retelling\n\n"
            f"_Map-step retelling failed: {exc}. Tutorial pipeline dispatch will skip this video._\n"
        )
        return MapResult(
            video_id=video.video_id,
            title=video.title,
            retell_markdown=fallback_md,
            thesis_line="(map step failed — see digest log)",
            classification={
                "video_id": video.video_id,
                "value_score": 0,
                "value_tier": "unknown",
                "code_implementation_prospect": False,
                "concept_only": True,
                "evidence_quality": "unknown",
                "reason": f"Map step error: {type(exc).__name__}",
            },
            error=str(exc),
            elapsed_seconds=elapsed,
            map_model=model,
            retries=int(stats.get("retries", 0)),
            rate_limit_hits=int(stats.get("rate_limit_hits", 0)),
            last_error_class=str(stats.get("last_error_class", "") or type(exc).__name__),
        )


async def _map_retell_videos(
    videos: list[VideoTranscriptPayload],
    *,
    model: str | None = None,
    concurrency: int | None = None,
    timeout_seconds: int | None = None,
    max_tokens: int | None = None,
    transcript_char_limit: int | None = None,
) -> list[MapResult]:
    """Run the map step over all videos with bounded concurrency.

    The internal semaphore bounds how many tasks we *try* to run in parallel.
    The global ZAIRateLimiter (`ZAI_MAX_CONCURRENT`, default 2) bounds how
    many of those actually hit the Z.AI proxy at once. So the effective
    parallelism is min(internal_semaphore, ZAI_MAX_CONCURRENT).
    """
    resolved_model = (
        model
        or os.getenv("UA_YOUTUBE_DIGEST_MAP_MODEL")
        or DIGEST_MAP_MODEL_DEFAULT
    )
    resolved_concurrency = max(
        1,
        int(
            concurrency
            if concurrency is not None
            else os.getenv("UA_YOUTUBE_DIGEST_MAP_CONCURRENCY", str(DIGEST_MAP_CONCURRENCY_DEFAULT))
        ),
    )
    resolved_timeout = int(
        timeout_seconds
        if timeout_seconds is not None
        else os.getenv("UA_YOUTUBE_DIGEST_MAP_TIMEOUT_SECONDS", str(DIGEST_MAP_TIMEOUT_SECONDS_DEFAULT))
    )
    resolved_max_tokens = int(
        max_tokens
        if max_tokens is not None
        else os.getenv("UA_YOUTUBE_DIGEST_MAP_MAX_TOKENS", str(DIGEST_MAP_MAX_TOKENS_DEFAULT))
    )
    resolved_char_limit = int(
        transcript_char_limit
        if transcript_char_limit is not None
        else os.getenv(
            "UA_YOUTUBE_DIGEST_MAP_TRANSCRIPT_CHAR_LIMIT",
            str(DIGEST_MAP_TRANSCRIPT_CHAR_LIMIT_DEFAULT),
        )
    )

    logger.info(
        "Map step: model=%s videos=%d concurrency=%d timeout=%ds max_tokens=%d",
        resolved_model,
        len(videos),
        resolved_concurrency,
        resolved_timeout,
        resolved_max_tokens,
    )

    client = _build_anthropic_client_for_zai(timeout_seconds=resolved_timeout)
    semaphore = asyncio.Semaphore(resolved_concurrency)

    async def _bounded(v: VideoTranscriptPayload) -> MapResult:
        async with semaphore:
            return await _retell_one_video(
                client=client,
                model=resolved_model,
                video=v,
                max_tokens=resolved_max_tokens,
                transcript_char_limit=resolved_char_limit,
            )

    try:
        results = await asyncio.gather(*[_bounded(v) for v in videos])
    finally:
        # Close the client inside this still-live loop so its httpx pool isn't
        # finalized on a closed loop later (see `_aclose_client`).
        await _aclose_client(client)
    # Surface aggregate stats in the log so operators can spot regressions.
    successes = [r for r in results if r.error is None]
    failures = [r for r in results if r.error is not None]
    total_retries = sum(r.retries for r in results)
    total_rate_limit_hits = sum(r.rate_limit_hits for r in results)
    error_class_breakdown: dict[str, int] = {}
    for r in results:
        cls = r.last_error_class
        if cls:
            error_class_breakdown[cls] = error_class_breakdown.get(cls, 0) + 1
    if successes:
        elapsed_list = sorted(r.elapsed_seconds for r in successes)
        median = elapsed_list[len(elapsed_list) // 2]
        logger.info(
            "Map step complete: %d ok / %d failed; latency min=%.1fs p50=%.1fs max=%.1fs; "
            "retries=%d (%d rate_limit); error_classes=%s",
            len(successes),
            len(failures),
            min(elapsed_list),
            median,
            max(elapsed_list),
            total_retries,
            total_rate_limit_hits,
            error_class_breakdown or "none",
        )
    else:
        logger.error(
            "Map step complete: 0 ok / %d failed (all videos failed); "
            "retries=%d (%d rate_limit); error_classes=%s",
            len(failures),
            total_retries,
            total_rate_limit_hits,
            error_class_breakdown or "none",
        )
    return results


async def _reduce_meta_synthesize(
    map_results: list[MapResult],
    *,
    day_name: str,
    date_str: str,
    model_tier: str | None = None,
    timeout_seconds: int | None = None,
    max_tokens: int | None = None,
) -> str:
    """One reduce call: meta-synthesis across all per-video classifications.

    The reducer sees only titles + thesis lines + classifications (NOT full
    retellings), so context stays small even on 30+ video days.
    """
    resolved_model = (
        os.getenv("UA_YOUTUBE_DIGEST_REDUCE_MODEL")
        or resolve_model(model_tier or DIGEST_REDUCE_MODEL_TIER_DEFAULT)
    )
    resolved_timeout = int(
        timeout_seconds
        if timeout_seconds is not None
        else os.getenv(
            "UA_YOUTUBE_DIGEST_REDUCE_TIMEOUT_SECONDS",
            str(DIGEST_REDUCE_TIMEOUT_SECONDS_DEFAULT),
        )
    )
    resolved_max_tokens = int(
        max_tokens
        if max_tokens is not None
        else os.getenv("UA_YOUTUBE_DIGEST_REDUCE_MAX_TOKENS", str(DIGEST_REDUCE_MAX_TOKENS_DEFAULT))
    )

    rollup = [
        {
            "video_id": r.video_id,
            "title": r.title,
            "thesis": r.thesis_line,
            "value_score": r.classification.get("value_score", 0),
            "value_tier": r.classification.get("value_tier", "unknown"),
            "code_implementation_prospect": bool(
                r.classification.get("code_implementation_prospect", False)
            ),
            "concept_only": bool(r.classification.get("concept_only", True)),
            "evidence_quality": r.classification.get("evidence_quality", "unknown"),
            "reason": r.classification.get("reason", ""),
        }
        for r in map_results
    ]
    rollup_json = json.dumps(rollup, indent=2, ensure_ascii=False)
    prompt = REDUCE_PROMPT.format(
        day_name=day_name.title(),
        date_str=date_str,
        video_count=len(map_results),
        rollup_json=rollup_json,
    )

    logger.info(
        "Reduce step: model=%s timeout=%ds max_tokens=%d rollup_chars=%d",
        resolved_model,
        resolved_timeout,
        resolved_max_tokens,
        len(rollup_json),
    )

    client = _build_anthropic_client_for_zai(timeout_seconds=resolved_timeout)
    try:
        return await _zai_call_with_retry(
            client=client,
            model=resolved_model,
            prompt=prompt,
            max_tokens=resolved_max_tokens,
            context="youtube_digest_reduce",
        )
    finally:
        await _aclose_client(client)


def _build_decisions_json_block(map_results: list[MapResult]) -> str:
    """Build the youtube_digest_decisions JSON block deterministically from
    the map-step's structured per-video classifications.

    Replaces the previous design where we asked the reducer LLM to re-emit
    this block. That was unreliable — the LLM sometimes dropped the block
    or produced malformed JSON, which silently fell through to all-zero
    fallback classifications and caused the demo-worthiness gate to reject
    every video.  See 2026-05-20 TUESDAY digest investigation for the
    incident this replaces.

    Each ranked entry derives directly from a MapResult.classification dict,
    which the map LLM has already populated alongside the retelling.  Rank
    is assigned by sorting on value_score (descending), with tutorial_candidate
    + recommended_tutorial_mode computed from code_implementation_prospect.
    """
    ranked: list[dict[str, Any]] = []
    for r in map_results:
        cls = r.classification or {}
        code_prospect = _coerce_bool(cls.get("code_implementation_prospect"))
        concept_only = _coerce_bool(cls.get("concept_only")) or not code_prospect
        tutorial_candidate = code_prospect
        recommended_mode = (
            "explainer_plus_code"
            if tutorial_candidate
            else ("concept_only" if concept_only else "none")
        )
        ranked.append({
            "video_id": r.video_id,
            "title": r.title,
            "value_score": _coerce_score(cls.get("value_score")),
            "value_tier": str(cls.get("value_tier") or "").strip().lower() or "unknown",
            "code_implementation_prospect": code_prospect,
            "concept_only": concept_only,
            "tutorial_candidate": tutorial_candidate,
            "recommended_tutorial_mode": recommended_mode,
            "evidence_quality": str(cls.get("evidence_quality") or "").strip().lower() or "unknown",
            "reason": str(cls.get("reason") or "").strip(),
        })
    # Sort by score descending and stamp 1-indexed rank.
    ranked.sort(key=lambda row: int(row.get("value_score") or 0), reverse=True)
    for idx, row in enumerate(ranked, start=1):
        row["rank"] = idx
    body = json.dumps({"ranked_videos": ranked}, indent=2, ensure_ascii=False)
    return f"```youtube_digest_decisions\n{body}\n```"


def _assemble_map_reduce_digest(
    *,
    reduce_output: str,
    map_results: list[MapResult],
) -> str:
    """Build the final digest markdown deterministically:

      <reducer meta-synthesis>
      --- Per-Video Retellings ---
      <each MapResult.retell_markdown joined by --->
      --- youtube_digest_decisions JSON block ---

    The reducer output is meta-synthesis prose ONLY (per REDUCE_PROMPT).
    The JSON block is built in Python from the map-step classifications,
    NOT from the reducer's output. The H1 title is added by the caller
    (process_daily_digest) — neither this function nor the reducer emit it.
    """
    retellings_md = "\n\n---\n\n".join(r.retell_markdown for r in map_results)
    decisions_block = _build_decisions_json_block(map_results)
    # No `---` between retellings and the JSON block: the JSON block is
    # stripped from the human-facing email by `_strip_digest_decision_blocks`,
    # which would leave the separator orphaned and render as a double `---`
    # next to the wrapper's separator before the tutorial-dispatch section.
    return (
        f"{reduce_output.rstrip()}\n\n"
        f"---\n\n"
        f"## Per-Video Retellings\n\n"
        f"{retellings_md}\n\n"
        f"{decisions_block}\n"
    )


async def _generate_digest_content_map_reduce(
    *,
    videos: list[VideoTranscriptPayload],
    day_name: str,
    date_str: str,
) -> str:
    """Map-reduce pipeline entry point. Parallel per-video retellings, then
    one reduce call for meta-synthesis + ranked JSON decision block."""
    map_results = await _map_retell_videos(videos)
    reduce_output = await _reduce_meta_synthesize(
        map_results,
        day_name=day_name,
        date_str=date_str,
    )
    return _assemble_map_reduce_digest(reduce_output=reduce_output, map_results=map_results)


async def _generate_digest_content_single_call(full_prompt: str) -> str:
    """Legacy single-LLM-call digest synthesis. Kept for fallback and for
    A/B comparison against the new map-reduce path. Hits one big-context
    glm-5.1 call with all transcripts concatenated.
    """
    model = os.getenv("UA_YOUTUBE_DIGEST_MODEL") or resolve_model("opus")
    timeout_seconds = float(os.getenv("UA_YOUTUBE_DIGEST_LLM_TIMEOUT_SECONDS", "900"))
    max_retries = int(os.getenv("UA_YOUTUBE_DIGEST_LLM_MAX_RETRIES", "5"))
    max_tokens = int(os.getenv("UA_YOUTUBE_DIGEST_MAX_TOKENS", "12000"))

    client = _build_anthropic_client_for_zai(timeout_seconds=timeout_seconds)
    logger.info("Single-call digest synthesis with model=%s...", model)
    try:
        return await _zai_call_with_retry(
            client=client,
            model=model,
            prompt=full_prompt,
            max_tokens=max_tokens,
            context="youtube_daily_digest",
            max_retries=max_retries,
        )
    finally:
        await _aclose_client(client)


async def _generate_digest_content(
    *,
    full_prompt: str | None = None,
    videos: list[VideoTranscriptPayload] | None = None,
    day_name: str | None = None,
    date_str: str | None = None,
    pipeline_override: str | None = None,
) -> str:
    """Dispatcher for digest synthesis. Picks pipeline based on
    `UA_YOUTUBE_DIGEST_PIPELINE` env var (or explicit override).

    - `map_reduce` (default): requires `videos`, `day_name`, `date_str`.
    - `single_call`: requires `full_prompt`.
    """
    pipeline = (
        pipeline_override
        or os.getenv("UA_YOUTUBE_DIGEST_PIPELINE", DIGEST_PIPELINE_DEFAULT)
    ).strip().lower()
    if pipeline == "single_call":
        if full_prompt is None:
            raise ValueError("single_call pipeline requires full_prompt")
        return await _generate_digest_content_single_call(full_prompt)
    if pipeline != "map_reduce":
        logger.warning(
            "Unknown UA_YOUTUBE_DIGEST_PIPELINE=%r; falling back to map_reduce",
            pipeline,
        )
    if videos is None or day_name is None or date_str is None:
        raise ValueError(
            "map_reduce pipeline requires videos, day_name, and date_str"
        )
    return await _generate_digest_content_map_reduce(
        videos=videos,
        day_name=day_name,
        date_str=date_str,
    )


# ---------------------------------------------------------------------------
# CSI Digest emission — write directly to the gateway's CSI SQLite DB
# ---------------------------------------------------------------------------

def _workspace_dir() -> Path:
    # Canonical, cwd-INDEPENDENT resolution. Honor an explicit UA_WORKSPACES_DIR
    # override, else anchor on the same repo-root-derived AGENT_RUN_WORKSPACES the
    # durable DB resolvers use (durable/db.py::get_activity_db_path). NEVER fall
    # back to Path.cwd(): a cwd-relative default forks .csi_digests.db /
    # youtube_ingestion_state.db into an orphan workspace the moment this job runs
    # from any cwd other than the repo root — the Phase D #756 DB-fork class. This
    # is latent today (the gateway WorkingDirectory=/opt/universal_agent masks it)
    # but would bite the instant youtube_daily_digest is migrated to a systemd unit.
    env_dir = os.getenv("UA_WORKSPACES_DIR")
    if env_dir:
        return Path(env_dir)
    from universal_agent.durable.db import get_activity_db_path

    return Path(get_activity_db_path()).parent


def _digest_artifacts_dir() -> Path:
    return _workspace_dir() / "daily_digests"


def _pockets_dir() -> Path:
    return _digest_artifacts_dir() / "repopulate_pockets"


def _load_gold_duration_overrides() -> dict[str, int]:
    """Return {channel_id: max_duration_seconds_override} for every gold channel
    that has an explicit override. Used to plumb per-channel cap-bypass into the
    digest's pre-ingest triage so e.g. Lex Fridman 3-hour interviews don't get
    auto-skipped by the global 90-minute cap.

    Returns an empty dict if the watchlist is missing or malformed — the
    default behavior (global cap applied everywhere) is preserved.
    """
    path = Path(
        os.getenv(
            "UA_YOUTUBE_CHANNELS_WATCHLIST_PATH",
            "/opt/universal_agent/channels_watchlist.json",
        )
    )
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load gold duration overrides from %s: %s", path, exc)
        return {}
    overrides: dict[str, int] = {}
    for c in data.get("channels", []):
        if c.get("tier") != "gold":
            continue
        override = c.get("duration_max_seconds_override")
        if isinstance(override, int) and override > 0:
            cid = c.get("channel_id")
            if cid:
                overrides[cid] = override
    return overrides


def _pocket_path(*, day_name: str, date_str: str) -> Path:
    day_upper = day_name.upper()
    return _pockets_dir() / day_upper / f"{date_str}_{day_upper}_playlist_pocket.json"


def _tutorial_candidates_path(*, day_name: str, date_str: str) -> Path:
    day_upper = day_name.upper()
    return _digest_artifacts_dir() / f"{date_str}_{day_upper}_tutorial_candidates.json"


def _latest_pocket_path(day_name: str) -> Path | None:
    day_upper = day_name.upper()
    day_dir = _pockets_dir() / day_upper
    if not day_dir.exists():
        return None
    pockets = sorted(day_dir.glob(f"*_{day_upper}_playlist_pocket.json"))
    return pockets[-1] if pockets else None


def _save_repopulate_pocket(
    *,
    day_name: str,
    date_str: str,
    playlist_id: str,
    items: list[dict],
    artifact_path: Path,
    dry_run: bool,
) -> Path:
    """Persist processed playlist contents before cleanup deletion."""
    path = _pocket_path(day_name=day_name, date_str=date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    videos = [
        {
            "video_id": str(item.get("video_id") or ""),
            "title": str(item.get("title") or ""),
            "original_playlist_item_id": str(item.get("playlist_item_id") or ""),
        }
        for item in items
        if item.get("video_id")
    ]
    pocket = {
        "schema_version": POCKET_SCHEMA_VERSION,
        "day_name": day_name.upper(),
        "date": date_str,
        "playlist_id": playlist_id,
        "artifact_path": str(artifact_path),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cleanup_mode": "dry_run" if dry_run else "delete_after_digest",
        "video_count": len(videos),
        "videos": videos,
    }
    path.write_text(json.dumps(pocket, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved digest repopulate pocket: %s (%d videos)", path, len(videos))
    return path


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_score(value: Any) -> int:
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return 0


def _extract_decision_json(digest_content: str) -> dict[str, Any]:
    """Extract the LLM's structured digest decision block."""
    match = re.search(
        r"```(?:youtube_digest_decisions|json)\s*(.*?)\s*```",
        digest_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return {"ranked_videos": []}
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse youtube_digest_decisions JSON block: %s", exc)
        return {"ranked_videos": []}
    if not isinstance(parsed, dict):
        return {"ranked_videos": []}
    videos = parsed.get("ranked_videos")
    if not isinstance(videos, list):
        parsed["ranked_videos"] = []
    return parsed


def _strip_digest_decision_blocks(digest_content: str) -> str:
    """Remove machine-readable decision blocks from human-facing digest text."""
    cleaned = re.sub(
        r"\n?```(?:youtube_digest_decisions|json)\s*.*?\s*```\s*",
        "\n",
        digest_content,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _split_email_body_and_attachment(full_content: str) -> tuple[str, str]:
    """Split the full markdown digest into:
      - body_md: everything up to but not including the `## Per-Video Retellings` header.
                 This is the meta-synthesis section (Cross-Video Themes / Learning Insights /
                 Neglected Opportunities) — short enough to scan inline in an email client.
      - attachment_md: the unchanged full markdown — meta-synthesis + per-video retellings
                       + tutorial dispatch summary — rendered as a standalone HTML file.

    The split point is `^## Per-Video Retellings` (case-insensitive). If the marker
    isn't found (e.g. an unusual reducer output), the body keeps everything and the
    attachment is identical.
    """
    pattern = re.compile(r"(?im)^\#{1,3}\s*Per[- ]Video Retellings\s*$")
    match = pattern.search(full_content)
    if not match:
        return full_content.strip(), full_content
    body_md = full_content[: match.start()].rstrip()
    # Drop a trailing `---` divider that was meant to separate body from per-video
    # retellings — looks like a stray rule when the per-video section is gone.
    body_md = re.sub(r"\n+---\s*$", "", body_md).rstrip()
    return body_md, full_content


# ---------------------------------------------------------------------------
# HTML rendering template
#
# Inline CSS is required for email clients (Gmail in particular strips
# `<style>` tags from the body and ignores anything that isn't inline). The
# attachment renders as a standalone HTML file so we can use `<style>` there.
# Notable choices:
#   * No `nl2br` markdown extension — `nl2br` inserts <br> on every newline,
#     including INSIDE a single paragraph the user wrote across two lines.
#     The result is the "way too much spacing" we saw on 2026-05-21.
#   * `border-bottom` on h1/h2 only (not h3) — h3 is too small to deserve a
#     full-width rule, and the per-video section has many of them.
#   * `<small>` metadata strip styled as dim, italic-free, single line.
#   * Code blocks: GitHub-style light grey, monospace, generous padding.
# ---------------------------------------------------------------------------

_DIGEST_HTML_HEAD_CSS = """
  :root { color-scheme: light; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                 Helvetica, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.55;
    color: #1f2328;
    background: #ffffff;
    max-width: 780px;
    margin: 0 auto;
    padding: 24px 28px 80px;
  }
  h1 {
    font-size: 24px;
    border-bottom: 2px solid #d0d7de;
    padding-bottom: 8px;
    margin-top: 8px;
  }
  h2 {
    font-size: 20px;
    border-bottom: 1px solid #d8dee4;
    padding-bottom: 6px;
    margin-top: 32px;
  }
  h3 {
    font-size: 16px;
    margin-top: 22px;
    margin-bottom: 4px;
    color: #24292f;
  }
  p, ul, ol { margin: 8px 0; }
  ul, ol { padding-left: 22px; }
  li { margin: 2px 0; }
  small {
    display: inline-block;
    font-size: 13px;
    color: #57606a;
    margin-bottom: 8px;
  }
  small a { color: #57606a; text-decoration: underline; }
  small a:hover { color: #0969da; }
  blockquote {
    border-left: 4px solid #d0d7de;
    margin: 8px 0;
    padding: 4px 14px;
    color: #57606a;
    background: #f6f8fa;
  }
  code {
    background: #eff1f3;
    padding: 1px 6px;
    border-radius: 4px;
    font-family: SFMono-Regular, Consolas, 'Liberation Mono', monospace;
    font-size: 13px;
  }
  pre {
    background: #f6f8fa;
    padding: 14px 16px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.45;
  }
  pre code { background: transparent; padding: 0; }
  hr { border: none; border-top: 1px solid #d8dee4; margin: 28px 0; }
  a { color: #0969da; }
  .scratch-back { font-size: 13px; margin: 0 0 18px; }
  .scratch-back a { color: #0969da; text-decoration: none; }
  .scratch-back a:hover { text-decoration: underline; }
  table { border-collapse: collapse; margin: 12px 0; }
  th, td { border: 1px solid #d0d7de; padding: 6px 10px; }
  th { background: #f6f8fa; }
  .digest-toc {
    background: #f6f8fa;
    border: 1px solid #d8dee4;
    border-radius: 6px;
    padding: 12px 18px;
    margin: 18px 0 28px;
  }
  .digest-toc h2 {
    font-size: 14px;
    border: none;
    margin: 0 0 6px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #57606a;
    padding: 0;
  }
  .digest-toc ol { margin: 4px 0 0; padding-left: 22px; }
  .digest-toc li { margin: 4px 0; font-size: 14px; line-height: 1.45; }
  .digest-toc a { text-decoration: none; color: inherit; }
  .digest-toc a:hover .toc-title { text-decoration: underline; color: #0969da; }
  .toc-channel { color: #1f2328; font-weight: 600; }
  .toc-sep { color: #8c959f; }
  .toc-title { color: #0969da; }
"""


def _markdown_to_html_fragment(md_text: str) -> str:
    """Render markdown to an HTML fragment with no inline-line-break (nl2br)
    expansion. Dropping nl2br is the single biggest fix for the 2026-05-21
    "excessive spacing" complaint — soft newlines no longer become <br>."""
    return markdown.markdown(md_text, extensions=["extra"])


_VIDEO_HEADING_PATTERN = re.compile(r"(?m)^##\s+(?!Per-Video Retellings)(.+?)\s*$")
# Headings that mark the END of the per-video range (anything below this is
# tutorial-dispatch summary / footer, NOT a video).
_PER_VIDEO_END_PATTERN = re.compile(
    r"(?im)^##\s+(?:Tutorial Pipeline Dispatch|Dispatch Summary|Footer)\b"
)


def _per_video_range(full_content: str) -> tuple[int, int] | None:
    """Return (start, end) indices of the per-video range within full_content,
    or None if no per-video marker is present. The range starts right after
    `## Per-Video Retellings` and ends at the first dispatch/footer heading."""
    start_match = re.search(
        r"(?im)^\#{1,3}\s*Per[- ]Video Retellings\s*$", full_content,
    )
    if not start_match:
        return None
    start = start_match.end()
    end_match = _PER_VIDEO_END_PATTERN.search(full_content, pos=start)
    end = end_match.start() if end_match else len(full_content)
    return start, end


_DURATION_PATTERN = re.compile(r"^\d+:\d+(:\d+)?$")
_DATE_PATTERN = re.compile(r"^[A-Za-z]+\s+\d+,\s+\d{4}$")


def _extract_channel_from_meta(meta_line: str) -> str:
    """Pull the channel name out of a per-video `<small>` metadata strip.

    The strip is built by `_build_per_video_header` as
    "Channel · 12:34 · May 19, 2026 · [watch ↗](...)" — channel is always
    first when present. If channel was missing the first segment will be
    duration / date / watch-link instead, so we shape-check before returning.
    """
    if not meta_line:
        return ""
    first = meta_line.split(" · ", 1)[0].strip()
    if not first:
        return ""
    if _DURATION_PATTERN.match(first):
        return ""
    if _DATE_PATTERN.match(first):
        return ""
    if first.startswith("[") or "watch" in first.lower():
        return ""
    return first


def _extract_video_entries(per_video_section: str) -> list[tuple[str, str]]:
    """Walk the per-video markdown section, return ``[(title, channel), ...]``
    in document order. Channel is empty string when the metadata strip was
    missing or malformed for that video."""
    entries: list[tuple[str, str]] = []
    # Split on H2 boundaries — blocks[0] is preamble; everything after each
    # boundary starts with the H2's text on its own line.
    blocks = re.split(r"(?m)^##\s+", per_video_section)
    for block in blocks[1:]:
        lines = block.split("\n", 1)
        title = lines[0].strip()
        if not title or title.lower().startswith("per-video retellings"):
            continue
        rest = lines[1] if len(lines) > 1 else ""
        channel = ""
        small_match = re.search(r"<small>(.+?)</small>", rest, flags=re.DOTALL)
        if small_match:
            channel = _extract_channel_from_meta(small_match.group(1))
        entries.append((title, channel))
    return entries


def _build_toc_html(full_content: str) -> str:
    """Build a per-video table of contents from the markdown source.

    Each entry is "Channel — Title" linked to that video's anchor. The TOC is
    placed at the END of the executive summary (right before "Per-Video
    Retellings") so the operator reads themes first, then drills into specific
    videos via click. Returns "" when fewer than two video sections exist.
    """
    bounds = _per_video_range(full_content)
    if not bounds:
        return ""
    per_video_section = full_content[bounds[0]:bounds[1]]
    entries = _extract_video_entries(per_video_section)
    if len(entries) < 2:
        return ""
    items: list[str] = []
    for idx, (raw_title, channel) in enumerate(entries, start=1):
        title_esc = html_module.escape(raw_title.strip())
        anchor = _slugify_anchor(raw_title, idx)
        if channel:
            channel_esc = html_module.escape(channel.strip())
            label = (
                f'<span class="toc-channel">{channel_esc}</span>'
                f'<span class="toc-sep"> — </span>'
                f'<span class="toc-title">{title_esc}</span>'
            )
        else:
            label = f'<span class="toc-title">{title_esc}</span>'
        items.append(f'<li><a href="#{anchor}">{label}</a></li>')
    return (
        '<div class="digest-toc">'
        '<h2>Jump to a video</h2>'
        f'<ol>{"".join(items)}</ol>'
        '</div>'
    )


def _slugify_anchor(text: str, idx: int) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return f"v{idx}-{base[:48]}" if base else f"v{idx}"


def _inject_video_anchors(html_fragment: str) -> str:
    """Add `id="vN-slug"` attributes to per-video <h2> elements so the TOC
    links resolve. Anchors are applied to h2s in the range between the
    "Per-Video Retellings" marker and the first dispatch/footer h2 — so
    meta-synthesis (before) and tutorial-dispatch (after) h2s are left
    untouched and the TOC indices line up with the per-video order."""
    marker = re.search(
        r"<h2[^>]*>\s*Per[- ]Video Retellings\s*</h2>",
        html_fragment,
        flags=re.IGNORECASE,
    )
    if not marker:
        return html_fragment
    end_marker = re.search(
        r"<h2[^>]*>\s*(?:Tutorial Pipeline Dispatch|Dispatch Summary|Footer)\b[^<]*</h2>",
        html_fragment[marker.end():],
        flags=re.IGNORECASE,
    )
    body_end = marker.end() + (end_marker.start() if end_marker else len(html_fragment) - marker.end())
    head = html_fragment[: marker.end()]
    body = html_fragment[marker.end(): body_end]
    foot = html_fragment[body_end:]
    counter = {"i": 0}

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        counter["i"] += 1
        # Unescape HTML entities before slugifying: the rendered h2 text has
        # `&` → `&amp;` etc., but `_build_toc_html` slugifies the raw markdown
        # title (unescaped). Without this, any title containing `&`/`<`/`>`
        # (e.g. "Antigravity & AGY CLI") gets a mismatched anchor (`...-amp-...`)
        # and its TOC link dies.
        stripped = html_module.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        anchor = _slugify_anchor(stripped, counter["i"])
        return f'<h2 id="{anchor}">{inner}</h2>'

    body = re.sub(r"<h2>(.*?)</h2>", repl, body, flags=re.DOTALL)
    return head + body + foot


def _render_full_digest_html(
    full_content: str,
    *,
    day_name: str,
    date_str: str,
) -> str:
    """Render the full digest markdown into a polished standalone HTML doc
    intended for the email attachment."""
    fragment = _markdown_to_html_fragment(full_content)
    fragment = _inject_video_anchors(fragment)
    toc_html = _build_toc_html(full_content)
    title = f"Daily YouTube Digest — {day_name.title()}, {date_str}"
    # Insert the TOC immediately BEFORE the per-video section heading — i.e.
    # at the end of the executive summary. Operator reads themes first, then
    # uses the TOC to jump into specific videos. Falls back to "after </h1>"
    # if the per-video marker is missing (defensive — shouldn't happen with
    # the digest's deterministic assembly).
    if toc_html:
        per_video_h2 = re.search(
            r"<h2[^>]*>\s*Per[- ]Video Retellings\s*</h2>",
            fragment,
            flags=re.IGNORECASE,
        )
        if per_video_h2:
            fragment = (
                fragment[: per_video_h2.start()]
                + toc_html
                + "\n"
                + fragment[per_video_h2.start():]
            )
        else:
            fragment = re.sub(
                r"(</h1>)",
                lambda m: m.group(1) + "\n" + toc_html,
                fragment,
                count=1,
            )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        # Force light rendering: pair the `:root{color-scheme:light}` CSS rule with
        # the meta so user agents (incl. dark-mode phones) don't auto-invert the page.
        '  <meta name="color-scheme" content="light">\n'
        f"  <title>{html_module.escape(title)}</title>\n"
        f"  <style>{_DIGEST_HTML_HEAD_CSS}</style>\n"
        "</head>\n"
        f"<body>{fragment}</body>\n"
        "</html>\n"
    )


# Print stylesheet applied ONLY when rendering the digest to PDF. The screen
# CSS (`_DIGEST_HTML_HEAD_CSS`) centers a 780px column with `margin:0 auto`,
# which on an A4 page can run into the printable-area edge. For PDF we let the
# `@page` margins own the whitespace and flow the content full printable width.
_DIGEST_PDF_PRINT_CSS = """
  @page { size: A4; margin: 1.6cm 1.4cm; }
  body { max-width: none; margin: 0; padding: 0; }
"""


def _render_full_digest_pdf(full_digest_html: str) -> bytes:
    """Render the standalone digest HTML (output of ``_render_full_digest_html``)
    into PDF bytes via WeasyPrint.

    PDF is the email attachment format because Gmail renders PDF attachments
    inline on click (one tap → rendered page), whereas it refuses to render
    `.html` attachments and shows their raw source instead. WeasyPrint:
      * honours the document's `<style>` block,
      * turns the per-video `<a href="#anchor">` TOC links into clickable
        intra-document PDF links (the `id=` anchors survive — unlike Gmail's
        inline-HTML path, which strips them), and
      * auto-generates a PDF outline/bookmark tree from the `<h1>/<h2>`
        headings, giving a native sidebar TOC on top of the in-page index.

    WeasyPrint is imported lazily so module import (and the test suite) does not
    pay its cost unless a PDF is actually being produced.
    """
    from weasyprint import CSS, HTML  # lazy: heavy import, isolates native-lib failures

    return HTML(string=full_digest_html).write_pdf(
        stylesheets=[CSS(string=_DIGEST_PDF_PRINT_CSS)],
    )


# Per-element inline styles. Gmail strips <style> blocks placed in the email
# body, so every visual rule the operator should see must be inlined directly
# on each tag. This map is applied by `_inline_email_styles` AFTER markdown is
# rendered to HTML. Keeping the inline values short — Gmail truncates emails
# over ~102KB and inline `style="..."` repetition eats into that budget.
_EMAIL_INLINE_STYLES: dict[str, str] = {
    "h1": "font-size:24px;border-bottom:2px solid #d0d7de;padding-bottom:8px;margin-top:8px;",
    "h2": "font-size:20px;border-bottom:1px solid #d8dee4;padding-bottom:6px;margin-top:32px;",
    "h3": "font-size:16px;margin-top:22px;margin-bottom:4px;color:#24292f;",
    "p": "margin:8px 0;",
    "ul": "margin:8px 0;padding-left:22px;",
    "ol": "margin:8px 0;padding-left:22px;",
    "li": "margin:2px 0;",
    "small": "display:inline-block;font-size:13px;color:#57606a;margin-bottom:8px;",
    "blockquote": "border-left:4px solid #d0d7de;margin:8px 0;padding:4px 14px;color:#57606a;background:#f6f8fa;",
    "code": "background:#eff1f3;padding:1px 6px;border-radius:4px;font-family:SFMono-Regular,Consolas,'Liberation Mono',monospace;font-size:13px;",
    "pre": "background:#f6f8fa;padding:14px 16px;border-radius:6px;overflow-x:auto;font-size:13px;line-height:1.45;",
    "hr": "border:none;border-top:1px solid #d8dee4;margin:28px 0;",
    "a": "color:#0969da;",
    "table": "border-collapse:collapse;margin:12px 0;",
    "th": "border:1px solid #d0d7de;padding:6px 10px;background:#f6f8fa;",
    "td": "border:1px solid #d0d7de;padding:6px 10px;",
}


def _inline_email_styles(html_fragment: str) -> str:
    """Walk the rendered HTML and inject ``style="..."`` on every supported
    element. Preserves existing attributes (notably ``id="vN-..."`` from the
    per-video anchor injection) so TOC links keep working.

    Uses a regex-based rewrite — the digest's HTML is well-formed (python-markdown
    is deterministic) and we control the input markdown, so we don't need a
    full parser.
    """
    for tag, inline_css in _EMAIL_INLINE_STYLES.items():
        pattern = re.compile(rf"<{tag}(\s[^>]*)?>", re.IGNORECASE)

        def repl(m: re.Match, _css: str = inline_css, _tag: str = tag) -> str:
            attrs = m.group(1) or ""
            if re.search(r'\bstyle\s*=', attrs, flags=re.IGNORECASE):
                return m.group(0)
            return f'<{_tag}{attrs} style="{_css}">'

        html_fragment = pattern.sub(repl, html_fragment)
    return html_fragment


def _build_inline_toc_html(full_content: str) -> str:
    """Variant of ``_build_toc_html`` with inline styles (Gmail strips
    ``<style>`` so the TOC box needs to be self-styled for the email body)."""
    bounds = _per_video_range(full_content)
    if not bounds:
        return ""
    per_video_section = full_content[bounds[0]:bounds[1]]
    entries = _extract_video_entries(per_video_section)
    if len(entries) < 2:
        return ""
    box_style = (
        "background:#f6f8fa;border:1px solid #d8dee4;border-radius:6px;"
        "padding:12px 18px;margin:18px 0 28px;"
    )
    heading_style = (
        "font-size:14px;border:none;margin:0 0 6px;text-transform:uppercase;"
        "letter-spacing:0.04em;color:#57606a;padding:0;"
    )
    ol_style = "margin:4px 0 0;padding-left:22px;"
    li_style = "margin:4px 0;font-size:14px;line-height:1.45;"
    link_style = "text-decoration:none;color:inherit;"
    channel_style = "color:#1f2328;font-weight:600;"
    sep_style = "color:#8c959f;"
    title_style = "color:#0969da;"
    items: list[str] = []
    for idx, (raw_title, channel) in enumerate(entries, start=1):
        title_esc = html_module.escape(raw_title.strip())
        anchor = _slugify_anchor(raw_title, idx)
        if channel:
            channel_esc = html_module.escape(channel.strip())
            label = (
                f'<span style="{channel_style}">{channel_esc}</span>'
                f'<span style="{sep_style}"> — </span>'
                f'<span style="{title_style}">{title_esc}</span>'
            )
        else:
            label = f'<span style="{title_style}">{title_esc}</span>'
        items.append(
            f'<li style="{li_style}">'
            f'<a href="#{anchor}" style="{link_style}">{label}</a>'
            f'</li>'
        )
    return (
        f'<div style="{box_style}">'
        f'<h2 style="{heading_style}">Jump to a video</h2>'
        f'<ol style="{ol_style}">{"".join(items)}</ol>'
        f'</div>'
    )


def _render_email_body_html(
    body_md: str,
    *,
    intro_html: str,
    full_content: str | None = None,
) -> str:
    """Render the email body as inline-styled HTML.

    PRODUCTION PATH (``full_content`` omitted / ``None``): the body renders
    only ``body_md`` — the short meta-synthesis summary — with NO inline TOC.
    This is what the digest cron sends. The full per-video retellings and the
    clickable per-video index live in the standalone HTML attachment
    (``_render_full_digest_html``), where anchors resolve in a browser.

    INLINE-EVERYTHING PATH (``full_content`` provided): renders the WHOLE
    digest inline — intro + clickable per-video TOC + meta-synthesis +
    per-video retellings — with ``#anchor`` jump links. This was the
    2026-05-28 "index at the beginning of the email" layout, but it does not
    work in Gmail: Gmail strips ``id=`` attributes at render time (so the TOC
    anchors have no targets) and clips messages over ~102KB (the inlined
    digest is ~130KB), hiding most sections behind "View entire message".
    Kept as a capability for clients that DO honor in-message anchors (e.g.
    Apple Mail) and for tests, but the cron no longer uses it (reverted
    2026-05-31).

    Gmail strips ``<style>`` so every visual rule is inlined per-element via
    ``_inline_email_styles``.
    """
    source_md = full_content if full_content is not None else body_md
    body_fragment = _markdown_to_html_fragment(source_md)
    body_fragment = _inject_video_anchors(body_fragment)
    # Strip the leading H1 (the email subject already names the day).
    body_fragment = re.sub(r"<h1>.*?</h1>\s*", "", body_fragment, count=1, flags=re.DOTALL)
    # Inject the TOC right BEFORE the per-video section so the reader sees
    # themes (meta-synthesis) first, then the clickable index, then dives in.
    if full_content is not None:
        toc_html = _build_inline_toc_html(full_content)
        if toc_html:
            per_video_h2 = re.search(
                r"<h2[^>]*>\s*Per[- ]Video Retellings\s*</h2>",
                body_fragment,
                flags=re.IGNORECASE,
            )
            if per_video_h2:
                body_fragment = (
                    body_fragment[: per_video_h2.start()]
                    + toc_html
                    + "\n"
                    + body_fragment[per_video_h2.start():]
                )
            else:
                # No per-video marker — fall back to placing TOC at the very
                # top so it's at least visible.
                body_fragment = toc_html + body_fragment
    body_fragment = _inline_email_styles(body_fragment)
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
        'Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;'
        'color:#1f2328;max-width:780px;margin:0 auto;padding:8px 4px 0;">'
        f'{intro_html}'
        f'{body_fragment}'
        '</div>'
    )


def _rank_digest_decisions(decisions: dict[str, Any], processed_items: list[dict]) -> dict[str, Any]:
    """Normalize LLM decisions and keep only known playlist videos."""
    items_by_id = {str(item.get("video_id") or ""): item for item in processed_items if item.get("video_id")}
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in decisions.get("ranked_videos", []):
        if not isinstance(raw, dict):
            continue
        video_id = str(raw.get("video_id") or "").strip()
        if not video_id or video_id not in items_by_id or video_id in seen:
            continue
        seen.add(video_id)
        item = items_by_id[video_id]
        code_prospect = _coerce_bool(raw.get("code_implementation_prospect"))
        tutorial_candidate = code_prospect
        concept_only = _coerce_bool(raw.get("concept_only")) or not code_prospect
        mode = "explainer_plus_code" if tutorial_candidate else ("concept_only" if concept_only else "none")
        ranked.append(
            {
                "video_id": video_id,
                "title": str(raw.get("title") or item.get("title") or ""),
                "value_score": _coerce_score(raw.get("value_score")),
                "value_tier": str(raw.get("value_tier") or "").strip().lower() or "low",
                "code_implementation_prospect": code_prospect,
                "concept_only": concept_only,
                "tutorial_candidate": tutorial_candidate,
                "recommended_tutorial_mode": mode,
                "evidence_quality": str(raw.get("evidence_quality") or "").strip().lower() or "unknown",
                "reason": str(raw.get("reason") or "").strip(),
            }
        )

    for video_id, item in items_by_id.items():
        if video_id in seen:
            continue
        ranked.append(
            {
                "video_id": video_id,
                "title": str(item.get("title") or ""),
                "value_score": 0,
                "value_tier": "unknown",
                "code_implementation_prospect": False,
                "concept_only": True,
                "tutorial_candidate": False,
                "recommended_tutorial_mode": "concept_only",
                "evidence_quality": "unknown",
                "reason": "No structured LLM decision was available for this video.",
            }
        )

    ranked.sort(key=lambda row: int(row.get("value_score") or 0), reverse=True)
    for idx, row in enumerate(ranked, start=1):
        row["rank"] = idx
    return {"ranked_videos": ranked}


DEMO_GATE_MIN_SCORE_DEFAULT = 70
_DEMO_GATE_REJECT_EVIDENCE = {"metadata_only"}
_DEMO_GATE_REJECT_TIERS = {"low", "unknown"}


def _is_demo_worthy(row: dict[str, Any], *, min_score: int) -> tuple[bool, str]:
    """Deterministic guardrails around the LLM's `code_implementation_prospect=true`.

    The LLM is allowed to nominate candidates, but we don't dispatch a tutorial
    pipeline run unless three additional signals (all present in the digest
    decisions JSON the LLM already emits) agree the video is build-tutorial
    material. Each signal can be tuned via env var.
    """
    if not _coerce_bool(row.get("code_implementation_prospect")):
        return False, "not_code_implementation_prospect"
    evidence = str(row.get("evidence_quality") or "").strip().lower()
    if evidence in _DEMO_GATE_REJECT_EVIDENCE:
        return False, f"evidence_quality={evidence or 'unknown'}"
    score = int(row.get("value_score") or 0)
    if score < min_score:
        return False, f"value_score={score}<min={min_score}"
    tier = str(row.get("value_tier") or "").strip().lower()
    if tier in _DEMO_GATE_REJECT_TIERS:
        return False, f"value_tier={tier or 'unknown'}"
    return True, "ok"


def _resolve_auto_tutorial_max_n(top_n: int) -> int:
    """Resolve the hard ceiling on tutorial dispatches when tie-extension
    rescues videos tied at the cutoff score.

    Score saturation (e.g. 2026-05-30 FRIDAY: 6 videos tied at exactly 85)
    means a static `top_n` cuts the tied cohort by arbitrary tie-order,
    dropping builds that are exactly as worthy as the last selected one.
    Tie-extension rescues those, but each dispatch triggers a full Cody
    tutorial build (real cost), so we bound the fan-out with this ceiling.

    Default is 2x top_n. Override with an absolute integer via
    UA_YOUTUBE_DIGEST_AUTO_TUTORIAL_MAX_N. The ceiling is never allowed below
    top_n (a ceiling under the target would make no sense).
    """
    raw = os.getenv("UA_YOUTUBE_DIGEST_AUTO_TUTORIAL_MAX_N")
    if raw is not None and raw.strip():
        try:
            return max(top_n, int(raw))
        except ValueError:
            logger.warning(
                "Invalid UA_YOUTUBE_DIGEST_AUTO_TUTORIAL_MAX_N=%r; using default (2x top_n)",
                raw,
            )
    return max(top_n, top_n * 2)


def _select_tutorial_dispatch_candidates(
    decisions: dict[str, Any],
    *,
    top_n: int,
    min_score: int | None = None,
    max_n: int | None = None,
) -> list[dict[str, Any]]:
    """Annotate every ranked row with a `dispatch_*` quartet and return the
    rows that pass the deterministic demo-worthiness gate AND fall within the
    dispatch budget.

    Selection budget:
      - The first `top_n` eligible rows (sorted by value_score descending) are
        always selected.
      - TIE-EXTENSION: when the `top_n`-th selected row ties subsequent eligible
        rows on `value_score`, those tied rows are ALSO selected so the tied
        cohort isn't split by arbitrary tie-order. This is bounded by `max_n`
        (default 2x top_n; see `_resolve_auto_tutorial_max_n`). Rescued rows are
        marked `dispatch_tie_extended=True` for observability.
      - Eligible rows scored strictly below the cutoff, or beyond `max_n`, are
        marked `eligible_overflow`.

    Side-effect: each ranked row in `decisions["ranked_videos"]` gains
    `dispatch_eligible`, `dispatch_reject_reason`, `dispatch_status`, and
    `dispatch_tie_extended` fields so the saved candidates JSON and the
    human-facing email both surface why a video was (or wasn't) sent to the
    tutorial pipeline.
    """
    threshold = DEMO_GATE_MIN_SCORE_DEFAULT if min_score is None else max(0, int(min_score))
    ceiling = _resolve_auto_tutorial_max_n(top_n) if max_n is None else max(top_n, int(max_n))
    selected: list[dict[str, Any]] = []
    # Score of the top_n-th selected row; once set, subsequent eligible rows
    # tying this score are rescued (up to `ceiling`). Rows are sorted by score
    # descending, so once a row scores below the cutoff no further ties exist.
    cutoff_score: int | None = None
    for row in decisions.get("ranked_videos", []):
        ok, reason = _is_demo_worthy(row, min_score=threshold)
        row["dispatch_eligible"] = ok
        row["dispatch_reject_reason"] = "" if ok else reason
        row["dispatch_tie_extended"] = False
        if top_n <= 0 or not ok:
            row["dispatch_status"] = "rejected" if not ok else "disabled_top_n_zero"
            continue
        score = int(row.get("value_score") or 0)
        if len(selected) < top_n:
            row["dispatch_status"] = "selected"
            selected.append(row)
            if len(selected) == top_n:
                cutoff_score = score
        elif (
            cutoff_score is not None
            and score == cutoff_score
            and len(selected) < ceiling
        ):
            row["dispatch_status"] = "selected"
            row["dispatch_tie_extended"] = True
            selected.append(row)
        else:
            row["dispatch_status"] = "eligible_overflow"
    return selected


def _save_tutorial_candidates(
    *,
    day_name: str,
    date_str: str,
    artifact_path: Path,
    decisions: dict[str, Any],
    selected: list[dict[str, Any]],
    dry_run: bool,
    top_n: int,
    max_n: int | None = None,
) -> Path:
    path = _tutorial_candidates_path(day_name=day_name, date_str=date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "day_name": day_name.upper(),
        "date": date_str,
        "digest_artifact_path": str(artifact_path),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "auto_tutorial_top_n": top_n,
        # Hard ceiling that tie-extension may grow the selection to when videos
        # tie at the cutoff score (default 2x top_n). selected_count > top_n
        # means tie-extension rescued videos this run.
        "auto_tutorial_max_n": max_n if max_n is not None else top_n,
        "selected_count": len(selected),
        "selected_video_ids": [str(row.get("video_id") or "") for row in selected],
        "ranked_videos": decisions.get("ranked_videos", []),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved tutorial candidate decisions: %s (%d selected)", path, len(selected))
    return path


def _gateway_base_url() -> str:
    configured = (
        os.getenv("UA_YOUTUBE_DIGEST_TUTORIAL_HOOK_URL")
        or os.getenv("UA_GATEWAY_URL")
        or "http://127.0.0.1:8002"
    ).strip()
    if configured.endswith("/api/v1/hooks/youtube/manual"):
        return configured
    return configured.rstrip("/") + "/api/v1/hooks/youtube/manual"


def _dispatch_tutorial_candidate(
    *,
    candidate: dict[str, Any],
    day_name: str,
    date_str: str,
    digest_artifact_path: Path,
    candidates_artifact_path: Path,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    video_id = str(candidate.get("video_id") or "").strip()
    if not video_id:
        return {"ok": False, "reason": "missing_video_id"}

    hook_token = (os.getenv("UA_HOOKS_TOKEN") or "").strip()
    if not hook_token:
        return {"ok": False, "reason": "missing_UA_HOOKS_TOKEN"}

    payload = {
        "video_id": video_id,
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "title": str(candidate.get("title") or ""),
        "mode": "explainer_plus_code",
        "allow_degraded_transcript_only": True,
        "description": (
            "Auto-selected by Daily YouTube Digest as a code implementation prospect. "
            f"Digest day={day_name}, date={date_str}, rank={candidate.get('rank')}, "
            f"value_score={candidate.get('value_score')}. Reason: {candidate.get('reason') or ''}"
        ),
        "source": "youtube_daily_digest",
        "digest_rank": candidate.get("rank"),
        "digest_value_score": candidate.get("value_score"),
        "digest_artifact_path": str(digest_artifact_path),
        "tutorial_candidates_artifact_path": str(candidates_artifact_path),
    }
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _gateway_base_url(),
        data=body,
        method="POST",
        headers={
            "authorization": f"Bearer {hook_token}",
            "content-type": "application/json",
            "x-ua-source": "youtube_daily_digest",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(resp.status) < 300,
                "status": int(resp.status),
                "response": text[:1000],
            }
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "reason": "http_error", "response": text[:1000]}
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__, "error": str(exc)}


def _dispatch_tutorial_candidates(
    *,
    selected: list[dict[str, Any]],
    day_name: str,
    date_str: str,
    digest_artifact_path: Path,
    candidates_artifact_path: Path,
    dry_run: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for candidate in selected:
        if dry_run:
            result = {"ok": True, "reason": "dry_run", "video_id": candidate.get("video_id")}
        else:
            result = _dispatch_tutorial_candidate(
                candidate=candidate,
                day_name=day_name,
                date_str=date_str,
                digest_artifact_path=digest_artifact_path,
                candidates_artifact_path=candidates_artifact_path,
            )
            result["video_id"] = candidate.get("video_id")
        results.append(result)
        logger.info(
            "Tutorial candidate dispatch video_id=%s ok=%s reason=%s status=%s",
            candidate.get("video_id"),
            result.get("ok"),
            result.get("reason"),
            result.get("status"),
        )
    return results


def _demo_build_candidates(decisions: dict[str, Any]) -> list[dict[str, Any]]:
    """Demo-lane candidates from the digest decisions (P3).

    Every ranked row that passed the deterministic demo-worthiness gate
    (``dispatch_eligible=True`` — annotated by
    ``_select_tutorial_dispatch_candidates`` on ALL rows, independent of the
    Tutorial-tier ``top_n``/tie-extension budget), in ``value_score`` order
    (``ranked_videos`` is already sorted descending). The Demo tier has its own
    throttle: the shared daily build ceiling.
    """
    candidates: list[dict[str, Any]] = []
    for row in decisions.get("ranked_videos", []):
        if not row.get("dispatch_eligible"):
            continue
        video_id = str(row.get("video_id") or "").strip()
        if not video_id:
            continue
        candidates.append(
            {
                "video_id": video_id,
                "video_title": str(row.get("title") or ""),
                "video_url": f"https://www.youtube.com/watch?v={video_id}",
                "channel_name": "",
                "extraction_plan": {
                    "summary": str(row.get("reason") or ""),
                    "category": "youtube_daily_digest",
                    "value_score": int(row.get("value_score") or 0),
                },
                "priority": 3,
            }
        )
    return candidates


def _queue_demo_builds(
    *,
    decisions: dict[str, Any],
    dry_run: bool,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """P3: queue demo-worthy digest videos into the ``tutorial_build`` Task Hub lane.

    Converges the curated Daily Digest source onto the SAME ranked
    daily-ceiling logic the broad CSI sweep uses
    (``proactive_tutorial_builds.queue_tutorial_builds_with_ceiling``):
    top-ranked candidates auto-dispatch up to the remaining America/Chicago
    daily budget; the rest queue as pending-approval rows for the dashboard
    button (P2b, uncapped). Dedupe across sources is structural —
    ``queue_tutorial_build_task`` derives ``task_id`` from ``sha256(video_id)``,
    so a video seen by both the digest and the broad RSS sweep lands on ONE row.

    This process is a standalone systemd subprocess with direct activity-DB
    access (same pattern as ``scripts/proactive_demo_build_sweep.py``);
    ``conn`` is injectable for tests. Failures are swallowed (logged) so the
    Brief email is never blocked by demo-lane queueing.
    """
    candidates = _demo_build_candidates(decisions)
    base = {"queued": 0, "auto_queued": 0, "auto_new": 0, "auto_reaffirmed": 0, "pending_approval": 0, "candidates": len(candidates)}
    if dry_run or not candidates:
        base["skipped"] = "dry_run" if dry_run else "no_candidates"
        return base
    try:
        from universal_agent.services.proactive_tutorial_builds import (
            queue_tutorial_builds_with_ceiling,
        )

        if conn is not None:
            outcome = queue_tutorial_builds_with_ceiling(
                conn, candidates, source="youtube_daily_digest"
            )
        else:
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )

            with connect_runtime_db(get_activity_db_path()) as db:
                outcome = queue_tutorial_builds_with_ceiling(
                    db, candidates, source="youtube_daily_digest"
                )
    except Exception as exc:  # noqa: BLE001 — demo-lane queueing must never break the digest
        logger.warning("Demo-build queueing failed (digest continues): %s", exc)
        base["error"] = str(exc)
        return base
    outcome["candidates"] = len(candidates)
    outcome["queued"] = int(outcome.get("auto_queued") or 0) + int(outcome.get("pending_approval") or 0)
    return outcome


def _format_tutorial_dispatch_summary(
    *,
    decisions: dict[str, Any],
    selected: list[dict[str, Any]],
    dispatch_results: list[dict[str, Any]],
    candidates_path: Path,
    dispatch_path: Path | None,
    top_n: int,
    min_score: int,
    dry_run: bool,
    max_n: int | None = None,
) -> str:
    """Build the human-facing tutorial pipeline section for the digest email/artifact."""
    ceiling = max_n if max_n is not None else top_n
    target_line = (
        f"Automation target: top {max(0, top_n)} code implementation prospects "
        f"(deterministic demo-worthiness gate: score >= {min_score}, "
        f"value_tier not in {{low, unknown}}, evidence_quality != metadata_only)."
    )
    if ceiling > top_n:
        target_line += (
            f" Tie-extension is enabled: videos tied at the cutoff score also "
            f"dispatch, up to {ceiling} total."
        )
    lines = [
        "## YouTube Tutorial Pipeline Dispatch",
        "",
        target_line,
    ]
    if dry_run:
        lines.append("Mode: dry run; no tutorial pipeline dispatches were sent.")
    if not selected:
        lines.extend(
            [
                "",
                "No videos were sent to the tutorial pipeline. Either no ranked video was a code-implementation prospect, or every prospect was rejected by the demo-worthiness gate (see below).",
            ]
        )
    else:
        result_by_id = {str(row.get("video_id") or ""): row for row in dispatch_results}
        lines.extend(["", "Selected videos:"])
        for row in selected:
            video_id = str(row.get("video_id") or "")
            result = result_by_id.get(video_id, {})
            status = "accepted" if result.get("ok") else f"not accepted ({result.get('reason') or result.get('status') or 'unknown'})"
            tie_note = " (tie-extended)" if row.get("dispatch_tie_extended") else ""
            lines.append(
                "- "
                f"#{row.get('rank')} {row.get('title') or video_id} "
                f"({video_id}) — score {row.get('value_score')}{tie_note}; {status}. "
                f"Reason: {row.get('reason') or 'classified as a code implementation prospect.'}"
            )

    rejected = [
        row
        for row in decisions.get("ranked_videos", [])
        if _coerce_bool(row.get("code_implementation_prospect"))
        and row.get("dispatch_status") == "rejected"
    ]
    if rejected:
        lines.extend(
            [
                "",
                "Rejected by demo-worthiness gate (LLM marked code-prospect, but deterministic checks disagreed):",
            ]
        )
        for row in rejected[:10]:
            video_id = str(row.get("video_id") or "")
            lines.append(
                "- "
                f"#{row.get('rank')} {row.get('title') or video_id} "
                f"({video_id}) — score {row.get('value_score')}, "
                f"tier {row.get('value_tier')}, evidence {row.get('evidence_quality')}; "
                f"reject_reason: {row.get('dispatch_reject_reason') or 'unknown'}."
            )

    lines.extend(["", f"Decision artifact: `{candidates_path}`"])
    if dispatch_path:
        lines.append(f"Dispatch results: `{dispatch_path}`")
    return "\n".join(lines).strip()


def repopulate_digest_playlist(
    *,
    day_override: str,
    date_override: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Restore a day's digest playlist from a saved repopulate pocket."""
    initialize_runtime_secrets()

    day_name = day_override.upper()
    if day_name not in DAYS:
        raise ValueError(f"Invalid day: {day_override}. Must be one of {DAYS}")

    playlist_id_var = f"{day_name}_YT_PLAYLIST"
    playlist_id = os.getenv(playlist_id_var)
    if not playlist_id:
        raise YouTubeOAuthError(f"No playlist configured for {day_name}: {playlist_id_var} is not set")

    path = _pocket_path(day_name=day_name, date_str=date_override) if date_override else _latest_pocket_path(day_name)
    if path is None or not path.exists():
        suffix = f" on {date_override}" if date_override else ""
        raise FileNotFoundError(f"No repopulate pocket found for {day_name}{suffix}")

    pocket = json.loads(path.read_text(encoding="utf-8"))
    videos = [video for video in pocket.get("videos", []) if video.get("video_id")]
    current_ids = {str(item.get("video_id")) for item in get_playlist_items(playlist_id)}

    added: list[str] = []
    skipped_existing: list[str] = []
    for video in videos:
        video_id = str(video["video_id"])
        if video_id in current_ids:
            skipped_existing.append(video_id)
            continue
        if not dry_run:
            add_playlist_item(playlist_id, video_id)
            current_ids.add(video_id)
            logger.info("Repopulated %s into %s playlist", video_id, day_name)
        added.append(video_id)

    result = {
        "pocket_path": str(path),
        "day_name": day_name,
        "date": pocket.get("date"),
        "playlist_id": playlist_id,
        "dry_run": dry_run,
        "requested": len(videos),
        "added": len(added),
        "skipped_existing": len(skipped_existing),
        "added_video_ids": added,
        "skipped_existing_video_ids": skipped_existing,
    }
    logger.info(
        "Repopulate complete day=%s requested=%d added=%d skipped_existing=%d dry_run=%s",
        day_name,
        result["requested"],
        result["added"],
        result["skipped_existing"],
        dry_run,
    )
    return result


def _emit_csi_digest(
    *,
    day_name: str,
    date_str: str,
    digest_content: str,
    video_count: int,
    video_titles: list[str],
) -> bool:
    """Write the daily digest as a CSI digest record for dashboard visibility.

    The digest is stored in the same SQLite DB used by the UA gateway's CSI
    Feed, so it shows up alongside Threads/YouTube RSS digests and
    can be processed by the batch brief / proactive signal pipeline.
    """
    # Locate the CSI digests DB — same path the gateway uses
    workspaces_dir = _workspace_dir()
    db_path = workspaces_dir / ".csi_digests.db"

    if not db_path.exists():
        logger.warning("CSI digest DB not found at %s — digest will not appear in CSI Feed", db_path)
        return False

    digest_id = str(uuid.uuid4())
    event_id = f"yt_daily_digest_{date_str}_{day_name.lower()}"
    title = f"Daily YouTube Digest: {day_name.title()}, {date_str} ({video_count} videos)"
    summary_lines = [f"Processed {video_count} videos from the {day_name.title()} Digest playlist."]
    if video_titles:
        summary_lines.append("Videos: " + " · ".join(video_titles[:5]))
        if len(video_titles) > 5:
            summary_lines.append(f"  ...and {len(video_titles) - 5} more")
    summary = " ".join(summary_lines)[:500]

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute(
            "INSERT OR REPLACE INTO csi_digests "
            "(id, event_id, source, event_type, title, summary, full_report_md, source_types, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                digest_id,
                event_id,
                "youtube_daily_digest",
                "youtube_daily_digest",
                title,
                summary,
                digest_content,
                json.dumps(["youtube"]),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        logger.info("Emitted CSI digest record: %s", digest_id)
        return True
    except Exception as exc:
        logger.error("Failed to emit CSI digest: %s", exc)
        return False

# ---------------------------------------------------------------------------
# Database State Management
# ---------------------------------------------------------------------------

def _ingestion_db_path() -> Path:
    return _workspace_dir() / "youtube_ingestion_state.db"


def _init_ingestion_db() -> None:
    db_path = _ingestion_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE IF NOT EXISTS processed_videos (
            video_id TEXT,
            day TEXT,
            title TEXT,
            processed_at TEXT,
            PRIMARY KEY (video_id, day)
        )
    ''')
    conn.commit()
    conn.close()


def _filter_unprocessed_items(items: list[dict], day_name: str) -> list[dict]:
    _init_ingestion_db()
    db_path = _ingestion_db_path()
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT video_id FROM processed_videos WHERE day = ?", (day_name,))
    processed_ids = {row[0] for row in cursor.fetchall()}
    conn.close()
    
    filtered = []
    for item in items:
        if item.get("video_id") not in processed_ids:
            filtered.append(item)
    return filtered


def _emit_proactive_delivery_failure(
    *,
    day_name: str,
    date_str: str,
    email_to: str | None,
    error: str,
) -> None:
    """Log + signal a proactive_delivery_failed condition so the operator
    can see that a digest run produced an artifact but failed to deliver
    it.  The cron service captures stderr into `cron_runs.jsonl`'s
    `output_preview`, which the gateway's `_emit_cron_event` fan-out
    surfaces as a `cron_run_failed` notification (kind-upserted).

    Best-effort posting to the gateway notification API is intentionally
    deferred — the cron-error path already covers operator visibility,
    and this helper stays free of network dependencies for testability.
    """
    msg = (
        f"Daily YouTube Digest delivery FAILED for {day_name} ({date_str}) "
        f"to {email_to}: {error}. Videos will NOT be marked processed; "
        f"next scheduled run will retry the same playlist."
    )
    logger.error(msg)
    try:
        # Tagged stderr line so the cron-run output_preview captures it
        # in a grep-friendly form for downstream surfacing.
        print(f"::proactive_delivery_failed:: {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _recreate_playlist_after_digest(
    *,
    day_name: str,
    old_playlist_id: str,
    processed_count: int,
) -> None:
    """Recreate the day-of-week playlist with the same name + description.

    Triggered after a successful digest run that processed at least one video.
    The point is to give Kevin a clean playlist for the next day's manual
    additions without paying per-video delete quota cost.

    Quota cost: ~101 YouTube Data API units per recreate (1 list + 50 create
    + 50 delete) regardless of how many videos were in the playlist. Compare
    against per-video delete which costs N*50 units — on a 65-video SATURDAY
    that's 3250 units (32% of daily quota).

    Safe ordering, so we never end up with zero playlists:
        1. Read the old playlist's metadata (title, description, privacy).
        2. Create a NEW playlist carrying that same metadata.
        3. Write the new playlist ID to Infisical.
        4. Only after Infisical persistence succeeds, delete the OLD playlist.

    Failure semantics:
      * Step 1 fail -> log, skip recreate. Playlist accumulates noise this
        cycle but the digest itself already succeeded.
      * Step 2 fail -> log, skip. No state changed.
      * Step 3 fail -> log loudly. Two playlists with the same name now exist;
        the old one is still referenced from Infisical, so the next cron tick
        still works. Operator can manually clean the orphan.
      * Step 4 fail -> log. Same outcome as step 3 — two playlists exist
        temporarily; the live env-var points at the new one. The old playlist
        is now orphaned but Kevin can delete it manually.
    """
    if processed_count <= 0:
        return
    if not old_playlist_id:
        return

    logger.info(
        "Recreating %s playlist (cleanup after %d processed videos)...",
        day_name, processed_count,
    )

    try:
        metadata = get_playlist_metadata(old_playlist_id)
    except YouTubeAPIError as exc:
        logger.warning(
            "Could not fetch metadata for %s playlist %s (skipping recreate; digest already succeeded): %s",
            day_name, old_playlist_id, exc,
        )
        return

    title = metadata.get("title") or f"{day_name.title()} Digest"
    try:
        new_id = create_playlist(
            title=title,
            description=metadata.get("description", ""),
            privacy_status=metadata.get("privacy_status", "private"),
        )
    except YouTubeAPIError as exc:
        logger.warning(
            "Could not create replacement %s playlist (skipping recreate): %s",
            day_name, exc,
        )
        return

    env_var = f"{day_name}_YT_PLAYLIST"
    upsert_ok = upsert_infisical_secret(env_var, new_id)
    if not upsert_ok:
        logger.error(
            "Failed to persist new %s playlist ID to Infisical. "
            "Two playlists with title %r now exist; Infisical still points at "
            "the old one (%s). The next cron will still work — manual cleanup "
            "of the new orphan (id=%s) is recommended.",
            day_name, title, old_playlist_id, new_id,
        )
        return

    try:
        delete_playlist(old_playlist_id)
    except YouTubeAPIError as exc:
        logger.warning(
            "Old %s playlist %s not deleted (orphan; new playlist %s is now live; "
            "safe to delete the orphan manually): %s",
            day_name, old_playlist_id, new_id, exc,
        )
    logger.info(
        "Recreated %s playlist: old=%s -> new=%s (title=%r preserved)",
        day_name, old_playlist_id, new_id, title,
    )


def _save_processed_videos(items: list[dict], day_name: str) -> None:
    db_path = _ingestion_db_path()
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO processed_videos (video_id, day, title, processed_at) VALUES (?, ?, ?, ?)",
            [(item.get("video_id"), day_name, item.get("title"), now) for item in items]
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Transcript-required mode
# ---------------------------------------------------------------------------
# When on (default), videos with no usable transcript are EXCLUDED from the
# digest rather than getting a low-value metadata-only retelling (which can
# never become a demo anyway — the demo gate rejects evidence_quality=
# metadata_only). The exclusion branches on WHY the transcript was missing:
#   * permanent (no captions exist) -> drop AND mark processed (don't retry).
#   * transient fetch block -> drop but do NOT mark processed, so it retries on
#     a later run (gold-channel videos re-enter via the poller's 30h window;
#     manually-queued videos retry if re-added). This avoids silently losing a
#     transcript-having video to a proxy/IP hiccup.
# Both are surfaced in a "Skipped — No Transcript" footer so nothing vanishes
# silently. Disable (restore metadata-only fallback) with
# UA_YOUTUBE_DIGEST_REQUIRE_TRANSCRIPT=0.
_RETRYABLE_TRANSCRIPT_FAILURE_CLASSES = {"request_blocked", "api_unavailable"}


def _require_transcript() -> bool:
    return os.getenv("UA_YOUTUBE_DIGEST_REQUIRE_TRANSCRIPT", "1").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _build_skipped_videos_footer(skipped: list[dict[str, Any]]) -> str:
    """Render a human-facing footer listing videos excluded for lack of a
    transcript, split into permanent (not retried) vs transient (will retry)."""
    if not skipped:
        return ""
    perm = [s for s in skipped if not s.get("retryable")]
    retry = [s for s in skipped if s.get("retryable")]
    lines = ["## Skipped — No Transcript", ""]

    def _row(s: dict[str, Any]) -> str:
        vid = s.get("video_id", "")
        return (
            f"- {s.get('title') or vid} — `{s.get('failure_class') or 'unknown'}` "
            f"([watch](https://www.youtube.com/watch?v={vid}))"
        )

    if perm:
        lines.append(
            f"**Excluded ({len(perm)})** — no usable transcript available; not retried:"
        )
        lines.extend(_row(s) for s in perm)
        lines.append("")
    if retry:
        lines.append(
            f"**Deferred ({len(retry)})** — transcript fetch failed transiently; "
            "left unprocessed to retry on a later run:"
        )
        lines.extend(_row(s) for s in retry)
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_daily_digest(
    dry_run: bool = False,
    day_override: str | None = None,
    email_to: str | None = None,
    auto_tutorial_top_n: int | None = None,
):
    initialize_runtime_secrets()

    # The cron fires the morning AFTER the user's content collection.
    # The user maintains 7 playlists named "<Day> Digest" — content
    # queued throughout Monday lives in "Monday Digest" and is summarised
    # by Tuesday's 8 AM run.  So Tuesday's run reads YESTERDAY's playlist:
    # MONDAY_YT_PLAYLIST.  `day_override` still uses the literal value the
    # operator passed (no shift), so manual reruns target an exact day.
    if day_override:
        day_name = day_override.upper()
    else:
        day_name = DAYS[(datetime.now() - timedelta(days=1)).weekday()]
    playlist_id_var = f"{day_name}_YT_PLAYLIST"
    playlist_id = os.getenv(playlist_id_var)

    if not playlist_id:
        logger.warning("No playlist configured for content day: %s (%s is not set)", day_name, playlist_id_var)
        return

    logger.info("Starting Daily Digest for %s (Playlist: %s)", day_name, playlist_id)

    try:
        items = get_playlist_items(playlist_id)
    except (YouTubeAPIError, YouTubeOAuthError) as e:
        logger.error("Failed to fetch playlist items: %s", e)
        # Re-raise so the run exits non-zero: a dead OAuth token / API failure
        # must surface as a FAILED cron run (cron_run_failed alert), not a
        # silent exit-0 that hides a broken digest for days.
        raise

    if not items:
        logger.info("Playlist is empty. Nothing to process today.")
        return

    logger.info("Found %d total videos in the playlist.", len(items))

    # Deduplicate against the database
    new_items = _filter_unprocessed_items(items, day_name)
    if not new_items:
        logger.info("No new videos to process for %s. All videos in the playlist were already processed. Exiting gracefully.", day_name)
        return

    logger.info("Found %d NEW videos to process for %s.", len(new_items), day_name)
    items = new_items

    transcripts: list[str] = []
    transcript_payloads: list[VideoTranscriptPayload] = []
    processed_items: list[dict] = []  # videos retold / included in the digest
    # Videos to write to processed_videos. == retold videos, PLUS permanently
    # transcript-less videos (so we don't re-fetch dead videos), but NOT
    # transiently-blocked ones (left unprocessed to retry). Diverges from
    # processed_items only in transcript-required mode.
    videos_to_persist: list[dict] = []
    skipped_videos: list[dict[str, Any]] = []  # excluded for lack of transcript
    video_titles: list[str] = []
    require_transcript = _require_transcript()

    # Load gold-channel duration overrides once. Maps video_owner_channel_id ->
    # max_duration_seconds_override. Used so e.g. Lex Fridman 3-hour interviews
    # don't get auto-triaged out of the digest by the global 90-minute cap.
    gold_duration_overrides = _load_gold_duration_overrides()

    for item in items:
        video_id = item["video_id"]
        title = item["title"]
        logger.info("Ingesting: %s (%s)", title, video_id)

        owner_channel_id = item.get("video_owner_channel_id") or ""
        duration_override = gold_duration_overrides.get(owner_channel_id)

        # Strategy: try proxy first (VPS path), then no-proxy, then metadata-only
        result = None
        for attempt_proxy in [True, False]:
            try:
                result = ingest_youtube_transcript(
                    video_url=None,
                    video_id=video_id,
                    require_proxy=attempt_proxy,
                    max_duration_seconds_override=duration_override,
                )
                if result.get("ok"):
                    break
                # If proxy-specific error, try without proxy
                detail = str(result.get("detail", ""))
                if attempt_proxy and ("407" in detail or "NO_USER" in detail or "proxy" in detail.lower()):
                    logger.info("Proxy failed for %s, retrying without proxy...", video_id)
                    continue
                break  # non-proxy error, don't retry
            except Exception as exc:
                logger.warning("Ingestion exception for %s (proxy=%s): %s", video_id, attempt_proxy, exc)
                if attempt_proxy:
                    continue
                result = {"ok": False, "error": str(exc)}

        if result and result.get("ok"):
            text = result.get("transcript_text", "")
            if len(text) > 50000:
                text = text[:50000] + "... [TRUNCATED]"

            result_metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            transcripts.append(f"Title: {title}\nVideo ID: {video_id}\nTranscript:\n{text}\n")
            transcript_payloads.append(
                VideoTranscriptPayload(
                    video_id=video_id,
                    title=title,
                    transcript_text=text,
                    is_metadata_only=False,
                    original_item=item,
                    metadata=result_metadata or {},
                )
            )
            processed_items.append(item)
            videos_to_persist.append(item)
            video_titles.append(title)
        else:
            if result:
                error_class = result.get("failure_class") or "unknown"
                error_name = result.get("error") or "unknown"
                error_detail = str(result.get("detail") or "")[:400]
            else:
                error_class = "unknown"
                error_name = "unknown"
                error_detail = ""
            logger.warning(
                "Failed to ingest %s: failure_class=%s error=%s detail=%s",
                video_id, error_class, error_name, error_detail,
            )

            if require_transcript:
                # No usable transcript -> exclude from the digest entirely (a
                # metadata-only retelling has no real content and can never be a
                # demo). Branch on WHY so we don't permanently lose a video that
                # only failed to fetch.
                retryable = error_class in _RETRYABLE_TRANSCRIPT_FAILURE_CLASSES
                skipped_videos.append({
                    "video_id": video_id,
                    "title": title,
                    "failure_class": error_class,
                    "retryable": retryable,
                })
                if retryable:
                    # Transient fetch block: leave UNPROCESSED so it retries on a
                    # later run (gold videos re-enter via the poller's 30h window;
                    # manual ones retry if re-queued).
                    logger.info(
                        "Skipping %s (transient %s) — left unprocessed to retry.",
                        video_id, error_class,
                    )
                else:
                    # Genuinely no transcript: mark processed so we don't re-fetch
                    # a dead video every run, but keep it out of the digest.
                    videos_to_persist.append(item)
                    logger.info(
                        "Skipping %s (%s) — no transcript; marked processed.",
                        video_id, error_class,
                    )
                continue

            # Legacy metadata-only fallback (UA_YOUTUBE_DIGEST_REQUIRE_TRANSCRIPT=0):
            # use title for synthesis (playlist API doesn't return description).
            # We still capture the metadata dict from the failed-fetch result if present —
            # the YouTube Data API call inside ingest_youtube_transcript runs even when the
            # transcript fetch fails, so channel/duration/upload_date are usually populated.
            result_metadata = (
                result.get("metadata") if isinstance(result, dict) and isinstance(result.get("metadata"), dict)
                else {}
            )
            fallback_text = f"[Metadata-only — transcript unavailable]\n\nTitle: {title}"
            transcripts.append(f"Title: {title}\nVideo ID: {video_id}\n{fallback_text}\n")
            transcript_payloads.append(
                VideoTranscriptPayload(
                    video_id=video_id,
                    title=title,
                    transcript_text="",
                    is_metadata_only=True,
                    original_item=item,
                    metadata=result_metadata or {},
                )
            )
            processed_items.append(item)
            videos_to_persist.append(item)
            video_titles.append(title)
            logger.info("Using metadata-only fallback for %s", video_id)

    if not transcripts:
        # No digest to produce or deliver. We intentionally do NOT mark anything
        # processed here — that preserves the invariant that processed-videos
        # writes only happen after a successful delivery (or email_to=None). The
        # cost is that on a rare all-transcript-less day these videos are
        # re-attempted next run; the common path (some transcripts succeed) still
        # marks the permanent-fails processed after delivery, below.
        logger.info(
            "No usable transcripts extracted (%d skipped). Exiting without a digest.",
            len(skipped_videos),
        )
        return

    # date_str is needed by both the map_reduce pipeline (passed to the reducer
    # for the H1 title line) and by artifact path construction below.
    date_str = datetime.now().strftime("%Y-%m-%d")
    pipeline = os.getenv("UA_YOUTUBE_DIGEST_PIPELINE", DIGEST_PIPELINE_DEFAULT).strip().lower()
    full_prompt = SYNTHESIS_PROMPT + "\n\n---\n\n".join(transcripts)

    try:
        if pipeline == "single_call":
            digest_content = asyncio.run(
                _generate_digest_content(full_prompt=full_prompt, pipeline_override="single_call")
            )
        else:
            digest_content = asyncio.run(
                _generate_digest_content(
                    videos=transcript_payloads,
                    day_name=day_name,
                    date_str=date_str,
                )
            )
    except Exception as e:
        logger.error("Failed to generate digest content with LLM (pipeline=%s): %s", pipeline, e)
        # Re-raise: an LLM failure produced no digest, so the run must exit
        # non-zero (FAILED cron run) instead of silently exiting 0.
        raise

    # Save Artifact to the persistent daily_digests workspace
    artifacts_dir = _digest_artifacts_dir()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    artifact_path = artifacts_dir / f"{date_str}_{day_name}_Digest.md"
    human_digest_content = _strip_digest_decision_blocks(digest_content)

    try:
        configured_top_n = (
            auto_tutorial_top_n
            if auto_tutorial_top_n is not None
            else int(os.getenv("UA_YOUTUBE_DIGEST_AUTO_TUTORIAL_TOP_N", "4"))
        )
    except ValueError:
        logger.warning("Invalid UA_YOUTUBE_DIGEST_AUTO_TUTORIAL_TOP_N; defaulting to 4")
        configured_top_n = 4
    try:
        configured_min_score = int(
            os.getenv("UA_YOUTUBE_DIGEST_DEMO_GATE_MIN_SCORE", str(DEMO_GATE_MIN_SCORE_DEFAULT))
        )
    except ValueError:
        logger.warning(
            "Invalid UA_YOUTUBE_DIGEST_DEMO_GATE_MIN_SCORE; defaulting to %d",
            DEMO_GATE_MIN_SCORE_DEFAULT,
        )
        configured_min_score = DEMO_GATE_MIN_SCORE_DEFAULT
    configured_max_n = _resolve_auto_tutorial_max_n(configured_top_n)
    decisions = _rank_digest_decisions(
        _extract_decision_json(digest_content),
        processed_items,
    )
    selected_tutorial_candidates = _select_tutorial_dispatch_candidates(
        decisions,
        top_n=configured_top_n,
        min_score=configured_min_score,
        max_n=configured_max_n,
    )
    candidates_path = _save_tutorial_candidates(
        day_name=day_name,
        date_str=date_str,
        artifact_path=artifact_path,
        decisions=decisions,
        selected=selected_tutorial_candidates,
        dry_run=dry_run,
        top_n=configured_top_n,
        max_n=configured_max_n,
    )
    dispatch_results = _dispatch_tutorial_candidates(
        selected=selected_tutorial_candidates,
        day_name=day_name,
        date_str=date_str,
        digest_artifact_path=artifact_path,
        candidates_artifact_path=candidates_path,
        dry_run=dry_run,
    )
    dispatch_path = None
    if dispatch_results:
        dispatch_path = candidates_path.with_name(candidates_path.stem + "_dispatch_results.json")
        dispatch_path.write_text(json.dumps(dispatch_results, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved tutorial dispatch results: %s", dispatch_path)

    # P3: the SAME gate-passing videos also enter the Demo ladder — tutorial_build
    # Task Hub rows through the shared daily ceiling (one ladder for both YouTube
    # sources; sha256(video_id) task ids dedupe against the broad CSI sweep).
    demo_queue_outcome = _queue_demo_builds(decisions=decisions, dry_run=dry_run)
    logger.info(
        "Demo-build lane queue outcome (tutorial_build, shared daily ceiling): %s",
        demo_queue_outcome,
    )

    tutorial_dispatch_summary = _format_tutorial_dispatch_summary(
        decisions=decisions,
        selected=selected_tutorial_candidates,
        dispatch_results=dispatch_results,
        candidates_path=candidates_path,
        dispatch_path=dispatch_path,
        top_n=configured_top_n,
        min_score=configured_min_score,
        max_n=configured_max_n,
        dry_run=dry_run,
    )
    skipped_footer = _build_skipped_videos_footer(skipped_videos)
    full_content = (
        f"# Daily YouTube Digest: {day_name.title()}, {date_str}\n\n"
        f"{human_digest_content}\n\n---\n\n{tutorial_dispatch_summary}\n"
    )
    if skipped_footer:
        full_content += f"\n\n---\n\n{skipped_footer}\n"

    with open(artifact_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    logger.info("Daily Digest saved to: %s", artifact_path)

    _save_repopulate_pocket(
        day_name=day_name,
        date_str=date_str,
        playlist_id=playlist_id,
        items=processed_items,
        artifact_path=artifact_path,
        dry_run=dry_run,
    )

    # Emit as a CSI digest record for dashboard visibility + proactive signal pipeline
    _emit_csi_digest(
        day_name=day_name,
        date_str=date_str,
        digest_content=full_content,
        video_count=len(processed_items),
        video_titles=video_titles,
    )

    # `email_to is None` is the intentional no-email mode (callers manage
    # delivery elsewhere).  Anything else: attempt delivery, and gate the
    # processed-videos DB write on success — otherwise a failed email
    # would burn the videos with no retry path.
    email_succeeded = email_to is None
    if email_to:
        logger.info("Sending email digest to %s...", email_to)
        # Build body (meta-synthesis only) + the full standalone HTML report.
        #
        # PRIMARY delivery is a LINK to the report published on the tailnet HTML
        # scratchpad. That page renders fully — styling AND the clickable per-video
        # index whose `#anchor` links actually jump to a section — on every one of the
        # operator's devices. An email ATTACHMENT cannot do that: Gmail strips in-email
        # `id=` anchors, flattens `.html` attachments to raw source, and a PDF only gets
        # a static bookmark outline (no in-document jump links). The scratchpad is
        # tailnet-only, which is fine here because the digest goes solely to the operator.
        #
        # We fall back to the PDF attachment (the prior behavior) ONLY if publishing
        # fails, so a digest is never dropped — a raw-but-delivered report beats nothing.
        body_md, attachment_md = _split_email_body_and_attachment(full_content)
        attachment_count = len(processed_items)
        attachment_html = _render_full_digest_html(
            attachment_md, day_name=day_name, date_str=date_str,
        )

        videos_phrase = (
            f"<strong>{attachment_count}</strong> "
            f"video{'s' if attachment_count != 1 else ''} from your "
            f"<em>{day_name.title()}</em> playlist"
        )

        # The "← Scratchpad index" back-link belongs ONLY on the scratchpad-served
        # page (it points at the tailnet artifact index). Inject it into a copy so the
        # PDF/HTML email-attachment fallback below — used only when publishing fails,
        # i.e. when there is no index page to return to — stays free of a dead link.
        scratch_html = attachment_html.replace(
            "<body>", f"<body>{scratch_back_link_html()}", 1,
        )
        scratch_url = publish_html_to_scratch(
            scratch_html,
            slug=f"yt-digest-{date_str}",
            filename=f"youtube-digest-{date_str}-{day_name.lower()}.html",
        )

        attachments_list: list[dict] = []
        if scratch_url:
            intro_html = (
                '<p style="margin:0 0 14px;">'
                f"Here's today's daily synthesis across {videos_phrase}. The themes "
                "are summarized below; the full per-video retellings — with a clickable "
                "index that jumps to any video — are in the rendered report:"
                "</p>"
                '<p style="margin:0 0 14px;">'
                f'<a href="{scratch_url}" '
                'style="display:inline-block;padding:9px 16px;background:#0969da;'
                'color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;">'
                "📄 Open the full YouTube digest</a>"
                "</p>"
                '<p style="margin:0 0 14px;color:#57606a;font-size:13px;">'
                "Opens on your own devices only (private, tailnet-served). Direct link: "
                f'<a href="{scratch_url}">{scratch_url}</a>'
                "</p>"
            )
            text_tail = (
                "\n\nFull per-video retellings + dispatch summary (rendered report, "
                f"with a clickable index): {scratch_url}\n"
            )
        else:
            # Publish failed — attach the report so the digest still lands. PDF first
            # (Gmail renders it inline on click); raw HTML only if PDF render itself
            # fails (e.g. a WeasyPrint native-lib hiccup).
            logger.warning(
                "Scratch publish returned no URL; falling back to email attachment.",
            )
            intro_html = (
                '<p style="margin:0 0 14px;">'
                f"Here's today's daily synthesis across {videos_phrase}. The themes "
                "are summarized below; the full per-video retellings — with a clickable "
                "index that jumps to any video — are in the attached report. Click the "
                "attachment to view it inline."
                "</p>"
            )
            try:
                attachment_pdf = _render_full_digest_pdf(attachment_html)
                attachment_filename = (
                    f"YouTube_Daily_Digest_{date_str}_{day_name.title()}.pdf"
                )
                attachment_payload = {
                    "content": base64.b64encode(attachment_pdf).decode("ascii"),
                    "filename": attachment_filename,
                    "content_type": "application/pdf",
                }
            except Exception as pdf_exc:  # noqa: BLE001 — degrade to HTML, never drop the digest
                logger.warning(
                    "PDF render failed (%s); falling back to HTML attachment.", pdf_exc,
                )
                attachment_filename = (
                    f"YouTube_Daily_Digest_{date_str}_{day_name.title()}.html"
                )
                attachment_payload = {
                    "content": base64.b64encode(attachment_html.encode("utf-8")).decode("ascii"),
                    "filename": attachment_filename,
                    "content_type": "text/html",
                }
            attachments_list = [attachment_payload]
            text_tail = (
                "\n\n(Full per-video retellings + dispatch summary are in the "
                f"attached file: {attachment_filename})\n"
            )

        # The email body is the short meta-synthesis summary only. In-email `#anchor`
        # jump links cannot work in Gmail (it strips `id=` attributes at render time),
        # and fully inlining the ~130KB digest blew past Gmail's ~102KB clip threshold.
        # The clickable per-video index lives in the rendered report (scratchpad link,
        # or the attachment in the fallback path), where anchors resolve in a browser.
        email_html = _render_email_body_html(
            body_md, intro_html=intro_html,
        )

        async def _send():
            mail = AgentMailService()
            await mail.startup()
            try:
                await mail.send_email(
                    to=email_to,
                    subject=f"Daily YouTube Summaries — {date_str}",
                    html=email_html,
                    text=body_md + text_tail,
                    attachments=attachments_list or None,
                    force_send=True,
                    require_approval=False,
                    action=ActionTag.FYI,
                    kind=KindTag.DIGEST,
                    source="youtube_daily_digest cron",
                    related=[f"day={day_name}", f"date={date_str}"],
                )
            finally:
                await mail.shutdown()
        try:
            asyncio.run(_send())
            logger.info("Email sent successfully.")
            email_succeeded = True
            try:
                from datetime import datetime as _dt, timezone as _tz
                reminder = send_digest_delivery_reminder(
                    subject=f"Daily YouTube Summaries — {date_str}",
                    recipient=email_to,
                    sent_at_utc=_dt.now(_tz.utc),
                )
                logger.info(
                    "Delivery reminder fired: telegram_ok=%s telegram_message_id=%s dashboard_event_id=%s expires_at=%s",
                    reminder.telegram_ok,
                    reminder.telegram_message_id,
                    reminder.dashboard_event_id,
                    reminder.expires_at_iso,
                )
            except Exception as reminder_exc:
                # Reminder failure is best-effort; never break the digest flow.
                logger.warning(
                    "Delivery reminder fan-out failed (non-fatal): %s", reminder_exc,
                )
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            _emit_proactive_delivery_failure(
                day_name=day_name,
                date_str=date_str,
                email_to=email_to,
                error=str(e),
            )

    if dry_run:
        logger.info("DRY RUN enabled. Skipping database state persistence.")
        return

    if not email_succeeded:
        logger.error(
            "Email delivery failed for %s; SKIPPING processed-videos DB write so "
            "the next cron tick can retry the same videos.",
            day_name,
        )
        # NOT silent: this path already emitted a proactive delivery-failure
        # alert via _emit_proactive_delivery_failure above, and intentionally
        # retries on the next tick. So it stays a graceful exit-0 — unlike the
        # OAuth/LLM paths, which raise (no prior alert, nothing produced).
        return

    logger.info("Saving state to processed_videos database...")
    _save_processed_videos(videos_to_persist, day_name)
    logger.info(
        "Recorded %d videos as processed for %s (%d retold + %d transcript-less; "
        "%d transient skips left for retry).",
        len(videos_to_persist), day_name, len(processed_items),
        len(videos_to_persist) - len(processed_items),
        sum(1 for s in skipped_videos if s.get("retryable")),
    )

    # Recreate the day-of-week playlist with the same name so the next day's
    # additions land in an empty playlist. Quota-cheap: 101 units flat instead
    # of N*50 per-item-delete. Failure here is non-fatal — the digest already
    # succeeded; an unrenamed playlist is a cleanup nuisance, not a delivery
    # issue. See `_recreate_playlist_after_digest` for the safe ordering.
    if os.getenv("UA_YOUTUBE_PLAYLIST_RECREATE_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            _recreate_playlist_after_digest(
                day_name=day_name,
                old_playlist_id=playlist_id,
                processed_count=len(videos_to_persist),
            )
        except Exception as recreate_exc:  # broad on purpose — never block on cleanup
            logger.warning(
                "Playlist recreate raised an unexpected exception for %s "
                "(digest succeeded; manual cleanup may be needed): %s",
                day_name, recreate_exc,
            )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the daily YouTube digest.")
    parser.add_argument("--dry-run", action="store_true", help="Do not delete videos from the playlist.")
    parser.add_argument("--day", type=str, default=None,
                        help="Override day of week (e.g., 'MONDAY'). Uses current day if not set.")
    parser.add_argument("--email-to", type=str, default="kevinjdragan@gmail.com", help="Email recipient for the digest.")
    parser.add_argument("--repopulate", action="store_true", help="Restore a day's digest playlist from its saved repopulate pocket.")
    parser.add_argument("--date", type=str, default=None, help="Pocket date to repopulate (YYYY-MM-DD). Defaults to the latest pocket for the day.")
    parser.add_argument(
        "--auto-tutorial-top-n",
        type=int,
        default=None,
        help="Number of ranked code-implementation prospects to dispatch to the tutorial pipeline. Defaults to UA_YOUTUBE_DIGEST_AUTO_TUTORIAL_TOP_N or 4.",
    )
    parser.add_argument(
        "--no-auto-tutorial-dispatch",
        action="store_true",
        help="Disable automatic tutorial dispatch for this digest run.",
    )
    args = parser.parse_args()

    if args.repopulate:
        # Same yesterday-shift as process_daily_digest: when no explicit
        # --day is passed, repopulate the previous day's content (which
        # this morning's run is summarising).
        day_upper = (args.day or DAYS[(datetime.now() - timedelta(days=1)).weekday()]).upper()
        if day_upper not in DAYS:
            logger.error("Invalid day: %s. Must be one of %s", args.day, DAYS)
            sys.exit(1)
        result = repopulate_digest_playlist(day_override=day_upper, date_override=args.date, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        sys.exit(0)

    if args.day:
        day_upper = args.day.upper()
        if day_upper not in DAYS:
            logger.error("Invalid day: %s. Must be one of %s", args.day, DAYS)
            sys.exit(1)
        logger.info("Day override: %s", day_upper)
        process_daily_digest(
            dry_run=args.dry_run,
            day_override=day_upper,
            email_to=args.email_to,
            auto_tutorial_top_n=0 if args.no_auto_tutorial_dispatch else args.auto_tutorial_top_n,
        )
    else:
        process_daily_digest(
            dry_run=args.dry_run,
            email_to=args.email_to,
            auto_tutorial_top_n=0 if args.no_auto_tutorial_dispatch else args.auto_tutorial_top_n,
        )
