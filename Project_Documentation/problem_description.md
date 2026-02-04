# 🩺 Problem Description: Gateway UI Regression & Logging Isolation

## 📝 Executive Summary

After a refactor to isolate Gateway session logs (moving away from global file descriptor hijacking), the Web UI experienced a "hang" state: status windows wouldn't close, document versions disappeared, and real-time log streaming broke. This was primarily caused by a **Session ID Mismatch** between the core agent's log context and the Gateway's session tracking.

## 🔍 The Investigation

### 1. The Starting Point: Logging deadlocks

Initially, `agent_core.py` was using `os.dup2` to hijack `stdout`/`stderr` globally. While this worked for simple CLI runs, it caused deadlocks in the concurrent Gateway environment because background heartbeats and other sessions were competing for the same hijacked stream, often leading to recursive logging or blocked file descriptors.

### 2. The Isolation Fix

We implemented a `SessionAwareRootHandler` and used Python's `contextvars` (`ctx_session_id`) to route logs to specific session files based on the execution context. This successfully removed the global hijacking and resolved the deadlocks.

### 3. The Broken Link (The "Hang" Cause)

The regression occurred because of how session IDs were assigned:

- **Gateway/UI**: Used the workspace folder name (e.g., `session_20260203_123456`) as the canonical `session_id`.
- **Agent Core**: Upon starting a query, it was generating a *random* `uuid.uuid4()` for the logging context.
- **Result**: The UI was listening for events tagged with the folder-based ID, while the logs were being produced under a random UUID. This "ghosted" the UI, making it think the agent was still working (or hadn't started) because it never saw the completion or status events for its expected ID.

### 4. Metadata Loss

In the cleanup of `agent_core.py`, the `SESSION_INFO` event was simplified. The UI's status bar and document header rely on specific keys in this event (`version`, `session_id`, `workspace`) to render correctly. When these were missing or mismatched, the UI failed to display the document version (e.g., "v2.1") and lost track of the session context.

## 🛠️ Changes Made & Technical Details

### `agent_core.py`

We changed the way `run_query` initializes the session:

- **Before**: Used a random UUID for `ctx_session_id`.
- **After**: Extracts the basename of the current `workspace_dir` (e.g., `session_...`) and sets that as the context ID. This aligns the core agent's logging with what the Gateway and UI expect.
- **Enhanced `SESSION_INFO`**: Restored the `version` field and ensured `session_id` is explicitly passed in the data payload.

### `execution_engine.py`

The `ProcessTurnAdapter` (which bridges the CLI logic to the Gateway) was also updated to emit the same enriched `SESSION_INFO` metadata, ensuring that whether a query starts via CLI or UI, the protocol remains consistent.

## 🤖 Guidance for Future Diagnosis

If you encounter a "hang" where the UI shows "Working..." but the logs are empty:

1. **Check IDs**: Verify that the session ID in the Gateway logs matches the ID being set in the `AgentCore` context.
2. **Inspect Protocol**: Ensure the `EventType.SESSION_INFO` data payload contains all fields defined in the Web UI's `events.py` and `SessionInfo` dataclass.
3. **Log Routing**: Check the `SessionAwareRootHandler` to ensure it can still resolve the `ctx_session_id`. If `ctx_session_id` is `None` or mismatched, logs will be dropped or routed to the "orphan" root log.

## 📍 Current Status

We have implemented the alignment fix. A verification script (`verify_fix.py`) has been created to confirm that:

- `SESSION_INFO` contains the correct `session_id` (matching the workspace).
- The `ContextVar` is correctly populated.
- The `version` metadata is present.

The system is now ready for a full-stack restart and verification.

---

## 🔄 Update (2026-02-04): Root Cause Was Broader Than SESSION_INFO

After additional investigation of live runs (notably `session_20260203_233426_fea06c93`),
we found the primary regressions were caused by **cross-session global state**, not
only `SESSION_INFO` metadata.

### Evidence

- A `Bash` tool call inside session `session_20260203_233426_fea06c93` resolved
  `CURRENT_SESSION_WORKSPACE/work_products` to *another* session directory:
  `session_20260203_233417_92eefd38`.
- `run.log` and `activity_journal.log` for one session contained interleaved
  log lines from other sessions (especially heartbeat executions).

### Root Causes

1. **Execution engine detection fell back to legacy**:
   `InProcessGateway` used legacy `AgentBridge` because `EXECUTION_ENGINE_AVAILABLE`
   was `False` on first import due to an import-cycle via `universal_agent.api.__init__`.
2. **Concurrency-unsafe globals**:
   - Workspace routing relied on `os.environ["CURRENT_SESSION_WORKSPACE"]` (global).
   - Stdio/log capture was implemented via process-wide redirection (global).
   - Heartbeats for *other* sessions were running concurrently and stomping this state.

### Fixes Implemented

- **Import-cycle fix**: `src/universal_agent/api/__init__.py` is now lightweight and
  lazily resolves exports to avoid importing gateway/bridges at package import time.
- **Gateway serialization**: `InProcessGateway` now serializes `create_session`,
  `resume_session`, and `execute` behind an asyncio lock to prevent cross-session
  contamination.
- **Scoped stdio capture**:
  `setup_session(attach_stdio=False)` in gateway mode; `ProcessTurnAdapter` redirects
  stdout/stderr to the correct session `run.log` only for the duration of a turn.
- **Heartbeat safety**:
  Heartbeat runs are deferred while *any* session is busy to avoid interleaving
  with active executions.

### Related UI/UX Fixes

- OpsPanel heartbeat summary rendering now safely stringifies non-string payloads.
- Heartbeat REST endpoint supports inactive sessions when a workspace exists on disk.
