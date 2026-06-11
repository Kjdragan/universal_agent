---
title: Simone-First Orchestration
status: active
canonical: true
subsystem: agents-simone
code_paths:
  - src/universal_agent/services/agent_router.py
  - src/universal_agent/services/dispatch_service.py
  - src/universal_agent/services/todo_dispatch_service.py
  - src/universal_agent/services/llm_classifier.py
  - src/universal_agent/gateway.py
last_verified: 2026-06-11
---

# Simone-First Orchestration

## What this is

Simone-first orchestration is the routing model where **every claimed Task Hub work
item is routed to Simone**, the primary Claude Code principal. Simone is the executor,
the triage decision-maker, and the lifecycle owner. She decides — per task — whether to
**execute the work herself** or **delegate it to a VP** (Atlas / Cody) via the
`vp_dispatch_mission` tool. The system never decides delegation; it only attaches an
advisory routing hint that Simone may honor or override.

This model replaced an older keyword-based `qualify_agent()` deterministic router.
**`qualify_agent*` is fully decommissioned** — `grep qualify_agent src/` returns nothing.

## The router itself is trivial

The "router" (`agent_router.py`) is intentionally tiny. Its job is not to make routing
decisions — it is to *stamp every task as Simone's* and carry a few agent-id constants:

```python
AGENT_SIMONE = "simone"
AGENT_CODER = "vp.coder.primary"
AGENT_GENERAL = "vp.general.primary"
```

`agent_router.py::route_all_to_simone` walks the claimed tasks and writes a `_routing`
dict onto each one, then returns `{ "simone": [...all tasks...] }`:

```python
task["_routing"] = {
    "agent_id": AGENT_SIMONE,            # always "simone"
    "confidence": "orchestrator",
    "reason": "Simone-first: all tasks route through primary orchestrator",
    "should_delegate": False,            # Simone decides delegation herself
}
```

There is no scoring, no keyword matching, no capability lookup here. The intelligence
lives in Simone's reasoning at execution time, not in the router.

## Two routing-enrichment paths

There are **two distinct code paths** that attach `_routing` to claimed tasks. Do not
conflate them — they run in different lanes and produce different metadata.

### 1. Generic sweep enrichment (`dispatch_service.py`)

The heartbeat dispatch sweep (`dispatch_service.py::dispatch_sweep`) and the dashboard
"Start Now" / "Approve" dispatch entry points all funnel claimed tasks through
`dispatch_service.py::_enrich_with_routing`, which simply calls `route_all_to_simone`.
This is the **unconditional, always-Simone** stamp. It is best-effort and never blocks
the sweep:

```python
def _enrich_with_routing(claimed):
    if not claimed:
        return claimed
    try:
        from universal_agent.services.agent_router import route_all_to_simone
        route_all_to_simone(claimed)
    except Exception as exc:
        log.debug("Agent routing enrichment unavailable: %s", exc)
    return claimed
```

`dispatch_sweep` defaults to `limit=1` and rebuilds the queue + claims the top N tasks
regardless of trigger type. Callers that should never claim VP-mirror rows pass
`forbidden_source_kinds=["vp_mission"]`.

### 2. LLM routing-judgment enrichment (`todo_dispatch_service.py`)

The Simone todo-dispatch lane (`todo_dispatch_service.py`) does something richer. Before
building Simone's execution prompt, it calls
`todo_dispatch_service.py::_enrich_with_llm_agent_routing`, which asks an LLM
(`llm_classifier.py::classify_agent_route`) to *recommend* an agent for each task. The
result is written into the same `_routing` key but with `method="llm"` and a real
`should_delegate` boolean.

Crucially, the LLM is only offered agents that currently have capacity. Concurrency caps
are read from env (`todo_dispatch_service.py::_available_agents_for_llm_routing`):

| Env var | Default | Meaning |
|---|---|---|
| `UA_MAX_CONCURRENT_VP_CODER` | `1` | Max simultaneous Cody (`vp.coder.primary`) missions |
| `UA_MAX_CONCURRENT_VP_GENERAL` | `2` | Max simultaneous Atlas (`vp.general.primary`) missions |

`simone` is always in the available set. A VP is only added if its active assignment
count is below its cap. If the LLM picks an unavailable VP, `classify_agent_route` falls
back to `simone` with `confidence="fallback"`. If the LLM call throws entirely, it falls
back to `simone` with `method="fallback"`, `should_delegate=False`.

`classify_agent_route` validates the returned `agent_id` against
`{"simone", "vp.coder.primary", "vp.general.primary"}` and coerces anything else to
`simone`.

## How Simone receives the routing signal

The `_routing` dict is **advisory input to Simone's prompt**, not a binding instruction.
`todo_dispatch_service.py::build_todo_execution_prompt` renders it as a line in the task
block:

- If `_routing.method == "llm"` → labeled **`LLM Routing Judgment`**
- Otherwise → labeled **`Routing Hint`**

