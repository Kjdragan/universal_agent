# DOC-NEXT: Todoist Integration — Complete Handoff Package

## Universal Agent System — Task Management & Brainstorming Layer

**Status:** Ready for implementation by project AI coder  
**Origin:** Architecture session between Kevin and Claude Opus 4.6  
**Decision:** Replace TaskWarrior with Todoist as sole task management system  
**Scope:** This document contains everything needed to implement the integration  

---

## Document Structure

This handoff has two parts:

**Part A (Sections 1–7):** Todoist domain knowledge, API reference, service contract, JSON schemas, CLI spec, and environment setup. This is fully specified and implementation-ready. None of it depends on existing codebase internals.

**Part B (Sections 8–11):** Integration brief describing intent and constraints for the heartbeat adapter, agent tools, and brainstorming pipeline. These sections describe WHAT should happen and WHY, but deliberately leave the HOW to the implementer who has codebase access. Open questions are flagged explicitly.

---

# PART A — IMPLEMENTATION-READY SPECIFICATIONS

---

## 1. Background & Decisions Made

### 1.1 Why Todoist

Kevin's multi-agent system currently uses a 30-minute heartbeat cycle to check for proactive work. The system has cron-based job scheduling but lacks a rich, human-accessible task queue. The goals are:

- Let Kevin add tasks from any device (phone, browser, desktop) that the agent picks up automatically
- Give tasks priority levels, categorization, due dates, and context
- Support an idea/brainstorming funnel upstream of the execution queue
- Maintain an audit trail via comments on task state transitions
- Provide a shared interface where both humans and agents manage work

Todoist was chosen over TaskWarrior because it offers cloud sync, mobile/web access, a maintained Python SDK, native priority/label/section/project hierarchy, natural language date parsing, and a comment system for audit trails. TaskWarrior is local-only with no mobile access, which defeats the human-agent shared interface goal.

### 1.2 TaskWarrior Deprecation

The existing TaskWarrior integration (`mcp_server_taskwarrior.py`) is unused and should be removed. However:

- Remove it AFTER the Todoist integration is operational, not simultaneously
- The structural pattern of `mcp_server_taskwarrior.py` (narrowed tool surface with add/list/done/modify) is a valid reference for shaping the Todoist tool surface
- Clean up any TaskWarrior references in `internal_registry.py`, `agent_setup.py`, requirements files, and environment variables

### 1.3 Architecture Decision: 4-Layer Design

We agreed on this layered approach based on analysis of the current system (including the `force_complex=True` heartbeat path and the static tool bundle in `internal_registry.py`):

```
Layer 1: TodoService        — Pure Python, all Todoist API logic, no LLM awareness
Layer 2: Heartbeat adapter  — Deterministic pre-step, direct Python, no tool definitions
Layer 3: Agent tools        — Small tool surface for interactive/conversational use
Layer 4: CLI wrapper        — JSON-output CLI for bash invocation and debugging
```

The critical constraint: **the heartbeat path (Layer 2) must not require tool definitions in the context window**. It calls `TodoService` directly in Python. The LLM is only invoked when there are actual tasks to dispatch.

### 1.4 Architecture Decision: Integrated Brainstorming

Rather than treating brainstorming as a separate system, it shares the same Todoist infrastructure as the task queue. Ideas live in a separate Todoist project (`UA Brainstorm Pipeline`) with a section-based lifecycle. Ideas can be promoted to the agent task queue when approved. The brainstorming concept is still being developed — Section 10 describes the current thinking and flags open design questions.

---

## 2. Todoist API Reference

Everything the implementer needs to know about the Todoist API without independent research.

### 2.1 Package & Auth

```bash
pip install todoist-api-python
# Current version: 3.2.0 (released Jan 16, 2026)
# Requires: Python 3.9+
# License: MIT
# Dependency: add to requirements as todoist-api-python>=3.2.0,<4
```

**Auth:** Bearer token via `TODOIST_API_TOKEN` environment variable.  
**Obtain from:** Todoist web app → Settings → Integrations → Developer → API token.  
**Rate limits:** 450 requests per 15-minute window per token. Typical heartbeat cycle uses 10–30 requests.

### 2.2 SDK Initialization

