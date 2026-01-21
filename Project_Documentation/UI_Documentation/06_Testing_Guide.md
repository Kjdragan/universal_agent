# Universal Agent UI - Testing Guide

## Pre-Test Checklist

- [ ] Backend running on `http://localhost:8001`
- [ ] Frontend running on `http://localhost:3000`
- [ ] `COMPOSIO_API_KEY` set in `.env`
- [ ] Browser DevTools open (Network + Console tabs)

---

## Test 1: Connection & Initialization

### Steps

1. Open browser to `http://localhost:3000`
2. Observe the UI load

### Expected Results

| Element | Expected |
|---------|----------|
| Header | Shows "Universal Agent v2.0" |
| Status | Green dot + "Connected" (within 2 seconds) |
| Sessions | Shows `session_YYYYMMDD_HHMMSS_xxxxxxxx` |
| Chat area | Shows "Universal Agent" placeholder with "Enter your query to begin" |
| Terminal | Shows "⌨️ Terminal ready" |
| Metrics | Shows "Tokens: 0", "Tools: 0", "Duration: 0s" |

### Debugging

**If not connecting:**
- Check Console for WebSocket errors
- Verify backend is running: `curl http://localhost:8001/api/health`
- Check Network tab for WebSocket connection

---

## Test 2: Basic Query

### Steps

1. In chat input, type: `What is 2+2?`
2. Click send button (or press Enter)
3. Watch the response

### Expected Results

| Phase | Expected |
|-------|----------|
| User message | Appears immediately on right side |
| Status indicator | Changes to "Processing..." (pulsing) |
| Agent response | Text streams character by character |
| Tool calls | None expected for simple math |
| Completion | Status returns to "Connected" |
| Cursor | Stops pulsing when complete |

### Messages to Observe

```json
// Client → Server
{"type":"query","data":{"text":"What is 2+2?"},"timestamp":...}

// Server → Client (stream)
{"type":"text","data":{"text":"2 + 2 equals "},...}
{"type":"text","data":{"text":"4."},...}
{"type":"query_complete","data":{...}}
```

---

## Test 3: Tool Call Visualization

### Steps

1. Send query: `Search for recent AI news`
2. Observe the terminal panel

### Expected Results

| Phase | Expected |
|-------|----------|
| Tool call | Card appears: `mcp__composio__COMPOSIO_SEARCH_WEB` [running] |
| Input | Click to expand, shows `{"tool_slug":"SEARCH_WEB","input":...}` |
| Result | Status changes to [complete], result preview appears |
| Timing | Shows offset like `+2.345s` |
| Color | Running=mint, Complete=green, Error=red |

### Expected WebSocket Events

```json
// Tool call starts
{"type":"tool_call","data":{"name":"...","id":"toolu_...","input":{...}}}

// Tool completes
{"type":"tool_result","data":{"tool_use_id":"...","is_error":false,"content_preview":"..."}}
```

---

## Test 4: Work Product Display

### Steps

1. Send query: `Create a simple HTML report about cats`
2. Wait for completion
3. Check "Work Products" panel

### Expected Results

| Phase | Expected |
|-------|----------|
| Event | `work_product` event received |
| List | New item appears in left panel: `report.html` or similar |
| Click | Click on item shows HTML in iframe preview |
| Rendering | HTML renders correctly (not escaped) |

### Expected WebSocket Event

```json
{
  "type":"work_product",
  "data":{
    "content_type":"text/html",
    "content":"<!DOCTYPE html>...",
    "filename":"cats_report.html",
    "path":"/path/to/work_products/cats_report.html"
  }
}
```

---

## Test 5: Metrics Tracking

### Steps

1. Run a complex query (e.g., `Research and summarize a topic`)
2. Watch metrics panel during execution

### Expected Results

| Metric | Should Update When |
|--------|-------------------|
| Tokens | After `tool_result` or `iteration_end` events |
| Tools | Immediately after `tool_call` |
| Duration | Updates continuously while processing |
| Iterations | Increments on `iteration_end` event |

### Check in Browser Console

```javascript
// After a query completes:
console.log(useAgentStore.getState().tokenUsage)
// {input: 5432, output: 1234, total: 6666}
```

---

## Test 6: Session Persistence

### Steps

1. Run a query that creates work products
2. Refresh the browser page (F5)
3. Check what persists

### Expected Results

| Element | Expected After Refresh |
|---------|----------------------|
| Connection | Reconnects automatically |
| Session ID | **NEW** session created |
| Messages | Cleared (new session) |
| Tool calls | Cleared (new session) |
| Work products | Cleared (new session) |

**Note:** Each page refresh creates a new session. To browse old sessions, you'd need the SessionList component (not yet implemented).

---

## Test 7: Approval Modal

### Steps

1. Send query that triggers planning phase
2. Look for approval modal

### Expected Results