Per the dispatch prompt (`TODO_DISPATCH_PROMPT`), an `LLM Routing Judgment` with
`should_delegate=true` is treated as **authoritative** (honor it unless the task clearly
fits an "execute yourself" case). The current default posture is **delegate by default** —
most `source_kind`s are owned by a VP, and Simone only self-executes small / interactive /
judgment tasks (e.g. `chat_panel`, `simone_chat`, one-tool-call invariant fixes).

> Note: legacy doc 05 framed the default as "Simone is the first choice; VPs are
> overflow." The current prompt inverts that emphasis to "delegate by default" with a
> per-`source_kind` ownership table baked into both the prompt and `HEARTBEAT.md`. The
> underlying mechanism (Simone decides) is unchanged; the *bias* shifted toward delegation.

### Explicit target_agent overrides everything

When a task carries an explicit `target_agent` (set by a user, the dashboard "Dispatch
Mission" box, or an upstream pipeline), the prompt renders a `⚡ TARGET_AGENT=<vp_id>`
line. This is **authoritative and non-negotiable**: Simone must delegate to that exact VP
via `vp_dispatch_mission` without re-evaluating, and must **not** consult any LLM Routing
Judgment for that item. Current builds suppress the LLM judgment line entirely when
`target_agent` is set; if both appear (legacy artifact), `target_agent` wins.

## Delegation mechanics

When Simone delegates, she:

1. Calls `vp_dispatch_mission(objective=..., target_vp="vp.general.primary"|"vp.coder.primary", task_id=..., idempotency_key="task-<task_id>")`.
   The `idempotency_key` is **mandatory** to prevent duplicate dispatches on interruption.
2. **Releases her claim with `task_hub_task_action(action="redirect_to", note="<vp_id>")`** —
   NOT `action="complete"`. The work is not done; the VP is just starting. `redirect_to`
   clears retry counters and stamps `metadata.preferred_vp`. The guardrail is satisfied not
   by `redirect_to` itself but by the preceding `vp_dispatch_mission` call (the
   `auto_delegate` branch — see [Lifecycle guardrail](#lifecycle-guardrail)). The VP closes
   the source task itself on completion.
3. Moves on. On VP success the source task auto-closes and the VP emails the requester
   directly (Simone CC'd). On VP failure, a `vp_mission_failure` informational task
   appears in Simone's queue for rescue handling.

> **Gotcha (corrects legacy doc 05):** the old "no task completes without Simone's
> sign-off / `pending_review`" model **was removed from the architecture**. There is no
> per-task `needs_review` pause for routine VP successes. See the in-code reference to
> `docs/01_Architecture/12_VP_Goal_Integration_And_Failure_Rescue_PRD.md` § 2. Simone
> still owns *failure* rescue, but routine success no longer round-trips through her.

When manifests reconcile (`todo_dispatch_service.py::_reconcile_manifest_with_llm_route`):
if the LLM route picks `AGENT_CODER`, the workflow manifest is rewritten to
`workflow_kind="code_change"`, `codebase_root` is resolved from
`approved_codebase_roots_from_env()`, and `repo_mutation_allowed` is set accordingly.

## Lifecycle guardrail

Every claimed work item must end with a durable Task Hub lifecycle mutation, or the
todo-execution guardrail (`mission_guardrails.py::MissionGuardrailTracker`, the `todo_execution`
branch) flags `lifecycle_mutation` as missing and blocks Simone's completion.

The guardrail passes a turn when **any** of these hold:

- a lifecycle action in the enumerated set is observed:
  `{"review", "complete", "block", "park", "approve"}` (this exact tuple is what the
  code tests — see the `not any(action in lifecycle_actions for action in (...))` check);
- a `"delegate"` action is in `lifecycle_actions` → `stage_status="delegated"`;
- a VP dispatch was attempted/succeeded (`self.successful_vp_dispatches or
  self._vp_dispatch_attempted`) → `stage_status="auto_delegate"`.

Note: `redirect_to` is **not** in the enumerated lifecycle-action tuple. A delegation that
ends with `redirect_to` clears the guardrail via the `auto_delegate` branch — because
Simone called `vp_dispatch_mission` first, `_vp_dispatch_attempted` is set, which is what
actually satisfies the check. (The `TODO_DISPATCH_PROMPT` text lists `redirect_to` among
accepted actions; that prompt copy and the literal guardrail enumeration diverge — the
behavior is consistent only because the dispatch attempt accompanies the `redirect_to`.)

`TaskStop` is **not** a lifecycle primitive in this lane and must not be used.

## Gateway VP routing (external-VP fast path)

`gateway.py` contains a separate, lower-level VP routing layer for requests that carry an
explicit `delegate_vp_id` in their metadata (or where an explicit VP intent is inferred).
This is the synchronous request path, distinct from the heartbeat todo lane.

Key behaviors (`gateway.py`, around the request-handling generator):

- `requested_vp_id = request_metadata.get("delegate_vp_id")`.
- VP inference is **disallowed** for certain non-interactive sources:
  `{"cron", "webhook", "heartbeat", "heartbeat_synthetic", "task_run", "email_hook"}`.
- `strict_external_vp` (from `require_external_vp` metadata /
  `vp_explicit_intent_require_external(default=True)`) controls fallback:
  - **strict** → if external-VP dispatch fails, the request errors out; **no** fallback to
    Simone direct execution (`routing="external_vp_dispatch_failed_strict"`).
  - **non-strict** → on dispatch failure, continues on the Simone primary path
    (`routing="external_vp_dispatch_fallback"`).
- The gateway also maintains a coder-VP lease (`_coder_vp_lease_owner =
  "simone-control-plane"`) with periodic heartbeats and a worker-liveness check that
  combines lease liveness with fresh worker heartbeats
  (`vp_worker_heartbeat_stale_seconds(default=180)`).

```mermaid
flowchart TD
    subgraph Ingest
      Email[Email / API / Telegram] --> TH[Task Hub]
      Manual[Manual / Dashboard] --> TH
    end

    TH --> Claim["claim_next_dispatch_tasks()"]

    Claim --> SweepLane["dispatch_sweep / dispatch_immediate<br/>_enrich_with_routing → route_all_to_simone<br/>(always agent_id=simone)"]
    Claim --> TodoLane["todo_dispatch lane<br/>_enrich_with_llm_agent_routing<br/>(LLM Routing Judgment + capacity caps)"]

    SweepLane --> Prompt[build_todo_execution_prompt]
    TodoLane --> Prompt

    Prompt --> Simone[Simone executes prompt]

    Simone -->|self-execute| Deliver[Deliver + task_hub_task_action complete]
    Simone -->|delegate| VPDispatch["vp_dispatch_mission(target_vp, idempotency_key)"]
    VPDispatch --> Redirect["task_hub_task_action redirect_to"]
    VPDispatch --> VP[Atlas / Cody mission]
    VP -->|success| AutoClose[Source task auto-closes, VP emails requester]
    VP -->|failure| Rescue["vp_mission_failure → Simone rescue queue"]

    Simone -.->|⚡ TARGET_AGENT set| ForceDelegate[Authoritative delegate — skip triage]
```

## Why centralize through Simone

- **One execution context to configure.** A single tool-permission set, one session
  context to debug, one heartbeat loop. Eliminates the bug class where different routing
  paths carried different permissions.
- **Batch / cross-task awareness.** Because Simone sees the queue (rather than a blind
  per-ticket worker), she can recognize dependent tasks and avoid redundant work — the
  core advantage over a pure pull-based worker model. (See legacy doc 06 for the full
  comparison against the `agent-worker` pull pattern; that doc is a design essay, not a
  description of shipped behavior.)
- **LLM-native delegation.** The "who should do this" decision is an LLM judgment
  (`classify_agent_route`) plus Simone's own reasoning, not a brittle keyword router.

## Gotchas

- **`qualify_agent*` is gone.** Any doc or comment referencing it describes the retired
  router. The replacement is `route_all_to_simone` (deterministic stamp) + LLM judgment +
  Simone's reasoning.
- **`should_delegate` differs by path.** From `route_all_to_simone` it is always `False`
  (stamp only). From `_enrich_with_llm_agent_routing` it is a real LLM decision and is
  treated as authoritative in the prompt.
- **Delegation closes the source task with `redirect_to`, never `complete`.** Using
  `complete` at dispatch time creates an audit-trail lie and breaks failure rescue. This
  was a real bug fixed per PRD § 5.6.
- **Capacity caps gate the *menu*, not the *decision*.** If a VP is at capacity it is not
  offered to the LLM at all, so the LLM cannot pick it — it falls back to Simone.
- **Simone's heartbeat runs unconstrained in production checkouts.** A bad branch deployed
  without review once introduced a mid-flight `SyntaxError` that crashed a cron; recovery
  required parking the task with careful SQL (plain `cancel` gets resurrected by the
  orphan-reconciler), resetting to `origin/main`, and manual verification. Treat
  Simone-executed code changes in prod with the same caution as any direct-to-main push.
- **Heartbeat auto-triage feeds Simone.** Non-OK heartbeat findings are dispatched to
  Simone (structured findings contract `heartbeat_findings_latest.json`); she owns the
  remediation decision and may fix bounded coding issues autonomously, escalating only
  destructive / security / approval-bound fixes to the operator.

> **Note — `/btw` is UA's own minor sidebar-session command, not Claude Code's native `/btw`.**
> A `/btw` handler in `gateway_server.py` (matches `user_input.startswith("/btw ")`) routes the
> message into an ephemeral side gateway session via `session_hub.py::set_active_sidebar` /
> `get_active_sidebar` (in-memory only); `/return` exits. It is unrelated to the `/btw` slash
> command Claude Code ships. Tangential to Simone-first routing — documented where it lives, in
> `05_channels/05_web_ui_communication.md`.
