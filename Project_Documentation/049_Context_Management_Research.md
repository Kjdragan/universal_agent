# Context Management Research Report

**Date:** 2026-02-04  
**Issue:** Token bloat across multi-message Telegram sessions causing 206k+ context overflow errors

---

## Executive Summary

The Universal Agent's Telegram bot resumes sessions by default, causing conversation history to accumulate until exceeding the 202k token limit. This report analyzes Clawbot's context management strategies and UA's existing capabilities, then provides actionable recommendations.

**Key Finding:** UA already has significant context management infrastructure (`context_summarizer.py`) that isn't being leveraged by the Telegram bot.

---

## Current Architecture

### Telegram Bot Session Handling (`agent_adapter.py`)

```python
async def _get_or_create_session(self, user_id: str):
    session_id = f"tg_{user_id}"
    try:
        return await self.gateway.resume_session(session_id)  # <-- RESUMES
    except ValueError:
        return await self.gateway.create_session(...)
```

**Problem:** Each Telegram message from the same user adds to the same session's context window. Over multiple queries, this accumulates until hitting token limits.

---

## Clawbot's Context Management Strategies

### 1. Context Window Guard (`context-window-guard.ts`)

Proactive monitoring and warnings:

- **Hard minimum:** 16,000 tokens - blocks if below
- **Warn threshold:** 32,000 tokens - triggers warning
- Configurable per-provider/model via `modelsConfig`

```typescript
export const CONTEXT_WINDOW_HARD_MIN_TOKENS = 16_000;
export const CONTEXT_WINDOW_WARN_BELOW_TOKENS = 32_000;
```

### 2. Memory Flush (`memory-flush.ts`)

**Pre-compaction memory preservation:**

- Triggers when approaching context limits
- Prompts agent to save durable memories to `memory/YYYY-MM-DD.md`
- Runs **before** auto-compaction to preserve critical info

```typescript
export const DEFAULT_MEMORY_FLUSH_PROMPT = [
  "Pre-compaction memory flush.",
  "Store durable memories now (use memory/YYYY-MM-DD.md).",
  "If nothing to store, reply with <SILENT>.",
].join(" ");
```

**Key Parameters:**

- `softThresholdTokens`: 4,000 (trigger threshold before context limit)
- `reserveTokensFloor`: Tokens reserved for compaction overhead

### 3. Context Pruning (`context-pruning/pruner.ts`)

**Intelligent tool result pruning:**

- Soft trim: Keep head/tail of large tool results, truncate middle
- Hard clear: Replace old tool results with placeholder text
- Protects recent N assistant turns from pruning
- Protects content before first user message (identity files)

```typescript
// Soft trimming preserves head + tail
const trimmed = `${head}\n...\n${tail}`;
const note = `[Tool result trimmed: kept first ${headChars} and last ${tailChars} of ${rawLen} chars.]`;

// Hard clear replaces with placeholder
const cleared = { ...msg, content: [asText(settings.hardClear.placeholder)] };
```

**Pruning Ratios:**

- `softTrimRatio`: Trigger soft trimming when context > X% full
- `hardClearRatio`: Trigger hard clearing when context > Y% full

### 4. Slash Commands

User-accessible controls:

- `/status` - View context utilization
- `/context list` - Breakdown of context usage
- `/compact` - Manual compaction trigger

---

## UA's Existing Infrastructure

### Context Summarizer (`urw/context_summarizer.py`)

**Already implemented but not used by Telegram:**

```python
@dataclass
class ContextCheckpoint:
    checkpoint_id: str
    session_id: str
    original_request: str
    completed_tasks: List[str]
    pending_tasks: List[str]
    artifacts: List[Dict]
    subagent_results: List[Dict]  # Critical - lost on compaction
    learnings: List[str]
    failed_approaches: List[str]
    
    def to_injection_prompt(self, max_length: int = 4000) -> str:
        """Format checkpoint for re-injection into fresh context."""
```

**PreCompact Hook:**

```python
async def pre_compact_checkpoint_hook(hook_input, summarizer, state_manager):
    checkpoint = summarizer.capture_from_state(...)
    summarizer.save_checkpoint(checkpoint)  # Saves to .urw/checkpoints/
    return {"continue_": True, "systemMessage": checkpoint.to_injection_prompt()}
```

