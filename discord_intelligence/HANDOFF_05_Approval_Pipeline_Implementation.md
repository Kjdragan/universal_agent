# HANDOFF: Discord Approval Pipeline — Implementation & Testing Guide

**Created:** 2026-04-12
**Purpose:** Actionable handoff so a local AI agent can verify, test, and deploy the Discord approval pipeline feature.
**Design Reference:** `docs/03_Operations/112_Discord_Approval_Pipeline_Design_2026-04-12.md`

---

## 1. What Was Built

An interactive approval pipeline for the Discord CC Bot that lets Kevin review autonomous agent output directly from Discord with one-tap buttons.

### New Files Created

#### `discord_intelligence/integration/gateway_client.py`
Async HTTP client for the UA gateway REST API. Replaces direct SQLite access for task actions that need concurrency safety (approve, reject, etc.).

**Key functions:**
- `get_review_tasks()` — Fetches tasks in `needs_review` / `pending_review` from `GET /api/v1/dashboard/human-actions/highlight`
- `approve_task(task_id)` — Approves via `POST /api/v1/dashboard/todolist/tasks/{task_id}/approve`
- `task_action(task_id, action, reason, note, agent_id)` — Any lifecycle action via `POST /api/v1/dashboard/todolist/tasks/{task_id}/action`
- `get_dispatch_queue(limit)` — Fetches dispatch queue summary
- `get_approvals_highlight()` — Fetches pending approvals count and list

**Auth:** Uses `UA_INTERNAL_API_TOKEN` (or `UA_OPS_TOKEN` fallback) via `x-ua-internal-token` header. Same pattern as Telegram bot (`src/universal_agent/api/gateway_bridge.py:70-80`).

**Gateway URL:** From `UA_GATEWAY_URL` env var, defaults to `http://127.0.0.1:8080`.

#### `discord_intelligence/views/__init__.py`
Empty init file for the new views package.

#### `discord_intelligence/views/review_queue.py`
Discord UI components for the approval pipeline.

**Classes:**
- `ReviewActionView(discord.ui.View)` — Persistent button row with 4 buttons:
  - **Approve** (green) — Calls `gateway_client.approve_task()`, updates embed to green
  - **Reject** (red) — Opens `RejectFeedbackModal`, then parks task with reason
  - **Revise** (blue) — Opens `ReviseNotesModal`, parks original, creates new task with revision notes
  - **Later** (grey) — Calls snooze action, updates embed to grey
- `RejectFeedbackModal(discord.ui.Modal)` — Popup with text field: "Why are you rejecting this?"
- `ReviseNotesModal(discord.ui.Modal)` — Popup with text field: "What should be revised?"
- `build_review_embed(task)` — Generates a digest card embed from a task dict

**Persistence:** Buttons use `custom_id="review:{action}:{task_id}"` format. On bot restart, `setup_hook()` re-registers `ReviewActionView()` with empty task_id, and discord.py matches incoming interactions by custom_id prefix.

**Post-action behavior:** After any button press, the embed is edited to reflect the decision (color change + footer text) and all buttons are removed.

### Modified Files

#### `discord_intelligence/cc_bot.py`
Changes made:

1. **New imports** (lines 13-14):
   ```python
   from .integration import gateway_client
   from .views.review_queue import ReviewActionView, build_review_embed
   ```

2. **`setup_hook()` updated** (lines 42-48):
   - Registers persistent view: `self.add_view(ReviewActionView())`
   - Initializes dedup set: `self._posted_review_task_ids: set[str] = set()`

3. **`on_ready()` updated** (lines 80-82):
   - Starts the new poll loop: `self.poll_review_queue.start()`

4. **New `_get_ops_channel()` helper** (lines 91-97):
   - Finds channels under the `📋 OPERATIONS` category
   - Falls back to searching all channels by name

5. **New `poll_review_queue` task loop** (lines 108-140):
   - Runs every 90 seconds
   - Calls `gateway_client.get_review_tasks()`
   - Filters out already-posted task_ids
   - Posts digest embed + `ReviewActionView` to `#review-queue` channel
   - Tracks posted IDs in `self._posted_review_task_ids`

6. **`/status` command replaced** (lines 491-529):
   - Was a stub: `"System Status: All clear. Heartbeats ok."`
   - Now calls `gateway_client.get_approvals_highlight()` and `gateway_client.get_dispatch_queue()`
   - Returns rich embed with pending review count, dispatch queue depth, color-coded health

7. **New `/queue` command** (lines 531-556):
   - Calls `gateway_client.get_review_tasks()`
   - Shows pending review items as a formatted list embed

#### `discord_intelligence/integration/task_hub.py`
Changes made:

1. **New import** (line 15): `from . import gateway_client`
2. **Updated module docstring** to mention gateway-backed functions
3. **Three new async functions** (lines 107-129):
   - `approve_task_via_gateway(task_id, agent_id)` — Approve through gateway
   - `reject_task_via_gateway(task_id, reason, agent_id)` — Park rejected task through gateway
   - `get_review_tasks_via_gateway()` — Fetch review tasks through gateway
