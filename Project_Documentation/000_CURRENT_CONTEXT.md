# 000: Current Project Context

> [!IMPORTANT]
> **For New AI Agents**: Read this document first to understand the current state of the project.
> This is a living document that tracks where we are and where we're going.

**Last Updated**: 2026-01-02 21:39 CST

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

## 📍 Current State (January 2, 2026)

### ✅ What's Working

| Feature | Status | Notes |
|---------|--------|-------|
| **Railway Deployment** | ✅ Production | US West, Static IP, Pro plan |
| **Telegram Bot** | ✅ Working | Webhook mode, FastAPI + PTB |
| **Research & Report Generation** | ✅ Production-ready | JIT Delegation via Knowledge Base |
| **PDF/PPTX Creation** | ✅ Working | Skills-based, conditional routing |
| **Email Delivery (Gmail)** | ✅ Working | Attachments via `upload_to_composio` |
| **Image Generation** | ✅ Working | Gemini 2.5 Flash Image |
| **Memory System** | ✅ Working | Core blocks, archival search |
| **Logfire Tracing** | ✅ Working | Dual Trace (Main + Subprocess) |
| **Durable Runs (Phase 0–3)** | ✅ Working | Run/step tracking, checkpoints, replay policy, RELAUNCH |
| **Operator CLI + Worker Mode** | ✅ Working | Runs list/show/tail/cancel + lease-based worker |
| **Policy Audit + Receipts** | ✅ Working | Tool policy audit + side-effect receipt export |
| **Run-Wide Completion Summary** | ✅ Working | Aggregated tool/step summary across resumes |
| **Filtered Research Corpus** | ✅ Working | `finalize_research` + filtered corpus + overview |

### 🆕 Recent Fixes (Jan 1–2, 2026)

1. **Durable Jobs Phase 0–3**: Runtime DB, tool-call ledger, idempotency, checkpoints, replay policy, RELAUNCH.
2. **Recovery Hardening**: Forced replay queue prevents extra tool calls after recovery drains.
3. **Tool Policies**: Config-driven policy map in `durable/tool_policies.yaml` plus TaskOutput/TaskResult guardrail.
4. **Crash Hooks**: Deterministic crash injection at tool boundaries for idempotency testing.
5. **Run-Wide Summaries**: Aggregated tool/step counts across resumes written to job completion + restart file.
6. **Step Indexing**: Monotonic `step_index` across recovery + continuation for audit clarity.
7. **Workspace Paths**: Job prompts resolve workspace-relative paths to absolute to avoid `$PWD` drift.
8. **Filtered Research Pipeline**: `finalize_research` builds filtered corpus + `research_overview.md`.
9. **Filter Tuning**: Looser drop thresholds; explicit filtered vs dropped tables in overview.
10. **Report Prompt Unification**: Report sub-agent now uses filtered corpus only (no raw crawl reads).
11. **MCP Server Fix**: Syntax/indent error fixed; Crawl4AI Cloud API handling stabilized.
12. **Operator CLI**: `ua runs list/show/tail/cancel` + cancellation guardrails.
13. **Worker Mode**: Lease/heartbeat worker entrypoint for background runs.
14. **Policy Audit**: Unknown-tool detection + policy audit report (`ua policy audit`).
15. **Receipts Export**: Side-effect receipt summary (`ua runs receipts`).
16. **Durability Smoke Script**: One-command crash → resume → verify (`scripts/durability_smoke.py`).

---

## 🚧 Known Issues & Next Steps

### 🚀 IMMEDIATE NEXT STEP: Submit Letta SDK Pull Request
**Purpose**: Fix critical `UnboundLocalError` in upstream `letta-ai/learning-sdk` to remove our local monkey patch.
**Artifacts for Submission**:
*   **PR Draft Description**: [`Project_Documentation/PR_DRAFT_LETTA_MEMORY_FIX.md`](Project_Documentation/PR_DRAFT_LETTA_MEMORY_FIX.md)
*   **Technical Context**: [`Project_Documentation/027_LETTA_INTEGRATION_TECHNICAL_NOTES.md`](Project_Documentation/027_LETTA_INTEGRATION_TECHNICAL_NOTES.md)
*   **Source Code Reference**: `sitecustomize.py` (specifically `_patch_letta_memory_upsert`)
**Action Required**:
1.  Fork/Clone `letta-ai/learning-sdk`.
2.  Apply the fix (typo correction in `client/memory/client.py`).
3.  Submit PR using the text from the draft artifact.

### ✅ RESOLVED: Session Persistence After Task (Fixed Dec 31, 2025)
**Fix**: Watchdog timeout + Worker health checks implemented in Bot.

### 🟡 Known Issues / In Progress

