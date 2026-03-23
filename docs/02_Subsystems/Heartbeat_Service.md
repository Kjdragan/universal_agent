# Heartbeat Service

The **Heartbeat Service** is the "autonomic nervous system" of the Universal Agent. It allows the agent to function without direct user interaction.

> [!IMPORTANT]
> **This document is the single source of truth** for the UA Heartbeat Service subsystem.
> Operational runbooks and incident notes may reference heartbeat concepts, but this file
> defines the authoritative architecture, data contracts, and implementation map.

## 1. Purpose

Most chatbots are passive—they only speak when spoken to. The Heartbeat Service changes this by:

- **Periodic Wakefulness**: Prompting the agent to re-evaluate its state every few minutes.
- **Monitoring Background Tasks**: Checking if long-running operations (like research or compilation) are finished.
- **Time Sensitivity**: Informing the agent of the current time, approaching deadlines, or scheduled events.

## 2. Process Heartbeat vs. UA Heartbeat Service

These are **two completely separate modules** with overlapping names:

| Dimension | Process Heartbeat (`process_heartbeat.py`) | UA Heartbeat Service (`heartbeat_service.py`) |
| --- | --- | --- |
| Purpose | OS-level liveness signal | Application-level proactive agent scheduler |
| Mechanism | Daemon thread writes timestamp file every 10s | Async task runs agent every ~30 min |
| Event-loop dependency | Independent (runs in OS thread) | Runs ON the asyncio event loop |
| Consumer | `vps_service_watchdog.sh` | Gateway mediation pipeline, Simone auto-triage |
| Env prefix | `UA_PROCESS_HEARTBEAT_*` | `UA_HEARTBEAT_*` / `UA_HB_*` |

## 3. Heartbeat Cycle

```mermaid
graph TD
    Start((Timer Start)) --> Wait[Wait Interval e.g. 300s]
    Wait --> Check{Active Hours?}
    Check -- No --> Wait
    Check -- Yes --> Trigger[Collect System Events]
    Trigger --> Inject[Inject Prompt to Agent]
    Inject --> Stream[Agent Thinking Turn]
    Stream --> Final{OK Token?}
    Final -- Yes --> Stop((Cycle End))
    Final -- No --> Action[Perform Autonomous Action]
    Action --> WriteArtifacts[Write Findings JSON + Health Report]
    WriteArtifacts --> PostValidate[Post-Write JSON Validation & Repair]
    PostValidate --> Stop
```

## 4. Configuration & Scheduling

The heartbeat is highly configurable via environment variables.

### Primary Settings

| Environment Variable | Default | Description |
| --- | --- | --- |
| `UA_HEARTBEAT_INTERVAL` | 1800 (30 min) | Primary interval between heartbeat runs (in seconds). Also accepts legacy `UA_HEARTBEAT_EVERY`. |
| `UA_HEARTBEAT_MIN_INTERVAL_SECONDS` | Dynamic | Minimum allowed interval; resolved after Infisical bootstrap. |
| `UA_HEARTBEAT_ACTIVE_START` | None | Start of active hours window (e.g., "08:00"). Heartbeat skips runs outside this window. |
| `UA_HEARTBEAT_ACTIVE_END` | None | End of active hours window (e.g., "20:00"). |
| `UA_HEARTBEAT_EXEC_TIMEOUT` | 1600 | Maximum execution time for a single heartbeat turn (in seconds). |
| `UA_HEARTBEAT_AUTONOMOUS_ENABLED` | 1 | Set to "0" to disable autonomous heartbeat actions entirely. |

### Retry & Continuation

| Environment Variable | Default | Description |
| --- | --- | --- |
| `UA_HEARTBEAT_RETRY_BASE_SECONDS` | 10 | Base delay for exponential backoff retries. |
| `UA_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS` | 3600 | Maximum retry backoff delay. |
| `UA_HEARTBEAT_CONTINUATION_DELAY_SECONDS` | 1 | Short delay after actionable runs for quick re-check. |
| `UA_HEARTBEAT_FOREGROUND_COOLDOWN_SECONDS` | 1800 | Cooldown after foreground (user) activity before heartbeat resumes. |

### Limits & Tuning

| Environment Variable | Default | Description |
| --- | --- | --- |
| `UA_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE` | 1 | Maximum proactive items to process per heartbeat cycle. |
| `UA_HEARTBEAT_MAX_ACTIONABLE` | 50 | Maximum actionable items to surface in a single run. |
| `UA_HEARTBEAT_MAX_SYSTEM_EVENTS` | 25 | Maximum system events to include per heartbeat. |
| `UA_HEARTBEAT_INVESTIGATION_ONLY` | None | If set, heartbeat runs in investigation-only mode (no mutations). |

