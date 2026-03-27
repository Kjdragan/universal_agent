# Task Hub Dashboard

> **Canonical source of truth** for the Task Hub Dashboard frontend — design system, component architecture, API integration, and Kanban UX patterns.
>
> **Last updated:** 2026-03-26

---

## 1. Overview

The Task Hub Dashboard is the primary UI for managing proactive tasks within Universal Agent. It replaces the legacy Todoist-based task management interface with a fully integrated, in-house Kanban board built as a Next.js client-side component consuming Python backend APIs.

**Key design principles:**
- **Glassmorphism-first**: All panels use `glass`/`tactical-panel` CSS classes with backdrop blur and translucent backgrounds
- **KCD Design System**: Strict adherence to the `kcd-*` Tailwind color palette (see `tailwind.config.ts`)
- **Responsive**: Stacking columns on `< md` breakpoints
- **Real-time awareness**: Polling-based data refresh with optimistic UI updates

---

## 2. File Map

| File | Purpose |
|------|---------|
| `web-ui/app/dashboard/todolist/page.tsx` | Main dashboard component (`"use client"`) — Kanban board, task cards, filters, lifecycle actions |
| `web-ui/app/globals.css` | KCD design tokens, glassmorphism utilities, tactical panel styles |
| `web-ui/tailwind.config.ts` | `kcd-*` color palette configuration |
| `src/universal_agent/gateway_server.py` | Backend API endpoints consumed by the dashboard |
| `src/universal_agent/task_hub.py` | Core Task Hub data layer (SQLite) |
| `src/universal_agent/services/dispatch_service.py` | Dispatch logic for "Start Now" / approval / scheduled tasks |

---

## 3. Design System

### 3.1 Color Palette (`kcd-*`)

The dashboard exclusively uses the `kcd-*` Tailwind palette defined in `tailwind.config.ts`:

| Token | Value | Usage |
|-------|-------|-------|
| `kcd-bg` | `#0a0e17` | Page background |
| `kcd-surface` | `#111827` | Card/panel backgrounds |
| `kcd-surface-alt` | `#1a2332` | Elevated surfaces, hover states |
| `kcd-border` | `#1e293b` | Panel borders |
| `kcd-text` | `#e2e8f0` | Primary text |
| `kcd-text-muted` | `#94a3b8` | Secondary/label text |
| `kcd-accent` | `#38bdf8` | Primary accent (links, focus rings) |
| `kcd-accent-hover` | `#7dd3fc` | Accent hover state |
| `kcd-success` | `#22c55e` | Success states, completed tasks |
| `kcd-warning` | `#f59e0b` | Warning states, medium priority |
| `kcd-error` | `#ef4444` | Error/critical states, high priority |

### 3.2 Glassmorphism Utilities

Defined in `globals.css`:

```css
.glass {
  background: rgba(17, 24, 39, 0.6);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(56, 189, 248, 0.1);
}

.tactical-panel {
  background: rgba(17, 24, 39, 0.8);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(30, 41, 59, 0.6);
  border-radius: 0.75rem;
}
```

### 3.3 Typography

- **Font stack**: Inter (via Google Fonts) with system fallbacks
- **Headings**: `font-semibold` or `font-bold` depending on hierarchy
- **Body**: `text-sm` (14px) for card content, `text-xs` (12px) for metadata

---

## 4. Component Architecture

### 4.1 Page Structure

```
page.tsx ("use client")
├── Header Bar (title + filter controls)
├── Quick-Add Input Bar (planned: Phase 6b)
├── Morning Report Banner (planned: Phase 6c)
└── Kanban Board
    ├── Column: To Do (status: "open")
    ├── Column: In Progress (status: "in_progress")  
    ├── Column: Review (status: "needs_review")
    └── Column: Done (status: "completed")
```

### 4.2 Task Card Components

Each task card renders:
- **Priority badge**: Color-coded using `priorityColorClass()` helper
- **Title**: Truncated with hover tooltip for overflow
- **Source pill**: Visual indicator of task origin (`sourceKindPill` helper)
- **Labels**: Tag chips for `agent-ready`, brainstorm stage, etc.
- **Action buttons**: Contextual lifecycle actions per column

### 4.3 Helper Functions

| Function | Purpose |
|----------|---------|
| `priorityColorClass(priority)` | Maps P0–P3 → Tailwind color classes (`kcd-error`, `kcd-warning`, `kcd-accent`, `kcd-text-muted`) |
| `sourceKindPill(sourceKind)` | Renders origin badge (email, heartbeat, manual, brainstorm) |
| `statusColumns` | Maps task statuses to Kanban column definitions |

---

## 5. API Integration

The dashboard consumes the following backend REST endpoints from `gateway_server.py`:

