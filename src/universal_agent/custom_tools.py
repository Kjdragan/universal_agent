"""
Composio Custom Tools - Local toolkit functionality registered as Composio custom tools.

These tools are registered with the Composio client so they appear through the Tool Router MCP,
enabling the COMPOSIO_SEARCH_TOOLS planner to include them in execution plans.
"""

import os
import json
from datetime import datetime
from pydantic import BaseModel, Field


# =============================================================================
# INPUT SCHEMAS
# =============================================================================


class CrawlParallelInput(BaseModel):
    """Input for parallel web crawling."""
    urls: list[str] = Field(
        ..., 
        description="List of URLs to scrape (recommended batch size: 5-10)"
    )
    session_dir: str = Field(
        ..., 
        description="Absolute path to the current session workspace (e.g., AGENT_RUN_WORKSPACES/session_...)"
    )


class WriteFileInput(BaseModel):
    """Input for writing a local file."""
    file_path: str = Field(
        ..., 
        description="Absolute path to the file to write"
    )
    content: str = Field(
        ..., 
        description="Content to write to the file"
    )


class ReadFileInput(BaseModel):
    """Input for reading a local file."""
    path: str = Field(
        ..., 
        description="Absolute path to the file to read"
    )


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


def register_custom_tools(composio_client):
    """
    Register all custom tools with the given Composio client.
    Call this after Composio() is initialized.
    """
    
    @composio_client.tools.custom_tool
    async def crawl_parallel(request: CrawlParallelInput) -> dict:
        """
        High-speed parallel web scraping using crawl4ai.
        Scrapes multiple URLs concurrently, extracts clean markdown (removing ads/nav),
        and saves results to 'search_results' directory in the session workspace.
        
        Returns JSON summary with success/fail counts and saved file paths.
        """
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
            from crawl4ai.content_filter_strategy import PruningContentFilter
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        except ImportError:
            return {
                "error": "crawl4ai not installed. Run: uv pip install crawl4ai && crawl4ai-setup"
            }
        
        import hashlib
        
        urls = request.urls
        session_dir = request.session_dir
        
        # 1. Configure Browser (Speed & Evasion)
        browser_config = BrowserConfig(
            headless=True,
            enable_stealth=True,
            browser_type="chromium",
        )
        
        # 2. Configure Extraction (Noise Reduction)
        prune_filter = PruningContentFilter(
            threshold=0.5, threshold_type="fixed", min_word_threshold=10
        )
        md_generator = DefaultMarkdownGenerator(content_filter=prune_filter)
        
        run_config = CrawlerRunConfig(
            markdown_generator=md_generator,
            excluded_tags=["nav", "footer", "header", "aside", "script", "style"],
            excluded_selector=".references, .footnotes, .citation, .cookie-banner, #cookie-consent, .donation, .newsletter, .signup, .promo",
            cache_mode=CacheMode.BYPASS,
        )
        
        results_summary = {
            "total": len(urls),
            "successful": 0,
            "failed": 0,
            "saved_files": [],
            "errors": [],
        }
        
        search_results_dir = os.path.join(session_dir, "search_results")
        os.makedirs(search_results_dir, exist_ok=True)
        
        # 3. Execute Parallel Crawl
        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                results = await crawler.arun_many(urls=urls, config=run_config)
                
                for res in results:
                    original_url = res.url
                    if res.success:
                        url_hash = hashlib.md5(original_url.encode()).hexdigest()[:12]
                        filename = f"crawl_{url_hash}.md"
                        filepath = os.path.join(search_results_dir, filename)
                        
                        content = res.markdown.fit_markdown or res.markdown.raw_markdown
                        final_content = f"# Source: {original_url}\n# Date: {datetime.utcnow().isoformat()}\n\n{content}"
                        
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(final_content)
                        
                        results_summary["successful"] += 1
                        results_summary["saved_files"].append({
                            "url": original_url,
                            "file": filename,
                            "path": filepath,
                        })
                    else:
                        results_summary["failed"] += 1
                        results_summary["errors"].append({
                            "url": original_url, 
                            "error": res.error_message
                        })
        
        except Exception as e:
            return {"error": f"Crawl execution failed: {str(e)}"}
        
        return results_summary
    
    
    @composio_client.tools.custom_tool
    def write_local_file(request: WriteFileInput) -> str:
        """
        Write content to a file in the local workspace.
        Useful for saving reports, summaries, or code generated by the agent.
        Creates parent directories if they don't exist.
        """
        try:
            abs_path = os.path.abspath(request.file_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(request.content)
            return f"Successfully wrote {len(request.content)} chars to {request.file_path}"
        except Exception as e:
            return f"Error writing file: {str(e)}"
    
    
    @composio_client.tools.custom_tool
    def read_local_file(request: ReadFileInput) -> str:
        """
        Read content from a file in the local workspace.
        """
        try:
            abs_path = os.path.abspath(request.path)
            if not os.path.exists(abs_path):
                return f"Error: File not found at {request.path}"
            
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            return content
        except Exception as e:
            return f"Error reading file: {str(e)}"
    
    
    print("✅ Registered custom tools: crawl_parallel, write_local_file, read_local_file")
