---
name: gemini-url-context-scraper
description: |
  Fast URL/PDF/image content extraction using Gemini "URL Context" (built-in web/PDF reader) via google-genai.
  Use when the user wants to: scrape a URL, read/summarize a PDF, extract structured facts from public web content, or create an interim “scraped context” work product for downstream tasks.
  Writes interim outputs to CURRENT_SESSION_WORKSPACE/work_products by default, and can persist outputs under UA_ARTIFACTS_DIR on request. Produces runnable PEP 723 + `uv run` scripts with dotenv auto-loading (no hardcoded secrets).
---

# Gemini URL Context Scraper

Use Gemini's `url_context` tool to fetch and interpret public URLs (HTML, PDFs, images) without external scrapers.

## Compatibility Note (IMPORTANT)

Depending on which Gemini API surface and model are available at runtime, the official `url_context`/browse tool may be rejected (e.g., “Browse tool is not supported”).

This skill's bundled script is designed to be resilient:

- First it **tries URL Context**.
- If rejected, it **falls back** to downloading the URL content and attaching it directly to the prompt:
  - PDFs/images: attach bytes (so PDFs still work well).
  - HTML/text: attach truncated text.

## Output Policy (MANDATORY)

1. **Interim (default)**: if this is part of a larger chain (e.g. “use this PDF as context to do X”), write outputs to:

- `CURRENT_SESSION_WORKSPACE/work_products/gemini-url-context/<slug>__<HHMMSS>/`

1. **Persistent (only if user asks to save)**: write outputs to:

- `UA_ARTIFACTS_DIR/gemini-url-context/<YYYY-MM-DD>/<slug>__<HHMMSS>/`

1. **Secrets**:

- Never hardcode API keys into scripts or artifacts.
- Scripts must auto-load `.env` via `python-dotenv` and read keys from env vars only.

## Primary Tooling

- Script runner: `uv run` + PEP 723 inline dependencies.
- Implementation script (bundled with this skill):
- `.claude/skills/gemini-url-context-scraper/scripts/gemini_url_context_scrape.py`

## Workflow

1. Confirm inputs

- URL(s): must be publicly accessible (no auth headers/cookies).
- Desired output:
  - `summary` (default) vs `extract` (ask for structured extraction)
  - format: Markdown (default) or JSON-ish Markdown
- Persistence:
  - If user says “save”, use `--persist` (writes under `UA_ARTIFACTS_DIR`).
  - Otherwise, write interim work product under session `work_products/`.

1. Run the scraper script

Interim work product (default):

```bash
uv run .claude/skills/gemini-url-context-scraper/scripts/gemini_url_context_scrape.py \
  --url "https://example.com" \
  --question "Summarize the key points." \
  --mode summary
```

Persistent artifact (only if asked):

```bash
uv run .claude/skills/gemini-url-context-scraper/scripts/gemini_url_context_scrape.py \
  --url "https://arxiv.org/pdf/1706.03762.pdf" \
  --question "Extract the core claims, key equations, and limitations. Use markdown." \
  --mode extract \
  --persist
```

1. Deliverables

- Always produce:
  - `answer.md`
  - `manifest.json` (includes inputs + output map + retention)
- If `--persist` was used, also write:
  - `README.md` (how to rerun)

## Notes / Guardrails

- Prefer models like `gemini-2.5-flash` by default (override with `--model` if needed).
- Do not install dependencies into the repo environment (`pip install`, `uv pip install`, `uv add`). If something is missing, fix PEP 723 deps and rerun with `uv run`.
- If `CURRENT_SESSION_WORKSPACE` is missing and `--persist` is not set, STOP and ask the user to run via UA (session workspace required) or use `--persist`.
