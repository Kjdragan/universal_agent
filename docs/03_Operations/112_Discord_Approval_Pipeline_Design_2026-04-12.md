# Discord Approval Pipeline — Investigation & Design

**Last Updated:** 2026-04-12
**Status:** Design complete, ready for implementation
**Parent References:**
- Subsystem doc: `docs/02_Subsystems/Discord_Intelligence_System.md`
- Operational guide: `docs/03_Operations/111_Discord_Operations_And_Usage_Guide_2026-04-09.md`
- Master plan: `discord_intelligence/Discord_UA_Master_Plan.md`
- CC handoff: `discord_intelligence/HANDOFF_03_Discord_Command_Control.md`

---

## 1. Problem Statement

The Universal Agent system is architecturally mature for **reactive reliability** (tasks execute correctly when triggered) but weak on the **human review loop** — getting autonomous output in front of Kevin efficiently so he can approve, reject, or redirect. Agents can produce work overnight, but there is no streamlined on-the-go interface for Kevin to curate that output.

Kevin's core philosophy:

> "My ability to review a pipeline of projects that agents have created on their own and just swiping left or right metaphorically is not a waste of time for the agents and it potentially adds significant value for me."

The approval pipeline bridges the gap between "agents produce autonomous work" and "Kevin efficiently curates it."

---

## 2. Investigation Findings: What Already Exists

A comprehensive codebase investigation (April 2026) revealed the Discord infrastructure is substantially more mature than initially assumed. The CC bot and intelligence daemon are deployed and running in production.

### 2.1 Deployed Components (No Work Needed)

| Component | File | Service |
|-----------|------|--------|
| Intelligence Daemon (45+ servers, Layer 1-3) | `discord_intelligence/daemon.py` | `ua-discord-intelligence.service` |
| CC Bot (15+ slash commands, feed channels) | `discord_intelligence/cc_bot.py` | `ua-discord-cc-bot.service` |
| SQLite DB (messages, signals, insights, events) | `discord_intelligence/database.py` | — |
| MCP Bridge (agent queries) | `discord_intelligence/mcp_bridge.py` | stdio via `.mcp.json` |
| Task Hub integration (create/list/get) | `discord_intelligence/integration/task_hub.py` | — |
| Event digest + Google Calendar sync | `discord_intelligence/event_digest.py`, `calendar_sync.py` | — |
| All feed channels (signals, announcements, events, KB, briefings) | cc_bot.py polling loops | — |
| Reaction-based event workflows | cc_bot.py `on_raw_reaction_add` | — |
| Simone chat hotline | cc_bot.py `on_message` for #simone-chat | — |

### 2.2 CC Server Channel Structure (Already Established)

```
UA Command Center (Discord Server)
├── 📋 OPERATIONS
│   ├── #simone-chat          ← Direct conversation with Simone
│   ├── #mission-status       ← Auto-updated mission progress
│   ├── #alerts               ← System health
│   └── #task-queue           ← Current Task Hub state
├── 🔬 INTELLIGENCE
│   ├── #research-feed        ← Layer 3 insights
│   ├── #announcements-feed   ← Layer 2 signals
│   ├── #event-calendar       ← Discord events + calendar sync
│   └── #knowledge-updates    ← Wiki updates
├── 📦 ARTIFACTS
│   ├── #briefings            ← Daily/weekly briefings
│   ├── #reports              ← Research reports
│   └── #code-artifacts       ← Generated code
└── ⚙️ SYSTEM
    ├── #bot-logs             ← Operation logs
    └── #config               ← Configuration commands
```

### 2.3 The Gap: What's Missing

The single critical missing piece is the **interactive approval pipeline** — no mechanism exists to:
1. Poll Task Hub for tasks awaiting human review (`needs_review` / `pending_review`)
2. Generate digest card embeds with one-tap decision buttons
3. Wire button interactions back to Task Hub state transitions via the gateway API
4. Capture feedback on rejection (Discord modal)
5. Update embeds to reflect decisions taken

---

## 3. Task Hub State Machine (Relevant States)

From `src/universal_agent/task_hub.py`:

```
TASK_STATUS_OPEN = "open"
TASK_STATUS_IN_PROGRESS = "in_progress"
TASK_STATUS_BLOCKED = "blocked"
TASK_STATUS_REVIEW = "needs_review"       ← Human input/approval needed
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_PARKED = "parked"
TASK_STATUS_DELEGATED = "delegated"       ← VP actively working
TASK_STATUS_PENDING_REVIEW = "pending_review"  ← VP done, Simone sign-off needed
TASK_STATUS_SCHEDULED = "scheduled"
```

