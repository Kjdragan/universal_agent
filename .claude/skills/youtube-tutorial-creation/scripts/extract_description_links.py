# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27.0", "anthropic>=0.40.0", "pydantic>=2.0.0"]
# ///
"""Extract, classify, and fetch links from YouTube video descriptions.

Two-pass classification: (1) deterministic social domain pre-filter,
(2) LLM judge for remaining URLs using Anthropic structured output.
Falls back to regex classification when no LLM key is available.
Extracts URLs from free-form video descriptions, classifies them by type,
and optionally fetches high-value resources using direct connections (no proxy).

Usage:
    # From a youtube_ingest.json file
    uv run extract_description_links.py \\
        --ingest-json downloads/youtube_ingest.json \\
        --output-dir work_products/description_resources \\
        --report-json work_products/description_links_report.json

    # From a raw description string
    uv run extract_description_links.py \\
        --description "Check out https://github.com/user/repo" \\
        --output-dir /tmp/resources \\
        --report-json /tmp/report.json

    # Classify only, don't fetch
    uv run extract_description_links.py \\
        --description "..." --dry-run --report-json /tmp/report.json

    # Import check
    uv run extract_description_links.py --self-test
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, HttpUrl, field_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# High-value link types that should be fetched
HIGH_VALUE_TYPES = frozenset({
    "github_repo",
    "kaggle_competition",
    "kaggle_dataset",
    "documentation",
    "dataset",
})

# Social / promotional domains — always filtered out
_SOCIAL_DOMAINS = frozenset({
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
})

# Social path patterns — catch redirects like marimo.io/discord
_SOCIAL_PATH_KEYWORDS = frozenset({
    "discord",
    "newsletter",
    "subscribe",
    "donate",
    "sponsor",
})

# URL extraction regex — matches http/https URLs in free text
_URL_PATTERN = re.compile(
    r"https?://[^\s<>\"'\)\],;]+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pydantic models for structured LLM output
# ---------------------------------------------------------------------------

UrlCategory = Literal[
    "github_repo", "documentation", "blog_post", "api_reference",
    "dataset", "tool_page", "changelog", "kaggle_competition",
    "kaggle_dataset", "social", "promotional", "media_only", "other",
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
        if isinstance(v, str):
            return v.strip()
        return v


class UrlJudgmentResult(BaseModel):
    """Batch result from the LLM URL judge."""
    verdicts: list[UrlVerdict]


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
                        "worth_fetching": {"type": "boolean", "description": "Whether this URL is worth fetching"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "github_repo", "documentation", "blog_post",
                                "api_reference", "dataset", "tool_page",
                                "changelog", "kaggle_competition", "kaggle_dataset",
                                "promotional", "media_only", "other",
                            ],
                        },
                        "reasoning": {"type": "string", "description": "Brief explanation"},
                    },
                    "required": ["url", "worth_fetching", "category", "reasoning"],
                },
            },
        },
        "required": ["verdicts"],
    },
}

_URL_JUDGE_SYSTEM = """\
You are a URL value assessor for a technical video tutorial analysis pipeline.

Given a context (video title and description excerpt) and a list of candidate URLs,
evaluate which URLs are likely to contain high-value technical content worth fetching.

