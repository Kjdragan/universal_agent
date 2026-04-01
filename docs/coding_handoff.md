# Run-Per-Task Workspace Refactor Coding Handoff

Date: 2026-03-31
Status: Handoff-ready implementation plan
Audience: An AI coder or engineer who has not worked in this codebase before

## Purpose

This document explains the required refactor to make each accepted work item execute inside its own durable run workspace, instead of reusing a long-lived session workspace.

The immediate driver is a correctness problem in the chat-panel and email-driven report pipelines:

- one direct chat query can currently leave sibling task directories from older work in the same workspace
- one run can show multiple `tasks/<task_name>/...` trees even when the user expects one run to represent one request
- retries and late follow-on steps can drift because the workspace identity is anchored to the transport session instead of the accepted task execution

The desired contract is:

- one accepted request or execution cycle => one durable run workspace
- retries for that execution => `attempts/<n>/` inside that run workspace
- exactly one task subtree inside that run workspace
- transport sessions remain transport containers only; they are not the artifact root for business work

This document is written so a new coder can start implementation without prior project history.

## Executive Summary

The current system still couples Task Hub execution to an already-open gateway session workspace.

For tracked chat:

- `src/universal_agent/gateway_server.py`
- `_prepare_tracked_chat_execution(...)` claims the task with `workspace_dir=session.workspace_dir`
- see `claim_task_for_agent(...)` call at `gateway_server.py:6415-6422`

For dedicated To Do dispatch:

- `src/universal_agent/services/todo_dispatch_service.py`
- the dispatch sweep claims tasks with `workspace_dir=session.workspace_dir`
- see `dispatch_sweep(...)` call path at `todo_dispatch_service.py:356-364`

For email intake:

- email task identity is already separate from execution lineage
- `src/universal_agent/services/email_task_bridge.py`
- stable thread-backed `task_id` is derived in `_deterministic_task_id(...)` at `email_task_bridge.py:44-47`
- workflow lineage is stored separately in the mapping row and Task Hub metadata at `email_task_bridge.py:339-341`, `email_task_bridge.py:410-412`, and `email_task_bridge.py:449-512`

That means the architecture already has the right separation of concerns for email task identity. What it does not yet do is allocate a fresh run workspace per accepted execution.

## Desired Architecture

### Core Rule

Accepted execution is the unit of workspace isolation.

- tracked chat query accepted into Task Hub => create fresh run workspace
- trusted email task accepted for execution => create fresh run workspace
- retries and resumptions for that execution stay inside the same run workspace under `attempts/`

### Ingestion Paths In Scope

This refactor is not chat-only. The coder should treat all canonical intake paths as part of the same execution-lineage problem.

Included ingestion paths:

- direct chat panel requests elevated into tracked Task Hub execution
- trusted inbound email requests materialized through the email bridge and then executed by Simone
- dedicated To Do dispatcher claims from the Task Hub queue, regardless of whether the original task came from chat, email, or another trusted internal source

Not in scope unless discovered as direct dependencies:

- legacy one-off hook sessions that do not create durable Task Hub execution
- unrelated heartbeat-only supervision paths that do not own the final business artifacts

### Target Filesystem Shape

```text
AGENT_RUN_WORKSPACES/
└── run_<id>/
    ├── run_manifest.json
    ├── transcript.md
    ├── trace.json
    ├── work_products/
    ├── turns/
    ├── activity.jsonl
    ├── attempts/
    │   ├── 001/
    │   │   ├── attempt_meta.json
    │   │   ├── run.log
    │   │   ├── trace.json
    │   │   ├── transcript.md
    │   │   └── work_products/
    │   └── 002/
    └── tasks/
        └── <task_name>/
            ├── task_manifest.json
            ├── search_results/
            │   ├── COMPOSIO_SEARCH_*.json
            │   ├── processed_json/
            │   └── crawl_*.md
            ├── filtered_corpus/
            ├── research_overview.md
            ├── refined_corpus.md
            ├── report/
            │   ├── report.html
            │   └── final_report.pdf
            └── scratch/
```

