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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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

from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.rate_limiter import ZAIRateLimiter
from universal_agent.services.agentmail_service import AgentMailService
from universal_agent.services.digest_delivery_reminder import (
    send_digest_delivery_reminder,
)
from universal_agent.services.youtube_playlist_manager import (
    YouTubeAPIError,
    YouTubeOAuthError,
    add_playlist_item,
    get_playlist_items,
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
# the map step rather than calling resolve_model("haiku"), because resolve_model
# globally points haiku at glm-5-turbo to dodge a historical preflight wedge
# (see model_resolution.py:11-30). Probe 2026-05-18 showed glm-4.5-air is stable
# at concurrency 1-3 for digest-shape prompts and ~30% faster per call than
# glm-5-turbo. Both models share the same account-level Fair-Usage-Policy
# throttle (Z.AI error 1313) when concurrency >= 5, so concurrency caps below
# are conservative.
DIGEST_MAP_MODEL_DEFAULT = "glm-4.5-air"
DIGEST_MAP_CONCURRENCY_DEFAULT = 3
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
    will handle that case by emitting a metadata-only classification."""
    video_id: str
    title: str
    transcript_text: str
    is_metadata_only: bool = False
    original_item: dict[str, Any] = field(default_factory=dict)


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
#   2. ### Retelling section (30-40% length retelling, NOT a summary)
#   3. ### Actionable Insights section (bullets)
#   4. ### Thesis (single short sentence — fed to the reducer for cross-video themes)
#   5. ```per_video_classification JSON fenced block (parsed by code, stripped from email)
#
# The map LLM does NOT see other videos and is told NOT to draw cross-video themes —
# that's the reducer's job.
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
<the 30-40% length retelling>

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

INPUT FOLLOWS:
"""


# ---------------------------------------------------------------------------
# Reduce-step prompt: sees only titles + classifications + thesis lines (NOT
# full retellings), produces the meta-synthesis (cross-video themes, learning
# insights, neglected opportunities) and the final ranked `youtube_digest_decisions`
# JSON block. Python code assembles the final markdown by sandwiching the
# retellings between the meta-synthesis and the JSON block.
# ---------------------------------------------------------------------------
REDUCE_PROMPT = """You are a senior technical analyst preparing a "Daily YouTube Digest" for a busy operator. You have ALREADY received per-video retellings from a map step; do NOT re-summarize the videos. Your job is meta-synthesis across them.

Below is the structured roll-up of every video in today's digest (titles, one-sentence theses, value scores, tiers, evidence quality, and the map-step's reasoning). Use this as your input.

Produce exactly the following markdown, in order:

1. A single H1 title line: `# Daily YouTube Digest: <day_name>, <date_str>`
2. `## Meta-Synthesis: Daily Digest`
3. `### Cross-Video Themes` — 3-7 themes that appear across multiple videos. Each theme is one short paragraph naming the relevant videos.
4. `### Learning Insights` — 2-5 non-obvious technical insights that wouldn't be apparent from any single video.
5. `### Neglected Opportunities` — 1-3 gaps (topics that should have been discussed but weren't).
6. Then a single fenced JSON block labeled exactly `youtube_digest_decisions` with this shape (one entry per video, sorted by value_score descending):

```youtube_digest_decisions
{{
  "ranked_videos": [
    {{
      "rank": 1,
      "video_id": "string",
      "title": "string",
      "value_score": <int 0-100>,
      "value_tier": "high|medium|low",
      "code_implementation_prospect": <bool>,
      "concept_only": <bool>,
      "tutorial_candidate": <bool>,
      "recommended_tutorial_mode": "explainer_plus_code|concept_only|none",
      "evidence_quality": "transcript|metadata_only|mixed",
      "reason": "short reason from the map step's classification"
    }}
  ]
}}
```

JSON rules:
- `tutorial_candidate` may be true only when `code_implementation_prospect` is true.
- `recommended_tutorial_mode` must be `explainer_plus_code` for tutorial_candidates, `concept_only` otherwise.
- Include every video from the input exactly once.
- Use the value_score and value_tier from the map-step input; do NOT re-score from scratch.

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
        return MapResult(
            video_id=video.video_id,
            title=video.title,
            retell_markdown=parsed["retell_markdown"],
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
        fallback_md = (
            f"## {video.title}\n\n"
            f"**Video ID:** {video.video_id}\n\n"
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

    results = await asyncio.gather(*[_bounded(v) for v in videos])
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
    return await _zai_call_with_retry(
        client=client,
        model=resolved_model,
        prompt=prompt,
        max_tokens=resolved_max_tokens,
        context="youtube_digest_reduce",
    )


def _assemble_map_reduce_digest(
    *,
    reduce_output: str,
    map_results: list[MapResult],
) -> str:
    """Combine the reducer's meta-synthesis (markdown + JSON block) with the
    map-step retellings into the final digest content. The retellings get
    inserted BEFORE the youtube_digest_decisions JSON block so the email
    body (`_strip_digest_decision_blocks`) carries them and the dispatch
    parser (`_extract_decision_json`) still finds the JSON at the end.
    """
    retellings_md = "\n\n---\n\n".join(r.retell_markdown for r in map_results)
    section = f"\n\n---\n\n## Per-Video Retellings\n\n{retellings_md}\n\n---\n\n"
    json_block_match = re.search(
        r"```(?:youtube_digest_decisions|json)\s*",
        reduce_output,
        flags=re.IGNORECASE,
    )
    if json_block_match:
        head = reduce_output[: json_block_match.start()].rstrip()
        tail = reduce_output[json_block_match.start():]
        return f"{head}{section}{tail}"
    # No JSON block in reducer output (shouldn't happen but fall back gracefully).
    return f"{reduce_output.rstrip()}{section}"


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
    return await _zai_call_with_retry(
        client=client,
        model=model,
        prompt=full_prompt,
        max_tokens=max_tokens,
        context="youtube_daily_digest",
        max_retries=max_retries,
    )


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
    return Path(os.getenv("UA_WORKSPACES_DIR") or (Path.cwd() / "AGENT_RUN_WORKSPACES"))


def _digest_artifacts_dir() -> Path:
    return _workspace_dir() / "daily_digests"


def _pockets_dir() -> Path:
    return _digest_artifacts_dir() / "repopulate_pockets"


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


def _select_tutorial_dispatch_candidates(
    decisions: dict[str, Any],
    *,
    top_n: int,
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    """Annotate every ranked row with a `dispatch_*` triplet and return only
    the top-N rows that pass the deterministic demo-worthiness gate.

    Side-effect: each ranked row in `decisions["ranked_videos"]` gains
    `dispatch_eligible`, `dispatch_reject_reason`, and `dispatch_status`
    fields so the saved candidates JSON and the human-facing email both
    surface why a video was (or wasn't) sent to the tutorial pipeline.
    """
    threshold = DEMO_GATE_MIN_SCORE_DEFAULT if min_score is None else max(0, int(min_score))
    selected: list[dict[str, Any]] = []
    for row in decisions.get("ranked_videos", []):
        ok, reason = _is_demo_worthy(row, min_score=threshold)
        row["dispatch_eligible"] = ok
        row["dispatch_reject_reason"] = "" if ok else reason
        if top_n <= 0 or not ok:
            row["dispatch_status"] = "rejected" if not ok else "disabled_top_n_zero"
            continue
        if len(selected) < top_n:
            row["dispatch_status"] = "selected"
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
) -> str:
    """Build the human-facing tutorial pipeline section for the digest email/artifact."""
    lines = [
        "## YouTube Tutorial Pipeline Dispatch",
        "",
        f"Automation target: top {max(0, top_n)} code implementation prospects "
        f"(deterministic demo-worthiness gate: score >= {min_score}, "
        f"value_tier not in {{low, unknown}}, evidence_quality != metadata_only).",
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
            lines.append(
                "- "
                f"#{row.get('rank')} {row.get('title') or video_id} "
                f"({video_id}) — score {row.get('value_score')}; {status}. "
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
    Feed, so it shows up alongside Reddit/Threads/YouTube RSS digests and
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
        return

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
    processed_items: list[dict] = []
    video_titles: list[str] = []

    for item in items:
        video_id = item["video_id"]
        title = item["title"]
        logger.info("Ingesting: %s (%s)", title, video_id)
        video_titles.append(title)

        # Strategy: try proxy first (VPS path), then no-proxy, then metadata-only
        result = None
        for attempt_proxy in [True, False]:
            try:
                result = ingest_youtube_transcript(
                    video_url=None,
                    video_id=video_id,
                    require_proxy=attempt_proxy,
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

            transcripts.append(f"Title: {title}\nVideo ID: {video_id}\nTranscript:\n{text}\n")
            transcript_payloads.append(
                VideoTranscriptPayload(
                    video_id=video_id,
                    title=title,
                    transcript_text=text,
                    is_metadata_only=False,
                    original_item=item,
                )
            )
            processed_items.append(item)
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

            # Metadata-only fallback: use title for synthesis (playlist API doesn't return description)
            fallback_text = f"[Metadata-only — transcript unavailable]\n\nTitle: {title}"
            transcripts.append(f"Title: {title}\nVideo ID: {video_id}\n{fallback_text}\n")
            transcript_payloads.append(
                VideoTranscriptPayload(
                    video_id=video_id,
                    title=title,
                    transcript_text="",
                    is_metadata_only=True,
                    original_item=item,
                )
            )
            processed_items.append(item)
            logger.info("Using metadata-only fallback for %s", video_id)

    if not transcripts:
        logger.info("No transcripts could be extracted. Exiting.")
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
        return

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
    decisions = _rank_digest_decisions(
        _extract_decision_json(digest_content),
        processed_items,
    )
    selected_tutorial_candidates = _select_tutorial_dispatch_candidates(
        decisions,
        top_n=configured_top_n,
        min_score=configured_min_score,
    )
    candidates_path = _save_tutorial_candidates(
        day_name=day_name,
        date_str=date_str,
        artifact_path=artifact_path,
        decisions=decisions,
        selected=selected_tutorial_candidates,
        dry_run=dry_run,
        top_n=configured_top_n,
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

    tutorial_dispatch_summary = _format_tutorial_dispatch_summary(
        decisions=decisions,
        selected=selected_tutorial_candidates,
        dispatch_results=dispatch_results,
        candidates_path=candidates_path,
        dispatch_path=dispatch_path,
        top_n=configured_top_n,
        min_score=configured_min_score,
        dry_run=dry_run,
    )
    full_content = (
        f"# Daily YouTube Digest: {day_name.title()}, {date_str}\n\n"
        f"{human_digest_content}\n\n---\n\n{tutorial_dispatch_summary}\n"
    )

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
        async def _send():
            mail = AgentMailService()
            await mail.startup()
            try:
                html_content = markdown.markdown(full_content, extensions=["extra", "nl2br"])
                await mail.send_email(
                    to=email_to,
                    subject=f"Daily YouTube Digest: {day_name.title()}",
                    html=f"<html><head><style>body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; max-width: 800px; margin: 0 auto; padding: 20px; }} h1, h2, h3 {{ border-bottom: 1px solid #eee; padding-bottom: 8px; }} img {{ max-width: 100%; }} blockquote {{ border-left: 4px solid #ddd; margin: 0; padding-left: 16px; color: #666; }} pre {{ background-color: #f6f8fa; padding: 16px; border-radius: 6px; overflow: auto; }}</style></head><body>{html_content}</body></html>",
                    text=full_content,
                    force_send=True,
                    require_approval=False,
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
                    subject=f"Daily YouTube Digest: {day_name.title()}",
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
        return

    logger.info("Saving state to processed_videos database...")
    _save_processed_videos(processed_items, day_name)
    logger.info("Successfully recorded %d videos as processed for %s.", len(processed_items), day_name)


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
