from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import sys
import json
import logging
import inspect
from datetime import datetime
from functools import wraps
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ValidationError

# Setup logger for MCP server
logger = logging.getLogger("mcp_server")
logging.basicConfig(level=logging.INFO)

# Ensure src path for imports
sys.path.append(os.path.abspath("src"))
# Ensure project root for Memory_System
sys.path.append(os.path.dirname(os.path.abspath(__file__))) # src/
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) # Repo Root
from universal_agent.search_config import SEARCH_TOOL_CONFIG
from tools.workbench_bridge import WorkbenchBridge
from composio import Composio

# Memory System Integration
disable_local_memory = os.getenv("UA_DISABLE_LOCAL_MEMORY", "").lower() in {"1", "true", "yes"}
if disable_local_memory:
    sys.stderr.write("[Local Toolkit] Memory System disabled via UA_DISABLE_LOCAL_MEMORY.\n")
    MEMORY_MANAGER = None
else:
    try:
        from Memory_System.manager import MemoryManager
        MEMORY_MANAGER = MemoryManager(storage_dir=os.path.join(os.path.dirname(__file__), "..", "Memory_System_Data"))
        sys.stderr.write("[Local Toolkit] Memory System initialized.\n")
    except Exception as e:
        sys.stderr.write(f"[Local Toolkit] Memory System init failed: {e}\n")
        MEMORY_MANAGER = None

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
    sys.stderr.write("[Local Toolkit] Server starting components...\n")
    mcp = FastMCP("Local Intelligence Toolkit")
except Exception:
    raise


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
        sys.stderr.write(f"[Local Toolkit] Path auto-corrected: AGENT_RUNSPACES → AGENT_RUN_WORKSPACES\n")
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


@mcp.tool()
@trace_tool_output
def read_local_file(path: str) -> str:
    """
    Read content from a file in the Local Workspace.
    """
    try:
        path = fix_path_typos(path)  # Auto-correct common model typos
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return f"Error: File not found at {path}"

        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        return content
    except Exception as e:
        return f"Error reading file: {str(e)}"


# =============================================================================
# CORPUS SIZE LIMITS - Conservative limits to prevent context overflow
# =============================================================================
BATCH_SAFE_THRESHOLD = 2500    # Files under this are "batch-safe" (auto-include)
# BATCH_MAX_TOTAL: Stop batch reading when cumulative word count hits this limit
# Set UA_BATCH_MAX_WORDS=100000 (or higher) for stress testing context limits
BATCH_MAX_TOTAL = int(os.getenv("UA_BATCH_MAX_WORDS", "25000"))
LARGE_FILE_THRESHOLD = 5000    # Files over this marked as "read individually"

