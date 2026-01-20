# Harness Data Model V2

**Status:** Implementation Complete
**Date:** January 6, 2026

## 1. Overview

This document defines the data structures used by the Harness V2 system to manage long-running "Massive Tasks" through the Universal Agent.

### Task Hierarchy

| Layer | Granularity | Owner | Example |
|-------|-------------|-------|---------|
| User Request | Vague/High-Level | User | "Create a geopolitical report on Latin America" |
| **Feature** | Well-Defined Chunk | `mission.json` | "Research Venezuela political crisis (Jan 2026)" |
| Atomic Steps | Tool Calls | Composio Search Tools | `[Search, Filter, WriteFile]` |

> **Important:** The Harness tracks *Features*. The Multi-Agent System decomposes *Features* into *Atomic Steps*.

---

## 2. Mission Manifest (`mission.json`)

**Location:** `{workspace}/mission.json`

### Schema

```json
{
  "mission_id": "uuid",
  "mission_root": "High-level objective description",
  "status": "PLANNING | IN_PROGRESS | COMPLETE",
  "clarifications": [
    {"question": "What date range?", "answer": "Jan 1-6, 2026"}
  ],
  "tasks": [
    {
      "id": "1",
      "description": "Research Venezuela political crisis",
      "context": {
        "date_range": "Jan 1-6, 2026",
        "focus_areas": ["Maduro status", "US policy reactions"],
        "prior_artifacts": []
      },
      "use_case": "research | report | analysis | other",
      "success_criteria": "A markdown file exists at output path with > 500 words",
      "output_artifacts": ["tasks/01/research_overview.md"],
      "status": "PENDING | IN_PROGRESS | COMPLETE"
    }
  ]
}
```

### Status Values

| Status | Meaning |
|--------|---------|
| `PLANNING` | Agent is decomposing task; Approval Gate pending |
| `IN_PROGRESS` | User approved; execution in progress |
| `COMPLETE` | All tasks marked complete; mission finished |

---

## 3. Progress Notes (`mission_progress.txt`)

**Location:** `{workspace}/mission_progress.txt`

Free-form text for the agent to leave notes for its future self (or next iteration). Useful for context that doesn't fit the structured JSON.

---

## 4. Interview Tool Schema

Used during Planning Phase to gather clarifications from the user.

```json
{
  "questions": [
    {
      "question": "What date range should the research cover?",
      "header": "Date Range",
      "options": [
        {"label": "Last 7 days", "description": "Most recent news"},
        {"label": "Last 30 days", "description": "Broader context"}
      ],
      "multiSelect": false
    }
  ]
}
```

**Answers:** Stored in `mission.json` under `clarifications`.

---

## 5. Harness Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│                        PLANNING PHASE                           │
├─────────────────────────────────────────────────────────────────┤
│  1. [THINK]       Agent analyzes Massive Task                   │
│  2. [GENERATE]    Agent creates mission.json (status=PLANNING)  │
│  3. [INTERVIEW]   If ambiguous, ask user (CLI)                  │
│  4. [PRESENT]     Show Plan Summary to user                     │
│  5. [GATE]        User approves / requests changes              │
│  6. [TRANSITION]  Set status = IN_PROGRESS                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                       EXECUTION PHASE                           │
├─────────────────────────────────────────────────────────────────┤
│  For each Feature in mission.json:                              │
│    1. Multi-Agent System decomposes into Atomic Steps           │
│    2. Execute Steps, update progress                            │
│    3. Mark Feature as COMPLETE                                  │
│    4. If context limit reached → Harness Restart                │
│  When all Features COMPLETE → Set mission status = COMPLETE     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Key Files

| File | Purpose |
|------|---------|
| `src/universal_agent/harness/__init__.py` | Harness module entry |
| `src/universal_agent/harness/interview_tool.py` | Interview & Approval Gate |
| `src/universal_agent/main.py` | Harness loop integration |
| `scripts/create_mission.py` | Helper to generate mission.json |
