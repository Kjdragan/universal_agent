# Crawl4AI Parallel Scraper Implementation

## 1. Overview
The `crawl_parallel` tool addresses the need for high-speed, noise-free web extraction. It leverages the `crawl4ai` library to scrape multiple URLs concurrently, significantly reducing the latency associated with sequential `webReader` calls.

**Key Features:**
*   **Parallel Execution**: Scraps batches of URLs (recommended size: 10) simultaneously using a browser context pool.
*   **Smart Cleaning**: Automatically removes ads, navigation, footers, citations, and donation requests while preserving article content.
*   **Stealth Mode**: Uses anti-bot detection techniques to access protected content.
*   **Local Storage**: Saves clean markdown files directly to the session's `search_results` directory.

## 2. Architecture

### 2.1 Component Integration
The tool is implemented in `src/mcp_server.py` as a function of the Local Intelligence Toolkit MCP server.

*   **Function**: `crawl_parallel(urls: list[str], session_dir: str)`
*   **Dependencies**: `crawl4ai`, `Playwright`, `asyncio`.

### 2.2 Execution Flow
1.  **Browser Initialization**: Configures a headless Chromium browser with stealth settings.
2.  **Context Operations**: Uses `crawler.arun_many()` to spawn concurrent tabs/contexts for each URL.
3.  **Extraction**: Applies `DefaultMarkdownGenerator` with a `PruningContentFilter` to strip low-value matching blocks.
4.  **Sanitization**: Applies strict CSS exclusion selectors (see Configuration) to remove specific noise elements.
5.  **Output Generation**: Hashes the requested URL to create a unique filename and saves the result as `.md`.

## 3. Configuration & Filtering

### 3.1 Content Filtering Rules
We employ a "surgical exclusion" strategy to clean reports (e.g., ISW, Al Jazeera) without post-processing scripts.

**Excluded CSS Selectors:**
*   **Citations/Footnotes**: `.references`, `.footnotes`, `.citation`, `.bibliography`, `.ref-list`, `.endnotes`
*   **Site-Specific Noise**: `.field-name-field-footnotes` (Drupal/ISW), `.cookie-banner`, `#cookie-consent`
*   **Solicitations**: `.donation`, `.donate`, `.subscription`, `.subscribe`, `.newsletter`, `.signup`, `.promo`

**Tag Handling:**
*   **Excluded**: `nav`, `footer`, `header`, `aside`, `script`, `style`
*   **Preserved**: `iframe` (Retained to keep embedded social media, maps, and video content).

### 3.2 Browser Config
```python
BrowserConfig(
    headless=True,
    enable_stealth=True,  # Bypasses basic bot detection
    browser_type="chromium"
)
```

## 4. Benchmarks & Validation
**Test Run (Dec 2025):**
*   **Target**: 10 Real-world URLs (ISW, Al Jazeera, Russia Matters, Consilium EU).
*   **Total Duration**: ~10 seconds.
*   **Success Rate**: 100% (10/10).
*   **Output Quality**:
    *   ISW Footer Citations: Removed (File size reduced ~70%).
    *   Inline Citations (`[1]`): Stripped from text.
    *   Donation Banners: Filtered (where standard classes are used).

## 5. Known Behaviors
*   **Content/URL Mapping**: The system strictly maps results by `res.url` to prevent ordering race conditions (Fixed in v1.1).
*   **Hard-coded Donations**: Some donation requests that are part of the main article text (not distinct banner elements) may remain.
*   **Timeouts**: Aggressive bot protection may occasionally cause timeouts on specific domains (e.g., ISW), but the parallel nature ensures other URLs in the batch succeed.
