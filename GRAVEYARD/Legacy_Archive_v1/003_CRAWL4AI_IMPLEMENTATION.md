# Crawl4AI Cloud Implementation & Research Pipeline

## 1. Overview
The current implementation of the `crawl_parallel` tool has migrated to a **Cloud-first architecture** using the Crawl4AI Cloud API. This move significantly improves extraction quality, reduces local resource consumption (avoiding Playwright/headless browser overhead), and enables advanced anti-bot bypass capabilities.

**Key Features:**
*   **Cloud API Execution**: Utilizes `crawl4ai-cloud.com` for high-speed, synchronous web extraction.
*   **Magic Mode**: Leverages advanced anti-bot protection and "Magic" extraction logic to handle complex sites (e.g., Al Jazeera, ISW).
*   **Automated Research Corpus**: A standardized pipeline that turns search results into a clean, metadata-rich corpus.
*   **Rich Metadata Extraction**: Automatically parses article titles, descriptions, and publication dates from URLs or content.
*   **Tiered Processing**: Categorizes sources by size (Batch-Safe vs. Large) to optimize context window usage.

## 2. Architecture

### 2.1 Component Integration
The tool core resides in `src/mcp_server.py`, specifically within the `_crawl_core` internal function, which is exposed via `crawl_parallel` and `finalize_research`.

*   **Primary Mode**: Cloud API (triggered by `CRAWL4AI_API_KEY`).
*   **Fallback Strategy**: The system is designed to allow reverting to local/standalone functionality (Playwright-based) if cloud costs become prohibitive or for offline/local-only environments.

### 2.2 Execution Flow (Cloud Mode)
1.  **Authentication**: Uses the `CRAWL4AI_API_KEY` for secure access to the Cloud API.
2.  **Synchronous Fetch**: Sends requests to the `/query` endpoint.
3.  **Post-Processing**: 
    *   Strips markdown links to minimize token bloat while preserving text.
    *   Removes image markdown and excessive blank lines.
    *   Filters secondary noise (nav, footer, ads) via API parameters.
4.  **Metadata Enrichment**:
    *   Extracts publication dates from URL patterns (e.g., `/2025/12/31/`) or content analysis.
    *   Generates **YAML Frontmatter** for each saved file.

## 3. Automated Research Pipeline (`finalize_research`)
The `finalize_research` tool provides a "one-click" workflow to prepare a research corpus:

1.  **Extraction**: Scans `search_results/` JSON files for URLs using tool-specific parsing (Composio, etc.) or Pydantic fallbacks.
2.  **Crawling**: Executes `crawl_parallel` on all unique URLs.
3.  **Overview Generation**: Creates `research_overview.md`, which includes:
    *   **Corpus Size Breakdown**: Tiers files into **Batch-Safe** (under 2.5k words), **Medium**, and **Large** (over 5k words).
    *   **Cumulative Statistics**: Tracks total word counts to prevent context overflow.
    *   **Source Details**: Lists title, date, and word count for every source.

## 4. Configuration

### 4.1 Environment Variables
*   `CRAWL4AI_API_KEY`: Required for Cloud API access. If absent, the tool defaults to local/minimal mode.
*   `CRAWL4AI_API_URL`: (Optional) Can be used to point to a self-hosted Docker instance of Crawl4AI.

### 4.2 Data Model (Output)
Files are saved to `search_results/` with the following frontmatter:
```yaml
---
title: "Article Title"
source: https://example.com/article
date: 2025-12-31
description: "Brief summary..."
word_count: 1200
---
```

## 5. Standalone Functionality & Cost Management
While the Cloud API is currently prioritized for its superior "Magic Mode" and extraction quality, the project retains the architecture required for **Standalone Operation**. 

**Future Roadmap:**
*   **Cost Monitoring**: If Cloud API usage exceeds budget limits, the toolset can be re-pointed to a local `AsyncWebCrawler` or a self-hosted Docker cluster.
*   **Hybrid Mode**: Potential for using Local mode for simple sites and Cloud API only for "Hard" targets (sites with heavy bot detection).

## 6. Known Behaviors
*   **Cloudflare/Captcha**: While the Cloud API bypasses many blocks, some extreme anti-bot measures (e.g., certain Cloudflare tiers) may still report a block. The system logs these separately.
*   **Order Consistency**: Results are mapped by URL to ensure the research overview remains deterministic regardless of parallel execution speed.
