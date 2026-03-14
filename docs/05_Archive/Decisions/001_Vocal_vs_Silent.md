# ADR 001: Vocal vs. Silent Tool Architectures

**Date**: 2026-01-23  
**Status**: Accepted

## Context

The Universal Agent can integrate tools in two primary ways:

1. **Vocal (In-Process)**: Tools run directly in the agent's Python environment.
2. **Silent (Subprocess)**: Tools run as independent processes (JSON-RPC over pipes).

## Decision

We maintain a **Hybrid Guard** strategy:

- **"Vocal" Internal Tools**: Core workflows (Research, Memory, Reporting) must be In-Process. This ensures "terminal disclosure"—the user sees real-time progress markers and logs.
- **"Silent" Third-Party Tools**: Lightweight or third-party integrations (like Composio or generic MCP servers) may run as subprocesses to maintain environment isolation and prevent library conflicts.

## Rationale

- **Visibility**: Long-running research tools (often 60+ seconds) are frustrating for users if "silent." Vocal tools provide a "heartbeat" of progress.
- **Safety**: Subprocesses provide better isolation for unstable scripts or tools with complex dependencies.

## Classification Policy

When adding a tool, use this rule of thumb:

- **Will it run for more than 5 seconds?**  
  - **YES**: It must be **Vocal (In-Process)**.
  - **NO**: It can be **Silent (Subprocess)**.
