# Context Management & Skill Replacement Strategy

## 1. Context Management Strategy

### Problem: Context Rot
Long-running agent sessions in the URW harness accumulate noise. While `claude-agent-sdk` manages context, it eventually hits token limits, forcing truncation or compaction.
- **Clawdbot Approach**: Uses `session.compact()` which intelligently summarizes history.
- **Our Constraint**: The `claude-agent-sdk` Python client (v0.0.20) isolates state in a subprocess. We cannot mutate the history of a generic *running* session directly.

### Solution: Harness-Managed "Micro-Pruning"
Instead of relying on the black-box SDK compaction, we should implement **Active Context Management** in the `UniversalAgent` class.

**Mechanism:**
1.  **Track History**: `UniversalAgent` already tracks messages in `self.history`.
2.  **Threshold Trigger**: When tokens > 70% of limit.
3.  **Micro-Pruning**:
    - Identify "closed" loops (Tool Call -> Result -> Success).
    - **Prune**: Replace the intermediate Tool/Result blocks with a one-line summary (e.g., "*Searched for X, found Y*").
    - **Keep**: User prompts and final answers.
4.  **Session Refresh** (The "Soft Restart"):
    - **Action**: `client.disconnect()`
    - **Re-init**: `client.connect(prompt=pruned_history)`
    - This forces the SDK to adopt our cleaned state without killing the entire OS process.

### Recommendation
Implement a `ContextManager` class in `universal_agent/memory/context_manager.py` that handles the pruning logic and integrates it into `UniversalAgent._run_conversation`.

## 2. Linux Skill Replacements

We need robust CLI-first alternatives for MacOS-specific skills.

### A. Reminders -> `Taskwarrior`
- **Tool**: `taskwarrior` (apt package `taskwarrior`).
- **Why**: Industry standard for CLI task management. Fast, local, text-based DB.
- **Implementation**:
    - Create `mcp_server_taskwarrior.py`.
    - Tools: `task_add`, `task_list`, `task_done`, `task_modify`.
    - **Action**: Implement new MCP server.

### B. Notes -> `Obsidian` (Flat Files)
- **Tool**: Existing `obsidian` skill (Markdown manipulation).
- **Why**: We already have it. It just needs to point to a valid Linux path.
- **Implementation**:
    - configuration: `OBSIDIAN_VAULT_PATH`.
    - **Action**: Verify `obsidian` skill supports generic file paths (it should) and set up a vault dir in `~/Documents/Obsidian`.

### C. Messaging -> `Telegram` (Revive) / `Signal-CLI`
- **Tool**: `Telegram` (Stale implementation exists).
- **Why**: Signal-CLI is complex to support (Java dependency, linking). Telegram is HTTP-based and easier to deploy via Railway later.
- **Implementation**:
    - Revive `mcp_server_telegram.py`.
    - **Action**: Prioritize Telegram over Signal for ease of integration.

### D. Calendar -> `khal` or `gcalcli`
- **Tool**: `gcalcli` (Google Calendar CLI).
- **Why**: Direct sync with Google Calendar (which user likely uses). `khal` is local-only (vdirsyncer).
- **Implementation**:
    - **Action**: Evaluate `gcalcli` for calendar integration.

## 3. Plan of Action

1.  **Approve Strategy**: Confirm this direction.
2.  **Context**: Implement `ContextManager` prototype.
3.  **Skills**:
    - Install `taskwarrior`.
    - Create `Taskwarrior` MCP.
    - Config `Obsidian` path.