Guidelines:
- GitHub repos, documentation pages, blog posts with code → worth_fetching: true
- API references, changelogs, Kaggle competitions/datasets → worth_fetching: true
- Tool/product landing pages with technical docs → worth_fetching: true
- Generic marketing pages, newsletters, promotional → worth_fetching: false
- Media-only URLs (images, video embeds) → worth_fetching: false
- If uncertain, lean toward worth_fetching: true for technical domains
"""


# ---------------------------------------------------------------------------
# Core functions (importable by tests)
# ---------------------------------------------------------------------------


def extract_urls(text: str | None) -> list[str]:
    """Extract unique URLs from free-form text, preserving order."""
    if not text:
        return []
    raw = _URL_PATTERN.findall(text)
    # Strip trailing punctuation that may have been captured
    cleaned: list[str] = []
    seen: set[str] = set()
    for url in raw:
        url = url.rstrip(".,;:!?)>]}")
        if url not in seen:
            seen.add(url)
            cleaned.append(url)
    return cleaned


def classify_url(url: str) -> str:
    """Classify a single URL into a category.

    Returns one of: github_repo, kaggle_competition, kaggle_dataset,
    documentation, dataset, social, other.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "other"

    host = (parsed.netloc or "").lower().lstrip("www.")
    path = (parsed.path or "").lower().rstrip("/")
    full = f"{host}{path}"

    # --- Social domains (exact match) ---
    for domain in _SOCIAL_DOMAINS:
        if host == domain or host.endswith(f".{domain}"):
            return "social"

    # --- Social path keywords (catch redirects like marimo.io/discord) ---
    path_tail = path.rsplit("/", 1)[-1] if "/" in path else path
    if path_tail in _SOCIAL_PATH_KEYWORDS:
        return "social"

    # --- GitHub / GitLab repos ---
    if host in ("github.com", "gitlab.com"):
        # Must have at least user/repo (2 path segments)
        segments = [s for s in path.split("/") if s]
        if len(segments) >= 2:
            return "github_repo"
        return "other"

    # --- Kaggle ---
    if host == "kaggle.com" or host.endswith(".kaggle.com"):
        if "/competitions/" in path:
            return "kaggle_competition"
        if "/datasets/" in path:
            return "kaggle_dataset"
        return "other"

    # --- Hugging Face ---
    if host == "huggingface.co" or host.endswith(".huggingface.co"):
        return "dataset"

    # --- Documentation sites ---
    if "readthedocs.io" in host or "readthedocs.org" in host:
        return "documentation"
    if host.endswith(".pydata.org") and "/docs" in path:
        return "documentation"
    if host.startswith("docs.") or "/docs/" in path or "/docs" == path:
        return "documentation"

    return "other"


def _llm_judge_urls(
    urls: list[str],
    context: str,
    *,
    max_retries: int = 2,
) -> list[dict[str, Any]] | None:
    """Use Anthropic LLM with structured output to judge URL value.

    Returns list of dicts with url/type/worth_fetching/reasoning,
    or None if LLM is unavailable (caller should use regex fallback).
    """
    api_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ZAI_API_KEY")
    )
    if not api_key:
        return None  # Signal caller to use regex fallback

    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    client = Anthropic(**client_kwargs)
    user_prompt = (
        f"Context: {context[:2000]}\n\n"
        f"Candidate URLs to assess ({len(urls)}):\n"
        + "\n".join(f"  {i+1}. {url}" for i, url in enumerate(urls))
    )

    for attempt in range(max_retries):
        try:
            # Determine model — prefer ANTHROPIC_MODEL env var, fallback to Sonnet
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
            response = client.messages.create(
                model=model,
                max_tokens=1000,
                system=_URL_JUDGE_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[_URL_JUDGE_TOOL],
                tool_choice={"type": "tool", "name": "url_assessment"},
            )

            # Extract tool_use block
            raw_input = None
            for block in response.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    raw_input = block.input
                    break

            if raw_input is None:
                logger.warning("LLM URL judge attempt %d: no tool_use block", attempt + 1)
                continue

            # Validate with Pydantic
            result = UrlJudgmentResult.model_validate(raw_input)

            # Convert to link record format
            records: list[dict[str, Any]] = []
            for verdict in result.verdicts:
                # Map LLM categories to our type system
                link_type = str(verdict.category)
                records.append({
                    "url": str(verdict.url),
                    "type": link_type,
                    "worth_fetching": verdict.worth_fetching,
                    "reasoning": verdict.reasoning,
                    "fetched": False,
                    "resource_path": None,
                })

            # Ensure all input URLs are accounted for
            judged_urls = {r["url"].rstrip("/") for r in records}
            for url in urls:
                if url.rstrip("/") not in judged_urls:
                    records.append({
                        "url": url,
                        "type": classify_url(url),
                        "worth_fetching": True,
                        "reasoning": "missed by LLM judge, defaulting to regex classification",
                        "fetched": False,
                        "resource_path": None,
                    })

            logger.info(
                "LLM URL judge: %d URLs assessed, %d worth fetching",
                len(records),
                sum(1 for r in records if r.get("worth_fetching")),
            )
            return records

        except Exception as exc:
            logger.warning(
                "LLM URL judge attempt %d/%d failed: %s",
                attempt + 1, max_retries, exc,
            )
            if attempt == max_retries - 1:
                return None

    return None


