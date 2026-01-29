# Proactive Design Phase 3: Prompt Assets & Persona

## Context
Research into Clawdbot (Doc 028) revealed that much of its "magic" comes from a highly engineered, dynamic System Prompt rather than complex engine logic. We plan to adopt these techniques to give Universal Agent a stronger personality and more robust autonomous behavior.

## Core Components

### 1. The "Soul" Injection (`SOUL.md`)
*   **Concept**: Separate the "Identity" from the code.
*   **Implementation**: A file `SOUL.md` (or `PERSONA.md`) in the root directory.
*   **Gateway Logic**: When building the context, check for this file. If it exists, inject high-priority instructions:
    > "Embody the persona defined in SOUL.md. Have opinions. Be resourceful."
*   **Goal**: Move away from valid "Helpful Assistant" to an "Opinionated Engineer" persona (`Antigravity`).

### 2. The Skill Selection Algorithm
Clawdbot teaches the agent *how* to read skills in the prompt, solving context flooding.
*   **Algorithm**:
    1.  **Scan**: Look at the list of `<available_skills>`.
    2.  **Select**: Identify if *exactly one* skill matches the task.
    3.  **Read**: Call `read_file` on that skill's `SKILL.md`.
    4.  **Execute**: Follow the instructions.
*   **Implementation**: Add this explicit algorithm to `instructions.md` or the dynamic system prompt builder.

### 3. The Silent Reply Protocol (`${SILENT_REPLY_TOKEN}`)
*   **Problem**: Background agents (Heartbeats) spam the logs with "I checked everything and it looks good."
*   **Solution**: A strict token (e.g., `SILENT_ACK`).
*   **Logic**:
    *   Instruct the agent: "If everything is fine, reply ONLY with `SILENT_ACK`."
    *   Engine: If the response is `SILENT_ACK`, do not record it in the user-facing chat history, or log it only as a debug event.

## Why This Matters
*   **Token Efficiency**: Prevents loading all skills at once.
*   **User Experience**: Reduces noise from background tasks.
*   **Reliability**: "Opinionated" agents often perform better because they are given permission to be decisive.