4. Existing direct-DB functions (`create_task_hub_mission`, `get_task_hub_items`, `get_mission_status`) are unchanged

---

## 2. Prerequisites for Testing

### Environment Variables

The CC bot's systemd unit (`ua-discord-cc-bot.service`) needs these env vars. They should already exist from Telegram bot configuration — verify via Infisical:

| Variable | Purpose | How to Verify |
|----------|---------|---------------|
| `DISCORD_BOT_TOKEN` | CC bot token | Already required — bot is running |
| `UA_GATEWAY_URL` | Gateway REST API base URL | Check: `infisical secrets get UA_GATEWAY_URL --env=production` |
| `UA_INTERNAL_API_TOKEN` | Service-to-service auth | Check: `infisical secrets get UA_INTERNAL_API_TOKEN --env=production` |

If `UA_GATEWAY_URL` or `UA_INTERNAL_API_TOKEN` are not in the CC bot's environment, add them:
```bash
uv run scripts/infisical_upsert_secret.py --environment production --secret "UA_GATEWAY_URL=http://127.0.0.1:8080"
```

### Discord Channel Setup

Create `#review-queue` under the `📋 OPERATIONS` category in the CC server:
1. Open Discord → UA Command Center server
2. Under `📋 OPERATIONS` category, create text channel `review-queue`
3. Set notification preference to "All Messages" (so Kevin gets notified for every review item)

Alternatively, the bot will fall back to searching for any channel named `review-queue` in the guild.

### Gateway Must Be Running

The approval pipeline requires the UA gateway to be online. Verify:
```bash
curl -s http://127.0.0.1:8080/api/v1/dashboard/approvals/highlight \
  -H "x-ua-internal-token: $(infisical secrets get UA_INTERNAL_API_TOKEN --env=production --plain)" \
  | python3 -m json.tool
```

Expected: JSON response with `pending_count` and `approvals` fields.

---

## 3. Testing Plan

### Step 1: Restart the CC Bot

```bash
sudo systemctl restart ua-discord-cc-bot
sudo systemctl status ua-discord-cc-bot
journalctl -u ua-discord-cc-bot -f --no-pager
```

Verify in logs:
- `Syncing slash commands...` followed by `Synced N command(s)`
- `Started poll_review_queue loop`
- No import errors or crashes

### Step 2: Test `/status` Command

In Discord, type `/status`. Verify:
- Returns a rich embed (not the old "All clear" stub)
- Shows "Pending Reviews" count
- Shows "Dispatch Queue" count
- Color: green (0 pending), orange (>3), red (>10)

### Step 3: Test `/queue` Command

In Discord, type `/queue`. Verify:
- If no review tasks exist: "No tasks pending review."
- If tasks exist: formatted list with task IDs, status, titles

### Step 4: Create a Test Review Task

SSH into the VPS and create a task in `needs_review` status:

```python
# Run this in the UA project directory
uv run python3 -c "
import uuid
from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

conn = connect_runtime_db(get_activity_db_path())
task_hub.ensure_schema(conn)

task_id = str(uuid.uuid4())
task_hub.upsert_item(conn, {
    'task_id': task_id,
    'title': '[TEST] Overnight Research Brief: AI Agent Frameworks 2026',
    'description': 'ATLAS completed a research brief on emerging AI agent frameworks. Covers LangGraph, CrewAI, AutoGen. Ready for review.',
    'status': 'needs_review',
    'priority': 3,
    'source_kind': 'discord_test',
    'agent_ready': False,
    'labels': ['test', 'review-pipeline'],
    'metadata': {'source': 'discord_test', 'tags': ['test']},
})
conn.commit()
conn.close()
print(f'Created test task: {task_id}')
"
```

### Step 5: Verify Digest Card Appears

Wait up to 90 seconds. Check the `#review-queue` channel. You should see:
- An embed with title: `📋 [TEST] Overnight Research Brief: AI Agent Frameworks 2026`
- Description: the task description text
- Fields: Status (needs_review), Priority (3), Source (discord_test)
- Footer: Task ID
- Four buttons: ✅ Approve | ❌ Reject | 📝 Revise | ⏸️ Later

### Step 6: Test Approve Button

Tap **✅ Approve**. Verify:
- Ephemeral message: "Task `{id}` approved and dispatched."
- Embed updates: color changes to green, footer shows "Approved by {your_name}"
- All buttons disappear from the embed
- On dashboard (`app.clearspringcg.com/dashboard/todolist`): task moved out of `needs_review`

### Step 7: Test Reject Button (Create Another Test Task First)

Create another test task (repeat Step 4 with different title). Wait for it to appear. Tap **❌ Reject**.