| Issue | Status | Notes |
|-------|--------|-------|
| Provider session_id only captured after ResultMessage | ⏳ Known | Early interrupts may lack provider_session_id; fallback works |
| Headless Chrome DBus warnings | ⏳ Known | No functional failures observed; logs are noisy |
| Multiple local-toolkit trace IDs | ⏳ Known | Local MCP uses multiple trace IDs per window |
| Agent College not auto-triggered | ⏳ Pending | Requires manual invocation |
| `/files` command not implemented | ⏳ Pending | Users can't download artifacts |
| `/stop` command not implemented | ⏳ Pending | Can't cancel running tasks |

---

## 📌 Phase 4 Ticket Pack (Next Focus)
Source of truth: `Project_Documentation/Long_Running_Agent_Design/Phase4_Ticket_Pack.md`

Planned work (in recommended sequence):
1) **Operator CLI**: list/show/tail/cancel runs from the runtime DB. ✅ Implemented
2) **Worker mode**: background execution with leasing + heartbeat. ✅ Implemented
3) **Receipt summaries**: export side-effect receipts for auditability. ✅ Implemented
4) **Policy audit**: unknown-tool detection + classification report. ✅ Implemented
5) **Triggers**: cron/webhook scaffolding to queue runs. ⏳ Pending

Requirement: **Create a numbered-prefix project doc for each ticket after completion** (tracking_development/NNN_*.md).

## 🏗️ Architecture Overview

### Railway Deployment

```
GitHub (git push main)
        │
        ▼
Railway Auto-Deploy
        │
        ▼
┌─────────────────────────────────────────┐
│ Container (python:3.12-slim-bookworm)   │
│                                         │
│  start.sh → Bot (FastAPI + PTB)         │
│           → Agent College (internal)    │
│                                         │
│  /app/data (Persistent Volume)          │
│   └── memory/, workspaces/              │
└─────────────────────────────────────────┘
```

**Production URL**: `https://web-production-3473.up.railway.app`

### Telegram Bot Flow

```
Telegram Cloud
    │
    │ HTTPS POST /webhook
    ▼
FastAPI (Uvicorn)
    │
    ▼
PTB Command Handlers
    │
    ▼
TaskManager (Queue)
    │
    ▼
AgentAdapter → Claude SDK
```

---

## 🧠 Agent College Architecture

```
Agent Runtime                    LogfireFetch Service
     │                                  │
     │ (errors/successes)               │
     ▼                                  ▼
  Logfire  ─────── polling ──────►  LogfireFetch
     │                                  │
     │                                  ▼
     │                            Critic/Scribe
     │                                  │
     │                                  ▼
     │                         [AGENT_COLLEGE_NOTES]
     │                           (Sandbox Memory)
     │                                  │
     │◄──────────────── read ───────────┘
     │
     ▼
  Professor (HITL Review)
     │
     ▼
  Graduation (New Skill / Rule)
```

---

## 🔧 Running the System

### Production (Railway)
Automatic on `git push main`. Monitor via Railway Dashboard.

### Local Development (CLI)
```bash
cd /home/kjdragan/lrepos/universal_agent

# Start Agent College + CLI
./local_dev.sh
```

### Durable Test Run (CLI)
```bash
PYTHONPATH=src uv run python -m universal_agent.main --job /home/kjdragan/lrepos/universal_agent/src/universal_agent/durable_demo.json
```

Interrupt with Ctrl-C to save a checkpoint. Resume with:
```bash
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

Latest resume command is written to:
`Project_Documentation/Long_Running_Agent_Design/KevinRestartWithThis.md`

### Quick Resume Test (CLI)
```bash
PYTHONPATH=src uv run python -m universal_agent.main --job /home/kjdragan/lrepos/universal_agent/tmp/quick_resume_job.json
```
Kill during the sleep step, then resume:
```bash
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

### Relaunch Resume Test (Task + Side Effects)
```bash
export UA_TEST_EMAIL_TO="kevin.dragan@outlook.com"
PYTHONPATH=src uv run python -m universal_agent.main --job /home/kjdragan/lrepos/universal_agent/tmp/relaunch_resume_job.json
```
Kill during the sleep step, then resume:
```bash
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

### Useful Commands
```bash
# Check webhook status
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"

# Force webhook registration
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<URL>&secret_token=<SECRET>"