def classify_and_filter(
    description: str | None,
    *,
    context_title: str = "",
    max_high_value: int = 5,
) -> list[dict[str, Any]]:
    """Extract URLs, classify each via two-pass approach, return structured link records.

    Pass 1: Deterministic social domain pre-filter (regex).
    Pass 2: LLM judge for remaining URLs (falls back to regex if no LLM key).

    Args:
        description: Free-form video description text.
        context_title: Video title for additional context to the LLM judge.
        max_high_value: Cap on high-value links to include (default 5).

    Returns:
        List of dicts with keys: url, type, fetched, resource_path, and optionally
        worth_fetching, reasoning (when LLM judge is used).
    """
    urls = extract_urls(description)
    if not urls:
        return []

    # Pass 1: deterministic social domain pre-filter
    social_records: list[dict[str, Any]] = []
    candidate_urls: list[str] = []
    for url in urls:
        link_type = classify_url(url)
        if link_type == "social":
            social_records.append({
                "url": url,
                "type": "social",
                "fetched": False,
                "resource_path": None,
                "worth_fetching": False,
                "reasoning": "social domain filtered",
            })
        else:
            candidate_urls.append(url)

    # Pass 2: LLM judge for candidates (or regex fallback)
    llm_context = f"Video: {context_title}\nDescription excerpt: {(description or '')[:500]}"
    llm_records = _llm_judge_urls(candidate_urls, llm_context) if candidate_urls else None

    if llm_records is not None:
        # LLM judge succeeded — use its classifications
        results = social_records + llm_records
    else:
        # Fallback to regex-only classification
        results = social_records
        for url in candidate_urls:
            link_type = classify_url(url)
            results.append({
                "url": url,
                "type": link_type,
                "fetched": False,
                "resource_path": None,
                "worth_fetching": link_type in HIGH_VALUE_TYPES,
                "reasoning": "regex classification (LLM unavailable)",
            })

    # Apply high-value cap
    capped_results: list[dict[str, Any]] = []
    high_value_count = 0
    for record in results:
        is_high_value = record.get("worth_fetching", record["type"] in HIGH_VALUE_TYPES)
        if is_high_value and high_value_count >= max_high_value:
            continue
        capped_results.append(record)
        if is_high_value:
            high_value_count += 1

    return capped_results


# ---------------------------------------------------------------------------
# Fetching logic
# ---------------------------------------------------------------------------


