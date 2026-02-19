# Universal Agent Documentation

Welcome to the official technical documentation for the **Universal Agent**. This documentation is designed for junior developers to quickly understand the project's architecture, core logic, and operational workflows.

## 📚 Table of Contents

### 1. [Architecture](01_Architecture)

- **[System Overview](01_Architecture/System_Overview.md)**: High-level component map.
- **[Core Classes](01_Architecture/Core_Classes.md)**: `UniversalAgent`, `HeartbeatService`, and `Gateway`.
- **[Soul Architecture](01_Architecture/Soul_Architecture.md)**: Identity and persona injection.
- **[UI Architecture](01_Architecture/UI_Architecture.md)**: Next.js, Zustand, and WebSocket flows.

### 2. [Subsystems](02_Subsystems)

- **[Memory System](02_Subsystems/Memory_System.md)**: Tiered memory & Auto-Flush.
- **[Heartbeat Service](02_Subsystems/Heartbeat_Service.md)**: Autonomic cycle.
- **[Durable Execution](02_Subsystems/Durable_Execution.md)**: Resilience features.
- **[URW Orchestration](02_Subsystems/URW_Orchestration.md)**: Multi-phase tasks.

### 3. [Flows](02_Flows)

- **[Event Streaming](02_Flows/Event_Streaming_Flow.md)**: Turn lifecycle.
- **[Resource Guardrails](02_Flows/Resource_Guardrails.md)**: Workspace security.
- **[Chat Panel Communication Layer](02_Flows/04_Chat_Panel_Communication_Layer.md)**: Full pipeline from SDK to chat UI — deduplication, sub-agent attribution, stream coalescing, rendering.
- **[Activity Log Communication Layer](02_Flows/05_Activity_Log_Communication_Layer.md)**: Full pipeline for operational observability — tool calls, hook activity, stdout capture, MCP detail.

### 4. [Operations](03_Operations)

- **[Configuration Guide](03_Operations/Configuration_Guide.md)**: Env vars & flags.
- **[Running the Agent](03_Operations/46_Running_The_Agent.md)**: CLI, Web, Telegram.
- **[Skill Development](03_Operations/Skill_Development.md)**: Developing tools.
- **[Testing Strategy](03_Operations/Testing_Strategy.md)**: QA and CI.
- **[OpenCLAW Release Parity Assessment (2026-02-06)](03_Operations/03_OpenCLAW_Release_Parity_Assessment_2026-02-06.md)**: Security and feature gap triage against recent OpenCLAW releases.
- **[VPS Host Security Hardening Runbook (2026-02-12)](03_Operations/26_VPS_Host_Security_Hardening_Runbook_2026-02-12.md)**: Solo-dev-safe VPS hardening steps with validation and rollback.
- **[Bowser Integration: Strategic Capability Expansion (2026-02-16)](03_Operations/40_Bowser_Integration_Strategic_Capability_Expansion_2026-02-16.md)**: How Bowser's layered browser automation stack expands UA from report-centric flows into browser-native execution, validation, and orchestration.
- **[Todoist Heartbeat and Triage Operational Runbook (2026-02-16)](03_Operations/41_Todoist_Heartbeat_And_Triage_Operational_Runbook_2026-02-16.md)**: Daily operating cadence for Todoist-backed heartbeat inputs, manual brainstorming triage, and guarded verification checks.
- **[Hybrid Local+VPS YouTube Webhook Operations Source of Truth (2026-02-18)](03_Operations/42_Hybrid_Local_VPS_Webhook_Operations_Source_Of_Truth_2026-02-18.md)**: Canonical runbook for hybrid ingress architecture, readiness checks, failure signatures, and recovery procedures.
- **[VPS WebUI Long-Running Query Evaluation (2026-02-18)](03_Operations/43_VPS_WebUI_Long_Running_Query_Evaluation_2026-02-18.md)**: End-to-end execution evidence, artifact inventory, transcript/log analysis, and reliability findings for a production-style long-running mission.

### 5. [API Reference](04_API_Reference)

- **[Gateway Ops API](04_API_Reference/Ops_API.md)**: Session and log management endpoints.

### 6. [Archive](05_Archive)

- **[Decisions](05_Archive/Decisions)**: Critical architectural decision records (ADRs).
- **[Glossary.md](Glossary.md)**: Project terminology.

---

## 🚀 Recommended Path for New Developers

1. Start with the **[System Overview](01_Architecture/System_Overview.md)** to understand the big picture.
2. Read the **[Core Classes](01_Architecture/Core_Classes.md)** to see how the main objects interact.
3. Check the **[Glossary](Glossary.md)** whenever you encounter a project-specific term.
4. Try running the agent in CLI mode using the **[Running the Agent](03_Operations/46_Running_The_Agent.md)** guide.
5. Explore **[Skill Development](03_Operations/Skill_Development.md)** to see how tools are integrated.
