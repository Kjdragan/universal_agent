---
title: MCP File Writing Latency Investigation (Session 20260214_082443_b851a68c)
date: 2026-02-14
scope: webui / agent-core / local-toolkit
status: draft_for_review
---

# 36. MCP File Writing Latency Investigation (2026-02-14)

## Executive Summary
This report analyzes why a successful social data run felt slow when writing interim work products, focusing on whether MCP-based file writing is necessary and what latency it introduced.

**Session reviewed (local workspace):**
`AGENT_RUN_WORKSPACES/session_20260214_082443_b851a68c`

**Key finding:**
The run performed **6 separate MCP file-write tool calls** (`mcp__internal__write_text_file`). Two of those writes had very large wall-clock gaps (**~51.8s** and **~39.8s**) between tool-call start and tool-result emission. Across all 6 writes, the tool-call boundary accounted for **~98.9s** of the run’s wall time (trace-derived), or roughly **37%** of the run’s ~266.8s duration.

**Interpretation:**
The latency is not explainable by filesystem I/O (files are 131–5911 bytes). It is consistent with **tool-orchestration overhead** (each tool call forces an additional tool round-trip and typically an additional model step), and/or intermittent slowness in the tool execution pipeline.

## What You Asked
1. Is it normal that file writes happen via MCP tool calls?
2. Do we have to use MCP for simple file writing?
3. Are we doing something unnecessary that increases end-to-end latency?
4. Provide a comprehensive review of this run’s outputs and timings (no code changes).

## Observed Outputs (Session Workspace Inventory)
Top-level artifacts present in `AGENT_RUN_WORKSPACES/session_20260214_082443_b851a68c/`:
- `run.log`: human-readable tool/event timeline + tool breakdown
- `transcript.md`: structured transcript of iteration events
- `trace.json`: machine-readable run trace summary (includes tool call records and time offsets)
- `trace_catalog.md`: pointers for Logfire trace inspection
- `session_checkpoint.json` + `session_checkpoint.md`: end-of-run checkpoint summary
- `work_products/`: generated work products (see below)
- Memory system state:
  - `memory/` (session index + session markdown)
  - `Memory_System_Data/` (`agent_core.db` + Chroma DB)

### Work Products Written
All work products under `AGENT_RUN_WORKSPACES/session_20260214_082443_b851a68c/work_products/`:
- `social/reddit/top_posts/request.json` (131 bytes)
- `social/reddit/top_posts/result.json` (5048 bytes)
- `social/reddit/top_posts/manifest.json` (248 bytes)
- `social/x/evidence_posts/openai__20260214_142616/request.json` (245 bytes)
- `social/x/evidence_posts/openai__20260214_142616/result.json` (5911 bytes)
- `social/x/evidence_posts/openai__20260214_142616/manifest.json` (249 bytes)
- `logfire-eval/trace_catalog.json` (1226 bytes)
- `logfire-eval/trace_catalog.md` (1964 bytes)

These are small files. Any multi-second latency in writing them is almost certainly not disk-bound.

## How File Writing Works In UA (As Implemented Today)
### The core constraint
Even though Universal Agent is a local Python program, the **LLM** cannot “just write a file” by itself. All external side effects (network calls, filesystem writes, etc.) must be performed by the **host runtime** on behalf of the model.

This is why file writing appears as tool calls in logs.

### Two file-writing paths currently referenced in UA prompts
In `src/universal_agent/prompt_builder.py`, the system prompt explicitly states:
- Prefer native `Write`
- If native `Write` is restricted, use `mcp__internal__write_text_file`

So: **No, you do not strictly have to use MCP for file writing**, but the system is designed to fall back to MCP-based file writing when native `Write` is unavailable/restricted in a given runtime.

### What `mcp__internal__write_text_file` actually does
Implementation is straightforward and fast:
- Tool: `write_text_file` in `src/mcp_server.py`
- It validates the target path is under either:
  - `CURRENT_SESSION_WORKSPACE` (ephemeral session scratch), or
  - `UA_ARTIFACTS_DIR` (durable artifacts)
- It then does `os.makedirs(..., exist_ok=True)` and writes a UTF-8 file.

This function should be milliseconds for the sizes in this run.

## Timing Analysis (Where The Time Went)
### Run duration (from session artifacts)
There are three relevant duration figures recorded:
- `trace.json.total_duration_seconds`: **266.825s**
- `transcript.md` duration: **266.825s**
- `run.log` execution summary: **241.581s**
- `session_checkpoint.json.execution_time_seconds`: **270.05s**

Interpretation:
- `~266.8s` appears to be the canonical “agent run” wall duration.
- The other numbers likely include/exclude post-run bookkeeping (e.g., transcript generation, memory indexing).

### Tool call durations (from `trace.json`)
`trace.json` includes `time_offset_seconds` for each tool call and its paired tool result (`tool_use_id` join).
Below are the **longest tool round-trips** in this run (dt = result_offset - call_offset):

