# Golden Run Anatomy

> A golden run is a known-good execution of the research→report→PDF→email pipeline
> that produces the expected end-to-end result. It serves as the regression baseline.

## Reference Prompt

```
Search for the latest information from the Russia-Ukraine war over the past five days.
Create a report, save the report as a PDF, and email it to me.
```

---

## Expected Tool Sequence

The primary agent (Simone) should produce this ordered tool sequence:

```
 Step  Tool                                       Purpose
 ─────────────────────────────────────────────────────────────────────────
  1    Task(research-specialist)                   Delegate research
  2    COMPOSIO_MULTI_EXECUTE_TOOL                 Parallel news searches
  3    mcp__internal__run_research_phase            Crawl + refine via Crawl4AI
  4    Read                                        Verify refined corpus
  5    Task(report-writer)                         Delegate report generation
  6    mcp__internal__run_report_generation         Outline → draft → synthesize → HTML
  7    mcp__internal__html_to_pdf                   Chrome headless PDF conversion
  8    Skill(agentmail) → Bash                     Email the PDF to Kevin
```

> **Key rule:** The FIRST tool call must be productive work, not cleanup/housekeeping.

### Acceptable Variations

- Steps 2-4 may happen inside the research-specialist sub-agent (not visible in primary log)
- Steps 5-6 may happen inside the report-writer sub-agent
- Email delivery may use AgentMail (Bash SDK call) or Gmail (COMPOSIO_MULTI_EXECUTE_TOOL)
- Up to 2 Read calls between report generation and PDF conversion are normal

### Red Flags

| Pattern | Indicates |
|---------|-----------|
| `TaskStop` as first action | Hallucinated task lifecycle management (see lessons_learned.md) |
| `Bash` before `Task` | Scouting/exploration instead of delegation |
| Missing `run_research_phase` | Research pipeline bypassed |
| Missing `run_report_generation` | Report pipeline bypassed |
| Files at repo root | Missing workspace injection |
| 50+ tool calls | Loop/hallucination |

---

## Timing Baseline

| Phase | Golden Run | Acceptable Range |
|-------|-----------|-----------------|
| Research (search + crawl + refine) | ~120s | 60-180s |
| Report (outline + draft + synthesize) | ~330s | 120-400s |
| PDF conversion | ~1s | 1-30s |
| Email delivery | ~20s | 5-60s |
| **Total** | **~580s** | **200-600s** |

---

## Expected Session Workspace Structure

```
session_{timestamp}_{hash}/
├── SOUL.md                           # Persona (injected at boot)
├── HEARTBEAT.md                      # Proactive config (injected at boot)
├── AGENTS.md                         # Agent docs
├── BOOTSTRAP.md                      # Workspace bootstrap
├── IDENTITY.md                       # Identity context
├── MEMORY.md                         # Core memory backup
├── TOOLS.md                          # Tool reference
├── USER.md                           # User context
├── capabilities.md                   # Dynamic capability routing (42KB)
├── session_policy.json               # Session policy
├── heartbeat_state.json              # Heartbeat state
├── run.log                           # ← KEY: Human-readable execution log
├── transcript.md                     # Full transcript
├── trace.json / trace_catalog.md     # Execution trace
├── session_checkpoint.json/md        # Session state
│
├── memory/                           # Session memory (may be empty)
├── downloads/                        # Downloaded files
│
├── search_results/                   # ← MUST EXIST
│   ├── crawl_*.md                    # 15-25 crawled source files
│   ├── research_overview.md          # Search overview
│   └── processed_json/              # Raw search result JSONs
│       ├── COMPOSIO_SEARCH_NEWS_0_*.json
│       ├── COMPOSIO_SEARCH_NEWS_1_*.json
│       ├── COMPOSIO_SEARCH_NEWS_2_*.json
│       └── COMPOSIO_SEARCH_NEWS_3_*.json
│
├── tasks/{task_name}/                # ← MUST EXIST
│   ├── refined_corpus.md             # ← REQUIRED: Synthesized research output
│   ├── filtered_corpus/              # Individual filtered crawl files
│   │   └── crawl_*.md                # 10-15 relevant articles
│   └── research_overview.md          # Task-level research summary
│
├── work_products/                    # ← MUST EXIST
│   ├── report.html                   # ← REQUIRED: Final HTML report
│   ├── *.pdf                         # ← REQUIRED: PDF conversion output
│   ├── _working/                     # Intermediate drafts
│   │   ├── outline.json              # Report outline
│   │   └── sections/                 # Section drafts
│   │       ├── 01_01_executive_summary.md
│   │       ├── 02_*.md
│   │       └── ...
│   └── logfire-eval/                 # Execution trace artifacts
│       ├── trace_catalog.json
│       └── trace_catalog.md
│
└── turns/                            # SDK turn transcripts
    └── turn_*.jsonl
```

---

## Golden Run Validation Checklist

When validating a run:

1. ☐ First tool call is `Task(research-specialist)` or equivalent productive work
2. ☐ `search_results/` directory created with 15+ crawl files
3. ☐ `tasks/{task_name}/refined_corpus.md` exists (synthesized research)
4. ☐ `work_products/report.html` exists (compiled HTML report)
5. ☐ `work_products/*.pdf` exists (PDF conversion successful)
6. ☐ Email sent successfully (AgentMail message_id or Gmail send confirmation)
7. ☐ Total tool calls ≤ 15
8. ☐ Total execution time < 600s
9. ☐ Zero `TaskStop` calls
10. ☐ No files leaked to repo root

---

## Reference Sessions

### Golden Run #2 — March 17, 2026 (LATEST)

```
Session:  session_20260317_061548_9618fd62
Tools:    9 tool calls
Time:     580.5s
Iters:    1 (single iteration, no retries)
Email:    AgentMail → kevinjdragan@gmail.com
```

**Run log:**
```
[06:16:09] 👤 USER: Get the latest information about the Russia-Ukraine war...
[06:16:33] 🔧 Task(research-specialist)
[06:16:46] 🔧 COMPOSIO_MULTI_EXECUTE_TOOL (4 parallel searches)
[06:16:47] 📦 RESULT (24867 bytes)
[06:16:54] 🔧 mcp__internal__run_research_phase
[06:18:56] 📦 RESULT → refined_corpus.md
[06:19:00] 🔧 Read (verify corpus, 33095 bytes)
[06:19:34] 🔧 Task(report-writer)
[06:19:40] 🔧 mcp__internal__run_report_generation
[06:24:56] 📦 RESULT → work_products/report.html
[06:25:16] 🔧 mcp__internal__html_to_pdf
[06:25:17] 📦 PDF created (chrome headless)
[06:25:26] 🔧 Skill(agentmail)
[06:25:41] 🔧 Bash (AgentMail send)
[06:25:43] 📦 Sent: <message_id>
[06:26:16] === Turn completed (9 tool calls) ===
```

### Golden Run #1 — February 23, 2026

```
Session:  session_20260223_215506_140963c1
Tools:    9 tool calls
Time:     376.2s
Iters:    1
Email:    Gmail via Composio
```

### Golden Run #0 — February 27, 2026

```
Session:  session_20260227_195151_2927affc
Tools:    11 tool calls
Time:     ~430s
Iters:    1
Email:    Gmail via Composio
```