**Key distinction:**
- `needs_review` — Used when Simone or the system flags a task for human review. This is the primary state for the approval pipeline.
- `pending_review` — VP finished execution, waiting for Simone's validation before marking complete.

**Both states are valid targets for the approval pipeline.** No new states are required.

### Valid Actions for Review

From `perform_task_action()` (task_hub.py:2835):
- `approve` → transitions `pending_review` → `completed` with sign-off metadata (`approved_at`, `approved_by`, `approval_note`)
- `reject` → marks `seizure_state="rejected"`, stores `metadata.last_reject_reason`
- `park` → transitions to `parked` with manual park metadata
- `snooze` → stores `metadata.snoozed_note` without state change
- `complete` → transitions to `completed`

### Gateway REST API Endpoints

From `src/universal_agent/gateway_server.py`:

| Endpoint | Purpose | Function Called |
|----------|---------|----------------|
| `POST /api/v1/dashboard/todolist/tasks/{id}/approve` | Approve and dispatch | `dispatch_on_approval()` |
| `POST /api/v1/dashboard/todolist/tasks/{id}/action` | Any lifecycle action | `perform_task_action()` |
| `POST /api/v1/dashboard/todolist/tasks/{id}/dispatch` | Immediate dispatch | `dispatch_immediate()` |
| `GET /api/v1/dashboard/approvals/highlight` | List pending approvals | `_list_ops_approvals()` |

**Authentication:** Internal services use `UA_INTERNAL_API_TOKEN` via `x-ua-internal-token` header or `Authorization: Bearer <token>`. This is the same pattern used by the Telegram bot adapter (`src/universal_agent/api/gateway_bridge.py:70-80`).

---

## 4. Proposed Architecture

### 4.1 Data Flow

```
Task reaches needs_review or pending_review in Task Hub
    │
    ▼
CC Bot poll_review_queue loop (new task loop, every ~90s)
    │   Calls: GET /api/v1/dashboard/approvals/highlight
    │   Tracks already-posted task_ids to avoid duplicates
    │
    ▼
Generate digest embed with interactive View buttons
    │   Title: task title
    │   Description: 2-3 line summary (task description, truncated)
    │   Fields: priority, source, age, assigned VP
    │   Buttons: [✅ Approve] [❌ Reject] [📝 Revise] [⏸️ Later]
    │
    ▼
Post to #review-queue channel (new channel under 📋 OPERATIONS)
    │
    ▼
Kevin taps a button
    │
    ├── ✅ Approve
    │   POST /api/v1/dashboard/todolist/tasks/{id}/approve
    │   → dispatch_on_approval() → task transitions to open+human_approved → claimed
    │   → Embed updates: green color, "✅ Approved by Kevin"
    │
    ├── ❌ Reject
    │   Discord Modal popup: "Why reject? (even one word helps)"
    │   → POST /api/v1/dashboard/todolist/tasks/{id}/action
    │     body: {action: "park", reason: feedback_text, agent_id: "discord_kevin"}
    │   → Task parked with metadata.last_reject_reason = feedback_text
    │   → Embed updates: red color, "❌ Rejected: {reason}"
    │
    ├── 📝 Revise
    │   Discord Modal popup: "What should change?"
    │   → Create new Task Hub item with revision context + link to original
    │   → Original task parked with note
    │   → Embed updates: yellow color, "📝 Revision requested"
    │
    └── ⏸️ Later
        → POST /api/v1/dashboard/todolist/tasks/{id}/action
          body: {action: "snooze", reason: "deferred_via_discord"}
        → Embed updates: grey color, "⏸️ Deferred"
        → Task reappears in next poll cycle (snooze doesn't change status)
```

### 4.2 Bot Restart Persistence

Discord.py supports persistent views via `custom_id` patterns:

1. Button `custom_id` format: `review:{action}:{task_id}` (e.g., `review:approve:abc123`)
2. In `CCBot.setup_hook()`, register the `ReviewActionView` class with `self.add_view(ReviewActionView())`
3. Discord automatically routes incoming interactions to the registered view class based on `custom_id` prefix matching
4. The `#review-queue` channel message history serves as persistent state — no extra DB table needed

### 4.3 Existing Pattern: Reaction-Based Event Workflow

The CC bot already has a working pattern for interactive decision-making in `on_raw_reaction_add()` (cc_bot.py:114-201). The event calendar uses ✅/🎙️/📋/❌ reactions to trigger calendar sync, audio recording, or decline. The approval pipeline uses the same concept but with discord.ui.View buttons instead of reactions — richer interaction (modals for feedback capture, embed updates for visual state).

