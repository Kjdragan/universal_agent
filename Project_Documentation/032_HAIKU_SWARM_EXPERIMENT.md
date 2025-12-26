# Haiku Swarm Experiment - Post-Mortem Analysis

**Date:** December 26, 2025  
**Status:** Experiment Concluded - Not Adopted  
**Branch:** (To be reverted)

---

## Executive Summary

This document captures the complete details of an experimental optimization called "Haiku Swarm" that was designed to reduce the ~97-second bottleneck during report synthesis. While the implementation was technically successful (the swarm correctly extracted 253 facts from 17 documents), the overall result **increased total execution time from 235s to 308s** due to API latency overhead. This experiment is being documented for future reference before reverting to the previous codebase.

---

## 1. Problem Statement

### 1.1 The Bottleneck
During report generation workflows, a significant bottleneck was identified in the synthesis phase:

| Phase | Duration | Description |
|-------|----------|-------------|
| Search | ~20s | Composio NEWS + WEB search |
| Crawl | ~17s | `crawl_parallel` fetches 10-17 URLs |
| **Synthesis** | **~97s** | Sub-agent reads raw markdown, synthesizes report |
| PDF + Email | ~50s | Conversion and delivery |

The synthesis phase was taking ~97 seconds because the sub-agent had to:
1. Read 10-17 individual markdown files (each 3-50KB)
2. Process ~50,000+ tokens of raw content
3. Extract relevant information while filtering noise
4. Synthesize a coherent report

### 1.2 Hypothesis
We hypothesized that by **pre-processing documents in parallel using fast, cheap models**, we could:
1. Extract verbatim facts (statistics, dates, quotes, events) from each document
2. Reduce the context size by ~80% (from raw markdown to structured facts)
3. Allow the synthesis agent to work with a condensed "dossier" instead of raw files
4. Target synthesis time reduction from ~97s to ~15-20s

---

## 2. Architecture: "Haiku Swarm"

### 2.1 Design Concept: Detail-Preserving Map-Reduce

```
┌─────────────────────────────────────────────────────────────────┐
│  BEFORE: Serial Processing                                       │
│                                                                   │
│  crawl_*.md files → Claude reads each → Synthesizes → Report    │
│       (17 files × 3-50KB each = ~50,000+ tokens)                │
│       Time: ~97 seconds                                          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  AFTER: Parallel Pre-Processing (Haiku Swarm)                    │
│                                                                   │
│  MAP PHASE (GLM-4.5-Air × 6 parallel):                          │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐               │
│  │Doc 1│ │Doc 2│ │Doc 3│ │Doc 4│ │Doc 5│ │Doc 6│  ...          │
│  └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘               │
│     ↓       ↓       ↓       ↓       ↓       ↓                    │
│  [Facts] [Facts] [Facts] [Facts] [Facts] [Facts]                │
│     └───────┴───────┴───────┴───────┴───────┘                    │
│                         ↓                                         │
│              combined_dossier.json                               │
│              (~253 structured facts)                             │
│                         ↓                                         │
│  REDUCE PHASE (Sonnet):                                          │
│              Synthesize Report from Dossier                      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Model Selection
- **Map Phase:** GLM-4.5-Air (via Z.AI endpoint at `https://api.z.ai/api/anthropic/v1`)
  - Fast, inexpensive model equivalent to Claude Haiku
  - Good at extraction tasks
  - Accessible via existing ZAI_API_KEY

- **Reduce Phase:** Claude Sonnet (inherited model)
  - Handles complex synthesis
  - Receives condensed dossier instead of raw files

### 2.3 Fact Extraction Schema
Each document was processed into structured JSON:

```json
{
  "source_url": "https://example.com/article",
  "facts": [
    {"type": "statistic", "text": "Russia seized 1,802 sq mi in 2025", "context": "territorial gains"},
    {"type": "date", "text": "December 24, 2025", "context": "major drone strikes"},
    {"type": "quote", "text": "The ball is in Ukraine's court", "speaker": "Vladimir Putin"},
    {"type": "event", "text": "Car bomb in Moscow killed 3", "context": "including 2 police officers"},
    {"type": "entity", "text": "Steve Witkoff", "context": "US peace envoy"}
  ]
}
```

---

## 3. Implementation Details

### 3.1 Files Modified

#### 3.1.1 `src/mcp_server.py` - New MCP Tool
Added a new tool `research_analyst_swarm()` with the following structure:

```python
@mcp.tool()
async def research_analyst_swarm(
    file_paths: list[str],
    query: str,
    session_dir: str,
    max_parallel: int = 6  # Reduced from 8 due to rate limits
) -> str:
    """
    Analyze documents in parallel using GLM-4.5-Air models via Z.AI endpoint.
    Extracts key facts before synthesis, reducing context by ~80%.
    """
```

