# Glossary of Terms

| Term | Definition |
| --- | --- |
| **Agent Core** | The primary logic block (`UniversalAgent`) that handles LLM interactions and tool orchestration. |
| **Artifact** | A persistent file produced by the agent (e.g., a PDF, image, or research report) stored in `UA_ARTIFACTS_DIR` or referenced from a durable run workspace. |
| **Attempt** | One execution try of a run. Retries create additional attempts under the same run. |
| **Brain Transplant** | The process of injecting global memory files into a new session's workspace at startup. |
| **Checkpoint** | A serialized snapshot of the agent's state (history, variables, plan) saved to the durable database. |
| **CSI (Creator Signal Intelligence)** | The ingestion subsystem that monitors, fetches, and processes creator signals from external sources (YouTube RSS, Reddit, X/Twitter trends) for trend analysis and opportunity detection. |
| **Durable Execution** | The system's ability to survive restarts and crashes by persisting state and resuming from checkpoints. |
| **Execution Session** | The temporary live provider/runtime process attached to an active attempt. This is the correct place to use the word `session` for runtime execution. |
| **Gateway** | A communication adapter that mediates between an interface (Telegram, CLI, Web) and the agent. |
| **Heartbeat** | An autonomic loop that triggers periodic agent "thinking" turns without user input. |
| **Heartbeat Environment Context** | A factory-aware context string injected into heartbeat prompts that tells the agent: (1) which machine/factory it's running on (`UA_MACHINE_SLUG`, `FACTORY_ROLE`), (2) the current run workspace path, (3) to run commands locally (not SSH), (4) to use `mcp__internal__write_text_file` for file writes, and (5) to consolidate health checks into single compound commands. |
| **MCP (Model Context Protocol)** | The protocol used to expose local and remote tools to the Claude Agent SDK. |
| **OK Token** | A special string (e.g., `HEARTBEAT_OK`) used by the agent to indicate it has completed its background work. |
| **Run** | The durable logical unit of work. A run may have one or more attempts and one durable run workspace. |
| **Run Workspace** | The durable filesystem evidence bundle for a run. It stores checkpoints, transcripts, traces, artifacts, and attempt-local diagnostics. |
| **Skill** | A reusable capability pack that extends agent abilities. Skills are defined in `.agents/skills/` (portable skills) or `.claude/skills/` (Claude Code-specific skills). Each skill contains a `SKILL.md` file with YAML frontmatter describing when and how to use it. Examples: `clean-code`, `agentmail`, `gmail`, `vp-orchestration`. |
| **Sub-agent** | A specialized agent instance created by the Primary Agent to handle a specific task (e.g., a "Research Specialist"). |
| **URW (Universal Reasoning Workflow)** | The orchestrator for long-running, multi-phase goals that uses planning and evaluation. |
| **Vector Memory** | A tiered storage system using embeddings to find semantically relevant historical context. |
| **Workspace** | A dedicated directory on disk for durable or temporary files. In the new lifecycle model, the canonical durable bundle is the run workspace. |
