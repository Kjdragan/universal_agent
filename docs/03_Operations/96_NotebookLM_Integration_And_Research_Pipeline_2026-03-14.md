# NotebookLM Integration and Research Pipeline Source of Truth (2026-03-14)

> Canonical reference for NotebookLM integration in Universal Agent — capabilities, architecture, delegation model, configuration, and lessons learned.

## Overview

**NotebookLM** is Google's AI-powered research tool that can discover web sources, ingest them into structured notebooks, and generate rich artifacts from the indexed content. UA integrates NotebookLM via the open-source **notebooklm-mcp-cli** MCP server, enabling Simone to autonomously execute full research-to-artifact pipelines.

### What NotebookLM Gives UA

| Capability | Description |
|---|---|
| **Web Research** | Built-in source discovery from the web (fast: ~30s/10 sources, deep: ~5min/40 sources) |
| **Source Ingestion** | URLs, YouTube videos, PDFs, Google Drive docs, pasted text |
| **Briefing Docs** | AI-generated structured reports from imported sources |
| **Audio Overviews** | Podcast-style deep dives with AI hosts discussing the source material |
| **Infographics** | Visual summaries in landscape/portrait/square orientations |
| **Slide Decks** | Presentation-ready PDF/PPTX from source material |
| **Quizzes & Flashcards** | Study materials in JSON/Markdown/HTML formats |
| **Mind Maps** | Visual relationship maps of source concepts |
| **Data Tables** | Structured CSV extractions from source material |
| **Video Overviews** | Animated explainer videos with multiple visual styles |
| **Source Querying** | Ask questions about ingested sources with citation-backed answers |

## Architecture

### Component Map

```
User Request
    │
    ▼
┌─────────────────┐
│  Simone (Main)   │  ── Detects NLM intent via hooks.py
│  Universal Agent │     pattern matching
└────────┬────────┘
         │  Task delegation
         ▼
┌─────────────────────────┐
│  notebooklm-operator     │  ── Claude sub-agent (model: opus)
│  .claude/agents/         │     Has MCP tool access
└────────┬────────────────┘
         │  MCP tool calls
         ▼
┌─────────────────────────┐
│  notebooklm-mcp-cli      │  ── MCP Server (PyPI package)
│  (MCP Server)            │     Translates to batchexecute RPCs
└────────┬────────────────┘
         │  HTTP/batchexecute
         ▼
┌─────────────────────────┐
│  NotebookLM API          │  ── Google's internal API
│  notebooklm.google.com   │     (undocumented, cookie-based auth)
└─────────────────────────┘
```

### Key Files

| File | Purpose |
|---|---|
| `.claude/agents/notebooklm-operator.md` | Sub-agent definition: tools, auth policy, happy path, guardrails |
| `.claude/skills/notebooklm-orchestration/SKILL.md` | Primary agent skill: routing guidance, pipeline steps, common mistakes |
| `src/universal_agent/hooks.py` | NLM intent detection patterns for automatic routing |
| `src/universal_agent/main.py` | Circuit breaker whitelist for polling tools (lines 379-390) |

### Delegation Model

Simone **never calls NLM MCP tools directly**. All NotebookLM work is delegated to the `notebooklm-operator` sub-agent, which has exclusive access to the `mcp__notebooklm-mcp__*` tool namespace. This separation ensures:

1. **Auth isolation** — The sub-agent handles the full auth lifecycle (refresh → cookie injection → retry)
2. **Circuit breaker safety** — Polling loops are contained within the sub-agent
3. **Output contract** — Sub-agent returns structured results for Simone to report to the user

## Authentication

### Auth Flow (MCP-First)

```
1. refresh_auth()          ← Fast path: reload tokens from disk cache
   └─ success? → proceed
   └─ fail? ↓

2. Read $NOTEBOOKLM_AUTH_COOKIE_HEADER env var
   └─ save_auth_tokens(cookies=<value>)
   └─ retry refresh_auth

3. If all fail → report "auth expired" to user
```

### Token Management

