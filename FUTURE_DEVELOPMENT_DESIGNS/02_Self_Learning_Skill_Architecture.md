# Proactive Design Phase 2: Self-Learning & Skill Architecture

## Context
Clawdbot demonstrates "Self-Learning" not through a neural network update, but through an **Agentic Loop** of observing friction, finding a solution, and saving that solution as a Skill. We aim to replicate this behavior in Universal Agent.

## The Friction-Correction-Codification Loop

1.  **Friction**: The agent fails to use a tool correctly (e.g., passing a list to a single file argument) or struggles with a complex multi-step workflow.
2.  **Correction**: The agent eventually solves the problem (via retry or reasoning).
3.  **Codification**:
    *   Instead of just finishing the task, the agent recognizes the value of the solution.
    *   It uses `init_skill` (from `skill-creator`) to document the pattern.
    *   It writes a `SKILL.md` file (e.g., `skills/gmail/SKILL.md`) with the learned lesson.
4.  **Assimilation**: The system loads this new Skill in future sessions, preventing the error from recurring.

## Implementation Architecture

### 1. The Meta-Skill (`skill-creator`)
We already have this. It provides the tools (`init_skill`, `write_to_file`) and the template for creating skills.

### 2. The Verification Request
We need to modify the **Prompt** or **Verification Step** to explicitly ask:
> "Did you have to try multiple times to get a tool working? If so, you MUST create a Skill to document the correct usage for your future self."

### 3. Skill Proposal Tool
Optionally, introduce a lightweight tool `propose_skill(topic: string, reason: string)` that the agent calls. The user (or a Supervisor Agent) then approves the creation of the full skill.

## Key Insight
"Learning" is just **Self-Documentation**. By giving the agent the power to write its own "User Manual" (Skills), we allow it to evolve without changing the underlying engine code.
