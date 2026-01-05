# Anthropic Autonomous Coding Harness - Deep Dive Research

**Date**: 2026-01-05  
**Source**: `anthropics/claude-quickstarts/autonomous-coding`

## Critical Discovery: NO COMPACTION

The Anthropic harness **does not use compaction** for cross-session continuity. Instead, it uses:

> "This is a FRESH context window - you have no memory of previous sessions."

Each session is a completely new agent instance with wiped memory. Continuity is 100% file-based.

---

## Architecture Overview

### Two-Agent Pattern

1. **Initializer Agent** (first session only)
   - Creates `feature_list.json` with 200+ features (all `passes: false`)
   - Creates `init.sh` for environment setup
   - Sets up git repo with initial commit
   - Prompt: `initializer_prompt.md`

2. **Coding Agent** (all subsequent sessions)
   - Reads state from files
   - Works on ONE feature per session
   - Updates `passes: true` when verified
   - Prompt: `coding_prompt.md`

### Session Loop (from `agent.py`)

```python
# Pseudocode of run_autonomous_agent
while not all_features_pass:
    # Create FRESH client each iteration
    client = ClaudeSDKClient(options)
    
    # Determine which prompt to use
    if not exists("feature_list.json"):
        prompt = initializer_prompt
    else:
        prompt = coding_prompt
    
    # Run session
    run_agent_session(client, prompt)
    
    # Check progress
    print_progress_summary()
```

Key insight: **New client every iteration**. Claude has no memory between sessions.

---

## The "Get Your Bearings" Protocol

At start of each coding session, agent MUST execute:

```bash
# 1. Understand location
pwd
ls -la

# 2. Read project spec
cat app_spec.txt

# 3. Read task list (CRITICAL)
cat feature_list.json | head -50

# 4. Read previous progress
cat claude-progress.txt

# 5. Check git history
git log --oneline -20

# 6. Count remaining work
cat feature_list.json | grep '"passes": false' | wc -l
```

This 30-second orientation replaces compaction entirely.

---

## File-Based State (The Core Mechanism)

### `feature_list.json` (Task List)

```json
[
  {
    "category": "functional",
    "description": "New chat button creates a fresh conversation",
    "steps": [
      "Navigate to main interface",
      "Click the 'New Chat' button",
      "Verify a new conversation is created",
      "Check that chat area shows welcome state",
      "Verify conversation appears in sidebar"
    ],
    "passes": false  // <-- ONLY field agent can modify
  }
]
```

Rules:
- Agent can ONLY change `passes: false` → `passes: true`
- Agent CANNOT remove, edit, combine, or reorder tests
- JSON format prevents accidental modification (more robust than markdown)

### `claude-progress.txt` (Narrative Log)

Written at END of each session:
```
Session completed: 2026-01-05 16:00

## Accomplishments
- Implemented new chat button feature (#45)
- Fixed sidebar rendering bug

## Tests Completed
- feature_list[45].passes = true

## Issues Discovered
- Some CSS styling needed for mobile

## Next Session Should
- Work on feature #46 (message input validation)

## Current Status
Passing: 45/200 (22.5%)
```

### `init.sh` (Environment Setup)

```bash
#!/bin/bash
npm install
npm run dev &
sleep 5
echo "Development server running"
```

Agent runs this at start of each session to restore environment.

---

## Session Termination Signals

Agent is instructed to END session cleanly when:
1. Feature is complete and verified
2. Context window getting full (agent detects this somehow)
3. Major blocker encountered

End-of-session checklist:
1. Commit all working code to git
2. Update `claude-progress.txt`
3. Ensure `feature_list.json` is saved
4. Leave app in working state (no broken builds)

---

## Implications for Universal Agent

### What Anthropic Gets Right

1. **No reliance on LLM memory** - Files are source of truth
2. **Explicit orientation step** - Agent knows exactly what to read first
3. **Single-field mutation** - Only `passes` changes, nothing else
4. **Clean session boundaries** - Agent ends before context exhausts

### What Universal Agent Needs

| Anthropic Pattern | UA Equivalent (Needed) |
|-------------------|------------------------|
| `feature_list.json` | `macro_tasks.json` or `task_progress.json` |
| `claude-progress.txt` | `session_progress.md` (narrative) |
| `init.sh` | Workspace restoration (already have session dirs) |
| Git history | Tool ledger history (already have) |
| Fresh ClaudeSDKClient | Need to implement session boundary restart |
| "Get Your Bearings" | Need to add to sub-agent prompts |

### Key Question

Does the Claude Agent SDK's **compaction** feature do something useful that we should also use?

From SDK docs:
> "Extended context windows (1M tokens) allow processing large documents and codebases in a single request"

So compaction may be a fallback WITHIN a session, but for cross-session, the answer is definitively: **fresh agents + file state**.

---

## Sub-Agent Handling

Anthropic's harness doesn't appear to use sub-agents (Task delegation) in the autonomous-coding example. It's a single-agent loop.

For Universal Agent:
- Sub-agents are ephemeral (no cross-turn memory)
- Sub-agent context CAN exhaust mid-task
- Options:
  1. Sub-agent writes progress file, gets re-delegated
  2. Sub-agent uses compaction internally
  3. Primary agent detects sub-agent failure, continues

This is an open question that requires experimentation.

---

## References

- DeepWiki: `anthropics/claude-quickstarts`
- Repository: https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding
- Blog: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