def _safe_filename(url: str, link_type: str) -> str:
    """Generate a safe filename from a URL."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").replace("www.", "").replace(".", "_")
    path = (parsed.path or "").strip("/").replace("/", "_")
    # Truncate to reasonable length
    name = f"{link_type}_{host}_{path}"[:120]
    # Remove unsafe chars
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return f"{name}.md"


def _fetch_github_repo(url: str, output_dir: Path, timeout: int) -> dict[str, Any]:
    """Shallow-clone a GitHub repo for full source access.

    Clones with --depth 1 into output_dir/<owner>__<repo>/ so the synthesis
    agent has the complete code tree.  Also writes a REPO_INFO.md with basic
    metadata (stars, language, description) fetched from the GitHub API.
    """
    import subprocess as sp

    import httpx

    parsed = urlparse(url)
    segments = [s for s in parsed.path.strip("/").split("/") if s]
    if len(segments) < 2:
        return {"ok": False, "error": "Invalid GitHub URL"}

    owner, repo = segments[0], segments[1]
    clone_dir_name = f"{owner}__{repo}"
    clone_path = output_dir / clone_dir_name

    # If already cloned (idempotent), skip
    if clone_path.exists() and (clone_path / ".git").exists():
        return {
            "ok": True,
            "path": str(clone_path),
            "dirname": clone_dir_name,
            "method": "git_clone_cached",
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    clone_url = f"https://github.com/{owner}/{repo}.git"

    # --- Shallow clone ---
    try:
        result = sp.run(
            ["git", "clone", "--depth", "1", clone_url, str(clone_path)],
            capture_output=True,
            text=True,
            timeout=max(timeout * 3, 60),  # clones may need more time
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            return {"ok": False, "error": f"git clone failed: {err}"}
    except sp.TimeoutExpired:
        # Clean up partial clone
        if clone_path.exists():
            import shutil
            shutil.rmtree(clone_path, ignore_errors=True)
        return {"ok": False, "error": f"Clone timed out for {clone_url}"}
    except FileNotFoundError:
        return {"ok": False, "error": "git not found on PATH"}

    # --- Write REPO_INFO.md with API metadata (best-effort) ---
    info_parts: list[str] = [
        f"# {owner}/{repo}\n",
        f"- **Cloned from**: {url}",
        f"- **Clone path**: `{clone_path}`\n",
    ]
    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(api_url, headers={"Accept": "application/vnd.github.v3+json"})
            if resp.status_code == 200:
                repo_info = resp.json()
                if repo_info.get("description"):
                    info_parts.append(f"> {repo_info['description']}\n")
                info_parts.append(f"- **Stars**: {repo_info.get('stargazers_count', 'N/A')}")
                info_parts.append(f"- **Language**: {repo_info.get('language', 'N/A')}")
                info_parts.append(f"- **Default branch**: {repo_info.get('default_branch', 'N/A')}")
                info_parts.append(f"- **License**: {(repo_info.get('license') or {}).get('spdx_id', 'N/A')}")
    except Exception:
        info_parts.append("\n_API metadata unavailable (rate-limited or offline)._")

    # Append file listing
    try:
        tree_result = sp.run(
            ["find", str(clone_path), "-not", "-path", "*/.git/*", "-not", "-path", "*/.git"],
            capture_output=True, text=True, timeout=10,
        )
        if tree_result.returncode == 0:
            files = [
                str(Path(p).relative_to(clone_path))
                for p in sorted(tree_result.stdout.strip().split("\n"))
                if p and p != str(clone_path)
            ]
            if files:
                info_parts.append("\n## File Tree\n")
                info_parts.append("```")
                for f in files[:150]:
                    info_parts.append(f)
                if len(files) > 150:
                    info_parts.append(f"... and {len(files) - 150} more files")
                info_parts.append("```")
    except Exception:
        pass

    (clone_path / "REPO_INFO.md").write_text("\n".join(info_parts), encoding="utf-8")

    return {
        "ok": True,
        "path": str(clone_path),
        "dirname": clone_dir_name,
        "method": "git_clone",
    }


def _fetch_web_page(url: str, output_dir: Path, link_type: str, timeout: int) -> dict[str, Any]:
    """Fetch a web page and save as markdown. Uses defuddle CLI if available, falls back to httpx."""
    import httpx
    import subprocess as sp

    filename = _safe_filename(url, link_type)
    output_path = output_dir / filename

    # Try defuddle first (best markdown extraction)
    try:
        result = sp.run(
            ["npx", "-y", "defuddle-cli@latest", url],
            capture_output=True,
            text=True,
            timeout=timeout + 10,  # Extra time for npx
            env={**__import__("os").environ, "NODE_NO_WARNINGS": "1"},
        )
        if result.returncode == 0 and result.stdout.strip():
            # defuddle returns JSON with content field
            try:
                data = json.loads(result.stdout)
                content = data.get("content") or data.get("markdown") or result.stdout
            except json.JSONDecodeError:
                content = result.stdout

            if len(content) > 20000:
                content = content[:20000] + "\n\n... [Content truncated at 20000 chars]"

            output_path.write_text(f"# Source: {url}\n\n{content}", encoding="utf-8")
            return {"ok": True, "path": str(output_path), "filename": filename, "method": "defuddle"}
    except (FileNotFoundError, sp.TimeoutExpired):
        pass  # defuddle not available, fall back to httpx

    # Fallback: plain httpx fetch
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; UABot/1.0; research pipeline)",
                "Accept": "text/html,application/xhtml+xml,*/*",
            })
            if resp.status_code == 200:
                text = resp.text
                if len(text) > 20000:
                    text = text[:20000] + "\n\n... [Content truncated at 20000 chars]"
                output_path.write_text(f"# Source: {url}\n\n{text}", encoding="utf-8")
                return {"ok": True, "path": str(output_path), "filename": filename, "method": "httpx"}
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except httpx.TimeoutException:
        return {"ok": False, "error": f"Timeout fetching {url}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def fetch_links(
    links: list[dict[str, Any]],
    output_dir: Path,
    *,
    timeout: int = 10,
) -> list[dict[str, Any]]:
    """Fetch content for high-value links. Modifies links in-place and returns them.

    Uses worth_fetching flag from LLM judge if available, otherwise falls back
    to checking type in HIGH_VALUE_TYPES.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    for link in links:
        # Use LLM judge verdict if available, otherwise fall back to type check
        should_fetch = link.get("worth_fetching", link["type"] in HIGH_VALUE_TYPES)
        if not should_fetch:
            continue

        link_type = link["type"]
        url = link["url"]

        if link_type == "github_repo":
            result = _fetch_github_repo(url, output_dir, timeout)
        else:
            result = _fetch_web_page(url, output_dir, link_type, timeout)

        if result.get("ok"):
            link["fetched"] = True
            link["resource_path"] = result.get("path")
        else:
            link["fetched"] = False
            link["error"] = result.get("error", "unknown")

    return links


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract, classify, and fetch links from YouTube video descriptions."
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--ingest-json",
        help="Path to youtube_ingest.json (reads metadata.description)",
    )
    input_group.add_argument(
        "--description",
        help="Raw description text",
    )
    parser.add_argument(
        "--output-dir",
        default="./description_resources",
        help="Directory to save fetched resources (default: ./description_resources)",
    )
    parser.add_argument(
        "--report-json",
        help="Path to write the structured JSON report",
    )
    parser.add_argument(
        "--max-links",
        type=int,
        default=5,
        help="Max high-value links to process (default: 5)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Per-link fetch timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify links without fetching content",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Verify imports and exit",
    )

    args = parser.parse_args()

    # Self-test mode
    if args.self_test:
        print("✅ extract_description_links: all imports OK")
        return 0

    # Resolve description text
    description = ""
    if args.ingest_json:
        ingest_path = Path(args.ingest_json)
        if not ingest_path.exists():
            print(f"❌ Ingest JSON not found: {ingest_path}", file=sys.stderr)
            return 1
        data = json.loads(ingest_path.read_text(encoding="utf-8"))
        metadata = data.get("metadata") or {}
        description = metadata.get("description") or ""
        if not description:
            print("ℹ️  No description in metadata — nothing to extract.")
            # Still write an empty report
            if args.report_json:
                report = {"links": [], "status": "skipped_no_description"}
                Path(args.report_json).parent.mkdir(parents=True, exist_ok=True)
                Path(args.report_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
            return 0
    elif args.description:
        description = args.description
    else:
        print("❌ Provide either --ingest-json or --description", file=sys.stderr)
        return 1

    # Extract and classify
    # Extract video title for LLM judge context
    video_title = ""
    if args.ingest_json:
        ingest_path = Path(args.ingest_json)
        if ingest_path.exists():
            data = json.loads(ingest_path.read_text(encoding="utf-8"))
            video_title = (data.get("metadata") or {}).get("title") or ""

    links = classify_and_filter(description, context_title=video_title, max_high_value=args.max_links)

    high_value = [l for l in links if l.get("worth_fetching", l["type"] in HIGH_VALUE_TYPES)]
    social = [l for l in links if l["type"] == "social"]

    print(f"📋 Extracted {len(links)} links: {len(high_value)} high-value, {len(social)} social")

    for link in links:
        if link.get("worth_fetching", link["type"] in HIGH_VALUE_TYPES):
            marker = "🔗"
        elif link["type"] == "social":
            marker = "🚫"
        else:
            marker = "⏭️"
        reasoning = f" ({link['reasoning']})" if link.get("reasoning") else ""
        print(f"  {marker} [{link['type']}] {link['url']}{reasoning}")

    # Fetch if not dry-run
    if not args.dry_run and high_value:
        output_dir = Path(args.output_dir)
        print(f"\n📥 Fetching {len(high_value)} high-value resources to {output_dir}...")
        fetch_links(links, output_dir, timeout=args.timeout)

        fetched = [l for l in links if l.get("fetched")]
        failed = [l for l in high_value if not l.get("fetched")]
        print(f"  ✅ Fetched: {len(fetched)}")
        if failed:
            print(f"  ❌ Failed: {len(failed)}")
            for f in failed:
                print(f"     - {f['url']}: {f.get('error', 'unknown')}")
    elif args.dry_run:
        print("\n🔍 Dry run — skipping fetch")

    # Write report
    status = "skipped_no_links" if not links else ("dry_run" if args.dry_run else "completed")
    report = {
        "status": status,
        "total_links": len(links),
        "high_value_count": len(high_value),
        "social_count": len(social),
        "links": links,
    }

    if args.report_json:
        report_path = Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n📄 Report written to {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
