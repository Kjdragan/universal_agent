# Clawdbot Self-Learning & Skill Architecture Deep Dive

## Executive Summary

The user challenged the previous finding, requesting "evidence and example code blocks" of Clawdbot's proactive skill creation mechanism.
**Conclusion:** After an exhaustive search of the `clawdbot` repository (`src/agents`, `src/commands`, `src/auto-reply`), I confirm that **there is no TypeScript code that effectively "hard-codes" proactive skill creation.**

The "learning" behavior is structurally different from "Operational Repair":
*   **Operational Repair (Hard-coded):** The engine *does* have C++ style logic for retries, context compaction, and auth failover.
*   **Knowledge Repair (Agentic):** The creation of new skills is an *emergent behavior* driven entirely by the **System Prompt** and the **`skill-creator` Skill Definition**, not by a hidden "Reflector" class in the engine.

---

## 1. Evidence of Hard-Coded "Operational" Self-Correction

The Clawdbot engine *is* proactive about keeping the *process* alive, but not about creating *content* (skills).

### A. Auto-Compaction (Context Repair)
In `src/agents/pi-embedded-runner/run.ts` and `compact.ts`, the engine detects `ContextOverflowError` and automatically "compacts" (summarizes) the session to free up space.

```typescript
// src/agents/pi-embedded-runner/run.ts
if (isContextOverflowError(errorText)) {
  if (!isCompactionFailure && !overflowCompactionAttempted) {
    log.warn(`context overflow detected; attempting auto-compaction...`);
    // Engine triggering a self-repair action
    const compactResult = await compactEmbeddedPiSessionDirect({ ... });
    if (compactResult.compacted) continue; // Retry the loop
  }
}
```
*This is the kind of "code block" the user was looking for, but it only handles **Context**, not **Skills**.*

### B. Auth Profiler & Failover
The engine actively monitors for Rate Limits and Timeouts, and automatically rotates API keys (`advanceAuthProfile`).

```typescript
// src/agents/pi-embedded-runner/run.ts
if (shouldRotate) {
   const rotated = await advanceAuthProfile(); // Switches to next API key
   if (rotated) continue; // Retries the loop
}
```

---

## 2. The Absence of "Skill Learning" Code

I searched specifically for logic that would trigger `create_skill` or `write_skill` based on success/failure signals.

*   **`run.ts` (Main Loop):** Contains NO logic to analyze the *content* of a successful turn to extract a lesson. It simply returns the `EmbeddedPiRunResult`.
*   **`session-transcript-repair.ts`:** Fixes JSON structure (missing tool results) but ignores semantic meaning.
*   **`system-prompt.ts`:** Injects the `skillsPrompt` variable but does not contain a "Reflect and Learn" section.
*   **`skill-commands.ts`:** Handles user commands (`/skill install`), not autonomous agent commands.

**Verdict:** The feature "Proactive Skill Creation" is not a function in the codebase (e.g., `autoCreateSkill()`).

---

## 3. How the "Learning" Actually Works (The Missing Link)

Since the code doesn't do it, the behavior must be **Instruction-Driven**. The Agent does it because it is *told* to, likely in one of two places:

### A. The `skill-creator` Skill Definition
The `skill-creator` skill (which the user provided) likely contains a `SKILL.md` with instructions like:
> "If you encounter a repetitive task or solve a complex problem, use the `init_skill` tool to save this workflow."

### B. The "Skills Prompt" Injection
Clawdbot's `system-prompt.ts` injects a `skillsPrompt` string (line 297).
```typescript
// src/agents/system-prompt.ts
const skillsPrompt = params.skillsPrompt?.trim();
// ...
...buildSkillsSection({ skillsPrompt, ... })
```
This specific string is likely dynamically configured (in the user's config or a hidden prompt file) to say:
> "You have the ability to create new skills. Use this power when..."

---

## 4. Recommendation for Universal Agent

To replicate this "Proactive Learning" behavior, we do not need to write Python code in `universal_agent`. We need to **Engineer the Prompt**.

**Proposed Action:**
1.  **Keep the Code Simple:** Do not try to write a "SkillMonitor" class in Python.
2.  **Update System Prompt / Instructions:** Add a dedicated section to our `instructions.md` or `skill-creator` skill:
    > **Self-Improvement Protocol:**
    > "Verification Phase is not just for fixing code—it is for fixing *your process*.
    > If you had to try more than once to get a tool usage right (like the Gmail Attachment issue), you MUST create a new Skill to document the correct usage for your future self."

This aligns with the architecture of Clawdbot: **Engine handles Life Support (Context/Auth), Agent handles Intelligence (Skills).**