### Domain Model

- `GatewaySession`: browser/websocket transport and conversational continuity
- `Task Hub item`: durable business task
- `run_id`: one accepted execution container
- `attempt_id`: retries and resumptions for that run
- `task_name`: one task subtree under the run

Recommended mapping:

- chat panel: one user query => one Task Hub item => one run
- trusted email: one email thread remains one durable task, but each accepted execution cycle on that task gets its own run

That preserves thread history without forcing all executions for a long email thread into one artifact workspace forever.

## Why This Change Is Needed

### 1. Current tracked chat incorrectly anchors work to session workspace

Relevant code:

- `src/universal_agent/gateway.py`
- `create_session(...)` creates one workspace per session at `gateway.py:459-520`
- `resume_session(...)` reuses the same workspace at `gateway.py:571-616`

- `src/universal_agent/gateway_server.py`
- `_should_track_chat_panel_request(...)` determines whether a chat request is elevated into Task Hub at `gateway_server.py:6347-6363`
- `_prepare_tracked_chat_execution(...)` creates the tracked task at `gateway_server.py:6365-6425`
- the claim uses `provider_session_id=session.session_id` and `workspace_dir=session.workspace_dir` at `gateway_server.py:6415-6422`

Result:

- multiple tracked chat tasks can accumulate under one session workspace
- the right-panel file tree may show unrelated old task directories beside the current request

### 2. Current dedicated To Do execution also anchors work to session workspace

Relevant code:

- `src/universal_agent/services/todo_dispatch_service.py`
- the dispatcher claims tasks with `provider_session_id=session.session_id` and `workspace_dir=session.workspace_dir` at `todo_dispatch_service.py:356-364`

- `src/universal_agent/services/dispatch_service.py`
- `dispatch_sweep(...)` is a thin wrapper over `task_hub.claim_next_dispatch_tasks(...)` and passes through workflow/session/workspace lineage at `dispatch_service.py:189-212`

Result:

- dispatcher sessions are still acting as the artifact root
- task execution isolation is weaker than intended by the run/attempt design

### 3. Email already has the correct identity split, but not the desired workspace split

Relevant code:

- `src/universal_agent/services/email_task_bridge.py`
- stable thread-based task identity at `email_task_bridge.py:44-47`
- materialization persists workflow linkage fields at `email_task_bridge.py:339-341`, `email_task_bridge.py:368-393`, `email_task_bridge.py:410-412`
- `link_workflow(...)` backfills `workflow_run_id`, `workflow_attempt_id`, `provider_session_id` at `email_task_bridge.py:449-512`

- `src/universal_agent/services/agentmail_service.py`
- inbound trusted email materialization delegates to the bridge at `agentmail_service.py:1337-1362`
- execution result linkage backfills `run_id`, `attempt_id`, and `session_id` at `agentmail_service.py:2210-2237`

Result:

- email tasks are already durable and thread-stable
- the missing piece is run workspace allocation per accepted execution, not thread identity

### 4. Any other Task Hub ingestion that reaches `todo_execution` must follow the same rule

If another ingress path eventually lands in the canonical Task Hub execution lane, it should not keep a special shared workspace rule.

The correct general contract is:

- ingress creates or updates the durable task identity
- claim/execution allocates the execution run
- execution artifacts belong to that run

This means the coder should audit for any non-chat, non-email path that still calls into Task Hub claim paths with a long-lived transport session workspace.

## Non-Goals

- do not remove durable Task Hub task identity
- do not collapse email threads into separate tasks for every reply
- do not make retries create sibling run roots unless explicitly intended
- do not keep using transport session workspace as the primary artifact root for accepted execution

## Proposed Implementation Strategy

### Phase 1: Introduce explicit run allocation for accepted execution

Create a small, explicit helper responsible for allocating or resolving the execution run before any Task Hub claim is finalized.

Suggested new concept:

- `ExecutionRunContext`
  - `run_id`
  - `workspace_dir`
  - `attempt_id` or initial attempt metadata
  - `provider_session_id`
  - `task_id`
  - `origin` such as `chat_panel` or `email`

