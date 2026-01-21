# Universal Agent UI - Quick Reference

## Commands

### Start Backend

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run python -m universal_agent.api.server
```

### Start Frontend

```bash
cd /home/kjdragan/lrepos/universal_agent/web-ui
npm run dev
```

### Build Frontend

```bash
cd web-ui
npm run build
```

### Test Backend Health

```bash
curl http://localhost:8001/api/health
```

---

## URLs

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8001 |
| API Docs | http://localhost:8001/docs |
| WebSocket | ws://localhost:8001/ws/agent |

---

## File Locations

| What | Path |
|------|------|
| Backend server | `src/universal_agent/api/server.py` |
| Event protocol | `src/universal_agent/api/events.py` |
| Agent bridge | `src/universal_agent/api/agent_bridge.py` |
| Frontend main | `web-ui/app/page.tsx` |
| WebSocket client | `web-ui/lib/websocket.ts` |
| State store | `web-ui/lib/store.ts` |
| TypeScript types | `web-ui/types/agent.ts` |

---

## WebSocket Events (Cheat Sheet)

### Server → Client

| Event | When Sent | Key Fields |
|-------|----------|------------|
| `connected` | On connect | `session: SessionInfo` |
| `text` | Streaming response | `text: string` |
| `tool_call` | Tool starts | `name, id, input` |
| `tool_result` | Tool completes | `tool_use_id, is_error, content_preview` |
| `status` | State change | `status, token_usage?` |
| `work_product` | File created | `content_type, content, filename, path` |
| `approval` | Needs approval | `phase_id, phase_name, tasks` |
| `query_complete` | Query done | `session_id` |
| `error` | Something broke | `message, details?` |

### Client → Server

| Event | When Sent | Key Fields |
|-------|----------|------------|
| `query` | User submits | `text: string` |
| `approval` | User approves | `phase_id, approved, followup_input?` |
| `ping` | Keep-alive | `{}` |

---

## Zustand Store State

```typescript
{
  // Connection
  connectionStatus: "connected" | "processing" | "error" | ...

  // Session
  currentSession: { session_id, workspace, user_id, ... } | null
  sessions: [ { session_id, status, ... }, ... ]

  // Chat
  messages: [ { id, role, content, timestamp, ... }, ... ]
  currentStreamingMessage: string

  // Tools
  toolCalls: [ { id, name, input, result, status, ... }, ... ]

  // Work Products
  workProducts: [ { id, content_type, content, filename, ... }, ... ]

  // Metrics
  tokenUsage: { input, output, total }
  toolCallCount: number
  iterationCount: number
  startTime: number | null

  // UI
  viewMode: { main: "chat" | "monitor" | "split", ... }
  lastError: string | null
}
```

---

## Component Props Reference

### ApprovalModal

```typescript
<ApprovalModal
  request={ ApprovalRequest | null }
  onApprove={(followupInput?: string) => void}
  onReject={() => void}
/>
```

### Key Hook: `useApprovalModal()`

```typescript
const {
  pendingApproval,    // ApprovalRequest | null
  handleApprove,      // (followupInput?: string) => void
  handleReject,       // () => void
} = useApprovalModal();
```

---

## Styling Classes

### Glassmorphism

```tsx
<div className="glass">           {/* Medium blur */}
<div className="glass-strong">     {/* Heavy blur */}
```

### Status Colors

```tsx
className="bg-primary"            {/* Mint */}
className="bg-secondary"          {/* Purple */}
className="bg-destructive"        {/* Red */}
className="bg-muted"             {/* Dark gray */}
```

### Animation

```tsx
<div className="animate-fade-in">
<div className="animate-slide-in">
<div className="animate-pulse-glow">
```

### Scrollbar

```tsx
<div className="scrollbar-thin overflow-y-auto">
```

---

## Common Patterns

### Subscribe to WebSocket Events

```typescript
useEffect(() => {
  const ws = getWebSocket();

  const unsubscribe = ws.on("event_type", (event) => {
    // Handle event
    console.log(event.data);
  });

  return unsubscribe;
}, []);
```

### Read from Zustand Store

```typescript
const messages = useAgentStore((s) => s.messages);
const connectionStatus = useAgentStore((s) => s.connectionStatus);
```

### Update Zustand Store

```typescript
useAgentStore.getState().addMessage({
  role: "user",
  content: "Hello",
  is_complete: true,
});
```

---

## Troubleshooting

### WebSocket not connecting

1. Check backend running: `curl http://localhost:8001/api/health`
2. Check Console for errors
3. Check Network tab → WS for connection status

