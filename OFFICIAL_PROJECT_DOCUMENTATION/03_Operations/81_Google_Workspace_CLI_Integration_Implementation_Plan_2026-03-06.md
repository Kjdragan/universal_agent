# Google Workspace CLI (`gws`) Integration — Comprehensive Implementation Plan

**Date:** 2026-03-06
**Author:** Cascade (AI Pair Programmer)
**Status:** Draft — Awaiting Owner Approval
**Audience:** AI coder implementing the integration
**Companion doc:** [80_Google_Workspace_Integration_Retrospective_Memo_2026-03-06.md](./80_Google_Workspace_Integration_Retrospective_Memo_2026-03-06.md)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Prerequisites](#3-prerequisites)
4. [Phase 0 — Install & Auth Bootstrap](#4-phase-0--install--auth-bootstrap)
5. [Phase 1 — MCP Bridge Integration](#5-phase-1--mcp-bridge-integration)
6. [Phase 2 — Feature Flag & Config](#6-phase-2--feature-flag--config)
7. [Phase 3 — Composio Google Connector Cleanup](#7-phase-3--composio-google-connector-cleanup)
8. [Phase 4 — Agent Skills & Helpers](#8-phase-4--agent-skills--helpers)
9. [Phase 5 — Workspace Events (Streaming)](#9-phase-5--workspace-events-streaming)
10. [Phase 6 — Hardening & Rollout](#10-phase-6--hardening--rollout)
11. [Cleanup — Remove Planning Prototype](#11-cleanup--remove-planning-prototype)
12. [Environment Variables Reference](#12-environment-variables-reference)
13. [File Inventory](#13-file-inventory)
14. [Testing Strategy](#14-testing-strategy)
15. [Risk Register](#15-risk-register)
16. [Acceptance Criteria](#16-acceptance-criteria)

---

## 1. Executive Summary

The Universal Agent currently accesses Google Workspace (Gmail, Calendar, Drive, Sheets) exclusively through Composio MCP tools. This plan integrates Google's official Workspace CLI (`gws`) as the **primary execution path** for Google Workspace operations, using its built-in MCP server (`gws mcp`).

**Why:**
- `gws` dynamically discovers all Google Workspace APIs at runtime — no static command lists
- `gws mcp` exposes these as typed MCP tools over stdio — directly compatible with UA's MCP infrastructure
- Built-in OAuth, encrypted token storage (AES-256-GCM), auto-pagination, and service helpers
- Google-maintained = automatic API coverage updates, zero custom API client code

**Strategy: GWS-MCP-first with Composio fallback — clean greenfield build**
- `gws mcp` handles all Google Workspace operations
- Composio retained for cross-SaaS orchestration and as fallback during migration
- New `UA_ENABLE_GWS_CLI` feature flag controls progressive rollout
- No dependency on any prior prototype code

---

## 2. Architecture Overview

### 2.1 Target State Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Universal Agent                          │
│                                                              │
│  ┌─────────────┐                                              │
│  │ Agent Core   │  (tools from both MCP servers are available) │
│  │ (LLM Loop)  │                                              │
│  └─────┬───────┘                                              │
│        │                                                      │
│   ┌────▼─────────────────────────────────────────┐       │
│   │            MCP Tool Registry                   │       │
│   └──────────┬────────────────────┬──────────┘       │
│             │                      │                    │
│       ┌─────▼─────┐        ┌──────▼─────┐            │
│       │ GWS MCP    │        │ Composio   │            │
│       │ Bridge     │        │ MCP        │            │
│       │ (new)      │        │ (existing) │            │
│       └─────┬─────┘        └─────┬──────┘            │
└─────────────┤────────────────┤────────────────────┘
              │                │
       ┌──────▼────┐     ┌────▼──────┐
       │ gws mcp   │     │ Composio  │
       │ (stdio)   │     │ Server    │
       └────┬──────┘     └───────────┘
            │
      ┌─────▼─────┐
      │ Google    │
      │ Workspace │
      │ APIs      │
      └───────────┘
```

### 2.2 How It Works

When `UA_ENABLE_GWS_CLI=1` and the `gws` binary is available, the UA starts a `gws mcp` subprocess alongside the existing Composio MCP server. Both sets of tools are registered in the agent's tool registry. The LLM selects the appropriate tool based on the task.

| Path | When Used |
|------|-----------|
| **gws MCP tools** | All Google Workspace operations (Gmail, Calendar, Drive, Sheets, etc.) |
| **Composio MCP tools** | Cross-SaaS workflows (Google + Slack + GitHub), fallback when `gws` is disabled or unavailable |

### 2.3 Key Principle: MCP-Native

The UA already speaks MCP natively (stdio transport, tool discovery, structured JSON). `gws mcp` is a standard MCP server. Integration is therefore a **configuration and bridge** problem, not an architecture change.

---

## 3. Prerequisites

### 3.1 Binary Installation

`gws` is distributed as a Rust binary via npm or direct download:

```bash
# Option A: npm (recommended for version pinning)
npm install -g @googleworkspace/cli

# Option B: cargo
cargo install gws

# Option C: Direct binary download (Linux x86-64)
# Download from GitHub releases: https://github.com/googleworkspace/cli/releases
```

**Version requirement:** ≥ 0.3.1 (first version with stable MCP server + workflow helpers)

### 3.2 Google Cloud Project Setup

`gws` requires a Google Cloud project with OAuth credentials:

```bash
# Automated setup (requires gcloud CLI)
gws auth setup

# Manual setup:
# 1. Create OAuth 2.0 Client ID (Desktop application) in Google Cloud Console
# 2. Download client_secret.json
# 3. Place in gws config dir or set GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE
```

### 3.3 Authentication

```bash
# Interactive OAuth login (one-time)
gws auth login

# Verify
gws auth status
```

**Auth environment variable precedence** (in `gws`):
1. `GOOGLE_WORKSPACE_CLI_TOKEN` — raw access token (highest priority)
2. `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` — path to service account or authorized user JSON
3. Encrypted `credentials.enc` in config directory
4. Plaintext `credentials.json` in config directory

For UA production, **Option 2 (service account)** or **encrypted credentials** is recommended.

### 3.4 Dependencies to Add

```toml
# No Python dependencies needed — gws is an external binary
# The UA communicates with it via MCP stdio protocol
```

The only requirement is that the `gws` binary is on `$PATH` at runtime.

---

## 4. Phase 0 — Install & Auth Bootstrap

**Goal:** Verify `gws` binary works end-to-end on the deployment host.

### 4.0.1 Install binary

```bash
npm install -g @googleworkspace/cli
gws --version  # expect ≥ 0.3.1
```

### 4.0.2 Authenticate

```bash
# For development: interactive OAuth
gws auth login

# For VPS/production: service account with domain-wide delegation
# Set GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=/path/to/service-account.json
# Set GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER=kevin@domain.com
```

### 4.0.3 Smoke test

```bash
# Test Gmail access
gws gmail users.messages.list --params '{"userId": "me", "maxResults": 3}'

# Test Calendar access
gws calendar events.list --params '{"calendarId": "primary", "maxResults": 3}'

# Test MCP server startup
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | gws mcp -s gmail,calendar,drive,sheets

# Test helper commands
gws gmail +triage
gws calendar +agenda
```

### 4.0.4 Acceptance

- [ ] `gws --version` returns ≥ 0.3.1
- [ ] `gws auth status` shows authenticated
- [ ] Gmail list returns messages
- [ ] Calendar list returns events
- [ ] MCP initialize handshake succeeds

---

## 5. Phase 1 — MCP Bridge Integration

**Goal:** Wire `gws mcp` as an MCP server in the UA runtime, register its tools.

### 5.1 MCP Server Configuration

Create or update the UA's MCP server configuration to include `gws`:

**File to create:** `src/universal_agent/services/gws_mcp_bridge.py`

```python
"""
Bridge module for Google Workspace CLI MCP server.

Manages the gws MCP subprocess lifecycle and provides
tool invocation through the UA's MCP client infrastructure.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GwsMcpConfig:
    """Configuration for the gws MCP server subprocess."""
    binary_path: str = "gws"  # Override with full path if not on $PATH
    services: list[str] = field(default_factory=lambda: [
        "gmail", "calendar", "drive", "sheets"
    ])
    enable_helpers: bool = True
    enable_workflows: bool = True
    sanitize: bool = False  # Enable Model Armor sanitization
    extra_args: list[str] = field(default_factory=list)

    def build_args(self) -> list[str]:
        args = ["mcp", "-s", ",".join(self.services)]
        if self.enable_helpers:
            args.append("-e")
        if self.enable_workflows:
            args.append("-w")
        if self.sanitize:
            args.append("--sanitize")
        args.extend(self.extra_args)
        return args


def is_gws_available(binary_path: str = "gws") -> bool:
    """Check if the gws binary is available on $PATH."""
    return shutil.which(binary_path) is not None


async def start_gws_mcp_server(
    config: GwsMcpConfig,
) -> asyncio.subprocess.Process:
    """
    Start the gws MCP server as a subprocess.

    Returns the subprocess handle. Communication happens
    via stdin/stdout using the MCP stdio protocol.
    """
    if not is_gws_available(config.binary_path):
        raise RuntimeError(
            f"gws binary not found at '{config.binary_path}'. "
            "Install with: npm install -g @googleworkspace/cli"
        )

    args = config.build_args()
    logger.info("Starting gws MCP server: %s %s", config.binary_path, " ".join(args))

    process = await asyncio.create_subprocess_exec(
        config.binary_path,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    logger.info("gws MCP server started (pid=%s)", process.pid)
    return process
```

### 5.2 Integration Point: Agent Setup

The UA's `agent_setup.py` initializes MCP servers. The `gws` MCP server should be registered alongside existing MCP servers.

**File to modify:** `src/universal_agent/agent_setup.py`

The implementation should:
1. Check if `UA_ENABLE_GWS_CLI` feature flag is enabled
2. Check if `gws` binary is available via `is_gws_available()`
3. If both true, start the `gws mcp` subprocess
4. Register its tools in the agent's tool registry
5. On shutdown, gracefully terminate the subprocess

### 5.3 MCP Client Wiring

The UA already has MCP client infrastructure for stdio transport. The `gws` MCP server is wired identically to any other MCP server:

```json
{
  "mcpServers": {
    "gws": {
      "command": "gws",
      "args": ["mcp", "-s", "gmail,calendar,drive,sheets", "-e", "-w"]
    }
  }
}
```

**Implementation notes:**
- The `gws mcp` server exposes tools with names like `gmail.users.messages.list`, `drive.files.list`, etc.
- Helper tools are exposed as `gmail.+send`, `calendar.+agenda`, etc.
- Workflow tools are exposed as `workflow.+standup-report`, etc.
- All tool inputs/outputs are structured JSON

### 5.4 Tool Name Mapping

`gws` MCP tool names follow Google API conventions. The implementer should create a thin mapping layer if the agent needs friendlier tool names:

| gws MCP Tool | Agent-Friendly Name (optional) |
|--------------|-------------------------------|
| `gmail.users.messages.list` | `google_gmail_list_messages` |
| `gmail.+send` | `google_gmail_send_email` |
| `gmail.+triage` | `google_gmail_inbox_triage` |
| `calendar.events.list` | `google_calendar_list_events` |
| `calendar.+agenda` | `google_calendar_agenda` |
| `calendar.+insert` | `google_calendar_create_event` |
| `drive.files.list` | `google_drive_list_files` |
| `drive.+upload` | `google_drive_upload_file` |
| `sheets.spreadsheets.values.get` | `google_sheets_read` |
| `sheets.+append` | `google_sheets_append_row` |
| `workflow.+standup-report` | `google_workflow_standup` |
| `workflow.+meeting-prep` | `google_workflow_meeting_prep` |
| `workflow.+email-to-task` | `google_workflow_email_to_task` |

**Decision for implementer:** Either expose `gws` tool names directly (simpler, agent learns them) or create a mapping wrapper (more consistent with existing UA tool naming). Recommend starting with direct exposure and adding aliases only if the LLM struggles with the naming.

### 5.5 Phase 1 Acceptance Criteria

- [ ] `gws mcp` subprocess starts successfully during UA boot when feature flag is enabled
- [ ] MCP tool discovery returns Google Workspace tools
- [ ] Agent can invoke a Gmail read operation through the `gws` MCP bridge
- [ ] Agent can invoke a Calendar list operation through the `gws` MCP bridge
- [ ] Subprocess is gracefully terminated on UA shutdown
- [ ] Falls back to Composio tools when `gws` is unavailable or flag is disabled

---

## 6. Phase 2 — Feature Flag & Config

**Goal:** Add a new feature flag and configuration to control the `gws` MCP integration. This is built fresh — no dependency on any prior prototype code.

### 6.1 New Feature Flag

**File to modify:** `src/universal_agent/feature_flags.py`

Add a new function following the existing UA feature flag pattern:

```python
def gws_cli_enabled(default: bool = False) -> bool:
    """Enable Google Workspace CLI (gws) as primary MCP execution path."""
    if _is_truthy(os.getenv("UA_DISABLE_GWS_CLI")):
        return False
    if _is_truthy(os.getenv("UA_ENABLE_GWS_CLI")):
        return True
    return default
```

### 6.2 Bridge Configuration

The `GwsMcpConfig` dataclass created in Phase 1 (inside `gws_mcp_bridge.py`) is the only configuration needed. It controls:
- Which `gws` services to expose (`gmail`, `calendar`, `drive`, `sheets`, etc.)
- Whether helpers and workflows are enabled
- The binary path override

No separate config module or routing layer is required — the MCP bridge config + feature flag is sufficient.

### 6.3 .env.sample Update

Add to `.env.sample`:

```bash
# --- Google Workspace CLI (gws) ---
UA_ENABLE_GWS_CLI=0                          # Enable gws as primary Google Workspace MCP path
# UA_GWS_BINARY_PATH=gws                     # Override path to gws binary
# UA_GWS_SERVICES=gmail,calendar,drive,sheets # Which services to expose
# GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=      # Service account JSON for gws auth
# GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER=     # User to impersonate (domain-wide delegation)
```

### 6.4 Phase 2 Acceptance Criteria

- [ ] `gws_cli_enabled()` feature flag function exists in `feature_flags.py`
- [ ] `UA_ENABLE_GWS_CLI=1` activates the gws MCP bridge at startup
- [ ] `UA_ENABLE_GWS_CLI=0` (default) means all Google traffic stays on Composio
- [ ] `.env.sample` documents all gws-related env vars
- [ ] Unit test verifies feature flag behavior

---

## 7. Phase 3 — Composio Google Connector Cleanup

**Goal:** Remove Composio Google Workspace connectors and update all hardwired references across the codebase so the agent no longer sees or uses Composio Google tools. This prevents confusion where both `gws` MCP tools and Composio Google tools are available simultaneously with overlapping functionality.

> **Timing:** This phase should execute **after** Phase 1 (gws MCP bridge is working) and Phase 2 (feature flag is active), so that `gws` tools are available before Composio Google tools are removed.

### 7.1 Composio Connectors to Disconnect/Uninstall

Remove these Google Workspace connectors from the Composio platform (via Composio dashboard or CLI):

| Composio Connector | Tool Prefix | Replacement |
|---------------------|-------------|-------------|
| `gmail` | `GMAIL_*` | `gws gmail` MCP tools |
| `googlecalendar` | `GOOGLECALENDAR_*` | `gws calendar` MCP tools |
| `googledrive` | `GOOGLEDRIVE_*` | `gws drive` MCP tools |
| `googlesheets` | `GOOGLESHEETS_*` | `gws sheets` MCP tools |
| `googledocs` | `GOOGLEDOCS_*` | `gws docs` MCP tools |

**Keep connected** (not Google Workspace):
- `github`, `slack`, `discord`, `reddit`, `telegram`, `notion`, `figma`, `composio_search`, `codeinterpreter`, `browserbase`, `serpapi`, `google_maps`, etc.

Once these connectors are removed, the dynamically-generated `capabilities.md` will no longer list them, which is the desired outcome.

### 7.2 Runtime Code — Hardwired Composio Google References

The following source files contain hardwired references to Composio Google tool names (e.g., `GMAIL_SEND_EMAIL`, `GOOGLECALENDAR_CREATE_EVENT`) that must be updated to reference `gws` MCP tool names instead.

#### Category A: System Prompts & Prompt Builder (HIGH PRIORITY)

| File | Lines | What to change |
|------|-------|---------------|
| `src/universal_agent/prompt_builder.py:258` | `mcp__composio__*` namespace listing mentions "Gmail, Calendar" | Update to list `gws` MCP tools for Google Workspace |
| `src/universal_agent/prompt_builder.py:285` | Communication section: `Gmail (mcp__composio__GMAIL_*)`, `Calendar (mcp__composio__GOOGLECALENDAR_*)` | Replace with `gws` MCP tool references |
| `src/universal_agent/prompt_builder.py:312` | Delegation hint: "Multi-channel delivery? → action-coordinator (Gmail + Slack + Calendar)" | Update tool path |
| `src/universal_agent/prompt_builder.py:322` | Handoff chain: `upload_to_composio -> GMAIL_SEND_EMAIL` | Replace with `gws` email workflow |
| `src/universal_agent/prompt_builder.py:345-349` | Example actions: `GOOGLECALENDAR_CREATE_EVENT`, `GOOGLEDRIVE_*`, `GOOGLESHEETS_*` | Replace with `gws` equivalents |
| `src/universal_agent/agent_core.py:1506` | Subagent prompt: `mcp__composio__*` lists "Gmail, Slack, Search" | Update Google tools to `gws` |
| `src/universal_agent/agent_core.py:514` | Composio SDK block message: "Use `GMAIL_SEND_EMAIL` tool directly" | Update to `gws` tool name |

#### Category B: Agent Definitions & Skills (HIGH PRIORITY)

| File | What to change |
|------|---------------|
| `.claude/agents/action-coordinator.md` | Tools list: `GMAIL_SEND_EMAIL`, `GMAIL_CREATE_EMAIL_DRAFT`, `GOOGLECALENDAR_CREATE_EVENT`, `GOOGLEDRIVE_UPLOAD_FILE`; workflow steps referencing Composio Gmail | Replace all with `gws` MCP tool references |
| `.claude/agents/task-decomposer.md` | "Composio tools first" doctrine, handoff examples (`upload_to_composio -> GMAIL_SEND_EMAIL`), tool-type tables | Rewrite to reference `gws` for Google ops, keep Composio for non-Google |
| `.claude/skills/gmail/SKILL.md` | **Entire file** is a Composio Gmail skill with `GMAIL_SEND_EMAIL`, `upload_to_composio` workflow | Rewrite as `gws` Gmail skill, or delete and rely on `gws` MCP tool discovery |
| `.claude/skills/google_calendar/SKILL.md` | **Entire file** references Composio `GOOGLECALENDAR_*` tools | Rewrite as `gws` Calendar skill, or delete |
| `.claude/knowledge/email_identity.md` | Gmail vs AgentMail routing table references "Gmail (Composio)" | Update Gmail path to reference `gws` tools |
| `.claude/knowledge/tool_guardrails.md` | `GMAIL_SEND_EMAIL` usage guide, `upload_to_composio` for attachments | Rewrite email sending workflow for `gws` |

#### Category C: Hooks & Guardrails (MEDIUM PRIORITY — update when gws is working)

| File | Lines | What to change |
|------|-------|---------------|
| `src/universal_agent/hooks.py:1204` | Composio SDK guard references `gmail_send_email` | Update tool name in guard pattern |
| `src/universal_agent/hooks.py:1220-1221` | Block message mentions `upload_to_composio -> GMAIL_SEND_EMAIL` flow | Update to `gws` email flow |
| `src/universal_agent/main.py:2566-2592` | `on_post_email_send_artifact` hook parses `GMAIL_SEND_EMAIL` tool responses | Update to parse `gws` email tool responses |
| `src/universal_agent/main.py:2710` | Composio SDK guard pattern: `gmail_send_email` | Update tool name |
| `src/universal_agent/main.py:2750` | Block message: "Use `GMAIL_SEND_EMAIL` tool directly" | Update to `gws` tool |
| `src/universal_agent/main.py:3174-3176` | Next-step hint: `upload_to_composio then GMAIL_SEND_EMAIL`, `GOOGLECALENDAR_CREATE_EVENT` | Update to `gws` tools |
| `src/universal_agent/main.py:3196` | Bowser hint: "Composio Gmail/Slack tools" | Update |
| `src/universal_agent/main.py:3439` | Delegation flow: "Send email using GMAIL_SEND_EMAIL" | Update |
| `src/universal_agent/guardrails/tool_schema.py:46-47` | `upload_to_composio` example references `GMAIL_SEND_EMAIL` | Update |
| `src/universal_agent/guardrails/tool_schema.py:95` | `COMPOSIO_MULTI_EXECUTE_TOOL` example references `GMAIL_SEND_EMAIL` | Update |
| `src/universal_agent/guardrails/tool_schema.py:113-117` | `gmail_send_email` schema definition | Replace with `gws` email tool schema |

#### Category D: Utility & Detection Code (MEDIUM PRIORITY)

| File | What to change |
|------|---------------|
| `src/universal_agent/cli_io.py:556-561` | Email send detection checks for `GMAIL_SEND_EMAIL` | Update to detect `gws` email tool calls |
| `src/universal_agent/mission_guardrails.py:14-21` | Gmail regex patterns for mission contract evaluation | Update to include `gws` tool patterns |
| `src/universal_agent/mission_guardrails.py:51-70` | `gmail_send_count` tracking, `_is_gmail_send_tool()` | Update to detect `gws` email sends |
| `src/universal_agent/utils/email_attachment_prep.py:64-69` | Gmail attachment rendering checks `toolkit_slug == "gmail"` | Update for `gws` attachment flow |
| `src/universal_agent/session_policy.py:14` | Email regex includes `gmail` keyword | Keep as-is (user-facing term, not tool name) |

#### Category E: Agent Setup & Discovery (LOW PRIORITY — update after connectors removed)

| File | What to change |
|------|---------------|
| `src/universal_agent/agent_setup.py:291` | `core_apps` list includes `gmail`, `googlecalendar`, `googlesheets`, `googledocs` | Remove Google apps from Composio core list |
| `src/universal_agent/agent_setup.py:559-560` | Operations group includes `gmail`, `googlecalendar` | Update to reference `gws` capability |
| `src/universal_agent/utils/composio_discovery.py:141-152` | Hardcoded descriptions for `gmail`, `googlecalendar`, `googledrive`, `googlesheets` | Remove (connectors will be gone) |
| `src/universal_agent/main.py:7568` | `ALLOWED_APPS` fallback includes `gmail` | Remove `gmail` from Composio fallback list |
| `src/universal_agent/main.py:3009` | Subagent skill mapping: `action-coordinator` → `["gmail"]` | Update to reference `gws` skill |

#### Category F: Knowledge Base & AgentMail Skill (LOW PRIORITY)

| File | What to change |
|------|---------------|
| `.agents/skills/agentmail/SKILL.md:14,17,23-24` | "Gmail is separate: Gmail (via Composio)" routing table | Update Gmail references to `gws` path |
| `src/universal_agent/prompt_assets/capabilities.md` | Dynamically generated — will auto-update once connectors are removed |
| `src/universal_agent/prompt_assets/capabilities.last_good.md` | Cached snapshot — regenerate after connectors removed |

#### Category G: Historical Documents (NO CHANGE NEEDED)

These files contain references in historical context (memory logs, run reviews, archived sessions). They document what happened and should **not** be edited:
- `Memory_System/ua_shared_workspace/memory/2026-02-*.md`
- `memory/2026-02-*.md`
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/*.md` (existing docs)
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/*.md`
- `AGENT_RUN_WORKSPACES*/` and `AGENT_RUN_WORKSPACES_ARCHIVE/`
- `RLM/` corpus files
- `education/` reference docs

### 7.3 Attachment Workflow Change

The current email attachment flow is:
```
Local file → upload_to_composio(s3key) → GMAIL_SEND_EMAIL(attachment: {s3key})
```

With `gws`, the flow simplifies to:
```
Local file → gws gmail +send --to ... --attachment /path/to/file
```

The `upload_to_composio` step is **no longer needed for Google email attachments** — `gws` handles file upload natively. The `upload_to_composio` MCP tool should be retained for non-Google Composio tools (Slack file uploads, etc.).

### 7.4 Phase 3 Execution Order

1. **Verify `gws` MCP is working** (Phase 1-2 complete)
2. **Update system prompts & prompt builder** (Category A) — highest impact, changes what the LLM sees
3. **Update agent definitions & skills** (Category B) — changes how subagents behave
4. **Update hooks & guardrails** (Category C) — changes runtime safety checks
5. **Update utility code** (Category D) — changes detection/tracking
6. **Disconnect Composio Google connectors** (Section 7.1) — removes old tools from discovery
7. **Regenerate `capabilities.md`** — run capability discovery to confirm Google tools are gone from Composio and present via `gws`
8. **Update agent setup & discovery** (Category E) — remove Google from Composio core apps
9. **Update knowledge base** (Category F) — align docs with new reality

### 7.5 Phase 3 Acceptance Criteria

- [ ] Composio Google connectors (gmail, googlecalendar, googledrive, googlesheets, googledocs) are disconnected
- [ ] `capabilities.md` no longer lists Composio Google tools
- [ ] `capabilities.md` lists `gws` MCP Google Workspace tools
- [ ] System prompts reference `gws` tools for Google Workspace operations
- [ ] Agent definitions (action-coordinator, task-decomposer) use `gws` tool names
- [ ] Gmail and Google Calendar skills updated or replaced
- [ ] Email sending guardrails and hooks detect `gws` email tool calls
- [ ] Email attachment flow works via `gws` native attachment support
- [ ] Mission guardrails track `gws` email sends correctly
- [ ] No remaining runtime references to `mcp__composio__GMAIL_*` or `mcp__composio__GOOGLECALENDAR_*`

---

## 8. Phase 4 — Agent Skills & Helpers

**Goal:** Expose `gws` helper commands and workflow skills to the agent for common operations.

### 8.1 High-Value Helper Tools

These `gws` helper tools provide significant UX improvements over raw API calls:

| Helper | Service | What it does | Priority |
|--------|---------|-------------|----------|
| `gmail +triage` | Gmail | Unread inbox summary (sender, subject, date) | **P0** |
| `gmail +send` | Gmail | Send email with simplified args | **P0** |
| `gmail +watch` | Gmail | Stream new emails as NDJSON | P1 |
| `calendar +agenda` | Calendar | Upcoming events across all calendars | **P0** |
| `calendar +insert` | Calendar | Create event with simplified args | **P0** |
| `drive +upload` | Drive | Upload file with auto-metadata | P1 |
| `sheets +append` | Sheets | Append row to spreadsheet | **P0** |
| `sheets +read` | Sheets | Read spreadsheet values | **P0** |

### 8.2 High-Value Workflow Tools

| Workflow | Services Used | What it does | Priority |
|----------|--------------|-------------|----------|
| `workflow +standup-report` | Calendar + Tasks | Today's meetings + open tasks summary | **P0** |
| `workflow +meeting-prep` | Calendar + Docs | Next meeting agenda, attendees, linked docs | P1 |
| `workflow +email-to-task` | Gmail + Tasks | Convert email to task entry | P1 |
| `workflow +weekly-digest` | Calendar + Gmail | Weekly meeting summary + unread count | P1 |
| `workflow +file-announce` | Drive + Chat | Announce file in Chat space | P2 |

### 8.3 Skill Registration

The `gws` skill files (`SKILL.md`) can be loaded by the UA's skill system. Two options:

**Option A: Direct MCP tool exposure (recommended for Phase 4)**
- The `gws mcp` server with `-e` (helpers) and `-w` (workflows) flags already exposes these as MCP tools
- No additional registration needed beyond Phase 1 wiring

**Option B: UA skill wrappers (optional, for enhanced behavior)**
- Create thin wrapper skills in `.agents/skills/` that reference the `gws` MCP tools
- Add UA-specific behavior (e.g., auto-save email drafts, confirmation before send)

### 8.4 Phase 4 Acceptance Criteria

- [ ] Agent can use `gmail +triage` to get inbox summary
- [ ] Agent can use `gmail +send` to send emails
- [ ] Agent can use `calendar +agenda` to check schedule
- [ ] Agent can use `sheets +append` to add data
- [ ] Agent can use `workflow +standup-report` for daily standup
- [ ] Helper and workflow tools appear in agent's tool list

---

## 9. Phase 5 — Workspace Events (Streaming)

**Goal:** Enable real-time Google Workspace event streaming via `gws` helpers.

### 9.1 Events Helper

`gws` includes an `events` service helper:

```bash
# Subscribe to Workspace events (streams NDJSON)
gws events +subscribe --params '{
  "targetResource": "//cloudresourcemanager.googleapis.com/projects/PROJECT_ID",
  "eventTypes": ["google.workspace.events.calendar.changed"],
  "payloadOptions": {"includeResource": true}
}'

# Renew subscription
gws events +renew --params '{"name": "subscriptions/SUB_ID"}'
```

### 9.2 Gmail Watch

```bash
# Stream new emails as NDJSON
gws gmail +watch --params '{"userId": "me"}'
```

### 9.3 Integration Approach

The UA's hook system can consume these NDJSON streams:

1. Start `gws gmail +watch` as a long-running subprocess
2. Parse NDJSON lines from stdout
3. Dispatch each event through `hooks_service.dispatch_internal_payload()`
4. Handle reconnection on subprocess exit

**File to create:** `src/universal_agent/services/gws_event_listener.py`

This module manages long-running `gws` event streams, similar to the existing YouTube playlist watcher pattern in `src/universal_agent/services/youtube_playlist_watcher.py`.

### 9.4 Phase 5 Acceptance Criteria

- [ ] Gmail watch stream starts and receives new email notifications
- [ ] Events are dispatched through the UA hook system
- [ ] Subprocess auto-restarts on crash/disconnect
- [ ] Feature flag `UA_ENABLE_GOOGLE_WORKSPACE_EVENTS` gates this
- [ ] Graceful shutdown terminates the watcher subprocess

---

## 10. Phase 6 — Hardening & Rollout

**Goal:** Production-ready deployment with monitoring, rollback capability, and documentation.

### 10.1 Monitoring

Add Logfire spans for:
- `gws_mcp_tool_call` — each tool invocation with service, method, duration, status
- `gws_mcp_error` — errors with classification (auth, scope, rate_limit, transient, permanent)
- `gws_mcp_fallback` — when routing falls back from GWS to Composio
- `gws_mcp_lifecycle` — subprocess start, stop, crash events

### 10.2 Error Handling

Build error handling directly into the `gws_mcp_bridge.py` module. `gws` returns structured JSON errors:

```json
{"error": {"code": 403, "message": "...", "status": "PERMISSION_DENIED"}}
```

The bridge should classify these and decide:
- **429 / 5xx** → Retry with backoff
- **401** → Log auth failure, surface to user
- **403 insufficient scopes** → Log scope issue, surface to user
- **Other permanent errors** → Log and report failure

This logic lives in the bridge module itself — no separate error policy module needed.

### 10.3 Rollout Plan

| Stage | `UA_ENABLE_GWS_CLI` | Behavior |
|-------|---------------------|----------|
| **Stage 0** | `0` (default) | All Google traffic via Composio (current state) |
| **Stage 1** | `1` | Both `gws` MCP and Composio tools available; LLM uses both, operator validates |
| **Stage 2** | `1` | `gws` tools are preferred; Composio Google tools can be removed from Composio config |
| **Stage 3** | `1` | `gws` is the sole Google Workspace path; Composio only for non-Google SaaS |

### 10.4 Rollback

Set `UA_ENABLE_GWS_CLI=0` to immediately revert all Google Workspace traffic to Composio. No code deployment needed.

### 10.5 VPS Deployment

On the production VPS:
1. Install `gws` binary (add to provisioning script)
2. Place service account credentials or run `gws auth login`
3. Set `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` in environment
4. Set `UA_ENABLE_GWS_CLI=1` to activate

### 10.6 Phase 6 Acceptance Criteria

- [ ] Logfire traces show gws MCP tool calls with timing and status
- [ ] Error classification works for gws JSON error responses
- [ ] Rollback via feature flag is verified (disable flag → all traffic to Composio)
- [ ] VPS deployment runbook includes gws installation steps
- [ ] Monitoring dashboard shows gws vs Composio traffic split

---

## 11. Cleanup — Remove Planning Prototype

A prior planning session created a prototype scaffold in `src/universal_agent/services/google_workspace/`. This was a design exploration exercise — never deployed to production, never wired into the agent runtime. It should be **deleted** as part of this work to avoid confusion.

### Files to Delete

| File/Directory | Why |
|----------------|-----|
| `src/universal_agent/services/google_workspace/` (entire directory) | Planning prototype — superseded by `gws` MCP approach |
| `tests/unit/test_google_workspace_scaffold.py` | Tests for the deleted prototype |
| Feature flags in `feature_flags.py`: `google_direct_enabled()`, `google_direct_allow_composio_fallback()`, `google_workspace_events_enabled()` | Prototype flags — replaced by new `gws_cli_enabled()` |
| Export of `google_workspace` in `src/universal_agent/services/__init__.py` | Reference to deleted prototype |

This cleanup can happen at any point during implementation (Phase 0 through Phase 2).

---

## 12. Environment Variables Reference

### New Variables

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `UA_ENABLE_GWS_CLI` | bool | `0` | Enable gws CLI as primary Google Workspace execution path |
| `UA_DISABLE_GWS_CLI` | bool | `0` | Force-disable gws CLI (overrides enable) |
| `UA_GWS_BINARY_PATH` | string | `gws` | Path to gws binary (if not on $PATH) |
| `UA_GWS_SERVICES` | string | `gmail,calendar,drive,sheets` | Comma-separated list of services to expose via MCP |
| `UA_GWS_ENABLE_HELPERS` | bool | `1` | Expose helper tools (+send, +triage, etc.) |
| `UA_GWS_ENABLE_WORKFLOWS` | bool | `1` | Expose workflow tools (+standup-report, etc.) |
| `UA_GWS_ENABLE_SANITIZE` | bool | `0` | Enable Model Armor response sanitization |
| `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` | path | none | Service account or authorized user JSON for gws auth |
| `GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER` | email | none | User to impersonate (service account + domain-wide delegation) |
| `GOOGLE_WORKSPACE_CLI_TOKEN` | string | none | Direct access token (highest priority, useful for CI) |

### Prototype Variables (To Be Removed)

The following env vars were created during the planning prototype phase and should be removed during cleanup (see Section 11):
- `UA_ENABLE_GOOGLE_DIRECT` / `UA_DISABLE_GOOGLE_DIRECT`
- `UA_ENABLE_GOOGLE_DIRECT_FALLBACK` / `UA_DISABLE_GOOGLE_DIRECT_FALLBACK`
- `UA_ENABLE_GOOGLE_WORKSPACE_EVENTS` / `UA_DISABLE_GOOGLE_WORKSPACE_EVENTS`

---

## 13. File Inventory

### Files to Create

| File | Phase | Purpose |
|------|-------|---------|
| `src/universal_agent/services/gws_mcp_bridge.py` | 1 | MCP subprocess manager, config, availability check |
| `src/universal_agent/services/gws_event_listener.py` | 4 | Long-running NDJSON event stream consumer |
| `tests/unit/test_gws_mcp_bridge.py` | 1-2 | Unit tests for bridge module and feature flag |
| `tests/integration/test_gws_mcp_smoke.py` | 1 | Integration smoke test (requires gws binary) |

### Files to Modify

| File | Phase | Changes |
|------|-------|---------|
| `src/universal_agent/feature_flags.py` | 2 | Add `gws_cli_enabled()` function |
| `src/universal_agent/agent_setup.py` | 1, 3 | Register gws MCP server on startup; remove Google from Composio core_apps |
| `src/universal_agent/services/__init__.py` | 1 | Export new bridge module (remove prototype export) |
| `.env.sample` | 2 | Add gws env vars |
| `src/universal_agent/prompt_builder.py` | 3 | Replace Composio Google tool refs with `gws` MCP refs |
| `src/universal_agent/agent_core.py` | 3 | Update subagent prompt and SDK block message |
| `src/universal_agent/hooks.py` | 3 | Update Composio SDK guard to reference `gws` tools |
| `src/universal_agent/main.py` | 3 | Update email artifact hook, SDK guard, next-step hints |
| `src/universal_agent/guardrails/tool_schema.py` | 3 | Replace `gmail_send_email` schema with `gws` tool schema |
| `src/universal_agent/cli_io.py` | 3 | Update email send detection |
| `src/universal_agent/mission_guardrails.py` | 3 | Update Gmail send tracking for `gws` tool names |
| `src/universal_agent/utils/email_attachment_prep.py` | 3 | Update for `gws` native attachment flow |
| `src/universal_agent/utils/composio_discovery.py` | 3 | Remove hardcoded Google connector descriptions |
| `.claude/agents/action-coordinator.md` | 3 | Replace Composio Google tool refs with `gws` |
| `.claude/agents/task-decomposer.md` | 3 | Update handoff examples and tool-type tables |
| `.claude/skills/gmail/SKILL.md` | 3 | Rewrite as `gws` Gmail skill |
| `.claude/skills/google_calendar/SKILL.md` | 3 | Rewrite as `gws` Calendar skill |
| `.claude/knowledge/email_identity.md` | 3 | Update Gmail routing to reference `gws` |
| `.claude/knowledge/tool_guardrails.md` | 3 | Rewrite email sending workflow for `gws` |
| `.agents/skills/agentmail/SKILL.md` | 3 | Update Gmail routing references |

### Files to Delete (planning prototype cleanup — see Section 11)

| File/Directory | Phase |
|----------------|-------|
| `src/universal_agent/services/google_workspace/` | Any |
| `tests/unit/test_google_workspace_scaffold.py` | Any |
| Prototype flag functions in `feature_flags.py` (lines 200-225) | Any |

---

## 14. Testing Strategy

### 14.1 Unit Tests (No gws binary required)

| Test | What it verifies |
|------|-----------------|
| `GwsMcpConfig.build_args()` produces correct CLI arguments | Config → args mapping |
| `is_gws_available()` returns False when binary missing | Graceful degradation |
| `gws_cli_enabled()` respects enable/disable env vars | Feature flag logic |
| Bridge skips startup when feature flag is disabled | Conditional activation |

### 14.2 Integration Tests (Requires gws binary + auth)

| Test | What it verifies |
|------|-----------------|
| MCP initialize handshake succeeds | Subprocess lifecycle |
| Tool discovery returns expected Gmail/Calendar/Drive/Sheets tools | Tool registration |
| `gmail.users.messages.list` returns valid JSON | End-to-end API call |
| `gmail.+triage` returns inbox summary | Helper tool execution |
| `calendar.+agenda` returns events | Helper tool execution |
| Subprocess shutdown is clean (no zombie processes) | Lifecycle management |

### 14.3 Test Markers

```python
import pytest

# Mark integration tests that require gws binary
pytestmark_gws = pytest.mark.skipif(
    not shutil.which("gws"),
    reason="gws binary not available"
)
```

---

## 15. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `gws` binary not available on VPS | Medium | High | Fallback to Composio via feature flag; add to provisioning script |
| `gws` MCP server crashes mid-session | Low | Medium | Subprocess health check + auto-restart; Composio fallback |
| Google changes Discovery Document format | Very Low | High | `gws` is Google-maintained; they'll update it |
| Auth token expires during long session | Low | Medium | `gws` handles token refresh internally |
| Rate limiting from Google APIs | Medium | Low | `gws` propagates 429; bridge error handling retries with backoff |
| `gws` binary update introduces breaking changes | Low | Medium | Pin npm version; test before upgrade |
| MCP protocol version mismatch | Very Low | Medium | Pin `gws` version matching our MCP client expectations |
| Composio deprecates Google tools | Low | High | `gws` is the replacement path — this plan addresses it |

---

## 16. Acceptance Criteria

### Overall Success Criteria

- [ ] Agent can read Gmail inbox via `gws` MCP tools
- [ ] Agent can send emails via `gws gmail +send`
- [ ] Agent can check calendar via `gws calendar +agenda`
- [ ] Agent can create calendar events via `gws calendar +insert`
- [ ] Agent can read/write Sheets via `gws sheets +read/+append`
- [ ] Agent can upload files to Drive via `gws drive +upload`
- [ ] Agent can generate standup reports via `gws workflow +standup-report`
- [ ] Composio fallback works when `gws` is unavailable
- [ ] Feature flag rollback (`UA_ENABLE_GWS_CLI=0`) instantly reverts to Composio
- [ ] Logfire traces show tool calls with timing and error classification
- [ ] All unit tests pass
- [ ] Integration smoke tests pass on dev environment
- [ ] No regression in existing Composio-based Google Workspace functionality

### Phase Completion Gates

| Phase | Gate |
|-------|------|
| Phase 0 | gws installed, authenticated, smoke tests pass |
| Phase 1 | MCP bridge starts, tools discovered, basic operations work |
| Phase 2 | Feature flag works, .env.sample updated, unit tests pass |
| Phase 3 | Composio Google connectors removed, all references updated, capabilities.md clean |
| Phase 4 | Helper and workflow tools accessible to agent |
| Phase 5 | Event streaming works, dispatches to hook system |
| Phase 6 | Monitoring, rollout stages verified, VPS deployed |

---

## Appendix A: Quick Reference — gws CLI Commands

```bash
# Authentication
gws auth setup          # Create Google Cloud project + OAuth credentials
gws auth login          # Interactive OAuth login
gws auth status         # Show current auth state

# Gmail
gws gmail users.messages.list --params '{"userId":"me","maxResults":5}'
gws gmail +send --to user@example.com --subject "Hello" --body "..."
gws gmail +triage
gws gmail +watch --params '{"userId":"me"}'

# Calendar
gws calendar events.list --params '{"calendarId":"primary","maxResults":5}'
gws calendar +agenda
gws calendar +insert --summary "Meeting" --start "2026-03-07T10:00:00" --end "2026-03-07T11:00:00"

# Drive
gws drive files.list --params '{"pageSize":10}'
gws drive +upload --file /path/to/file

# Sheets
gws sheets spreadsheets.values.get --params '{"spreadsheetId":"ID","range":"Sheet1!A1:D10"}'
gws sheets +append --spreadsheet-id ID --range "Sheet1" --values '["a","b","c"]'
gws sheets +read --spreadsheet-id ID --range "Sheet1!A1:D10"

# Workflows
gws workflow +standup-report
gws workflow +meeting-prep
gws workflow +email-to-task --message-id MSG_ID
gws workflow +weekly-digest

# MCP Server
gws mcp -s gmail,calendar,drive,sheets -e -w

# Schema discovery
gws schema gmail.users.messages.list
gws schema calendar.events.insert
```

---

## Appendix B: gws Auth Environment Variable Precedence

```
1. GOOGLE_WORKSPACE_CLI_TOKEN          → raw access token (highest)
2. GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE → service account or authorized user JSON
3. ~/.config/gws/credentials.enc        → encrypted credentials (AES-256-GCM)
4. ~/.config/gws/credentials.json       → plaintext credentials (fallback)
```

For production VPS: use option 2 with a service account + domain-wide delegation.
For development: use option 3 (run `gws auth login` once).

---

## Appendix C: Why Not Strategy C (Custom Direct API)?

Strategy C was *designed* but never implemented. Here's what it *would have required* vs what `gws` provides for free:

| Dimension | Strategy C would have required | Strategy D (gws MCP) provides |
|-----------|-------------------------------|------------------------------|
| Auth implementation | ~500 lines custom OAuth flow (PKCE, callbacks, refresh) | Zero — `gws auth` handles it |
| API client code | ~200 lines per service × 4 services | Zero — `gws` discovers dynamically |
| Token storage | Custom encrypted vault with cipher injection | AES-256-GCM + OS keyring built-in |
| Scope management | Manual wave definitions, progressive consent UI | Auto-requests per-method scopes |
| Pagination | Per-API implementation | Built-in `--page-all` |
| Error handling | Custom HTTP error classifier module | Structured JSON errors from `gws`; handle in bridge |
| Maintenance | Track Google API changes manually | Auto-discovers via Discovery Service |
| Time to implement | 2-3 weeks | 2-3 days |
| Risk profile | Custom code = custom bugs | Google-maintained binary |
