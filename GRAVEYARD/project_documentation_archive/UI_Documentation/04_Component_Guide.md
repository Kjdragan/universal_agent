# Universal Agent UI - Component Guide

## Component Overview

The UI is built with React components organized by functionality:

```
components/
├── approvals/
│   └── ApprovalModal.tsx      # URW phase approvals
├── monitor/
│   ├── TerminalLog.tsx         # Tool call visualization
│   ├── ToolCallCard.tsx        # Individual tool call display
│   ├── ActivityFeed.tsx        # Live activity stream
│   └── MetricsPanel.tsx        # Token usage, duration, etc.
├── workspace/
│   ├── FileBrowser.tsx         # Navigate workspace files
│   ├── WorkProductViewer.tsx   # View HTML reports
│   └── InterimProducts.tsx     # In-progress work
└── ui/                         # Base UI components (shadcn-style)
    ├── button.tsx
    ├── card.tsx
    └── [more base components]
```

---

## Core Components

### HomePage (`app/page.tsx`)

The main application component that orchestrates all sub-components.

**Responsibilities:**
- Initialize WebSocket connection on mount
- Set up event listeners for all WebSocket events
- Manage connection status
- Render layout with all sub-components

**Key Hooks:**
```typescript
const ws = getWebSocket();              // WebSocket instance
const { pendingApproval, handleApprove, handleReject } = useApprovalModal();
```

**Layout:**
```
┌─────────────────────────────────────────┐
│ Header (ConnectionIndicator)            │
├────────┬────────────────────┬───────────┤
│        │  ChatInterface     │  Metrics  │
│Session │                    │  Activity │
│ List   │                    │  Terminal │
│        │                    │  Products │
└────────┴────────────────────┴───────────┘
```

---

### ChatInterface (`app/page.tsx`)

Chat UI with message history and input.

**State:**
- `messages`: Array of completed messages
- `currentStreamingMessage`: Temp buffer for streaming text
- `input`: Current user input
- `isSending`: Sending state for UI feedback

**Features:**
- Auto-scroll to latest message
- Streaming text with cursor animation
- User/Agent message differentiation
- Disabled state when disconnected

**Key Code:**
```typescript
const handleSend = async () => {
  useAgentStore.getState().addMessage({
    role: "user",
    content: query,
    is_complete: true,
  });
  ws.sendQuery(query);
};
```

---

### ConnectionIndicator (`app/page.tsx`)

Visual indicator of WebSocket connection status.

**States:**
| Status | Color | Label | Pulse |
|--------|-------|-------|-------|
| `disconnected` | Red | Disconnected | No |
| `connecting` | Yellow | Connecting... | No |
| `connected` | Mint | Connected | No |
| `processing` | Mint | Processing... | Yes |
| `error` | Red | Error | No |

---

### MetricsPanel (`app/page.tsx`)

Displays execution metrics.

**Metrics Shown:**
- Total tokens used
- Tool call count
- Duration (formatted as Xm Xs)
- Iteration count

**Data Source:**
```typescript
const tokenUsage = useAgentStore((s) => s.tokenUsage);
const toolCallCount = useAgentStore((s) => s.toolCallCount);
const startTime = useAgentStore((s) => s.startTime);
const iterationCount = useAgentStore((s) => s.iterationCount);
```

---

### ToolCallCard (`app/page.tsx`)

Expandable card showing tool call details.

**States:**
| Status | Color | Label |
|--------|-------|-------|
| `pending` | Yellow | pending |
| `running` | Mint | running |
| `complete` | Green | complete |
| `error` | Red | error |

**Expanded View Shows:**
- Tool name with monospace font
- Full input JSON (formatted)
- Result preview (truncated to 200 chars)

**Interaction:**
- Click to expand/collapse

---

### TerminalLog (`app/page.tsx`)

Scrollable list of all tool calls.

**Features:**
- Custom scrollbar styling
- Auto-scroll to latest (not implemented yet)
- Empty state with icon

**Data Source:**
```typescript
const toolCalls = useAgentStore((s) => s.toolCalls);
```

---

### ActivityFeed (`app/page.tsx`)

Compact list of recent activity.

**Activity Types:**
- Tool calls (shown with terminal icon)
- Work products (shown with file icon)

**Features:**
- Sorted by time
- Truncated names
- Max height with scroll

---

### WorkProductViewer (`app/page.tsx`)

View HTML reports and other work products.

**Layout:**
- Left panel: List of work products
- Right panel: iframe preview (for HTML)

**Features:**
- Selection highlighting
- HTML rendered in iframe
- Empty state when no products

**Note:** Currently auto-selects nothing; user must click to view.

---

## Modal Components

### ApprovalModal (`components/approvals/ApprovalModal.tsx`)