```python
from todoist_api_python.api import TodoistAPI
from todoist_api_python.api_async import TodoistAPIAsync  # async variant

api = TodoistAPI("token_here")

# Async:
import asyncio
async_api = TodoistAPIAsync("token_here")
tasks = await async_api.get_tasks()
```

### 2.3 SDK Methods — Tasks

```python
# Fetch active tasks
tasks = api.get_tasks()                                    # all
tasks = api.get_tasks(project_id="12345")                  # by project
tasks = api.get_tasks(filter="today & @agent-ready")       # by filter
tasks = api.get_tasks(label="agent-ready")                 # by label

# Single task
task = api.get_task("task_id_string")

# Create
task = api.add_task(
    content="Task title",
    description="Detailed context",
    project_id="12345",
    section_id="67890",
    priority=4,            # ⚠️ SEE PRIORITY WARNING BELOW
    labels=["agent-ready", "sub-agent:research"],
    due_string="tomorrow at 9am",   # natural language parsing
    parent_id="parent_task_id",     # for subtasks
)

# Quick Add (natural language, Todoist parses inline syntax)
task = api.quick_add_task("Review PR tomorrow 2pm #AgentTasks @agent-ready p1")

# Update
api.update_task(task_id="id", content="New title", labels=["blocked"])

# Complete / Reopen / Delete
api.close_task("task_id")
api.reopen_task("task_id")
api.delete_task("task_id")
```

### 2.4 SDK Methods — Projects, Sections, Comments, Labels

```python
# Projects
projects = api.get_projects()
project = api.add_project(name="Agent Tasks")
api.update_project(project_id="id", name="New Name")
api.delete_project(project_id="id")

# Sections
sections = api.get_sections(project_id="12345")
section = api.add_section(name="Immediate", project_id="12345")

# Comments (paginated iterator)
comments_iter = api.get_comments(task_id="id")
for page in comments_iter:
    for comment in page:
        print(comment.content)

api.add_comment(task_id="id", content="**Agent note:** completed successfully.")

# Labels
labels = api.get_labels()
label = api.add_label(name="agent-ready")
```

### 2.5 Priority Inversion Warning

**⚠️ CRITICAL:** Todoist's API priority numbering is the INVERSE of the UI:

| UI Display | API Value | Meaning |
|---|---|---|
| P1 (red, urgent) | `priority=4` | Highest |
| P2 (orange) | `priority=3` | High |
| P3 (blue) | `priority=2` | Medium |
| P4 (no color) | `priority=1` | Low (default) |

The implementation MUST use a mapping. Never pass raw integers without it:

```python
PRIORITY_TO_API = {"urgent": 4, "high": 3, "medium": 2, "low": 1}
API_TO_DISPLAY = {4: "P1-Urgent", 3: "P2-High", 2: "P3-Medium", 1: "P4-Low"}
```

### 2.6 Task Object Fields

Each task returned by `api.get_tasks()` has:

```
task.id              str         unique task ID
task.content         str         task title
task.description     str         detailed description (can be empty)
task.priority        int         1-4 (see inversion warning)
task.project_id      str         parent project ID
task.section_id      str|None    section within project
task.parent_id       str|None    parent task (subtasks)
task.labels          list[str]   label names
task.due             Due|None    due date object
task.due.date        str         "2026-02-16"
task.due.datetime    str|None    "2026-02-16T14:00:00Z" (if time set)
task.due.is_recurring bool       recurring task flag
task.is_completed    bool
task.order           int         position within project/section
task.creator_id      str
task.assignee_id     str|None
task.created_at      str         ISO datetime
task.url             str         web URL
task.comment_count   int
```

### 2.7 Filter Syntax

Todoist filter strings are used for the heartbeat query. Key syntax:

```
today                      due today
overdue                    past due
no date                    no due date set
@label_name                has label
!@label_name               does NOT have label
#ProjectName               in project
p1, p2, p3, p4             by priority (UI naming)
(A | B)                    OR
A & B                      AND
assigned to: me            assigned tasks
```

**Default heartbeat filter:**
```
(overdue | today | no date) & @agent-ready & !@blocked
```

### 2.8 REST API Idempotency

The REST API supports `X-Request-Id` headers for idempotent writes. This is important for heartbeat retry scenarios. The SDK doesn't expose this directly, but the underlying `requests` calls can be wrapped to include it. Alternatively, implement application-level dedup (check before create).

### 2.9 Endpoints NOT in SDK

