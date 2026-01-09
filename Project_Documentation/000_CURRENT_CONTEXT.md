# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2026-01-09 01:15 CST

---

## 🎯 Project Overview

**Universal Agent** is a standalone agent using Claude Agent SDK with Composio Tool Router integration.

**Core Capabilities**:
- Claude Agent SDK for agentic workflows
- Composio Tool Router for 500+ tool integrations (Gmail, SERP, Slack, etc.)
- Crawl4AI parallel web extraction via local MCP server
- Sub-agent delegation for specialized tasks (report generation)
- Logfire tracing for observability
- **Letta-style Memory System** with Core Memory blocks (persona, human, system_rules)
- **Agent College** self-improvement subsystem
- Automatic workspace and artifact management
- Observer pattern for async result processing and error tracking

**Main Entry Point**: `src/universal_agent/main.py`
**MCP Server Tools**: `src/mcp_server.py`

---

## 🔴 CURRENT FOCUS: Evidence Ledger + Context Compaction

We're solving TWO problems with ONE architectural change:

1. **Context Fatigue** - Large reports fail with 0 bytes written (context exhaustion)
2. **Report Quality** - One-shot synthesis from raw research produces sterile "summary of summaries"

### The Solution: Two-Phase Report Generation with Evidence Ledger

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 1: EXTRACTION (Tool-heavy, minimal synthesis)                    │
│                                                                         │
│  finalize_research() → read_research_files() → BUILD EVIDENCE LEDGER   │
│                                                                         │
│  For each source, extract:                                              │
│  • Direct quotes (with attribution)                                     │
│  • Specific data points (numbers, dates, percentages)                   │
│  • Key claims and findings                                              │
│  • Contradictions or tensions between sources                           │
│  • Notable voices and perspectives                                      │
│                                                                         │
│  Output: evidence_ledger.md (~10-20% size of raw corpus)               │
│          Contains 100% of the "quotable" material                       │
│                                                                         │
│  ─────────────── CONTEXT COMPACTION HAPPENS HERE ───────────────        │
│  Clear/summarize all messages from Phase 1                              │
│  Agent now has fresh context for synthesis                              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 2: SYNTHESIS (Creative, full context available for writing)      │
│                                                                         │
│  Read ONLY evidence_ledger.md (compressed, high-signal)                 │
│                                                                         │
│  Generate report with:                                                  │
│  • Full creative freedom on structure (NO templates)                    │
│  • Rich detail from extracted quotes and data                           │
│  • Organic flow based on thematic connections                           │
│  • Topic-appropriate tone (science vs business vs culture)              │
│                                                                         │
│  Output: Full report with specifics, not sterile summaries              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why This Works

| Problem | How Evidence Ledger Solves It |
|---------|-------------------------------|
| **Context exhaustion** | Ledger is 10-20% of raw corpus size; synthesis reads only ledger |
| **Summary of summaries** | Ledger preserves exact quotes, numbers, dates - the "texture" |
| **Sterile templates** | No structure imposed; agent organizes by thematic connections |
| **Topic diversity** | Works for science, business, culture, AI - structure emerges from evidence |
| **Lost specifics** | Extraction phase explicitly captures quotable material before discarding |

### Evidence Ledger Format (Example)

```markdown
# Evidence Ledger: [Topic]
Generated: 2026-01-09 | Sources: 8 files | ~15KB compressed from ~120KB raw

---

## Source: arxiv_transformer_scaling.md
**Type**: Academic Paper | **Date**: 2025-11

### Key Claims
- "We observe a 70.7% accuracy on GDPval, representing a 12.3% improvement over the previous SOTA" (Section 4.2)
- "Scaling beyond 400B parameters shows diminishing returns for reasoning tasks" (Abstract)

### Data Points
- Training: 2.1T tokens, 45 days on 2048 H100s
- Inference: 0.8s latency at batch=1, 2.3s at batch=32
- Cost: ~$4.2M training budget

### Tensions
- Contradicts Chen et al. (2024) claim that scaling continues linearly

---

## Source: hackernews_practitioner_thread.md
**Type**: Community Discussion | **Date**: 2025-12

### Notable Voices
- @senior_ml_eng (claims Anthropic employee): "The benchmark methodology is fundamentally flawed for production use cases"
- @startup_founder: "We switched from GPT-4 to Claude and saw 40% cost reduction with comparable quality"

### Sentiment
- Skepticism about benchmark validity (7 of 12 top comments)
- Enthusiasm about cost/performance tradeoff (4 of 12)

### Quotes
- "Benchmarks are the new vanity metrics" - @ml_skeptic (847 upvotes)

---

## Source: company_blog_announcement.md
**Type**: Corporate | **Date**: 2025-12

### Key Claims
- "Available to all API users starting January 15, 2026"
- "Pricing: $15/M input, $75/M output tokens"

### Marketing vs Reality Flags
- Claim: "10x faster" - Note: Compared to their own previous model, not competitors
- Claim: "Best in class" - Note: Only on 2 of 5 standard benchmarks

---
```