Possible placement:

- `src/universal_agent/services/execution_run_service.py`

Responsibilities:

- create a durable run workspace rooted under `AGENT_RUN_WORKSPACES/`
- persist run metadata in whatever catalog/service currently owns run registration
- return canonical lineage values to Task Hub claim callers
- optionally stamp session metadata with `run_id` only for UI visibility, not as the primary workspace source of truth

### Phase 2: Change tracked chat to allocate one run per query

Primary touchpoints:

- `src/universal_agent/gateway_server.py`
- `_prepare_tracked_chat_execution(...)`
- websocket execution flow around `gateway_server.py:26694-26721`

Required behavior:

1. user submits chat query
2. request is elevated into tracked Task Hub execution
3. before `task_hub.claim_task_for_agent(...)`, allocate a fresh execution run
4. claim the task with:
   - `workflow_run_id=<new_run_id>`
   - `provider_session_id=<execution session or dedicated provider session>`
   - `workspace_dir=<new_run_workspace>`
5. ensure downstream execution metadata uses the run workspace, not the original chat session workspace

Important design choice:

- keep the browser/websocket session alive for UX continuity
- do not let its `workspace_dir` remain the artifact root for this tracked task

Recommended approach:

- either create a dedicated execution session bound to the new run workspace
- or introduce request-level override metadata that all execution and file APIs prefer over the transport session workspace

The first approach is cleaner.

### Phase 3: Change email execution to allocate one run per accepted execution

Primary touchpoints:

- `src/universal_agent/services/agentmail_service.py`
- `src/universal_agent/services/email_task_bridge.py`
- any trusted-email execution dispatch path in `src/universal_agent/gateway_server.py`

Required behavior:

1. inbound email still materializes or updates the same durable email task
2. once the task is accepted for active execution, allocate a fresh execution run
3. backfill the email-task mapping using `link_workflow(...)`
4. claim the Task Hub work with the new run workspace
5. all produced report artifacts land under that run only

Important nuance:

- one email thread can receive multiple accepted executions over time
- each such execution can have a new run
- `email_task_mappings` should continue to point to the latest execution lineage or preserve latest-active lineage, depending on UI needs

Additional required audit:

- review any path in `agentmail_service.py`, `email_task_bridge.py`, and `gateway_server.py` that emits a session ID, run ID, or assignment notification for trusted email tasks
- ensure those emitted notifications and dashboard projections reference the dedicated execution run, not the initial triage or listener session

### Phase 4: Make dedicated To Do dispatcher run-aware

Primary touchpoints:

- `src/universal_agent/services/todo_dispatch_service.py`
- `src/universal_agent/services/dispatch_service.py`
- `src/universal_agent/task_hub.py`

Current behavior:

- `dispatch_sweep(...)` claims against `session.workspace_dir`

Required behavior:

- dispatcher must allocate a run per claimed task or per dispatch batch, depending on product decision

Recommended decision:

- one claimed task => one run

Reason:

- this matches the desired one-task-per-run tree
- it prevents a single dispatcher session from mixing unrelated tasks
- it lines up with the user expectation for isolated report workspaces

This phase is what makes the fix apply uniformly to all ingestion paths.

Even if chat and email are corrected upstream, any remaining dispatcher path that still multiplexes multiple claimed tasks into one execution workspace will reintroduce the same artifact-collision problem.

Implementation note:

- if a single dispatcher loop claims up to 5 tasks today, reduce that path to claim and launch each task into its own run workspace
- batching multiple unrelated tasks into one execution workspace is exactly what should stop happening

### Phase 5: Ensure UI and APIs prefer run lineage over transport session lineage

Primary touchpoints:

- `src/universal_agent/gateway_server.py`
- `_session_run_summary(...)` and `_session_run_id(...)` at `gateway_server.py:3968-3991`
- any file-browser/session payload APIs
- `web-ui/app/page.tsx`

Required behavior:

- if an execution has `workflow_run_id`, UI file browsing should use that run
- current task/file view must not be sourced from the long-lived chat transport session if a task-specific run exists