Verify:
- A modal popup appears: "Rejection Feedback" with text field "Why are you rejecting this?"
- Enter a reason (e.g., "too shallow, needs benchmarks")
- Submit
- Ephemeral message: "Rejected and parked. Reason: too shallow, needs benchmarks"
- Embed updates: color changes to red, footer shows "Rejected: too shallow, needs benchmarks"
- On dashboard: task is now `parked`, metadata contains `last_reject_reason`

### Step 8: Test Revise Button

Create another test task. Tap **📝 Revise**.

Verify:
- Modal popup: "Revision Notes" with text field "What should be revised?"
- Enter notes (e.g., "Add benchmark comparisons and pricing analysis")
- Submit
- Ephemeral message: "Original task parked. New revision task created: `{new_id}`"
- Embed updates: gold color, "Revision requested"
- On Task Hub: original task parked, NEW task created with title `[Revision] ...` and description containing the revision instructions

### Step 9: Test Later Button

Create another test task. Tap **⏸️ Later**.

Verify:
- Ephemeral message: "Task `{id}` snoozed — will reappear later."
- Embed updates: grey color, "Snoozed"
- Task remains in `needs_review` status (snooze only adds metadata, doesn't change state)
- Task will NOT reappear in `#review-queue` because the bot's dedup set remembers it (until bot restart)

### Step 10: Test Bot Restart Persistence

```bash
sudo systemctl restart ua-discord-cc-bot
```

Create a new test task. After the bot restarts and the new task's embed appears, go back to an OLD embed (from before the restart) that still has active buttons. Tap a button.

**Expected behavior:** The button should still work because `ReviewActionView` was re-registered in `setup_hook()` and the `custom_id` pattern matching reconnects interactions.

**Note:** The dedup set (`_posted_review_task_ids`) resets on restart. Previously-posted tasks that are still in `needs_review` will be re-posted. This is acceptable — better to duplicate than to miss.

### Step 11: Regression Checks

Verify existing features still work:
- [ ] `#signals-feed` still receives Layer 2 signals
- [ ] `#announcements-feed` still receives Layer 3 insights
- [ ] `#event-calendar` still shows events with reaction controls (✅/🎙️/📋/❌)
- [ ] `#briefings` still receives new briefing files
- [ ] `#knowledge-updates` still receives KB updates
- [ ] `#simone-chat` message routing still works (send a message, check Task Hub)
- [ ] `/task_add`, `/task_list`, `/research`, `/briefing` slash commands still work

---

## 4. Cleanup After Testing

Remove test tasks from Task Hub:
```python
uv run python3 -c "
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
conn = connect_runtime_db(get_activity_db_path())
conn.execute(\"DELETE FROM task_hub_items WHERE source_kind = 'discord_test'\")
conn.commit()
print('Cleaned up test tasks')
conn.close()
"
```

Delete test messages from `#review-queue` channel manually or leave them (they're harmless with buttons disabled).

---

## 5. Known Limitations & Edge Cases

| Issue | Impact | Mitigation |
|-------|--------|------------|
| Dedup set resets on bot restart | Previously-posted review tasks re-posted | Acceptable — duplicates are harmless, buttons still work |
| Gateway down → poll_review_queue silently fails | No new review cards posted | Logged as error; existing buttons still work (they call gateway on tap) |
| Button tap when gateway is down | Error message shown to user | Ephemeral error: "Approve failed: {error}" — task state unchanged |
| `#review-queue` channel doesn't exist | Tasks not posted | Logged as debug; bot continues running, other features unaffected |
| Revise modal creates task via direct DB | Not gateway-routed | Acceptable — task creation doesn't need concurrency safety |
| Stale snooze: snoozed tasks reappear after bot restart | Kevin sees them again | By design — snooze is temporary deferral, not permanent |

---

## 6. Production Deployment Checklist

- [ ] Verify `UA_GATEWAY_URL` is in CC bot's Infisical environment
- [ ] Verify `UA_INTERNAL_API_TOKEN` is in CC bot's Infisical environment
- [ ] Create `#review-queue` channel under `📋 OPERATIONS` in Discord
- [ ] Set `#review-queue` notification preference to "All Messages"
- [ ] Deploy latest code to VPS (push to `main` triggers GitHub Actions deploy)
- [ ] Restart CC bot: `sudo systemctl restart ua-discord-cc-bot`
- [ ] Run through Steps 1-11 of the testing plan
- [ ] Monitor logs for 24 hours: `journalctl -u ua-discord-cc-bot --since '24 hours ago'`

---

## 7. File Inventory

| File | Action | Lines |
|------|--------|-------|
| `discord_intelligence/integration/gateway_client.py` | Created | 123 |
| `discord_intelligence/views/__init__.py` | Created | 0 |
| `discord_intelligence/views/review_queue.py` | Created | 316 |
| `discord_intelligence/cc_bot.py` | Modified | ~100 lines added |
| `discord_intelligence/integration/task_hub.py` | Modified | ~25 lines added |
| **Total new/changed** | | **~565 lines** |
