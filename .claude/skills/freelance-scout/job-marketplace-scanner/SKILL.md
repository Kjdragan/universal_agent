# Job Marketplace Scanner

## Metadata
| Field | Value |
|-------|-------|
| **Name** | job-marketplace-scanner |
| **Version** | 1.0.0 |
| **Tier** | 1 — Autonomous Freelancer Engine |
| **Priority** | P1 (Critical) |
| **Complexity** | High |
| **Agent Level** | Sub-Agent (Freelance Scout) |
| **Dependencies** | web-research, code-scripting |

## Description

Scans freelance marketplace platforms to discover job opportunities matching our capabilities. This is the "eyes" of the autonomous freelancer engine — it monitors multiple platforms continuously, normalizes heterogeneous data into a unified opportunity model, and feeds downstream analysis and bidding pipelines.

The scanner operates across three platform tiers based on API accessibility:

1. **API-Native** (best data quality): Upwork GraphQL API, Freelancer.com REST API
2. **Scraper-Assisted** (good data quality): Apify actors for Fiverr, additional platforms
3. **URL-Constructed** (fallback): Generate search URLs for manual or browser-based access

## Inputs

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| search_queries | dict[str, list[str]] | Yes | Categorized search queries (quick_wins, high_value, growth) |
| platforms | list[str] | No | Which platforms to scan. Default: all configured |
| filters | dict | No | Global filters: posted_within, min_budget, max_budget, etc. |
| page_size | int | No | Results per query per platform. Default: 20 |
| dedup_against | str | No | Path to previous scan JSON for deduplication |

## Outputs

| Output | Type | Description |
|--------|------|-------------|
| scan_results | JSON | Array of Opportunity objects with platform metadata |
| search_urls | list[str] | Constructed search URLs for platforms without API access |
| scan_summary | str | Human-readable summary of results for agent consumption |
| errors | list[str] | Any errors encountered during scanning |

## Instructions

### Step 1: Platform Health Check
Before scanning, verify connectivity to all configured platforms:
```bash
python scripts/scanner_orchestrator.py --mode health
```
Log which platforms are available via API vs. URL-only fallback.

### Step 2: Execute Multi-Platform Scan
Run the orchestrator with configured queries:
```bash
# Full scan (all platforms, all query categories)
python scripts/scanner_orchestrator.py --mode full --output results/

# Quick scan (single platform, quick-win queries only)
python scripts/scanner_orchestrator.py --mode quick --platform upwork
```

### Step 3: Deduplication
The scanner automatically deduplicates within a single run using content fingerprints (SHA-256 hash of platform + ID + title). For cross-run deduplication, pass previous results:
```bash
python scripts/scanner_orchestrator.py --mode full --dedup results/latest_scan.json
```

### Step 4: Output Normalization
All results are normalized to the unified `Opportunity` data model defined in `scripts/models.py`. Every opportunity has:
- Unique fingerprint for dedup
- Normalized budget info (USD, typed as fixed/hourly)
- Normalized client info (rating, spend history, verification)
- Skill tags extracted and cleaned
- Platform-specific URL for direct access

### Search Query Strategy

Queries are organized by barbell strategy position:

**Quick Wins** (high volume, fast execution):
- "data entry automation", "web scraping python"
- "spreadsheet automation", "research report"
- "data cleaning", "content writing blog"

**High Value** (premium, differentiating):
- "AI agent development", "LLM integration python"
- "machine learning pipeline", "data science consulting"
- "API automation workflow", "chatbot development"

**Growth** (building toward future capabilities):
- "full stack python next.js", "cloud automation"
- "AI workflow automation", "multi-agent system"

Queries should be tuned over time based on which searches yield the highest-scoring opportunities.

## Platform-Specific Notes

### Upwork (Primary)
- **Access**: GraphQL API with OAuth 2.0 (preferred) or URL construction (fallback)
- **API Docs**: https://www.upwork.com/developer/documentation/graphql/api/docs/
- **Key Limitation**: GraphQL job search scope may be restricted; some users report needing specific scope approvals. RSS feeds deprecated August 2024.
- **Rate Limit**: ~100 requests/minute
- **Best For**: Largest volume of knowledge-work opportunities

### Freelancer.com (Secondary)
- **Access**: Official REST API with Python SDK
- **API Docs**: https://developers.freelancer.com
- **Python SDK**: `pip install freelancersdk`
- **Key Advantage**: Most API-friendly platform. Full project search with extensive filters.
- **Best For**: Broader range of project types, good international coverage

### Fiverr (Tertiary)
- **Access**: Web scraping via Apify actors (no official buyer-side API)
- **Model Difference**: Seller-posts-gig model vs. buyer-posts-job. "Buyer Briefs" are the equivalent of job postings but access is limited.
- **Apify Actor**: `automation-lab/fiverr-scraper`
- **Best For**: Understanding competitor pricing, identifying gig categories to create

## Quality Criteria

- **Coverage**: Scan must hit at least 2 platforms per run
- **Freshness**: Only return opportunities posted within configured window (default: 7 days)
- **Dedup Rate**: Cross-query dedup should eliminate >30% duplicates on typical runs
- **Error Handling**: Individual query failures must not abort the full scan
- **Latency**: Full scan should complete within 5 minutes for typical query set

## Common Pitfalls

1. **API Token Expiry**: OAuth tokens expire. Build refresh logic or alert when tokens are stale.
2. **Rate Limiting**: Upwork throttles at ~100/min. The adapter has built-in throttling but aggressive concurrent scanning can still hit limits.
3. **Scraper Fragility**: Web scraping adapters break when platforms change HTML structure. Monitor for parsing errors and update selectors.
4. **Query Overlap**: Similar queries return overlapping results. Dedup is critical.
5. **Stale Results**: Some platforms show old listings in search results. Always check posted_at dates.

## Composio Integrations

This skill can be enhanced with Composio connectors for:
- **Slack/Discord**: Push new high-scoring opportunities to a channel
- **Google Sheets**: Append scan results to a tracking spreadsheet
- **Notion**: Create entries in an opportunity pipeline database
- **Email**: Daily digest delivery

## Supporting Scripts

| Script | Purpose |
|--------|---------|
| `scripts/scanner_orchestrator.py` | Main entry point for all scan operations |
| `scripts/platform_adapters.py` | Platform-specific API/scraping adapters |
| `scripts/models.py` | Unified data models (Opportunity, ScanResult, etc.) |