| dt (s) | Tool | Notes |
|---:|---|---|
| 51.831 | `mcp__internal__write_text_file` | writing X `request.json` (245 chars) |
| 39.765 | `mcp__internal__write_text_file` | writing Reddit `request.json` (131 chars) |
| 33.015 | `Bash` | Grok X fetch via `uv run ...grok_x_trends.py` |
| 4.072 | `mcp__internal__write_text_file` | writing X `result.json` (~5.9k chars) |
| 3.107 | `mcp__internal__write_text_file` | writing Reddit `result.json` (~5.0k chars) |

Aggregate (trace-derived):
- `mcp__internal__write_text_file`: **~98.9s total across 6 calls**
- Total run: **266.8s**
- Share of run: **~37%**

### Why this feels slow (even if the filesystem is fast)
Each tool call is a synchronization boundary:
- The agent emits a tool request.
- The host executes the tool.
- The agent loop must then continue from a new model step (or equivalent orchestration state).

When a workflow writes 6 files as 6 separate tool calls, you pay the overhead 6 times.

In this run, that overhead was unusually high for two small writes (51.8s and 39.8s). That strongly suggests the latency is not “writing bytes,” but either:
- Tool invocation/transport overhead (MCP server call path, tracing hooks, etc.), and/or
- LLM/tool-loop coordination overhead (a model step boundary that was slow at that moment), and/or
- A transient stall (contention, blocking I/O elsewhere, or provider hiccup) that happened to coincide with those tool calls.

## Are We Doing Something Unnecessary?
From a latency perspective, yes:
- Writing `request.json`, `result.json`, and `manifest.json` via **3 separate tool calls per source** is high overhead for small payloads.
- The write calls are not providing additional value beyond persistence and auditability (both can be preserved with fewer calls).

The run also includes a `Read` of the Grok skill file at runtime to decide what to do. That is a normal “skill discovery” pattern, but it adds another tool call boundary (small in this run: ~0.09s).

## Do We Have To Use MCP For File Writing?
**No, but the host must do the writing somehow.** Options differ by tradeoffs:

### Option 1: Native `Write` tool (non-MCP) when available
Pros:
- Potentially simpler path than MCP bridging in some environments.
Cons:
- Still a tool boundary per file unless you batch.
- Some runtimes restrict `Write`, which is why UA includes the MCP fallback in prompts.

### Option 2: Batch writes in a single tool call
Concept:
- Add a “write many files” tool surface (one tool call writes multiple `{path, content}` pairs).
Pros:
- Minimizes tool boundaries (best latency win without changing the overall security model).
Cons:
- Requires new tool schema + implementation and guardrail review.

### Option 3: Write from within existing `Bash` calls (single command)
Concept:
- Since this run already uses `Bash`, it could write all required JSON files using one `Bash` tool call (here-documents).
Pros:
- Reduces tool-call count with no new tool surface.
Cons:
- Still a tool boundary; also more brittle if content needs escaping.

### Option 4: Host-side persistence (no LLM tool calls for writing)
Concept:
- Treat “save work products” as a deterministic host responsibility:
  - Tool results and/or structured intermediate objects get written to `work_products/` automatically.
Pros:
- Eliminates tool-boundary overhead entirely for persistence.
- Strongest latency improvement.
Cons:
- Requires a clear contract about what to persist, naming conventions, and when.

## Recommended Next Investigation (No Code Changes Yet)
To pinpoint whether the big write delays are “tool transport” vs “model step boundary”:
1. Open Logfire trace `019c5c8a75ac98bab2678467671169a3` and filter spans around offsets:
   - ~122s to ~174s (first slow write)
   - ~186s to ~226s (second slow write)
2. Check whether time is dominated by:
   - `llm_api_wait` (suggests model/provider slowness), or
   - `tool_use` / `tool_result` spans (suggests tool execution path), or
   - anything else (locks, observer activity, memory indexing).

If `llm_api_wait` dominates those windows, the cure is still the same: **fewer tool boundaries** (batching or host-side persistence) because each boundary requires another model step.

## Appendix A: Tool Timeline (From `trace.json`)
Tool call offsets for `mcp__internal__write_text_file`:
- 122.508s: X request.json
- 174.485s: X result.json
- 178.566s: X manifest.json
- 186.605s: Reddit request.json
- 226.512s: Reddit result.json
- 229.664s: Reddit manifest.json

## Appendix B: Primary Source Files
- `AGENT_RUN_WORKSPACES/session_20260214_082443_b851a68c/run.log`
- `AGENT_RUN_WORKSPACES/session_20260214_082443_b851a68c/transcript.md`
- `AGENT_RUN_WORKSPACES/session_20260214_082443_b851a68c/trace.json`
- `AGENT_RUN_WORKSPACES/session_20260214_082443_b851a68c/session_checkpoint.json`
- `AGENT_RUN_WORKSPACES/session_20260214_082443_b851a68c/work_products/social/`

