"""LLM-judged URL enrichment pipeline for intelligence systems.

Three-pass architecture:
  Pass 1 — Fast regex pre-filter: strip social, self-referential, opaque URLs.
  Pass 2 — LLM judge: evaluate remaining URLs as strings for fetch-worthiness.
  Pass 3 — Selective fetch: only scrape approved URLs (defuddle → httpx fallback).

Used by both the CSI X-post pipeline and the YouTube tutorial pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess as sp
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, HttpUrl, field_validator

from universal_agent.utils.model_resolution import resolve_opus

logger = logging.getLogger(__name__)


# ── Tunable limits (env-configurable) ────────────────────────────────────────
# These caps were previously hard-coded at 20K storage / 3K analysis context /
# 3-fetch per post, which collapsed long official docs into excerpts before the
# downstream classifier ever saw them. v2 raises them so the analysis pass
# reads the full source. See docs/proactive_signals/claudedevs_intel_v2_design.md
# §6.2.

def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


DOC_STORAGE_MAX_CHARS = _env_int("UA_CSI_DOC_STORAGE_MAX_CHARS", 200_000)
DEFAULT_MAX_FETCH = _env_int("UA_CSI_MAX_FETCH_PER_POST", 10)


# ── Pydantic Models ──────────────────────────────────────────────────────────

UrlCategory = Literal[
    "github_repo",
    "documentation",
    "blog_post",
    "api_reference",
    "dataset",
    "tool_page",
    "changelog",
    "social_noise",
    "promotional",
    "media_only",
    "other",
]


class UrlVerdict(BaseModel):
    """A single URL's assessment from the LLM judge."""

    url: HttpUrl
    worth_fetching: bool
    category: UrlCategory
    reasoning: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def strip_url(cls, v: Any) -> Any:
        """Return the cached judgement for the URL, if present."""
        if isinstance(v, str):
            return v.strip()
        return v


class UrlJudgmentResult(BaseModel):
    """Batch result from the LLM URL judge."""

    verdicts: list[UrlVerdict]


class EnrichmentRecord(BaseModel):
    """A fully-processed URL record after all 3 passes."""

    url: str
    category: str = "other"
    worth_fetching: bool = False
    reasoning: str = ""
    fetch_status: str = "pending"  # pending | fetched | failed | skipped | filtered
    skip_reason: str = ""
    content_path: str = ""
    content_chars: int = 0
    post_id: str = ""


# ── Pass 1: Fast Regex Pre-Filter ────────────────────────────────────────────

SOCIAL_DOMAINS = frozenset({
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "linkedin.com",
    "discord.gg",
    "discord.com",
    "reddit.com",
    "bsky.app",
    "threads.net",
    "mastodon.social",
    "youtube.com",
    "youtu.be",
    "twitch.tv",
    "t.co",
    "bit.ly",
})

SOCIAL_PATH_KEYWORDS = frozenset({
    "discord",
    "newsletter",
    "subscribe",
    "donate",
    "sponsor",
    "merch",
})

# Product app domains — not content sources for intelligence
PRODUCT_APP_DOMAINS = frozenset({
    "claude.ai",
    "chat.openai.com",
    "chatgpt.com",
    "gemini.google.com",
})


# Tweet-URL extraction for the X API tweet-fetch fallback (Tier B1).
# Linked tweets carry real signal (replies, threads, quoted reactions). The
# pre_filter_urls() pass classifies every x.com/twitter.com URL as
# social_noise; this short-circuit pulls tweet-status URLs out first so we
# can fetch them via the X API /2/tweets/{id} endpoint and persist the
# tweet body as a synthesized source page. Non-tweet x.com URLs (profiles,
# search, etc.) still flow through pre_filter_urls and remain filtered.
_TWEET_STATUS_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/(\d+)",
    re.IGNORECASE,
)


def parse_tweet_id_from_url(url: str) -> str | None:
    """Extract the numeric tweet ID from an x.com/twitter.com /status/ URL."""
    if not url:
        return None
    match = _TWEET_STATUS_URL_PATTERN.match(url.strip())
    if not match:
        return None
    return match.group(1)