**Key Features:**
- `asyncio.Semaphore(max_parallel)` for rate limiting
- `httpx.AsyncClient` for async HTTP calls
- Saves individual dossiers to `session_dir/dossiers/dossier_XX.json`
- Aggregates all facts into `combined_dossier.json`

**Extraction Prompt Template:**
```
You are a research analyst. Extract ALL specific, verifiable facts from this document.

EXTRACTION RULES:
- Extract VERBATIM quotes, exact numbers, specific dates
- Include speaker/source attribution
- Preserve context for each fact
- Do NOT summarize or paraphrase

Return JSON format:
{
  "source_url": "...",
  "facts": [
    {"type": "statistic|date|quote|event|entity", "text": "...", "context": "..."}
  ]
}
```

#### 3.1.2 `src/universal_agent/utils/composio_discovery.py`
Added the new tool to the discovery list:

```python
LOCAL_MCP_TOOLS = [
    "mcp__local_toolkit__crawl_parallel",
    "mcp__local_toolkit__research_analyst_swarm",  # NEW
    "mcp__local_toolkit__read_local_file",
    # ... other tools
]
```

#### 3.1.3 `.claude/agents/report-creation-expert.md`
Updated sub-agent prompt to make swarm usage **MANDATORY**:

```markdown
### Step 2.5: 🚀 MANDATORY - Pre-Process with Swarm (SPEED OPTIMIZATION)

**YOU MUST USE `research_analyst_swarm` BEFORE reading raw files.**

After `crawl_parallel` completes:
1. List all `.md` files in `search_results/`
2. **IMMEDIATELY call `research_analyst_swarm`** with file paths
3. Read ONLY the `combined_dossier.json` from `dossiers/`
4. Use the pre-extracted facts for synthesis

**🚨 DO NOT read raw crawl_*.md files individually. Use the swarm output.**
```

#### 3.1.4 `src/universal_agent/main.py`
Updated inline AgentDefinition prompt with same mandatory swarm step.

---

## 4. Bugs Encountered and Fixed

### 4.1 Bug: JSON Wrapped in Markdown Code Fences
**Problem:** The Z.AI API returned JSON wrapped in markdown code blocks:
```
```json
{"source_url": "...", "facts": [...]}
```
```

**Symptom:** `json.loads()` failed, falling back to empty facts array.

**Fix:** Added code fence stripping before parsing:
```python
clean_text = extracted_text.strip()
if clean_text.startswith("```"):
    first_newline = clean_text.find("\n")
    if first_newline != -1:
        clean_text = clean_text[first_newline + 1:]
    if clean_text.rstrip().endswith("```"):
        clean_text = clean_text.rstrip()[:-3].rstrip()
dossier = json.loads(clean_text)
```

### 4.2 Bug: Type Validation Missing
**Problem:** Parsed JSON sometimes lacked `facts` key or had wrong type.

**Fix:** Added type validation:
```python
if not isinstance(dossier, dict):
    dossier = {"source_url": source_url, "raw_extraction": extracted_text, "facts": []}
elif "facts" not in dossier or not isinstance(dossier.get("facts"), list):
    dossier["facts"] = []
