# CODIE Redesign: Claude Code CLI Bridge Architecture

**Created:** 2026-03-07  
**Status:** Architecture Discussion — No implementation yet  
**Scope:** Upgrading VP workers to direct Claude Code CLI sessions, giving UA factories the best of both worlds

---

## 1. Context & Problem Statement

### What We Have Today

Universal Agent (UA) runs on the **Claude Agent SDK** — Anthropic's programmatic SDK for building agent systems. This runtime powers Simone (the primary orchestrator), CODIE (the VP coder), VP General, and all subagents (research-specialist, report-writer, etc.).

The Claude Agent SDK provides:
- Tool calling (MCP servers, Composio, local tools)
- Subagent delegation (`Task` with `subagent_type`)
- Session management, checkpointing, streaming events
- Hook system for guardrails and workflow control

The Claude Agent SDK does **NOT** provide:
- **Agent Teams** — multi-agent coordination with inter-agent messaging
- **Claude Code toolchain** — file editing, git operations, terminal commands as first-class tools
- **Independent context windows per teammate** — all subagents share the parent's context constraints

### What Claude Code CLI Provides

The `claude` CLI (Claude Code) is a separate runtime with capabilities beyond the SDK:

- **Agent Teams** — `TeamCreate`, `SendMessage`, `TaskCreate`, shared task lists, teammate coordination
- **Full development toolchain** — Read, Write, Edit, Bash, Grep, Glob as native tools
- **Skills and MCP servers** — loaded from `.claude/skills/` and `.mcp.json`
- **Autonomous operation** — `claude --print` runs headless, streams JSON output
- **Session management** — `/resume`, `/rewind`, conversation history

### The Gap

Our `modular-research-report-expert` skill requires Agent Teams (6 specialized teammates in a draft-critique-revise pipeline). This skill **cannot run** in the Claude Agent SDK runtime. It needs Claude Code CLI.

CODIE today is misnamed — it runs the same SDK as Simone with no differentiated coding capability. It's essentially Simone with a different workspace guard.

### The Opportunity

By bridging UA's SDK runtime to Claude Code CLI sessions, we get:
1. **Agent Teams for any VP worker** — report pipelines, complex research, parallel exploration
2. **True autonomous coding** — CODIE directing real Claude Code sessions for development projects
3. **Best of both worlds** — UA's orchestration + Claude Code's execution capabilities
4. **General-purpose super-agent access** — Claude Code is much more than a coder; it's an autonomous agent that can do research, analysis, writing, coordination, and development

---

## 2. Core Architectural Concept

### Claude Code as a Capability, Not a Role

The key insight: **Claude Code CLI is a tool that any VP worker can use, not a separate agent identity.**

```
┌─────────────────────────────────────────────────┐
│  UA Gateway (Claude Agent SDK Runtime)           │
│                                                  │
│  Simone (Orchestrator)                           │
│    ├── dispatches mission ──► VP Mission DB      │
│    └── moves on to other work                    │
│                                                  │
└──────────────────┬──────────────────────────────┘
                   │ claims mission
┌──────────────────▼──────────────────────────────┐
│  VP Worker (CODIE or VP General)                 │
│  Running in Claude Agent SDK                     │
│                                                  │
│  Evaluates mission:                              │
│    execution_mode: "sdk" → use ProcessTurnAdapter│
│    execution_mode: "cli" → use ClaudeCodeCLIClient│
│                                                  │
│  If CLI mode:                                    │
│    1. Crafts structured prompt from objective     │
│    2. Launches claude --print subprocess          │
│    3. Monitors JSON output stream                │
│    4. Responds to input requests                 │
│    5. Evaluates result on completion             │
│    6. Reports back via VP mission DB             │
│                                                  │
└──────────────────┬──────────────────────────────┘
                   │ spawns subprocess
┌──────────────────▼──────────────────────────────┐
│  Claude Code CLI (External Process)              │
│                                                  │
│  ✅ Agent Teams (TeamCreate, SendMessage, etc.)  │
│  ✅ Full toolchain (Read, Write, Edit, Bash)     │
│  ✅ Skills (.claude/skills/*)                    │
│  ✅ MCP servers (.mcp.json)                      │
│  ✅ Autonomous execution                         │
│                                                  │
│  Runs independently until:                       │
│    - Completion (exits with result)              │
│    - Needs input (VP worker responds)            │
│    - Error/timeout (VP worker handles)           │
│                                                  │
└─────────────────────────────────────────────────┘
```

