# 04. Dual-Factory Utilization & Capability Expansion Brainstorm

**Created:** 2026-03-06  
**Status:** Brainstorm — no implementation decisions made yet

## Context

Universal Agent now operates with a VPS headquarters and an optional local desktop factory. Both factories run VP workers that can execute delegated missions. The cross-machine delegation bus (Redis Streams), factory heartbeat, pause/resume controls, and Corporation View dashboard are all operational.

This document explores how to best utilize having two factories with different compute profiles, and what new capabilities the system could develop to expand its reach, autonomy, and usefulness.

---

## Part 1: Dual-Factory Workload Strategy

### Current State

| Factory | Compute | Cost | Network | Role |
|---------|---------|------|---------|------|
| VPS (HQ) | 16GB RAM, shared CPU | Metered (hosting) | Public IP, always-on | Interactive, operator-facing |
| Local Desktop | 32GB+ RAM, dedicated CPU/GPU | Free (electricity only) | Residential IP, intermittent | Batch work, development |

### Strategy A: Specialization by Workload Type

**HQ handles:** Interactive queries, real-time chat, webhook responses, Telegram, operator-facing APIs, time-sensitive notifications, CSI analytics processing.

**Local factory handles:** Long-running research tasks (30+ min), bulk data processing, video/audio analysis (GPU), large codebase analysis, development/staging of new VP capabilities.

**Implementation pattern:** Mission dispatcher checks workload type and available factory capacity. If local factory is online and task is batch-appropriate, delegate to local. Otherwise, execute on HQ.

### Strategy B: Overflow and Load Balancing

When HQ VP workers are both busy (running missions), new missions queue. If the local factory is online, overflow missions could be routed there automatically.

**Implementation:** Add a `preferred_factory` field to mission envelopes. HQ dispatcher checks local VP worker availability via factory registry before queuing locally. If a factory with idle workers exists, publish to Redis for that factory.

### Strategy C: Research Lab Pattern

Local factory as a dedicated "research lab" — runs longer, more experimental agent sessions that would monopolize HQ resources.

**Examples:**
- Multi-hour codebase analysis across multiple repositories
- Batch processing of YouTube tutorial backlog
- Experimental prompt strategies tested against real tasks before promoting to HQ
- Training data generation (structured examples for future fine-tuning)

### Strategy D: Development Staging

Use local factory as the staging environment:
- New VP capabilities are developed and tested locally first
- `system:update_factory` deploys to local for validation before HQ
- Factory-specific feature flags allow gradual rollout
- Regression testing against real mission payloads without risking HQ stability

---

## Part 2: New Capability Areas

### 2.1 Proactive Repository Monitor

A VP worker that continuously watches configured GitHub repositories:
- Detects new issues, PRs, failed CI runs
- Analyzes code changes for potential problems
- Files PRs for simple fixes (dependency updates, typo corrections)
- Generates daily summaries of repository activity
- Alerts on breaking changes or security vulnerabilities

**Why this matters:** Reduces the time between "problem exists" and "problem is addressed" from hours/days to minutes.

### 2.2 Multi-Agent Collaboration Protocol

VP workers that can communicate with each other on complex tasks:
- General VP researches a topic, hands structured findings to Coder VP for implementation
- Coder VP builds something, hands to General VP for documentation and testing
- Both VPs can work on different aspects of the same mission simultaneously

**Implementation sketch:** Extend mission payload with `depends_on_mission_id` and `collaboration_context`. When a VP completes a mission that has dependents, the dependent mission is automatically unblocked with the predecessor's result injected as context.

### 2.3 Autonomous Intelligence Briefings

Scheduled VP missions that produce daily/weekly intelligence:
- Morning briefing: overnight GitHub activity, CSI trends, email summary, calendar preview
- Weekly report: task completion rates, VP utilization, cost analysis, system health trends
- Topic-specific research: configured topics that the agent researches autonomously and delivers findings

**Current state:** CronService exists and can trigger sessions. This would formalize the pattern with structured output templates and delivery to email/Telegram.

### 2.4 Self-Improvement Pipeline

An agent that reviews its own past performance:
- Analyzes failed missions: what went wrong, what could be different
- Identifies recurring tool call patterns that could be optimized
- Suggests prompt improvements based on observed failure modes
- Tracks token usage patterns and suggests cost optimizations
- Proposes new skills or tool integrations based on frequently-requested capabilities

**Implementation:** A scheduled VP mission that reads the VP mission history DB, analyzes patterns, and produces actionable improvement recommendations.

### 2.5 Knowledge Base Maintenance Agent

A VP worker dedicated to keeping documentation current:
- Compares recent code changes (git diff) against existing documentation
- Identifies docs that are now inaccurate or incomplete
- Generates updated doc sections based on actual source code
- Maintains the canonical source-of-truth documents as the codebase evolves

**Why this matters:** Documentation drift is the #1 cause of operational confusion. An autonomous doc-maintenance agent eliminates the "docs are stale" problem.

### 2.6 External Integration Agents

VP workers dedicated to maintaining external service integrations:
- **Social media agent:** Scheduled posting to X/Twitter, Threads, LinkedIn based on content calendar
- **Email campaign agent:** Manages outbound email sequences through AgentMail
- **Data sync agent:** Keeps external data stores synchronized (Google Sheets, Notion, Airtable)
- **Monitoring agent:** Watches external service health (API uptime, rate limits, quota usage)

