# 05 Independent Clawdbot Memory Parity Assessment (2026-02-07)

## Objective

Perform an independent, code-level investigation of Clawdbot memory capabilities versus Universal Agent (UA), focused on:

1. Pre-compaction memory flush.
2. Session memory search across past conversations.

No production code changes were made as part of this assessment.

## Scope and Method

Reviewed implementation source in:

- Clawdbot repo: `/home/kjdragan/lrepos/clawdbot`
- UA repo: `/home/kjdragan/lrepos/universal_agent`

Primary evidence files are listed in the findings below.
To the extent that we have differing implementations because of structure, for example, type Python backend that we're using versus other approaches in Cloudbot, I understand we're going to have to do our own implementation. So that's fine. Also, we may have different functionality, and rather than just deleting it all, I'd like to potentially be able to keep some of that functionality dormant. but then design an actual working pipeline that you outlined that you cagree on. And this allows us to potentially change our approach in the future. For example, right now we have a connection to Leta, which is a memory management system, but we just haven't worked through it. I don't want to necessarily delete it. Right now we have a parameter that makes it dormant. So I'd like to be able to, for example, keep something like that and just keep it off and not consider it right now as we develop, but not actually remove it from our system. You can comment, add comments around it to describe what we're doing, but I'd rather not just remove it right

## Findings: Clawdbot

### 1) Pre-compaction memory flush is real and active

Clawdbot has an explicit memory flush subsystem that runs a dedicated agent turn before compaction:

- Settings resolution: `src/auto-reply/reply/memory-flush.ts`
  - `enabled` defaults to `true` when unset (`resolveMemoryFlushSettings`, line 40).
  - Default soft threshold is 4000 tokens (`DEFAULT_MEMORY_FLUSH_SOFT_TOKENS`, line 8).
- Execution path: `src/auto-reply/reply/agent-runner.ts:202`
  - `runMemoryFlushIfNeeded(...)` is called before normal run execution.
- Flush behavior: `src/auto-reply/reply/agent-runner-memory.ts`
  - Runs a separate embedded agent turn (`runEmbeddedPiAgent`) with memory-flush prompt/system prompt.
  - Skips in read-only sandbox, heartbeat, and CLI-provider runs.
  - Tracks `memoryFlushAt` and `memoryFlushCompactionCount` in session state.

Result: this is not a stub; it is production-grade and wired into the turn pipeline.

### 2) Session memory search exists and is explicitly gated

Clawdbot implements session transcript indexing/search, but it is opt-in:

- Default config behavior: `src/agents/memory-search.ts`
  - `experimental.sessionMemory` defaults to `false` (line 125-126).
  - `sources` default to `["memory"]` (line 87).
  - Session source is only accepted when `sessionMemory` is enabled (line 99).
- Session indexing implementation: `src/memory/manager.ts`
  - Session transcript listener is only enabled when `sources` includes `"sessions"` (line 843-845).
  - Session files are indexed under source `"sessions"` (line 1199).
  - Session transcripts are read from agent session `.jsonl` files and converted into searchable text (line 1538-1638).

The docs match this behavior:

- `docs/concepts/memory.md:441-452` shows required config:
  - `experimental.sessionMemory: true`
  - `sources: ["memory", "sessions"]`

### 3) Accuracy check on the quoted claim

Claim in the prompt context: "By default, the 2 best Clawd memory features are turned OFF."

Current code reality:

- Memory flush: default ON.
- Session memory search: default OFF (experimental).

So the claim is partially outdated for the current Clawdbot codebase.

## Findings: Universal Agent

### 1) UA already has pre-compaction capture, but behavior differs

UA has a pre-compact hook wired into default hooks:

- Hook registration: `src/universal_agent/agent_setup.py:628-630`
- Hook implementation: `src/universal_agent/agent_core.py:586-672`
  - Invokes `flush_pre_compact_memory(...)` when memory is enabled.
- Flush implementation: `src/universal_agent/memory/memory_flush.py:29-48`
  - Stores transcript tail snapshot into memory entries (deterministic capture).

This gives UA compaction-adjacent persistence, but not the same agentic summarization turn architecture as Clawdbot's flush runner.

### 2) UA has an additional memory flush sub-agent path

UA also contains a context-reset memory sub-agent flow:

- `src/universal_agent/main.py:5093-5165`
- Triggered before history clear in overflow/reset path:
  - `src/universal_agent/main.py:5437`

This is agentic (tool-using) but occurs in reset flow, not the same structured pre-compaction cycle semantics used by Clawdbot.

### 3) UA memory search is present, but session transcript search parity is incomplete

UA has searchable memory for `MEMORY.md` and `memory/`:

- Tooling: `src/universal_agent/tools/memory.py:110-137`
- Store/index: `src/universal_agent/memory/memory_store.py` and `memory_index.py`

However, true "session transcript memory search" parity is not fully wired end-to-end:

- `Memory_System.manager.transcript_index(...)` exists (`Memory_System/manager.py:211-217`),
  but runtime usage appears absent (search only found tests).
- `mcp_server.archival_memory_search` currently returns a stub response directing users to `ua_memory_search`:
  - `src/mcp_server.py:1409`

## Parity Assessment

### Memory Flush Parity

Status: **Partial parity (medium-high)**.

Why:

- UA already captures memory pre-compaction and on exit/overflow paths.
- Clawdbot still has stronger integrated behavior for "silent dedicated memory turn before compaction," with explicit per-compaction metadata and tighter gating logic in one pipeline.

### Session Memory Search Parity

Status: **Foundational capability present, product parity not complete**.

Why:

- UA has memory infrastructure, vector backends, and indexing primitives.
- UA does not currently present a Clawdbot-equivalent, first-class "session transcript indexing + retrieval source control" flow integrated into normal memory search behavior.

## Feasibility

Feasibility to reach Clawdbot-equivalent behavior in UA is **high**.

Key reason:

- Core primitives already exist in UA:
  - pre-compact hooks,
  - memory persistence formats,
  - vector search backends,
  - transcript-related indexing helper in `Memory_System`.

Primary work is integration and unification, not net-new architecture.

## Recommended Path (No Code Changes Applied Here)

1. Define one canonical memory pipeline (today UA has overlapping memory surfaces: `universal_agent.memory`, `Memory_System`, and MCP-facing wrappers).
2. Add a configurable session source model like Clawdbot:
   - `memorySearch.sources`
   - `experimental.sessionMemory`
   - explicit defaults and gating.
3. Decide desired flush mode:
   - deterministic transcript-tail capture,
   - agentic pre-compaction summarization turn,
   - or hybrid (recommended).
4. Replace/complete stub memory search paths (example: `archival_memory_search` MCP surface).
5. Add observability parity:
   - per-compaction flush counters,
   - session indexing freshness metrics,
   - source-level status reporting.

## Conclusion

Clawdbot definitely contains the two discussed memory features. In current code, only session memory search is off by default; memory flush is already on by default. Universal Agent can achieve practical parity without major re-architecture, but should unify its memory pathways and explicitly productize session transcript indexing/search as a first-class capability.