### Shared Bridge, Multiple Directors

The `ClaudeCodeCLIClient` is infrastructure shared by all VP workers:

| Director | Directs CLI For | Keeps SDK For |
|----------|----------------|---------------|
| **CODIE** | Coding projects, repo creation, refactoring, implementation | Quick code fixes, simple tasks |
| **VP General** | Report pipelines with Agent Teams, complex multi-agent research, parallel analysis | Standard research, summaries, routine tasks |

The mission payload determines which path:

```json
{
  "mission_type": "report_pipeline",
  "execution_mode": "cli",
  "objective": "Use the modular-research-report-expert skill to generate a publication-quality report from the research corpus at /path/to/refined_corpus.md",
  "payload": {
    "corpus_path": "/path/to/refined_corpus.md",
    "skill": "modular-research-report-expert",
    "timeout_seconds": 3600
  }
}
```

---

## 3. CODIE's Redesigned Role

### What CODIE Was

A VP coder worker running the same Claude Agent SDK runtime as Simone, with workspace isolation. No differentiated capability.

### What CODIE Becomes

A **project director** that can operate Claude Code CLI sessions for complex work:

1. **Receives mission from Simone** via VP mission queue (existing infrastructure)
2. **Evaluates the mission** — does it need CLI capabilities (Agent Teams, full toolchain) or can SDK handle it?
3. **For CLI missions:**
   - Crafts a well-structured prompt from the mission objective
   - Launches `claude --print --output-format stream-json` as a subprocess
   - Sets the working directory to the mission workspace
   - Configures environment (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, etc.)
4. **Monitors but does not micromanage** — reads the JSON output stream for:
   - Errors that need intervention
   - Input requests that need a response (CODIE provides the "user input")
   - Completion signals
5. **Evaluates the result** — inspects output artifacts, decides if the mission succeeded
6. **Reports back** via VP mission DB → Simone picks up the result

### What CODIE Does NOT Do

- Does not write code itself (the CLI does that)
- Does not micromanage the CLI's tool calls
- Does not intercept every step of the CLI's execution
- Does not need to understand the CLI's internal Agent Team coordination

### CODIE as Input Provider

When Claude Code CLI needs "user input" (e.g., clarification, approval, or direction), CODIE acts as the user. This is the supervisory role — CODIE evaluates the CLI's question and provides an informed response based on the original mission objective and any constraints from Simone.

---

## 4. VP General's CLI Access

### The Question

Should VP General also be able to direct Claude Code CLI sessions? Or should only CODIE have this capability?

### The Answer: Both Should Have Access

The Claude Code CLI bridge is **infrastructure**, not identity. Consider:

- Simone asks VP General to produce a research report with Agent Teams
- VP General claims the mission, sees `execution_mode: "cli"`
- VP General uses the same `ClaudeCodeCLIClient` that CODIE uses
- The CLI runs the `modular-research-report-expert` skill with 6 teammates
- VP General receives the result and reports back

There's no reason to force this through CODIE when the task isn't coding-related. The routing decision is:

| Task Nature | Director | Execution Mode |
|------------|----------|---------------|
| Coding project | CODIE | CLI |
| Quick code fix | CODIE | SDK |
| Report with Agent Teams | VP General | CLI |
| Standard research | VP General | SDK |
| Complex multi-agent analysis | VP General | CLI |
| Tutorial repo bootstrap | CODIE | CLI |
| Simple summary | VP General | SDK |

Simone specifies the VP lane AND execution mode when dispatching. Or the VP worker can decide based on the mission objective.

---