```

### 4.3 Bug: Rate Limiting (429 Errors)
**Problem:** With `max_parallel=8`, Z.AI returned 429 "Too Many Requests" errors.

**Fix:** Reduced parallelism from 8 to 6:
```python
max_parallel: int = 6  # Was 8
```

### 4.4 Bug: Tool Not Discovered
**Problem:** Agent didn't call the swarm tool because it wasn't in `LOCAL_MCP_TOOLS`.

**Fix:** Added to `composio_discovery.py` tool list.

---

## 5. Test Results

### 5.1 Baseline (Before Swarm)
- **Session:** `session_20251226_075332`
- **Total Time:** 235.8 seconds
- **Report Synthesis:** ~97 seconds (from +69s to +166s)
- **Trace ID:** `019b5aefa315da88e5ea2d353faf06c1`

### 5.2 With Swarm (After Implementation)
- **Session:** `session_20251226_085015`
- **Total Time:** 308.2 seconds (+72.4s slower)
- **Facts Extracted:** 253
- **Trace ID:** `019b5b238fa276d36c300c99a3d22355`

### 5.3 Timing Comparison

| Phase | Baseline | With Swarm | Difference |
|-------|----------|------------|------------|
| Search | ~20s | ~27s | +7s |
| Crawl parallel | ~17s | ~17s | 0 |
| **Swarm extraction** | N/A | **75s** | **+75s** |
| Read dossier | N/A | ~2s | +2s |
| Report synthesis | ~97s | ~80s | **-17s** |
| PDF + Email | ~50s | ~52s | +2s |
| **TOTAL** | **235.8s** | **308.2s** | **+72.4s** |

### 5.4 Facts Extracted (Session 085015)

| Fact Type | Count |
|-----------|-------|
| Entities | 99 |
| Quotes | 49 |
| Events | 41 |
| Dates | 33 |
| Statistics | 30+ |
| **Total** | **253** |

---

## 6. Analysis: Why It Didn't Work

### 6.1 The Core Problem: API Latency
The Z.AI endpoint took **75 seconds** to process 17 documents with 6 parallel workers. This latency far exceeded the 17 seconds saved in the synthesis phase.

**Math:**
- Time added by swarm: +75s
- Time saved in synthesis: -17s  
- Net impact: **+58s slower**

### 6.2 Why Z.AI Was Slow
- Each document required a full round-trip to the Z.AI API
- 6 parallel workers meant 3 "waves" for 17 documents
- Each API call included ~3-50KB of document content
- Response generation took 3-8 seconds per document

### 6.3 The Flawed Assumption
We assumed the "fast model swarm" would be faster than having Claude read the files directly. However:
- Claude SDK already batches file reads efficiently
- Claude's internal context processing is highly optimized
- External API calls introduce network latency that dominates

### 6.4 When Swarm WOULD Work
The swarm architecture would provide benefit if:
1. **Faster API endpoint** - Local inference or sub-100ms API
2. **Larger document sets** - 30+ documents where synthesis bottleneck is worse
3. **Parallel with crawling** - Start swarm on first documents while others still crawling
4. **Different use case** - When fact extraction is the primary goal, not speed

---

## 7. Lessons Learned

### 7.1 Technical Lessons
1. **API latency matters more than model speed** - A "fast" model is useless if the API round-trip is slow
2. **Test early with real infrastructure** - Mock tests passed, but real API behavior was different
3. **Measure the right thing** - We optimized synthesis but added more time elsewhere
4. **Code fence handling** - LLMs often wrap JSON in markdown, always strip code fences

### 7.2 Process Lessons
1. **Baseline first** - We had a good baseline measurement, which made comparison clear
2. **Incremental verification** - We caught bugs (tool discovery, code fences) through testing
3. **Document experiments** - This document preserves the learning even though we're reverting

### 7.3 Architecture Lessons
1. **Claude SDK is already optimized** - Don't assume external tools will be faster
2. **Map-reduce has overhead** - The coordination cost can exceed the parallelism benefit
3. **Consider the full pipeline** - Optimizing one phase can slow down the overall flow

---

## 8. Artifacts Produced

### 8.1 Code Changes (To Be Reverted)
- `src/mcp_server.py` - Lines 380-600 (research_analyst_swarm function)
- `src/universal_agent/utils/composio_discovery.py` - Line 20-21 (tool list)
- `src/universal_agent/main.py` - Lines 1647-1658 (inline prompt)
- `.claude/agents/report-creation-expert.md` - Lines 55-78 (swarm step)

### 8.2 Test Sessions
- `AGENT_RUN_WORKSPACES/session_20251226_075332` - Baseline
- `AGENT_RUN_WORKSPACES/session_20251226_085015` - Final swarm test (253 facts)
- Multiple intermediate sessions during debugging

### 8.3 Unit Tests Created
- `tests/test_research_analyst_swarm.py` - All tests passed

---

## 9. Alternative Approaches Not Tried

### 9.1 Local Inference
Using a local model (Ollama, llama.cpp) would eliminate API latency but require GPU resources.

### 9.2 Streaming/Pipelining
Instead of waiting for all swarm results, start synthesis as soon as first dossiers arrive.

### 9.3 Caching
Cache processed dossiers for frequently accessed URLs to avoid re-extraction.

### 9.4 Different API Provider
Test with Anthropic, OpenAI, or Groq which may have lower latency.

### 9.5 Prompt Optimization
Instead of swarm extraction, optimize the synthesis prompt to be more selective about what it reads.

---

## 10. Conclusion

The Haiku Swarm experiment was a valuable learning exercise that produced technically correct code but failed to achieve its optimization goal. The 253 facts were successfully extracted, proving the architecture works, but the Z.AI API latency made the overall execution **72.4 seconds slower** than baseline.

The codebase will be reverted to the previous state before this experiment. This document serves as a comprehensive record of the approach, implementation, and findings for future reference.

---

## Appendix A: Environment Variables Required

```bash
# Z.AI API key (for GLM-4.5-Air access)
ZAI_API_KEY=your_key_here

# Optional: Disable swarm without removing code
HAIKU_SWARM_ENABLED=false

# Optional: Override model (default: GLM-4.5-Air)
HAIKU_SWARM_MODEL=GLM-4.5-Air

# Optional: Override parallelism (default: 6)
HAIKU_SWARM_MAX_PARALLEL=6
```

## Appendix B: Logfire Traces

- **Baseline:** `019b5aefa315da88e5ea2d353faf06c1`
- **Final Test:** `019b5b238fa276d36c300c99a3d22355`

Both traces available at: `https://logfire.pydantic.dev/Kjdragan/composio-claudemultiagent`