| Phase | Expected |
|-------|----------|
| Modal appears | Shows "Approval Required" header |
| Phase name | Shows "Planning Phase" or similar |
| Tasks listed | Shows array of planned tasks |
| Buttons | "Reject" (outline) and "Approve & Continue" (primary) |
| Followup input | Shows if `requires_followup: true` |

### Test Approve Flow

1. Click "Approve & Continue"
2. Check Network tab for WebSocket message

Expected:
```json
{"type":"approval","data":{"phase_id":"planning","approved":true}}
```

3. Agent resumes execution

### Test Reject Flow

1. Click "Reject"
2. Check for message

Expected:
```json
{"type":"approval","data":{"phase_id":"planning","approved":false}}
```

3. Agent should stop or change direction

---

## Test 8: Error Handling

### Test A: Backend Not Running

1. Stop backend server
2. Refresh page or send query

**Expected:** Status shows "Error", red indicator

### Test B: Invalid Query

1. Send empty query
2. Send very long query (10k+ chars)

**Expected:** Graceful handling, no crash

### Test C: WebSocket Disconnect

1. Start a query
2. Stop backend mid-query
3. Observe frontend behavior

**Expected:** Status changes to "disconnected" or "error"

---

## Test 9: UI Responsiveness

### Resize Browser Window

**Test at different sizes:**

| Size | Expected Behavior |
|------|-------------------|
| 1920x1080 | Full 3-column layout |
| 1280x720 | Still functional, may scroll |
| 768x1024 (tablet) | Sidebars may stack or hide |
| 375x667 (mobile) | May break (not optimized yet) |

### Dark Mode

1. Page should load in dark mode by default
2. Colors should be: dark bg, mint primary, purple secondary
3. Glassmorphism effects visible on panels

---

## Test 10: Concurrent Operations

### Rapid Queries

1. Send query A
2. Immediately send query B (before A completes)

**Expected Behavior:**
- Query B queues or interrupts A
- No crash
- Status indicator shows state correctly

### Navigation During Query

1. Start a query
2. Navigate to another tab
3. Come back

**Expected:**
- Query continues in background
- Messages visible on return
- Metrics updated

---

## Performance Benchmarks

### Target Metrics

| Metric | Target |
|--------|--------|
| Initial page load | < 3 seconds |
| WebSocket connect | < 1 second |
| First byte (time to first text) | < 2 seconds |
| Streaming latency | < 100ms per chunk |
| Tool call display | < 50ms after event |
| Approval modal render | < 100ms |

### Measure in Browser

Open DevTools → Performance tab:

1. Click "Record"
2. Send a query
3. Stop recording when complete
4. Check "Main" thread timing

---

## Browser Compatibility

### Tested Browsers

| Browser | Version | Status |
|---------|---------|--------|
| Chrome | 120+ | ✅ Primary target |
| Firefox | 120+ | ⚠️ Should work, less tested |
| Safari | 17+ | ⚠️ Should work, less tested |
| Edge | 120+ | ✅ Chromium-based |

### Required Features

- WebSocket (Secure or insecure)
- ES2020+ (async/await, optional chaining)
- CSS Grid & Flexbox
- Backdrop-filter (for glassmorphism)

---

## Manual Test Script

Copy-paste this sequence into the chat:

```
1. What is the capital of France?
2. Calculate 25 * 17
3. Tell me a joke
4. What's the weather like today? (will show tool calls)
5. Create a simple HTML page with a blue button
```

**Verify after each:**
- Response appears and streams correctly
- Metrics update
- Tool calls show for queries that need them
- Work products appear for HTML generation

---

## Known Issues

### Current Limitations

1. **Session List** - Cannot browse old sessions (UI shows current only)
2. **Mobile** - Not responsive on mobile screens
3. **Auto-scroll** - Chat doesn't always scroll to latest message
4. **File browser** - Cannot navigate workspace directories
5. **Thinking display** - `thinking` events not shown to user

### Workarounds

| Issue | Workaround |
|-------|-----------|
| Can't see old sessions | Check `AGENT_RUN_WORKSPACES/` directory directly |
| Mobile not working | Use desktop browser |
| Chat doesn't scroll | Manually scroll to bottom |
| Can't browse files | Use backend `/api/files` endpoint directly |
| Thinking hidden | Check browser console for events |

---

## Reporting Issues

When reporting bugs, include:

1. **Browser & Version**: (e.g., Chrome 120)
2. **Steps to Reproduce**: Exact sequence
3. **Expected vs Actual**: What you saw vs what should happen
4. **Console Errors**: Copy from DevTools Console tab
5. **Network Logs**: WebSocket messages from Network tab

### Example Bug Report

```
Title: Tool call card not expanding on Firefox 120

Steps:
1. Send query: "Search for AI news"
2. Wait for tool call to appear in terminal
3. Click on the tool call card

Expected: Card expands to show input JSON
Actual: Nothing happens, card stays collapsed

Console: No errors
Network: tool_call and tool_result events both received correctly
```
