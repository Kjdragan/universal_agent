# Next Phase from MVP — Conversation Summary (01)

## Context
We reached gateway/CLI parity and stabilized in‑process tooling. The focus now shifts to “wow factor” capabilities and product expansion beyond core research/report workflows.

## Themes Raised
- **Capability expansion**: add new processes/skills to make the agent feel more proactive and impressive.
- **Messaging surfaces**: evaluate/expand gateway integrations beyond web UI (e.g., Telegram bot, WhatsApp, potentially Threads/Twitter visibility).
- **Memory system**: review how memory is currently wired (Letta + local memory tooling), ensure it’s actually being used, and confirm the injection strategy.
- **Gateway leverage**: exploit multi‑user and multi‑surface access without losing reliability or observability.
- **“Heartbeat” / proactive mode**: determine whether heartbeat exists, whether it is wired, and how to enable agent‑initiated behaviors.
- **Soul / personality assets**: confirm if the SOUL prompt assets are active and if they can be evolved for proactive engagement.
- **Clawdbot reference**: compare against improved OpenClawd implementation in `/home/kjdragan/lrepos/clawdbot` for ideas.

## Immediate Focus Suggested
1. **Telegram bot audit**
   - Verify it’s working end‑to‑end.
   - Compare its tool availability, permissions, and in‑process tool wiring to the main gateway.
   - Identify any performance or UX gaps (latency, formatting, attachment handling).

2. **Memory system validation**
   - Confirm what is actually persisted and injected between turns.
   - Validate whether follow‑up requests are context‑aware and consistent across surfaces.

3. **Proactive/heartbeat capability**
   - Assess if the heartbeat system is wired and where.
   - Decide what proactive behaviors make sense (alerts, scheduled digests, monitoring tasks).

4. **Integration opportunities**
   - Evaluate feasibility of WhatsApp/Threads/Twitter and constraints (auth, TOS, API access).
   - Consider a unified “communication router” with per‑surface guardrails.

## Notes
- No code changes requested at this stage; this is a capture of the brainstorming direction.
- This directory is intended to record exploration and decision-making for the post‑MVP phase.