Recommended behavior:

- when showing a tracked task or active assignment, prefer `assignment.workflow_run_id` and `assignment.workspace_dir`
- session-level explorer fallback should only be used when no task/run is associated

## Data Model Changes

### Task Hub

Relevant schema:

- `src/universal_agent/task_hub.py`
- `task_hub_items` at `task_hub.py:182-207`
- `task_hub_assignments` at `task_hub.py:226-239`

Observed code currently inserts `workspace_dir` into assignments in claim paths at:

- `task_hub.py:1049-1066`
- `task_hub.py:1141-1157`

The implementation should ensure every claimed execution assignment has:

- `workflow_run_id`
- `workflow_attempt_id` if applicable
- `provider_session_id`
- `workspace_dir`

Those values must refer to the dedicated execution run, not the browser transport session.

### Email Task Mapping

Keep:

- stable `task_id` per thread
- latest or active workflow linkage fields

Potential enhancement:

- if historical execution visibility is needed, add an execution history table instead of overwriting only the latest linkage in `email_task_mappings`

This is optional for the immediate refactor.

## Files And Functions To Inspect First

Start with these files in this order:

1. `src/universal_agent/gateway.py`
   - `create_session(...)`
   - `resume_session(...)`

2. `src/universal_agent/gateway_server.py`
   - `_should_track_chat_panel_request(...)`
   - `_prepare_tracked_chat_execution(...)`
   - `_session_run_summary(...)`
   - websocket execute path around `26694-26721`
   - Task Hub dashboard task-action and history endpoints

3. `src/universal_agent/services/todo_dispatch_service.py`
   - dispatch sweep claim path
   - `TODO_DISPATCH_PROMPT`

4. `src/universal_agent/services/dispatch_service.py`
   - `dispatch_sweep(...)`

5. `src/universal_agent/task_hub.py`
   - `claim_next_dispatch_tasks(...)`
   - `claim_task_for_agent(...)`
   - assignment metadata updates

6. `src/universal_agent/services/email_task_bridge.py`
   - `_deterministic_task_id(...)`
   - `materialize(...)`
   - `link_workflow(...)`

7. `src/universal_agent/services/agentmail_service.py`
   - trusted email materialization path
   - workflow linkage backfill path

8. `src/universal_agent/services/email_task_bridge.py`
   - stable email-thread task identity
   - latest execution linkage fields
   - any assumptions that one email task maps to one workspace forever

9. `src/mcp_server.py`
   - research/report artifact roots
   - anything assuming `workspace/tasks/<task_name>/...`

10. `web-ui/app/page.tsx`
   - session explorer data-source selection

11. Search broadly for other claim sites
   - `claim_task_for_agent(`
   - `claim_next_dispatch_tasks(`
   - `dispatch_sweep(`
   - `workflow_run_id`
   - `provider_session_id`
   - `workspace_dir`

The coder should confirm there are no remaining ingress or dispatch paths that still bind execution artifacts to a reusable transport session workspace.

## Acceptance Criteria

The refactor is complete when all of the following are true.

### Chat panel

- one direct chat request produces a fresh run workspace
- that run contains only one `tasks/<task_name>/` directory
- a second unrelated chat request produces a new run workspace, not a sibling task directory under the first run
- retries land under `attempts/` for the same run

### Email

- one trusted email thread remains one durable task in Task Hub
- each accepted execution cycle for that task gets a fresh run workspace
- follow-up emails update the same task identity but do not pollute an older completed run workspace
- email-side notifications, mapping rows, and dashboard projections resolve to the current execution run for active work

### To Do dispatcher

- claiming multiple unrelated tasks no longer binds them to one shared execution workspace
- each active assignment exposes its own `workflow_run_id` and `workspace_dir`
- this is true regardless of whether the task originated from chat, email, or another trusted internal ingestion source

### File explorer

- the right-panel browser shows the current task's run workspace, not stale sibling task directories from earlier unrelated work

### Research/report pipelines

- generated research artifacts appear under the task subtree of the dedicated run
- `refined_corpus.md` and report outputs are easy to locate via run and assignment metadata

