# Long-Running Task Extensions Design

**Document Version**: 1.0  
**Date**: 2026-01-05  
**Status**: Design Proposal (Needs Review)  
**Author**: AI Architect  

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current State Assessment](#current-state-assessment)
3. [Gap Analysis by Runtime Duration](#gap-analysis-by-runtime-duration)
4. [Proposed Architecture Extensions](#proposed-architecture-extensions)
5. [Implementation Roadmap](#implementation-roadmap)
6. [Risk Assessment](#risk-assessment)
7. [Appendix: Reference Materials](#appendix-reference-materials)

---

## Executive Summary

This document proposes extensions to the Universal Agent's durability system to support **longer-running autonomous tasks** spanning 1 hour to 24+ hours. The goal is to evolve from a "session-with-recovery" model to a "durable workflow engine" that can:

- Run multi-phase research and report generation autonomously for hours
- Survive restarts, crashes, and network outages without duplicate side effects
- Maintain goal coherence and prevent drift over extended periods
- Support always-on monitoring and triggered execution

### Key Insight

The Universal Agent already has a **solid durability foundation** (Phases 0-4 complete). The next step is not rebuilding—it's **layering phase-gated autonomy** inspired by the Ralph Wiggum iterative loop pattern, while leveraging our existing:

- Lease/heartbeat primitives in `state.py`
- Idempotency keys and receipts in `ledger.py`
- Step-boundary checkpoints in `checkpointing.py`
- Agent College background monitoring

---

## Current State Assessment

### What We Have (Durability Phases 0-4)

| Component | Location | Capability |
|-----------|----------|------------|
| **Run/Step Model** | `durable/state.py` | Run lifecycle, step tracking, status transitions |
| **Lease/Heartbeat** | `durable/state.py` | `acquire_run_lease()`, `heartbeat_run_lease()`, `release_run_lease()` |
| **Tool Ledger** | `durable/ledger.py` | Idempotency keys, side-effect classification, receipts, replay policies |
| **Checkpointing** | `durable/checkpointing.py` | Phase anchors: `pre_read_only`, `pre_side_effect`, `post_replay` |
| **Replay Orchestration** | `main.py` | `reconcile_inflight_tools()`, `_build_forced_tool_prompt()` |
| **Worker Mode** | `worker.py` | Background runner with lease acquisition |
| **Operator CLI** | `agent_operator/` | `ua runs list/show/tail/cancel` |
| **Agent College** | `agent_college/runner.py` | Background failure monitoring and critique |
| **Letta Memory** | Integration in `main.py` | Persistent sub-agent memory blocks |

### Current Runtime DB Schema (Already Implemented)

```sql
-- runs: lease_owner, lease_expires_at, last_heartbeat_at, cancel_requested_at
-- run_steps: step_id, run_id, step_index, phase, status
-- tool_calls: idempotency_key, replay_policy, side_effect_class, receipts
-- checkpoints: checkpoint_type, state_snapshot_json, cursor_json
```

### Ralph Wiggum Pattern Analysis

The [Ralph Wiggum plugin](https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum) provides:

1. **Stop Hook Interception**: Catches agent exit and re-injects the same prompt
2. **Completion Promise**: A marker phrase (`<promise>...`) that signals genuine completion
3. **Iteration Limit**: `--max-iterations` to prevent runaway loops
4. **Self-Referential Feedback**: Agent sees its previous work (via files/git) each iteration

**Key Takeaway**: Ralph Wiggum achieves long-running behavior through **prompt reinjection**—not persistent state. We can adapt this pattern while layering it on our durable state infrastructure.

---

## Gap Analysis by Runtime Duration

### Capability Matrix: Current vs Required

| Duration | Current State | Gap |
|----------|--------------|-----|
| **≈30 min** | ✅ Fully supported | None—existing durability handles retries, checkpoints, idempotency |
| **≈1 hour** | ⚠️ Partial | Missing: Phase-gated loop control, automatic phase transitions, drift detection |
| **2-8 hours** | ⚠️ Limited | Missing: Auth refresh, saga/compensation, phase-gated critic loops |
| **24+ hours** | ❌ Not supported | Missing: Triggers/cron, memory hierarchy consolidation, always-on service model |

### Detailed Gap Breakdown

#### A. Phase-Gated Loop Control (Missing)

**Problem**: Current system runs until agent says "done" or hits budget limits. No structured phases.

**Needed**: Explicit phase transitions (Research → Synthesis → Delivery) with validation gates between phases.

#### B. Automatic Continuation (Missing)

**Problem**: Ralph Wiggum's "rerun same prompt" pattern isn't implemented. After task completion, system stops.

**Needed**: Hook-based continuation that re-injects work until a completion condition is met.

#### C. Drift Prevention (Missing)

**Problem**: Over long runs, agent may forget constraints or invent subgoals.

**Needed**: Run spec reinjection at phase boundaries; immutable constraints stored separately from conversation.

#### D. Auth Lifecycle (Not Implemented)

**Problem**: OAuth tokens expire during multi-hour runs (Composio, Gmail).

**Needed**: Auth broker pattern or escalation to "waiting_for_auth" state.

#### E. Triggers/Cron (Not Implemented)

**Problem**: No way to start runs automatically or react to external events.

**Needed**: Schedule-based and event-based run creation.

#### F. Memory Consolidation (Partial)

**Problem**: Letta memory grows unbounded; no consolidation or staleness tracking.

**Needed**: Background consolidation job, memory provenance, and decay.

---

## Proposed Architecture Extensions

### Approach: Incremental Layers (Not Rebuild)

We propose **3 extension phases** that build on existing infrastructure:

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE C: Triggers + Always-On (24h+)                           │
│   - Cron scheduler, event triggers, memory consolidation       │
├─────────────────────────────────────────────────────────────────┤
│ PHASE B: Phase-Gated Autonomy (1-8 hours)                      │
│   - Phase controller, drift prevention, auth refresh           │
├─────────────────────────────────────────────────────────────────┤
│ PHASE A: Loop Harness (Ralph Wiggum-style) (30min-1hr)         │
│   - SubagentStop hook continuation, completion promise         │
├─────────────────────────────────────────────────────────────────┤
│ EXISTING: Durability System (Phases 0-4)                       │
│   - Runs, steps, ledger, checkpoints, replay, worker           │
└─────────────────────────────────────────────────────────────────┘
```

---

### Phase A: Loop Harness (Ralph Wiggum Adaptation)

**Goal**: Enable iterative, autonomous loops for extended single-objective tasks.

#### A1. Completion Promise Pattern

Adapt Ralph Wiggum's completion promise to our hook system:

```python
# New: Completion condition stored in run_spec
run_spec = {
    "objective": "Create comprehensive AI research report",
    "completion_promise": "TASK_COMPLETE: Report delivered to user",
    "max_iterations": 10,
    "max_wallclock_seconds": 7200,  # 2 hours
}

# Hook: on_agent_stop (new hook or extend SubagentStop)
def on_agent_stop(context: StopHookContext) -> StopResult:
    run_spec = load_run_spec(context.run_id)
    
    # Check completion promise in output
    if run_spec["completion_promise"] in context.final_output:
        return StopResult(action="complete", reason="promise_fulfilled")
    
    # Check iteration limit
    if get_iteration_count(context.run_id) >= run_spec["max_iterations"]:
        return StopResult(action="stop", reason="max_iterations_reached")
    
    # Continue: reinject prompt
    return StopResult(
        action="continue",
        next_prompt=build_continuation_prompt(run_spec, context)
    )
```

#### A2. Continuation Prompt Injection

```python
def build_continuation_prompt(run_spec: dict, context: StopHookContext) -> str:
    """Build the next iteration's prompt with context"""
    return f"""
You are continuing an autonomous task. Progress so far is visible in your workspace.

## Original Objective
{run_spec["objective"]}

## Completion Condition
When you are COMPLETELY done, output exactly: {run_spec["completion_promise"]}

## Current Progress
Iteration: {context.iteration} of {run_spec["max_iterations"]}
Elapsed: {context.elapsed_seconds}s of {run_spec["max_wallclock_seconds"]}s
Last action: {context.last_tool_name}

## Instructions
Continue from where you left off. Review your previous work in the workspace.
Do NOT start over. Build on what exists.
"""
```

#### A3. Budget Controller Enhancement

Extend existing budget tracking to support iteration counting:

```python
# New columns in runs table (migration)
ALTER TABLE runs ADD COLUMN iteration_count INTEGER DEFAULT 0;
ALTER TABLE runs ADD COLUMN max_iterations INTEGER;
ALTER TABLE runs ADD COLUMN completion_promise TEXT;
```

#### A4. New Slash Command: `/long-task`

```bash
/long-task "Create comprehensive AI industry report and email to user" \
  --max-iterations 15 \
  --max-hours 4 \
  --completion-promise "TASK_COMPLETE"
```

---

### Phase B: Phase-Gated Autonomy

**Goal**: Structure long runs into explicit phases with validation gates.

#### B1. Phase Controller

```python
# New: Phase definitions for complex workflows
RESEARCH_REPORT_PHASES = [
    Phase(
        name="research",
        objective="Search and crawl sources",
        success_criteria="search_results/*.md contains >= 5 files",
        max_steps=50,
        tools_allowed=["COMPOSIO_SEARCH_NEWS", "crawl_parallel"],
    ),
    Phase(
        name="synthesis",
        objective="Create HTML report in work_products/",
        success_criteria="work_products/*.html exists",
        max_steps=30,
        tools_allowed=["finalize_research", "read_research_files", "write_local_file"],
    ),
    Phase(
        name="delivery",
        objective="Upload and email report",
        success_criteria="GMAIL_SEND_EMAIL succeeded",
        max_steps=20,
        tools_allowed=["upload_to_composio", "GMAIL_SEND_EMAIL"],
    ),
]
```

#### B2. Phase Transition Logic

```python
def check_phase_completion(run_id: str, phase: Phase) -> PhaseResult:
    """Evaluate if phase success criteria are met"""
    workspace = get_workspace(run_id)
    
    if phase.success_criteria.startswith("search_results"):
        # Check file count
        files = glob.glob(f"{workspace}/search_results/*.md")
        required = int(re.search(r">= (\d+)", phase.success_criteria).group(1))
        if len(files) >= required:
            return PhaseResult(passed=True)
        return PhaseResult(passed=False, reason=f"Only {len(files)} files, need {required}")
    
    # ... other criteria checks
```

#### B3. Phase-Gated Critic Loop (Agent College Integration)

At each phase boundary, invoke the Critic agent:

```python
def on_phase_boundary(run_id: str, completed_phase: str, next_phase: str):
    """Run critic at phase boundaries, not every step"""
    
    # 1. Save phase checkpoint
    save_checkpoint(
        run_id=run_id,
        checkpoint_type="phase_boundary",
        phase_completed=completed_phase,
    )
    
    # 2. Invoke Critic for quality check
    critique = critic_agent.evaluate_phase(
        run_id=run_id,
        phase=completed_phase,
        workspace=get_workspace(run_id),
    )
    
    # 3. Decide: proceed, retry, or escalate
    if critique.result == "pass":
        return PhaseAction.PROCEED
    elif critique.retry_suggested and get_phase_retries(run_id, completed_phase) < 2:
        return PhaseAction.RETRY_PHASE
    else:
        return PhaseAction.ESCALATE_TO_HUMAN
```

#### B4. Drift Prevention: Run Spec Reinjection

```python
def build_phase_prompt(run_spec: dict, phase: Phase, context: PhaseContext) -> str:
    """Reinject immutable spec at phase boundaries to prevent drift"""
    return f"""
## IMMUTABLE RUN SPECIFICATION
{json.dumps(run_spec["constraints"], indent=2)}

## CURRENT PHASE: {phase.name}
{phase.objective}

## SUCCESS CRITERIA
{phase.success_criteria}

## PREVIOUS PHASE SUMMARY
{context.previous_phase_summary}

## FORBIDDEN ACTIONS
- Do NOT re-execute tools from previous phases
- Do NOT deviate from the phase objective
- Do NOT start over—build on existing work
"""
```

#### B5. Auth Refresh Handler

```python
class AuthHandler:
    """Centralized auth lifecycle management"""
    
    def check_token_validity(self, provider: str) -> TokenStatus:
        # Check with Composio if token is still valid
        pass
    
    def on_auth_required(self, run_id: str, provider: str):
        """Handle auth expiry during run"""
        # 1. Checkpoint current state
        save_checkpoint(run_id, checkpoint_type="pre_auth_refresh")
        
        # 2. Transition to waiting state
        update_run_status(run_id, status="waiting_for_auth")
        
        # 3. Log escalation
        logfire.warning("auth_required", run_id=run_id, provider=provider)
        
        # (User must re-auth via Composio link, then resume)
```

---

### Phase C: Triggers + Always-On (24h+)

**Goal**: Transform from "runs" to "service" model with scheduled/event-driven execution.

#### C1. Schedule Table (New)

```sql
CREATE TABLE schedules (
    schedule_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    cron_expr TEXT,                    -- e.g., "0 8 * * 1" = Monday 8am
    target TEXT NOT NULL,              -- "create_run" | "resume_run"
    run_spec_json TEXT NOT NULL,       -- Objective, constraints, etc.
    enabled INTEGER DEFAULT 1,
    last_fired_at TEXT,
    next_fire_at TEXT
);
```

#### C2. Trigger Table (New)

```sql
CREATE TABLE triggers (
    trigger_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,              -- "telegram" | "email" | "webhook"
    filter_json TEXT,                  -- JSON filter for matching events
    action TEXT NOT NULL,              -- "create_run" | "append_to_run"
    run_spec_json TEXT NOT NULL,
    enabled INTEGER DEFAULT 1
);
```

#### C3. Scheduler Worker

```python
async def scheduler_loop():
    """Background scheduler for cron-based runs"""
    while True:
        now = datetime.now(timezone.utc)
        
        # Find due schedules
        due = conn.execute("""
            SELECT * FROM schedules 
            WHERE enabled = 1 AND next_fire_at <= ?
        """, (now.isoformat(),)).fetchall()
        
        for schedule in due:
            # Create run from schedule
            run_id = create_run_from_schedule(schedule)
            
            # Update next_fire_at
            next_fire = compute_next_fire(schedule["cron_expr"], now)
            conn.execute("""
                UPDATE schedules 
                SET last_fired_at = ?, next_fire_at = ?
                WHERE schedule_id = ?
            """, (now.isoformat(), next_fire.isoformat(), schedule["schedule_id"]))
        
        await asyncio.sleep(60)  # Check every minute
```

#### C4. Memory Consolidation Job

```python
async def memory_consolidation_job(user_id: str):
    """Periodic memory cleanup and consolidation"""
    
    # 1. Find stale memories (not accessed in 30 days)
    stale = memory_manager.find_stale_memories(user_id, days=30)
    
    # 2. Summarize and archive
    for memory in stale:
        summary = llm_summarize(memory.content)
        memory_manager.archive_memory(memory.id, summary)
    
    # 3. Deduplicate active memories
    duplicates = memory_manager.find_duplicates(user_id)
    for dup_group in duplicates:
        merged = llm_merge_memories(dup_group)
        memory_manager.replace_memories(dup_group, merged)
    
    logfire.info("memory_consolidated", user_id=user_id, 
                 archived=len(stale), deduplicated=len(duplicates))
```

---

## Implementation Roadmap

### Recommended Order (Simplest First)

| Phase | Effort | Dependencies | Deliverable |
|-------|--------|--------------|-------------|
| **A1-A2**: Loop Harness | 2-3 days | None | `/long-task` command with completion promise |
| **A3-A4**: Iteration Tracking | 1 day | A1-A2 | DB migration, CLI command |
| **B1-B2**: Phase Controller | 3-5 days | A complete | Phase definitions, transition logic |
| **B3**: Critic Integration | 2 days | B1-B2, Agent College | Phase-gated quality checks |
| **B4**: Drift Prevention | 1 day | B1-B2 | Run spec reinjection |
| **B5**: Auth Refresh | 2-3 days | None | `waiting_for_auth` state handling |
| **C1-C2**: Schedule/Trigger Tables | 1 day | None | DB schema |
| **C3**: Scheduler Worker | 2-3 days | C1-C2 | Background scheduler |
| **C4**: Memory Consolidation | 3-5 days | Letta integration | Consolidation job |

### Phase A Minimum Viable Implementation

For the simplest possible start, implement only:

1. **SubagentStop hook modification** to check completion promise
2. **Prompt reinjection** with workspace context
3. **Iteration counter** in runs table
4. **`/long-task` slash command** or CLI flag

This gives you Ralph Wiggum-style looping behavior in ~2-3 days.

---

## Risk Assessment

### Technical Risks

| Risk | Mitigation |
|------|------------|
| Runaway loops (infinite iteration) | Hard max_iterations limit; wallclock budget; same-error counter |
| Context bloat | Reinject only run_spec summary, not full history |
| Auth token expiry | `waiting_for_auth` state; proactive refresh checks |
| Duplicate side effects on loop boundaries | Existing idempotency keys handle this |
| Agent drift/hallucination | Run spec reinjection; phase-gated critic validation |

### Operational Risks

| Risk | Mitigation |
|------|------------|
| Long runs consume resources | Worker lease timeouts; concurrency limits |
| Debugging multi-hour runs | Logfire tracing already in place; add phase-boundary spans |
| User expectation mismatch | Clear progress reporting; phase notifications |

---

## Appendix: Reference Materials

### A. Ralph Wiggum Plugin (Anthropic)

- **Repository**: [anthropics/claude-code/plugins/ralph-wiggum](https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum)
- **Pattern**: Stop hook interception → prompt reinjection → completion promise check
- **Commands**: `/ralph-loop`, `/cancel-ralph`

### B. Current Universal Agent Durability System

| Document | Purpose |
|----------|---------|
| `017_LONG_RUNNING_AGENTS_PROGRESS.md` | Phase 0-4 implementation status |
| `Durability_System_Documentation/` | Code-verified durability docs |
| `durable/state.py` | Runs, steps, leases, heartbeats |
| `durable/ledger.py` | Tool ledger, idempotency, receipts |
| `durable/checkpointing.py` | Phase checkpoints |

### C. Runtime Taxonomy from User Context

| Duration | Key Pressures | Primitives Required |
|----------|--------------|---------------------|
| ≈30 min | Tool failures, context bloat | Run/step model, retries, idempotency |
| ≈1 hour | Crashes, restarts | Checkpoint/resume, worker leases |
| 2-8 hours | Auth expiry, partial failures | Auth broker, saga/compensation |
| 24+ hours | Drift, triggers, memory growth | Cron, events, memory consolidation |

### D. Existing Primitives to Leverage

```python
# Already implemented in state.py:
acquire_run_lease(conn, run_id, lease_owner, lease_ttl_seconds)
heartbeat_run_lease(conn, run_id, lease_owner, lease_ttl_seconds)
release_run_lease(conn, run_id, lease_owner)
is_cancel_requested(conn, run_id)

# Already implemented in ledger.py:
prepare_tool_call(..., idempotency_key)  # Prevents duplicates
mark_succeeded(..., response)            # Creates receipt
get_receipt_by_idempotency(key)          # Lookup for replay

# Already implemented in checkpointing.py:
save_checkpoint(..., checkpoint_type, state_snapshot)
load_last_checkpoint(conn, run_id)
```

---

## Decision Points for User Review

1. **Loop Harness vs Phase Controller First?**
   - Option A: Implement Ralph Wiggum-style loop first (simpler, faster)
   - Option B: Implement phase controller first (more structured)

2. **Completion Promise Location**
   - Option A: Agent outputs `<promise>TASK_COMPLETE</promise>` in text
   - Option B: Agent calls a special `mark_complete` tool
   - Option C: Automatic detection based on workspace state

3. **Phase Definitions**
   - Option A: Hardcoded phase templates for common workflows
   - Option B: User-defined phases in run_spec
   - Option C: LLM-generated phase breakdown at run start

4. **Auth Refresh Strategy**
   - Option A: Proactive refresh (check before long operations)
   - Option B: Reactive escalation (wait for failure, then pause)
   - Option C: Both (proactive check + reactive fallback)

---

**Document Status**: 📝 Ready for Review  
**Next Step**: User approval, then prioritize implementation phases  
