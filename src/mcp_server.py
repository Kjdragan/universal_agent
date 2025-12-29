from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import sys
import json
import logging
from datetime import datetime
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
from tools.workbench_bridge import WorkbenchBridge
from tools.workbench_bridge import WorkbenchBridge
from composio import Composio

# Memory System Integration
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
    if os.getenv("LOGFIRE_TOKEN"):
        logfire.configure(
            service_name="local-toolkit",
            send_to_logfire="if-token-present",
        )
        logfire.instrument_mcp()
        sys.stderr.write("[Local Toolkit] Logfire instrumentation enabled\\n")
except ImportError:
    pass

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


@mcp.tool()
def read_research_files(file_paths: list[str]) -> str:
    """
    Read multiple research files in a single batch call.
    
    Use this after reviewing research_overview.md to efficiently read 
    the full content of selected source files for quotes and facts.
    
    Args:
        file_paths: List of file paths to read (from research_overview.md listing)
    
    Returns:
        Combined content of all files, separated by clear markers.
        Each file section includes the filename and word count.
    """
    if not file_paths:
        return "Error: No file paths provided"
    
    results = []
    total_words = 0
    success_count = 0
    
    for path in file_paths:
        try:
            path = fix_path_typos(path)
            abs_path = os.path.abspath(path)
            
            if not os.path.exists(abs_path):
                results.append(f"\n{'='*60}\n❌ FILE NOT FOUND: {path}\n{'='*60}\n")
                continue
            
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            word_count = len(content.split())
            total_words += word_count
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
        f"**Total words:** {total_words:,}\n\n"
    )
    
    return header + "\n".join(results)


