# Universal Agent Documentation

Welcome to the official technical documentation for the **Universal Agent**. This documentation is designed for junior developers to quickly understand the project's architecture, core logic, and operational workflows.

## 📚 Table of Contents

### 1. [Architecture](01_Architecture)

- **[System Overview](01_Architecture/System_Overview.md)**: High-level component map and core philosophy.
- **[Core Classes](01_Architecture/Core_Classes.md)**: Deep dive into `UniversalAgent`, `HeartbeatService`, and `Gateway`.

### 2. [Subsystems](02_Subsystems)

- **[Memory System](02_Subsystems/Memory_System.md)**: Tiered memory (SQLite, Vector, Archival).
- **[Heartbeat Service](02_Subsystems/Heartbeat_Service.md)**: Autonomic cycle and background monitoring.
- **[Durable Execution](02_Subsystems/Durable_Execution.md)**: Resiliency through checkpointing and state snapshots.
- **[URW Orchestration](02_Subsystems/URW_Orchestration.md)**: Complex reasoning and multi-phase task management.

### 3. [Flows](02_Flows)

- **[Event Streaming](02_Flows/Event_Streaming_Flow.md)**: Life of a conversation turn from thought to UI.
- **[Resource Guardrails](02_Flows/Resource_Guardrails.md)**: Security, workspace boundaries, and tool blocking.

### 4. [Operations](03_Operations)

- **[Configuration Guide](03_Operations/Configuration_Guide.md)**: Environment variables and feature flags.
- **[Running the Agent](03_Operations/Running_The_Agent.md)**: Modes of operation (CLI, Web, Telegram).
- **[Skill Development](03_Operations/Skill_Development.md)**: How to add new capabilities and tools.
- **[Testing Strategy](03_Operations/Testing_Strategy.md)**: Unit tests, LLM markers, and CI.

### 5. [Glossary](Glossary.md)

- Definitions of common terms like URW, Heartbeat, OK Token, and Artifacts.

---

## 🚀 Recommended Path for New Developers

1. Start with the **[System Overview](01_Architecture/System_Overview.md)** to understand the big picture.
2. Read the **[Core Classes](01_Architecture/Core_Classes.md)** to see how the main objects interact.
3. Check the **[Glossary](Glossary.md)** whenever you encounter a project-specific term.
4. Try running the agent in CLI mode using the **[Running the Agent](03_Operations/Running_The_Agent.md)** guide.
5. Explore **[Skill Development](03_Operations/Skill_Development.md)** to see how tools are integrated.