### What UA Has vs Clawbot

| Feature | Clawbot | UA | Status |
|---------|---------|-----|--------|
| PreCompact hook | ✅ | ✅ | Exists but unused by Telegram |
| Memory flush to disk | ✅ | ⚠️ | Checkpoints exist, no YYYY-MM-DD.md pattern |
| Tool result pruning | ✅ | ❌ | Not implemented |
| Context window guard | ✅ | ❌ | Not implemented |
| `/compact` command | ✅ | ❌ | Not implemented |
| `/status` context view | ✅ | ❌ | Not implemented |

---

## Recommendations

### Option A: Fresh Session Per Message (Simple)

**Pros:** Simple, guaranteed no overflow  
**Cons:** No conversational continuity

```python
# Always create new session, inject last checkpoint
async def _get_or_create_session(self, user_id: str):
    checkpoint = self._load_latest_checkpoint(user_id)
    session = await self.gateway.create_session(...)
    if checkpoint:
        # Inject as system message
        await session.inject_context(checkpoint.to_injection_prompt())
    return session
```

### Option B: Checkpoint-Based Session Reset (Recommended)

**How it works:**

1. After each query completes, capture checkpoint via `ContextSummarizer`
2. On next message, **create fresh session** but inject the checkpoint
3. User gets continuity via injected context (~2-4k tokens) instead of full history

**Benefits:**

- Bounded context usage (~10-20k tokens per query cycle)
- Continuity preserved via structured checkpoint
- Sub-agent results, artifacts, learnings all preserved
- Failed approaches prevent repetition

**Implementation:**

1. **End-of-run hook** in `agent_adapter.py`:

```python
async def _on_run_complete(self, session, result):
    summarizer = ContextSummarizer(session.workspace_path)
    checkpoint = summarizer.capture_from_state(
        state_manager=result.state_manager,
        trigger="query_complete",
        session_id=session.session_id
    )
    summarizer.save_checkpoint(checkpoint)
```

1. **Session creation** with checkpoint injection:

```python
async def _get_or_create_session(self, user_id: str):
    workspace = f"AGENT_RUN_WORKSPACES/tg_{user_id}"
    summarizer = ContextSummarizer(workspace)
    last_checkpoint = summarizer.load_checkpoint()
    
    # Always fresh session
    session = await self.gateway.create_session(...)
    
    if last_checkpoint:
        # Inject ~2-4k token summary of prior work
        await session.inject_system_context(
            last_checkpoint.to_injection_prompt(max_length=4000)
        )
    
    return session
```

### Option C: Implement Tool Result Pruning (Advanced)

Port Clawbot's context-pruning logic to UA:

- Soft-trim large tool results (keep head/tail)
- Hard-clear old tool results when context > 80%
- Protect recent N turns

**Complexity:** High - requires SDK message access  
**Recommendation:** Defer unless Option B insufficient

### Option D: Telegram Slash Commands

Add commands for user control:

- `/reset` - Clear session, start fresh
- `/status` - Show context usage
- `/compact` - Trigger manual compaction (if SDK supports)

---

## Implementation Priority

1. **Immediate (Low effort):** Checkpoint-based session reset (Option B)
   - Leverage existing `ContextSummarizer`
   - ~2 hours of work

2. **Short-term:** Add `/reset` command (Option D partial)
   - Simple user escape hatch
   - ~30 minutes

3. **Medium-term:** Context usage tracking
   - Track tokens per session
   - Warn when approaching limits

4. **Long-term:** Tool result pruning (Option C)
   - Only if Option B proves insufficient

---

## Conclusion

UA already has the core infrastructure (`ContextSummarizer`, `ContextCheckpoint`, PreCompact hook) — it just isn't wired into the Telegram bot. The recommended approach is **Option B: Checkpoint-Based Session Reset**, which:

1. Guarantees bounded context usage
2. Preserves conversational continuity via structured checkpoints
3. Leverages existing UA code with minimal changes

This approach mirrors Clawbot's philosophy of "flush to disk, inject on resume" but with simpler implementation since we're not trying to persist full conversation history.