@mcp.tool()
def write_local_file(path: str, content: str) -> str:
    """
    Write content to a file in the Local Workspace.
    Useful for saving reports, summaries, or code generated by sub-agents.
    """
    try:
        abs_path = os.path.abspath(path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"


@mcp.tool()
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
# MEMORY SYSTEM TOOLS
# =============================================================================

@mcp.tool()
def core_memory_replace(label: str, new_value: str) -> str:
    """
    Overwrite a Core Memory block (e.g. 'human', 'persona').
    Use this to update persistent facts about the user or yourself.
    """
    if not MEMORY_MANAGER:
        return "Error: Memory System not initialized."
    return MEMORY_MANAGER.core_memory_replace(label, new_value)

@mcp.tool()
def core_memory_append(label: str, text_to_append: str) -> str:
    """
    Append text to a Core Memory block.
    Useful for adding a new preference without deleting old ones.
    """
    if not MEMORY_MANAGER:
         return "Error: Memory System not initialized."
    return MEMORY_MANAGER.core_memory_append(label, text_to_append)

@mcp.tool()
def archival_memory_insert(content: str, tags: str = "") -> str:
    """
    Save a fact, document, or event to long-term archival memory.
    Use for things that don't need to be in active context.
    """
    if not MEMORY_MANAGER:
         return "Error: Memory System not initialized."
    return MEMORY_MANAGER.archival_memory_insert(content, tags)

@mcp.tool()
def archival_memory_search(query: str, limit: int = 5) -> str:
    """
    Search long-term archival memory using semantic search.
    """
    if not MEMORY_MANAGER:
         return "Error: Memory System not initialized."
    return MEMORY_MANAGER.archival_memory_search(query, limit)

@mcp.tool()
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
    url: str
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
        if self.results:
            items.extend(self.results)
        if self.articles:
            items.extend(self.articles)
        
        # Deduplicate while preserving order? No, set is simpler.
        # But we want to preserve order of relevance usually.
        # Use simple list comprehension
        urls = [item.url for item in items if item.url and item.url.startswith("http")]
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
                        
                        # Get content (Cloud API returns 'content' key)
                        raw_content = data.get("content", "")
                        content = raw_content
                        
                        # Post-process: Strip markdown links, keep just the text
                        # [link text](url) -> link text
                        import re
                        content = re.sub(r'\\[([^\\]]+)\\]\\([^)]+\\)', r'\\1', content)
                        # Also remove bare URLs that start lines
                        content = re.sub(r'^https?://[^\\s]+\\s*$', '', content, flags=re.MULTILINE)
                        # Remove image markdown ![alt](url)
                        content = re.sub(r'!\\[[^\\]]*\\]\\([^)]+\\)', '', content)
                        # Clean up excessive blank lines
                        content = re.sub(r'\\n{3,}', '\\n\\n', content)
                        
                        return {
                            "url": url,
                            "success": True,
                            "content": content,
                            "raw_content": raw_content,  # Keep for date extraction
                            "metadata": data.get("metadata", {})
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
                        date_match = re.search(r'/(\\d{4})/(\\d{1,2})/(\\d{1,2})/', url)
                        if date_match:
                            article_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
                        else:
                            date_match = re.search(r'/(\\d{4})-(\\d{1,2})-(\\d{1,2})/', url)
                            if date_match:
                                article_date = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
                        
                        # Fallback: extract from raw content (before link stripping)
                        if not article_date:
                            raw_content = result.get("raw_content", content)
                            # Search full content for date patterns (some sites have tons of nav bloat)
                            # Pattern: "Month Day, Year" (e.g., December 28, 2025)
                            months = 'January|February|March|April|May|June|July|August|September|October|November|December'
                            match = re.search(rf'({months})\\s+(\\d{{1,2}}),?\\s+(\\d{{4}})', raw_content, re.I)
                            if match:
                                month_map = {'january':'01','february':'02','march':'03','april':'04','may':'05','june':'06',
                                             'july':'07','august':'08','september':'09','october':'10','november':'11','december':'12'}
                                article_date = f"{match.group(3)}-{month_map[match.group(1).lower()]}-{match.group(2).zfill(2)}"
                            else:
                                # Pattern: "Day Mon Year" (e.g., 29 Dec 2025)
                                match = re.search(r'(\\d{1,2})\\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+(\\d{4})', raw_content, re.I)
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
        
        # Generate research_overview.md - combined context-efficient file
        if results_summary["saved_files"]:
            try:
                total_words = 0
                overview_sections = []
                
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
                    
                    # Create 1000-word excerpt
                    words = body.split()
                    excerpt = " ".join(words[:1000])
                    if len(words) > 1000:
                        excerpt += "..."
                    
                    # Build section
                    title = metadata.get("title", "Untitled")
                    date = metadata.get("date", "unknown")
                    description = metadata.get("description", "")
                    
                    section = f"""## Source {i}
**File:** {file_info['file']} ({word_count:,} words full)
**Title:** {title}
**Date:** {date}
**URL:** {file_info['url']}

### Excerpt (first 1000 words):
{excerpt}

---
"""
                    overview_sections.append(section)
                
                # Assemble overview file
                overview_header = f"""# Research Sources Overview
**Generated:** {datetime.utcnow().isoformat()}Z
**Total Sources:** {len(results_summary['saved_files'])} articles
**Total Words Available:** {total_words:,} words across all sources

> **Usage Guide:** This overview contains metadata and 1000-word excerpts for each source.
> For quotes, specific facts, and deeper analysis, read the full source files listed below.

---

"""
                overview_content = overview_header + "\n".join(overview_sections)
                overview_path = os.path.join(search_results_dir, "research_overview.md")
                
                with open(overview_path, "w", encoding="utf-8") as f:
                    f.write(overview_content)
                
                results_summary["overview_file"] = overview_path
                results_summary["total_words_available"] = total_words
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
async def finalize_research_corpus(session_dir: str) -> str:
    """
    AUTOMATED RESEARCH PIPELINE:
    1. Scans 'search_results/' for JSON search outputs.
    2. Extracts all URLs using Pydantic validation (safe, typed).
    3. Executes parallel crawl on all extracted URLs.
    4. Generates 'research_overview.md'.
    
    Use this as the FIRST step in report generation to turn search results into a ready-to-read corpus.
    
    Args:
        session_dir: Path to the current session workspace.
        
    Returns:
        Summary of operation: URLs found, crawl success/fail, and path to overview.
    """
    try:
        search_results_dir = os.path.join(session_dir, "search_results")
        if not os.path.exists(search_results_dir):
            return json.dumps({"error": f"Search results directory not found: {search_results_dir}"})
            
        all_urls = set()
        scanned_files = 0
        
        # 1. Scan and Extract
        for filename in os.listdir(search_results_dir):
            if filename.endswith(".json"):
                path = os.path.join(search_results_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # Validate and parse with Pydantic
                    try:
                        # Attempt to parse as SearchResultFile (expecting dict inputs)
                        if isinstance(data, dict):
                            model = SearchResultFile.model_validate(data)
                            urls = model.all_urls
                            if urls:
                                all_urls.update(urls)
                                scanned_files += 1
                                logger.info(f"Extracted {len(urls)} URLs from {filename}")
                        else:
                            # If it's a list (rare for Search files, but possible for generic tools)
                            logger.warning(f"Skipping {filename}: Root element is not a dict")
                                
                    except ValidationError as ve:
                        logger.warning(f"Pydantic validation failed for {filename}: {ve}")
                        # Fallback? No, we want strictness now.
                        pass
                        
                except Exception as e:
                    logger.warning(f"Error reading {filename}: {e}")
        
        if not all_urls:
            return json.dumps({
                "status": "No URLs found", 
                "scanned_files": scanned_files,
                "note": "Ensure search results are regular JSON files with 'results' or 'articles' lists."
            })
            
        url_list = list(all_urls)
        
        # 2. Execute Crawl
        sys.stderr.write(f"[finalize] Found {len(url_list)} unique URLs from {scanned_files} files. Starting crawl...\\n")
        
        # 3. Call Core Logic
        crawl_result_json = await _crawl_core(url_list, session_dir)
        crawl_result = json.loads(crawl_result_json)
        
        # 4. Return Summary
        return json.dumps({
            "status": "Research Corpus Finalized",
            "extracted_urls": len(url_list),
            "sources_scanned": scanned_files,
            "crawl_summary": crawl_result
        }, indent=2)
        
    except Exception as e:
        import traceback
        return json.dumps({"error": f"Pipeline failed: {str(e)}", "traceback": traceback.format_exc()})


# =============================================================================
# IMAGE GENERATION TOOLS
# =============================================================================

@mcp.tool()
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
