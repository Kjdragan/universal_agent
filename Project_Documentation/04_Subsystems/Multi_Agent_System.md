# Multi-Agent System (The Execution Engine)

## Overview
If the URW is the "body" that provides endurance, the **Multi-Agent System** is the "brain" that performs the work. It is the execution engine that runs within the context windows managed by the harness. It is responsible for reasoning, tool execution, and producing the actual output.

## Architecture
The system is not a single monad but a collection of specialized sub-agents orchestrated to solve a problem.

### 1. The Dispatcher
The entry point for the engine. It analyzes the current input (or the clean context from URW) and routes control to the appropriate sub-agent or toolset.

### 2. The Worker Agents
Specialized personas for distinct activities:
*   **Researcher**: Uses browser and search tools to gather information.
*   **Coder**: Capable of file I/O, syntax checking, and running tests.
*   **Planner**: Breaks down the immediate `Mission` into actionable `Steps`.

### 3. The LLM Judge (Quality Assurance)
A critical innovation in this system is the **Subjective Evaluation Layer**.
*   **The Challenge**: In complex tasks (e.g., "Write a blog post"), there is no compiler error to tell you if you failed.
*   **The Solution**: The engine employs an "LLM Judge"—a distinct model call with a specific prompt designed to critique the output of the Worker Agents.
*   **Criteria**: It evaluates based on:
    *   **Completeness**: Did it answer the user's prompt?
    *   **Quality**: Is the tone/depth appropriate?
    *   **Accuracy**: Does it hallucinate known facts? (checked against retrieved context)
*   **Loop**: If the Judge rejects the output, the Worker is tasked to fix it *before* the system reports "Success" to the URW or User.

## Tooling Interface
The Execution Engine interacts with the outside world via:
*   **Native Tools**: File system, Shell (controlled environment).
*   **Composio Integration**: For external SaaS interactions (GitHub, Slack, etc.).
*   **MCP Clients**: Connecting to Model Context Protocol servers for extended capabilities.