- Cookies are stored in `~/.notebooklm-mcp-cli/profiles/<profile>/cookies.json`
- Cookies last **weeks** — they're Google session cookies, not short-lived OAuth tokens
- The MCP server auto-refreshes CSRF tokens on expiry
- On VPS, cookies are provisioned via Infisical secret `NOTEBOOKLM_AUTH_COOKIE_HEADER`

> [!WARNING]
> **Never run `nlm login` on VPS** — there's no browser. Use `nlm login --manual` or provision cookies via Infisical.

## Research Pipeline

### End-to-End Flow

```
1. notebook_create(title="...")           → notebook_id
2. research_start(mode="fast", query=...) → task_id
3. [poll] research_status + sleep(15)     → wait for completion
4. research_import(task_id=...)           → sources imported
5. studio_create(type="report", ...)      → artifact generation starts
6. [poll] studio_status + sleep(15)       → wait for all artifacts
7. download_artifact(type=..., path=...)  → files saved to disk
```

### Research Modes

| Mode | Duration | Sources | When to Use |
|---|---|---|---|
| `fast` | ~30 seconds | ~10 | **Default.** Standard research requests |
| `deep` | ~5 minutes | ~40 | Only when user explicitly says "comprehensive", "thorough", "exhaustive", "in-depth", "deep research", or "find everything" |

> [!IMPORTANT]
> Deep mode is **unreliable** — it can hang and return 0 sources. Always default to fast unless explicitly requested.

### Artifact Types and Timings

| Artifact | Generation Time | Output Format | Key Parameters |
|---|---|---|---|
| **Report** (Briefing Doc) | ~30s | Markdown | `report_format`: Briefing Doc, Study Guide, Blog Post |
| **Audio Overview** | 3-5 min | MP3/MP4 | `audio_format`: deep_dive, brief, critique, debate |
| **Infographic** | 1-2 min | PNG | `orientation`: landscape, portrait, square |
| **Slide Deck** | 1-2 min | PDF/PPTX | `slide_format`: detailed_deck, presenter_slides |
| **Quiz** | ~30s | JSON/MD/HTML | `question_count`, `difficulty`: easy/medium/hard |
| **Flashcards** | ~30s | JSON/MD/HTML | `difficulty`: easy/medium/hard |
| **Mind Map** | ~30s | JSON | `title` |
| **Data Table** | ~30s | CSV | `description` (required) |
| **Video** | 3-5 min | MP4 | `video_format`: explainer, brief, cinematic |

## Lessons Learned

### 1. MCP Transport Does Not Honor `max_wait` Blocking

The `research_status` and `studio_status` tools accept `max_wait` and `poll_interval` parameters that are supposed to block the call until the operation completes. **The MCP transport returns immediately regardless**, likely due to transport-level timeouts.

**Impact:** Without mitigation, the sub-agent calls `research_status` every ~4 seconds with identical parameters, triggering the circuit breaker after 8 calls.

**Solution:** Explicit `Bash("sleep 15")` between poll calls. Set `max_wait=0` to avoid confusion about blocking behavior.

### 2. Circuit Breaker Must Whitelist Polling Tools

The UA circuit breaker (`main.py`) trips after 8 consecutive identical tool calls — a safety mechanism against infinite loops. However, polling tools are *supposed* to be called repeatedly with identical params.

**Solution:** A `_POLLING_TOOLS` whitelist gives `research_status` and `studio_status` a higher threshold of 30 consecutive calls (configurable via `UA_CIRCUIT_BREAKER_MAX_CONSECUTIVE_POLLING`). This provides 30 × 15s = **7.5 minutes** of polling headroom.

### 3. Deep Research is Unreliable

Deep research mode (`mode="deep"`) frequently returns 0 sources after 5+ minutes of waiting. The fast mode (`mode="fast"`) reliably returns ~10 sources in ~30 seconds. The quality difference is minimal for most use cases.

**Solution:** Default to `mode="fast"` and only use deep when the user explicitly requests comprehensive research.

### 4. List Parameters Must Be Actual JSON Arrays

