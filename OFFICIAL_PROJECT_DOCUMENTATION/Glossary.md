# Glossary of Terms

| Term | Definition |
| --- | --- |
| **Agent Core** | The primary logic block (`UniversalAgent`) that handles LLM interactions and tool orchestration. |
| **Artifact** | A persistent file produced by the agent (e.g., a PDF, image, or research report) stored in a session's `artifacts/` folder. |
| **Brain Transplant** | The process of injecting global memory files into a new session's workspace at startup. |
| **Checkpoint** | A serialized snapshot of the agent's state (history, variables, plan) saved to the durable database. |
| **Durable Execution** | The system's ability to survive restarts and crashes by persisting state and resuming from checkpoints. |
| **Gateway** | A communication adapter that mediates between an interface (Telegram, CLI, Web) and the agent. |
| **Heartbeat** | An autonomic loop that triggers periodic agent "thinking" turns without user input. |
| **MCP (Model Context Protocol)** | The protocol used to expose local and remote tools to the Claude Agent SDK. |
| **OK Token** | A special string (e.g., `HEARTBEAT_OK`) used by the agent to indicate it has completed its background work. |
| **Sub-agent** | A specialized agent instance created by the Primary Agent to handle a specific task (e.g., a "Research Specialist"). |
| **URW (Universal Reasoning Workflow)** | The orchestrator for long-running, multi-phase goals that uses planning and evaluation. |
| **Vector Memory** | A tiered storage system using embeddings to find semantically relevant historical context. |
| **Workspace** | A dedicated directory on disk where a specific agent session stores its temporary files, memory, and logs. |
