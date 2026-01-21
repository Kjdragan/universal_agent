# Universal Agent - Modern Front-End UI Implementation Plan

## Overview
Build a modern React/Next.js web interface for the Universal Agent system with real-time chat, monitoring dashboard, and work product visualization.

## Architecture

### Front-End Stack
- **Framework**: Next.js 15 (App Router) + TypeScript
- **UI Library**: shadcn/ui + Radix UI primitives
- **Styling**: Tailwind CSS
- **State**: Zustand for global state, React Query for server state
- **Real-time**: WebSocket connection for live agent events
- **Components**: Custom components built with UI/UX Pro skill guidance

### Back-End API (New)
- **Framework**: FastAPI (rebuilt from scratch)
- **WebSocket**: Real-time bidirectional event streaming
- **REST API**: Sessions, files, configuration, approvals
- **Database**: SQLite (existing durability layer integration)

---

## Phase 1: Backend API Foundation

### 1.1 Create New FastAPI Server
**File**: `src/universal_agent/api/server.py`

```python
# Core endpoints:
- POST /api/chat          # Start new chat session
- GET  /api/sessions      # List all sessions
- GET  /api/sessions/{id} # Get session details
- GET  /api/files         # List workspace files
- GET  /api/files/{path}  # Get file content
- POST /api/approvals     # Submit approval for URW tasks
- WS   /ws/agent          # WebSocket for real-time events
```

### 1.2 WebSocket Event Protocol
**File**: `src/universal_agent/api/events.py`

Define event types matching `agent_core.py`:
- `text` - Agent response chunks
- `tool_call` - Tool being called with params
- `tool_result` - Tool completion with result preview
- `thinking` - Agent internal reasoning
- `status` - Status updates (processing, complete, error)
- `work_product` - HTML reports, files generated
- `auth_required` - OAuth approval needed
- `approval_required` - URW phase approval

### 1.3 Agent Bridge Module
**File**: `src/universal_agent/api/agent_bridge.py`

Bridge between FastAPI and `UniversalAgent` class:
- Initialize agent with streaming event callbacks
- Convert `AgentEvent` to WebSocket messages
- Handle session persistence and workspaces
- Support for follow-up tasks in same session

**Critical files to integrate**:
- `src/universal_agent/agent_core.py` - Core agent with `AgentEvent` streaming
- `src/universal_agent/urw/` - URW orchestration for approvals

---

## Phase 2: Front-End Application

### 2.1 Project Structure
```
src/
├── app/
│   ├── layout.tsx          # Root layout
│   ├── page.tsx            # Main dashboard
│   ├── api/                # Next.js API routes (backend proxy)
│   └── chat/
│       └── [id]/page.tsx   # Individual chat session
├── components/
│   ├── chat/
│   │   ├── ChatInput.tsx
│   │   ├── ChatMessage.tsx
│   │   └── MessageList.tsx
│   ├── monitor/
│   │   ├── TerminalLog.tsx    # Tool calls in terminal style
│   │   ├── ToolCallCard.tsx   # Individual tool visualization
│   │   ├── ActivityFeed.tsx   # Live activity stream
│   │   └── MetricsPanel.tsx   # Token count, tool calls, time
│   ├── workspace/
│   │   ├── FileBrowser.tsx
│   │   ├── WorkProductViewer.tsx  # HTML report iframe
│   │   └── InterimProducts.tsx    # In-progress work
│   ├── approvals/
│   │   └── ApprovalModal.tsx  # URW phase approvals
│   └── ui/                   # shadcn base components
├── lib/
│   ├── api.ts               # API client functions
│   ├── websocket.ts         # WebSocket manager
│   └── store.ts             # Zustand store
└── types/
    └── agent.ts             # TypeScript types
```

### 2.2 Key Pages & Views

**Main Dashboard** (`page.tsx`)
- Sidebar: Session list with status indicators
- Main: Chat interface or monitoring view (toggle)
- Right panel: Activity feed + metrics
- Bottom: File browser / work products

**Chat Interface**
- Message list with markdown rendering
- Real-time streaming text
- Tool call cards in separate panel
- Approval prompts for URW phases

**Monitoring View**
- Terminal-style log output (scrollable)
- Tool call visualization with expand/collapse
- Progress indicators for long-running tasks
- Work product viewer (iframe for HTML)

### 2.3 State Management