# Check health
curl https://web-production-3473.up.railway.app/health
```

---

## 📚 Key Documentation

| Priority | Document | Purpose |
|----------|----------|---------|
| 1 | `Telegram_Integration/` | Bot architecture & deployment |
| 2 | `Architecture/11_railway_deployment_plan.md` | Railway setup |
| 3 | `013_AGENT_COLLEGE_ARCHITECTURE.md` | Agent College overview |
| 4 | `012_LETTA_MEMORY_SYSTEM_MANUAL.md` | Memory System design |
| 5 | `002_LESSONS_LEARNED.md` | Patterns and gotchas |
| 6 | `Project_Documentation/Long_Running_Agent_Design/` | Durable Jobs v1 + tracking |
| 7 | `Project_Documentation/Long_Running_Agent_Design/Phase4_Ticket_Pack.md` | Next-phase ticket pack (operator/worker/policy/receipts/triggers) |
| 8 | `Project_Documentation/Long_Running_Agent_Design/Durable_Jobs_Next_Steps_Ticket_Pack.md` | Next steps (smoke test/runbook) |

### Latest Durability Reports (Read These)
| Doc | Purpose |
|-----|---------|
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/006_provider_session_wiring_report.md` | Provider session resume/fork wiring + tests |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/007_resume_continuity_evaluation_quick_job.md` | Latest resume evaluation (in-flight replay works) |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/008_durable_runner_architecture.md` | Current durability architecture |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/009_relaunch_resume_evaluation.md` | Relaunch resume evaluation (Task + side effects) |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/011_relaunch_resume_evaluation_post_fix_v2.md` | Post-fix evaluation with run-wide summary |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/010_phase4_ticket1_operator_cli.md` | Ticket 1 implementation (Operator CLI) |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/012_phase4_ticket2_worker_mode.md` | Ticket 2 implementation (Worker mode) |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/013_phase4_ticket4_receipts.md` | Ticket 4 implementation (Receipts) |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/014_phase4_ticket3_policy_audit.md` | Ticket 3 implementation (Policy audit) |
| `Project_Documentation/Long_Running_Agent_Design/tracking_development/015_durability_smoke_script.md` | Smoke test script runbook |

---

## ✅ Recent Durability Updates (Jan 2, 2026)
1) **Replay policy classification**: REPLAY_EXACT, REPLAY_IDEMPOTENT, RELAUNCH.
2) **Relaunch semantics**: Task tool calls are abandoned on resume and deterministically relaunched.
3) **TaskOutput guardrail**: TaskOutput/TaskResult forced to RELAUNCH (no direct replay).
4) **Recovery hardening**: Forced replay queue prevents extra tool calls after recovery drains.
5) **Run-wide summaries**: Aggregated across resumes in job completion and restart file.
6) **Workspace path resolution**: Job prompts resolve workspace-relative paths to absolute.
7) **Step index monotonicity**: Recovery and continuation share a single step_index sequence.
8) **Crash hooks**: Deterministic crash injection for PREPARED→SUCCEEDED testing.
9) **Provider session continuity**: Store `provider_session_id`; resume with continue_conversation when available.
10) **Fork support**: `--fork --run-id <BASE>` creates new run with parent_run_id.
11) **In-flight tool replay**: Prepared/running tools re-run before continuation.
12) **SIGINT debounce**: Avoids multiple interrupt checkpoints.

---

## 🧭 New Coder Handoff (Quick Start)
**Goal**: Maintain durable jobs with no duplicate side effects across resume.

**What’s implemented now**:
1) Runtime DB + tool ledger + idempotency (Phases 1–3).
2) Replay policy (EXACT/IDEMPOTENT/RELAUNCH) with Task/TaskOutput guardrails.
3) Recovery-only tool execution during forced replay (no extra tool calls).
4) Run-wide completion summary across resumes (job_completion + KevinRestartWithThis).
5) Workspace path resolution to avoid $PWD drift in job prompts.

**Primary tests**:
1) `tmp/quick_resume_job.json` (sleep → resume).
2) `tmp/relaunch_resume_job.json` (Task → PDF → sleep → email; verify no duplicate email).

**Where to look**:
1) Durable logic: `src/universal_agent/durable/` (ledger, tool_gateway, state).
2) CLI entrypoint: `src/universal_agent/main.py`.
3) Runtime DB: `AGENT_RUN_WORKSPACES/runtime_state.db`.
4) Latest evals: `Project_Documentation/Long_Running_Agent_Design/tracking_development/009_relaunch_resume_evaluation.md`.

**Next milestone**: Phase 4 ticket pack (operator CLI, worker mode, policy audit, receipts, triggers).

---

## 🏗️ Project Structure

```
universal_agent/
├── src/
│   ├── universal_agent/
│   │   ├── main.py                 # Main agent
│   │   ├── bot/                    # Telegram bot
│   │   │   ├── main.py             # FastAPI + PTB
│   │   │   ├── config.py           # Environment vars
│   │   │   ├── telegram_handlers.py# Commands
│   │   │   ├── task_manager.py     # Async queue
│   │   │   └── agent_adapter.py    # Claude SDK bridge
│   │   └── agent_college/          # Professor, Critic, Scribe
│   └── mcp_server.py               # Local MCP tools
├── AgentCollege/                   # FastAPI service
├── Memory_System/                  # Letta-style memory
├── .claude/
│   ├── skills/                     # Skill definitions
│   └── knowledge/                  # Knowledge base
├── Project_Documentation/
│   ├── 000_CURRENT_CONTEXT.md      # This file
│   └── Telegram_Integration/       # Bot docs
├── Dockerfile                      # Container build
├── start.sh                        # Container entrypoint
└── AGENT_RUN_WORKSPACES/           # Session artifacts (local)
```

---

*Update this document whenever significant progress is made or context changes.*