These Sync API endpoints require direct HTTP calls (useful for reporting, not needed for v1):

```python
# Productivity statistics
GET https://api.todoist.com/sync/v9/completed/get_stats
Headers: Authorization: Bearer {token}

# Completed task history
GET https://api.todoist.com/sync/v9/completed/get_all
Params: since={ISO_datetime}, limit=50
Headers: Authorization: Bearer {token}
```

---

## 3. Todoist Taxonomy

### 3.1 Projects

```
Agent Tasks              — Primary agent execution queue
UA Brainstorm Pipeline   — Idea lifecycle funnel (see Section 10)
```

### 3.2 Sections — Agent Tasks Project

```
Immediate    — P1 tasks, execute on next heartbeat
Scheduled    — Has due dates, respect timing
Background   — P3/P4 fill-idle-cycles work
Recurring    — Repeating tasks
```

### 3.3 Sections — UA Brainstorm Pipeline Project

```
Inbox                — Raw idea capture, no evaluation yet
Triaging             — Agent or human is evaluating the idea
Heartbeat Candidate  — Survived triage, being monitored for recurrence/evidence
Approved for Build   — Approved, ready to promote to Agent Tasks
In Implementation    — Promoted, linked to agent task(s)
Parked / Rejected    — Not pursuing, with rationale in comments
```

### 3.4 Labels

```
agent-ready             Cleared for autonomous agent execution
needs-review            Agent completed work, human should verify
blocked                 Waiting on dependency, skip during heartbeat
sub-agent:research      Route to research sub-agent
sub-agent:writer        Route to content generation sub-agent
sub-agent:code          Route to coding sub-agent
brainstorm              Marks a task as an idea (vs. actionable work)
heartbeat-candidate     Idea that the heartbeat should monitor
needs-spec              Idea needs further specification before promotion
approved                Idea approved for implementation
```

### 3.5 Task Lifecycle — Agent Tasks

```
[Created] → @agent-ready → [Executing] → ✅ Completed (closed)
                                │
                                ├→ @needs-review (agent-ready removed)
                                │       └→ Human reviews → ✅ Completed
                                │
                                └→ @blocked (agent-ready removed)
                                        └→ Unblocked → @agent-ready
```

### 3.6 Idea Lifecycle — Brainstorm Pipeline

```
[Captured] → Inbox → Triaging → Heartbeat Candidate → Approved for Build
                                      │                        │
                                      │                  In Implementation
                                      │                        │
                                      └→ Parked / Rejected    ✅ Completed
```

Comments are appended at each state transition as an audit trail.

---

## 4. TodoService — Class Contract

### 4.1 File Location

```
src/universal_agent/services/todoist_service.py
```

Create `services/` directory if it doesn't exist. Follow existing service patterns in the codebase.

### 4.2 Method Signatures