## 5. Claude Code Is More Than a Coder

### The Broader Capability

Claude Code CLI with Agent Teams is effectively an **autonomous super-agent**. It can:

- **Research**: spawn researcher teammates who explore different angles simultaneously
- **Write**: spawn writers, editors, and critics in a draft-critique-revise loop
- **Analyze**: spawn analysts who investigate competing hypotheses in parallel
- **Build**: spawn builders who each own different modules of a project
- **Coordinate**: manage shared task lists, inter-teammate messaging, dependency resolution

Our first use case — the modular research report pipeline — is NOT a coding task. It's a multi-agent writing and editorial process. This demonstrates that the CLI bridge is a **general-purpose capability**, not just a coding feature.

### When Claude Code CLI Is Better Than SDK Subagents

| Dimension | SDK Subagents | Claude Code CLI |
|-----------|--------------|----------------|
| **Parallelism** | Sequential (one at a time) | True parallel (Agent Teams) |
| **Context** | Shares parent's constraints | Own context window per teammate |
| **Communication** | Reports back to parent only | Teammates talk to each other |
| **Toolchain** | MCP tools only | Full dev toolchain + MCP |
| **Coordination** | Parent manages all | Self-coordinating via shared task list |
| **Best for** | Focused single tasks | Complex multi-faceted work |

### When SDK Is Better

- Quick, focused tasks (less overhead)
- Tasks that need direct gateway integration (session streaming, WebSocket events)
- Tasks where the result needs to flow into Simone's context immediately
- Cost-sensitive operations (CLI uses more tokens per session)

---

## 6. Rate Limiting & Concurrency Strategy

### ZAI Coding Plan: 5 Concurrent Sessions

This is the hard constraint. Every Claude Code CLI session (including Agent Team teammates) counts as a session.

### Session Budget Allocation

| Consumer | Sessions | Priority | Notes |
|----------|----------|----------|-------|
| Simone (interactive) | 1 | **Always reserved** | User-facing, cannot be preempted |
| CSI Analytics | 1 | Medium | Uses haiku; can pause during heavy CLI work |
| VP Worker (SDK mode) | 1 | Medium | Standard mission execution |
| VP + CLI teammates | 1-3 | Variable | Agent Teams scale session usage |

### "Heavy Mission Mode"

When a VP worker launches a CLI session with Agent Teams:

1. VP requests "heavy mission" allocation from the gateway
2. Gateway pauses non-critical consumers (CSI analytics timers)
3. VP launches CLI with `MAX_CONCURRENT_AGENTS` set to available slots
4. On mission completion, gateway resumes paused consumers

### Dynamic Concurrency Ceiling

The CLI's `MAX_CONCURRENT_AGENTS` should be set dynamically:

```
available_slots = MAX_PLAN_SESSIONS - reserved_simone - active_csi - active_other_vp
cli_max_agents = max(1, available_slots - 1)  # -1 for the VP worker itself
```

With 5 total, Simone reserved (1), CSI paused (0): `5 - 1 - 0 - 0 = 4` slots → `MAX_CONCURRENT_AGENTS=3` (4 minus CODIE itself).

### Second Coding Plan Option

A second ZAI coding plan would cleanly separate:
- **Plan A**: Simone + CSI + VP SDK work (always-on)
- **Plan B**: CODIE/VP General + Claude Code CLI (on-demand)

This eliminates the need for heavy mission mode and CSI pausing. Each plan has its own 5-session budget.

---

## 7. Communication Model

### Dispatch → Execute → Report

```
1. Simone dispatches mission to VP mission queue
   └── Simone is NOT blocked; continues other work

2. VP Worker claims mission from queue
   ├── Evaluates: SDK or CLI?
   └── If CLI: crafts prompt, launches subprocess

3. Claude Code CLI runs independently
   ├── May run for minutes or hours
   ├── VP monitors JSON stream (light touch)
   └── VP responds to input requests as needed

4. CLI completes (or VP decides to terminate)
   └── VP writes result to VP mission DB

5. VP event bridge notifies Simone
   └── Simone communicates to user (dashboard, Telegram, email)
```

