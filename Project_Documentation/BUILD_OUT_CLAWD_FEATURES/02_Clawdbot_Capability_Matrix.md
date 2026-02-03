# Next Phase from MVP — Clawdbot Capability Matrix (02)

## Purpose
Compare updated Clawdbot/OpenClaw capabilities against Universal Agent (UA), identify gaps, and recommend next steps. Each row includes a recommendation and whether it is **improvable with existing capacity** or a **new integration**.

## Capability Matrix

| Capability | Clawdbot/OpenClaw Snapshot | UA Current State | Recommendation | Capacity Type |
| --- | --- | --- | --- | --- |
| Gateway control plane + Control UI | Gateway is single control plane; Control UI talks directly to Gateway WS; manage channels, skills, approvals, logs. | UA has Gateway + Web UI, but not a unified Control UI for ops-level management. | Add a lightweight ops/control UI surface for gateway (status, channels, logs, approvals, skills). | New integration (UI + gateway ops endpoints) |
| Multi-channel routing | Built-in channels: WhatsApp/Telegram/Discord/Slack + plugins. | UA has Telegram + Web UI; other channels via Composio or MCP. | Prioritize stable Telegram parity first; then add one “wow” channel (e.g., WhatsApp or Slack) with unified session mapping. | New integration (channel adapter + policies) |
| File-based memory + index | Markdown memory files + SQLite vector/FTS index; auto memory flush before compaction. | UA uses Letta memory + local tools; not file-native memory with indexing. | Add Clawdbot-style file memory (MEMORY.md + memory/YYYY-MM-DD.md) with a small indexer. | New integration (memory subsystem) |
| Memory flush on compaction | Auto trigger writes when near compaction; silent by default. | UA has context management but no explicit pre-compaction memory flush. | Implement a pre-compaction memory flush hook to persist durable notes. | Improve existing capacity (context manager + hooks) |
| Heartbeat system | Heartbeats keep cache warm, can run checks, silent OK, with delivery rules. | UA has early heartbeat concepts, not fully wired. | Implement heartbeat loop as a gateway-managed scheduled task; enable per-session heartbeat directives (HEARTBEAT.md). | New integration (scheduler + policies) |
| Cron/scheduler | Cron can trigger agent runs and heartbeats; integrated with delivery rules. | UA has URW harness but no general cron. | Add a lightweight scheduler for proactive tasks; start with a single “daily digest” lane. | New integration (scheduler) |
| Tool policy + sandboxing | Per-agent tool allow/deny + sandboxed tool execution. | UA has hooks/guardrails but not sandboxed execution. | Extend tool policy reporting and optionally sandbox for risky tools. | New integration (sandbox + policy engine) |
| Session routing + last-channel | Sessions keyed by channel/user and governed by routing rules. | UA session mapping exists in gateway; Telegram uses tg_<id> mapping. | Consolidate session routing rules across Web UI and Telegram; add “last channel” semantics. | Improve existing capacity (gateway + bot adapter) |
| Control-plane auth | Gateway auth + tokens + Tailscale. | UA has gateway but less explicit control-plane auth. | Add explicit gateway auth/token support for multi-user deployments. | Improve existing capacity |
| Skills discovery + tooling | Control UI can list/enabled/disable skills; tool schema policy. | UA has skills list + injected knowledge, but no UI to manage. | Expose skills list/enablement in the gateway UI. | Improve existing capacity |
| Observability | Control UI can stream logs; heartbeat logs are explicit. | UA has Logfire + run logs; activity panel. | Add lightweight log tail in UI and a “gateway ops” page. | Improve existing capacity |

## Recommendations (Prioritized)

1. **Telegram parity first (complete Option C)**
   - Ensures single engine + shared tooling across surfaces.
   - Capacity: improve existing architecture.

2. **File-based memory + memory flush**
   - Adds durable recall and user trust.
   - Capacity: partial new integration (memory indexer) + improve existing hooks.

3. **Heartbeat + scheduled checks**
   - Enables proactive behavior (wow factor).
   - Capacity: new integration (scheduler + heartbeat policy).

4. **Gateway ops/control UI**
   - Improves multi-user management and debugging.
   - Capacity: new UI + gateway endpoints.

5. **Add one new channel**
   - After Telegram parity, choose one channel (WhatsApp or Slack) to showcase multi-channel routing.
   - Capacity: new integration.

## Notes
- The quickest “wow” boost is **proactive heartbeats** plus **file-based memory**.
- The lowest risk lift is **Telegram parity** (already underway) plus **control UI improvements**.
- Memory + heartbeat changes should be staged to avoid unintended proactive behavior.
