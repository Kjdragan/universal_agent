# CODIE Redesign: Implementation Roadmap

**Created:** 2026-03-07  
**Status:** Planning — No implementation yet  
**Depends on:** `01_Architecture_Discussion.md`

---

## Phase 1: ClaudeCodeCLIClient (The Bridge)

**Goal:** Build the subprocess bridge that launches and monitors Claude Code CLI sessions.

**Priority:** High — this is the foundation everything else depends on.

### 1.1 New File: `src/universal_agent/vp/clients/claude_cli_client.py`

The core client that:
- Spawns `claude --print --output-format stream-json` as an async subprocess
- Sets working directory to mission workspace
- Configures environment variables:
  - `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
  - `CURRENT_SESSION_WORKSPACE` (mission workspace)
  - `ANTHROPIC_API_KEY` (inherited or explicit)
- Reads the JSON output stream line-by-line
- Detects and handles:
  - **Completion**: CLI exits with code 0 → extract final result text
  - **Error**: CLI exits non-zero → capture stderr, report failure
  - **Input request**: CLI pauses for user input → VP worker provides response
  - **Timeout**: configurable per-mission, default 30 minutes
- Returns `MissionOutcome` with result_ref pointing to workspace artifacts

### 1.2 JSON Stream Protocol

Claude Code CLI with `--output-format stream-json` emits newline-delimited JSON:

```json
{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "..."}]}}
{"type": "tool_use", "tool": {"name": "Read", "input": {"file_path": "..."}}}
{"type": "tool_result", "tool": {"name": "Read", "output": "..."}}
{"type": "result", "result": "final output text", "cost": {...}, "duration_ms": 12345}
```

The client parses this stream to:
- Track progress (tool calls indicate work happening)
- Detect stalls (no output for configurable timeout)
- Capture the final `result` message
- Extract cost/token metrics

### 1.3 Input Request Handling

When Claude Code CLI needs "user input", it pauses and waits on stdin. The VP worker detects this by:
- Monitoring for a `{"type": "input_request", ...}` event in the stream
- Or detecting that the subprocess is waiting (no output, process still alive)

The VP worker then:
1. Evaluates the context (what did the CLI ask?)
2. Generates an appropriate response based on the mission objective
3. Writes the response to the subprocess's stdin
4. Resumes monitoring

For Phase 1, input handling can be simple: if the CLI asks for input, provide a generic "proceed with the current approach" response. More sophisticated handling in Phase 2.

### 1.4 Integration with VpWorkerLoop

Modify `worker_loop.py` to select the client based on mission metadata:

```python
def _get_client(self, mission: dict) -> VpClient:
    payload = json.loads(mission.get("payload_json", "{}") or "{}")
    execution_mode = payload.get("execution_mode", "sdk")
    
    if execution_mode == "cli":
        from universal_agent.vp.clients.claude_cli_client import ClaudeCodeCLIClient
        return ClaudeCodeCLIClient()
    
    # Existing SDK clients
    if self.vp_id.startswith("vp.coder"):
        return ClaudeCodeClient()
    return ClaudeGeneralistClient()
```

### 1.5 Verification

- Unit test: spawn `claude --print` with a simple prompt, verify JSON stream parsing
- Integration test: dispatch a mission with `execution_mode: "cli"`, verify result flows back
- Verify `claude` CLI is installed and accessible on VPS: `which claude`

### 1.6 Prerequisites Check

Before Phase 1 can begin:
- [ ] Verify `claude` CLI is installed on VPS
- [ ] Verify `ANTHROPIC_API_KEY` is available to the worker process
- [ ] Verify `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` can be set per-subprocess
- [ ] Test `claude --print --output-format stream-json` manually on VPS

---

## Phase 2: CODIE Upgrade

**Goal:** CODIE can direct Claude Code CLI sessions for coding projects.

**Priority:** High — first real use case.

### 2.1 Mission Dispatch Enhancement

Add `execution_mode` field to the VP dispatch API:

```python
# gateway_server.py - dispatch endpoint
class VpMissionDispatchRequest(BaseModel):
    vp_id: str
    objective: str
    execution_mode: str = "sdk"  # "sdk" or "cli"
    # ... existing fields
```

### 2.2 CODIE Prompt Engineering

When CODIE directs a CLI session, it needs to craft a well-structured prompt. This is different from just passing the raw mission objective — CODIE adds context:

```
You are working on a coding project. Here is your objective:

{mission.objective}

Workspace: {workspace_dir}
Constraints: {mission.constraints}