def extract_tweet_urls(urls: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Split URLs into (tweet_url_pairs, remaining_urls).

    tweet_url_pairs is [(url, tweet_id), ...] for x.com/twitter.com /status/
    URLs. remaining_urls is everything else, suitable to feed into
    pre_filter_urls().
    """
    tweet_pairs: list[tuple[str, str]] = []
    remaining: list[str] = []
    for url in urls:
        clean = url.strip().rstrip(".,)") if isinstance(url, str) else ""
        if not clean:
            continue
        tweet_id = parse_tweet_id_from_url(clean)
        if tweet_id:
            tweet_pairs.append((clean, tweet_id))
        else:
            remaining.append(clean)
    return tweet_pairs, remaining


def _x_api_tweet_fetch_enabled() -> bool:
    raw = str(os.getenv("UA_CSI_X_API_TWEET_FETCH_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def pre_filter_urls(urls: list[str]) -> tuple[list[str], list[EnrichmentRecord]]:
    """Pass 1: Fast deterministic filter.

    Returns (candidate_urls, discarded_records).
    Strips social domains, product apps, self-referential links, and t.co shortlinks.
    """
    candidates: list[str] = []
    discarded: list[EnrichmentRecord] = []

    for url in urls:
        clean = url.strip().rstrip(".,)")
        if not clean:
            continue

        try:
            parsed = urlparse(clean)
        except Exception:
            discarded.append(EnrichmentRecord(
                url=clean, category="other", fetch_status="filtered",
                skip_reason="unparseable_url",
            ))
            continue

        host = (parsed.netloc or "").lower().lstrip("www.")
        path = (parsed.path or "").lower().rstrip("/")

        # Social domain check
        is_social = any(
            host == domain or host.endswith(f".{domain}")
            for domain in SOCIAL_DOMAINS
        )
        if is_social:
            discarded.append(EnrichmentRecord(
                url=clean, category="social_noise", fetch_status="filtered",
                skip_reason="social_domain",
            ))
            continue

        # Product app check
        is_product = any(
            host == domain or host.endswith(f".{domain}")
            for domain in PRODUCT_APP_DOMAINS
        )
        if is_product:
            discarded.append(EnrichmentRecord(
                url=clean, category="other", fetch_status="filtered",
                skip_reason="product_app_not_content",
            ))
            continue

        # Social path keyword check (catch redirects like marimo.io/discord)
        path_tail = path.rsplit("/", 1)[-1] if "/" in path else path
        if path_tail in SOCIAL_PATH_KEYWORDS:
            discarded.append(EnrichmentRecord(
                url=clean, category="social_noise", fetch_status="filtered",
                skip_reason="social_path_keyword",
            ))
            continue

        candidates.append(clean)

    return candidates, discarded


# ── Pass 2: LLM Judge ────────────────────────────────────────────────────────

_URL_JUDGE_SYSTEM = """\
You are a URL value assessor for an engineering intelligence system.

Given a context (tweet text or video description) and a list of candidate URLs,
evaluate which URLs are likely to contain high-value technical content worth fetching.

Guidelines:
- GitHub repos, documentation pages, blog posts with code → worth_fetching: true
- API references, changelogs, release pages → worth_fetching: true
- Tool/product landing pages with technical docs → worth_fetching: true
- Generic marketing pages, newsletters, promotional → worth_fetching: false
- Media-only URLs (images, video embeds) → worth_fetching: false
- If uncertain, lean toward worth_fetching: true for technical domains

Categories:
- github_repo: GitHub/GitLab repository or code page
- documentation: Official docs, readthedocs, API docs
- blog_post: Technical blog post or article
- api_reference: API spec, reference docs, OpenAPI
- dataset: Hugging Face, Kaggle, data repositories
- tool_page: Product/tool page with technical substance
- changelog: Release notes, changelogs, version history
- promotional: Marketing, merch, non-technical promo
- media_only: Image, video, or media-only content
- other: Doesn't fit above categories
"""

# Anthropic tool_use schema for structured output
_URL_JUDGE_TOOL = {
    "name": "url_assessment",
    "description": "Return the assessment verdicts for each URL",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The URL being assessed"},
                        "worth_fetching": {"type": "boolean", "description": "Whether this URL is worth fetching for content extraction"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "github_repo", "documentation", "blog_post",
                                "api_reference", "dataset", "tool_page",
                                "changelog", "promotional", "media_only", "other",
                            ],
                        },
                        "reasoning": {"type": "string", "description": "Brief explanation of the assessment"},
                    },
                    "required": ["url", "worth_fetching", "category", "reasoning"],
                },
            },
        },
        "required": ["verdicts"],
    },
}


def _has_llm_key() -> bool:
    """Check if an Anthropic-compatible API key is available."""
    return bool(
        str(
            os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ZAI_API_KEY")
            or ""
        ).strip()
    )


def _call_llm_structured(*, system: str, user: str, tool: dict[str, Any], max_retries: int = 2) -> dict[str, Any]:
    """Call LLM with tool_use for structured output. Returns the tool input dict."""
    from anthropic import Anthropic

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError("No Anthropic-compatible API key available")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    client = Anthropic(**client_kwargs)

    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=resolve_opus(),
                max_tokens=1000,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
            # Extract tool_use block
            for block in response.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    return block.input  # type: ignore[return-value]

            # No tool_use block found — retry
            logger.warning("LLM URL judge attempt %d: no tool_use block in response", attempt + 1)
            continue

        except Exception as exc:
            logger.warning("LLM URL judge attempt %d failed: %s", attempt + 1, exc)
            if attempt == max_retries - 1:
                raise

    raise RuntimeError(f"LLM URL judge failed after {max_retries} attempts")


def _heuristic_judge_fallback(urls: list[str]) -> list[EnrichmentRecord]:
    """Fallback URL classification when LLM is unavailable.

    Uses domain-based heuristics similar to the legacy regex approach.
    """
    records: list[EnrichmentRecord] = []
    for url in urls:
        try:
            parsed = urlparse(url)
        except Exception:
            records.append(EnrichmentRecord(
                url=url, category="other", worth_fetching=False,
                fetch_status="skipped", skip_reason="unparseable_url",
            ))
            continue

        host = (parsed.netloc or "").lower().lstrip("www.")
        path = (parsed.path or "").lower()

        if host in ("github.com", "gitlab.com"):
            segments = [s for s in path.split("/") if s]
            if len(segments) >= 2:
                records.append(EnrichmentRecord(
                    url=url, category="github_repo", worth_fetching=True,
                    reasoning="heuristic: GitHub/GitLab repository",
                ))
                continue

        if host.startswith("docs.") or "/docs/" in path or "/docs" == path:
            records.append(EnrichmentRecord(
                url=url, category="documentation", worth_fetching=True,
                reasoning="heuristic: documentation domain/path",
            ))
            continue

        if "readthedocs" in host:
            records.append(EnrichmentRecord(
                url=url, category="documentation", worth_fetching=True,
                reasoning="heuristic: ReadTheDocs",
            ))
            continue

        if any(kw in host for kw in ("anthropic.com", "openai.com", "google.dev", "ai.google")):
            records.append(EnrichmentRecord(
                url=url, category="api_reference", worth_fetching=True,
                reasoning="heuristic: AI vendor domain",
            ))
            continue

        if host in ("huggingface.co",) or host.endswith(".huggingface.co"):
            records.append(EnrichmentRecord(
                url=url, category="dataset", worth_fetching=True,
                reasoning="heuristic: Hugging Face",
            ))
            continue

        # Default: worth fetching if it's a real web page
        records.append(EnrichmentRecord(
            url=url, category="other", worth_fetching=True,
            reasoning="heuristic: unknown domain, fetching for evaluation",
        ))

    return records


def judge_urls(urls: list[str], context: str) -> list[EnrichmentRecord]:
    """Pass 2: LLM judge evaluates URL strings for fetch-worthiness.

    Uses Anthropic tool_use for structured output with Pydantic validation.
    Falls back to heuristic classification if LLM is unavailable.
    """
    if not urls:
        return []

    if not _has_llm_key():
        logger.info("No LLM key available; using heuristic URL judge fallback")
        return _heuristic_judge_fallback(urls)

    user_prompt = (
        f"Context: {context[:2000]}\n\n"
        f"Candidate URLs to assess ({len(urls)}):\n"
        + "\n".join(f"  {i+1}. {url}" for i, url in enumerate(urls))
    )

    max_retries = 2
    for attempt in range(max_retries):
        try:
            raw = _call_llm_structured(
                system=_URL_JUDGE_SYSTEM,
                user=user_prompt,
                tool=_URL_JUDGE_TOOL,
            )

            # Validate with Pydantic
            result = UrlJudgmentResult.model_validate(raw)

            # Convert to EnrichmentRecords
            records: list[EnrichmentRecord] = []
            for verdict in result.verdicts:
                records.append(EnrichmentRecord(
                    url=str(verdict.url),
                    category=verdict.category,
                    worth_fetching=verdict.worth_fetching,
                    reasoning=verdict.reasoning,
                    fetch_status="skipped" if not verdict.worth_fetching else "pending",
                    skip_reason="llm_judge_not_worth_fetching" if not verdict.worth_fetching else "",
                ))

            # Verify all input URLs are accounted for
            judged_urls = {str(r.url).rstrip("/") for r in records}
            for url in urls:
                if url.rstrip("/") not in judged_urls:
                    logger.warning("LLM judge missed URL: %s — adding as worth_fetching=True", url)
                    records.append(EnrichmentRecord(
                        url=url, category="other", worth_fetching=True,
                        reasoning="missed by LLM judge, defaulting to fetch",
                    ))

            logger.info(
                "LLM URL judge: %d URLs assessed, %d worth fetching",
                len(records),
                sum(1 for r in records if r.worth_fetching),
            )
            return records

        except Exception as exc:
            logger.warning(
                "LLM URL judge attempt %d/%d failed validation: %s — %s",
                attempt + 1, max_retries, type(exc).__name__, exc,
            )
            if attempt == max_retries - 1:
                logger.warning("LLM URL judge exhausted retries; falling back to heuristic")
                return _heuristic_judge_fallback(urls)

    return _heuristic_judge_fallback(urls)


# ── Pass 3: Content Fetch ────────────────────────────────────────────────────

def _safe_filename(url: str, category: str) -> str:
    """Generate a safe filename from a URL."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").replace("www.", "").replace(".", "_")
    path = (parsed.path or "").strip("/").replace("/", "_")
    name = f"{category}_{host}_{path}"[:120]
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return f"{name}.md"