```python
class TodoService:
    """Pure Todoist API service. No LLM/tool coupling."""

    def __init__(self, api_token: str | None = None):
        """Initialize with token from param or TODOIST_API_TOKEN env var.
        Raises ValueError if no token available."""

    # ── Bootstrap ──────────────────────────────────────────────────────

    def ensure_taxonomy(self) -> dict:
        """Idempotently create projects, sections, labels.
        Returns: {
            "agent_project_id": "...",
            "brainstorm_project_id": "...",
            "agent_sections": {"immediate": "id", ...},
            "brainstorm_sections": {"inbox": "id", ...},
            "labels_created": ["agent-ready", ...],
        }
        """

    # ── Task Queries ───────────────────────────────────────────────────

    def get_actionable_tasks(self, filter_str: str | None = None) -> list[dict]:
        """Default filter: '(overdue | today | no date) & @agent-ready & !@blocked'
        Returns list of TaskDict sorted by priority desc, due_date asc."""

    def get_task_detail(self, task_id: str) -> dict | None:
        """Single task with full comment history. Returns TaskDetailDict."""

    def get_all_tasks(self, project_id: str | None = None, label: str | None = None) -> list[dict]:
        """Unfiltered task list with optional project/label constraint."""

    # ── Task Mutations ─────────────────────────────────────────────────

    def create_task(
        self,
        content: str,
        description: str = "",
        priority: str = "low",
        section: str = "background",
        labels: list[str] | None = None,
        due_string: str | None = None,
        sub_agent: str | None = None,
        parent_id: str | None = None,
    ) -> dict:
        """Create in Agent Tasks project. Auto-adds 'agent-ready' label.
        Returns TaskDict."""

    def complete_task(self, task_id: str, summary: str | None = None) -> bool:
        """Close task. Adds completion comment if summary provided."""

    def update_task(self, task_id: str, **kwargs) -> bool:
        """Update arbitrary task fields."""

    def mark_blocked(self, task_id: str, reason: str) -> bool:
        """Swap agent-ready → blocked, add reason comment."""

    def mark_needs_review(self, task_id: str, result_summary: str) -> bool:
        """Swap agent-ready → needs-review, add summary comment."""

    def unblock_task(self, task_id: str) -> bool:
        """Swap blocked → agent-ready."""

    def add_comment(self, task_id: str, content: str) -> bool:
        """Add Markdown comment to task."""

    def delete_task(self, task_id: str) -> bool:
        """Delete task."""

    # ── Brainstorming ──────────────────────────────────────────────────

    def record_idea(
        self,
        content: str,
        description: str = "",
        dedupe_key: str | None = None,
        source_session_id: str | None = None,
        source_trace_id: str | None = None,
        impact: str = "M",
        effort: str = "M",
    ) -> dict:
        """Capture idea in Brainstorm Pipeline Inbox.
        If dedupe_key matches existing task, add comment instead of creating.
        Returns TaskDict (created or existing)."""

    def promote_idea(self, task_id: str, target_section: str = "approved") -> bool:
        """Move idea between brainstorm pipeline sections.
        Valid targets: any section key from brainstorm_sections."""

    def park_idea(self, task_id: str, rationale: str) -> bool:
        """Move to Parked/Rejected with rationale comment."""

    def get_pipeline_summary(self) -> dict:
        """Returns count of ideas per brainstorm section.
        Used in heartbeat reporting."""

    # ── Heartbeat ──────────────────────────────────────────────────────

    def heartbeat_summary(self) -> dict:
        """Main heartbeat entry point. Returns HeartbeatSummary.
        Deterministic Python — NO LLM involvement."""

    # ── Reporting (optional, v2) ───────────────────────────────────────

    def get_productivity_stats(self) -> dict | None:
        """Sync API call to /completed/get_stats."""

    def get_completed_since(self, since_iso: str) -> list[dict]:
        """Sync API call to /completed/get_all."""
```

### 4.3 Structured Description Format for Ideas

When `record_idea()` creates a task, embed structured metadata in the description using a YAML-like frontmatter block:

```
---
dedupe_key: claude-sdk-recursive-tool-improvement
source_session: abc123
source_trace: trace_456
impact: H
effort: S
confidence: 1
---
Human-readable context and rationale goes here.
Agent observed that recursive tool calls could be optimized by...
```

The `confidence` field starts at 1 and increments each time the same `dedupe_key` is seen again (via comment addition). This allows tracking how often the system independently surfaces the same idea.

Parse this frontmatter when reading tasks to extract structured fields. A simple split on `---` delimiters is sufficient.

### 4.4 Implementation Notes

**Caching:** Cache project and section ID-to-name mappings after first fetch. Invalidate on taxonomy creation. Never cache task data.

**Error handling:** Catch all `todoist_api_python` exceptions, log with `logger.error()`, return `False` / `None` / empty list. Never let a Todoist API failure crash the heartbeat or block other agent operations.

**Label mutation pattern:** Always fetch the current task to get its existing labels before modifying. Don't assume label state — another process or the human may have changed labels since last check.

**Dedup logic for record_idea():** Search for tasks in the Brainstorm Pipeline project where the description contains `dedupe_key: {key}`. If found, add a comment with the new context and increment the confidence counter in the description. If not found, create a new task in the Inbox section.

---

## 5. JSON Output Schemas

All methods returning dicts must conform to these schemas. Consistency across CLI, tools, and heartbeat.

### 5.1 TaskDict

```json
{
    "id": "abc123",
    "content": "Research latest Claude SDK updates",
    "description": "Detailed context...",
    "priority": "P1-Urgent",
    "project": "Agent Tasks",
    "section": "Immediate",
    "labels": ["agent-ready", "sub-agent:research"],
    "due_date": "2026-02-16",
    "due_datetime": "2026-02-16T14:00:00Z",
    "is_recurring": false,
    "sub_agent": "research",
    "parent_id": null,
    "url": "https://app.todoist.com/showTask?id=abc123",
    "created_at": "2026-02-15T10:00:00Z",
    "comment_count": 3
}
```