**Zustand Store** (`lib/store.ts`)
```typescript
- sessions: Session[]
- currentSession: Session | null
- messages: Message[]
- toolCalls: ToolCall[]
- workProducts: WorkProduct[]
- connectionStatus: 'connected' | 'disconnected' | 'processing'
```

---

## Phase 3: Real-Time Communication

### 3.1 WebSocket Manager
**File**: `src/lib/websocket.ts`

```typescript
class AgentWebSocket {
  connect(sessionId: string)
  sendQuery(text: string)
  sendApproval(approval: Approval)
  on(event: EventType, callback: Handler)
  disconnect()
}
```

### 3.2 Event Handling
- `text` → Append to current agent message
- `tool_call` → Add tool call card, show in terminal
- `tool_result` → Update tool call with result
- `work_product` → Add to work products, auto-open HTML
- `approval_required` → Show approval modal

---

## Phase 4: UI/UX Design (UI/UX Pro Skill)

### Design Principles
- **AGI-era aesthetic**: Dark mode, glassmorphism, subtle animations
- **Functional first**: Clear information hierarchy, minimal cognitive load
- **Real-time feedback**: Status indicators, progress bars, live updates
- **Terminal-inspired log view**: Monospace, syntax highlighting, timestamps

### Key Components to Design
1. **Terminal/Log Panel** - Tool calls with expandable JSON, syntax highlighting
2. **Tool Call Card** - Clean summary with expandable details
3. **Work Product Viewer** - Tabbed interface for multiple outputs
4. **Approval Modal** - Clear action buttons for URW phase approval
5. **Session List** - Status badges, time stamps, quick preview

### Color Palette (Reference)
- Background: Deep (#050507)
- Primary: Mint (#00ffc8)
- Secondary: Purple (#9d4edd)
- Accent: Amber (#ffa500), Magenta (#ff55a3)
- Glass panels with blur effects

---

## Phase 5: Integration & Testing

### 5.1 Backend Integration Points
**Files to modify/integrate**:
- `src/universal_agent/agent_core.py:100` - `AgentEvent` enum (already has what we need)
- `src/universal_agent/agent_core.py:200-400` - `UniversalAgent.run()` streaming events
- `src/universal_agent/urw/harness_orchestrator.py` - URW approval points

### 5.2 Verification Steps
1. Start backend: `uv run python -m universal_agent.api.server`
2. Start frontend: `npm run dev` in Next.js project
3. Test chat flow: Send query → receive streaming response
4. Test tool calls: Verify tools appear in terminal log
5. Test work products: Generate HTML report, view in iframe
6. Test approvals: Trigger URW approval flow
7. Test sessions: Create new session, resume existing

---

## Implementation Order

1. **Backend First** (1-2 days)
   - New FastAPI server skeleton
   - WebSocket endpoint with agent integration
   - Basic REST endpoints

2. **Frontend Skeleton** (1 day)
   - Next.js project setup with shadcn/ui
   - Basic layout and routing
   - WebSocket connection

3. **Core Chat** (1-2 days)
   - Chat input and message list
   - Streaming text rendering
   - Markdown support

4. **Monitoring View** (2 days)
   - Terminal log component
   - Tool call cards
   - Activity feed

5. **Work Products** (1 day)
   - File browser
   - Work product viewer (iframe)
   - Interim product handling

6. **Approvals** (1 day)
   - URW approval modal
   - Follow-up task input

7. **Polish** (1-2 days)
   - UI/UX Pro design refinement
   - Animations and transitions
   - Error handling

---

## Critical Files Reference

**Backend to modify/create**:
- `src/universal_agent/api/server.py` - NEW main FastAPI server
- `src/universal_agent/api/agent_bridge.py` - NEW agent integration
- `src/universal_agent/agent_core.py` - READ: Event types and streaming
- `src/universal_agent/urw/harness_orchestrator.py` - READ: Approval flow

**Frontend to create** (new Next.js project):
- `src/app/layout.tsx`
- `src/app/page.tsx`
- `src/components/monitor/TerminalLog.tsx`
- `src/lib/websocket.ts`
- `src/lib/store.ts`

---

## Notes
- Delete/archive old `src/web/server.py` and `universal_agent_ui.html`
- New backend will run on port 8001 (avoid conflict)
- Frontend dev server on port 3000 with API proxy
- Use existing workspaces: `AGENT_RUN_WORKSPACES/`