---

## 5. Files to Create/Modify

### 5.1 New: `discord_intelligence/integration/gateway_client.py` (~80 lines)

Async HTTP client for the UA gateway REST API, replacing direct SQLite access for actions that trigger dispatches (where concurrency safety matters).

```python
class GatewayClient:
    """Async HTTP client for the UA Gateway REST API."""

    def __init__(self, base_url: str, token: str):
        # httpx.AsyncClient with x-ua-internal-token auth

    async def get_review_tasks(self) -> list[dict]:
        # GET /api/v1/dashboard/approvals/highlight

    async def approve_task(self, task_id: str, agent_id: str = "discord_kevin") -> dict:
        # POST /api/v1/dashboard/todolist/tasks/{task_id}/approve

    async def task_action(self, task_id: str, action: str, reason: str = "",
                          note: str = "", agent_id: str = "discord_kevin") -> dict:
        # POST /api/v1/dashboard/todolist/tasks/{task_id}/action

    async def get_task(self, task_id: str) -> dict | None:
        # GET /api/v1/dashboard/todolist/tasks/{task_id}
```

**Environment variables:**
- `UA_GATEWAY_URL` — Gateway base URL (already used by Telegram bot)
- `UA_INTERNAL_API_TOKEN` — Service-to-service auth token (already configured)

### 5.2 New: `discord_intelligence/views/review_queue.py` (~150 lines)

Discord UI components for the approval pipeline.

```python
class ReviewActionView(discord.ui.View):
    """Persistent interactive buttons for task review decisions.
    timeout=None so buttons survive indefinitely.
    custom_id pattern: review:{action}:{task_id}
    """

    @discord.ui.button(label="Approve", style=ButtonStyle.success, custom_id="review:approve")
    async def approve_button(self, interaction, button): ...

    @discord.ui.button(label="Reject", style=ButtonStyle.danger, custom_id="review:reject")
    async def reject_button(self, interaction, button): ...

    @discord.ui.button(label="Revise", style=ButtonStyle.primary, custom_id="review:revise")
    async def revise_button(self, interaction, button): ...

    @discord.ui.button(label="Later", style=ButtonStyle.secondary, custom_id="review:later")
    async def later_button(self, interaction, button): ...


class RejectFeedbackModal(discord.ui.Modal):
    """Captures rejection reason. Even one word is valuable for preference learning."""

class RevisionNotesModal(discord.ui.Modal):
    """Captures what should change for a revision request."""
```

### 5.3 Modify: `discord_intelligence/cc_bot.py` (~60 lines added)

Changes to the `CCBot` class:

1. **New `poll_review_queue` task loop** (~30 lines):
   ```python
   @tasks.loop(seconds=90)
   async def poll_review_queue(self):
       # Call gateway_client.get_review_tasks()
       # For each unposted task, generate embed + ReviewActionView
       # Post to #review-queue channel
       # Track posted task_ids in self._posted_review_ids set
   ```

2. **`setup_hook()` addition** — Register persistent view:
   ```python
   self.add_view(ReviewActionView())
   ```

3. **`on_ready()` addition** — Start the new loop:
   ```python
   if not self.poll_review_queue.is_running():
       self.poll_review_queue.start()
   ```

4. **New `_get_ops_channel()` helper** — Find channels under 📋 OPERATIONS category.

5. **New slash commands in `setup_commands()`**:
   - `/queue` — Show pending review items count and titles
   - Improve `/status` — Call gateway for real heartbeat/queue/mission data (replace stub)

### 5.4 Modify: `discord_intelligence/integration/task_hub.py` (~30 lines added)

Add gateway-backed functions for actions that need concurrency safety:

```python
async def approve_task_via_gateway(task_id: str, agent_id: str = "discord_kevin") -> dict:
    """Approve a task via the gateway REST API (concurrency-safe)."""

async def reject_task_via_gateway(task_id: str, reason: str, agent_id: str = "discord_kevin") -> dict:
    """Park a rejected task with feedback via the gateway REST API."""
```

Existing direct-DB functions (`create_task_hub_mission`, `get_task_hub_items`, `get_mission_status`) remain unchanged — they work fine for task creation and reads.

---

## 6. Configuration & Environment

### New Environment Variables