### No Mid-Execution Updates to Simone

During a CLI session, Simone is not updated. The VP worker is the supervisor. Only on completion (success or failure) does Simone learn the outcome. This is by design — Simone is the orchestrator for the entire UA system and should not be blocked waiting for a long-running CLI session.

### User Visibility

The dashboard already shows VP mission status (queued/running/completed/failed). The user can monitor progress there. Future enhancement: stream CLI progress events to the dashboard in real-time.

---

## 8. Open Design Questions

### Q1: CLI Configuration Inheritance
Should CLI sessions inherit UA's `.claude/` config (CLAUDE.md, MCP servers, skills)?
- **Likely yes** for skills (they're the whole point of some CLI use cases)
- **Maybe no** for CLAUDE.md (UA-specific persona instructions might confuse the CLI)
- **Selective** for MCP servers (some are useful, some are gateway-specific)

### Q2: Crash Recovery
How should the VP handle CLI crashes mid-session?
- Option A: Retry with same prompt (simple, may hit same failure)
- Option B: Retry with adjusted prompt based on error (smart, more complex)
- Option C: Mark mission failed, let Simone decide (safest)

### Q3: Session Reuse
Should the VP reuse CLI sessions across missions (faster startup) or always start fresh (cleaner isolation)?
- Fresh per mission is simpler and safer
- Session reuse would need state management

### Q4: Token Usage Tracking
How to track CLI token usage against the coding plan budget?
- Claude Code CLI has its own usage tracking
- VP could parse CLI output for token metrics
- Dashboard could surface CLI cost alongside SDK cost

### Q5: When Does Simone Specify CLI vs. VP Decides?
- Option A: Simone always specifies `execution_mode` in the dispatch
- Option B: VP evaluates the objective and decides autonomously
- Option C: Simone specifies for explicit requests; VP decides for delegated work
- **Recommendation: Option C** — gives both control and autonomy

---

## 9. Relationship to Existing Systems

### What Changes

| Component | Change |
|-----------|--------|
| `ClaudeCodeCLIClient` | **New** — VP client that spawns `claude` CLI subprocess |
| `VpWorkerLoop` | **Modified** — selects SDK or CLI client based on mission `execution_mode` |
| `vp/profiles.py` | **Modified** — profiles include `cli_capable: true/false` |
| Mission dispatch API | **Modified** — accepts `execution_mode` parameter |
| Dashboard VP panel | **Enhanced** — shows CLI session status, duration, agent team count |

### What Stays the Same

| Component | Status |
|-----------|--------|
| Simone's orchestration | Unchanged — dispatches missions as before |
| VP mission queue (SQLite) | Unchanged — same dispatch/claim/finalize flow |
| VP event bridge | Unchanged — same notification mechanism |
| Redis delegation bus | Unchanged — cross-factory dispatch works the same |
| Existing SDK subagents | Unchanged — research-specialist, report-writer still work |
| Factory heartbeat | Unchanged — factory presence tracking continues |

---

## 10. Summary: Best of Both Worlds

| Capability | Claude Agent SDK (current) | Claude Code CLI (new bridge) |
|-----------|---------------------------|------------------------------|
| Agent Teams | ❌ | ✅ |
| Subagent delegation | ✅ | ✅ (via Teams) |
| Gateway integration | ✅ (native) | Via VP mission bridge |
| Session streaming | ✅ (WebSocket) | JSON stream → VP → mission DB |
| MCP tools | ✅ | ✅ |
| Skills | ✅ | ✅ |
| Full dev toolchain | ❌ | ✅ |
| Concurrent teammates | ❌ | ✅ |
| User-facing chat | ✅ | ❌ (headless) |
| Token efficiency | Higher | Lower (per-teammate overhead) |

By bridging both runtimes through the VP worker layer, each factory gets access to **both** capability sets. Simone orchestrates, VP workers direct, and Claude Code CLI executes with its full autonomous power when needed.
