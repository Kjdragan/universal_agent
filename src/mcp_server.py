from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Load params from .env immediately
load_dotenv()

import os
import sys
import json
import re
import logging
import inspect
import heapq
from collections import Counter
from datetime import datetime, timezone
import traceback

# UA_LOG_LEVEL Control (INFO by default)
UA_LOG_LEVEL = os.getenv("UA_LOG_LEVEL", "INFO").upper()

# Global callback for UI redirection
_LOG_CALLBACK = None

def set_mcp_log_callback(callback):
    """Set a callback for mcp_log to redirect logs to UI/other consumers."""
    global _LOG_CALLBACK
    _LOG_CALLBACK = callback

def mcp_log(message: str, level: str = "INFO", prefix: str = "[Local Toolkit]"):
    """
    Log to stderr with level control. 
    Outputs appear in real-time in the Agent terminal.
    """
    # Order: DEBUG < INFO < WARNING < ERROR
    levels = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
    current_val = levels.get(UA_LOG_LEVEL, 20)
    msg_val = levels.get(level, 20)
    
    if msg_val >= current_val:
        # Route to UI if callback is registered
        if _LOG_CALLBACK:
            try:
                _LOG_CALLBACK(message, level, prefix)
            except Exception:
                pass
        
        sys.stderr.write(f"{prefix} {message}\n")
        sys.stderr.flush()

from functools import wraps
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Setup logger for MCP server
logger = logging.getLogger("mcp_server")
# Match standard logging level to UA_LOG_LEVEL
logging.basicConfig(level=getattr(logging, UA_LOG_LEVEL, logging.INFO))

# Ensure src path for imports
sys.path.append(os.path.abspath("src"))
# Ensure project root for Memory_System
sys.path.append(os.path.dirname(os.path.abspath(__file__)))  # src/
sys.path.append(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)  # Repo Root
from universal_agent.search_config import SEARCH_TOOL_CONFIG
from universal_agent.tools.corpus_refiner import refine_corpus_programmatic
from universal_agent.feature_flags import (
    heartbeat_enabled,
    memory_index_enabled,
    memory_orchestrator_enabled,
)
from tools.workbench_bridge import WorkbenchBridge
from composio import Composio

# Memory System Integration
disable_local_memory = os.getenv("UA_DISABLE_LOCAL_MEMORY", "").lower() in {
    "1",
    "true",
    "yes",
}

# Feature flags (placeholders for future gating; no behavior change yet)
# Feature flags (placeholders for future gating; no behavior change yet)
HEARTBEAT_ENABLED = heartbeat_enabled()
MEMORY_INDEX_ENABLED = memory_index_enabled()

# Local Memory System (Parity with Clawdbot)
# We use the universal_agent.memory package which implements Hindsight-like memory
# (Markdown source-of-truth + JSON/Vector index)
try:
    from universal_agent.memory.memory_store import (
        append_memory_entry, 
        ensure_memory_scaffold,
        _upsert_section  # Internal helper, reusing for core memory emulation
    )
    from universal_agent.memory.memory_models import MemoryEntry
    from universal_agent.tools.memory import ua_memory_search
    
    MEMORY_SYSTEM_AVAILABLE = True
    sys.stderr.write("[Local Toolkit] Memory System active (universal_agent.memory).\n")
except ImportError as e:
    sys.stderr.write(f"[Local Toolkit] Memory System imports failed: {e}\n")
    MEMORY_SYSTEM_AVAILABLE = False

# Backward-compat shim for older tests/codepaths that expect a MEMORY_MANAGER
# attribute on this module. The unified file memory path does not expose a
# single manager instance, so we retain a non-None sentinel when local memory
# is available.
MEMORY_MANAGER = None if (disable_local_memory or not MEMORY_SYSTEM_AVAILABLE) else object()

# Initialize Configuration
load_dotenv()

# Configure Logfire for MCP observability
try:
    import logfire
    from opentelemetry import trace as otel_trace

    if os.getenv("LOGFIRE_TOKEN"):
        logfire.configure(
            service_name="local-toolkit",
            send_to_logfire="if-token-present",
            inspect_arguments=False,  # Suppress InspectArgumentsFailedWarning
        )
        logfire.instrument_mcp()
        sys.stderr.write("[Local Toolkit] Logfire instrumentation enabled\n")
except ImportError:
    pass

TRACE_OUTPUT_ENABLED = os.getenv("UA_EMIT_LOCAL_TRACE_IDS", "1").lower() in {
    "1",
    "true",
    "yes",
}


def _current_trace_id() -> Optional[str]:
    """Get the current trace ID from the active span."""
    if not TRACE_OUTPUT_ENABLED:
        return None

    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:
        return None
    return None


def _attach_trace_output(text: str) -> str:
    trace_id = _current_trace_id()
    if not trace_id:
        return text
    return f"[local-toolkit-trace-id: {trace_id}]\n{text}"


def trace_tool_output(func):
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            if isinstance(result, str):
                return _attach_trace_output(result)
            return result

        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        if isinstance(result, str):
            return _attach_trace_output(result)
        return result

    return sync_wrapper


try:
    mcp_log("Server starting components...", level="DEBUG")
    mcp = FastMCP("Local Intelligence Toolkit")
except Exception:
    raise

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WORKSPACES_ROOT = (Path(PROJECT_ROOT) / "AGENT_RUN_WORKSPACES").resolve()
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _resolve_workspace(preferred: str | None = None) -> str | None:
    candidates = []
    if preferred:
        candidates.append(preferred)
        
    # Priority 2: Marker File (Dynamic, updated by harness)
    marker_path = os.getenv("CURRENT_SESSION_WORKSPACE_FILE") or os.path.join(
        PROJECT_ROOT, "AGENT_RUN_WORKSPACES", ".current_session_workspace"
    )
    if os.path.exists(marker_path):
        try:
            candidates.append(Path(marker_path).read_text().strip())
        except Exception:
            pass

    # Priority 3: Env Var (Static, often stale in long-running processes)
    env_workspace = os.getenv("CURRENT_SESSION_WORKSPACE")
    if env_workspace:
        candidates.append(env_workspace)

    for candidate in candidates:
        if not candidate:
            continue
        resolved = fix_path_typos(candidate)
        if os.path.exists(resolved):
            return resolved
    return None