### 5.2 TaskDetailDict (extends TaskDict)

```json
{
    "...all TaskDict fields...": "...",
    "comments": [
        {
            "id": "comment_id",
            "content": "Agent progress note...",
            "posted_at": "2026-02-15T12:30:00Z"
        }
    ]
}
```

### 5.3 HeartbeatSummary

```json
{
    "timestamp": "2026-02-16T12:00:00+00:00",
    "actionable_count": 5,
    "overdue_count": 2,
    "tasks": [
        {
            "id": "abc123",
            "content": "Research latest Claude SDK updates",
            "priority": "P1-Urgent",
            "sub_agent": "research",
            "due_date": "2026-02-15",
            "section": "Immediate"
        }
    ],
    "by_sub_agent": {
        "research": ["abc123", "def456"],
        "code": ["ghi789"],
        "unrouted": ["jkl012"]
    },
    "by_priority": {
        "P1-Urgent": ["abc123"],
        "P2-High": ["def456", "ghi789"],
        "P4-Low": ["jkl012"]
    },
    "brainstorm_pipeline": {
        "inbox": 3,
        "triaging": 1,
        "heartbeat_candidate": 5,
        "approved": 2,
        "in_implementation": 1,
        "parked": 7
    },
    "summary": "5 actionable tasks, 2 overdue, 1 P1-Urgent; 12 ideas in pipeline (2 approved)"
}
```

### 5.4 MutationResult

```json
{
    "success": true,
    "task_id": "abc123",
    "action": "completed",
    "message": "Task 'Research latest Claude SDK updates' marked complete"
}
```

---

## 6. CLI Wrapper Specification

### 6.1 File Location

```
src/universal_agent/cli/todoist_cli.py
```

Or inline with existing CLI patterns in the project.

### 6.2 Commands

```
todoist setup                              → Taxonomy creation result JSON
todoist heartbeat                          → HeartbeatSummary JSON

todoist tasks [--filter "..."]             → list of TaskDict JSON
todoist task <id>                          → TaskDetailDict JSON
todoist create "<content>" [options]       → MutationResult JSON
todoist complete <id> [--summary "..."]    → MutationResult JSON
todoist update <id> [options]              → MutationResult JSON
todoist block <id> --reason "..."          → MutationResult JSON
todoist review <id> --summary "..."        → MutationResult JSON
todoist unblock <id>                       → MutationResult JSON
todoist comment <id> "<text>"              → MutationResult JSON
todoist delete <id>                        → MutationResult JSON

todoist idea "<content>" [options]         → TaskDict JSON (dedup-aware)
todoist promote <id> [--to section]        → MutationResult JSON
todoist park <id> --rationale "..."        → MutationResult JSON
todoist pipeline                           → Pipeline section counts JSON
```

### 6.3 Implementation Notes

Use `argparse` (no extra dependency) or `click` if already in the project. All output is JSON to stdout. Errors are JSON to stderr: `{"error": "message", "success": false}`. Make the CLI executable and invocable via `python -m universal_agent.cli.todoist_cli` or a console_scripts entrypoint.

---

## 7. Environment & Dependencies

### 7.1 New Environment Variable

```
TODOIST_API_TOKEN=<token from Todoist Settings → Integrations → Developer>
```

Add to whatever secrets management is in use.

### 7.2 New Dependency

```
todoist-api-python>=3.2.0,<4
```

The SDK depends on `requests` and `attrs` — both likely already in the dependency tree.

### 7.3 First-Run Bootstrap

After token is configured:
```bash
python -m universal_agent.cli.todoist_cli setup
```

This calls `ensure_taxonomy()`, which idempotently creates both projects, all sections, and all labels.

---

# PART B — INTEGRATION BRIEF (Intent & Constraints)

The following sections describe what should happen at integration points with the existing system. They deliberately do NOT prescribe specific code changes, since the implementer has codebase access and can determine the best approach.

---

## 8. Heartbeat Integration — Intent & Constraints

### 8.1 Goal

