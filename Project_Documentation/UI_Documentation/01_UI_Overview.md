# Universal Agent UI - Overview

## What Was Built

A modern React/Next.js web interface for the Universal Agent system with real-time chat, monitoring dashboard, and work product visualization.

**Status**: ✅ Implementation Complete (as of 2025-01-21)

---

## Quick Start

```bash
# Backend (FastAPI + WebSocket)
cd /home/kjdragan/lrepos/universal_agent
uv run python -m universal_agent.api.server
# Runs on http://localhost:8001

# Frontend (Next.js)
cd /home/kjdragan/lrepos/universal_agent/web-ui
npm install
npm run dev
# Runs on http://localhost:3000
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Browser (Frontend)                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │   Chat UI    │  │  Terminal    │  │   Work Products      │ │
│  │              │  │  Monitor     │  │   Viewer             │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘ │
│         │                  │                                    │
│         └──────────────────┴────────────────────┐               │
│                    │ WebSocket (ws://localhost:8001/ws/agent)   │
└────────────────────┼───────────────────────────────────────────┘
                     │
┌────────────────────┼───────────────────────────────────────────┐
│                    ▼                    Backend               │
│  ┌──────────────────────────────────────────────────────┐     │
│  │            FastAPI Server (port 8001)                 │     │
│  │  ┌────────────────┐    ┌─────────────────────────┐   │     │
│  │  │  WebSocket     │    │  REST API               │   │     │
│  │  │  /ws/agent     │    │  /api/sessions          │   │     │
│  │  └───────┬────────┘    │  /api/files             │   │     │
│  │          │             │  /api/approvals         │   │     │
│  │          ▼             └─────────────────────────┘   │     │
│  │  ┌─────────────────────────────────────────────┐   │     │
│  │  │         Agent Bridge                        │   │     │
│  │  │  - Manages sessions                         │   │     │
│  │  │  - Bridges to UniversalAgent                │   │     │
│  │  │  - Streams events via WebSocket             │   │     │
│  │  └───────────────────┬─────────────────────────┘   │     │
│  └──────────────────────┼─────────────────────────────┘     │
│                         │                                    │
│  ┌──────────────────────┼─────────────────────────────────┐ │
│  │  UniversalAgent       ▼                                 │ │
│  │  (agent_core.py)                                       │ │
│  │  - Composio tools                                      │ │
│  │  - MCP servers                                         │ │
│  │  - Sub-agents (report-writer, research-specialist)     │ │
│  └────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
```

---

## UI Layout

```
┌────────────────────────────────────────────────────────────────────┐
│ Header: Universal Agent v2.0                     [Connected ●]     │
├──────────┬────────────────────────────────────┬───────────────────┤
│          │                                    │                   │
│ Sessions │         Chat Interface             │  Metrics          │
│          │  ┌──────────────────────────────┐ │  Tokens: 12,345   │
│ session_ │  │ Agent: Here's the report...    │ │  Tools: 23        │
│ 20250121 │  │                                │ │  Duration: 2m 30s  │
│          │  │ User: Great thanks!            │ │  Iterations: 5    │
│ session_ │  │                                │ │                   │
│ 20250120 │  └──────────────────────────────┘ │  Activity         │
│          │  [Enter your query...]      [➤]  │  ⌨️ Read           │
│          │                                    │  📄 Write          │
│          │                                    │  ⚡ COMPOSIO_...   │
│          ├────────────────────────────────────┤                   │
│          │         Terminal Log               │  Work Products    │
│          │  ┌──────────────────────────────┐ │  ┌─────┬────────┐ │
│          │  │ ⌨️ Read         [running]     │ │  │repo│ report │ │
│          │  │   Input: {...}               │ │  │rt.md│ .html  │ │
│          │  │                              │ │  └─────┴────────┘ │
│          │  │ 📦 Tool Result               │ │  [Preview iframe]│
│          │  │   Preview: {...}             │ │                   │
│          │  └──────────────────────────────┘ │                   │
└──────────┴────────────────────────────────────┴───────────────────┘
```

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Real-time Chat** | Streaming text responses from the agent via WebSocket |
| **Terminal Monitor** | Tool call visualization with expandable details |
| **Work Products** | View HTML reports and generated files inline |
| **Session Management** | Browse and resume previous agent sessions |
| **Metrics Dashboard** | Token usage, tool call count, duration tracking |
| **Approval Modal** | URW phase approvals (plan review, replan requests) |
| **Dark Theme** | AGI-era aesthetic with glassmorphism effects |

---

## Technology Stack

### Frontend (`web-ui/`)
- **Framework**: Next.js 15 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS
- **State**: Zustand
- **Real-time**: Native WebSocket
- **UI Components**: Radix UI primitives

### Backend (`src/universal_agent/api/`)
- **Framework**: FastAPI
- **WebSocket**: Native `websockets` library
- **Agent Integration**: `UniversalAgent` from `agent_core.py`
- **Database**: SQLite (via existing `AGENT_RUN_WORKSPACES/`)

---

## File Structure

```
universal_agent/
├── src/universal_agent/api/          # NEW - Backend API
│   ├── server.py                     # FastAPI + WebSocket
│   ├── events.py                     # Event protocol
│   ├── agent_bridge.py               # Agent integration
│   └── __init__.py
│
├── web-ui/                           # NEW - Frontend
│   ├── app/
│   │   ├── page.tsx                  # Main dashboard
│   │   ├── layout.tsx                # Root layout
│   │   └── globals.css               # Global styles
│   ├── components/
│   │   ├── approvals/
│   │   │   └── ApprovalModal.tsx     # URW approval modal
│   │   └── ui/                       # Base UI components
│   ├── lib/
│   │   ├── store.ts                  # Zustand state
│   │   ├── websocket.ts              # WebSocket manager
│   │   └── utils.ts                  # Utilities
│   └── types/
│       └── agent.ts                  # TypeScript types
```

---

## Related Documentation

- `02_Architecture.md` - Detailed system architecture
- `03_API_Reference.md` - WebSocket and REST API documentation
- `04_Component_Guide.md` - React component documentation
- `05_Setup_Guide.md` - Installation and setup instructions
- `06_Testing_Guide.md` - How to test the UI