def _fetch_with_defuddle(url: str, output_path: Path, timeout: int) -> dict[str, Any]:
    """Try to fetch and convert to markdown using defuddle CLI."""
    try:
        result = sp.run(
            ["npx", "-y", "defuddle-cli@latest", url],
            capture_output=True,
            text=True,
            timeout=timeout + 10,
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                content = data.get("content") or data.get("markdown") or result.stdout
            except json.JSONDecodeError:
                content = result.stdout

            cap = DOC_STORAGE_MAX_CHARS
            if len(content) > cap:
                content = content[:cap] + f"\n\n... [Content truncated at {cap} chars]"

            output_path.write_text(f"# Source: {url}\n\n{content}", encoding="utf-8")
            return {"ok": True, "path": str(output_path), "method": "defuddle", "chars": len(content)}
    except (FileNotFoundError, sp.TimeoutExpired):
        pass

    return {"ok": False}


def _fetch_with_httpx(url: str, output_path: Path, timeout: int) -> dict[str, Any]:
    """Fallback: fetch with httpx and save raw content.

    Note: this writes the raw HTML response body to disk. Downstream consumers
    that expect extracted markdown (e.g. research_grounding) must validate the
    body — see research_grounding._looks_like_raw_html. When defuddle is
    unavailable on the host (no ``npx``/``defuddle-cli``), every URL falls
    through this path and the saved file is HTML markup, not prose.
    """
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            if resp.status_code == 200:
                text = resp.text
                cap = DOC_STORAGE_MAX_CHARS
                if len(text) > cap:
                    text = text[:cap] + f"\n\n... [Content truncated at {cap} chars]"
                output_path.write_text(f"# Source: {url}\n\n{text}", encoding="utf-8")
                # Operator-visible signal: when this fires for every URL on a
                # host, defuddle is missing or broken and downstream consumers
                # are getting raw HTML they need to validate themselves.
                if "<!doctype html>" in text[:2048].lower():
                    logger.warning(
                        "csi_url_judge._fetch_with_httpx: defuddle fallback saved raw HTML for %s "
                        "(file=%s). Downstream consumers must validate this is not markup-only content.",
                        url,
                        output_path,
                    )
                return {"ok": True, "path": str(output_path), "method": "httpx", "chars": len(text)}
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except httpx.TimeoutException:
        return {"ok": False, "error": f"Timeout fetching {url}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _fetch_github_repo(url: str, output_dir: Path, timeout: int) -> dict[str, Any]:
    """Fetch GitHub repo README via API (fast, no clone needed for intelligence)."""
    parsed = urlparse(url)
    segments = [s for s in parsed.path.strip("/").split("/") if s]
    if len(segments) < 2:
        return {"ok": False, "error": "Invalid GitHub URL"}

    owner, repo = segments[0], segments[1]
    readme_path = output_dir / f"github_{owner}_{repo}_readme.md"

    # Fetch README via GitHub API (much faster than cloning)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            # Try raw README first
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
            resp = client.get(raw_url)
            if resp.status_code != 200:
                # Try .rst
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.rst"
                resp = client.get(raw_url)
            if resp.status_code == 200:
                content = resp.text
                cap = DOC_STORAGE_MAX_CHARS
                if len(content) > cap:
                    content = content[:cap] + f"\n\n... [Content truncated at {cap} chars]"

                # Also get repo metadata
                api_url = f"https://api.github.com/repos/{owner}/{repo}"
                meta_parts = [f"# GitHub: {owner}/{repo}\n"]
                try:
                    meta_resp = client.get(api_url, headers={"Accept": "application/vnd.github.v3+json"})
                    if meta_resp.status_code == 200:
                        info = meta_resp.json()
                        if info.get("description"):
                            meta_parts.append(f"> {info['description']}\n")
                        meta_parts.append(f"- **Stars**: {info.get('stargazers_count', 'N/A')}")
                        meta_parts.append(f"- **Language**: {info.get('language', 'N/A')}")
                        meta_parts.append(f"- **License**: {(info.get('license') or {}).get('spdx_id', 'N/A')}")
                        meta_parts.append("")
                except Exception:
                    pass

                full_content = "\n".join(meta_parts) + "\n---\n\n" + content
                readme_path.write_text(full_content, encoding="utf-8")
                return {"ok": True, "path": str(readme_path), "method": "github_api", "chars": len(full_content)}

            return {"ok": False, "error": f"Could not fetch README for {owner}/{repo}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def fetch_url_content(
    url: str,
    category: str,
    output_dir: Path,
    *,
    timeout: int = 15,
) -> dict[str, Any]:
    """Pass 3: Fetch content from a URL approved by the judge.

    Uses GitHub API for repos, defuddle CLI for web pages, httpx as fallback.
    Returns dict with ok, path, method, chars fields.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # GitHub repos get special treatment — fetch README + metadata via API
    if category == "github_repo":
        return _fetch_github_repo(url, output_dir, timeout)

    # All other URLs: defuddle → httpx fallback
    filename = _safe_filename(url, category)
    output_path = output_dir / filename

    result = _fetch_with_defuddle(url, output_path, timeout)
    if result.get("ok"):
        return result

    return _fetch_with_httpx(url, output_path, timeout)


def _render_tweet_markdown(tweet_payload: dict[str, Any], url: str) -> str:
    """Render an X API /2/tweets/{id} response as a markdown source page."""
    data = tweet_payload.get("data") or {}
    includes_users = (tweet_payload.get("includes") or {}).get("users") or []
    author_lookup: dict[str, dict[str, Any]] = {}
    for user in includes_users:
        if isinstance(user, dict) and user.get("id"):
            author_lookup[str(user["id"])] = user

    text = str(data.get("text") or "").strip()
    tweet_id = str(data.get("id") or "").strip()
    created_at = str(data.get("created_at") or "").strip()
    author_id = str(data.get("author_id") or "").strip()
    author = author_lookup.get(author_id) or {}
    author_handle = str(author.get("username") or "").strip()
    author_name = str(author.get("name") or "").strip()
    metrics = data.get("public_metrics") or {}
    referenced = data.get("referenced_tweets") or []

    header_lines = [f"# Tweet: {url}", ""]
    if author_handle or author_name:
        byline = f"@{author_handle}" if author_handle else ""
        if author_name:
            byline = f"{author_name} ({byline})" if byline else author_name
        header_lines.append(f"- **Author**: {byline}")
    if created_at:
        header_lines.append(f"- **Created**: {created_at}")
    if tweet_id:
        header_lines.append(f"- **Tweet ID**: {tweet_id}")
    if metrics:
        header_lines.append(
            "- **Metrics**: "
            + ", ".join(f"{k}={v}" for k, v in metrics.items() if v is not None)
        )
    if referenced:
        refs = [
            f"{r.get('type', '?')}:{r.get('id', '?')}"
            for r in referenced
            if isinstance(r, dict)
        ]
        if refs:
            header_lines.append(f"- **Referenced**: {', '.join(refs)}")

    return "\n".join(header_lines) + "\n\n---\n\n" + text + "\n"


def _fetch_tweet_via_x_api(
    url: str,
    tweet_id: str,
    output_dir: Path,
    *,
    timeout: int,
) -> EnrichmentRecord:
    """Fetch one tweet via the X API and persist it as a synthesized source page.

    Returns an EnrichmentRecord shaped exactly like a regular fetched URL so
    downstream callers (build_linked_context, classifier prompts) treat it
    identically. On any failure, returns a record with fetch_status=failed
    and a structured skip_reason — never raises.
    """
    record = EnrichmentRecord(
        url=url,
        category="other",
        worth_fetching=True,
        reasoning="x_api_tweet_fetch: linked tweet body",
        fetch_status="pending",
    )

    try:
        from universal_agent.services.claude_code_intel import (
            fetch_tweet_by_id_with_fallbacks,
            get_x_bearer_token,
        )
    except Exception as exc:
        record.fetch_status = "failed"
        record.skip_reason = f"x_api_import_error:{type(exc).__name__}"
        logger.warning("X API tweet fetch unavailable: %s", exc)
        return record

    token = get_x_bearer_token()
    # Bearer absent AND no OAuth fallback creds → caller must downgrade to
    # the legacy social_noise filter for this URL. We signal that via a
    # specific skip_reason the orchestrator checks for.
    if not token and not all(
        str(os.getenv(key) or "").strip()
        for key in (
            "X_OAUTH_CONSUMER_KEY",
            "X_OAUTH_CONSUMER_SECRET",
            "X_OAUTH_ACCESS_TOKEN",
            "X_OAUTH_ACCESS_TOKEN_SECRET",
        )
    ) and not str(os.getenv("X_OAUTH2_ACCESS_TOKEN") or "").strip():
        record.fetch_status = "failed"
        record.skip_reason = "x_api_no_auth"
        return record

    try:
        with httpx.Client(timeout=timeout) as client:
            payload = fetch_tweet_by_id_with_fallbacks(
                client, token=token, tweet_id=tweet_id
            )
    except Exception as exc:
        record.fetch_status = "failed"
        reason = str(exc) or type(exc).__name__
        record.skip_reason = f"x_api_{reason[:120]}"
        logger.warning("X API tweet fetch failed for %s: %s", url, exc)
        return record

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        markdown = _render_tweet_markdown(payload, url)
        cap = DOC_STORAGE_MAX_CHARS
        if len(markdown) > cap:
            markdown = markdown[:cap] + f"\n\n... [Content truncated at {cap} chars]"
        md_path = output_dir / f"tweet_{tweet_id}.md"
        md_path.write_text(markdown, encoding="utf-8")
        # Also persist the raw JSON next to it for downstream debugging.
        json_path = output_dir / f"tweet_{tweet_id}.json"
        json_path.write_text(
            json.dumps(
                {k: v for k, v in payload.items() if k != "_ua_auth_mode"},
                indent=2,
            ),
            encoding="utf-8",
        )
        record.fetch_status = "fetched"
        record.content_path = str(md_path)
        record.content_chars = len(markdown)
        logger.info(
            "Fetched tweet via X API [%s] → %d chars at %s",
            url, record.content_chars, md_path,
        )
    except Exception as exc:
        record.fetch_status = "failed"
        record.skip_reason = f"x_api_persist_error:{type(exc).__name__}"
        logger.warning("Failed to persist X API tweet %s: %s", url, exc)
    return record


# ── Orchestrator ─────────────────────────────────────────────────────────────

def enrich_urls(
    urls: list[str],
    context: str,
    output_dir: Path,
    *,
    max_fetch: int | None = None,
    timeout: int = 15,
    trust_source: bool = False,
) -> list[EnrichmentRecord]:
    """Full 3-pass pipeline: pre-filter → LLM judge → selective fetch.

    Returns list of EnrichmentRecords with fetch status, content paths,
    and categories for downstream classification.

    `max_fetch` defaults to `DEFAULT_MAX_FETCH` (env `UA_CSI_MAX_FETCH_PER_POST`,
    default 10). Pass an explicit int to override per call.

    `trust_source=True` short-circuits the LLM judge: every URL that
    survives the pre-filter is marked `worth_fetching=True` and goes
    straight to the fetch pass. Use this for lanes where the upstream
    source is already curated as official (e.g. CSI lanes that only
    poll hand-picked handles like @ClaudeDevs / @bcherny). The judge
    was originally added to filter noise from open-web crawls; for
    intentional links from official handles it just drops the actual
    documentation we exist to capture. Set
    `UA_CSI_TRUST_SOURCE_BYPASS_JUDGE=0` in the env to disable.
    """
    if not urls:
        return []
    if max_fetch is None:
        max_fetch = DEFAULT_MAX_FETCH

    # Pass 0: pull tweet-status URLs aside so we can fetch them via the X API
    # /2/tweets/{id} endpoint. Without this short-circuit, pre_filter_urls()
    # classifies every x.com/twitter.com URL as social_noise and we lose
    # linked-tweet signal entirely. Set UA_CSI_X_API_TWEET_FETCH_ENABLED=0
    # to disable and fall back to legacy filtering.
    tweet_pairs: list[tuple[str, str]] = []
    if _x_api_tweet_fetch_enabled():
        tweet_pairs, remaining_urls = extract_tweet_urls(urls)
    else:
        remaining_urls = list(urls)

    # Pass 1: fast pre-filter (operates only on non-tweet URLs).
    candidates, discarded = pre_filter_urls(remaining_urls)
    if not candidates and not tweet_pairs:
        return discarded

    # Pass 2: LLM judge — bypassed when trust_source is on AND env permits.
    bypass_judge = trust_source and (
        os.getenv("UA_CSI_TRUST_SOURCE_BYPASS_JUDGE", "1").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if bypass_judge:
        records = [
            EnrichmentRecord(
                url=url,
                category="trusted_source",
                worth_fetching=True,
                reasoning="trust_source bypass: official-handle link, fetch unconditionally",
                fetch_status="pending",
            )
            for url in candidates
        ]
        logger.info(
            "URL judge bypassed (trust_source=True): %d URLs queued for fetch",
            len(records),
        )
    else:
        records = judge_urls(candidates, context)

    # Pass 3: selective fetch
    fetched_count = 0
    for record in records:
        if not record.worth_fetching:
            continue
        if fetched_count >= max_fetch:
            record.fetch_status = "skipped"
            record.skip_reason = "max_fetch_limit_reached"
            continue

        url_hash = hashlib.sha256(record.url.encode("utf-8")).hexdigest()[:12]
        fetch_dir = output_dir / url_hash
        fetch_dir.mkdir(parents=True, exist_ok=True)

        result = fetch_url_content(record.url, record.category, fetch_dir, timeout=timeout)
        if result.get("ok"):
            record.fetch_status = "fetched"
            record.content_path = result.get("path", "")
            record.content_chars = result.get("chars", 0)
            fetched_count += 1
            logger.info(
                "Fetched URL [%s] %s → %d chars via %s",
                record.category, record.url, record.content_chars, result.get("method"),
            )
        else:
            record.fetch_status = "failed"
            record.skip_reason = result.get("error", "unknown_fetch_error")
            logger.warning("Failed to fetch URL %s: %s", record.url, record.skip_reason)

    # Pass 4: X API tweet fetch — synthesizes EnrichmentRecords for linked
    # tweets that would otherwise be lost to the social_noise filter. Each
    # tweet costs one /2/tweets/{id} read; we keep them outside the
    # max_fetch budget because the API quota is independently rate-limited
    # and these are not web crawls.
    tweet_records: list[EnrichmentRecord] = []
    for tweet_url, tweet_id in tweet_pairs:
        url_hash = hashlib.sha256(tweet_url.encode("utf-8")).hexdigest()[:12]
        fetch_dir = output_dir / url_hash
        tweet_record = _fetch_tweet_via_x_api(
            tweet_url, tweet_id, fetch_dir, timeout=timeout
        )
        if tweet_record.skip_reason == "x_api_no_auth":
            # No usable creds. Downgrade this URL to the legacy filter so
            # behavior matches the pre-Tier-B1 pipeline.
            discarded.append(
                EnrichmentRecord(
                    url=tweet_url,
                    category="social_noise",
                    fetch_status="filtered",
                    skip_reason="social_domain",
                )
            )
            continue
        tweet_records.append(tweet_record)

    all_records = discarded + records + tweet_records
    logger.info(
        "URL enrichment complete: %d total, %d filtered, %d judged, %d fetched, %d tweets via x_api",
        len(all_records), len(discarded), len(records),
        sum(1 for r in records if r.fetch_status == "fetched"),
        sum(1 for r in tweet_records if r.fetch_status == "fetched"),
    )
    return all_records


def build_linked_context(
    records: list[EnrichmentRecord],
    *,
    max_content_chars: int | None = None,
) -> str:
    """Build a linked_context string from enrichment records for classify_post().

    Reads fetched content files and assembles a structured context string
    that the tier classifier can use for informed decisions.

    `max_content_chars=None` (default) returns the full fetched document.
    Pass a positive int to truncate per-source. v1 hard-coded 3,000 here,
    which collapsed long official docs into excerpts before the classifier
    ever saw them. v2 defaults to no truncation so the classifier reads what
    the storage layer actually fetched.
    """
    parts: list[str] = []
    for record in records:
        if record.fetch_status == "fetched" and record.content_path:
            content_path = Path(record.content_path)
            if content_path.exists():
                content = content_path.read_text(encoding="utf-8", errors="replace")
                if max_content_chars is not None and max_content_chars > 0:
                    excerpt = content[:max_content_chars]
                    label = "content_excerpt"
                else:
                    excerpt = content
                    label = "content"
                parts.append(
                    f"source_type={record.category} | "
                    f"url={record.url} | "
                    f"{label}={excerpt}"
                )
        elif record.category and record.category != "social_noise":
            # Even unfetched URLs provide signal via their category
            parts.append(
                f"source_type={record.category} | "
                f"url={record.url} | "
                f"worth_fetching={record.worth_fetching} | "
                f"status={record.fetch_status}"
            )

    return "\n".join(parts)