Add Todoist as a new source of discoverable work in the heartbeat cycle. This supplements (does not replace) whatever the heartbeat currently checks.

### 8.2 Constraints

1. **The Todoist check must be deterministic Python.** Call `TodoService.heartbeat_summary()` directly. No tool definitions required, no LLM invocation, no context window cost.

2. **The LLM should only be invoked when there are tasks to act on.** If `heartbeat_summary()` returns `actionable_count == 0`, log it and move on. Do not send a prompt to the gateway/orchestrator about Todoist.

3. **When there ARE tasks, pass the summary as context to the orchestrator.** The heartbeat summary (JSON) should be included in whatever prompt the heartbeat constructs for the gateway. The orchestrator then decides how to dispatch tasks to sub-agents using its existing delegation mechanism.

4. **Do not register Todoist tools in the system prompt for heartbeat-triggered runs.** The orchestrator delegates to sub-agents; those sub-agents may have Todoist tools if needed, but the primary agent shouldn't carry the tool definition overhead.

5. **The Todoist check should not break the existing heartbeat flow.** If the Todoist API is down or the token is misconfigured, catch the error, log it, and let the rest of the heartbeat proceed normally.

### 8.3 Open Questions for Implementer

- Where in the heartbeat cycle should the Todoist check run? Before the existing `force_complex` path? As a parallel check? As a replacement for part of it?
- Should the heartbeat summary feed into the existing system events injection mechanism (around line 956–983 of `heartbeat_service.py`), or should it be a separate channel?
- Does the current task decomposition pipeline need to be aware of Todoist tasks, or does the orchestrator handle that naturally through its existing delegation logic?
- The on-demand wake hooks (around line 598–625) could potentially be triggered by Todoist changes (e.g., a new P1 task). Is this desirable for v1, or should we stick to poll-only via the 30-minute cycle?

---

## 9. Agent Tools Integration — Intent & Constraints

### 9.1 Goal

Provide a small tool surface for interactive Todoist access during conversational or agentic workflows (not the heartbeat path).

### 9.2 Recommended Tool Surface

Six tools mirroring the shape of the existing TaskWarrior MCP server pattern:

```
todoist_query     — Query tasks with filter string
todoist_create    — Create task with full options
todoist_complete  — Complete task with summary
todoist_update    — Update fields + lifecycle actions (block/review/unblock)
todoist_detail    — Get task with comment history
todoist_comment   — Add comment to task
```

Input schemas are specified in Section 6.2 of this document (the CLI commands map 1:1).

### 9.3 Implementation Choice

**Option A — Native SDK tools:** Register in the internal tool registry (`internal_registry.py`). Keeps everything in-process, no additional MCP server. Tool count impact: ~800–1000 tokens.

**Option B — MCP server:** Create `mcp_server_todoist.py` mirroring `mcp_server_taskwarrior.py`. Consistent with existing MCP patterns, gets Quad H SDK visibility for free.

**Option C — CLI-via-bash:** No tool registration at all. Agent uses existing bash tool to invoke `todoist_cli.py`. Zero context window overhead, but introduces string-formatting failure modes.

The implementer should choose based on:
- Whether MCP Tool Search / on-demand loading is operational (if yes, Option B is fine)
- Whether the Quad H SDK debugging visibility is valuable enough to justify MCP (if yes, Option B)
- Current total tool definition token count and budget constraints (if tight, Option C)

### 9.4 Brainstorming Tools

If implementing the brainstorming pipeline, add these to whichever tool surface is chosen:

```
todoist_idea      — Capture idea with dedup
todoist_promote   — Move idea between pipeline sections
todoist_park      — Park/reject idea with rationale
```

---

## 10. Brainstorming Pipeline — Design Direction

### 10.1 Context

Kevin frequently generates ideas during active work sessions that get tagged "out of scope" and are lost. The brainstorming pipeline is an idea funnel that sits UPSTREAM of the agent task queue. Ideas have a different lifecycle than tasks — they need to be captured fast, deduplicated, allowed to accumulate evidence over time, and only promoted to actionable work when validated.

### 10.2 Core Concepts

**Idea capture:** When the agent or Kevin identifies a potential improvement, feature idea, or exploration topic, it's recorded as a task in the Brainstorm Pipeline's Inbox section via `record_idea()`.