Work autonomously. Create all necessary files, install dependencies,
and verify the implementation works. Report your final result including:
- Files created/modified
- Tests run and their results
- Any issues encountered
```

### 2.3 Result Evaluation

After CLI completes, CODIE evaluates:
- Did the CLI exit cleanly (code 0)?
- Are expected artifacts present in the workspace?
- Did the CLI report success or failure in its final output?
- Should CODIE retry with adjusted instructions?

### 2.4 Retry Logic

If the CLI fails or produces incomplete results:
1. CODIE reads the error output
2. Crafts a follow-up prompt addressing the specific failure
3. Launches a new CLI session (or resumes if supported)
4. Maximum 2 retries before marking mission failed

---

## Phase 3: VP General CLI Support

**Goal:** VP General can use Claude Code CLI for non-coding tasks (report pipelines, complex research).

**Priority:** Medium — extends the bridge to the second VP lane.

### 3.1 VP General Profile Update

Add `cli_capable: true` to the VP General profile:

```python
# vp/profiles.py
VP_PROFILES = {
    "vp.coder.primary": VpProfile(
        display_name="CODIE",
        cli_capable=True,
        # ...
    ),
    "vp.general.primary": VpProfile(
        display_name="General VP",
        cli_capable=True,  # NEW
        # ...
    ),
}
```

### 3.2 Skill Invocation via CLI

For the modular-research-report-expert use case:

```
You have access to the modular-research-report-expert skill.

Invoke it with:
  Skill(skill='modular-research-report-expert', args='{corpus_path}')

The skill will:
1. Create an Agent Team with 6 specialized teammates
2. Run a 6-phase pipeline: outline → source mining → drafting → critique → revision → assembly
3. Produce report.html and report.pdf in the report output directory

Corpus location: {corpus_path}
Output directory: {workspace_dir}/report-output/

After the skill completes, verify the outputs exist and report the paths.
```

### 3.3 Routing Logic

Simone's dispatch decision tree:

```
User wants a report from research corpus:
  → Is the request for "agent team" / "detailed pipeline" / "modular report"?
    → Yes: dispatch to VP General with execution_mode="cli"
    → No: dispatch to report-writer subagent with SDK (existing path)

User wants a coding project:
  → dispatch to CODIE with execution_mode="cli"

User wants quick research summary:
  → dispatch to research-specialist subagent with SDK (existing path)
```

---

## Phase 4: Concurrency Management

**Goal:** Manage Claude Code CLI session slots within the ZAI coding plan budget.

**Priority:** Medium — needed for production stability, not for initial testing.

### 4.1 Session Slot Tracker

New module: `src/universal_agent/session_budget.py`

Tracks active sessions across all consumers:
- Simone (gateway interactive sessions)
- CSI Analytics (timer-driven)
- VP workers (SDK mode)
- CLI sessions (including Agent Team teammates)

### 4.2 Heavy Mission Mode

Gateway endpoint to request/release heavy mission allocation:

```
POST /api/v1/ops/heavy-mission/request
  → pauses CSI analytics timers
  → returns available_slots count

POST /api/v1/ops/heavy-mission/release
  → resumes CSI analytics timers
```

VP worker calls this before launching CLI with Agent Teams.

### 4.3 Dynamic MAX_CONCURRENT_AGENTS

Set per-subprocess based on available budget:

```python
available = session_budget.available_slots()
max_agents = max(1, available - 1)  # Reserve 1 for the VP worker itself
env["REPORT_MAX_CONCURRENT_AGENTS"] = str(max_agents)
```

### 4.4 Dashboard Integration

Corporation View / VP panel shows:
- Current session budget usage (X/5 slots active)
- CLI sessions running (with teammate count)
- Heavy mission mode indicator

---

## Phase Summary

| Phase | Deliverable | Effort | Dependencies |
|-------|------------|--------|-------------|
| **1** | `ClaudeCodeCLIClient` + worker loop integration | 2-3 days | `claude` CLI on VPS |
| **2** | CODIE upgrade with prompt engineering + retry | 1-2 days | Phase 1 |
| **3** | VP General CLI access + skill invocation | 1 day | Phase 1 |
| **4** | Session budget management + heavy mission mode | 2 days | Phase 1 |

### Recommended Order

1. **Phase 1** first — the bridge is the foundation
2. **Phase 3** second — report pipeline is the most requested use case
3. **Phase 2** third — CODIE coding upgrade
4. **Phase 4** last — concurrency management for production stability

Phase 3 before Phase 2 because the report pipeline (VP General + CLI) is the immediate need that motivated this entire redesign. CODIE's coding upgrade is valuable but less urgent.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| `claude` CLI not installed on VPS | Low | Blocks all phases | Verify in Phase 1 prerequisites |
| Rate limiting from Agent Teams | High | Degraded quality | Phase 4 concurrency management |
| CLI subprocess hangs | Medium | VP worker stuck | Timeout + kill + retry logic |
| CLI output format changes | Low | Stream parsing breaks | Version-pin CLI, defensive parsing |
| Token cost explosion | Medium | Budget overrun | Phase 4 budget tracking + alerts |
| CLI and SDK workspace conflicts | Low | File corruption | Separate workspace directories |

---

## Success Criteria

Phase 1 is complete when:
- [ ] A VP worker can dispatch a mission with `execution_mode: "cli"`
- [ ] The CLI subprocess runs, produces output, and exits
- [ ] The VP worker captures the result and writes it to the mission DB
- [ ] Simone can retrieve the result via the existing VP mission API

The full redesign is complete when:
- [ ] The modular-research-report-expert skill runs successfully via CODIE or VP General
- [ ] A coding project can be dispatched to CODIE and executed via Claude Code CLI
- [ ] Session budget management prevents rate limit violations
- [ ] Dashboard shows CLI session status and cost metrics