The MCP transport requires actual JSON arrays, not stringified versions. This causes silent failures when the sub-agent passes `source_indices: "[0, 1, 2]"` instead of `source_indices: [0, 1, 2]`.

**Solution:** Omit optional list parameters entirely — defaults work correctly (e.g., omitting `source_indices` imports all sources).

### 5. Auth Preflight Scripts Break on VPS

The original auth preflight script (`scripts/notebooklm_auth_preflight.py`) runs `uv run`, which triggers a fresh `.venv` build from the wrong directory, downloads 3GB of deps, and fails because VPS rustc < 1.76.

**Solution:** Use MCP-first auth (`refresh_auth` → cookie injection from env var). Never run preflight scripts on VPS.

### 6. `nlm login` Requires a Browser

The `nlm login` command launches Chrome for Google authentication. This works on desktop machines but **cannot run on headless VPS**.

**Solution:** On VPS, authenticate via cookie injection from Infisical. On local machines, `nlm login` works normally.

### 7. Task IDs Can Change During Deep Research

Deep research can produce a *different* `task_id` in `research_status` responses than the one returned by `research_start`. The `query` parameter in `research_status` helps match the correct task when IDs change.

**Solution:** Pass the `query` parameter to `research_status` as a fallback matching mechanism when the task_id changes during deep research.

### 8. Email Delivery Belongs Outside the NLM Pipeline

The NLM sub-agent should **never** attempt email delivery. In a March 2026 incident, the sub-agent returned artifacts to the primary agent, which then failed to send via AgentMail (SDK not installed, Ops API auth issues) and fell back to Composio `GMAIL_SEND_EMAIL` — sending from Kevin's identity instead of Simone's.

**Solution:** The NLM sub-agent now explicitly returns artifact paths and notebook URL to the primary agent without attempting delivery. Email routing follows the standard identity policy: AgentMail for Simone's work, gws MCP Gmail only on Kevin's explicit request. Composio Gmail tools are deprecated.

### 9. Org-Level API Keys Block Image Generation Silently

The `generate_image` MCP tool used `GEMINI_API_KEY` (a Cloud Console key) that worked for text but returned HTTP 403 for image generation. The cause was Google Cloud org-level policy enforcement on Imagen endpoints — invisible at the project API-enablement level.

**Impact:** KB→Report→Infographic pipelines failed silently, with agents retrying `generate_image` until they exhausted fallback paths.

**Solution:** Store a separate `GEMINI_IMAGE_API_KEY` from AI Studio (`aistudio.google.com`) in Infisical. The `generate_image` tool now prefers `GEMINI_IMAGE_API_KEY` over `GEMINI_API_KEY`. AI Studio keys bypass org-level policy enforcement.

### 10. KB Pipelines Must Explicitly Mandate NLM for Artifact Generation

When the nightly wiki agent dispatched a VP mission to build knowledge bases, the VP used `generate_image` for infographics instead of NLM's native `studio_create(type="infographic")`. The pipeline prompt described the *desired output* but never named the *required tool path*.

**Impact:** Agents defaulted to generic tools, hit the 403 issue above, and fell into HTML-to-PDF fallback loops instead of using NLM's high-quality infographic renderer.

**Solution:** Pipeline prompts must explicitly mandate the NLM tool path for each artifact type:
- Research → `research_start` + `research_import`
- Reports → `studio_create(type="report")`  
- Infographics → `studio_create(type="infographic")`
- Audio → `studio_create(type="audio")`
- Download → `download_artifact`

