# 001 - Front End Architecture Overview

This document explains the overall architecture of the Universal Agent UI and how it connects to the Python backend.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     USER'S BROWSER                          │
│  ┌────────────────────────────────────────────────────────┐ │
│  │            universal_agent_ui.html                     │ │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐ │ │
│  │  │  Chat    │  │  Output  │  │   Neural Activity     │ │ │
│  │  │  Panel   │  │  Panel   │  │   (Tool calls, etc)   │ │ │
│  │  └──────────┘  └──────────┘  └───────────────────────┘ │ │
│  │                      │                                  │ │
│  │              JavaScript (WebSocket)                     │ │
│  └────────────────────────|────────────────────────────────┘ │
└────────────────────────────|─────────────────────────────────┘
                             │
                    WebSocket Connection
                       ws://localhost:8000/ws
                             │
┌────────────────────────────|─────────────────────────────────┐
│                     PYTHON BACKEND                           │
│  ┌────────────────────────────────────────────────────────┐ │
│  │              server.py (FastAPI)                       │ │
│  │   • Serves HTML at /                                   │ │
│  │   • WebSocket endpoint at /ws                          │ │
│  └───────────────────────────|────────────────────────────┘ │
│                              │                               │
│  ┌───────────────────────────|────────────────────────────┐ │
│  │             agent_core.py (UniversalAgent)             │ │
│  │   • Manages Claude SDK + Composio sessions             │ │
│  │   • Emits AgentEvent objects as async generator        │ │
│  └────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

---

## The Communication Flow

1. **User opens browser** → Loads `universal_agent_ui.html` from FastAPI server
2. **JavaScript connects** → Opens WebSocket to `ws://localhost:8000/ws`
3. **User types message** → JS sends `{type: "query", text: "..."}` via WebSocket
4. **Python processes** → `UniversalAgent.run_query()` yields events
5. **Backend streams events** → Each `AgentEvent` is sent as JSON over WebSocket
6. **JS updates UI** → `handleServerMessage()` processes each event type

---

## Key Files

| File | Language | Purpose |
|------|----------|---------|
| `universal_agent_ui.html` | HTML/CSS/JS | The single-page UI |
| `src/universal_agent/server.py` | Python | FastAPI app with WebSocket |
| `src/universal_agent/agent_core.py` | Python | The `UniversalAgent` class |

---

## Why WebSockets?

HTTP is request-response: you ask, you wait, you get one answer.

**WebSockets** are bidirectional and persistent:
- The connection stays open
- Server can push messages anytime (streaming tokens, tool calls)
- Client can send messages anytime (user input)

This is essential because:
- Agent responses stream in piece by piece (not one big response)
- Tool calls happen mid-conversation and we want to show them live
- The UI needs to update in real-time as the agent "thinks"

---

## Next Documents

- **002** - The HTML Structure
- **003** - CSS Styling (Colors, Panels, Animations)
- **004** - JavaScript WebSocket Client
- **005** - Event Types and Message Protocol
