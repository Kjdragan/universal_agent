# Architectural Comparison: Pull-Based Agent Workers vs. Simone-First Orchestration

> **Established:** 2026-03-27
> **Context:** A comprehensive evaluation of the pull-based, branch-isolated `agent-worker` pattern compared against the event-driven, hierarchical `Simone-First Orchestration` model recently instituted in the Universal Agent.

---

## 1. Executive Summary

We evaluated the architectural approach of the [`agent-worker` repository](https://github.com/owainlewis/agent-worker) alongside its companion background worker tutorials, contrasting it with our **Simone-First Orchestration**. 

The **Agent Worker** model champions a brutally simple, highly secure, **pull-based (polling) architecture** combined with deterministic, hook-driven git worktree isolation. Conversely, our **Simone-First** model heavily leverages an **event-driven, hierarchical intelligence** approach where a primary agent (Simone) acts as a manager, router, and quality gate for specialized Virtual Personas (VPs).

While both solve the problem of "getting the human out of the loop," they optimize for completely different axes:
- **Agent Worker** optimizes for **Network Security, Simplicity, and Deterministic Enforcement**.
- **Simone-First** optimizes for **Context-Awareness, Orchestration Logic, and Quality Assurance**.

---

## 2. Core Philosophy & Flow

### 2.1 The Agent Worker Approach (Pull-Based Polling)
The `agent-worker` operates strictly as a background daemon running on consumer hardware or a secure server.
1. **Poll**: Reaches out to a third-party task tracker (e.g., Linear, Todoist) on an interval.
2. **Setup (Pre-hook)**: Automatically provisions an isolated Git worktree and new branch for the task.
3. **Execute**: Passes the task description to an isolated "dumb" CLI harness (i.e., Claude Code or Codex). 
4. **Verify (Post-hook)**: Runs deterministic bounds (tests, linters) before committing and pushing the PR.
5. **Report**: Marks the upstream ticket as Done or Failed (with the error log).

### 2.2 The Simone-First Approach (Hierarchical Orchestration)
The Universal Agent operates under a "Super-Agent" topology wrapped in an event-driven framework (Heartbeats + Hooks).
1. **Ingest**: Work arrives via push (Email Webhooks, API, Telegram) to the internal Task Hub.
2. **Triage**: Simone wakes up on the heartbeat, reviews up to 5 tasks at once, and decides delegation contextually.
3. **Delegation**: Simone executes directly (SELF) or delegates to specialized VP sandboxes (Atlas/Codie).
4. **Execution**: VPs execute in their environments; they do not require deterministic git hooks to be considered "Done," but rather rely on artifact handover.
5. **Sign-off**: Simone reviews the VP's output as an intelligent QA gate before marking the ticket `completed`.

---

## 3. Comparative Analysis

### 3.1 Security and Attack Surface

| Feature | Agent Worker (Pull) | Simone-First (Hybrid/Push) |
|---------|---------------------|----------------------------|
| **Network Exposure** | **Excellent:** Zero inbound ports. Purely outgoing HTTPS requests to a SaaS task manager. | **Moderate**: Retains inbound endpoints for webhooks (Email/Telegram) and WebSockets. Requires infrastructure hardening. |
| **Execution Sandboxing** | **Excellent:** Strictly isolated inside per-task git worktrees. Eliminates cross-contamination. | **Good:** VP agents run in separate lanes, but Simone executes `SELF` tasks in a shared state workspace. |
| **Authentication** | Simplistic API token for Task Manager. | Complex tracking of user sessions, Token Bypass, and Tailscale internal nets. |

**Winner: Agent Worker** — The polling model is undeniably more secure for unattended agent execution because the attack surface is virtually zero. If you don't expose a port, you can't be hit with a webhook injection payload.

### 3.2 Task Isolation & Reliability

The **Agent Worker** treats LLMs as "dangerous and non-deterministic". By forcing the agent to run inside a temporary Git worktree and guarding success behind deterministic bash hooks (`bun test`, `npm run lint`), it guarantees the codebase is never broken by an agent hallucination. If tests fail, the ticket fails.

**Simone-First** treats agents as "competent employees". VP Codie or Simone might write tests, but the system doesn't rigidly enforce a pass/fail matrix via post-hooks prior to marking a task "Done." Instead, it uses Simone as an LLM-based quality gate to review the artifacts. 

**Winner: Agent Worker (for Code), Simone-First (for Open-Ended Research/Generative Tasks)** — For strict software engineering, deterministic post-hook testing is vastly superior to LLM review. However, hooks fail entirely for qualitative tasks like "Research X and write a summary."

### 3.3 Orchestration and Cognitive Context

The **Agent Worker** is completely blind to the "bigger picture". If it pulls three tickets related to the same database refactor, it will spin up three independent worktrees that will eventually violently conflict at the PR merge stage. It has zero orchestration context.

**Simone-First** shines here. Because Simone receives a **Batch Triage** prompt of all queued tasks, she possesses the cognitive reasoning to recognize dependent tasks, consolidate them, decide which capabilities are best suited to the job, and remember the context between heartbeats. 

**Winner: Simone-First** — The centralized triage model prevents redundant work, enables task dependencies, and leverages the right tool (VP Atlas vs VP Codie vs SELF) dynamically.

---

## 4. Pros and Cons

### Agent Worker Model
**Pros:**
- Incredibly simple architecture with high horizontal scalability (just run more worker processes).
- Near-zero network attack surface (no webhooks).
- Deterministic guardrails (hooks) force agents to write objectively correct code.
- Git worktree isolation prevents cross-ticket state bleed.

**Cons:**
- High latency (polling interval delay).
- Agents lack holistic project awareness; cannot coordinate on multi-ticket epics.
- Cannot dynamically ask clarifying questions mid-task.

### Simone-First Orchestration Model
**Pros:**
- Contextually aware; can group tasks or sequence dependencies.
- Intelligent delegation to specialized, locally tuned runtimes (VPs).
- Fast and reactive to inbound push events (emails, manual triage).
- Human-like QA step via Simone's review logic.

**Cons:**
- Massive architectural complexity (Gateways, Event Bridges, Task Hub scoring).
- Inbound networking overhead creates security and proxying concerns.
- Vulnerable to LLM hallucination during the final Sign-Off QA gate (unlike deterministic tests).

---

## 5. Recommendations and Synthesis

Our Simone-First architecture represents a profound leap in cognitive orchestration, but it currently lacks the deterministic safety execution found in the Agent Worker model. **We should not abandon Simone, but we should adopt the mechanical guardrails of the Agent Worker.**

### Recommended Action Items (No Immediate Code Changes Required)

1. **Adopt Git Worktree Isolation for VP Codie**
   Currently, VP Codie missions operate in standard sandboxes. We should consider updating the `vp_dispatch_mission` orchestration so that whenever Simone delegates a coding task to Codie, the system dynamically generates an isolated git worktree branch (`agent/task-{mission_id}`).

2. **Introduce Deterministic Post-Hooks for VP Sign-off**
   Instead of Simone solely reviewing Codie's artifacts via an LLM reading code/reports, we should introduce a "Pre-Sign-Off" hook execution. If VP Codie finishes a task, the Universal Agent should automatically run the project's test suite.
   - *If tests pass:* Simone receives the artifacts to review architecture/style.
   - *If tests fail:* Simone immediately bounces the task back to `delegated` with the test failure logs, saving tokens on the QA pass.

3. **Re-evaluate Push vs. Poll Boundaries**
   While we require Push (Email, Telegram) to maintain latency SLAs for communication, background tasks (like GitHub Issue scraping or Jira ingesting) should strictly follow the Agent Worker's polling model over scheduled heartbeats to reduce external exposure.

## Conclusion
The `agent-worker` is a brilliant, hardened pipeline for shipping tickets safely. Our Simone-first orchestration is a brilliant cognitive engine for understanding what to build. 

Merging the two—where Simone is the "Brain" deciding what/who builds, but the "Hands" (VPs) are restricted by the `agent-worker`'s deterministic worktree validation—would yield the ultimate autonomous framework.
