# Lessons Learned: Browser Debugging & File Path Resolution

**Date**: 2026-02-05
**Context**: Fixing "File not found" errors when clicking relative paths in the Web UI.

## 1. The Issue & Fix

### Problem

The Web UI constructs absolute file paths by appending relative paths (e.g., `./web-ui/app/page.tsx`) to the current workspace path (for example, `/.../AGENT_RUN_WORKSPACES/run_123/`).

However, project source files (like the Web UI code itself) reside in the **Repo Root**, not the **Run Workspace**. The backend API correctly rejected access because the files were not found in the path provided by the frontend.

### Solution: Backend Fallback Logic

We modified `gateway_bridge.py` to implement a robust path resolution strategy:

1. **Check Workspace**: Look for the file exactly as requested (inside the current workspace).
2. **Fallback to Project Root**:
    * If the path looks like it includes the workspace directory prefix (but the file isn't there), **strip the prefix** to recover the relative path.
    * Resolve this relative path against the **Project Base Directory**.
    * **Security Check**: Ensure the resolved project path is still within the project root (prevent traversal to `/etc/passwd`).

This allows the frontend to be "dumb" (always sending workspace paths) while the backend intelligently finds the file in either the sandbox or the project source.

---

## 2. Browser Debugging Lessons

Debugging this issue required verifying UI behavior, network requests, and backend logic. Here is what we learned about using the Browser Subagent effectively:

### A. Check Console Logs Early

Initial verification failed to catch the specific error details. The `browser_subagent` action to `capture_browser_console_logs` is critical.

* **Lesson**: When a UI action fails silently (or with a generic toast), checks logs immediately.
* **Action**: Always include a log capture step after a failed interaction test.

### B. Verify API Traffic

The UI showed "File not found", but we didn't initially know *which path* it was requesting.

* **Lesson**: The UI might transform the data before sending it.
* **Action**: Use `backend` logs (api.log) in conjunction with browser tests to see the *received* request. We added `DEBUG` logs to `gateway_bridge.py` which immediately revealed the path mismatch.

### C. Concise Reproduction Scripts

We ran long listing/navigation tasks.

* **Lesson**: Fast iteration is better. A simple script ("Go to URL, Type 'Check X', Click Link") allows for rapid retry cycles after backend changes.
* **Action**: Create focused "Reproduction" tasks for the browser subagent rather than generic "Explore" tasks.

### D. Compare The User's Browser Profile Against A Clean Browser

Browser automation can prove that a route works in a clean session while the user's real browser still fails.

* **Lesson**: "Clean automation works" does not contradict "the user's browser is still broken." It usually means browser-local state is part of the bug.
* **Action**:
  1. inspect `localStorage` and `sessionStorage`
  2. measure suspicious payload sizes
  3. compare authenticated production behavior in the real browser against a clean browser session

### E. Clear One Suspect Key Before Nuking All Site Data

In the dashboard return-crash incident, the decisive browser-side artifact was one oversized key: `ua.agent-flow-spotlight.v1`.

* **Lesson**: clearing one suspected key preserves the rest of the user's session and gives a cleaner yes/no test than clearing all site data.
* **Action**:
  1. identify the most suspicious persisted key
  2. remove only that key
  3. rerun the failing flow
  4. only wipe all site data if the narrow reset does not help

### F. Use Production Network + Storage Together

If the route returns fine in automation but still crashes in the user's browser, capture:

1. fresh console entries
2. the API request list for the failing transition
3. the persisted storage relevant to the route

* **Lesson**: network traces alone can miss browser-profile corruption, and storage inspection alone can miss response-shape differences. Use both.

---

## 3. Skill Development Recommendation

We should create a **Browser Debugging Skill** (`skill-browser-debug`) to standardize this process for the Universal Agent.

### Proposed Capabilities

1. **`verify_ui_element(selector, expected_text)`**: specific DOM check.
2. **`click_and_monitor(selector, api_pattern)`**: Click an element and watch for specific API calls/errors in the console.
3. **`reproduce_issue(steps_json)`**: A standardized schema for reproduction steps that the agent can execute reliably.

### Goal

Reduce the cognitive load of "how to debug" and allow the agent to simply "run debug pattern X" when a UI issue is reported.