Modal for URW phase approvals (planning, replan, etc.).

**Props:**
```typescript
interface ApprovalModalProps {
  request: ApprovalRequest | null;     // null = modal hidden
  onApprove: (followupInput?: string) => void;
  onReject: () => void;
}
```

**Request Structure:**
```typescript
interface ApprovalRequest {
  phase_id: string;
  phase_name: string;
  phase_description: string;
  tasks: TaskInfo[];
  requires_followup: boolean;
}
```

**Features:**
- Task list with status indicators
- Optional follow-up input textarea
- Approve/Reject buttons
- Backdrop blur overlay
- Disabled when connection lost

**Hook:** `useApprovalModal()`
- Automatically subscribes to `approval` events
- Manages pending approval state
- Handles WebSocket approval response

---

## UI Utility Components

### Button (`components/ui/button.tsx`)

shadcn-style button with variants.

**Variants:**
- `default` - Primary mint color
- `destructive` - Red for dangerous actions
- `outline` - Bordered, no background
- `secondary` - Purple secondary color
- `ghost` - Hover background only
- `link` - Underlined text

**Sizes:**
- `default` - h-9, px-4
- `sm` - h-8, px-3
- `lg` - h-10, px-8
- `icon` - h-9, w-9 (square)

### Card (`components/ui/card.tsx`)

shadcn-style card components.

**Sub-components:**
- `Card` - Container with border and shadow
- `CardHeader` - Header section
- `CardTitle` - Large title text
- `CardDescription` - Muted subtitle
- `CardContent` - Main content area
- `CardFooter` - Footer with actions

---

## Styling Utilities

### Glassmorphism (`app/globals.css`)

```css
.glass {               /* Medium blur */
  backdrop-filter: blur(12px);
  background: rgba(var(--card-rgb), 0.7);
  border: 1px solid rgba(var(--border-rgb), 0.5);
}

.glass-strong {         /* Heavy blur for headers/modals */
  backdrop-filter: blur(16px);
  background: rgba(var(--card-rgb), 0.8);
  border: 1px solid rgba(var(--border-rgb), 0.5);
}
```

### Gradient Text

```css
.gradient-text {
  background: linear-gradient(to right,
    hsl(var(--primary)),
    hsl(var(--secondary))
  );
  background-clip: text;
  color: transparent;
}
```

### Custom Scrollbar

```css
.scrollbar-thin {
  scrollbar-width: thin;
  scrollbar-color: hsl(var(--muted)) transparent;
}
```

---

## Color Palette (Dark Theme)

```css
--background: 240 10% 3%;      /* #050507 Deep black */
--foreground: 240 5% 92%;      /* #e8e8eb Off-white */

--primary: 158 100% 50%;       /* #00ffc8 Mint */
--secondary: 280 60% 60%;      /* #9d4edd Purple */

--muted: 240 5% 15%;          /* #222224 Dark gray */
--muted-foreground: 240 5% 60%; /* #888888 Medium gray */

--border: 240 5% 15%;         /* Subtle border */
--card: 240 10% 5%;           /* Slightly lighter than bg */
```

---

## Animations

```css
@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes slide-in {
  from { transform: translateY(10px); opacity: 0; }
  to { transform: translateY(0); opacity: 1; }
}

@keyframes pulse-glow {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
```

Applied via:
```tsx
<div className="animate-fade-in" />
<div className="animate-pulse-glow" />
```

---

## Component Data Flow

### Example: New Tool Call

```
1. Backend emits "tool_call" event via WebSocket
                │
                ▼
2. WebSocket Manager (websocket.ts) receives message
                │
                ▼
3. Event listener calls processWebSocketEvent(tool_call)
                │
                ▼
4. Zustand store.addToolCall(toolCall)
                │
                ▼
5. TerminalLog component re-renders with new ToolCallCard
```

### Example: Streaming Text

```
1. Backend emits "text" event (chunk)
                │
                ▼
2. processWebSocketEvent(text)
                │
                ▼
3. store.appendToStream(chunk)
                │
                ▼
4. ChatInterface displays currentStreamingMessage
                │
                ▼
5. Cursor animation shows streaming active
                │
                ▼
6. "query_complete" event
                │
                ▼
7. store.finishStream()
                │
                ▼
8. Message added to messages[], streaming cleared
```

---

## Missing Components (Not Yet Implemented)

The following were planned but not implemented:

1. **FileBrowser** - Navigate workspace directories
   - Planned for left sidebar expansion
   - Would use `/api/files` endpoint

2. **InterimProducts** - Show work-in-progress files
   - Would monitor `work_products/_working/`
   - Show compilation status

3. **ThinkingIndicator** - Show agent reasoning
   - Would display `thinking` events
   - Collapsible detail panel

These can be added by following the existing component patterns.
