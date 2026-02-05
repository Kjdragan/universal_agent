# Universal Ralph Wrapper (URW)

## Overview
The **Universal Ralph Wrapper (URW)** is the system's "harness" designed to enable ultra-long-duration autonomy. While a standard agent session eventually collapses under the weight of its own context window or crashes due to transient errors, the URW wraps the execution engine to provide stability and continuity for tasks lasting 24 hours or longer.

## Core Responsibilities

### 1. Context Hygiene & "Clean Windows"
The primary limiter of long-running agents is the context window. As the conversation history grows, cost increases, and reasoning capability degrades (the "lost in the middle" phenomenon).
*   **The Problem**: A 10-hour research session generates too much text for a single prompt.
*   **The URW Solution**: The URW monitors context usage. When a threshold is reached (or a logical sub-task completes), it triggers a **Compaction Event**.
    *   It summarizes the current state.
    *   It archives the raw logs to disk.
    *   It starts a *fresh* agent session (a "clean window") injected with only the summary and current objectives.
    *   This allows the agent to work indefinitely with a high-performance, low-latency context.

### 2. Durability & Resumption
The URW acts as a supervisor process.
*   **State Persistence**: Every major step implies a state commit to the local database/filesystem.
*   **Crash Recovery**: If the underlying process dies (OOM, restart, API failure), the URW can re-hydrate the agent from the last checkpoint. This is critical for 24h+ runs where minor network blips are statistically inevitable.

### 3. Subjective Task Decomposition
For massive tasks (e.g., "Map the entire landscape of quantum computing research"), the URW helps decompose the vague objective into a series of `Mission` objects.
*   It tracks which missions are `PENDING`, `IN_PROGRESS`, or `COMPLETED`.
*   It ensures that even if the agent gets "distracted" or goes down a rabbit hole, the URW brings the focus back to the high-level roadmap.

## Usage
The URW is typically invoked automatically when the `--long-running` or `--harness` flag is passed to the main entry point, or when a task is detected as "Massive" by the initial planner.