## Test Plan

### Unit tests

Add or update tests for:

- tracked chat intake allocating a new run per query
- dispatcher claim path allocating dedicated run lineage
- email accepted execution linking a fresh run without changing stable thread task identity
- Session Explorer preferring assignment/run workspace over raw session workspace

Likely locations:

- `tests/unit/`
- `tests/integration/`

Look for existing workspace-hint and research pipeline tests before adding new files.

### Integration tests

Minimum scenarios:

1. chat query A
   - assert one run
   - assert one task subtree

2. chat query B in same browser session
   - assert different run
   - assert no sibling task in run A

3. trusted email task first execution
   - assert thread-stable task id
   - assert fresh run workspace

4. trusted email follow-up causing a second execution
   - assert same task id
   - assert new run id

5. dispatcher run
   - assert assignment workspace is not the dispatcher transport session workspace

6. email follow-up after a completed run
   - assert same durable `task_id`
   - assert a second accepted execution allocates a different `run_id`
   - assert the previous run workspace remains unchanged

7. non-chat Task Hub ingress
   - pick one additional trusted internal path if available
   - assert that once it reaches `todo_execution`, it receives its own run workspace

### Manual validation

Use the three-panel UI and verify:

- current request only shows one task subtree under the active run
- old topics do not appear as sibling task dirs in the current run
- dashboard lifecycle still completes correctly
- final delivery still works through the sanctioned internal tools

## Risks And Edge Cases

### Risk 1: breaking session continuity

If execution is moved to a fresh run workspace, chat streaming and UI continuity must still appear tied to the user’s live browser session.

Mitigation:

- keep transport session and execution run distinct
- preserve user-visible session continuity while swapping execution lineage underneath

### Risk 2: email thread updates

A new inbound on an existing email thread must not incorrectly overwrite historical artifact roots for an older completed execution.

Mitigation:

- treat email task identity as stable
- treat execution run linkage as per-execution

### Risk 3: legacy code paths that assume `session.workspace_dir`

Many downstream tools still read `session.workspace_dir` directly.

Mitigation:

- audit all claim, report, research, and file-explorer paths
- introduce one canonical helper for “active execution workspace resolution”
- migrate callers to that helper instead of ad hoc session workspace reads

## Recommended Helper APIs

Introduce a single canonical resolver layer instead of scattered logic.

Suggested helpers:

- `allocate_execution_run(...) -> ExecutionRunContext`
- `resolve_active_execution_workspace(session, request_metadata, assignment) -> str`
- `resolve_active_run_id(session, request_metadata, assignment) -> str`

If these helpers exist, downstream components should not guess whether to use:

- transport session workspace
- assignment workspace
- run catalog lookup
- task metadata

They should ask one canonical resolver.

## Documentation To Update After The Code Change

Once implemented, update at least:

- `docs/03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md`
- `docs/03_Operations/90_Artifacts_Workspaces_And_Remote_Sync_Source_Of_Truth_2026-03-06.md`
- `docs/03_Operations/104_Run_Attempt_Lifecycle_And_Nomenclature_Migration_Plan_2026-03-24.md`
- `docs/02_Subsystems/Task_Hub_Dashboard.md`
- `docs/02_Flows/04_Chat_Panel_Communication_Layer.md`

## Recommended Implementation Order

1. Add explicit execution-run allocation service
2. Convert tracked chat intake to use it
3. Convert trusted email accepted execution to use it
4. Convert dispatcher claim path to use it for all ingress sources
5. Update Session Explorer/run-resolution APIs and UI
6. Audit for any remaining non-chat ingestion paths that still bind to reusable session workspaces
7. Update tests
8. Update canonical docs

## Short Recommendation

This refactor is worth doing.

It is the cleaner architecture, it matches the run/attempt model already documented in the repo, and it removes a class of confusing bugs caused by treating a long-lived transport session as if it were the durable execution workspace.

The correct long-term rule is:

- sessions are for transport
- tasks are for durable business identity
- runs are for artifact isolation and execution lineage
- attempts are for retries