### OK Tokens

The agent can emit special strings (like `HEARTBEAT_OK` or `UA_HEARTBEAT_OK`) to indicate it has nothing to do, ending the turn cleanly. These are stripped and matched by `_strip_heartbeat_tokens()` which also detects "no-op checklist" language to avoid accidental leakage.

### Retry Queue and Continuation Passes

Heartbeat scheduling includes a persisted retry queue in `heartbeat_state.json`.

- **Busy or foreground-locked runs** schedule a retry with exponential backoff:
  - `delay = min(base_retry_seconds * 2^(attempt - 1), max_retry_backoff_seconds)`
- **Failure-driven retries** use the same exponential backoff pattern.
- **Successful actionable runs** schedule a short continuation re-check (default 1 second).
- Retry metadata is persisted with: `retry_kind`, `retry_attempt`, `retry_reason`, `next_retry_at`, `last_retry_delay_seconds`.

## 5. Visibility (Stealth Mode)

The agent can perform "stealth heartbeats" where its thoughts are logged internally but not displayed to the user. Heartbeat execution broadcasts agent events onto the gateway session stream when a UI is connected.

## 6. Heartbeat Findings Contract

### Artifacts Written

Each heartbeat cycle that produces non-OK findings writes two artifacts:

| Artifact | Format | Purpose |
| --- | --- | --- |
| `work_products/system_health_latest.md` | Markdown | Human-readable health report |
| `work_products/heartbeat_findings_latest.json` | JSON | Machine-readable findings for gateway mediation |

### JSON Schema

The findings JSON must follow this schema (defined in `memory/HEARTBEAT.md` and enforced by `HeartbeatFindings` Pydantic model):

```json
{
  "version": 1,
  "overall_status": "ok|warn|critical",
  "generated_at_utc": "ISO-8601 UTC timestamp",
  "source": "heartbeat",
  "summary": "Short one-paragraph summary.",
  "findings": [
    {
      "finding_id": "stable_snake_case_id",
      "category": "gateway|system|disk|memory|cpu|dispatch|database|unknown",
      "severity": "ok|warn|critical",
      "metric_key": "metric_name",
      "observed_value": "<any>",
      "threshold_text": ">50",
      "known_rule_match": true,
      "confidence": "low|medium|high",
      "title": "Human-readable title",
      "recommendation": "Actionable recommendation.",
      "runbook_command": "shell command for diagnosis",
      "metadata": {}
    }
  ]
}
```

### JSON Repair Pipeline

The LLM agent writes findings JSON via the Write tool, which is inherently fragile (missing commas, trailing commas, Python literals like `True`/`None`). A three-layer defense ensures reliable parsing:

```mermaid
graph LR
    Agent[LLM Writes JSON via Tool] --> PostWrite[Post-Write Validation]
    PostWrite --> Repair1["extract_json_payload() + HeartbeatFindings model"]
    Repair1 --> Rewrite["json.dumps() re-serialization"]
    Rewrite --> GatewayRead[Gateway Reads Artifact]
    GatewayRead --> Repair2["extract_json_payload() + HeartbeatFindings model"]
    Repair2 --> Mediation[Classification & Mediation]
```

**Layer 1: Post-write validation (`heartbeat_service.py`)**
After the agent finishes its turn and writes the findings file, the service reads it back, runs `extract_json_payload()` with the `HeartbeatFindings` Pydantic model, and re-serializes with `json.dumps()`. This catches and repairs malformed JSON before it ever reaches the gateway.

**Layer 2: Gateway repair (`gateway_server.py`)**
When the gateway reads the findings artifact in `_heartbeat_findings_from_artifacts()`, it uses the same `extract_json_payload()` + `HeartbeatFindings` pipeline instead of bare `json.loads()`. This handles edge cases where post-write validation was skipped or failed.

**Layer 3: Synthetic fallback (`heartbeat_service.py`)**
If the agent doesn't write findings at all (common for Task Hub dispatch, exec completions, etc.), the service generates synthetic findings using `json.dumps()` — always valid JSON.

The repair pipeline uses:
- `json_repair` library: fixes missing commas, trailing commas, unquoted keys, Python `True`/`False`/`None` literals
- `HeartbeatFindings` Pydantic model: validates schema and fills missing fields with permissive defaults
- `json.dumps()`: deterministic re-serialization guarantees valid JSON

### Missing vs. Corrupt Artifact Distinction