### What Makes This Different from Templates

**BAD (Template-driven)**:
```
1. Executive Summary
2. Introduction
3. Findings
4. Analysis
5. Conclusion
```
This produces formulaic reports regardless of topic.

**GOOD (Evidence-driven)**:
The agent sees the ledger and decides: "The tension between academic benchmarks and practitioner experience is the real story here. I'll structure around that conflict."

Result: Organic structure that emerges from the evidence itself.

---

## 📋 IMPLEMENTATION PLAN

### Phase 1: Build Evidence Ledger Tool

**New MCP Tool**: `build_evidence_ledger`

```python
@mcp.tool()
async def build_evidence_ledger(
    session_dir: str,
    topic: str,
    source_files: list[str] | None = None  # If None, use all in search_results_filtered_best/
) -> str:
    """
    Reads research files and extracts structured evidence into a ledger.

    For each source, extracts:
    - Direct quotes with attribution
    - Specific data points (numbers, dates, stats)
    - Key claims and findings
    - Contradictions or tensions
    - Notable voices/perspectives

    Returns: Path to evidence_ledger.md
    """
```

**Implementation Options**:
1. **LLM-powered extraction** - Use Claude to read each file and extract evidence (higher quality)
2. **Heuristic extraction** - Regex/rules for quotes, numbers, dates (faster, no API cost)
3. **Hybrid** - Heuristics for data points, LLM for claims/tensions

Recommend: **Option 1 (LLM-powered)** for quality, since this is a one-time cost per report.

### Phase 2: Context Compaction Hook

**Location**: After `build_evidence_ledger` completes, before synthesis begins

**Mechanism**:
```python
# In report-creation-expert workflow or as hook
if ledger_built:
    # Option A: Clear message history
    history.clear_except_system_prompt()

    # Option B: Summarize and replace
    summary = f"Phase 1 complete. Evidence ledger saved to {ledger_path}. Ready for synthesis."
    history.replace_with_summary(summary)
```

### Phase 3: Update Report Sub-Agent Workflow

**Modify**: `.claude/agents/report-creation-expert.md`

```markdown
## Updated Workflow

### Step 1: Finalize Research (unchanged)
Call: finalize_research(session_dir="{CURRENT_SESSION_WORKSPACE}")

### Step 2: Build Evidence Ledger (NEW)
Call: build_evidence_ledger(session_dir="{WORKSPACE}", topic="{TOPIC}")
- Extracts quotes, data, claims, tensions from each source
- Saves to: search_results/evidence_ledger.md

### Step 3: Context Compaction (NEW - AUTOMATIC)
- System clears Phase 1 messages
- Fresh context available for synthesis

### Step 4: Synthesize Report
- Read ONLY: evidence_ledger.md
- DO NOT read raw crawl files
- Let structure emerge from evidence themes
- Include specific quotes, numbers, dates from ledger
- NO template structure - organize by thematic connections

### Step 5: Save Report
Write to work_products/{topic}_{date}.html
```

---

## 🔬 Root Cause Analysis (For Context)

### The 0 Bytes Problem

When generating large reports, files are written with **0 bytes**:

```
🔧 [mcp__local_toolkit__write_local_file] +395.337s
   Input size: 0 bytes          ← Content was truncated
📦 Tool Result (30 bytes) +395.406s
   Preview: Tool schema validation failed.
```

**Cause**: Context fatigue cascade
1. Agent reads 5-10 files × 50KB = 250-500KB → ~60-100K tokens
2. Agent synthesizes report → more tokens consumed
3. Context hits 90%+ (180K of 200K limit)
4. Claude SDK truncates `content` parameter → 0 bytes

### Why Evidence Ledger Fixes This

| Stage | Without Ledger | With Ledger |
|-------|---------------|-------------|
| Research read | 250KB raw | 250KB raw |
| Context after read | ~100K tokens | ~100K tokens |
| **Compaction** | ❌ None | ✅ Clear to ~5K tokens |
| Synthesis input | 250KB in context | 25KB ledger only |
| Context for write | ~180K (exhausted) | ~60K (plenty of room) |

---

## 📍 Current State (January 9, 2026)

### ✅ What's Working

