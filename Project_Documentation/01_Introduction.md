# Universal Agent System: Introduction

## What is the Universal Agent?
The Universal Agent is not simply a coding assistant; it is a comprehensive **Autonomous Execution System** designed to handle massive, complex, and often subjective tasks that extend far beyond simple binary pass/fail criteria.

While traditional agents excel at short-lived, well-defined coding problems, the Universal Agent is built for:
*   **Long-Running Operations**: Tasks that span hours or days (e.g., deep research, system monitoring).
*   **Subjective Evaluation**: Work where "success" is qualitative (e.g., "Write a comprehensive report on market trends") rather than just a unit test passing.
*   **Complex Decomposition**: Breaking down vague, high-level objectives (e.g., "How do I build an airplane?") into executable steps.

## Core Philosophy: The Engine and The Wrapper
The system is composed of two primary pillars that work in tandem:

### 1. The Multi-Agent System (The Engine)
This is the core execution logic. It is a powerful, flexible multi-agent framework capable of reasoning, planning, and tool usage.
*   **Role**: Executes specific slices of work.
*   **Capability**: Manages tools, reasoning loops, and immediate task completion.
*   **Limitation**: Without a harness, it is bound by context window limits and session fragility.

### 2. Universal Ralph Wrapper (URW) (The Harness)
The URW is the "wrapper" or harness designed to sustain the Engine over extremely long durations (24+ hours).
*   **Role**: Orchestrates the Engine across multiple sessions.
*   **Mechanism**: It generates "clean context windows" for the agent at regular intervals or logical break points. This allows the agent to essentially "sleep" and "wake up" refreshed, with only the critical context needed to continue the mission.
*   **Durability**: Ensures that a crash or context limit doesn't kill the mission. The URW persists state and resumes execution seamlessly.

## Key Capabilities
*   **Qualitative Judgment**: Includes an **LLM Judge** component to evaluate output quality for subjective tasks, preventing the system from marking a poor report as "complete" just because a file was generated.
*   **Authorized Integration**: Uses **Composio** to securely manage authenticated connections to external tools (GitHub, Slack, etc.), treating them as first-class citizens in the agent's environment.