### Messages not appearing

1. Check Network tab for WebSocket frames
2. Look for `text` events with content
3. Verify `processWebSocketEvent()` is being called

### Tool calls not showing

1. Look for `tool_call` events in Network tab
2. Check `toolCalls` in Zustand store: `useAgentStore.getState().toolCalls`
3. Verify TerminalLog is rendering

### Build fails with type errors

1. Clear cache: `rm -rf .next`
2. Reinstall: `npm install`
3. Rebuild: `npm run build`

---

## Color Palette (Dark Mode)

```css
/* Backgrounds */
--background: #050507      /* Deep black */
--card: #0a0a0d             /* Slightly lighter */
--muted: #222224            /* Dark gray */

/* Text */
--foreground: #e8e8eb      /* Off-white */
--muted-foreground: #888888 /* Medium gray */

/* Accents */
--primary: #00ffc8          /* Mint green */
--secondary: #9d4edd        /* Purple */
--destructive: #ef4444      /* Red */

/* Borders */
--border: #222224           /* Subtle */
--input: #222224            /* Input field border */
--ring: #00ffc8             /* Focus ring */
```

---

## Keyboard Shortcuts (Planned)

| Key | Action | Status |
|-----|--------|--------|
| Enter | Send message | ✅ Implemented |
| Shift+Enter | New line in input | ❌ Not implemented |
| Escape | Close modal | ❌ Not implemented |
| Ctrl+K | Clear chat | ❌ Not implemented |

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `COMPOSIO_API_KEY` | ✅ Yes | - | Composio API key |
| `LOGFIRE_TOKEN` | No | - | Optional tracing |
| `UA_API_PORT` | No | 8001 | Backend port |
| `UA_API_HOST` | No | 0.0.0.0 | Backend host |

---

## Port Conflicts

| Port | Used By | Change Method |
|------|---------|---------------|
| 8001 | Backend API | `UA_API_PORT` env var |
| 3000 | Frontend dev | `-p` flag: `npm run dev -- -p 3001` |

---

## Browser DevTools Tips

### Monitor WebSocket

1. Open DevTools (F12)
2. Go to Network tab
3. Filter by "WS"
4. Click WebSocket connection
5. View "Messages" tab for all events

### Inspect Zustand Store

1. Open DevTools Console
2. Run: `useAgentStore.getState()`
3. Returns entire state object

### React DevTools

1. Install React DevTools extension
2. Components tab shows React tree
3. Can inspect props and state of each component

---

## Performance Tips

### Frontend

- Next.js automatically code-splits by route
- Lazy load approval modal (already conditional render)
- WebSocket uses native browser API (no polyfill)

### Backend

- Agent streams events (no buffering)
- FastAPI uses uvloop (fast async)
- WebSocket uses native websockets library

---

## Security Notes

### Current State

- ⚠️ CORS allows all origins (`*`) - OK for dev, restrict in prod
- ⚠️ No authentication on API - Add for production
- ⚠️ No rate limiting - Add for production
- ✅ Path traversal protection in file API
- ✅ Type-safe WebSocket messages

### Production Checklist

- [ ] Restrict CORS to frontend domain only
- [ ] Add API authentication (JWT, API keys, etc.)
- [ ] Add rate limiting on WebSocket connections
- [ ] Enable HTTPS/WSS for secure connections
- [ ] Add input validation/sanitization
- [ ] Set up CSP headers
