# Google Trends → Google Sheets — but as a Claude Agent SDK process

**The seed.** An infographic of an n8n automation: every hour, read keywords from a
Google Sheet → hit Google Trends → convert the XML to JSON → filter by traffic → scrape
the linked pages with jina.ai → append rows to a Google Sheet. Classic no-code glue.

**The steer.** *Don't use n8n. Build it as a Claude Agent SDK process.*

## What got built

One Python program (`agent.py`, ~200 lines) where **Claude is the workflow engine**.
The n8n graph's nodes become four in-process tools Claude calls in order:

1. `get_pipeline_config` — geo, traffic floor, max results, optional keywords
2. `fetch_google_trends` — the **live** Google Trends RSS feed (real network)
3. `scrape_news` — optional jina.ai page-read for extra context
4. `append_trend_rows` — writes to a **Google Sheet** (with creds) or a local `.xlsx`/`.csv`

Between fetching and saving, Claude does the thing n8n needs a bolted-on LLM node for:
it writes a **one-sentence, plain-language summary** of *why* each term is trending,
grounded in the top headline. The deterministic parts (fetch, parse, filter, persist)
stay in code; the judgment stays with the model.

## Why it's interesting

| n8n | Claude Agent SDK process |
|---|---|
| 11 visual nodes wired by hand | 1 program: 4 tools + a goal |
| Needs the n8n server running | Runs anywhere `python` + `claude` CLI run |
| LLM is one more node you configure | Claude *is* the orchestrator |
| Per-item "loop + map" plumbing | Claude iterates natively |
| Fixed transforms | Reasons over each item, degrades gracefully |

## Resilience built in

- The **live RSS feed** is the only reliable no-key Trends surface (pytrends is archived
  and broken; the old daily RSS path 404s) — verified live on build day.
- **No Google creds?** It writes a local `output/trends.xlsx` + `.csv` with the same
  columns, so it runs in CI without secrets. Drop in a service-account JSON + a sheet id
  to write to the real Sheet.
- **Agent SDK endpoint down?** It prints an honest `degraded_deterministic` banner and
  still produces the artifact from real Trends data — an aborted run is the worst outcome.

## Run it

```
uv run python agent.py        # one pipeline pass -> sink
uv run python test_pipeline.py # offline self-check (parse / filter / sink)
```

Schedule it (the n8n "hourly trigger") with cron/systemd — it's just a program.
