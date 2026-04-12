# Universal Agent — Discord Command & Control Buildout: Context Handoff for Claude Code

**Created:** 2026-04-12
**Source:** Extended architecture review conversation in Claude.ai (Opus)
**Purpose:** Transfer strategic context + investigation directives so Claude Code can execute with full understanding

---

## How to Use This Document

This document has two parts:

1. **Strategic Context** — What was discussed, what was decided, and why. This is the "what" and "why."
2. **Codebase Investigation Directives** — Specific questions Claude Code must answer by reading the actual codebase before making implementation decisions. This is the "go find out" list.

**Claude Code: Read Part 1 fully first. Then execute the investigations in Part 2 before proposing any implementation plans. The investigations will ground the strategy in the actual code reality.**

---

## PART 1: STRATEGIC CONTEXT

### 1.1 What Is the Universal Agent?

Kevin's bespoke AI agent system. Python-based (65%), deployed on a VPS. Key actors:

- **Simone** — Executive orchestrator agent. Triages all tasks, delegates to VPs, communicates with Kevin via email/AgentMail. She is the central router for everything.
- **CODIE** — VP Coder agent. Handles coding/technical delegated work.
- **ATLAS** — VP General agent. Handles research/analysis delegated work.
- **Task Hub** — SQLite-backed Kanban lifecycle system. All work flows through here. States: open → in_progress → delegated → pending_review → needs_review → completed/parked/blocked.
- **Proactive Pipeline** — 7 subsystems working together for autonomous work: Heartbeat, Task Hub, Cron, CSI, Email/AgentMail, Calendar, Brainstorm Pipeline.
- **CSI (Creator Signal Intelligence)** — Signal intelligence subsystem. Currently ingests YouTube channels. Discord integration is being built (this is what we're extending).
- **LLM Wiki** — External knowledge vault + internal memory vault. Just had first smoke test (2026-04-07). External vault works; internal sync has timeout issues.
- **Heartbeat Service** — Fires ~every 30 minutes. Sweeps Task Hub for eligible work, feeds it to Simone.
- **Memory System** — Tiered memory with auto-flush, DAG-based compression.
- **Morning Briefing** — Deterministic snapshot injected into heartbeat prompts.

### 1.2 The Core Problem We're Solving

The UA system is architecturally mature for **reactive reliability** (tasks execute correctly when triggered), but weak on **proactive value generation** (agents autonomously producing work Kevin didn't explicitly request) and critically weak on the **human review loop** (getting autonomous output in front of Kevin efficiently so he can approve/reject/redirect).

Kevin's philosophy: "There is no wasted compute. Even if I reject 80% of what agents produce, the 20% I keep is pure value. And as they learn from feedback, that ratio improves."

### 1.3 The Decision: Discord as Primary Command & Control Channel

We evaluated Telegram vs Discord for the human-in-the-loop approval/control channel. **Discord won.** Reasons:

- **Convergence** — Discord is already being built as a CSI intelligence ingestion channel. Adding command & control there means one platform serves both passive consumption and active decision-making, rather than maintaining two separate bot integrations.
- **Superior interaction model** — Discord has slash commands, interactive button components on embeds, threads for feedback conversations, and channel-based organization with per-channel notification control. Telegram inline keyboards are flatter and lack threads/channels.
- **Architectural runway** — Discord supports richer patterns (channel separation, role-based access, embeds with multiple button rows) that will matter as the system scales.
- **Telegram stays available** as a narrow escalation/notification channel if needed later, but Discord is the primary control plane.

### 1.4 Proposed Discord Channel Architecture

This is the recommended structure. Validate against what already exists and adapt:

| Channel | Purpose |
|---------|---------|
| `#briefing` | Morning/weekly intelligence products delivered here |
| `#review-queue` | Autonomous work awaiting Kevin's approval. Interactive button embeds (Approve / Reject / Revise / Later) |
| `#signals` | CSI intelligence feed (may already exist or be partially built) |
| `#agent-log` | Activity stream of what agents are doing (optional, for situational awareness) |

### 1.5 The Approval Pipeline Design

This is the highest-priority feature. The flow:

1. Agent completes autonomous work → artifact generated → task moves to `needs_review` (or a new human-review state if needed — **investigate what states exist and whether we need a new one distinct from Simone's `pending_review`**)
2. Simone (or the dispatch system) generates a **digest card**: 2-3 sentence summary of what was produced, why, and the artifact link
3. Digest card pushed to `#review-queue` as a Discord embed with interactive buttons: ✅ Approve / ❌ Reject / 📝 Revise / ⏸️ Later
4. Kevin taps a button → interaction fires back to the gateway (webhook or bot event) → Task Hub state transition
5. On **Reject**, prompt for brief feedback reason (Discord modal or thread reply) — even one word ("irrelevant", "too shallow", "wrong angle") feeds preference learning
6. Approved artifacts flow to their destination (email sent, wiki updated, briefing included, etc.)

**Key architectural question:** The current `needs_review` state is described as "waiting for human input or approval," and `pending_review` is "VP finished, waiting for Simone's validation." We may need to distinguish between Simone-review and human-review more cleanly, or the existing states may already handle this. **Investigate.**

### 1.6 The Broader Proactive Improvement Roadmap

Beyond Discord command & control, these are the other high-impact improvements discussed, ranked by priority. Discord C&C is the prerequisite that unlocks the rest:

**Tier 1 — Build alongside or immediately after Discord C&C:**

- **CSI → ATLAS overnight research pipeline** — When CSI ingests a signal above a quality threshold, auto-create a Task Hub research task for ATLAS. Agents work overnight; Kevin wakes up to completed research briefs in `#review-queue`. Both pieces exist (CSI ingestion, ATLAS delegation); they need to be bridged.
- **Preference learning closed loop** — On every approve/reject decision, run a lightweight LLM extraction to capture a preference signal (e.g., "Kevin rejects research briefs that lack benchmarks"). Store in LLM Wiki internal vault. Inject relevant preferences into delegation briefings when Simone delegates similar future tasks. This is the mechanism that makes the 80/20 ratio improve over time.

**Tier 2 — After the core loop is working:**

- **Enhanced morning briefing** — Evolve from deterministic snapshot to structured intelligence product: overnight activity summary, CSI signal digest with cross-source synthesis, decision queue with aging indicators, calendar + task forecast, system health.
- **Overnight research desk** — Standing research queue that ATLAS processes during idle hours. Topics auto-seeded from CSI signals, completed tasks, or a manually maintained interests list.
- **Codebase health monitor** — CODIE runs periodic autonomous audits of the UA repo itself (dependency vulns, dead code, doc-code drift via the drift pipeline in doc 99, test coverage). Findings become Task Hub items.

**Tier 3 — Ambitious extensions:**

- **Google Calendar deeper integration** — Reverse direction: Simone creates calendar events for deadlines and review blocks, not just ingests them.
- **GitHub notifications triage** — Adapter that monitors Kevin's GitHub notification stream, filters for signal, creates Task Hub items.
- **Personal learning pathway engine** — Structured learning roadmap maintained by agents, with spaced-repetition review tasks.
- **Financial monitoring** — RSS/API adapter for portfolio snapshots, alert tasks on significant moves.

### 1.7 The Web Dashboard Relationship

The web dashboard (screenshot shows Task Hub at `app.clearspringcg.com/dashboard/todolist`) already has the Kanban view, dispatcher health, approval indicators, and work item history. The dashboard should remain the "sit down and do a deep review" interface. Discord becomes the "on-the-go, quick decisions" interface. They share the same Task Hub backend — actions taken in Discord should be visible in the dashboard and vice versa.

---

## PART 2: CODEBASE INVESTIGATION DIRECTIVES

**Claude Code: Complete these investigations before proposing implementation plans. Each one informs critical design decisions.**

### Investigation 1: Current Discord Integration State

**Goal:** Understand exactly what Discord code exists today, what works, what's stubbed out.

**Look at:**
- Search the entire repo for Discord-related code: `grep -r "discord" --include="*.py" --include="*.js" --include="*.ts" --include="*.json" --include="*.md" -l`
- Check for a Discord bot file, cog structure, or slash command definitions
- Check for Discord webhook endpoints in the gateway (`gateway_server.py` or hooks service)
- Check if there's a Discord bot token in the Infisical secrets config (`.infisical.json`, `.env.example`, `.env.sample`)
- Check the CSI ingester for Discord adapter code — the README mentions Discord integration being planned for CSI
- Look at `FUTURE_DEVELOPMENT_DESIGNS/` directory for any Discord design docs
- Check `config/` for Discord configuration

**Questions to answer:**
1. Is there already a Discord bot running? What library (discord.py, interactions.py, etc.)?
2. Are there any slash commands already defined? What do they do?
3. How does the current Discord integration connect to the UA system — via webhook, direct bot, or CSI adapter?
4. What Discord channels exist on the server currently?
5. Is the bot using gateway (persistent connection) or interaction-based (webhook) architecture?
6. What permissions does the bot currently have?

### Investigation 2: Task Hub State Machine and Review States

**Goal:** Understand the exact task lifecycle states and whether we need new states for human-in-the-loop approval via Discord.

**Look at:**
- `src/**/task_hub.py` — the state machine definition, all valid state transitions
- The `needs_review` state — what triggers it, what actions are available from it, who is expected to act on it
- The `pending_review` state — how it differs from `needs_review`
- The dispatch service — how tasks get claimed and executed
- The `task_hub_task_action` tool — what actions does it expose (review, complete, block, park, unblock, delegate, approve)

**Questions to answer:**
1. Can the existing `needs_review` state serve as our "awaiting Kevin's human approval" state, or is it currently used for something else that would create ambiguity?
2. What happens when a task in `needs_review` is approved vs. rejected? What state transitions exist?
3. Is there an `approve` action that could be triggered externally (from Discord), or would we need to add one?
4. How does the approval flow on the web dashboard work currently? (The screenshot shows an "APPROVALS" button in the top right — what does it do?)
5. Is there a concept of "who" is reviewing — i.e., can we distinguish Simone reviewing a VP's work from Kevin reviewing Simone's output?

### Investigation 3: Existing Webhook and External Trigger Infrastructure

**Goal:** Understand how external events currently enter the system, so we can design Discord interactions to use the same patterns.

**Look at:**
- `hooks_service.py` — how webhooks are received, authenticated, and dispatched
- `webhook_transforms/` — how external payloads get transformed into Task Hub items
- The AgentMail email ingress path — how emails become tasks (in `email_task_bridge.py`)
- The Calendar webhook integration — how calendar events become tasks
- The CSI delivery contract — how CSI signals get delivered to the UA

**Questions to answer:**
1. Is there a standardized "external event → Task Hub item" pipeline we should plug Discord interactions into?
2. How are webhooks authenticated? Will Discord interactions need to go through the same auth?
3. What's the pattern for a new ingress channel? Is there a template/interface we should follow?
4. Does the gateway expose any REST endpoints that could serve as Discord interaction endpoints?

### Investigation 4: Telegram Bot Architecture (As Reference Pattern)

**Goal:** Understand the existing Telegram bot implementation so we can learn from its patterns (and its gaps).

**Look at:**
- The Telegram bot code — likely polling-based per doc 91
- How Telegram messages trigger agent actions
- The Telegram → gateway execution path
- What the Telegram bot can currently do vs. what it was designed to do
- Any allowlist or authentication mechanism

**Questions to answer:**
1. What is the Telegram bot's execution model — does it run inside the gateway process or separately?
2. What commands/actions does it support?
3. How does it authenticate that messages are from Kevin?
4. What lessons from the Telegram implementation should we apply (or avoid) for Discord?

### Investigation 5: CSI Discord Integration Plans

**Goal:** Understand what's planned for CSI's Discord integration so we build the command & control layer to be compatible.

**Look at:**
- CSI architecture docs: `docs/04_CSI/CSI_Master_Architecture.md`
- CSI source adapters — is there a Discord adapter in progress?
- The CSI rebuild docs: `docs/csi-rebuild/`
- Any design docs mentioning Discord in `FUTURE_DEVELOPMENT_DESIGNS/`

**Questions to answer:**
1. Is CSI planning to ingest Discord messages as signals (like it does YouTube)?
2. If so, how do we keep the CSI ingestion path separate from the command & control path? (We don't want Kevin's `/approve` slash command being treated as a CSI signal.)
3. Are there Discord channels designated for CSI intelligence vs. human interaction?

### Investigation 6: Morning Briefing and Proactive Advisor

**Goal:** Understand the current briefing implementation so we can plan how to deliver richer briefings via Discord.

**Look at:**
- `proactive_advisor.py` — what context it assembles
- The morning report snapshot builder
- How the briefing gets injected into heartbeat prompts
- Whether the briefing is currently delivered to any external channel (email? dashboard?)
- `docs/03_Operations/78_Daily_Autonomous_Briefing_Reliability_And_Input_Diagnostics_2026-02-26.md`

**Questions to answer:**
1. What data sources feed the current morning briefing?
2. Is it delivered anywhere Kevin can read it directly, or is it only used internally as prompt context?
3. What would it take to format and push the briefing to a Discord `#briefing` channel?
4. What are the known reliability issues (doc 78 covers this)?

### Investigation 7: Memory System and Preference Learning Feasibility

**Goal:** Understand whether the memory system and LLM Wiki can support the preference learning loop.

**Look at:**
- `Memory_System/` and `memory/` directories
- The memory orchestrator (`orchestrator.py`)
- LLM Wiki implementation: `docs/02_Subsystems/LLM_Wiki_System.md` and `docs/03_Operations/109_LLM_Wiki_Implementation_Status_2026-04-06.md`
- The internal memory vault API — can agents write to it programmatically?
- The smoke test results in doc 110

**Questions to answer:**
1. Can the memory system currently accept structured preference entries (key-value or tagged)?
2. Can the LLM Wiki internal vault be written to programmatically during task completion?
3. What are the current blockers on the internal vault (doc 109 mentions timeout issues)?
4. Is there any existing preference or feedback tracking mechanism?

### Investigation 8: Gateway and Authentication Surface

**Goal:** Understand how Discord bot interactions would authenticate and integrate with the gateway.

**Look at:**
- `gateway_server.py` — all exposed endpoints, auth mechanisms
- `docs/02_Flows/08_Gateway_And_Web_UI_Auth_And_Session_Security_Source_Of_Truth_2026-03-06.md`
- How the web dashboard authenticates and sends commands (dispatch_immediate, dispatch_on_approval)
- Whether there's an internal API that bypasses web auth for trusted services

**Questions to answer:**
1. Is there an internal service-to-service auth mechanism that the Discord bot could use?
2. Can the existing `dispatch_immediate` and `dispatch_on_approval` actions be triggered programmatically from a bot?
3. Does the gateway have a REST API layer that non-browser clients can use, or is everything WebSocket/session-based?

---

## PART 3: IMPLEMENTATION PRIORITIES (Post-Investigation)

After completing the investigations, the recommended implementation order is:

### Phase 1: Discord Bot Foundation + Approval Pipeline
1. Establish the Discord bot with proper auth and connection to the UA gateway
2. Implement `#review-queue` channel with interactive embed buttons
3. Wire button interactions to Task Hub state transitions (approve → completed, reject → feedback capture, revise → new task, later → no-op/snooze)
4. Add feedback capture on reject (Discord modal or thread)
5. Test the full loop: autonomous task completes → digest card appears in Discord → Kevin taps approve → task completes in Task Hub → visible on dashboard

### Phase 2: Discord Intelligence Channels
1. Wire morning briefing delivery to `#briefing` channel
2. Wire CSI signals to `#signals` channel (if not already done)
3. Add `#agent-log` for activity stream (optional)

### Phase 3: Slash Commands for Direct Control
1. `/status` — Current Task Hub summary (dispatch eligible, active, pending review counts)
2. `/queue` — Show pending review items
3. `/task <description>` — Quick-add a task to Task Hub
4. `/heartbeat` — Manually trigger a heartbeat cycle
5. `/brief` — Request an on-demand briefing

### Phase 4: Preference Learning Loop
1. On approve/reject, trigger lightweight preference extraction
2. Store preference entries in LLM Wiki internal vault (or memory system if wiki isn't ready)
3. Inject relevant preferences into Simone's delegation briefings
4. Weekly preference digest in `#briefing`

---

## Appendix: Key Documentation Paths

These docs in the repo are the most relevant for this work:

| Document | Path | Why It Matters |
|----------|------|----------------|
| Pipeline Masterpiece | `docs/01_Architecture/000_PIPELINE_MASTERPIECE.md` | End-to-end pipeline overview |
| Simone-First Orchestration | `docs/01_Architecture/05_Simone_First_Orchestration.md` | How Simone routes everything |
| Proactive Pipeline | `docs/02_Subsystems/Proactive_Pipeline.md` | The 7-subsystem autonomous work engine |
| Task Hub Dashboard | `docs/02_Subsystems/Task_Hub_Dashboard.md` | Frontend design, Kanban, approval UI |
| LLM Wiki System | `docs/02_Subsystems/LLM_Wiki_System.md` | Knowledge/memory vault architecture |
| Memory System | `docs/02_Subsystems/Memory_System.md` | Tiered memory architecture |
| Telegram Architecture | `docs/03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` | Reference pattern for bot integration |
| CSI Architecture | `docs/03_Operations/92_CSI_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` | Signal intelligence subsystem |
| Gateway Auth | `docs/02_Flows/08_Gateway_And_Web_UI_Auth_And_Session_Security_Source_Of_Truth_2026-03-06.md` | Auth model for external integrations |
| Webhook Architecture | `docs/03_Operations/83_Webhook_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md` | How external triggers enter the system |
| Morning Briefing Reliability | `docs/03_Operations/78_Daily_Autonomous_Briefing_Reliability_And_Input_Diagnostics_2026-02-26.md` | Known briefing issues |
| LLM Wiki Status | `docs/03_Operations/109_LLM_Wiki_Implementation_Status_2026-04-06.md` | Current wiki implementation state |
| LLM Wiki Smoke Test | `docs/03_Operations/110_LLM_Wiki_Smoke_Test_2026-04-07.md` | What works/doesn't in the wiki |
| Task Hub Execution Master Ref | `docs/03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md` | Definitive Task Hub lifecycle reference |
| CSI Master Architecture | `docs/04_CSI/CSI_Master_Architecture.md` | CSI design and adapter architecture |