| Feature | Status | Notes |
|---------|--------|-------|
| **Railway Deployment** | ✅ Production | US West, Static IP, Pro plan |
| **Telegram Bot** | ✅ Working | Webhook mode, FastAPI + PTB |
| **Research & Report Generation** | ⚠️ Context issues | Works for small reports, fails on large |
| **PDF/PPTX Creation** | ✅ Working | Skills-based, conditional routing |
| **Email Delivery (Gmail)** | ✅ Working | Attachments via `upload_to_composio` |
| **Filtered Research Corpus** | ✅ Working | `finalize_research` + filtered corpus |
| **MessageHistory class** | ✅ Exists | Has `truncate()`, `should_handoff()` |

### 🔴 Current Issue → Solution In Progress

| Issue | Status | Solution |
|-------|--------|----------|
| **0 Bytes Write** | 🔴 Active | Evidence Ledger + Compaction |
| **Summary of Summaries** | 🔴 Active | Evidence Ledger preserves texture |

---

## 🧭 New Coder Handoff (Quick Start)

**Goal**: Implement Evidence Ledger + Context Compaction for report generation.

**What you need to implement**:

1. **`build_evidence_ledger` MCP tool** in `src/mcp_server.py`
   - Reads research files
   - Extracts quotes, data, claims, tensions per source
   - Saves structured ledger to `search_results/evidence_ledger.md`

2. **Context compaction mechanism**
   - After ledger built, clear/summarize Phase 1 messages
   - Options: `history.truncate()`, `history.clear()`, or summary replacement
   - Location: Hook in main.py or instruction in sub-agent workflow

3. **Update report-creation-expert.md**
   - Add Step 2: `build_evidence_ledger`
   - Add Step 3: Context compaction note
   - Modify Step 4: Read only ledger, not raw files

**Key files to modify**:
| File | Change |
|------|--------|
| `src/mcp_server.py` | Add `build_evidence_ledger` tool |
| `.claude/agents/report-creation-expert.md` | Update workflow |
| `src/universal_agent/main.py` | Add compaction hook (optional) |
| `src/universal_agent/utils/message_history.py` | May need new methods |

**Test approach**:
```bash
./local_dev.sh
# Ask: "Create a comprehensive report on [topic with 10+ search results]"
# Verify:
# 1. evidence_ledger.md created in search_results/
# 2. Report includes specific quotes and numbers from ledger
# 3. No 0-byte write errors
# 4. Report structure is organic, not template-driven
```

---

## 🔧 Running the System

### Local Development (CLI)
```bash
cd /home/kjdragan/lrepos/universal_agent
./local_dev.sh
```

### Check Recent Session Logs
```bash
ls -lt AGENT_RUN_WORKSPACES/ | head -5
tail -200 AGENT_RUN_WORKSPACES/session_*/run.log
```

---

## 📚 Key Documentation

| Priority | Document | Purpose |
|----------|----------|---------|
| 1 | **This file** | Current focus and implementation plan |
| 2 | `.claude/agents/report-creation-expert.md` | Report sub-agent workflow (to be updated) |
| 3 | `src/mcp_server.py` | Where to add `build_evidence_ledger` |
| 4 | `src/universal_agent/utils/message_history.py` | Context management |
| 5 | `002_LESSONS_LEARNED.md` | Patterns and gotchas |

---

## 🏗️ Project Structure (Relevant to Current Task)

```
universal_agent/
├── src/
│   ├── universal_agent/
│   │   ├── main.py                 # Main agent, hooks, harness
│   │   ├── utils/
│   │   │   └── message_history.py  # Context tracking, truncate()
│   │   └── durable/                # State management
│   └── mcp_server.py               # ADD build_evidence_ledger HERE
├── .claude/
│   └── agents/
│       └── report-creation-expert.md  # UPDATE workflow HERE
├── Project_Documentation/
│   └── 000_CURRENT_CONTEXT.md      # This file
└── AGENT_RUN_WORKSPACES/
    └── session_*/
        └── search_results/
            ├── evidence_ledger.md  # NEW - will be created here
            └── research_overview.md
```

---

## 🎯 Success Criteria

The implementation is complete when:

1. ✅ `build_evidence_ledger` tool exists and produces structured output
2. ✅ Evidence ledger preserves quotes, numbers, dates, tensions
3. ✅ Context compaction happens after ledger is built
4. ✅ Report synthesis reads only the ledger (not raw files)
5. ✅ Large reports (10+ sources) complete without 0-byte errors
6. ✅ Reports have organic structure (not template-driven)
7. ✅ Reports include specific details from ledger (not vague summaries)

---

*Update this document whenever significant progress is made or context changes.*