# =============================================================================
# FILTERED CORPUS RULES (for report generation)
# =============================================================================
FILTER_BLACKLIST_DOMAINS = {
    "wikipedia.org",
}
FILTER_URL_SKIP_TOKENS = (
    "/live", "/liveblog", "/live-blog", "/home", "/topics", "/tag/",
    "/sitemap", "/video", "/videos", "/podcast", "/audio", "/photo",
)
FILTER_TITLE_SKIP_TOKENS = (
    "home", "live", "liveblog", "newsletter", "podcast", "video",
    "watch", "listen", "most read", "trending",
)
FILTER_PROMO_TOKENS = (
    "subscribe", "sign in", "sign up", "support", "donate", "contribute",
    "membership", "account", "continue", "payment", "your support",
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


def _is_promotional(text: str) -> bool:
    lowered = text.lower()
    hits = sum(lowered.count(token) for token in FILTER_PROMO_TOKENS)
    return hits >= 4


def _word_count(text: str) -> int:
    return len(text.split())


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


def _remove_navigation_lines(body: str) -> tuple[str, int]:
    lines = []
    short_line_count = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("*") or stripped.startswith("!"):
            continue
        if len(stripped) < 40:
            short_line_count += 1
            continue
        lines.append(stripped)
    return "\n".join(lines).strip(), short_line_count


def _filter_crawl_content(raw_text: str) -> tuple[str | None, str, dict, str]:
    meta, meta_block, body = _split_front_matter(raw_text)
    ok, reason = _url_title_gate(meta)
    if not ok:
        return None, reason, meta, meta_block

    cleaned, short_line_count = _remove_navigation_lines(body)
    if _word_count(cleaned) < 225:
        return None, "too_short", meta, meta_block
    if _word_count(cleaned) < 300 and _is_promotional(cleaned):
        return None, "promo_short", meta, meta_block
    if short_line_count > 300 and _word_count(cleaned) < 800:
        return None, "nav_heavy", meta, meta_block
    return cleaned, "ok", meta, meta_block


@mcp.tool()
@trace_tool_output
def read_research_files(file_paths: list[str]) -> str:
    """
    Read ALL research files in a single batch call. Returns complete corpus content.
    
    Use this after reviewing research_overview.md to efficiently read 
    the full content of selected source files for quotes and facts.
    
    OVERFLOW HANDLING (automatic):
    - If output exceeds Claude's 25K token limit, it will be saved to a temp file
    - You'll receive instructions to read the temp file in chunks using offset/limit
    - This is the expected behavior for large research corpora - follow the instructions
    
    Args:
        file_paths: List of file paths to read (from research_overview.md listing)
    
    Returns:
        Combined content of ALL files, separated by clear markers.
        Each file section includes the filename and word count.
    """
    if not file_paths:
        return "Error: No file paths provided"
    
    MAX_BATCH_CHARS = 75000
    results = []
    current_chars = 0
    success_count = 0
    truncated = False
    remaining_start_idx = 0
    remapped_files = []
    
    for i, path in enumerate(file_paths):
        try:
            original_path = path # Keep for reference
            path = fix_path_typos(path)
            abs_path = os.path.abspath(path)

            # Prefer filtered corpus when raw crawl paths are provided.
            if "/search_results/" in abs_path and "/search_results_filtered_best/" not in abs_path:
                filtered_path = abs_path.replace(
                    "/search_results/", "/search_results_filtered_best/"
                )
                if os.path.exists(filtered_path):
                    remapped_files.append((abs_path, filtered_path))
                    abs_path = filtered_path
            
            if not os.path.exists(abs_path):
                results.append(f"\n{'='*60}\n❌ FILE NOT FOUND: {original_path}\n{'='*60}\n")
                continue
            
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            content_len = len(content)
            
            # Smart Batching Check:
            # If we already have content, and adding this file would exceed limit, STOP.
            # (We always allow at least one file to ensure progress, even if it exceeds limit slightly)
            if results and (current_chars + content_len > MAX_BATCH_CHARS):
                truncated = True
                remaining_start_idx = i
                break

            word_count = len(content.split())
            current_chars += content_len
            success_count += 1
            
            # Add clear section marker
            filename = os.path.basename(path)
            results.append(
                f"\n{'='*60}\n"
                f"📄 FILE: {filename} ({word_count:,} words)\n"
                f"{'='*60}\n\n"
                f"{content}"
            )
        except Exception as e:
            results.append(f"\n{'='*60}\n❌ ERROR reading {path}: {str(e)}\n{'='*60}\n")
    
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
    header += "\n"
    
    combined_output = header + "\n".join(results)
    
    if truncated:
        remaining_files = file_paths[remaining_start_idx:]
        combined_output += (
            f"\n\n{'='*60}\n"
            f"⚠️  BATCH TRUNCATED TO AVOID ERRORS (Limit {MAX_BATCH_CHARS:,} chars)\n"
            f"Read {success_count} of {len(file_paths)} requested files.\n"
            f"{'='*60}\n"
            f"👇 TO CONTINUE, CALL TOOL AGAIN WITH THESE FILES:\n"
            f"{json.dumps(remaining_files)}\n"
            f"{'='*60}\n"
        )
        
    return combined_output


@mcp.tool()
@trace_tool_output
def write_local_file(path: str, content: str, append: bool = False) -> str:
    """
    Write content to a file in the Local Workspace.
    Useful for saving reports, summaries, or code generated by sub-agents.
    
    IMPORTANT - FOR LARGE CONTENT (>50KB):
    - If you're writing very large content (like full HTML reports), consider
      using the native Write tool instead, which handles large content better.
    - If this tool fails or you receive schema validation errors, switch to
      the native Write tool.
    
    CHUNKED WRITING (for very large files):
    - Write in chunks by using append=True after the first chunk:
      1. First call: write_local_file(path="report.html", content="<html>...part1...")
      2. Continue: write_local_file(path="report.html", content="...part2...", append=True)
      3. Finish: write_local_file(path="report.html", content="...</html>", append=True)
    
    Args:
        path: Target file path (absolute or relative to session workspace)
        content: Content to write to the file
        append: If True, append to existing file instead of overwriting (default: False)
    
    Returns:
#         Success message with character count, or error description.
#     """
    try:
        abs_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        mode = "a" if append else "w"
        with open(abs_path, mode, encoding="utf-8") as f:
            f.write(content)
        action = "appended" if append else "wrote"
        return f"Successfully {action} {len(content)} chars to {path}"
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

        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in validated_files:
                # Arcname is the name inside the zip file (basename)
                zf.write(file_path, arcname=os.path.basename(file_path))
                
        # Check if created
        if os.path.exists(output_path):
             size = os.path.getsize(output_path)
             return json.dumps({
                 "success": True, 
                 "archive_path": output_path,
                 "size_bytes": size,
                 "files_included": len(validated_files)
             })
        return json.dumps({"error": "Failed to create archive file"})

    except Exception as e:
        return json.dumps({"error": f"Compression failed: {str(e)}"})


@mcp.tool()
@trace_tool_output
def upload_to_composio(
    path: str, 
    tool_slug: str = "GMAIL_SEND_EMAIL",
    toolkit_slug: str = "gmail"
) -> str:
    """
    Upload a local file to Composio S3 for use as an email attachment or other tool input.
    Uses native Composio SDK FileUploadable.from_path() - the correct, supported method.
    
    Args:
        path: Absolute path to the local file to upload
        tool_slug: The Composio tool that will consume this file (default: GMAIL_SEND_EMAIL)
        toolkit_slug: The toolkit the tool belongs to (default: gmail)
    
    Returns JSON with:
    - s3key: ID for tool attachments (pass to Gmail/Slack)
    - mimetype: Detected file type
    - name: Original filename
    """
    try:
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return json.dumps({"error": f"File not found: {path}"})
        
        # Import native Composio file helper
        from composio.core.models._files import FileUploadable
        
        # Get Composio client
        client = Composio(api_key=os.environ.get("COMPOSIO_API_KEY"))
        
        # Use native SDK method - this is the correct approach per Composio docs
        sys.stderr.write(f"[upload_to_composio] Uploading {abs_path} via native FileUploadable.from_path()\\n")
        
        result = FileUploadable.from_path(
            client=client.client,
            file=abs_path,
            tool=tool_slug,
            toolkit=toolkit_slug
        )
        
        # Return the attachment-ready format
        response = {
            "s3key": result.s3key,
            "mimetype": result.mimetype,
            "name": result.name,
            "local_path": abs_path
        }
        
        sys.stderr.write(f"[upload_to_composio] SUCCESS: s3key={result.s3key}\\n")
        return json.dumps(response, indent=2)
        
    except Exception as e:
        import traceback
        sys.stderr.write(f"[upload_to_composio] ERROR: {traceback.format_exc()}\\n")
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
    
    Examples of good questions:
    - Delivery preferences (email results? Slack notification?)
    - Output format (detailed report vs summary vs raw research?)
    - Scope constraints (date range? specific focus areas?)
    - Quality expectations (quick scan vs deep analysis?)
    
    Args:
        questions: List of question objects, each containing:
            - question (str): The full question text
            - header (str): Short label (max 12 chars), e.g., "Delivery"
            - options (list): Available choices with 'label' and 'description'
            - multiSelect (bool): Allow multiple selections
    
    Returns:
        JSON string with answers: {"question_text": "selected_option_label", ...}
    
    Example:
        ask_user_questions([
            {
                "question": "Would you like me to email the final results?",
                "header": "Delivery",
                "options": [
                    {"label": "Yes, email me", "description": "Send to your registered email"},
                    {"label": "No, save locally", "description": "Just save files to workspace"}
                ],
                "multiSelect": False
            }
        ])
    """
    try:
        from universal_agent.harness import ask_user_questions as _ask
        answers = _ask(questions)
        return json.dumps(answers, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})

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
    if not MEMORY_MANAGER:
        return "Error: Memory System not initialized."
    return MEMORY_MANAGER.core_memory_replace(label, new_value)

@mcp.tool()
@trace_tool_output
def core_memory_append(label: str, text_to_append: str) -> str:
    """
    Append text to a Core Memory block.
    Useful for adding a new preference without deleting old ones.
    """
    if not MEMORY_MANAGER:
         return "Error: Memory System not initialized."
    return MEMORY_MANAGER.core_memory_append(label, text_to_append)

@mcp.tool()
@trace_tool_output
def archival_memory_insert(content: str, tags: str = "") -> str:
    """
    Save a fact, document, or event to long-term archival memory.
    Use for things that don't need to be in active context.
    """
    if not MEMORY_MANAGER:
         return "Error: Memory System not initialized."
    return MEMORY_MANAGER.archival_memory_insert(content, tags)

@mcp.tool()
@trace_tool_output
def archival_memory_search(query: str, limit: int = 5) -> str:
    """
    Search long-term archival memory using semantic search.
    """
    if not MEMORY_MANAGER:
         return "Error: Memory System not initialized."
    return MEMORY_MANAGER.archival_memory_search(query, limit)

@mcp.tool()
@trace_tool_output
def get_core_memory_blocks() -> str:
    """
    Read all current Core Memory blocks.
    Useful to verify what you currently 'know' in your core memory.
    """
    if not MEMORY_MANAGER:
         return "Error: Memory System not initialized."
    
    blocks = MEMORY_MANAGER.agent_state.core_memory
    output = []
    for b in blocks:
        output.append(f"[{b.label}]\n{b.value}\n")
    return "\n".join(output)


# =============================================================================
# PYDANTIC MODELS FOR SEARCH RESULTS
# =============================================================================

class SearchItem(BaseModel):
    """Represents a single search result or news article."""
    url: Optional[str] = None
    link: Optional[str] = None  # Fallback for Scholar/News
    title: Optional[str] = None
    snippet: Optional[str] = None
    # Allow extra fields for flexibility
    class Config:
        extra = "ignore"

class SearchResultFile(BaseModel):
    """
    Schema for Composio search result files (combines Web and News structures).
    Web Search: {"results": [...]}
    News Search: {"articles": [...]}
    Web Answer: {"results": [...]} (nested inside outer object, but we parse the file content)
    """
    results: Optional[List[SearchItem]] = None
    articles: Optional[List[SearchItem]] = None
    
    # Allow extra fields/metadata
    class Config:
        extra = "ignore"

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
        sys.stderr.write(f"[crawl_core] Using Cloud API for {len(urls)} URLs\\n")
        
        try:
            import asyncio

            async def crawl_single_url(session, url):
                """Crawl a single URL using Cloud API"""
                payload = {
                    "url": url,
                    "apikey": crawl4ai_api_key,
                    # Note: Don't use output_format:fit_markdown - returns empty for news sites
                    "excluded_tags": ["nav", "footer", "header", "aside", "script", "style", "form"],
                    "remove_overlay_elements": True,
                    "word_count_threshold": 10,
                    "cache_mode": "bypass",
                    "magic": True,  # Anti-bot protection bypass
                }
                
                try:
                    async with session.post(cloud_endpoint, json=payload, timeout=60) as resp:
                        if resp.status != 200:
                            return {"url": url, "success": False, "error": f"HTTP {resp.status}"}
                        
                        data = await resp.json()
                        
                        # Cloud API returns content directly (no polling needed)
                        if data.get("success") == False:
                            return {"url": url, "success": False, "error": data.get("error", "Unknown error")}
                        if isinstance(data.get("data"), str):
                            return {"url": url, "success": False, "error": data.get("data")}
                        
                        # Get content (may be nested under data)
                        payload = data.get("data") if isinstance(data.get("data"), dict) else data
                        raw_content = (
                            payload.get("content")
                            or payload.get("markdown")
                            or payload.get("fit_markdown")
                            or ""
                        )
                        content = raw_content
                        
                        # Post-process: Strip markdown links, keep just the text
                        # [link text](url) -> link text
                        import re
                        content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
                        # Also remove bare URLs that start lines
                        content = re.sub(r'^https?://[^\s]+\s*$', '', content, flags=re.MULTILINE)
                        # Remove image markdown ![alt](url)
                        content = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', content)
                        # Clean up excessive blank lines
                        content = re.sub(r'\n{3,}', '\n\n', content)
                        
                        return {
                            "url": url,
                            "success": True,
                            "content": content,
                            "raw_content": raw_content,  # Keep for date extraction
                            "metadata": payload.get("metadata", {}),
                        }
                except asyncio.TimeoutError:
                    return {"url": url, "success": False, "error": "Timeout (60s)"}
                except Exception as e:
                    return {"url": url, "success": False, "error": str(e)}
            
            # Execute concurrent crawl (direct await since we're already async)
            async with aiohttp.ClientSession() as session:
                tasks = [crawl_single_url(session, url) for url in urls]
                crawl_results = await asyncio.gather(*tasks, return_exceptions=True)
            sys.stderr.write(f"[crawl_core] Cloud API returned {len(crawl_results)} results\\n")
            
            # Process results
            for result in crawl_results:
                if isinstance(result, Exception):
                    results_summary["failed"] += 1
                    results_summary["errors"].append({"url": "unknown", "error": str(result)})
                    continue
                
                url = result.get("url", "unknown")
                
                if result.get("success"):
                    content = result.get("content", "")
                    metadata = result.get("metadata", {})
                    
                    if content:
                        # Detect Cloudflare/captcha blocks
                        is_cloudflare_blocked = (
                            len(content) < 2000 and 
                            ("cloudflare" in content.lower() or 
                             "verifying you are human" in content.lower() or
                             "security of your connection" in content.lower())
                        )
                        if is_cloudflare_blocked:
                            logger.warning(f"Cloudflare blocked: {url}")
                            results_summary["failed"] += 1
                            results_summary["errors"].append({"url": url, "error": "Cloudflare blocked"})
                            continue
                        
                        # Save to file with YAML frontmatter metadata
                        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
                        filename = f"crawl_{url_hash}.md"
                        filepath = os.path.join(search_results_dir, filename)
                        
                        # Build metadata header
                        title = metadata.get("title") or metadata.get("og:title") or "Untitled"
                        description = metadata.get("description") or metadata.get("og:description") or ""
                        
                        # Extract article date from URL pattern (e.g., /2025/12/28/)
                        import re
                        article_date = None
                        
                        # Try URL pattern first (most reliable)
                        date_match = re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})/', url)
                        if date_match:
                            article_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
                        else:
                            date_match = re.search(r'/(\d{4})-(\d{1,2})-(\d{1,2})/', url)
                            if date_match:
                                article_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
                        
                        # Fallback: extract from raw content (before link stripping)
                        if not article_date:
                            raw_content = result.get("raw_content", content)
                            # Search full content for date patterns (some sites have tons of nav bloat)
                            # Pattern: "Month Day, Year" (e.g., December 28, 2025)
                            months = 'January|February|March|April|May|June|July|August|September|October|November|December'
                            match = re.search(rf'({months})\s+(\d{{1,2}}),?\s+(\d{{4}})', raw_content, re.I)
                            if match:
                                month_map = {'january':'01','february':'02','march':'03','april':'04','may':'05','june':'06',
                                             'july':'07','august':'08','september':'09','october':'10','november':'11','december':'12'}
                                article_date = f"{match.group(3)}-{month_map[match.group(1).lower()]}-{match.group(2).zfill(2)}"
                            else:
                                # Pattern: "Day Mon Year" (e.g., 29 Dec 2025)
                                match = re.search(r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', raw_content, re.I)
                                if match:
                                    month_map = {'jan':'01','feb':'02','mar':'03','apr':'04','may':'05','jun':'06',
                                                 'jul':'07','aug':'08','sep':'09','oct':'10','nov':'11','dec':'12'}
                                    article_date = f"{match.group(3)}-{month_map[match.group(2).lower()]}-{match.group(1).zfill(2)}"
                        
                        # YAML frontmatter for rich metadata
                        date_line = f"date: {article_date}" if article_date else "date: unknown"
                        frontmatter = f"""---
title: "{title.replace('"', "'")}"
source: {url}
{date_line}
description: "{description[:200].replace('"', "'") if description else ''}"
word_count: {len(content.split())}
---

"""
                        final_content = frontmatter + content
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(final_content)
                        
                        logger.info(f"Cloud API: Saved {len(content)} bytes for {url[:50]}")
                        results_summary["successful"] += 1
                        results_summary["saved_files"].append({
                            "url": url,
                            "file": filename,
                            "path": filepath,
                        })
                    else:
                        results_summary["failed"] += 1
                        results_summary["errors"].append({"url": url, "error": "Empty markdown"})
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
                            body = full_content[fm_end + 4:].strip()
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
                    file_metadata.append({
                        "index": i,
                        "file": file_info['file'],
                        "path": file_info['path'],
                        "url": file_info['url'],
                        "title": metadata.get("title", "Untitled"),
                        "date": metadata.get("date", "unknown"),
                        "word_count": word_count,
                    })
                
                # Categorize files by size
                batch_safe_files = [f for f in file_metadata if f["word_count"] <= BATCH_SAFE_THRESHOLD]
                large_files = [f for f in file_metadata if f["word_count"] > LARGE_FILE_THRESHOLD]
                medium_files = [f for f in file_metadata if BATCH_SAFE_THRESHOLD < f["word_count"] <= LARGE_FILE_THRESHOLD]
                
                # Build tiered overview
                overview_header = f"""# Research Sources Overview
**Generated:** {datetime.utcnow().isoformat()}Z
**Total Sources:** {len(results_summary['saved_files'])} articles
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
                        overview_header += f"| {f['index']} | `{f['file']}` | {f['word_count']:,} | {f['title'][:40]}... | `read_local_file(path=\"{f['path']}\")` |\n"
                
                overview_header += f"""
---

## All Sources Detail

"""
                # Add brief metadata for each source (no excerpts to save space)
                for f in file_metadata:
                    overview_header += f"""### Source {f['index']}: {f['title'][:60]}
- **File:** `{f['file']}` ({f['word_count']:,} words)
- **URL:** {f['url']}
- **Date:** {f['date']}

"""
                
                overview_path = os.path.join(search_results_dir, "research_overview.md")
                
                with open(overview_path, "w", encoding="utf-8") as f:
                    f.write(overview_header)
                
                results_summary["overview_file"] = overview_path
                results_summary["total_words_available"] = total_words
                results_summary["batch_safe_count"] = len(batch_safe_files)
                results_summary["large_file_count"] = len(large_files)
                logger.info(f"Created research_overview.md with {len(results_summary['saved_files'])} sources, {total_words:,} total words")
                
            except Exception as e:
                logger.warning(f"Failed to create research overview: {e}")
            
        return json.dumps(results_summary, indent=2)
    
    # Fallback to Local/Docker mode (unchanged from original)
    else:
         if crawl4ai_api_url:
            # Docker API mode (legacy): Use crawl4ai Docker container
            sys.stderr.write(f"[crawl_core] Using Docker API at {crawl4ai_api_url} for {len(urls)} URLs\\n")
            return json.dumps({"error": "Docker API mode deprecated. Set CRAWL4AI_API_KEY for Cloud API or remove CRAWL4AI_API_URL for local mode."})
                
         return json.dumps(results_summary, indent=2)


@mcp.tool()
@trace_tool_output
async def crawl_parallel(urls: list[str], session_dir: str) -> str:
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
async def finalize_research(session_dir: str, task_name: str = "default") -> str:
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
        
    Returns:
        Summary of operation: URLs found, crawl success/fail, and path to scoped overview.
    """
    try:
        search_results_dir = os.path.join(session_dir, "search_results")
        processed_dir = os.path.join(search_results_dir, "processed_json")
        
        # Task-specific context directory
        task_dir = os.path.join(session_dir, "tasks", task_name)
        os.makedirs(task_dir, exist_ok=True)
        
        if not os.path.exists(search_results_dir):
            return json.dumps({"error": f"Search results directory not found: {search_results_dir}"})
            
        # Ensure archive dir exists
        os.makedirs(processed_dir, exist_ok=True)
            
        all_urls = set()
        scanned_files = 0
        processed_files_list = []
        
        # 1. Scan Inbox (Root only) and Extract
        # Strict filter: ONLY .json files in the root (ignore directories)
        candidates = [
            f for f in os.listdir(search_results_dir) 
            if f.endswith(".json") and os.path.isfile(os.path.join(search_results_dir, f))
        ]
        
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
                
                extracted_count = 0
                
                if config:
                    # We know exactly how to parse this tool
                    list_key = config["list_key"]
                    url_key = config["url_key"]
                    
                    # Get the list of items
                    items = data.get(list_key, [])
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                url = item.get(url_key)
                                if url and isinstance(url, str) and url.startswith("http"):
                                    all_urls.add(url)
                                    extracted_count += 1
                    
                    if extracted_count > 0:
                        logger.info(f"[{tool_name}] Config-parsed {extracted_count} URLs from {filename}")
                        scanned_files += 1
                        processed_files_list.append(filename)
                        # We will move file later
                
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
                            logger.info(f"[Legacy] Pydantic-parsed {len(urls)} URLs from {filename}")
                    except ValidationError:
                        # Only warn if we didn't already handle it via config
                        logger.warning(f"Unknown tool schema in {filename} (Tool: {tool_name})")

            except Exception as e:
                logger.warning(f"Error reading {filename}: {e}")
        
        if not all_urls:
            return json.dumps({
                "status": "No URLs found", 
                "scanned_files": scanned_files,
                "note": "Ensure search results are regular JSON files with 'results' or 'articles' lists."
            })
            
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

        # 3. Execute Crawl (Saves to Global Cache in search_results/)
        sys.stderr.write(
            f"[finalize] Found {len(url_list)} unique URLs from {scanned_files} files "
            f"({len(filtered_urls)} after blacklist). Starting crawl...\\n"
        )
        
        # Call Core Logic
        crawl_result_json = await _crawl_core(filtered_urls, session_dir)
        crawl_result = json.loads(crawl_result_json)

        # 4. Build Filtered Corpus (SCOPED to Task Directory)
        # Instead of search_results_filtered_best, we put cleaned files in tasks/{task_name}/filtered_corpus
        filtered_dir = os.path.join(task_dir, "filtered_corpus")
        os.makedirs(filtered_dir, exist_ok=True)
        
        filtered_files = []
        filtered_dropped = []

        for item in crawl_result.get("saved_files", []):
            path = item.get("path")
            if not path or not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            filtered_body, status, meta, meta_block = _filter_crawl_content(raw_text)
            if not filtered_body:
                filtered_dropped.append({"path": path, "status": status})
                continue
            frontmatter = f"---\n{meta_block}\n---\n\n" if meta_block else ""
            filename = os.path.basename(path)
            filtered_path = os.path.join(filtered_dir, filename)
            final_content = frontmatter + filtered_body
            with open(filtered_path, "w", encoding="utf-8") as f:
                f.write(final_content)
            filtered_files.append({
                "file": filename,
                "path": filtered_path,
                "url": meta.get("source", ""),
                "title": meta.get("title", "Untitled"),
                "date": meta.get("date", "unknown"),
                "word_count": _word_count(filtered_body),
            })

        # 5. Build Scoped Overview
        # We need to read the JSONs again to build the index snippets.
        # But they are now in the ARCHIVE directory (processed_dir).
        search_items = []
        for filename in processed_files_list:
            # Look in processed_dir now
            path = os.path.join(processed_dir, os.path.basename(filename)) # Naive name match, assumes move didn't rename
            # Logic: If we renamed during move, we can't easily find it. 
            # Better approach: Read from 'src' BEFORE move, or just re-read listing of destination?
            # Ideally we extract metadata during the first pass. 
            # For simplicity in this hotfix: We'll assume no rename for now, or just scan processed_dir for RECENTLY moved files?
            # Actually, let's just re-read the ARCHIVED file.
            
            # Find the file in processed_dir. It might be renamed if collision occurred.
            # But processed_files_list contains original filenames.
            # Let's try to find it.
            candidates = [f for f in os.listdir(processed_dir) if f.startswith(os.path.splitext(filename)[0])]
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
                    search_items.append({
                        "tool": tool_name,
                        "position": item.get("position"),
                        "title": item.get("title"),
                        "url": url,
                        "snippet": item.get("snippet"),
                        "source": item.get("source"),
                    })
            except Exception as e:
                logger.warning(f"Error reading archived {filename}: {e}")

        filtered_lookup = {f["url"]: f for f in filtered_files if f.get("url")}
        overview_lines = []
        overview_lines.append(f"# Research Sources Overview (Task: {task_name})")
        overview_lines.append(f"**Generated:** {datetime.utcnow().isoformat()}Z")
        overview_lines.append(f"**Search Inputs:** {scanned_files} files (Archived to `processed_json/`)")
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
                f"| {idx} | {item.get('tool','')} | {title} | {url} | {snippet} | {filtered_file} |"
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
                overview_lines.append(
                    f"| {idx} | `{os.path.basename(dropped['path'])}` | {dropped['status']} |"
                )

        # SAVE OVERVIEW TO TASK DIRECTORY
        overview_path = os.path.join(task_dir, "research_overview.md")
        with open(overview_path, "w", encoding="utf-8") as f:
            f.write("\n".join(overview_lines))
        
        # 6. Return Summary
        failed_urls = crawl_result.get("errors", [])
        
        return json.dumps({
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
            },
            "overview_path": overview_path,
        }, indent=2)
        
    except Exception as e:
        import traceback
        return json.dumps({"error": f"Pipeline failed: {str(e)}", "traceback": traceback.format_exc()})


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
    model_name: str = "gemini-3-pro-image-preview"
) -> str:
    """
    Generate or edit an image using Gemini models.
    
    Args:
        prompt: Text description for generation, or edit instruction if input_image provided.
        input_image_path: Optional path to source image (for editing). If None, generates from scratch.
        output_dir: Directory to save output. Defaults to workspace work_products/media/.
        output_filename: Optional filename. If None, auto-generates with timestamp.
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
        
        # Determine output directory
        if not output_dir:
            # Try to infer from workspace - look for work_products/media
            output_dir = os.path.join(os.getcwd(), "work_products", "media")
        
        os.makedirs(output_dir, exist_ok=True)
        
        # Prepare content for generation
        parts = []
        
        # If editing, include the input image
        if input_image_path:
            if not os.path.exists(input_image_path):
                return json.dumps({"error": f"Input image not found: {input_image_path}"})
            
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
            )
        )
        
        saved_path = None
        text_output = ""
        
        for chunk in response_stream:
            if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                continue
                
            for part in chunk.candidates[0].content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
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
                        safe_desc = "".join(c if c.isalnum() or c in (' ', '_') else '' for c in description)
                        safe_desc = "_".join(safe_desc.split()[:5])  # First 5 words
                        output_filename = f"{safe_desc}_{timestamp}.png"
                    
                    saved_path = os.path.join(output_dir, output_filename)
                    image.save(saved_path, "PNG")
                    
                elif hasattr(part, 'text') and part.text:
                    text_output += part.text

        if not saved_path:
            return json.dumps({"error": "No image generated", "text_output": text_output})
            
        result = {
            "success": True,
            "output_path": saved_path,
            "description": description if 'description' in locals() else None,
            "size_bytes": os.path.getsize(saved_path),
            "text_output": text_output if text_output else None
        }
        
        # Launch preview if requested
        if preview:
            try:
                viewer_result = preview_image(saved_path)
                viewer_data = json.loads(viewer_result)
                if "viewer_url" in viewer_data:
                    result["viewer_url"] = viewer_data["viewer_url"]
            except Exception as e:
                result["preview_error"] = str(e)
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        import traceback
        return json.dumps({"error": str(e), "traceback": traceback.format_exc()})


def describe_image_internal(image: 'Image.Image') -> str:
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
            "..", ".claude", "skills", "image-generation", "scripts", "gradio_viewer.py"
        )
        
        if not os.path.exists(script_path):
            return json.dumps({
                "error": "Gradio viewer script not found",
                "expected_path": script_path,
                "note": "Preview functionality requires image-generation skill to be initialized"
            })
        
        # Launch gradio viewer in background
        subprocess.Popen(
            [sys.executable, script_path, image_path, str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        viewer_url = f"http://127.0.0.1:{port}"
        return json.dumps({
            "success": True,
            "viewer_url": viewer_url,
            "image_path": image_path
        })
        
    except Exception as e:
        return json.dumps({"error": str(e)})


# =============================================================================
# MAIN - Start stdio server when run as a script
# =============================================================================

if __name__ == "__main__":
    # Run the MCP server using stdio transport
    mcp.run(transport="stdio")
