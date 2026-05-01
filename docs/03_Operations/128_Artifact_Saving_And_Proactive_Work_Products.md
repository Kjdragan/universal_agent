# Artifact Saving and Proactive Work Products

**Date:** 2026-04-30
**Component:** VP Workers, Proactive Intel Pipeline, Task Hub
**Authoritative Path Strategy:** `/home/ua/vpsrepos/` vs `AGENT_RUN_WORKSPACES/`

This document defines the canonical strategy for how autonomous agents (specifically VP agents like CODIE) persist files, code projects, and demonstrations generated during proactive tasks (e.g. Tier 3 X-Intel packets).

## The Problem: Ephemeral Daemon Archives

By default, the Universal Agent runtime executes tasks within a designated "run workspace."
For background daemon operations (like `simone_todo_daemon`), these run workspaces are placed into a transient archive folder:
`/opt/universal_agent/AGENT_RUN_WORKSPACES/_daemon_archives/`

If a VP agent is dispatched to "build a demo" during a proactive task, and it writes that code relative to its current working directory, the resulting code will be trapped inside that ephemeral run log. While preserved temporarily, it is extremely difficult to discover manually, and is subject to eventual garbage collection.

## The Solution: Centralized Persistent Artifacts

To prevent valuable proactive work from being lost in run archives, we establish the following canonical rule:

**All code projects, technical demonstrations, and multi-file artifacts generated proactively by an agent MUST be saved to a centralized, durable directory on the VPS.**

### The Canonical Path: `/home/ua/vpsrepos/`

When agents are instructed to build new code, they are directed via their system instructions (e.g., in `claude_code_intel.py`) to output their work directly to `/home/ua/vpsrepos/<project_name>`.

This ensures that:
1. Work products outlive the ephemeral heartbeat or daemon run that spawned them.
2. The user has a single, predictable location to find all autonomously generated code on the VPS.
3. VP agents have a shared space where they can be dispatched to resume work on previously created projects.

### Searching for Lost Artifacts

If you receive an email or Task Hub notification indicating a VP agent has completed a task, but you cannot find the output in `/home/ua/vpsrepos/`, it is likely the agent defaulted to its run workspace.

You can trace the work product by looking up the `session_id` or `run_id` mentioned in the logs, and navigating to:
`/opt/universal_agent/AGENT_RUN_WORKSPACES/_daemon_archives/<run_id>/work_products/`

## Implementation Checklist for Agent Skill Creators

When building new skills or proactive intelligence pipelines that result in code generation or significant artifact creation, always include an explicit path instruction in the task payload.

**Example Instruction:**
> IMPORTANT: When building code or demos on the VPS, ALWAYS save them to `/home/ua/vpsrepos/<project_name>` instead of the ephemeral run workspace.