**Deduplication:** Each idea can carry a `dedupe_key`. If the same key is seen again (e.g., the heartbeat independently surfaces the same idea in a later cycle), a comment is added to the existing task rather than creating a duplicate. A `confidence` counter in the structured description tracks how many times the idea has been independently surfaced.

**Evidence accumulation:** Comments serve as the audit trail — why was this idea surfaced? What context triggered it? What are the tradeoffs? Over time, an idea's comment thread becomes its specification.

**Promotion:** When an idea is approved (manually by Kevin or by a policy rule), it moves to "Approved for Build" and eventually either moves to "In Implementation" or spawns a linked task in the Agent Tasks project.

**Parking:** Ideas that aren't worth pursuing get moved to "Parked / Rejected" with a rationale comment. They're preserved (not deleted) in case they become relevant later.

### 10.3 Heartbeat Interaction

The heartbeat has two relationships with the brainstorming pipeline:

1. **As a consumer:** The heartbeat summary should include pipeline counts (how many ideas in each section) as situational awareness for the orchestrator.

2. **As a producer:** When the heartbeat's existing analysis (e.g., examining logs, checking system health, reviewing artifacts) surfaces a concrete improvement idea, it should call `record_idea()` to capture it rather than just logging it.

### 10.4 Automation Policy (Suggested, Not Prescriptive)

```
IF heartbeat result is effectively idle/no-op:
    → No brainstorm action

IF heartbeat surfaces a concrete improvement idea:
    → Call record_idea() targeting Heartbeat Candidate section
    → If same dedupe_key exists, add comment + bump confidence

IF idea confidence reaches threshold (e.g., 3+ independent occurrences):
    → Consider auto-promoting to Approved for Build
    → Or flag for human review

IF manually approved by Kevin (in Todoist UI or via agent conversation):
    → promote_idea() to Approved for Build
    → Optionally create linked task in Agent Tasks
```

### 10.5 Open Questions

- **What's the right confidence threshold for auto-promotion?** Start with manual-only promotion in v1?
- **Should the heartbeat actively review Heartbeat Candidate ideas each cycle?** Or only capture new ones and leave triage to humans/explicit agent conversations?
- **How does idea promotion interact with the existing task decomposition pipeline?** Does a promoted idea become a single agent task, or does it trigger decomposition into subtasks?
- **Should there be a periodic "brainstorm review" heartbeat activity** that evaluates all Heartbeat Candidates and makes promotion/parking recommendations?
- **What's the structured description format preference?** The YAML frontmatter approach is proposed in Section 4.3, but if the project has an existing metadata convention, use that instead.

---

## 11. Implementation Order

```
Phase 1 — Foundation (no existing code changes):
  1. Add todoist-api-python dependency
  2. Set up TODOIST_API_TOKEN
  3. Implement TodoService (Layer 1) with all methods
  4. Implement CLI wrapper (Layer 4)
  5. Run setup via CLI, verify taxonomy created in Todoist
  6. Test full lifecycle via CLI: create → query → complete
  7. Test idea lifecycle via CLI: idea → promote → park

Phase 2 — Heartbeat integration (touches existing code):
  8. Add deterministic Todoist pre-step to heartbeat
  9. Verify heartbeat correctly skips LLM when no tasks
  10. Verify heartbeat passes task summary to orchestrator when tasks exist
  11. End-to-end test: add task in Todoist app → heartbeat picks up → agent processes

Phase 3 — Agent tools (touches existing code):
  12. Implement tool surface (MCP, native, or CLI — implementer's choice)
  13. Test interactive task management via agent conversation

Phase 4 — Cleanup:
  14. Remove TaskWarrior integration
  15. Document in project README / docs

Phase 5 — Brainstorming (can be deferred):
  16. Implement record_idea() with dedup logic
  17. Wire heartbeat to call record_idea() when it surfaces ideas
  18. Test idea capture and dedup via CLI
  19. Implement promotion/parking flows
  20. Add brainstorm pipeline counts to heartbeat summary
```

Phase 1 is fully independent and testable. Phase 2–3 are the integration points. Phase 5 can be deferred to a separate sprint if desired — the TodoService contract supports it but the implementation can wait.

---

## End of Document

This document should provide everything needed to implement the Todoist integration. The implementer has full authority over integration-point decisions (heartbeat wiring, tool surface choice, brainstorming timing). Part A is specification; Part B is guidance.
