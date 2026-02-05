---
name: browser-debugging
description: Dynamic guide for debugging Web UI issues using the Browser Subagent.
version: 1.0.0
---

# Browser Debugging Skill

> **DYNAMIC SKILL**: This document is a living knowledge base. As we discover new techniques for debugging the Universal Agent Web UI, we MUST update this file.
>
> **Current Focus**: Element verification, API error correlation, and path resolution.

## 1. Core Debugging Philosophy

When the Web UI behaves unexpectedly (e.g., clicks do nothing, data is missing, errors appear):

1. **Don't just look**: Interaction is required to trigger state changes.
2. **Logs are truth**: The visible UI often hides the root cause (e.g., a "File not found" toast vs. a 403 Forbidden API response).
3. **Isolate variables**: Test atomic actions (click one thing) rather than long flows.

## 2. Standard Debugging Patterns

### Pattern A: The "Click and Log" Trace

Use this when an action seems to fail silently or throws a generic error.

```yaml
Task: |
  Navigate to <URL>.
  1. Open console (implicitly done by capture_browser_console_logs).
  2. <Perform Action, e.g., Click Button>.
  3. Wait 2 seconds (allow network requests to complete).
  4. Capture console logs.
  5. Return logs and visual state.
```

**Why**: Network errors (404, 500) and JS exceptions appear in the console logs, which `browser_subagent` can capture.

### Pattern B: The "DOM Inspector"

Use this when you suspect an element is not clickable, hidden, or has incorrect attributes (like the wrong `href`).

```javascript
// JS to inject via execute_browser_javascript
(() => {
  const el = document.querySelector('YOUR_SELECTOR');
  if (!el) return "Element not found";
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return {
    tagName: el.tagName,
    visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden',
    clickable: style.cursor === 'pointer' || el.tagName === 'A' || el.tagName === 'BUTTON',
    attributes: {
      href: el.getAttribute('href'),
      onclick: el.getAttribute('onclick'),
      // Add relevant data attributes
      'data-path': el.getAttribute('data-path')
    }
  };
})()
```

### Pattern C: The "Path Resolution" Test

Specific to file system issues. If a file isn't loading:

1. Request the file via UI.
2. If it fails, **manually verify** if the backend can read it using `read_file`.
3. If backend can read it but UI fails, the issue is likely **Path Mismatch** (frontend sending one path, backend expecting another).

## 3. Reproduction Script Template

When asking the `browser_subagent` to reproduce a bug, use this structure for the `Task` argument:

```text
Target: <URL>
Goal: Reproduce <Issue Description>

Steps:
1. Reload page (clean state).
2. <Setup Step, e.g., Type query>.
3. <Trigger Step, e.g., Click result>.
4. Wait <N> seconds.
5. Capture screenshot (visual proof).
6. Capture console logs (technical proof).
7. Report exact error message seen or log content.
```

## 4. Known Gotchas

* **Leading Slashes**: The API implementation in `server.py` and `gateway_bridge.py` may strip leading slashes from absolute paths. Ensure backend logic handles `home/user` as `/home/user`.
* **Session vs. Project**: The frontend runs in a session context (`AGENT_RUN_WORKSPACES/session_ID`). Project files are outside this. Ensure the backend has fallback logic to read from `BASE_DIR` if the session read fails.