### 2.7 Financial Analytics VP

A dedicated worker for cost tracking and budget management:
- Tracks API costs across all providers (Anthropic, OpenAI, Composio, AgentMail)
- Monitors token usage per session, per VP, per mission type
- Generates cost reports with trends and anomaly detection
- Alerts when spending approaches configured thresholds
- Suggests cost optimization strategies (model downgrades for simple tasks, caching for repeated queries)

---

## Part 3: Autonomy Expansion

### 3.1 Goal-Driven Multi-Step Missions

Current missions are single-step: dispatch, execute, complete. Goal-driven missions would span hours or days:

- Mission has a high-level objective and a series of checkpoints
- VP executes step 1, reports progress, waits for next trigger (time-based or event-based)
- Mission state persists across VP restarts
- Operator can inspect progress, adjust parameters, or cancel at any checkpoint
- Final deliverable is assembled from all checkpoint outputs

**Example:** "Research competitor X's product strategy" → Step 1: Gather public information → Step 2: Analyze findings → Step 3: Produce comparative report → Step 4: Email to Kevin with recommendations.

### 3.2 Inter-Factory Communication

Currently factories only communicate through HQ (Redis bus). Direct factory-to-factory communication would enable:

- Local factory delegates a sub-task to HQ (reverse delegation)
- Factories share intermediate results without round-tripping through Redis
- Peer-to-peer file transfer for large artifacts (via Tailscale direct connection)
- Coordinated multi-factory execution for very large tasks

### 3.3 Self-Healing Infrastructure

Factories that detect and fix their own problems:
- Bridge detects repeated Redis connection failures → automatic reconnect with backoff
- VP worker detects Claude API rate limiting → automatic pause and resume
- Factory detects disk space running low → automatic workspace cleanup
- HQ detects CSI delivery health degraded → automatic CSI service restart
- Service watchdog detects memory pressure → automatic selective service restart

**Current state:** The service watchdog (`vps_service_watchdog.sh`) handles basic process-level recovery. Self-healing would extend this to application-level intelligence.

### 3.4 Capability Discovery and Self-Extension

VPs that can identify gaps in their own capabilities:
- VP encounters a task requiring a tool it doesn't have → logs the gap
- Accumulated gap reports are analyzed periodically
- System suggests new skills or MCP tool integrations to fill recurring gaps
- Operator approves, and the system installs the new capability
- New capability is validated on local factory before rolling to HQ

---

## Part 4: Telegram as First-Class Interface

### Current Limitations
- Telegram is polling-based, no push notifications from agent to user
- Session model creates fresh sessions per query (no persistent conversation)
- No rich media support (images, files, interactive buttons)
- No command menu beyond /new and /continue

### Expansion Ideas

**Rich Interaction Patterns:**
- Inline keyboards for mission approval (approve/reject directly in Telegram)
- File/image sharing (VP work products delivered as Telegram documents)
- Status cards with live updating (mission progress, system health)
- Custom command menu: `/status`, `/missions`, `/briefing`, `/delegate <task>`

**Mobile-First Operator Experience:**
- Morning briefing delivered as a formatted Telegram message at configured time
- Quick delegation: forward a message/URL to Simone, she processes it as a mission
- Alert escalation: critical events that require attention are pushed as notifications
- Voice memo processing: voice messages transcribed and processed as queries

**Bidirectional Integration:**
- Telegram as an approval channel (operator approves missions without opening dashboard)
- Telegram as a delivery channel (VP work products sent to operator's chat)
- Telegram as a monitoring channel (system alerts, health reports, daily summaries)

---

## Priority Assessment

| Capability | Impact | Effort | Priority |
|-----------|--------|--------|----------|
| Workload specialization (Strategy A) | High | Low | **Now** — routing logic exists, just needs policy |
| Overflow load balancing (Strategy B) | Medium | Medium | **Next** — needs dispatcher enhancement |
| Autonomous briefings (2.3) | High | Low | **Now** — cron + VP + template |
| Knowledge base maintenance (2.5) | High | Medium | **Next** — git diff analysis + doc generation |
| Self-improvement pipeline (2.4) | High | Medium | **Next** — mission history analysis |
| Telegram expansion | High | Medium | **Next** — rich keyboards, file sharing |
| Goal-driven missions (3.1) | Very High | High | **Later** — needs mission state machine |
| Multi-agent collaboration (2.2) | Very High | High | **Later** — needs mission dependency system |
| Financial analytics (2.7) | Medium | Medium | **Later** — needs cost tracking infrastructure |
| Self-healing (3.3) | Medium | Medium | **Later** — incremental improvement |
| Inter-factory comms (3.2) | Low | High | **Future** — only needed at scale |
| Capability discovery (3.4) | Medium | High | **Future** — ambitious but high-value |

---

## Bottom Line

The dual-factory model is primarily valuable for **workload specialization** (HQ interactive, local batch) and **development staging** (test locally before deploying to HQ). These can be implemented with minimal new infrastructure.

The highest-value new capabilities are **autonomous briefings** (already nearly possible with existing cron + VP), **knowledge base maintenance** (high impact on documentation quality), and **Telegram expansion** (makes the system genuinely useful from mobile).

The most ambitious capabilities — goal-driven multi-step missions and multi-agent collaboration — would transform the system from "agent that handles requests" to "agent that pursues objectives." These should be designed carefully and implemented after the foundation is solid.