def _resolve_workspaces_root() -> Path:
    raw = (os.getenv("UA_WORKSPACES_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_WORKSPACES_ROOT


def get_bridge():
    client = Composio(api_key=os.environ.get("COMPOSIO_API_KEY"))
    return WorkbenchBridge(composio_client=client, user_id="user_123")


def fix_path_typos(path: str) -> str:
    """
    Fix common model typos in workspace paths.
    Models sometimes truncate 'AGENT_RUN_WORKSPACES' to 'AGENT_RUNSPACES'.
    """
    # Fix: AGENT_RUNSPACES -> AGENT_RUN_WORKSPACES
    if "AGENT_RUNSPACES" in path and "AGENT_RUN_WORKSPACES" not in path:
        path = path.replace("AGENT_RUNSPACES", "AGENT_RUN_WORKSPACES")
        sys.stderr.write(
            f"[Local Toolkit] Path auto-corrected: AGENT_RUNSPACES → AGENT_RUN_WORKSPACES\n"
        )
    return path


@mcp.tool()
@trace_tool_output
def workbench_download(
    remote_path: str, local_path: str, session_id: str = None
) -> str:
    """
    Download a file from the Remote Composio Workbench to the Local Workspace.
    """
    bridge = get_bridge()
    result = bridge.download(remote_path, local_path, session_id=session_id)
    if result.get("error"):
        return f"Error: {result['error']}"
    return f"Successfully downloaded {remote_path} to {local_path}. Local path: {result.get('local_path')}"


@mcp.tool()
@trace_tool_output
def workbench_upload(local_path: str, remote_path: str, session_id: str = None) -> str:
    """
    Upload a file from the Local Workspace to the Remote Composio Workbench.
    """
    bridge = get_bridge()
    result = bridge.upload(local_path, remote_path, session_id=session_id)
    if result.get("error"):
        return f"Error: {result['error']}"
    return f"Successfully uploaded {local_path} to {remote_path}."


# =============================================================================
# CORPUS SIZE LIMITS - Conservative limits to prevent context overflow
# =============================================================================
BATCH_SAFE_THRESHOLD = 2500  # Files under this are "batch-safe" (auto-include)
# BATCH_MAX_TOTAL: Stop batch reading when cumulative word count hits this limit
# Set UA_BATCH_MAX_WORDS=100000 (or higher) for stress testing context limits
BATCH_MAX_TOTAL = int(os.getenv("UA_BATCH_MAX_WORDS", "50000"))
LARGE_FILE_THRESHOLD = 5000  # Files over this marked as "read individually"

# =============================================================================
# FILTERED CORPUS RULES (for report generation)
# =============================================================================
FILTER_BLACKLIST_DOMAINS = {
    "wikipedia.org",
}
FILTER_URL_SKIP_TOKENS = (
    "/live",
    "/liveblog",
    "/live-blog",
    "/home",
    "/topics",
    "/tag/",
    "/sitemap",
    "/video",
    "/videos",
    "/podcast",
    "/audio",
    "/photo",
)
FILTER_TITLE_SKIP_TOKENS = (
    "home",
    "live",
    "liveblog",
    "newsletter",
    "podcast",
    "video",
    "youtube",
    "watch",
    "listen",
    "most read",
    "trending",
)
FILTER_PROMO_TOKENS = (
    "subscribe",
    "sign in",
    "sign up",
    "support",
    "donate",
    "contribute",
    "membership",
    "account",
    "continue",
    "payment",
    "your support",
)
FILTER_URL_ALLOW_PATTERNS = (
    # Al Jazeera key events timeline pages (useful despite list structure)
    "aljazeera.com/news/202",
    "aljazeera.com/news/2025/",
    "aljazeera.com/news/2026/",
    "russia-ukraine-war-list-of-key-events",
    # ISW assessment pages sometimes render with "Home" titles
    "understandingwar.org/research/russia-ukraine/",
)

TOPIC_TERM_STOPWORDS = {
    "latest",
    "news",
    "update",
    "live",
    "report",
    "reports",
    "analysis",
    "assessment",
    "briefing",
    "watch",
    "listen",
    "video",
    "podcast",
    "blog",
    "today",
    "yesterday",
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
TOPIC_TERM_MIN_LEN = 4
TOPIC_MAX_TERMS = 24
TOPIC_MIN_TERMS = 3
TOPIC_MIN_WORDS = 300
TOPIC_MAX_WORDS = 1800
TOPIC_MIN_NARRATIVE_RATIO = 0.12
TOPIC_MIN_HIT_RATE = 0.7

LINK_MARKERS = ("http://", "https://", "](", "<http", "www.")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
URL_RE = re.compile(r"https?://\S+")
HEADLINE_TIME_RE = re.compile(
    r"\b(?:mins?|minutes?|hrs?|hours?)\s+ago\b", re.IGNORECASE
)
HEADLINE_NUMBER_RE = re.compile(r"^\s*\d+[\.)]\s+")


def _split_front_matter(raw_text: str) -> tuple[dict, str, str]:
    if raw_text.startswith("---"):
        parts = raw_text.split("---", 2)
        if len(parts) >= 3:
            meta_block = parts[1]
            body = parts[2]
            meta = {}
            for line in meta_block.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip()
            return meta, meta_block.strip(), body.strip()
    return {}, "", raw_text.strip()


def _load_task_config(task_dir: str) -> dict:
    for filename in ("task_config.json", "task_metadata.json"):
        path = os.path.join(task_dir, filename)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Failed to load task config {path}: {e}")
    return {}


def _is_promotional(text: str) -> bool:
    lowered = text.lower()
    hits = sum(lowered.count(token) for token in FILTER_PROMO_TOKENS)
    return hits >= 4


def _word_count(text: str) -> int:
    return len(text.split())


def _line_has_link(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in LINK_MARKERS)


def _line_is_link_only(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    without_md = MARKDOWN_LINK_RE.sub("", stripped)
    without_urls = URL_RE.sub("", without_md)
    compact = re.sub(r"[^A-Za-z0-9]+", "", without_urls)
    return len(compact) < 4


def _line_is_headline_item(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if HEADLINE_TIME_RE.search(stripped):
        return True
    if HEADLINE_NUMBER_RE.match(stripped):
        return True
    if stripped.startswith(("*", "•")) and len(stripped) > 40:
        return True
    return False


def _line_is_narrative(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if _line_is_link_only(stripped) or _line_is_headline_item(stripped):
        return False
    words = stripped.split()
    return len(words) >= 12


def _normalize_topic_terms(terms: list[str]) -> list[str]:
    normalized = []
    for term in terms:
        if not isinstance(term, str):
            continue
        cleaned = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
        if not cleaned:
            continue
        if cleaned in TOPIC_TERM_STOPWORDS:
            continue
        if cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def _extract_topic_terms_from_texts(texts: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    for text in texts:
        if not isinstance(text, str):
            continue
        tokens = re.findall(r"[a-z0-9][a-z0-9'\-]{2,}", text.lower())
        for token in tokens:
            token = token.strip("-'")
            if len(token) < TOPIC_TERM_MIN_LEN:
                continue
            if token in TOPIC_TERM_STOPWORDS:
                continue
            counts[token] += 1
    if not counts:
        return []
    return [term for term, _ in counts.most_common(TOPIC_MAX_TERMS)]


def _build_topic_terms(search_texts: list[str], topic_keywords: list[str]) -> list[str]:
    normalized_keywords = _normalize_topic_terms(topic_keywords)
    extracted_terms = _extract_topic_terms_from_texts(search_texts)
    combined = []
    for term in normalized_keywords + extracted_terms:
        if term not in combined:
            combined.append(term)
    return combined


def _compile_topic_terms(terms: list[str]) -> list[tuple[str, re.Pattern | None]]:
    compiled = []
    for term in terms:
        if " " in term:
            compiled.append((term, None))
        else:
            compiled.append((term, re.compile(rf"\b{re.escape(term)}\b")))
    return compiled


def _collect_search_texts(data: dict, config: dict | None) -> list[str]:
    texts: list[str] = []
    for key in ("query", "search_query", "answer"):
        value = data.get(key)
        if isinstance(value, str) and value:
            texts.append(value)
    if config:
        items = data.get(config["list_key"], [])
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                for field in ("title", "snippet", "source"):
                    value = item.get(field)
                    if isinstance(value, str) and value:
                        texts.append(value)
    return texts


def _line_has_topic(text: str, topic_terms: list[tuple[str, re.Pattern | None]]) -> bool:
    lowered = text.lower()
    for term, pattern in topic_terms:
        if pattern:
            if pattern.search(lowered):
                return True
        elif term in lowered:
            return True
    return False


def _topic_relevance_stats(
    text: str, topic_terms: list[tuple[str, re.Pattern | None]]
) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    total_lines = len(lines)
    line_hits = 0
    narrative_lines = 0
    narrative_hits = 0
    for line in lines:
        if _line_has_topic(line, topic_terms):
            line_hits += 1
        if _line_is_narrative(line):
            narrative_lines += 1
            if _line_has_topic(line, topic_terms):
                narrative_hits += 1
    lowered = text.lower()
    total_hits = 0
    for term, pattern in topic_terms:
        if pattern:
            total_hits += len(pattern.findall(lowered))
        else:
            total_hits += lowered.count(term)
    word_count = _word_count(text)
    return {
        "total_lines": total_lines,
        "line_hits": line_hits,
        "line_ratio": line_hits / total_lines if total_lines else 0,
        "narrative_lines": narrative_lines,
        "narrative_hits": narrative_hits,
        "narrative_ratio": (
            narrative_hits / narrative_lines if narrative_lines else 0
        ),
        "total_hits": total_hits,
        "hit_rate": (total_hits / word_count) * 100 if word_count else 0,
    }


def _url_is_blacklisted(url: str) -> bool:
    lowered = url.lower()
    return any(domain in lowered for domain in FILTER_BLACKLIST_DOMAINS)


def _url_title_gate(meta: dict) -> tuple[bool, str]:
    url = (meta.get("source") or "").lower()
    title = (meta.get("title") or "").lower()
    if any(pattern in url for pattern in FILTER_URL_ALLOW_PATTERNS):
        return True, "allowlist"
    if _url_is_blacklisted(url):
        return False, "domain_blacklist"
    if any(token in url for token in FILTER_URL_SKIP_TOKENS):
        return False, "url_skip"
    if any(token in title for token in FILTER_TITLE_SKIP_TOKENS):
        return False, "title_skip"
    return True, "ok"


def _remove_navigation_lines(body: str) -> tuple[str, dict]:
    lines = []
    stats = {
        "total_lines": 0,
        "short_lines": 0,
        "link_lines": 0,
        "link_only_lines": 0,
        "headline_lines": 0,
        "narrative_lines": 0,
    }
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stats["total_lines"] += 1
        if _line_has_link(stripped):
            stats["link_lines"] += 1
        if _line_is_link_only(stripped):
            stats["link_only_lines"] += 1
        if _line_is_headline_item(stripped):
            stats["headline_lines"] += 1
        if _line_is_narrative(stripped):
            stats["narrative_lines"] += 1
        if (
            stripped.startswith("#")
            or stripped.startswith("!")
        ):
            continue
        # Only remove bullet points if they are likely navigation items (short)
        if stripped.startswith("*") and len(stripped) < 60:
            continue
        if len(stripped) < 40:
            stats["short_lines"] += 1
            continue
        lines.append(stripped)
    return "\n".join(lines).strip(), stats


def _filter_crawl_content(
    raw_text: str,
    topic_terms: list[tuple[str, re.Pattern | None]] | None = None,
    enable_topic_filter: bool = True,
) -> tuple[str | None, str, dict, str]:
    meta, meta_block, body = _split_front_matter(raw_text)
    ok, reason = _url_title_gate(meta)
    if not ok:
        return None, reason, meta, meta_block

    cleaned, stats = _remove_navigation_lines(body)
    word_count = _word_count(cleaned)
    if word_count < 225:
        return None, "too_short", meta, meta_block
    if word_count < 300 and _is_promotional(cleaned):
        return None, "promo_short", meta, meta_block
    if stats["short_lines"] > 300 and word_count < 800:
        return None, "nav_heavy", meta, meta_block
    if stats["total_lines"]:
        link_only_ratio = stats["link_only_lines"] / stats["total_lines"]
        link_ratio = stats["link_lines"] / stats["total_lines"]
        headline_ratio = stats["headline_lines"] / stats["total_lines"]
        narrative_ratio = stats["narrative_lines"] / stats["total_lines"]
    else:
        link_only_ratio = 0
        link_ratio = 0
        headline_ratio = 0
        narrative_ratio = 0
    if link_only_ratio >= 0.25 and word_count < 1200:
        return None, "link_list", meta, meta_block
    if link_ratio >= 0.45 and narrative_ratio < 0.2 and word_count < 1500:
        return None, "link_dense", meta, meta_block
    if headline_ratio >= 0.35 and narrative_ratio < 0.25 and word_count < 1600:
        return None, "headline_list", meta, meta_block
    if narrative_ratio < 0.12 and word_count < 600:
        return None, "low_narrative", meta, meta_block
    topic_terms = topic_terms or []
    if enable_topic_filter and len(topic_terms) >= TOPIC_MIN_TERMS:
        topic_stats = _topic_relevance_stats(cleaned, topic_terms)
        if (
            word_count >= TOPIC_MIN_WORDS
            and word_count <= TOPIC_MAX_WORDS
            and topic_stats["narrative_lines"] >= 3
            and topic_stats["narrative_ratio"] < TOPIC_MIN_NARRATIVE_RATIO
            and topic_stats["hit_rate"] < TOPIC_MIN_HIT_RATE
        ):
            return None, "topic_low", meta, meta_block
    return cleaned, "ok", meta, meta_block


@mcp.tool()
@trace_tool_output
def read_research_files(file_paths: list[str]) -> str:
    """
    DEPRECATED: Use refined_corpus.md from finalize_research instead.
    
    This function is no longer used in the standard workflow. The corpus refiner
    integrated into finalize_research creates a pre-extracted, token-efficient
    refined_corpus.md that report-writer reads directly.
    
    Kept for backwards compatibility only.

    Args:
        file_paths: List of file paths to read (from research_overview.md listing)

    Returns:
        Combined content of ALL files, separated by clear markers.
        Each file section includes the filename and word count.
    """
    if not file_paths:
        return "Error: No file paths provided"

    MAX_BATCH_CHARS = 75000
    MAX_SINGLE_FILE_CHARS = 50000  # Truncate individual files larger than this
    results = []
    current_chars = 0
    success_count = 0
    truncated = False
    remaining_start_idx = 0
    remapped_files = []
    truncated_files = []  # Track files that were truncated

    for i, path in enumerate(file_paths):
        try:
            original_path = path  # Keep for reference
            path = fix_path_typos(path)
            abs_path = os.path.abspath(path)

            # Prefer filtered corpus when raw crawl paths are provided.
            if (
                "/search_results/" in abs_path
                and "/search_results_filtered_best/" not in abs_path
            ):
                filtered_path = abs_path.replace(
                    "/search_results/", "/search_results_filtered_best/"
                )
                if os.path.exists(filtered_path):
                    remapped_files.append((abs_path, filtered_path))
                    abs_path = filtered_path

            if not os.path.exists(abs_path):
                results.append(
                    f"\n{'=' * 60}\n❌ FILE NOT FOUND: {original_path}\n{'=' * 60}\n"
                )
                continue

            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()

            content_len = len(content)
            filename = os.path.basename(path)

            # Truncate oversized individual files to prevent context exhaustion
            if content_len > MAX_SINGLE_FILE_CHARS:
                original_len = content_len
                content = content[:MAX_SINGLE_FILE_CHARS]
                content += f"\n\n... [TRUNCATED: File was {original_len:,} chars, showing first {MAX_SINGLE_FILE_CHARS:,}] ..."
                content_len = len(content)
                truncated_files.append((filename, original_len))

            # Smart Batching Check:
            # If we already have content, and adding this file would exceed limit, STOP.
            if results and (current_chars + content_len > MAX_BATCH_CHARS):
                truncated = True
                remaining_start_idx = i
                break

            word_count = len(content.split())
            current_chars += content_len
            success_count += 1

            # Add clear section marker
            results.append(
                f"\n{'=' * 60}\n"
                f"📄 FILE: {filename} ({word_count:,} words)\n"
                f"{'=' * 60}\n\n"
                f"{content}"
            )
        except Exception as e:
            results.append(
                f"\n{'=' * 60}\n❌ ERROR reading {path}: {str(e)}\n{'=' * 60}\n"
            )

    # Add summary header
    header = (
        f"# Research Files Batch Read\n"
        f"**Files read:** {success_count}/{len(file_paths)}\n"
        f"**Total chars:** {current_chars:,} (Limit: {MAX_BATCH_CHARS:,})\n"
    )
    if remapped_files:
        remap_lines = "\n".join(
            f"- {os.path.basename(src)} → {os.path.basename(dst)}"
            for src, dst in remapped_files
        )
        header += f"\n**Remapped to filtered corpus:**\n{remap_lines}\n"
    if truncated_files:
        trunc_lines = "\n".join(
            f"- {fname}: {orig_len:,} chars → {MAX_SINGLE_FILE_CHARS:,} chars"
            for fname, orig_len in truncated_files
        )
        header += f"\n⚠️ **Truncated oversized files:**\n{trunc_lines}\n"
    header += "\n"

    combined_output = header + "\n".join(results)

    if truncated:
        remaining_files = file_paths[remaining_start_idx:]
        combined_output += (
            f"\n\n{'=' * 60}\n"
            f"⚠️  BATCH TRUNCATED TO AVOID ERRORS (Limit {MAX_BATCH_CHARS:,} chars)\n"
            f"Read {success_count} of {len(file_paths)} requested files.\n"
            f"{'=' * 60}\n"
            f"👇 TO CONTINUE, CALL TOOL AGAIN WITH THESE FILES:\n"
            f"{json.dumps(remaining_files)}\n"
            f"{'=' * 60}\n"
        )

    return combined_output


@mcp.tool()
@trace_tool_output
async def draft_report_parallel(retry_id: str = "", task_name: str = "default") -> str:
    """
    Execute the Python-based parallel drafting system to generate report sections concurrently.
    
    This tool:
    1. Reads `work_products/_working/outline.json`
    2. Reads `tasks/[task_name]/refined_corpus.md`
    3. Spawns concurrent LLM workers (AsyncAnthropic) to write sections
    4. Saves output to `work_products/_working/sections/*.md`
    
    Use this immediately after creating the outline.
    
    Args:
        retry_id: Optional string (e.g., timestamp) to force re-execution if previous call failed/was blocked.
        task_name: The task name to locate the specific research corpus (default: "default").
    """
    
    # Lazy import to avoid circular dependencies or top-level errors
    try:
        from universal_agent.scripts.parallel_draft import draft_report_async
    except ImportError:
        return "Error: Could not import draft_report_async. Check python path."

    workspace = _resolve_workspace()
    if not workspace:
        return "Error: CURRENT_SESSION_WORKSPACE not set. Cannot determine session workspace."
        
    sys.stderr.write(f"[Local Toolkit] Starting parallel drafter in-process for: {workspace} (Task: {task_name})\n")
    
    try:
        # Run the async drafting process directly with specific corpus path
        ws_path = Path(workspace)
        corpus_path = ws_path / "tasks" / task_name / "refined_corpus.md"
        
        result = await draft_report_async(ws_path, corpus_path=corpus_path)
        return result
    except Exception as e:
        logger.error(f"Error in draft_report_parallel: {e}", exc_info=True)
        return f"Error running in-process drafter: {e}"


@mcp.tool()
@trace_tool_output
def compile_report(theme: str = "modern", custom_css: str = None) -> str:
    """
    Compile all section markdown files into a single professional HTML report.
    This tool handles:
    1. Concatenation of sections
    2. Markdown -> HTML conversion
    3. CSS Styling (via theme or custom_css)
    4. Saving to `work_products/report.html`
    
    Args:
        theme: "modern", "financial", or "creative" (default: "modern")
        custom_css: Optional raw CSS string to override/extend styles.
    """
    import subprocess
    
    script_path = os.path.join(
        PROJECT_ROOT, 
        "src", "universal_agent", "scripts", "compile_report.py"
    )
    
    # Locate workspace - MUST be set in environment
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: CURRENT_SESSION_WORKSPACE not set. Cannot determine session workspace."
        
    cmd = [sys.executable, script_path, "--work-dir", workspace, "--theme", theme]
    if custom_css:
        cmd.extend(["--custom-css", custom_css])
        
    try:
        sys.stderr.write(f"[Local Toolkit] Compiling report with theme='{theme}' via {script_path}\n")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            return f"✅ Report Compiled Successfully.\nPath: {workspace}/work_products/report.html"
        else:
            return f"❌ Compilation Failed:\n{result.stderr}\n\nOutput:\n{result.stdout}"
            
    except Exception as e:
        return f"Error executing compile script: {str(e)}"


@mcp.tool()
@trace_tool_output
async def cleanup_report() -> str:
    """
    Run a cleanup pass over drafted report sections to normalize headings,
    remove duplicated content, and fix formatting inconsistencies.

    This tool:
    1. Reads all markdown sections in `work_products/_working/sections`
    2. Uses an LLM to check for coherence, duplicated stats, and formatting consistency
    3. Rewrites sections that need improvement
    4. Validates for placeholder text like [INSERT STATS]
    
    Use this BEFORE compiling the report.
    """
    # Lazy import
    try:
        from universal_agent.scripts.cleanup_report import cleanup_report_async
    except ImportError:
        return "Error: Could not import cleanup_report_async."

    workspace = _resolve_workspace()
    if not workspace:
        return "Error: CURRENT_SESSION_WORKSPACE not set."

    sys.stderr.write(f"[Local Toolkit] Running cleanup in-process for: {workspace}\n")
    
    try:
        result = await cleanup_report_async(Path(workspace))
        return result
    except Exception as e:
        logger.error(f"Error in cleanup_report: {e}", exc_info=True)
        return f"Error running cleanup: {e}"


@mcp.tool()
@trace_tool_output
async def generate_outline(topic: str, task_name: str = "default") -> str:
    """
    Generate a report outline from the refined corpus.
    Use this AFTER finalize_research and BEFORE drafted_report_parallel.
    """
    try:
        from universal_agent.scripts.generate_outline import generate_outline_async
    except ImportError:
        return "Error: Could not import generate_outline_async."
        
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: CURRENT_SESSION_WORKSPACE not set."

    sys.stderr.write(f"[Local Toolkit] Generating outline for task '{task_name}' in: {workspace}\n")
    
    try:
        result = await generate_outline_async(Path(workspace), task_name, topic)
        return result
    except Exception as e:
        logger.error(f"Error in generate_outline: {e}", exc_info=True)
        return f"Error generating outline: {e}"


@mcp.tool()
@trace_tool_output
def append_to_file(path: str, content: str) -> str:
    """
    Append content to an existing file in the Local Workspace.

    CRITICAL: Use this ONLY for chunked writing of large files (>50KB).
    1. Create the file first using the native `Write` tool (this tool requires the file to exist).
    2. Then call this tool to append subsequent chunks.
    """
    try:
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return f"Error: File does not exist at {path}. Use native Write tool to create it first."

        with open(abs_path, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully appended {len(content)} chars to {path}"
    except Exception as e:
        return f"Error appending to file: {str(e)}"


def _repo_root() -> str:
    # src/mcp_server.py -> repo root is parent of src/
    return str(Path(__file__).resolve().parent.parent)


def _resolve_artifacts_root() -> str:
    """
    Resolve persistent artifacts root.

    Config: UA_ARTIFACTS_DIR
    Default: <repo-root>/artifacts
    """
    raw = (os.getenv("UA_ARTIFACTS_DIR") or "").strip()
    if raw:
        return str(Path(raw).expanduser().resolve())
    return str((Path(_repo_root()) / "artifacts").resolve())


def _is_within_root(root: str, path: str) -> bool:
    try:
        root_resolved = str(Path(root).resolve())
        path_resolved = str(Path(path).resolve())
        return path_resolved.startswith(root_resolved)
    except Exception:
        return False


@mcp.tool()
@trace_tool_output
def write_text_file(path: str, content: str, overwrite: bool = True) -> str:
    """
    Write a UTF-8 text file.

    Security: only allows writing under:
    - CURRENT_SESSION_WORKSPACE (ephemeral scratch)
    - UA_ARTIFACTS_DIR (durable artifacts)
    """
    try:
        if content is None:
            return "Error: content is required"

        abs_path = os.path.abspath(fix_path_typos(path))
        ws = _resolve_workspace()
        artifacts_root = _resolve_artifacts_root()

        allowed = False
        if ws and _is_within_root(ws, abs_path):
            allowed = True
        if _is_within_root(artifacts_root, abs_path):
            allowed = True

        if not allowed:
            return (
                "Error: write denied. Path must be within CURRENT_SESSION_WORKSPACE "
                f"or UA_ARTIFACTS_DIR. Got: {abs_path}"
            )

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        if (not overwrite) and os.path.exists(abs_path):
            return f"Error: file exists and overwrite=false: {abs_path}"

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"File written: {abs_path} ({len(content)} chars)"
    except Exception as e:
        return f"Error writing file: {str(e)}"


@mcp.tool()
@trace_tool_output
def list_directory(path: str) -> str:
    """
    List contents of a directory in the Local Workspace.
    """
    try:
        path = fix_path_typos(path)  # Auto-correct common model typos
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return f"Error: Directory not found at {path}"

        items = os.listdir(abs_path)
        return json.dumps(items, indent=2)
    except Exception as e:
        return f"Error listing directory: {str(e)}"


def _workspace_scope_guard(workspace: Path) -> tuple[bool, str]:
    """Ensure inspector reads only from approved workspace roots."""
    allowed_roots = {_resolve_workspaces_root()}
    current_workspace = _resolve_workspace()
    if current_workspace:
        allowed_roots.add(Path(current_workspace).resolve())

    for root in allowed_roots:
        try:
            workspace.resolve().relative_to(root)
            return True, ""
        except Exception:
            continue

    roots_str = ", ".join(str(root) for root in sorted(allowed_roots))
    return False, f"Workspace must be under one of: {roots_str}"


def _resolve_workspace_for_inspection(session_id: str | None) -> tuple[Path | None, str, str]:
    requested_session = (session_id or "").strip()
    if requested_session:
        if not SESSION_ID_PATTERN.match(requested_session):
            return None, "", "Invalid session_id format."
        workspace = (_resolve_workspaces_root() / requested_session).resolve()
        ok, error = _workspace_scope_guard(workspace)
        if not ok:
            return None, "", error
        if not workspace.exists():
            return None, "", f"Session workspace not found: {requested_session}"
        return workspace, "session_id", ""

    current_workspace = _resolve_workspace()
    if current_workspace:
        workspace = Path(current_workspace).resolve()
        ok, error = _workspace_scope_guard(workspace)
        if not ok:
            return None, "", error
        if workspace.exists():
            return workspace, "current_workspace", ""

    return None, "", "No active workspace found. Provide session_id explicitly."


def _safe_file_tail(path: Path, *, max_lines: int, max_bytes: int) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}

    size_bytes = path.stat().st_size
    read_bytes = min(size_bytes, max_bytes)
    with open(path, "rb") as handle:
        if size_bytes > read_bytes:
            handle.seek(size_bytes - read_bytes)
        chunk = handle.read(read_bytes)

    text = chunk.decode("utf-8", errors="replace")
    if size_bytes > read_bytes and "\n" in text:
        text = text.split("\n", 1)[1]
    lines = text.splitlines()
    truncated = size_bytes > read_bytes or len(lines) > max_lines
    tail_lines = lines[-max_lines:]

    return {
        "exists": True,
        "size_bytes": size_bytes,
        "tail_line_count": len(tail_lines),
        "tail_truncated": truncated,
        "tail": tail_lines,
    }


def _safe_json_preview(path: Path, *, max_bytes: int, max_keys: int = 40) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}

    size_bytes = path.stat().st_size
    if size_bytes > max_bytes:
        return {
            "exists": True,
            "size_bytes": size_bytes,
            "preview_skipped": True,
            "reason": f"file exceeds max_bytes={max_bytes}",
        }

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            payload = json.load(handle)
    except Exception as exc:
        return {
            "exists": True,
            "size_bytes": size_bytes,
            "preview_skipped": True,
            "reason": f"json_parse_error: {exc}",
        }

    if isinstance(payload, dict):
        keys = list(payload.keys())
        preview_keys = keys[:max_keys]
        return {
            "exists": True,
            "size_bytes": size_bytes,
            "keys": preview_keys,
            "key_count": len(keys),
            "preview": {k: payload.get(k) for k in preview_keys},
        }

    return {
        "exists": True,
        "size_bytes": size_bytes,
        "type": type(payload).__name__,
        "preview": payload,
    }


def _recent_files_snapshot(base_dir: Path, *, limit: int) -> dict[str, Any]:
    if not base_dir.exists():
        return {"exists": False, "total_files": 0, "recent": []}

    newest_heap: list[tuple[float, Path]] = []
    total_files = 0

    for root, _, files in os.walk(base_dir):
        for filename in files:
            candidate = Path(root) / filename
            total_files += 1
            try:
                mtime = candidate.stat().st_mtime
            except Exception:
                continue
            if len(newest_heap) < limit:
                heapq.heappush(newest_heap, (mtime, candidate))
            else:
                heapq.heappushpop(newest_heap, (mtime, candidate))

    newest = sorted(newest_heap, key=lambda item: item[0], reverse=True)
    recent = []
    for mtime, file_path in newest:
        try:
            rel_path = str(file_path.relative_to(base_dir))
            size_bytes = file_path.stat().st_size
        except Exception:
            continue
        recent.append(
            {
                "path": rel_path,
                "size_bytes": size_bytes,
                "modified_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
            }
        )

    return {"exists": True, "total_files": total_files, "recent": recent}


@mcp.tool()
@trace_tool_output
def inspect_session_workspace(
    session_id: str = "",
    include_transcript: bool = True,
    tail_lines: int = 120,
    max_bytes_per_file: int = 65536,
    recent_file_limit: int = 25,
) -> str:
    """
    Read-only snapshot of a session workspace for debugging and self-review.

    By default this inspects the active workspace and includes transcript.md,
    run.log/activity logs tail, trace.json, heartbeat state, and recent artifacts.
    """
    tail_lines = max(1, min(int(tail_lines or 120), 400))
    max_bytes_per_file = max(4096, min(int(max_bytes_per_file or 65536), 262144))
    recent_file_limit = max(1, min(int(recent_file_limit or 25), 80))

    workspace, source, error = _resolve_workspace_for_inspection(session_id)
    if workspace is None:
        return json.dumps({"ok": False, "error": error}, indent=2)

    payload: dict[str, Any] = {
        "ok": True,
        "workspace": str(workspace),
        "session_id": workspace.name,
        "source": source,
        "includes": {
            "transcript_md": include_transcript,
            "tail_lines": tail_lines,
            "max_bytes_per_file": max_bytes_per_file,
            "recent_file_limit": recent_file_limit,
        },
        "files": {
            "run.log": _safe_file_tail(
                workspace / "run.log",
                max_lines=tail_lines,
                max_bytes=max_bytes_per_file,
            ),
            "activity_journal.log": _safe_file_tail(
                workspace / "activity_journal.log",
                max_lines=tail_lines,
                max_bytes=max_bytes_per_file,
            ),
            "trace.json": _safe_json_preview(
                workspace / "trace.json",
                max_bytes=max_bytes_per_file,
            ),
            "heartbeat_state.json": _safe_json_preview(
                workspace / "heartbeat_state.json",
                max_bytes=max_bytes_per_file,
            ),
        },
        "artifacts": {
            "work_products": _recent_files_snapshot(
                workspace / "work_products",
                limit=recent_file_limit,
            ),
            "tasks": _recent_files_snapshot(
                workspace / "tasks",
                limit=recent_file_limit,
            ),
        },
    }

    if include_transcript:
        payload["files"]["transcript.md"] = _safe_file_tail(
            workspace / "transcript.md",
            max_lines=tail_lines,
            max_bytes=max_bytes_per_file,
        )

    return json.dumps(payload, indent=2)


@mcp.tool()
@trace_tool_output
def compress_files(files: list[str], output_archive: str) -> str:
    """
    Compress a list of files into a zip archive.
    Args:
        files: List of absolute file paths to include.
        output_archive: Absolute path for the output zip file.
    """
    import zipfile

    try:
        # Validate input paths
        validated_files = []
        for f in files:
            abs_path = os.path.abspath(f)
            if not os.path.exists(abs_path):
                return json.dumps({"error": f"File not found: {f}"})
            validated_files.append(abs_path)

        output_path = os.path.abspath(output_archive)

        # Create parent directory if needed
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in validated_files:
                # Arcname is the name inside the zip file (basename)
                zf.write(file_path, arcname=os.path.basename(file_path))

        # Check if created
        if os.path.exists(output_path):
            size = os.path.getsize(output_path)
            return json.dumps(
                {
                    "success": True,
                    "archive_path": output_path,
                    "size_bytes": size,
                    "files_included": len(validated_files),
                }
            )
        return json.dumps({"error": "Failed to create archive file"})

    except Exception as e:
        return json.dumps({"error": f"Compression failed: {str(e)}"})


@mcp.tool()
@trace_tool_output
def upload_to_composio(
    path: str, tool_slug: str = "GMAIL_SEND_EMAIL", toolkit_slug: str = "gmail"
) -> str:
    """
    Upload a local file to Composio S3 for use as an email attachment or other tool input.
    Uses native Composio SDK FileUploadable.from_path() - the correct, supported method.
    
    Includes wait-retry logic for race conditions (e.g., file still being written).

    Args:
        path: Absolute path to the local file to upload
        tool_slug: The Composio tool that will consume this file (default: GMAIL_SEND_EMAIL)
        toolkit_slug: The toolkit the tool belongs to (default: gmail)

    Returns JSON with:
    - s3key: ID for tool attachments (pass to Gmail/Slack)
    - mimetype: Detected file type
    - name: Original filename
    """
    import time
    
    MAX_RETRIES = 3
    abs_path = os.path.abspath(path)
    
    # Wait-retry loop for file existence (handles race conditions)
    for attempt in range(MAX_RETRIES):
        if os.path.exists(abs_path):
            break
        wait_time = 2 ** attempt  # 1s, 2s, 4s
        sys.stderr.write(
            f"[upload_to_composio] File not found at {abs_path}, waiting {wait_time}s (attempt {attempt + 1}/{MAX_RETRIES})\n"
        )
        time.sleep(wait_time)
    
    # Final check after retries
    if not os.path.exists(abs_path):
        # Try common alternative locations before giving up
        session_workspace = _resolve_workspace() or ""
        cwd = os.getcwd()
        basename = os.path.basename(path)
        
        alternatives = [
            os.path.join(session_workspace, "work_products", basename),
            os.path.join(cwd, basename),
            os.path.join(cwd, "work_products", basename),
        ]
        
        found_alt = None
        for alt in alternatives:
            if alt and os.path.exists(alt):
                found_alt = alt
                sys.stderr.write(f"[upload_to_composio] Found file at alternative path: {alt}\n")
                break
        
        if found_alt:
            abs_path = found_alt
        else:
            return json.dumps({
                "error": f"File not found after {MAX_RETRIES} retries: {path}",
                "tried_paths": [path] + [a for a in alternatives if a],
                "suggestion": "Verify the file was created at the expected path. Check Chrome PDF output location."
            })
    
    try:
        # Import native Composio file helper
        from composio.core.models._files import FileUploadable

        # Get Composio client
        client = Composio(api_key=os.environ.get("COMPOSIO_API_KEY"))

        # Use native SDK method - this is the correct approach per Composio docs
        sys.stderr.write(
            f"[upload_to_composio] Uploading {abs_path} via native FileUploadable.from_path()\n"
        )

        result = FileUploadable.from_path(
            client=client.client, file=abs_path, tool=tool_slug, toolkit=toolkit_slug
        )

        # Return the attachment-ready format with NEXT_STEP guidance
        response = {
            "s3key": result.s3key,
            "mimetype": result.mimetype,
            "name": result.name,
            "local_path": abs_path,
            # Inline guidance to prevent agent from hallucinating Python code
            "NEXT_STEP": {
                "instruction": "Use mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL to send the email with this attachment",
                "tool": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
                "schema": {
                    "tools": [
                        {
                            "tool_slug": "GMAIL_SEND_EMAIL",
                            "arguments": {
                                "recipient_email": "<USER_EMAIL>",
                                "subject": "<EMAIL_SUBJECT>",
                                "body": "<EMAIL_BODY>",
                                "attachment": {
                                    "name": result.name,
                                    "mimetype": result.mimetype,
                                    "s3key": result.s3key,
                                },
                            },
                        }
                    ]
                },
                "notes": [
                    "Replace <USER_EMAIL> with the target email (use 'me' for the connected Gmail)",
                    "DO NOT use Python/Bash code to call Composio SDK directly",
                    "The MCP tool handles authentication and execution automatically",
                ],
            },
        }

        sys.stderr.write(f"[upload_to_composio] SUCCESS: s3key={result.s3key}\n")
        return json.dumps(response, indent=2)

    except Exception as e:
        import traceback

        sys.stderr.write(f"[upload_to_composio] ERROR: {traceback.format_exc()}\n")
        return json.dumps({"error": str(e)})


# =============================================================================
# HARNESS PLANNING TOOLS
# =============================================================================


@mcp.tool()
@trace_tool_output
def ask_user_questions(questions: list) -> str:
    """
    Present structured questions to the user for clarification during Planning Phase.

    Use this tool when you detect ambiguity in a massive task request.
    Only ask 2-4 essential questions. Be helpful, not annoying.

    The questions will be displayed to the user in the main CLI terminal.
    Each question can have pre-defined options PLUS the user can always
    provide a custom free-text response if the options don't fit.

    Args:
        questions: List of question objects, each containing:
            - question (str): The full question text
            - header (str): Short label (max 12 chars), e.g., "Delivery"
            - options (list): Available choices with 'label' and 'description'
            - multiSelect (bool): Allow multiple selections

    Returns:
        JSON string with "__INTERVIEW_REQUEST__" marker and questions.
        The main CLI will intercept this, display the interview, and
        inject the answers into the next prompt.
    """
    # Return a structured signal that the main CLI can intercept
    return json.dumps({"__INTERVIEW_REQUEST__": True, "questions": questions}, indent=2)


# =============================================================================
# MEMORY SYSTEM TOOLS
# =============================================================================


# =============================================================================
# MEMORY SYSTEM TOOLS
# =============================================================================


@mcp.tool()
@trace_tool_output
def core_memory_replace(label: str, new_value: str) -> str:
    """
    Overwrite a Core Memory block (e.g. 'human', 'persona').
    Use this to update persistent facts about the user or yourself.
    """
    if not MEMORY_SYSTEM_AVAILABLE:
        return "Error: Memory System not available."
    
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: No active workspace for memory."
        
    try:
        paths = ensure_memory_scaffold(workspace)
        with open(paths.memory_md, "r", encoding="utf-8") as f:
            current_md = f.read()
            
        # Update the section using the store's helper
        updated_md = _upsert_section(current_md, label, new_value)
        
        with open(paths.memory_md, "w", encoding="utf-8") as f:
            f.write(updated_md)
            
        return f"Successfully updated Core Memory block [{label}]"
    except Exception as e:
        return f"Error updating core memory: {e}"


@mcp.tool()
@trace_tool_output
def core_memory_append(label: str, text_to_append: str) -> str:
    """
    Append text to a Core Memory block.
    Useful for adding a new preference without deleting old ones.
    """
    if not MEMORY_SYSTEM_AVAILABLE:
        return "Error: Memory System not available."
        
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: No active workspace for memory."
        
    try:
        paths = ensure_memory_scaffold(workspace)
        with open(paths.memory_md, "r", encoding="utf-8") as f:
            current_md = f.read()
            
        # Very basic append strategy: read existing, append, replace
        # Limitation: This is a bit brute-force compared to true section parsing
        # but fits the text-file-as-db philosophy.
        
        # We need to find if the section exists to append properly
        section_header = f"## [{label}]"
        if section_header in current_md:
            # Append requires read-modify-write
            # This is complex to do robustly with just _upsert_section replacement
            # without parsing the *existing* content of that section first.
            # Simplified approach: Just tell the user to use replace for now if they need precision.
            # OR, we implement a quick read capability.
            pass # Implementation below
            
        # Fallback: Just append a new generic note if too complex? 
        # Better: Since we don't have a specific `get_section` helper exposed easily without parsing,
        # we will recommend `core_memory_replace` for now or implement a "read-modify-write" simply.
        
        # ACTUALLY, for parity, let's implement a simple read-modify-write
        # But for now, returning a message that encourages replace is safer than breaking data.
        return "Use 'core_memory_replace' to update blocks. Append is not fully supported in this version."
        
    except Exception as e:
        return f"Error accessing memory: {e}"


@mcp.tool()
@trace_tool_output
def archival_memory_insert(content: str, tags: str = "") -> str:
    """
    Save a fact, document, or event to long-term archival memory.
    Use for things that don't need to be in active context.
    """
    if not MEMORY_SYSTEM_AVAILABLE:
        return "Error: Memory System not available."
    
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: No active workspace for memory."
        
    try:
        tag_list = [t.strip() for t in tags.split(",")] if tags else []
        if memory_orchestrator_enabled(default=False):
            from universal_agent.memory.orchestrator import get_memory_orchestrator

            broker = get_memory_orchestrator(workspace_dir=workspace)
            entry = broker.write(
                content=content,
                source="agent_tool",
                session_id=None,
                tags=tag_list,
                memory_class="long_term",
                importance=0.7,
            )
            if entry is None:
                return "Memory write skipped by policy or dedupe."
            return "Successfully saved to archival memory."

        entry = MemoryEntry(
            source="agent_tool",
            content=content,
            tags=tag_list,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        append_memory_entry(workspace, entry)
        return "Successfully saved to archival memory."
    except Exception as e:
        return f"Error saving memory: {e}"


@mcp.tool()
@trace_tool_output
def archival_memory_search(query: str, limit: int = 5) -> str:
    """
    Search long-term archival memory using semantic search.
    """
    if not MEMORY_SYSTEM_AVAILABLE:
        return "Error: Memory System not available."
        
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: No active workspace for memory."
        
    try:
        return ua_memory_search(query=query, limit=limit)
    except Exception as e:
        return f"Error searching memory: {e}"


@mcp.tool()
@trace_tool_output
def get_core_memory_blocks() -> str:
    """
    Read all current Core Memory blocks.
    Useful to verify what you currently 'know' in your core memory.
    """
    if not MEMORY_SYSTEM_AVAILABLE:
        return "Error: Memory System not available."
        
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: No active workspace for memory."
        
    try:
        paths = ensure_memory_scaffold(workspace)
        if os.path.exists(paths.memory_md):
             with open(paths.memory_md, "r", encoding="utf-8") as f:
                 return f.read()
        return "Memory file empty."
    except Exception as e:
        return f"Error reading memory: {e}"


# =============================================================================
# PYDANTIC MODELS FOR SEARCH RESULTS
# =============================================================================


class SearchItem(BaseModel):
    """Represents a single search result or news article."""
    model_config = ConfigDict(extra="ignore")

    url: Optional[str] = None
    link: Optional[str] = None  # Fallback for Scholar/News
    title: Optional[str] = None
    snippet: Optional[str] = None


class SearchResultFile(BaseModel):
    """
    Schema for Composio search result files (combines Web and News structures).
    Web Search: {"results": [...]}
    News Search: {"articles": [...]}
    Web Answer: {"results": [...]} (nested inside outer object, but we parse the file content)
    """
    model_config = ConfigDict(extra="ignore")

    results: Optional[List[SearchItem]] = None
    articles: Optional[List[SearchItem]] = None

    @property
    def all_urls(self) -> List[str]:
        """Extract all valid URLs from the file."""
        items = []
        # Support hardcoded keys (Web/News)
        if self.results:
            items.extend(self.results)
        if self.articles:
            items.extend(self.articles)

        # NOTE: For fully generic support, we rely on the caller to inject tool-specific logic
        # OR we could iterate through all extra fields if pydantic allows.
        # But 'extra="ignore"' prevents us from seeing dynamic fields in this model.
        #
        # INSTEAD: We rely on the 'finalize_research' tool to use the 'SEARCH_TOOL_CONFIG'
        # to parse dynamic schemas from the raw JSON if this static model fails.
        #
        # For now, just return what matches standard schemas:

        # Deduplicate while preserving order? No, set is simpler.
        # But we want to preserve order of relevance usually.
        # Use simple list comprehension
        urls = []
        for item in items:
            target_url = item.url or item.link
            if target_url and target_url.startswith("http"):
                urls.append(target_url)
        return urls


# =============================================================================
# CRAWL4AI TOOLS & HELPERS
# =============================================================================


async def _crawl_core(urls: list[str], session_dir: str) -> str:
    """
    Core implementation of parallel crawling.
    Shared by crawl_parallel (manual tool) and finalize_research_corpus (automated).
    """
    import hashlib
    import aiohttp

    search_results_dir = os.path.join(session_dir, "search_results")
    os.makedirs(search_results_dir, exist_ok=True)

    results_summary = {
        "total": len(urls),
        "successful": 0,
        "failed": 0,
        "saved_files": [],
        "errors": [],
    }

    if not urls:
        return json.dumps({"error": "No URLs provided to crawl"}, indent=2)

    # Check if we should use Cloud API (CRAWL4AI_API_KEY env var set)
    crawl4ai_api_key = os.environ.get("CRAWL4AI_API_KEY")
    crawl4ai_api_url = os.environ.get("CRAWL4AI_API_URL")  # For Docker fallback

    if crawl4ai_api_key:
        # Cloud API mode: Use crawl4ai-cloud.com synchronous /query endpoint
        cloud_endpoint = "https://www.crawl4ai-cloud.com/query"
        mcp_log(f"🌐 [Crawl4AI] Starting cloud crawl of {len(urls)} URLs...", level="INFO", prefix="")
        mcp_log(f"Using Cloud API for {len(urls)} URLs", level="DEBUG", prefix="[crawl_core]")
        if logfire:
            logfire.info("crawl4ai_started", url_count=len(urls), mode="cloud")

        try:
            import asyncio

            # Concurrency limit: configurable via env var, default 8 for paid plans
            CONCURRENCY_LIMIT = int(os.environ.get("CRAWL4AI_CONCURRENCY", "8"))
            semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

            # Retry settings
            MAX_RETRIES = 2
            RETRY_BACKOFF = [1, 3]  # seconds between retries

            async def crawl_single_url(session, url):
                """Crawl a single URL using Cloud API with retry logic"""
                payload = {
                    "url": url,
                    "apikey": crawl4ai_api_key,
                    # Note: Don't use output_format:fit_markdown - returns empty for news sites
                    "excluded_tags": [
                        "nav",
                        "footer",
                        "header",
                        "aside",
                        "script",
                        "style",
                        "form",
                    ],
                    "remove_overlay_elements": True,
                    "word_count_threshold": 10,
                    "cache_mode": "bypass",
                    "magic": True,  # Anti-bot protection bypass
                }

                last_error = None

                for attempt in range(MAX_RETRIES):
                    try:
                        async with semaphore:  # Rate limit concurrent requests
                            async with session.post(
                                cloud_endpoint, json=payload, timeout=30
                            ) as resp:
                                if resp.status == 429:  # Rate limited
                                    last_error = "Rate limited (429)"
                                    if attempt < MAX_RETRIES - 1:
                                        await asyncio.sleep(RETRY_BACKOFF[attempt])
                                        continue
                                    return {
                                        "url": url,
                                        "success": False,
                                        "error": last_error,
                                    }

                                if resp.status != 200:
                                    last_error = f"HTTP {resp.status}"
                                    if attempt < MAX_RETRIES - 1:
                                        await asyncio.sleep(RETRY_BACKOFF[attempt])
                                        continue
                                    return {
                                        "url": url,
                                        "success": False,
                                        "error": last_error,
                                    }

                                data = await resp.json()

                                # Cloud API returns content directly (no polling needed)
                                if data.get("success") == False:
                                    last_error = data.get("error", "Unknown error")
                                    # Don't retry on explicit API errors (e.g., blocked)
                                    return {
                                        "url": url,
                                        "success": False,
                                        "error": last_error,
                                    }
                                if isinstance(data.get("data"), str):
                                    return {
                                        "url": url,
                                        "success": False,
                                        "error": data.get("data"),
                                    }

                                # Get content (may be nested under data)
                                response_payload = (
                                    data.get("data")
                                    if isinstance(data.get("data"), dict)
                                    else data
                                )
                                raw_content = (
                                    response_payload.get("content")
                                    or response_payload.get("markdown")
                                    or response_payload.get("fit_markdown")
                                    or ""
                                )
                                content = raw_content

                                # Post-process: Strip markdown links, keep just the text
                                # [link text](url) -> link text
                                import re

                                content = re.sub(
                                    r"\[([^\]]+)\]\([^)]+\)", r"\1", content
                                )
                                # Also remove bare URLs that start lines
                                content = re.sub(
                                    r"^https?://[^\s]+\s*$",
                                    "",
                                    content,
                                    flags=re.MULTILINE,
                                )
                                # Remove image markdown ![alt](url)
                                content = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", content)
                                # Clean up excessive blank lines
                                content = re.sub(r"\n{3,}", "\n\n", content)

                                return {
                                    "url": url,
                                    "success": True,
                                    "content": content,
                                    "raw_content": raw_content,  # Keep for date extraction
                                    "metadata": response_payload.get("metadata", {}),
                                }

                    except asyncio.TimeoutError:
                        last_error = "Timeout (30s)"
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                    except aiohttp.ClientError as e:
                        last_error = f"Connection error: {str(e)}"
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_BACKOFF[attempt])
                            continue
                    except Exception as e:
                        last_error = str(e)
                        # Don't retry on unexpected errors
                        break

                return {
                    "url": url,
                    "success": False,
                    "error": last_error or "Unknown error",
                }

            # Execute concurrent crawl with rate limiting
            mcp_log(
                f"Starting crawl of {len(urls)} URLs (max {CONCURRENCY_LIMIT} parallel, {MAX_RETRIES} retries)",
                level="DEBUG",
                prefix="[crawl_core]"
            )
            async with aiohttp.ClientSession() as session:
                tasks = [crawl_single_url(session, url) for url in urls]
                crawl_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Console visibility
            success_count = sum(1 for r in crawl_results if isinstance(r, dict) and r.get("success"))
            mcp_log(f"✅ Crawl complete: {success_count}/{len(urls)} successful", level="INFO", prefix="   ")
            mcp_log(
                f"Cloud API returned {len(crawl_results)} results",
                level="DEBUG",
                prefix="[crawl_core]"
            )
            if logfire:
                logfire.info(
                    "crawl4ai_complete",
                    total_urls=len(urls),
                    success_count=success_count,
                    failed_count=len(urls) - success_count,
                )

            # Process results
            for result in crawl_results:
                if isinstance(result, Exception):
                    # Structured Logfire event for crawl exception
                    try:
                        logfire.info(
                            "crawl_failure",
                            url="unknown",
                            reason="exception",
                            error=str(result),
                            phase="finalize_research",
                        )
                    except Exception:
                        pass
                    results_summary["failed"] += 1
                    results_summary["errors"].append(
                        {"url": "unknown", "error": str(result)}
                    )
                    continue

                url = result.get("url", "unknown")

                if result.get("success"):
                    content = result.get("content", "")
                    metadata = result.get("metadata", {})

                    if content:
                        # Detect Cloudflare/captcha blocks
                        is_cloudflare_blocked = len(content) < 2000 and (
                            "cloudflare" in content.lower()
                            or "verifying you are human" in content.lower()
                            or "security of your connection" in content.lower()
                        )
                        if is_cloudflare_blocked:
                            logger.warning(f"Cloudflare blocked: {url}")
                            # Structured Logfire event for crawl failure
                            try:
                                logfire.info(
                                    "crawl_failure",
                                    url=url,
                                    reason="cloudflare_blocked",
                                    content_length=len(content),
                                    phase="finalize_research",
                                )
                            except Exception:
                                pass  # Logfire optional
                            results_summary["failed"] += 1
                            results_summary["errors"].append(
                                {"url": url, "error": "Cloudflare blocked"}
                            )
                            continue

                        # Save to file with YAML frontmatter metadata
                        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
                        filename = f"crawl_{url_hash}.md"
                        filepath = os.path.join(search_results_dir, filename)

                        # Build metadata header
                        title = (
                            metadata.get("title")
                            or metadata.get("og:title")
                            or "Untitled"
                        )
                        description = (
                            metadata.get("description")
                            or metadata.get("og:description")
                            or ""
                        )

                        # Extract article date from URL pattern (e.g., /2025/12/28/)
                        import re

                        article_date = None

                        # Try URL pattern first (most reliable)
                        date_match = re.search(r"/(\d{4})/(\d{1,2})/(\d{1,2})/", url)
                        if date_match:
                            article_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
                        else:
                            date_match = re.search(
                                r"/(\d{4})-(\d{1,2})-(\d{1,2})/", url
                            )
                            if date_match:
                                article_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"

                        # Fallback: extract from raw content (before link stripping)
                        if not article_date:
                            raw_content = result.get("raw_content", content)
                            # Search full content for date patterns (some sites have tons of nav bloat)
                            # Pattern: "Month Day, Year" (e.g., December 28, 2025)
                            months = "January|February|March|April|May|June|July|August|September|October|November|December"
                            match = re.search(
                                rf"({months})\s+(\d{{1,2}}),?\s+(\d{{4}})",
                                raw_content,
                                re.I,
                            )
                            if match:
                                month_map = {
                                    "january": "01",
                                    "february": "02",
                                    "march": "03",
                                    "april": "04",
                                    "may": "05",
                                    "june": "06",
                                    "july": "07",
                                    "august": "08",
                                    "september": "09",
                                    "october": "10",
                                    "november": "11",
                                    "december": "12",
                                }
                                article_date = f"{match.group(3)}-{month_map[match.group(1).lower()]}-{match.group(2).zfill(2)}"
                            else:
                                # Pattern: "Day Mon Year" (e.g., 29 Dec 2025)
                                match = re.search(
                                    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
                                    raw_content,
                                    re.I,
                                )
                                if match:
                                    month_map = {
                                        "jan": "01",
                                        "feb": "02",
                                        "mar": "03",
                                        "apr": "04",
                                        "may": "05",
                                        "jun": "06",
                                        "jul": "07",
                                        "aug": "08",
                                        "sep": "09",
                                        "oct": "10",
                                        "nov": "11",
                                        "dec": "12",
                                    }
                                    article_date = f"{match.group(3)}-{month_map[match.group(2).lower()]}-{match.group(1).zfill(2)}"

                        # YAML frontmatter for rich metadata
                        date_line = (
                            f"date: {article_date}" if article_date else "date: unknown"
                        )
                        frontmatter = f"""---
title: "{title.replace('"', "'")}"
source: {url}
{date_line}
description: "{description[:200].replace('"', "'") if description else ""}"
word_count: {len(content.split())}
---

"""
                        final_content = frontmatter + content
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(final_content)

                        logger.info(
                            f"Cloud API: Saved {len(content)} bytes for {url[:50]}"
                        )
                        results_summary["successful"] += 1
                        results_summary["saved_files"].append(
                            {
                                "url": url,
                                "file": filename,
                                "path": filepath,
                            }
                        )
                    else:
                        results_summary["failed"] += 1
                        results_summary["errors"].append(
                            {"url": url, "error": "Empty markdown"}
                        )
                else:
                    error_msg = result.get("error", "Crawl failed")
                    results_summary["failed"] += 1
                    results_summary["errors"].append({"url": url, "error": error_msg})

        except Exception as e:
            return json.dumps({"error": f"Crawl4AI Cloud API error: {str(e)}"})

        # Generate research_overview.md - combined context-efficient file with size tiers
        if results_summary["saved_files"]:
            try:
                total_words = 0
                file_metadata = []  # Store metadata for tiered categorization

                for i, file_info in enumerate(results_summary["saved_files"], 1):
                    with open(file_info["path"], "r", encoding="utf-8") as f:
                        full_content = f.read()

                    # Parse frontmatter metadata
                    import yaml

                    if full_content.startswith("---"):
                        fm_end = full_content.find("---", 4)
                        if fm_end != -1:
                            fm_text = full_content[4:fm_end].strip()
                            try:
                                metadata = yaml.safe_load(fm_text)
                            except:
                                metadata = {}
                            body = full_content[fm_end + 4 :].strip()
                        else:
                            metadata = {}
                            body = full_content
                    else:
                        metadata = {}
                        body = full_content

                    # Count words in full file
                    word_count = len(body.split())
                    total_words += word_count

                    # Store metadata for categorization
                    file_metadata.append(
                        {
                            "index": i,
                            "file": file_info["file"],
                            "path": file_info["path"],
                            "url": file_info["url"],
                            "title": metadata.get("title", "Untitled"),
                            "date": metadata.get("date", "unknown"),
                            "word_count": word_count,
                        }
                    )

                # Categorize files by size
                batch_safe_files = [
                    f for f in file_metadata if f["word_count"] <= BATCH_SAFE_THRESHOLD
                ]
                large_files = [
                    f for f in file_metadata if f["word_count"] > LARGE_FILE_THRESHOLD
                ]
                medium_files = [
                    f
                    for f in file_metadata
                    if BATCH_SAFE_THRESHOLD < f["word_count"] <= LARGE_FILE_THRESHOLD
                ]

                # Build tiered overview
                overview_header = f"""# Research Sources Overview
**Generated:** {datetime.utcnow().isoformat()}Z
**Total Sources:** {len(results_summary["saved_files"])} articles
**Total Words Available:** {total_words:,} words across all sources

## Corpus Size Breakdown
| Category | Count | Description |
|----------|-------|-------------|
| **Batch-Safe** | {len(batch_safe_files)} | Under {BATCH_SAFE_THRESHOLD:,} words - safe for batch reading |
| **Medium** | {len(medium_files)} | {BATCH_SAFE_THRESHOLD:,}-{LARGE_FILE_THRESHOLD:,} words - included in batch |
| **Large** | {len(large_files)} | Over {LARGE_FILE_THRESHOLD:,} words - read individually |

⚠️ **Batch Limit:** `read_research_files` stops at **{BATCH_MAX_TOTAL:,} words** cumulative.
If you need large files, read them individually with `read_local_file`.

---

## Batch-Safe Files (Recommended for batch read)

| # | File | Words | Title |
|---|------|-------|-------|
"""
                for f in batch_safe_files:
                    overview_header += f"| {f['index']} | `{f['file']}` | {f['word_count']:,} | {f['title'][:50]}... |\n"

                if medium_files:
                    overview_header += f"\n## Medium Files (Included in batch if space)\n\n| # | File | Words | Title |\n|---|------|-------|-------|\n"
                    for f in medium_files:
                        overview_header += f"| {f['index']} | `{f['file']}` | {f['word_count']:,} | {f['title'][:50]}... |\n"

                if large_files:
                    overview_header += f"\n## ⚠️ Large Files (Read Individually)\n\n| # | File | Words | Title | Command |\n|---|------|-------|-------|--------|\n"
                    for f in large_files:
                        overview_header += f'| {f["index"]} | `{f["file"]}` | {f["word_count"]:,} | {f["title"][:40]}... | `read_local_file(path="{f["path"]}")` |\n'

                overview_header += f"""
---

## All Sources Detail

"""
                # Add brief metadata for each source (no excerpts to save space)
                for f in file_metadata:
                    overview_header += f"""### Source {f["index"]}: {f["title"][:60]}
- **File:** `{f["file"]}` ({f["word_count"]:,} words)
- **URL:** {f["url"]}
- **Date:** {f["date"]}

"""

                overview_path = os.path.join(search_results_dir, "research_overview.md")

                with open(overview_path, "w", encoding="utf-8") as f:
                    f.write(overview_header)

                results_summary["overview_file"] = overview_path
                results_summary["total_words_available"] = total_words
                results_summary["batch_safe_count"] = len(batch_safe_files)
                results_summary["large_file_count"] = len(large_files)
                logger.info(
                    f"Created research_overview.md with {len(results_summary['saved_files'])} sources, {total_words:,} total words"
                )

            except Exception as e:
                logger.warning(f"Failed to create research overview: {e}")

        return json.dumps(results_summary, indent=2)

    # Fallback to Local/Docker mode (unchanged from original)
    else:
        if crawl4ai_api_url:
            # Docker API mode (legacy): Use crawl4ai Docker container
            mcp_log(
                f"Using Docker API at {crawl4ai_api_url} for {len(urls)} URLs",
                level="DEBUG",
                prefix="[crawl_core]"
            )
            return json.dumps(
                {
                    "error": "Docker API mode deprecated. Set CRAWL4AI_API_KEY for Cloud API or remove CRAWL4AI_API_URL for local mode."
                }
            )

        return json.dumps(results_summary, indent=2)


# LEGACY - Hidden from MCP server to favor in-process tool
async def _crawl_parallel_legacy(urls: list[str], session_dir: str) -> str:
    """
    High-speed parallel web scraping using crawl4ai.
    Scrapes multiple URLs concurrently, extracts clean markdown (removing ads/nav),
    and saves results to 'search_results' directory in the session workspace.

    Args:
        urls: List of URLs to scrape (no limit - crawl4ai handles parallel batches automatically)
        session_dir: Absolute path to the current session workspace (e.g. AGENT_RUN_WORKSPACES/session_...)

    Returns:
        JSON summary of results (success/fail counts, saved file paths).
    """
    return await _crawl_core(urls, session_dir)


@mcp.tool()
@trace_tool_output
async def finalize_research(
    session_dir: str,
    task_name: str = "default",
    enable_topic_filter: bool = True,
    retry_id: str = None,
) -> str:
    """
    AUTOMATED RESEARCH PIPELINE (Inbox Pattern):
    1. Scans 'search_results/' INBOX for NEW JSON search outputs.
    2. Archives processed JSONs to 'search_results/processed_json/'.
    3. Executes parallel crawl on extracted URLs (saves raw to global 'search_results/').
    4. Generates scoped 'research_overview.md' in 'tasks/{task_name}/'.

    Args:
        session_dir: Path to the current session workspace.
        task_name: Name of the current task/iteration (e.g., "01_venezuela").
                   Used to isolate context in 'tasks/{task_name}/'.
        retry_id: Optional identifier to bypass idempotency checks (e.g., timestamp).

    Returns:
        Summary of operation: URLs found, crawl success/fail, and path to scoped overview.
    """
    try:
        session_dir = fix_path_typos(session_dir)
        # Robustness: Check for search_results directory
        search_results_dir = os.path.join(session_dir, "search_results")
        
        # If standard path missing, check if session_dir IS the search results dir (fallback)
        if not os.path.exists(search_results_dir):
            if os.path.basename(session_dir.rstrip("/\\")) == "search_results" and os.path.exists(session_dir):
                 search_results_dir = session_dir
                 # Adjust session_dir up one level for task output
                 session_dir = os.path.dirname(session_dir.rstrip("/\\"))
            else:
                 # Last ditch: check if there are JSONs directly in session_dir (agent passed root)
                 root_jsons = [f for f in os.listdir(session_dir) if f.endswith(".json")]
                 if root_jsons and "search_results" not in os.listdir(session_dir):
                     search_results_dir = session_dir
                 else:
                     # Fallback: use resolved workspace if it has search_results
                     fallback_workspace = _resolve_workspace()
                     fallback_search = (
                         os.path.join(fallback_workspace, "search_results")
                         if fallback_workspace
                         else ""
                     )
                     if fallback_workspace and os.path.exists(fallback_search):
                         search_results_dir = fallback_search
                         session_dir = fallback_workspace
                     else:
                         return json.dumps(
                             {"error": f"Search results directory not found at {search_results_dir} or {session_dir}"}
                         )

        processed_dir = os.path.join(search_results_dir, "processed_json")
        
        # Task-specific context directory (using potentially adjusted session_dir)
        task_dir = os.path.join(session_dir, "tasks", task_name)
        os.makedirs(task_dir, exist_ok=True)
        task_config = _load_task_config(task_dir)
        config_enable_topic = task_config.get("enable_topic_filter")
        if isinstance(config_enable_topic, bool):
            enable_topic_filter = config_enable_topic
        topic_keywords = task_config.get("topic_keywords", [])
        if isinstance(topic_keywords, str):
            topic_keywords = [term.strip() for term in topic_keywords.split(",")]
        if not isinstance(topic_keywords, list):
            topic_keywords = []

        # Ensure archive dir exists
        os.makedirs(processed_dir, exist_ok=True)

        all_urls = set()
        scanned_files = 0
        processed_files_list = []

        # 1. Scan Inbox (Root only) and Extract
        # Strict filter: ONLY .json files in the root (ignore directories)
        candidates = sorted([
            f
            for f in os.listdir(search_results_dir)
            if f.endswith(".json")
            and os.path.isfile(os.path.join(search_results_dir, f))
        ])

        for filename in candidates:
            path = os.path.join(search_results_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    logger.warning(f"Skipping {filename}: Root element is not a dict")
                    continue

                # ---------------------------------------------------------
                # STRATEGY A: Configuration-Driven Extraction (Robust)
                # ---------------------------------------------------------
                tool_name = data.get("tool", "")
                config = SEARCH_TOOL_CONFIG.get(tool_name)

                # HEURISTIC FALLBACK: Infer tool from filename if explicit tool tag missing/unknown
                if not config:
                    fname_slug = (
                        filename.lower()
                        .replace(".json", "")
                        .replace("_", "")
                        .replace("-", "")
                    )
                    best_match = None
                    best_match_slug = ""
                    for k, v in SEARCH_TOOL_CONFIG.items():
                        k_slug = k.lower().replace("_", "")
                        # Support filenames with suffixes like _2_123112 by prefix matching.
                        if k_slug == fname_slug or fname_slug.startswith(k_slug):
                            if len(k_slug) > len(best_match_slug):
                                best_match = (k, v)
                                best_match_slug = k_slug
                    if best_match:
                        tool_name, config = best_match
                        logger.info(
                            "Inferred tool '%s' from filename '%s'",
                            tool_name,
                            filename,
                        )

                extracted_count = 0

                if config:
                    # We know exactly how to parse this tool
                    list_key = config["list_key"]
                    url_key = config["url_key"]

                    # Get the list of items (handle nested keys)
                    items = data.get(list_key, {})
                    # If there's a subkey, navigate to it
                    if "list_subkey" in config and isinstance(items, dict):
                        items = items.get(config["list_subkey"], [])
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                url = item.get(url_key)
                                if (
                                    url
                                    and isinstance(url, str)
                                    and url.startswith("http")
                                ):
                                    all_urls.add(url)
                                    extracted_count += 1

                    if extracted_count > 0:
                        logger.info(
                            f"[{tool_name}] Config-parsed {extracted_count} URLs from {filename}"
                        )
                        scanned_files += 1
                        processed_files_list.append(filename)
                        # We will move file later

                    # Config matched but no URLs extracted; fall back to legacy parser
                    if extracted_count == 0:
                        try:
                            model = SearchResultFile.model_validate(data)
                            urls = model.all_urls
                            if urls:
                                all_urls.update(urls)
                                scanned_files += 1
                                processed_files_list.append(filename)
                                logger.info(
                                    "[%s] Fallback-parsed %d URLs from %s",
                                    tool_name,
                                    len(urls),
                                    filename,
                                )
                        except ValidationError:
                            logger.warning(
                                "Unknown tool schema in %s (Tool: %s)",
                                filename,
                                tool_name,
                            )

                # ---------------------------------------------------------
                # STRATEGY B: Static Pydantic Fallback (Legacy/Unknown Tools)
                # ---------------------------------------------------------
                else:
                    try:
                        model = SearchResultFile.model_validate(data)
                        urls = model.all_urls
                        if urls:
                            all_urls.update(urls)
                            scanned_files += 1
                            processed_files_list.append(filename)
                            logger.info(
                                f"[Legacy] Pydantic-parsed {len(urls)} URLs from {filename}"
                            )
                    except ValidationError:
                        # Only warn if we didn't already handle it via config
                        logger.warning(
                            f"Unknown tool schema in {filename} (Tool: {tool_name})"
                        )

            except Exception as e:
                logger.warning(f"Error reading {filename}: {e}")

        if not all_urls:
            return json.dumps(
                {
                    "status": "No URLs found",
                    "scanned_files": scanned_files,
                    "error": (
                        "NO SEARCH RESULTS FOUND. The search tool may have failed silently or found nothing. "
                        "ACTION REQUIRED: Retry the search using 'mcp__composio__COMPOSIO_SEARCH_TOOLS' with a **modified query** "
                        "(e.g., change word order or add 'latest') to ensure you get fresh results."
                    ),
                }
            )

        url_list = list(all_urls)
        filtered_urls = [u for u in url_list if not _url_is_blacklisted(u)]
        dropped_urls = [u for u in url_list if _url_is_blacklisted(u)]

        # 2. Archive Processed Files (Move to processed_json)
        import shutil

        for filename in processed_files_list:
            src = os.path.join(search_results_dir, filename)
            dst = os.path.join(processed_dir, filename)
            # Handle potential collision in archive
            if os.path.exists(dst):
                base, ext = os.path.splitext(filename)
                timestamp = datetime.now().strftime("%H%M%S")
                dst = os.path.join(processed_dir, f"{base}_{timestamp}{ext}")

            shutil.move(src, dst)
            logger.info(f"Archived verified search input: {filename}")

        search_texts: list[str] = []
        for filename in processed_files_list:
            path = os.path.join(processed_dir, filename)
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tool_name = data.get("tool", "")
                config = SEARCH_TOOL_CONFIG.get(tool_name)
                search_texts.extend(_collect_search_texts(data, config))
            except Exception as e:
                logger.warning(f"Error reading archived {filename}: {e}")

        topic_terms_raw = _build_topic_terms(search_texts, topic_keywords)
        compiled_topic_terms = _compile_topic_terms(topic_terms_raw)
        if enable_topic_filter and compiled_topic_terms:
            preview_terms = ", ".join(topic_terms_raw[:12])
            preview_suffix = "" if len(topic_terms_raw) <= 12 else "..."
            logger.info(
                "Topic filter enabled (%d terms). Preview: %s%s",
                len(compiled_topic_terms),
                preview_terms,
                preview_suffix,
            )
        elif enable_topic_filter:
            logger.info("Topic filter enabled but no topic terms found.")

        # 3. Execute Crawl (Saves to Global Cache in search_results/)
        # 3. Execute Crawl (Saves to Global Cache in search_results/)
        sys.stderr.write(
            f"[finalize] ⏳ Found {len(url_list)} unique URLs from {scanned_files} files "
            f"({len(filtered_urls)} after blacklist). Starting crawl (this may take 30-60s)...\n"
        )

        # Call Core Logic
        crawl_result_json = await _crawl_core(filtered_urls, session_dir)
        crawl_result = json.loads(crawl_result_json)
        
        sys.stderr.write(
            f"[finalize] ✅ Crawl complete. Successful: {crawl_result.get('successful', 0)}, Failed: {crawl_result.get('failed', 0)}. Processing filtering...\n"
        )

        # 4. Build Filtered Corpus (SCOPED to Task Directory)
        # Instead of search_results_filtered_best, we put cleaned files in tasks/{task_name}/filtered_corpus
        filtered_dir = os.path.join(task_dir, "filtered_corpus")
        os.makedirs(filtered_dir, exist_ok=True)

        filtered_files = []
        filtered_dropped = []
        seen_content_hashes = set()

        for item in crawl_result.get("saved_files", []):
            path = item.get("path")
            if not path or not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            filtered_body, status, meta, meta_block = _filter_crawl_content(
                raw_text,
                topic_terms=compiled_topic_terms,
                enable_topic_filter=enable_topic_filter,
            )
            if not filtered_body:
                if status.startswith("topic_"):
                    logger.info("Topic filter drop: %s (%s)", path, status)
                filtered_dropped.append({"path": path, "status": status})
                continue

            # Dedupe by content hash
            import hashlib
            content_hash = hashlib.md5(filtered_body.encode()).hexdigest()
            if content_hash in seen_content_hashes:
                filtered_dropped.append({"path": path, "status": "duplicate_content"})
                continue
            seen_content_hashes.add(content_hash)

            frontmatter = f"---\n{meta_block}\n---\n\n" if meta_block else ""
            filename = os.path.basename(path)
            filtered_path = os.path.join(filtered_dir, filename)
            final_content = frontmatter + filtered_body
            with open(filtered_path, "w", encoding="utf-8") as f:
                f.write(final_content)
            filtered_files.append(
                {
                    "file": filename,
                    "path": filtered_path,
                    "url": meta.get("source", ""),
                    "title": meta.get("title", "Untitled"),
                    "date": meta.get("date", "unknown"),
                    "word_count": _word_count(filtered_body),
                }
            )

        # 5. Build Scoped Overview
        # We need to read the JSONs again to build the index snippets.
        # But they are now in the ARCHIVE directory (processed_dir).
        search_items = []
        for filename in processed_files_list:
            # Look in processed_dir now
            path = os.path.join(
                processed_dir, os.path.basename(filename)
            )  # Naive name match, assumes move didn't rename
            # Logic: If we renamed during move, we can't easily find it.
            # Better approach: Read from 'src' BEFORE move, or just re-read listing of destination?
            # Ideally we extract metadata during the first pass.
            # For simplicity in this hotfix: We'll assume no rename for now, or just scan processed_dir for RECENTLY moved files?
            # Actually, let's just re-read the ARCHIVED file.

            # Find the file in processed_dir. It might be renamed if collision occurred.
            # But processed_files_list contains original filenames.
            # Let's try to find it.
            candidates = [
                f
                for f in os.listdir(processed_dir)
                if f.startswith(os.path.splitext(filename)[0])
            ]
            # Pick the most recent one if multiple?
            # This is complex. Use the 'src' path logic: we just moved them.
            # Let's iterate processed_files_list and assume valid path construction for now.
            # If collision rename happened, we might miss it.
            # Safe fallback: Don't error out.

            # Simple fix: We know we moved 'filename' to 'processed_dir/filename' usually.
            path = os.path.join(processed_dir, filename)
            if not os.path.exists(path):
                # Try finding with timestamp suffix? Too hard.
                # We skip snippets if file lost.
                continue

            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tool_name = data.get("tool", "")
                config = SEARCH_TOOL_CONFIG.get(tool_name)
                if not config:
                    continue
                items = data.get(config["list_key"], [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    url = item.get(config["url_key"])
                    if not url or not isinstance(url, str):
                        continue
                    search_items.append(
                        {
                            "tool": tool_name,
                            "position": item.get("position"),
                            "title": item.get("title"),
                            "url": url,
                            "snippet": item.get("snippet"),
                            "source": item.get("source"),
                        }
                    )
            except Exception as e:
                logger.warning(f"Error reading archived {filename}: {e}")

        filtered_lookup = {f["url"]: f for f in filtered_files if f.get("url")}
        overview_lines = []
        overview_lines.append(f"# Research Sources Overview (Task: {task_name})")
        overview_lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}Z")
        overview_lines.append(
            f"**Search Inputs:** {scanned_files} files (Archived to `processed_json/`)"
        )
        if enable_topic_filter and compiled_topic_terms:
            preview_terms = ", ".join(topic_terms_raw[:12])
            preview_suffix = "" if len(topic_terms_raw) <= 12 else "..."
            overview_lines.append(
                f"**Topic filter:** enabled ({len(topic_terms_raw)} terms) {preview_terms}{preview_suffix}"
            )
        elif enable_topic_filter:
            overview_lines.append("**Topic filter:** enabled (no terms found)")
        else:
            overview_lines.append("**Topic filter:** disabled")
        overview_lines.append(f"**Search Results URLs:** {len(url_list)}")
        overview_lines.append(f"**Filtered Corpus Files:** {len(filtered_files)}")
        overview_lines.append("")
        overview_lines.append("## Search Results Index (Snippets from Inbox)")
        overview_lines.append("| # | Tool | Title | URL | Snippet | Filtered File |")
        overview_lines.append("|---|------|-------|-----|---------|---------------|")
        for idx, item in enumerate(search_items, 1):
            url = item.get("url", "")
            title = (item.get("title") or "")[:60]
            snippet = (item.get("snippet") or "")[:90]
            filtered = filtered_lookup.get(url)
            filtered_file = f"`{filtered['file']}`" if filtered else ""
            overview_lines.append(
                f"| {idx} | {item.get('tool', '')} | {title} | {url} | {snippet} | {filtered_file} |"
            )

        if dropped_urls:
            overview_lines.append("")
            overview_lines.append("## Blacklisted URLs (Skipped Before Crawl)")
            for url in dropped_urls:
                overview_lines.append(f"- {url}")

        overview_lines.append("")
        overview_lines.append("## Filtered Corpus (Read for Report)")
        overview_lines.append(
            "Only files listed below should be read for report generation. "
            "Do NOT read raw `search_results/crawl_*.md` files."
        )
        overview_lines.append("| # | File | Words | Title | Date | URL |")
        overview_lines.append("|---|------|-------|-------|------|-----|")
        for idx, f in enumerate(filtered_files, 1):
            overview_lines.append(
                f"| {idx} | `{f['file']}` | {f['word_count']:,} | {f['title'][:50]} | {f['date']} | {f['url']} |"
            )

        if filtered_dropped:
            overview_lines.append("")
            overview_lines.append("## Filtered-Out Crawl Files (Dropped After Crawl)")
            overview_lines.append("| # | File | Reason |")
            overview_lines.append("|---|------|--------|")
            for idx, dropped in enumerate(filtered_dropped, 1):
                status = dropped["status"]
                display_status = f"{status}*" if status.startswith("topic_") else status
                overview_lines.append(
                    f"| {idx} | `{os.path.basename(dropped['path'])}` | {display_status} |"
                )
            if any(item["status"].startswith("topic_") for item in filtered_dropped):
                overview_lines.append(
                    "\n*Topic filter removed the item due to low topical relevance."
                )

        # SAVE OVERVIEW TO TASK DIRECTORY
        overview_path = os.path.join(task_dir, "research_overview.md")
        with open(overview_path, "w", encoding="utf-8") as f:
            f.write("\n".join(overview_lines))

        # 6. Run corpus refinement (AUTOMATIC - deterministic Python)
        # This replaces the legacy evidence_ledger approach with batched LLM extraction
        # Always uses expanded mode for maximum detail; agent can override with accelerated if needed
        refined_corpus_path = None
        refiner_metrics = None
        
        if len(filtered_files) > 0:
            try:
                from pathlib import Path
                
                sys.stderr.write(
                    f"[finalize] ⏳ Running corpus refinement ({len(filtered_files)} files, mode=expanded). This leverages LLMs and may take ~1-2 minutes...\n"
                )
                
                refiner_metrics = await refine_corpus_programmatic(
                    corpus_dir=Path(filtered_dir),
                    output_file=Path(task_dir) / "refined_corpus.md",
                    accelerated=False,  # Always expanded for maximum detail
                )
                refined_corpus_path = refiner_metrics.get("output_file")
                sys.stderr.write(
                    f"[finalize] Corpus refinement complete: "
                    f"{refiner_metrics.get('original_words', 0):,} words → "
                    f"{refiner_metrics.get('output_words', 0):,} words "
                    f"({refiner_metrics.get('compression_ratio', 0)}x) "
                    f"in {refiner_metrics.get('total_time_ms', 0) / 1000:.1f}s\n"
                )
            except Exception as e:
                sys.stderr.write(f"[finalize] Corpus refinement failed: {e}\n")
                # Non-fatal - continue without refinement
        
        # 7. Calculate corpus size and recommend mode
        failed_urls = crawl_result.get("errors", [])
        
        # Calculate total corpus size (chars and words)
        total_corpus_chars = 0
        total_corpus_words = 0
        for finfo in filtered_files:
            # Approximate chars from word count (avg ~5 chars per word)
            word_count = finfo.get("word_count", 0)
            total_corpus_words += word_count
            # Read actual file size for accurate char count
            fpath = finfo.get("path")
            if fpath and os.path.exists(fpath):
                total_corpus_chars += os.path.getsize(fpath)
        
        # Simplified mode recommendation - we now have refined_corpus.md available
        file_count = len(filtered_files)
        if refined_corpus_path:
            recommended_mode = "REFINED_CORPUS"
            mode_reason = f"Refined corpus available ({refiner_metrics.get('output_words', 0):,} words). Read refined_corpus.md instead of individual files."
        elif total_corpus_chars >= 150000 or file_count >= 15:
            recommended_mode = "HARNESS_REQUIRED"
            mode_reason = f"Corpus too large for single context ({total_corpus_chars:,} chars, {file_count} files). Use /harness mode."
        elif total_corpus_chars >= 100000 or file_count >= 8:
            recommended_mode = "LARGE_CORPUS"
            mode_reason = f"Large corpus ({total_corpus_chars:,} chars, {file_count} files). Consider using refined_corpus.md."
        else:
            recommended_mode = "STANDARD"
            mode_reason = f"Small corpus ({total_corpus_chars:,} chars, {file_count} files). Direct read and synthesis."

        return json.dumps(
            {
                "status": "Research Corpus Finalized (Inbox Processed)",
                "task_scope": task_name,
                "processed_input_files": len(processed_files_list),
                "archive_location": "search_results/processed_json/",
                "extracted_urls": len(url_list),
                "urls_after_blacklist": len(filtered_urls),
                "crawl_summary": {
                    "total": crawl_result.get("total", 0),
                    "successful": crawl_result.get("successful", 0),
                    "failed": crawl_result.get("failed", 0),
                },
                "failed_urls": failed_urls,
                "filtered_corpus": {
                    "filtered_dir": filtered_dir,
                    "kept_files": len(filtered_files),
                    "dropped_files": len(filtered_dropped),
                    "total_chars": total_corpus_chars,
                    "total_words": total_corpus_words,
                },
                # NEW: Refined corpus info
                "refined_corpus": {
                    "path": refined_corpus_path,
                    "original_words": refiner_metrics.get("original_words") if refiner_metrics else None,
                    "output_words": refiner_metrics.get("output_words") if refiner_metrics else None,
                    "compression_ratio": refiner_metrics.get("compression_ratio") if refiner_metrics else None,
                    "processing_time_ms": refiner_metrics.get("total_time_ms") if refiner_metrics else None,
                } if refiner_metrics else None,
                "overview_path": overview_path,
                # Updated mode recommendation
                "recommended_mode": recommended_mode,
                "mode_reason": mode_reason,
            },
            indent=2,
        )

    except Exception as e:
        import traceback

        return json.dumps(
            {"error": f"Pipeline failed: {str(e)}", "traceback": traceback.format_exc()}
        )


# =============================================================================
# EVIDENCE LEDGER TOOLS
# =============================================================================


def _extract_quotes(text: str) -> list[str]:
    """Extract quoted text from content."""
    import re

    # Match "quoted text" or 'quoted text' or "smart quotes"
    patterns = [
        r'"([^"]{20,300})"',  # Standard double quotes
        r"'([^']{20,300})'",  # Single quotes (avoid contractions with min length)
        r"\u201c([^\u201d]{20,300})\u201d",  # Smart double quotes (curly)
        r"\u2018([^\u2019]{20,300})\u2019",  # Smart single quotes (curly)
    ]
    quotes = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        quotes.extend(matches)
    return quotes[:10]  # Limit to 10 most prominent quotes


def _extract_numbers(text: str) -> list[dict]:
    """Extract significant numbers with context."""
    import re

    numbers = []

    # Patterns for significant numbers (with context)
    patterns = [
        # Percentages
        (r"(\d+(?:\.\d+)?)\s*%", "percentage"),
        # Dollar amounts
        (
            r"\$\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(million|billion|trillion|M|B|T)?",
            "currency",
        ),
        # Large numbers with units
        (
            r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(million|billion|trillion|thousand)",
            "quantity",
        ),
        # Parameter counts (AI-specific)
        (r"(\d+(?:\.\d+)?)\s*[BM]\s*param", "parameters"),
        # Token counts
        (r"(\d+(?:\.\d+)?)\s*[BMT]?\s*tokens?", "tokens"),
    ]

    for pattern, num_type in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            # Get surrounding context (40 chars each side)
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 40)
            context = text[start:end].strip()
            numbers.append(
                {"value": match.group(0), "type": num_type, "context": context}
            )

    return numbers[:15]  # Limit to 15 data points


def _extract_dates(text: str) -> list[str]:
    """Extract date references."""
    import re

    dates = []

    patterns = [
        # Month Day, Year
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}",
        # Day Month Year
        r"\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}",
        # YYYY-MM-DD
        r"20\d{2}-\d{2}-\d{2}",
        # Quarter references
        r"Q[1-4]\s+20\d{2}",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if isinstance(matches[0], tuple) if matches else False:
            dates.extend([m[0] if isinstance(m, tuple) else m for m in matches])
        else:
            dates.extend(matches)

    return list(set(dates))[:10]


def _extract_key_claims(text: str, title: str = "") -> list[str]:
    """Extract likely key claims using heuristics."""
    import re

    claims = []

    # Sentences that start with claim indicators
    claim_patterns = [
        r"(?:^|\. )([A-Z][^.]*(?:announced|launched|released|introduced|achieved|reported|revealed|discovered|found|showed|demonstrated)[^.]*\.)",
        r"(?:^|\. )([A-Z][^.]*(?:will|plans to|expects to|intends to)[^.]*\.)",
        r"(?:^|\. )([A-Z][^.]*(?:first|largest|fastest|newest|leading|breakthrough)[^.]*\.)",
        r"(?:^|\. )([A-Z][^.]*(?:According to|Based on|Research shows)[^.]*\.)",
    ]

    for pattern in claim_patterns:
        matches = re.findall(pattern, text)
        claims.extend(matches)

    # Also grab first substantive sentence from each paragraph (often contains key info)
    paragraphs = text.split("\n\n")
    for para in paragraphs[:5]:  # First 5 paragraphs
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        if sentences and len(sentences[0]) > 50:
            claims.append(sentences[0])

    # Deduplicate and limit
    seen = set()
    unique_claims = []
    for claim in claims:
        claim_clean = claim.strip()
        if claim_clean not in seen and len(claim_clean) > 30:
            seen.add(claim_clean)
            unique_claims.append(claim_clean)

    return unique_claims[:8]


@mcp.tool()
@trace_tool_output
async def build_evidence_ledger(
    session_dir: str, topic: str, task_name: str = "default"
) -> str:
    """
    Build structured evidence ledger from research corpus for context-efficient synthesis.

    Extracts from each source:
    - Direct quotes with attribution
    - Specific data points (numbers, percentages, dates)
    - Key claims and findings
    - Source metadata (title, URL, date)

    The ledger compresses a large research corpus (~100KB+) into a smaller, high-signal
    evidence file (~10-20KB) that preserves the "quotable" material needed for synthesis.

    Use this AFTER finalize_research and BEFORE report synthesis for large corpora.

    Args:
        session_dir: Path to the current session workspace
        topic: Research topic (for context and themes)
        task_name: Task scope (should match finalize_research task_name)

    Returns:
        JSON with ledger path, extraction stats, and corpus size comparison
    """
    try:
        task_dir = os.path.join(session_dir, "tasks", task_name)
        filtered_dir = os.path.join(task_dir, "filtered_corpus")

        if not os.path.exists(filtered_dir):
            # Fallback to search_results_filtered_best if task-scoped corpus doesn't exist
            filtered_dir = os.path.join(session_dir, "search_results_filtered_best")
            if not os.path.exists(filtered_dir):
                return json.dumps(
                    {
                        "error": "No filtered corpus found. Run finalize_research first.",
                        "checked_paths": [
                            os.path.join(task_dir, "filtered_corpus"),
                            os.path.join(session_dir, "search_results_filtered_best"),
                        ],
                    }
                )

        # Collect all research files
        research_files = [
            f
            for f in os.listdir(filtered_dir)
            if f.endswith(".md") and not f.startswith("research_overview")
        ]

        if not research_files:
            return json.dumps(
                {
                    "error": "No research files found in filtered corpus",
                    "filtered_dir": filtered_dir,
                }
            )

        raw_corpus_size = 0
        evidence_entries = []
        evidence_id = 1

        for filename in sorted(research_files):
            filepath = os.path.join(filtered_dir, filename)

            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            raw_corpus_size += len(content)

            # Parse YAML frontmatter
            import yaml

            metadata = {}
            body = content

            if content.startswith("---"):
                fm_end = content.find("---", 4)
                if fm_end != -1:
                    fm_text = content[4:fm_end].strip()
                    try:
                        metadata = yaml.safe_load(fm_text) or {}
                    except:
                        pass
                    body = content[fm_end + 4 :].strip()

            # Extract evidence
            quotes = _extract_quotes(body)
            numbers = _extract_numbers(body)
            dates = _extract_dates(body)
            claims = _extract_key_claims(body, metadata.get("title", ""))

            # Build entry
            entry = {
                "id": f"EVID-{evidence_id:03d}",
                "title": metadata.get("title", "Untitled")[:80],
                "url": metadata.get("source", ""),
                "date": metadata.get("date", "unknown"),
                "filename": filename,
                "word_count": len(body.split()),
                "quotes": quotes,
                "data_points": numbers,
                "dates_mentioned": dates,
                "key_claims": claims,
            }

            evidence_entries.append(entry)
            evidence_id += 1

        # Build ledger markdown
        ledger_lines = [
            f"# Evidence Ledger: {topic}",
            f"**Generated:** {datetime.utcnow().isoformat()}Z",
            f"**Sources:** {len(evidence_entries)} files",
            f"**Original Corpus:** {raw_corpus_size:,} chars",
            "",
            "---",
            "",
            "> Use EVID-XXX IDs when citing evidence in the report.",
            "> This ledger preserves quotes, numbers, and claims for synthesis.",
            "",
        ]

        for entry in evidence_entries:
            ledger_lines.append(f"## {entry['id']}: {entry['title']}")
            ledger_lines.append(f"- **Source:** [{entry['filename']}]({entry['url']})")
            ledger_lines.append(f"- **Date:** {entry['date']}")
            ledger_lines.append(f"- **Words:** {entry['word_count']:,}")
            ledger_lines.append("")

            if entry["key_claims"]:
                ledger_lines.append("### Key Claims")
                for claim in entry["key_claims"][:5]:
                    ledger_lines.append(f"- {claim}")
                ledger_lines.append("")

            if entry["data_points"]:
                ledger_lines.append("### Data Points")
                for dp in entry["data_points"][:8]:
                    ledger_lines.append(
                        f"- **{dp['value']}** ({dp['type']}): {dp['context']}"
                    )
                ledger_lines.append("")

            if entry["quotes"]:
                ledger_lines.append("### Notable Quotes")
                for quote in entry["quotes"][:5]:
                    ledger_lines.append(f'- "{quote}"')
                ledger_lines.append("")

            if entry["dates_mentioned"]:
                ledger_lines.append(
                    f"### Dates Mentioned: {', '.join(entry['dates_mentioned'][:5])}"
                )
                ledger_lines.append("")

            ledger_lines.append("---")
            ledger_lines.append("")

        # Calculate compression stats
        ledger_content = "\n".join(ledger_lines)
        ledger_size = len(ledger_content)
        compression_ratio = (
            round((1 - ledger_size / raw_corpus_size) * 100, 1)
            if raw_corpus_size > 0
            else 0
        )

        # Save ledger
        ledger_path = os.path.join(task_dir, "evidence_ledger.md")
        os.makedirs(task_dir, exist_ok=True)

        with open(ledger_path, "w", encoding="utf-8") as f:
            f.write(ledger_content)

        # Write handoff.json for harness integration
        # This enables the harness to inject ledger-aware prompts on restart
        handoff_path = os.path.join(session_dir, "handoff.json")
        handoff_state = {
            "phase": "ledger_complete",
            "task_name": task_name,
            "topic": topic,
            "ledger_path": ledger_path,
            "corpus_stats": {
                "files": len(evidence_entries),
                "raw_chars": raw_corpus_size,
                "ledger_chars": ledger_size,
                "compression_ratio": compression_ratio,
            },
            "next_action": "READ_LEDGER_AND_WRITE_REPORT",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        with open(handoff_path, "w", encoding="utf-8") as f:
            json.dump(handoff_state, f, indent=2)

        return json.dumps(
            {
                "status": "Evidence ledger built successfully",
                "ledger_path": ledger_path,
                "sources_processed": len(evidence_entries),
                "raw_corpus_chars": raw_corpus_size,
                "ledger_chars": ledger_size,
                "compression_ratio": f"{compression_ratio}%",
                "total_quotes": sum(len(e["quotes"]) for e in evidence_entries),
                "total_data_points": sum(
                    len(e["data_points"]) for e in evidence_entries
                ),
                "total_claims": sum(len(e["key_claims"]) for e in evidence_entries),
                "next_step": "Read ONLY evidence_ledger.md for report synthesis. Do NOT read raw corpus files.",
                "handoff_saved": handoff_path,
            },
            indent=2,
        )

    except Exception as e:
        import traceback

        return json.dumps(
            {
                "error": f"Evidence extraction failed: {str(e)}",
                "traceback": traceback.format_exc(),
            }
        )


# =============================================================================
# IMAGE GENERATION TOOLS
# =============================================================================


@mcp.tool()
@trace_tool_output
def generate_image(
    prompt: str,
    input_image_path: str = None,
    output_dir: str = None,
    output_filename: str = None,
    preview: bool = False,
    model_name: str = "gemini-3-pro-image-preview",
) -> str:
    """
    Generate or edit an image using Gemini models.

    Args:
        prompt: Text description for generation, or edit instruction if input_image provided.
        input_image_path: Optional path to source image (for editing). If None, generates from scratch.
        output_dir: Optional preferred directory. Final output is always normalized under session work_products/media.
        output_filename: Optional filename. Directory components are ignored for safety.
        preview: If True, launches Gradio viewer with the generated image.
        model_name: Gemini model to use. Defaults to "gemini-3-pro-image-preview".

    Returns:
        JSON with status, output_path, description, and viewer_url (if preview=True).
    """
    try:
        from google import genai
        from google.genai import types
        from google.genai.types import GenerateContentConfig, Part
        from PIL import Image
        import base64
        from io import BytesIO

        # Initialize Gemini client
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return json.dumps({"error": "GEMINI_API_KEY not set in environment"})

        client = genai.Client(api_key=api_key)

        # Resolve happy-path output roots:
        # 1) session workspace work_products/media (primary)
        # 2) persistent artifacts/media (mirror)
        workspace = _resolve_workspace()
        if workspace:
            session_media_dir = (
                Path(workspace).resolve() / "work_products" / "media"
            )
        else:
            session_media_dir = (Path(os.getcwd()).resolve() / "work_products" / "media")
        artifacts_media_dir = Path(_resolve_artifacts_root()).resolve() / "media"

        # Coerce requested output_dir to workspace if it points outside allowed roots.
        requested_output_dir = (output_dir or "").strip()
        if requested_output_dir:
            try:
                requested_dir = Path(requested_output_dir).expanduser()
                if not requested_dir.is_absolute():
                    requested_dir = (Path(os.getcwd()) / requested_dir).resolve()
                else:
                    requested_dir = requested_dir.resolve()
                in_workspace = workspace and _is_within_root(workspace, str(requested_dir))
                in_artifacts = _is_within_root(str(artifacts_media_dir.parent), str(requested_dir))
                if not in_workspace and not in_artifacts:
                    requested_output_dir = ""
            except Exception:
                requested_output_dir = ""

        # Always write the primary output into session work_products/media.
        output_dir = str(session_media_dir)
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(artifacts_media_dir, exist_ok=True)

        # Prepare content for generation
        parts = []

        # If editing, include the input image
        if input_image_path:
            if not os.path.exists(input_image_path):
                return json.dumps(
                    {"error": f"Input image not found: {input_image_path}"}
                )

            with open(input_image_path, "rb") as img_file:
                img_bytes = img_file.read()
                parts.append(Part.from_bytes(data=img_bytes, mime_type="image/png"))

        # Prepare request content
        parts.append(types.Part.from_text(text=prompt))
        content_obj = types.Content(role="user", parts=parts)

        # Generate the image using streaming (more robust for mixed modalities)
        response_stream = client.models.generate_content_stream(
            model=model_name,
            contents=[content_obj],
            config=GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        saved_path = None
        text_output = ""

        for chunk in response_stream:
            if (
                not chunk.candidates
                or not chunk.candidates[0].content
                or not chunk.candidates[0].content.parts
            ):
                continue

            for part in chunk.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    # Found image data
                    # Streaming API returns raw bytes in inline_data.data
                    image_data = part.inline_data.data
                    image = Image.open(BytesIO(image_data))

                    # Generate filename if not provided
                    if not output_filename:
                        # Get description for filename
                        try:
                            description = describe_image_internal(image)
                        except:
                            description = "generated_image"

                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        safe_desc = "".join(
                            c if c.isalnum() or c in (" ", "_") else ""
                            for c in description
                        )
                        safe_desc = "_".join(safe_desc.split()[:5])  # First 5 words
                        output_filename = f"{safe_desc}_{timestamp}.png"

                    # Safety: prevent absolute/path-traversal filename overrides.
                    safe_output_name = os.path.basename(str(output_filename).strip())
                    if not safe_output_name:
                        safe_output_name = f"generated_image_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

                    saved_path = os.path.join(output_dir, safe_output_name)
                    image.save(saved_path, "PNG")

                elif hasattr(part, "text") and part.text:
                    text_output += part.text

        if not saved_path:
            return json.dumps(
                {"error": "No image generated", "text_output": text_output}
            )

        # Mirror to persistent artifacts/media with timestamped name.
        saved_path_obj = Path(saved_path).resolve()
        persistent_copy = artifacts_media_dir / (
            f"{saved_path_obj.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{saved_path_obj.suffix}"
        )
        try:
            import shutil

            shutil.copy2(saved_path_obj, persistent_copy)
        except Exception as exc:
            persistent_copy = None
            logger.warning("generate_image_persistent_copy_failed: %s", exc)

        result = {
            "success": True,
            "output_path": str(saved_path_obj),
            "description": description if "description" in locals() else None,
            "size_bytes": os.path.getsize(saved_path),
            "text_output": text_output if text_output else None,
        }
        if persistent_copy is not None:
            result["persistent_path"] = str(persistent_copy)
        result["session_output_path"] = str(saved_path_obj)
        if requested_output_dir:
            result["requested_output_dir"] = requested_output_dir

        # Launch preview if requested
        if preview:
            try:
                viewer_result = preview_image(str(saved_path_obj))
                viewer_data = json.loads(viewer_result)
                if "viewer_url" in viewer_data:
                    result["viewer_url"] = viewer_data["viewer_url"]
            except Exception as e:
                result["preview_error"] = str(e)

        return json.dumps(result, indent=2)

    except Exception as e:
        import traceback

        return json.dumps({"error": str(e), "traceback": traceback.format_exc()})


def describe_image_internal(image: "Image.Image") -> str:
    """Internal helper to describe image without ZAI Vision (uses simple analysis)."""
    # Simple fallback description based on image properties
    width, height = image.size
    mode = image.mode
    return f"{mode}_image_{width}x{height}"


@mcp.tool()
@trace_tool_output
def describe_image(image_path: str, max_words: int = 10) -> str:
    """
    Get a short description of an image using ZAI Vision (free).
    Useful for generating descriptive filenames.

    Args:
        image_path: Path to the image file.
        max_words: Maximum words in description (default 10).

    Returns:
        Short description suitable for filenames.
    """
    try:
        if not os.path.exists(image_path):
            return json.dumps({"error": f"Image not found: {image_path}"})

        # Try ZAI Vision via MCP if available
        try:
            # This would require calling the zai_vision MCP server
            # For now, fall back to simple description
            from PIL import Image

            img = Image.open(image_path)
            desc = describe_image_internal(img)
            return json.dumps({"description": desc})
        except Exception:
            # Fallback to basic file info
            filename = os.path.basename(image_path)
            return json.dumps({"description": f"image_{filename}"})

    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
@trace_tool_output
def preview_image(image_path: str, port: int = 7860) -> str:
    """
    Open an image in the Gradio viewer for human review.
    Useful for viewing any existing image in the workspace.

    Args:
        image_path: Absolute path to the image file.
        port: Port to launch Gradio on (default 7860).

    Returns:
        JSON with viewer_url (e.g., "http://127.0.0.1:7860").
    """
    try:
        import subprocess

        if not os.path.exists(image_path):
            return json.dumps({"error": f"Image not found: {image_path}"})

        # Check if gradio_viewer.py script exists
        script_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            ".claude",
            "skills",
            "image-generation",
            "scripts",
            "gradio_viewer.py",
        )

        if not os.path.exists(script_path):
            return json.dumps(
                {
                    "error": "Gradio viewer script not found",
                    "expected_path": script_path,
                    "note": "Preview functionality requires image-generation skill to be initialized",
                }
            )

        # Launch gradio viewer in background
        subprocess.Popen(
            [sys.executable, script_path, image_path, str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        viewer_url = f"http://127.0.0.1:{port}"
        return json.dumps(
            {"success": True, "viewer_url": viewer_url, "image_path": image_path}
        )

    except Exception as e:
        return json.dumps({"error": str(e)})


# =============================================================================
# MAIN - Start stdio server when run as a script
# =============================================================================

@mcp.tool()
@trace_tool_output
def batch_tool_execute(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Execute multiple tool calls in a batch (sequentially).
    Supports both Composio tools and Local Toolkit tools.
    
    Args:
        tool_calls: List of dicts, each with 'tool' (str) and 'input' (dict).
        
    Returns:
        List of results in the same order.
    """
    results = []
    
    # Initialize Composio client
    try:
        bridge = get_bridge()
        client = bridge.composio_client
    except Exception as e:
        return [{"error": f"Failed to initialize Composio client: {e}"}]
    
    import concurrent.futures
    import time

    # GUARDRAIL: Limit batch size to prevent resource exhaustion
    MAX_BATCH_SIZE = 20
    if len(tool_calls) > MAX_BATCH_SIZE:
         return [{"error": f"Batch size of {len(tool_calls)} exceeds maximum limit of {MAX_BATCH_SIZE}. Please split into smaller batches."}]

    sys.stderr.write(f"[Local Toolkit] Parallel Batch executing {len(tool_calls)} calls (max 10 workers)\n")
    sys.stderr.flush()

    results = [None] * len(tool_calls)
    
    def execute_single_tool_safe(index, call_data):
        name = call_data.get("tool", "")
        args = call_data.get("input", {})
        result_item = {"index": index, "tool": name, "status": "pending"}
        
        try:
            # 1. Composio Tools
            if "mcp__composio__" in name:
                action_name = name.split("mcp__composio__")[1]
                # Bridge check inside the thread? Better to get client once outside.
                resp = client.action(action_name).execute(args)
                
            # 2. Local Tools (Self-Call)
            elif "mcp__local_toolkit__" in name:
                local_name = name.split("mcp__local_toolkit__")[1]
                func = globals().get(local_name)
                if not func:
                     raise ValueError(f"Local tool '{local_name}' not found")
                resp = func(**args)
            
            else:
                 raise ValueError(f"Tool '{name}' not supported")

            # Truncate large results
            resp_str = str(resp)
            if len(resp_str) > 5000:
                result_item["result"] = resp_str[:5000] + "... [TRUNCATED]"
                result_item["truncated"] = True
            else:
                result_item["result"] = resp
            
            result_item["status"] = "success"

        except Exception as e:
            result_item["status"] = "error"
            result_item["error"] = str(e)
            sys.stderr.write(f"[Batch] Item {index} failed: {e}\n")
        
        return result_item

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_index = {
            executor.submit(execute_single_tool_safe, i, call): i 
            for i, call in enumerate(tool_calls)
        }
        
        for future in concurrent.futures.as_completed(future_to_index):
            i = future_to_index[future]
            try:
                # 3 minute timeout per item total execution time
                results[i] = future.result(timeout=180)
            except Exception as exc:
                sys.stderr.write(f"[Batch] Item {i} generated an exception: {exc}\n")
                results[i] = {"index": i, "status": "error", "error": str(exc)}

    return results


# @mcp.tool()
@trace_tool_output
async def _run_research_pipeline_legacy(query: str, task_name: str = "default") -> str:
    """
    Execute the Post-Search Research Pipeline: Crawl -> Refine -> Outline -> Draft -> Cleanup -> Compile.
    
    IMPORTANT: This tool expects that search results already exist in the session's 
    `search_results/` directory. The agent should call Composio search tools 
    (via MCP) BEFORE calling this tool.
    
    This function orchestrates:
    1. Finalize Research (Crawl URLs, process results, create corpus)
    2. Generate Outline (LLM-based)
    3. Draft Report (Parallel Workers)
    4. Cleanup Report (Synthesis & Formatting)
    5. Compile Report (HTML Generation)
    
    Args:
        query: The research topic (used for outline generation context)
        task_name: Identifier for the task directory (e.g., "russia_ukraine_jan2026")
    
    Returns:
        Success message with report location, or error details.
    """
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: CURRENT_SESSION_WORKSPACE not set."

    mcp_log(f"🚀 [Pipeline] Starting post-search research pipeline for: '{query}'", level="INFO", prefix="")
    
    # Verify search results exist
    search_dir = os.path.join(workspace, "search_results")
    if not os.path.isdir(search_dir):
        return (
            "❌ Pipeline Failed: No search_results/ directory found.\n"
            "The agent must call COMPOSIO_MULTI_EXECUTE_TOOL with search queries BEFORE calling this tool."
        )
    
    json_files = [f for f in os.listdir(search_dir) if f.endswith(".json")]
    if not json_files:
        return (
            "❌ Pipeline Failed: search_results/ directory is empty.\n"
            "The agent must call COMPOSIO_MULTI_EXECUTE_TOOL with search queries BEFORE calling this tool."
        )
    
    mcp_log(f"[Pipeline] Found {len(json_files)} search result file(s). Proceeding...", level="DEBUG")

    # 1. FINALIZE (Crawl & Refine)
    try:
        mcp_log("[Pipeline] Step 1/5: Crawling & Refining (this may take 30-60s)...", level="INFO")
        res = await finalize_research(session_dir=workspace, task_name=task_name)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "phase": "finalize_research",
            "message": f"Pipeline Failed at Finalize Step: {e}"
        }, indent=2)

    # 2. OUTLINE
    try:
        mcp_log("[Pipeline] Step 2/5: Generating Outline...", level="INFO")
        res = await generate_outline(topic=query, task_name=task_name)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "phase": "generate_outline",
            "message": f"Pipeline Failed at Outline Step: {e}"
        }, indent=2)

    # 3. DRAFT
    try:
        mcp_log("[Pipeline] Step 3/5: Drafting Sections (Parallel)...", level="INFO")
        res = await draft_report_parallel(task_name=task_name)
    except Exception as e:
         return json.dumps({
            "status": "error",
            "phase": "draft_report_parallel",
            "message": f"Pipeline Failed at Drafting Step: {e}"
        }, indent=2)

    # 4. CLEANUP
    try:
        mcp_log("[Pipeline] Step 4/5: Cleaning & Synthesizing (LLM Audit)...", level="INFO")
        res = await cleanup_report()
    except Exception as e:
         return json.dumps({
            "status": "error",
            "phase": "cleanup_report",
            "message": f"Pipeline Failed at Cleanup Step: {e}"
        }, indent=2)

    # 5. COMPILE
    try:
        mcp_log("[Pipeline] Step 5/5: Compiling HTML...", level="INFO")
        res = compile_report(theme="modern")
        
        return json.dumps({
            "status": "success",
            "message": "Unified Research Pipeline Complete!",
            "workspace": workspace,
            "outputs": {
                "report_html": os.path.join(workspace, "work_products", "report.html"),
                "refined_corpus": os.path.join(workspace, "tasks", task_name, "refined_corpus.md")
            },
            "summary": res
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "phase": "compile",
            "message": str(e)
        }, indent=2)



@mcp.tool()
@trace_tool_output
async def _run_research_phase_legacy(query: str, task_name: str = "default") -> str:
    """
    Execute Phase 1 of the Research Pipeline: Crawl -> Refine.
    This creates the 'refined_corpus.md' needed for the report writer.
    """
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: CURRENT_SESSION_WORKSPACE not set."

    mcp_log(f"🚀 [Research Phase] Starting crawling & refinement for: '{query}'", level="INFO", prefix="")
    
    # Verify search results exist
    search_dir = os.path.join(workspace, "search_results")
    if not os.path.isdir(search_dir):
        return (
            "❌ Research Phase Failed: No search_results/ directory found.\n"
            "The agent must call COMPOSIO_MULTI_EXECUTE_TOOL with search queries BEFORE calling this tool."
        )
    
    json_files = [f for f in os.listdir(search_dir) if f.endswith(".json")]
    if not json_files:
        return (
            "❌ Research Phase Failed: search_results/ directory is empty.\n"
            "The agent must call COMPOSIO_MULTI_EXECUTE_TOOL with search queries BEFORE calling this tool."
        )
    
    mcp_log(f"[Research Phase] Found {len(json_files)} search result file(s). Proceeding...", level="DEBUG")

    # 1. FINALIZE (Crawl & Refine)
    try:
        mcp_log("[Research Phase] Step 1/1: Crawling & Refining...", level="INFO")
        res = await finalize_research(session_dir=workspace, task_name=task_name)
        if "error" in res.lower() and "status" not in res.lower():
             return f"❌ Research Phase Failed: {res}"
    except Exception as e:
        return json.dumps({
            "status": "error",
            "phase": "research",
            "message": str(e)
        }, indent=2)
        
    corpus_path = os.path.join(workspace, 'tasks', task_name, 'refined_corpus.md')
    return json.dumps({
        "status": "success",
        "message": "Research Phase Complete! Refined corpus created.",
        "workspace": workspace,
        "outputs": {
            "refined_corpus": corpus_path
        },
        "next_step_suggestion": "run_report_generation"
    }, indent=2)


@mcp.tool()
@trace_tool_output
async def _run_report_generation_legacy(query: str, task_name: str = "default", corpus_data: str = None) -> str:
    """
    Execute Phase 2 of the Research Pipeline: Outline -> Draft -> Cleanup -> Compile.
    This consumes 'refined_corpus.md' and generates 'report.html'.
    
    Args:
        query: Report topic/query
        task_name: Unique task identifier
        corpus_data: Optional text content to use as the corpus (auto-creates refined_corpus.md)
    """
    workspace = _resolve_workspace()
    if not workspace:
        return "Error: CURRENT_SESSION_WORKSPACE not set."

    mcp_log(f"🚀 [Report Gen] Starting report generation for: '{query}'", level="INFO", prefix="")
    
    corpus_path = os.path.join(workspace, "tasks", task_name, "refined_corpus.md")

    # If explicit corpus data provided, write it to disk first
    if corpus_data:
        try:
            mcp_log("[Report Gen] Using provided corpus data...", level="INFO")
            os.makedirs(os.path.dirname(corpus_path), exist_ok=True)
            with open(corpus_path, "w", encoding="utf-8") as f:
                f.write(corpus_data)
        except Exception as e:
            return f"❌ Failed to write corpus_data to {corpus_path}: {e}"
    
    # Verify refined corpus exists
    if not os.path.exists(corpus_path):
        return (
            f"❌ Report Gen Failed: Refined corpus not found at {corpus_path}.\n"
            "The Research Specialist must complete the research phase first."
        )

    # 1. OUTLINE
    try:
        mcp_log("[Report Gen] Step 1/4: Generating Outline...", level="INFO")
        res = await generate_outline(topic=query, task_name=task_name)
        if "Error" in res:
             return f"❌ Report Gen Failed at Outline Step: {res}"
    except Exception as e:
        return f"❌ Report Gen Failed at Outline Step: {e}"

    # 2. DRAFT
    try:
        mcp_log("[Report Gen] Step 2/4: Drafting Sections (Parallel)...", level="INFO")
        res = await draft_report_parallel(task_name=task_name)
        if "Error" in res:
             return f"❌ Report Gen Failed at Drafting Step: {res}"
    except Exception as e:
         return f"❌ Report Gen Failed at Drafting Step: {e}"

    # 3. CLEANUP
    try:
        mcp_log("[Report Gen] Step 3/4: Cleaning & Synthesizing (LLM Audit)...", level="INFO")
        res = await cleanup_report()
    except Exception as e:
         return json.dumps({
            "status": "error",
            "phase": "cleanup",
            "message": str(e)
        }, indent=2)

    # 4. COMPILE
    try:
        mcp_log("[Report Gen] Step 4/4: Compiling HTML...", level="INFO")
        res = compile_report(theme="modern")
        
        return json.dumps({
            "status": "success",
            "message": "Report Generation Phase Complete!",
            "workspace": workspace,
            "outputs": {
                "report_html": os.path.join(workspace, "work_products", "report.html")
            },
            "summary": res
        }, indent=2)
    except Exception as e:
         return json.dumps({
            "status": "error",
            "phase": "compile",
            "message": str(e)
        }, indent=2)


if __name__ == "__main__":
    # Run the MCP server using stdio transport
    mcp.run(transport="stdio")