Generic tools (`generate_image`, LLM markdown) should only be used when NLM is explicitly unavailable.

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NOTEBOOKLM_AUTH_COOKIE_HEADER` | — | Google session cookies for NLM API auth |
| `UA_CIRCUIT_BREAKER_MAX_CONSECUTIVE_POLLING` | `30` | Max consecutive identical calls for polling tools |
| `UA_CIRCUIT_BREAKER_MAX_CONSECUTIVE_SAME_SIGNATURE` | `8` | Max consecutive identical calls for regular tools |

### NLM MCP Server Config

The NLM MCP server is configured in the Claude agent's MCP settings. It runs as the `notebooklm-mcp` server with stdio transport. The server auto-refreshes tokens and supports multiple Google account profiles.

## Strategic Context

### What NLM Changes for UA

NotebookLM gives UA access to a **premium research-and-artifact engine** running on Google's infrastructure at zero compute cost to us. It handles capabilities that we previously built manually:

| Capability | Before NLM | With NLM |
|---|---|---|
| Web source discovery | research-specialist + scraping tools | Built-in `research_start` with ranked results |
| Multi-source synthesis | Manual collation in prompts | Automatic corpus indexing + cross-source querying |
| Report generation | report-writer sub-agent + LLM prompting | Native briefing docs, study guides, blog posts |
| Audio content | Not available | Podcast-style deep dives with AI hosts |
| Visual artifacts | image-expert for individual images | Infographics, slide decks, mind maps, video |
| Research persistence | Ephemeral (in-conversation only) | Persistent notebooks accessible via API |

### Latency Tradeoffs

NLM introduces meaningful latency compared to direct LLM tool use:

| Operation | Typical Latency | Notes |
|---|---|---|
| Fast research + import | ~45 seconds | Source discovery + indexing |
| Report generation | ~30 seconds | After sources are indexed |
| Audio overview | 3-5 minutes | Full podcast generation |
| Infographic | 1-2 minutes | Visual rendering |
| Slide deck | 1-2 minutes | PDF/PPTX generation |
| Video overview | 3-5 minutes | Animated video rendering |
| **Full pipeline** (research → 4 artifacts) | **8-15 minutes** | End-to-end including downloads |

For comparison, the existing research-specialist + report-writer pipeline typically completes in 2-4 minutes for text output.

### When to Use NLM vs Existing Tools

| Scenario | Recommendation | Rationale |
|---|---|---|
| Quick factual research | **Existing tools** (research-specialist) | Lower latency, sufficient quality |
| Multi-source deep analysis | **NLM** | Superior source discovery, cross-source synthesis |
| Text-only report | **Either** — NLM for premium quality, existing for speed | NLM reports are well-structured with citations |
| Audio/video/visual artifacts | **NLM** (only option) | No equivalent in existing toolset |
| Recurring scheduled research | **NLM** | Persistent notebooks accumulate knowledge |
| Time-sensitive deliverables | **Existing tools** | NLM latency may be unacceptable |
| Comprehensive briefing packages | **NLM** | Generate report + slides + audio in one pipeline |

### Resource Model

NLM operations run on Google's infrastructure via our existing subscription. Key implications:

- **No compute cost** to UA — research and generation happen off-platform
- **Concurrent pipelines** — multiple topics can be researched simultaneously
- **Persistent storage** — notebooks remain accessible for iterative work
- **Rate limits** — Google's internal API rate limits apply (not documented)

## Future Opportunities

1. ~~**Scheduled Research Digests**~~ — **REALIZED (2026-04-14):** The `nightly_wiki_agent.py` cron job runs at 03:15 CST, selects top proactive signal cards, dispatches VP missions to build NLM knowledge bases, and saves a summary for the morning briefing agent to surface. See `schedule_nightly_wiki.py` for cron registration.
2. **Source Curation** — Use `source_describe` to evaluate and filter imported sources before artifact generation
3. **Iterative Research** — Add more sources to existing notebooks over time, building cumulative knowledge bases
4. **Export to Google Workspace** — Use `export_artifact` to push reports to Google Docs and data tables to Google Sheets
5. **Collaborative Notebooks** — Use `notebook_share_invite` to share research notebooks with team members
6. **Video Generation** — Leverage video overviews with styles like "cinematic" and "whiteboard" for presentation-ready content
7. **Hybrid Pipelines** — Use NLM for research/corpus building, then existing agents for time-sensitive artifact formatting
8. **Research Corpus as Knowledge Base** — Use NLM notebooks as persistent, queryable knowledge bases that agents can consult via `notebook_query`

