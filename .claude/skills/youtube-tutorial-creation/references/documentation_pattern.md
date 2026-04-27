# Agentic Documentation Architecture & Best Practices

This document outlines the strict documentation patterns, rules, and architectural standards that should be implemented when configuring a new repository for an AI agent team. Following this pattern ensures that agents maintain a coherent, single-source-of-truth knowledge base without fragmenting information.

## 1. Directory Structure & Naming

**Strict Boundary:** All project documentation MUST reside exclusively within the `docs/` directory. 
- Creating any other documentation directories (such as `OFFICIAL_PROJECT_DOCUMENTATION/` or scattered `.md` files in source folders) is strictly prohibited.
- Subdirectories should be organized by domain (e.g., `docs/01_Architecture/`, `docs/02_Operations/`).

## 2. The Dual-Index System (MANDATORY)

Every project must maintain two central indexes at the root of the `docs/` directory. These indexes prevent documentation drift and fragmentation.

1. **`docs/README.md`**: The thematic index. Organizes documents by subject matter, architecture, flows, and operations. It serves as the entry point for onboarding.
2. **`docs/Documentation_Status.md`**: The metadata tracker. Logs the status, last verified dates, and ownership of every document.

**The Golden Rule of Indexing:** No document should exist in `docs/` without being explicitly linked in *both* index files.

## 3. Agent Rules for Creating & Updating Docs

Agents operating in the repository must adhere to the following workflow when modifying documentation:

1. **Always Check the Indexes First:** Before taking action, the agent must consult `docs/README.md` and `docs/Documentation_Status.md` to understand the existing knowledge map.
2. **Update Over Create:** If a document already exists for a topic, update the existing file rather than creating a new one. Do not create overlapping files.
3. **Log New Documents:** If a completely new file is required, the agent MUST add a link and description of that new file to both index files before completing the task.

## 4. Dynamic Documentation Maintenance

Documentation updates are **not optional follow-up work** — they are part of the core implementation itself. Agents must follow these principles:

- **Update during implementation, not after:** Treat documentation updates as a deliverable of the same work unit as the code changes.
- **Update canonical source-of-truth docs first:** Identify the "master document" for a system component and update it directly, rather than leaving peripheral notes elsewhere.
- **Code-Verified Citations:** Do not describe system behavior vaguely. Use concrete, code-verified citations with direct links (e.g., `file:///src/app.py#L42`) to support explanations.
- **Include Visual Artifacts:** Text is often insufficient for complex architectures. Agents should proactively inject Mermaid sequence diagrams, flowcharts, and architecture graphs into the documentation.

## 5. Standard for Implementation Plans

When an agent proposes an architectural change or a new feature, the implementation plan must be a high-quality decision document:

- **Mermaid Diagrams:** Required for any multi-component interactions, state machines, or branching logic.
- **Concrete Code Snippets:** Show actual function signatures, imports, and core logic changes, not just abstract descriptions.
- **Impact Summary Tables:** Clearly contrast "What Changes" vs "What Stays the Same".
- **Phase Breakdown:** Separate configuration updates, code modifications, and prompt/documentation updates into distinct phases.

---

### Implementation Instructions for the Agent

1. Initialize a `docs/` directory in the root of the new project.
2. Create `docs/README.md` and `docs/Documentation_Status.md`.
3. Add a `<user_rules>` or system prompt injection that strictly enforces the indexing rules and dynamic maintenance responsibilities described above.
