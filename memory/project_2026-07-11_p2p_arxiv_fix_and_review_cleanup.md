# 2026-07-11 — paper_to_podcast arXiv-429 durable fix + review cleanup tier (A1–A3) + test-email isolation

Session: Fable, executing `~/handoffs/2026-07-11_review_cleanup_and_paper_to_podcast.md`
(follow-up to the 16-PR ultrareview remediation of 2026-07-10 — do NOT redo those).
Seven PRs shipped, all auto-merged green and deployed.

## Workstream B — paper_to_podcast 429 fix (the real prod bug)

Root cause (confirmed): arXiv throttles the VPS IP **server-side**; client-side
pacing in the third-party arxiv-mcp-server cannot prevent it, and a hand-rolled
client against the same public API would hit the identical 429. Fresh evidence
during this session: the 2026-07-11 02:00 run died differently — the
arxiv-mcp-server **stdio subprocess crashed** (`MCP error -32000: Connection
closed`) 63s in, no retry, no email. Fix = change the ACCESS PATTERN:

- **#1360** `services/arxiv_local_index.py` — local SQLite **FTS5 metadata
  index** harvested via arXiv's sanctioned OAI-PMH bulk interface (sets
  cs,stat,eess). `search` = pure local read (bm25 + recency cutoff), zero live
  calls; `cache-fallback` = deterministic topic-overlap ranking over the
  163-paper full-text cache (`~/.arxiv-mcp-server/papers/`). Daily systemd
  timer `universal-agent-arxiv-index-harvest.timer` (04:40 CT, `--days 3`,
  no secrets) + installer wired into `remote_deploy.sh`.
- **#1362** SKILL.md + cron-prompt wiring: three-tier discovery (local index →
  ONE live search_papers → cache-fallback), MCP-transport death (`-32000`) is
  now an explicit retry-once-then-fall-back trigger, per-paper failures skip +
  top-up, FAILURE.txt fail-loud contract unchanged.
- One-time 12-month backfill run on the VPS: **262,658 papers**
  (`~/.arxiv-local-index/arxiv_index.db`, ~500 MB), harvest_state current.
  Search smoke-test on prod returned on-topic papers ranked first.
- Verification: manual cron trigger. First attempt (07:47Z) was killed ~2 min
  in by an unrelated deploy restart (07:49Z) — the guard correctly flagged it;
  NOT a defect in the new path. Re-triggered 08:45Z:
  **VERIFIED** — full artifact set landed in the real cron context:
  `podcast_audio.m4a` **41.4 MB**, `report.html` 20 KB (published to
  scratch/paper-to-podcast), `manifest.json`, `papers_metadata.json`
  (5 on-topic papers), `quiz.json`, `flashcards.json`. The manifest itself
  records: "5 papers sourced via the local arXiv metadata index
  (arxiv_local_index search) + arxiv-mcp-server download_paper. Zero
  rate-limit / 429 failures; no offline cache-fallback needed."

Knobs: `UA_ARXIV_INDEX_DB` (db path), timer `--days N`, backfill
`--backfill-months N`. Docs: cron_and_scheduling.md § "Local arXiv metadata
index", Platform Status Registry §4a.

## Workstream A — review cleanup tier (drift-risk consolidation, not bugs)

- **A1 JSON parsers → `utils/json_utils.extract_json_payload`**: #1363
  (mission_control_tier1 + chief_of_staff, byte-identical pair), #1365
  (claude_code_intel `_parse_json_object` + cron_artifact_notifier
  `_parse_llm_json`), #1367 (the `_json_loads_obj` cluster — actually **4
  copies**, consolidated onto a NEW strict `utils/json_utils.json_loads_obj`,
  deliberately NOT the LLM-repair parser: those sites parse DB/Task-Hub-stored
  JSON where {}-on-corruption beats creative repair).
  **llm_classifier._parse_json_response deliberately NOT migrated** — its
  raw_decode scan beats the canonical parser on stray-brace-in-prose input
  (canonical silently returns a corrupted dict; verified empirically) and 9
  tests + 8 external consumers pin its strict JSONDecodeError contract. A
  `# NOTE(json-consolidation, 2026-07-11)` above it records this.
- **A2 `_slugify` → `wiki/core._slugify`**: #1370. Five copies consolidated;
  truncation bounds preserved (40 directed/tutorial, 48 dispatch CLI). The
  directed_demo_builds ↔ tutorial_demo_finalize pair moved TOGETHER —
  `vp/worker_loop.py`'s legacy fallback recomputes a directed dir name with
  the proactive-lane function, so byte-identity is load-bearing (now enforced
  by one shared impl + tests). Out of scope: memory/orchestrator (64-char),
  `_slugify_interview_key`, `_slugify_anchor`.
- **A3 Anthropic-client block → `utils/anthropic_client.py`**: #1371. Two
  tiers: `call_llm_structured` (forced tool_use + retry + GLM-5.2
  thinking-disable; csi_url_judge @1000 tokens, csi_intelligence_pass @4096)
  and `call_llm_text` (single-shot; claude_code_intel @600, wiki/llm @2048 via
  a SemanticExtractionError-translating boundary). Recon found a 4th copy
  (wiki/llm) + two `_has_llm_key` twins — all consolidated. Public names kept
  as shims (tests monkeypatch them). First-ever direct unit coverage for the
  block (13 tests). The ~15 AsyncAnthropic sites are a different lineage —
  untouched.

## Test-email leak (operator-reported mid-session)

Five real `[ERROR]`/`[WARNING]` emails hit Kevin's Gmail at 06:59–07:00Z from
Simone's live AgentMail inbox — bodies were TEST FIXTURES (`unit_test_raiser`,
"boom from a background coroutine"). A desktop test run (dev shells carry real
Infisical secrets) drove the real notification pipeline. Prod verified healthy.
Fix **#1368**: `_isolate_outbound_channels` session-autouse conftest fixture
(scrubs `AGENTMAIL_API_KEY` + `TELEGRAM_BOT_TOKEN`, forces
`UA_AGENTMAIL_GMAIL_FALLBACK=0`, `UA_INFISICAL_ENABLED=0` so secrets can't be
re-fetched mid-test) — same systemic-backstop pattern as `_isolate_scratch_root`.
Its strict assertions immediately caught a second latent leak: the
`infisical_loader.inject()` tests wrote fake keys into os.environ with no
teardown (fixed in the same PR). CI runs green with no secrets, so the scrub
can't break gated tests.

## Gotchas recorded for future sessions

- The cron service (`run_job_now`, `/api/v1/cron/jobs`) lives on the
  **autonomous worker, port 8092** — the gateway on 8002 answers "Chron
  service not available".
- Manual verification runs get killed by unrelated deploy restarts (dark
  factory merges continuously) — check the deploy queue before triggering a
  long-running cron by hand, and expect to re-trigger.
- `.agents/skills/paper-to-podcast-tf/` is a stale never-discovered copy;
  `.claude/skills/` is canonical (skills_system doc gotcha) — do not "sync" it.
- CI `check_test_date_literals.py` blocks new quoted YYYY-MM-DD in tests/ —
  use relative dates or `# date-pinned-ok`.