| Variable | Purpose | Default |
|----------|---------|--------|
| `UA_GATEWAY_URL` | Gateway base URL for REST API calls | (required, already set for Telegram) |
| `UA_INTERNAL_API_TOKEN` | Service-to-service auth | (required, already set) |
| `UA_DISCORD_REVIEW_POLL_SECONDS` | Review queue poll interval | `90` |
| `UA_DISCORD_REVIEW_CHANNEL` | Channel name for review queue | `review-queue` |

### Systemd Unit Update

The `ua-discord-cc-bot.service` unit already loads env vars via Infisical. The two gateway variables (`UA_GATEWAY_URL`, `UA_INTERNAL_API_TOKEN`) need to be available in the CC bot's environment — verify they are already present from the Telegram bot configuration, or add them to the Infisical project.

### New Discord Channel

Create `#review-queue` under the 📋 OPERATIONS category in the CC server:
- Can be done manually or via `/setup_webhooks`-style command
- Set notification preference to "All Messages" for this channel (Kevin wants to see every review item)

---

## 7. Prioritized Improvement Roadmap

### P0 — The Approval Pipeline (This Document)

The single highest-impact feature. Estimated ~320 lines across 4 files. Unlocks the proactive value loop.

### P1 — Slash Command Improvements

- **`/status` real data**: Replace stub at cc_bot.py:442-443 with gateway API call. ~15 lines.
- **`/queue` command**: New slash command showing pending review count + titles. ~20 lines.

### P2 — Briefing Path Alignment

The CC bot's `poll_briefings` loop (cc_bot.py:369) reads from `UA_DISCORD_BRIEFINGS_DIR` (default: `kb/briefings/`). The UA morning briefing writes to `Path(UA_ARTIFACTS_DIR) / "autonomous-briefings" / {date}/`. Fix: add the UA artifacts path as a second source, or update the env var. ~5 lines.

### P3 — Preference Learning Closed Loop

Build on the feedback data captured by the approval pipeline's reject flow:
1. Scheduled job reads `metadata.last_reject_reason` from parked tasks
2. LLM extracts preference signals (e.g., "Kevin rejects research briefs that lack benchmarks")
3. Writes to memory via `orchestrator.write(source="discord_preference", tags=["preference"])`
4. Wiki internal sync extracts to `preferences/preferences-ledger.md` (extraction keywords already in `wiki/core.py:866`)
5. Simone queries preferences when delegating similar future tasks

P3 depends on P0 being live first to generate feedback data.

### Deprioritized / Not Needed

| Idea | Why Skip |
|------|----------|
| New task states | `needs_review` and `pending_review` already cover the semantics |
| CSI-Discord bridge | Separate by architectural design decision |
| Discord webhook ingress | CC bot calls gateway REST API directly |
| Telegram deprecation | "Survival of the fittest" per master plan |
| Overnight research pipeline | Independent of Discord — just a Task Hub routing rule |

---

## 8. Verification Plan

### Test the Full Approval Loop

1. **Create a test task** in `needs_review` status via direct DB or `/task_add` + manual status change
2. **Wait** for `poll_review_queue` (~90s) — verify embed appears in `#review-queue` with 4 buttons
3. **Tap Approve** — verify task transitions to `open` → `in_progress` in Task Hub; embed turns green
4. **Create another task, tap Reject** — verify modal appears, submit reason, task parks with `metadata.last_reject_reason`; embed turns red
5. **Restart CC bot** (`sudo systemctl restart ua-discord-cc-bot`) — verify buttons on existing messages still work
6. **Test `/queue`** — verify it shows pending review items
7. **Test `/status`** — verify real data, not "All clear"
8. **Check dashboard** — verify state changes from Discord are reflected in the web UI

### Regression Checks

- Existing feed channels (signals, announcements, events, KB, briefings) continue polling normally
- `#simone-chat` message routing still works
- Event calendar reaction workflow still works
- Existing slash commands unchanged

---

## 9. Relationship to Other Documents

| Document | Relationship |
|----------|-------------|
| `Discord_UA_Master_Plan.md` | This design implements the "Phase 2: Command & Control" approval pipeline envisioned there |
| `HANDOFF_03_Discord_Command_Control.md` | This design fills the gap identified in that handoff — interactive approval was planned but not implemented |
| `docs/02_Subsystems/Discord_Intelligence_System.md` | Update the roadmap section to reflect this new phase when implemented |
| `docs/03_Operations/111_Discord_Operations_And_Usage_Guide_2026-04-09.md` | Add usage guide for `/queue`, improved `/status`, and #review-queue workflow when implemented |
| `docs/03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md` | The approval pipeline uses the existing `dispatch_on_approval()` and `perform_task_action()` paths documented there |