The gateway distinguishes between these cases since v2026-03-19:

- **Missing artifact**: Normal for many run types (Task Hub, exec completions). No `heartbeat_findings_parse_failed` notification emitted. Synthetic findings are used.
- **Corrupt artifact**: The artifact exists but can't be parsed even after repair. Emits `heartbeat_findings_parse_failed` notification with the parse error. Fallback findings are used.

## 7. Non-OK Heartbeat Mediation

Non-OK heartbeats are treated as operational investigations:

1. Heartbeat detects the issue
2. Gateway classifies findings (known-rule vs unknown, severity)
3. Gateway adds `autonomous_heartbeat_completed` notification with `requires_action=true`
4. Simone is automatically dispatched for investigation
5. Simone writes investigation summary back into hook session workspace
6. If operator review needed, Kevin gets dashboard notification + AgentMail

> [!NOTE]
> This is deliberately auto-investigation, not auto-remediation.
> Simone cannot auto-edit code, auto-run shell commands, or auto-deploy in this flow.
> See [Heartbeat Issue Mediation and Auto-Triage](../03_Operations/95_Heartbeat_Issue_Mediation_And_Auto_Triage_2026-03-12.md) for the full mediation contract.

### Cooldown and Deduplication

Equivalent findings are suppressed from repeated Simone dispatch for a configurable window (default: `cooldown_minutes = 60`). The notification still appears; only duplicate dispatch is suppressed.

### No-op Suppression

Heartbeats with no meaningful activity (no writes, no work products, no elevated severity, no unknown rules) are suppressed from notification entirely via `_heartbeat_has_meaningful_activity()`.

## 8. Agent Instructions (`memory/HEARTBEAT.md`)

The `memory/HEARTBEAT.md` file is the **agent-facing operating contract** — it tells the LLM what checks to run, what thresholds to use, what artifacts to write, and what schema to follow. It is NOT documentation; it is a prompt.

Key contents:
- Active monitors (VPS system health, local desktop health)
- Mission-focus items and execution windows
- The JSON findings schema (authoritative for the agent)
- Checkbox semantics: `[ ]` = active/pending, `[x]` = completed/disabled
- Kevin's working style preferences
- Response policy (concise summaries, no-op skipping)

## 9. Implementation Files

| File | Role |
| --- | --- |
| `src/universal_agent/heartbeat_service.py` | Main service: scheduling, execution, prompt composition, retry queue, post-write validation, synthetic fallback |
| `src/universal_agent/utils/heartbeat_findings_schema.py` | `HeartbeatFindings` + `HeartbeatFinding` Pydantic models with permissive defaults and normalizers |
| `src/universal_agent/utils/json_utils.py` | `extract_json_payload()`: 5-layer JSON repair (json.loads → json_repair → regex extraction → Pydantic validation) |
| `src/universal_agent/gateway_server.py` | `_heartbeat_findings_from_artifacts()`: reads + repairs + classifies findings; `_emit_heartbeat_event()`: mediation dispatch |
| `src/universal_agent/heartbeat_mediation.py` | `sanitize_heartbeat_recommendation_text()`: rewrite stale provider-specific language in mediation output |
| `src/universal_agent/process_heartbeat.py` | OS-level liveness writer (daemon thread, separate from this service) |
| `src/universal_agent/hooks_service.py` | Hook completion handling for Simone heartbeat investigations |
| `memory/HEARTBEAT.md` | Agent-facing operating instructions and active monitors |
| `src/universal_agent/main.py` | Bootstraps the service during agent initialization |

## 10. Related Documentation

| Document | Scope |
| --- | --- |
| [Heartbeat Issue Mediation and Auto-Triage (2026-03-12)](../03_Operations/95_Heartbeat_Issue_Mediation_And_Auto_Triage_2026-03-12.md) | Full mediation contract: notification model, Simone dispatch, operator escalation, UI badges |
| [Factory Delegation, Heartbeat, and Registry (2026-03-06)](../03_Operations/88_Factory_Delegation_Heartbeat_And_Registry_Source_Of_Truth_2026-03-06.md) | Redis transport, factory heartbeat (VP fleet registration), delegation context |
| [Heartbeat Debug Fixes (2026-02-05)](../03_Operations/01_Heartbeat_Debug_Fixes.md) | Historical: no-op strictness, text dedup, UI visibility fixes |
| [Todoist Heartbeat and Triage Runbook (2026-02-16)](../03_Operations/41_Todoist_Heartbeat_And_Triage_Operational_Runbook_2026-02-16.md) | Operational cadence for Todoist-backed heartbeat inputs |