### 5.1 Read Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/task-hub/items` | GET | List all tasks (with optional status/label filters) |
| `/api/task-hub/items/{task_id}` | GET | Get single task details |
| `/api/task-hub/queue` | GET | Get current dispatch queue state |
| `/api/task-hub/morning-report` | GET | Get deterministic morning report snapshot |
| `/api/task-hub/comments/{task_id}` | GET | List comments for a task |
| `/api/task-hub/questions/pending` | GET | List pending clarification questions |

### 5.2 Write Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/task-hub/items` | POST | Create/upsert a task (quick-add) |
| `/api/task-hub/items/{task_id}/action` | POST | Lifecycle action: `complete`, `block`, `park`, `review`, `reopen` |
| `/api/task-hub/dispatch/immediate/{task_id}` | POST | "Start Now" — immediate dispatch to agent |
| `/api/task-hub/dispatch/approve/{task_id}` | POST | Approve a task for agent execution |
| `/api/task-hub/comments/{task_id}` | POST | Add a comment to a task |
| `/api/task-hub/questions/{question_id}/answer` | POST | Answer a pending clarification question |

### 5.3 Data Flow

```mermaid
graph LR
    subgraph Frontend["Next.js Dashboard"]
        KC["Kanban Component"]
        FC["Fetch Client"]
    end

    subgraph Backend["Gateway Server"]
        API["REST Endpoints"]
        TH["task_hub.py"]
        DS["dispatch_service.py"]
        PA["proactive_advisor.py"]
    end

    subgraph Storage["SQLite"]
        DB["activity_state.db"]
    end

    KC --> FC
    FC -->|"fetch()"| API
    API --> TH
    API --> DS
    API --> PA
    TH --> DB
    DS --> DB
    PA --> DB
```

---

## 6. Task Lifecycle on the Dashboard

### 6.1 Status Columns

| Column | Status | Available Actions |
|--------|--------|-------------------|
| **To Do** | `open` | Start Now (dispatch), Park, Delete |
| **In Progress** | `in_progress` | Complete, Block, Review, Park |
| **Review** | `needs_review` | Complete, Reopen, Park |
| **Done** | `completed` | Reopen |

### 6.2 Action → API Mapping

| UI Action | API Call | Backend Function |
|-----------|----------|------------------|
| "Start Now" | `POST /dispatch/immediate/{id}` | `dispatch_immediate()` |
| "Complete" | `POST /items/{id}/action` body: `{action: "complete"}` | `perform_task_action()` |
| "Park" | `POST /items/{id}/action` body: `{action: "park"}` | `perform_task_action()` |
| "Block" | `POST /items/{id}/action` body: `{action: "block"}` | `perform_task_action()` |
| "Review" | `POST /items/{id}/action` body: `{action: "review"}` | `perform_task_action()` |
| "Reopen" | `POST /items/{id}/action` body: `{action: "reopen"}` | `perform_task_action()` |
| "Quick Add" | `POST /items` body: `{title, priority, ...}` | `upsert_item()` |

---

## 7. Priority Display System

Tasks are visually coded by priority:

| Priority | Label | Color | Tailwind Class |
|----------|-------|-------|----------------|
| P0 | Critical | Red | `text-kcd-error`, `border-kcd-error` |
| P1 | High | Amber | `text-kcd-warning`, `border-kcd-warning` |
| P2 | Medium | Blue | `text-kcd-accent`, `border-kcd-accent` |
| P3 | Low | Gray | `text-kcd-text-muted`, `border-kcd-border` |

---

## 8. Source Kind Pills

Visual indicators showing where a task originated:

| Source | Pill Style | Origin |
|--------|-----------|--------|
| `email` | Blue outline | Materialized via `EmailTaskBridge` |
| `heartbeat` | Green outline | Created during heartbeat cycle |
| `manual` | Gray outline | User-created via dashboard quick-add |
| `brainstorm` | Purple outline | Born from brainstorm refinement pipeline |
| `webhook` | Orange outline | Ingested via webhook handler |

---

## 9. Planned Features (Phase 6 Roadmap)

| Phase | Feature | Status |
|-------|---------|--------|
| **6a** | Tailwind CSS / `kcd-*` migration | ✅ Complete |
| **6b** | Quick-Add sticky input bar | 🔲 Planned |
| **6c** | Morning Report collapsible banner | 🔲 Planned |
| **6d** | Simplified Kanban + icon-only hover actions | 🔲 Planned |
| **6e** | Mobile responsive layout | 🔲 Planned |
| **6f** | Skeleton loading + micro-animations | 🔲 Planned |

---

## 10. Related Documentation

| Document | Scope |
|----------|-------|
| [Proactive Pipeline](Proactive_Pipeline.md) | End-to-end autonomous task execution — ingress, scoring, dispatch, refinement, decomposition |
| [Heartbeat Service](Heartbeat_Service.md) | Cycle mechanics, task claim integration |
| [Memory System](Memory_System.md) | Tiered memory architecture used by proactive agents |
