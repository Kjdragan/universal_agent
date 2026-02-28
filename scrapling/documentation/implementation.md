# Scrapling Implementation

## Core Runtime Code

Scraping runtime implementation lives in:

- `src/universal_agent/tools/scrapling_scraper/inbox_processor.py`
- `src/universal_agent/tools/scrapling_scraper/fetcher_strategy.py`
- `src/universal_agent/tools/scrapling_scraper/content_converter.py`

Experiment runner script:

- `scrapling/scripts/run_scrapling_eval.py`

## Flow

1. Load JSON files from `inbox/source_batch` recursively.
2. Parse either:
   - explicit `urls` lists, or
   - recursive URL fields from search payloads.
3. Build `ScrapeRequest` from job `options`.
4. Fetch with selected strategy (`basic`, `dynamic`, `stealthy`, or adaptive escalation).
5. Convert page response to markdown.
6. Write one `.md` per URL under `processed/`.
7. Move JSON job file to `inbox/done` or `inbox/failed`.

## Markdown Cleaning (Current)

Content cleaning is deterministic (no LLM):

- boilerplate/nav/footer regex filtering
- duplicate short-line suppression
- content-likeness heuristics
- optional inclusion of page structure and links

Job options controlling output:

- `clean_markdown` (default `true`)
- `include_structure` (default `false`)
- `include_links` (default `false`)

## Tests

Scrapling-specific tests:

- `tests/test_scrapling_scraper.py`

Run:

```bash
uv run --with pytest python3 -m pytest -q tests/test_scrapling_scraper.py
```
