# Clawdbot Prompt Assets & Techniques Analysis

## Executive Summary

After a comprehensive search of the Clawdbot repository, I have identified the core "Prompt Assets" and the engineering techniques used to assemble them. Unlike a static text file, Clawdbot uses a **dynamic, modular system prompt builder** (`src/agents/system-prompt.ts`) that injects context-specific instructions.

Key discoveries include:
1.  **"Soul" Architecture**: A dedicated `SOUL.md` file for persona definition.
2.  **Instructional plumbing**: Specific prompts that teach the agent *how* to use its own architecture (Skills, Memory).
3.  **Silent Protocols**: A strictly enforced "Silent Reply" token to allow the agent to work without chattering.
4.  **OpenProse VM**: A "Virtual Machine" persona that enforces strict execution of a custom language.

---

## 1. System Prompt Architecture

**Location:** `src/agents/system-prompt.ts`

The system prompt is not a static string. It is a function (`buildAgentSystemPrompt`) that conditionally assembles the following blocks:

### A. The "Soul" Injection (Persona)
Instead of hardcoding "You are a helpful assistant", Clawdbot checks for a `SOUL.md` file in the workspace.
**Technique:** If found, it injects this high-leverage instruction:
> "If SOUL.md is present, embody its persona and tone. Avoid stiff, generic replies; follow its guidance unless higher-priority instructions override it."

**Asset:** `docs/reference/templates/SOUL.md`
```markdown
# SOUL.md - Who You Are
*You're not a chatbot. You're becoming someone.*
**Be genuinely helpful, not performatively helpful.** Skip the "Great question!" ...
**Have opinions.** ... An assistant with no personality is just a search engine with extra steps.
```
*Recommendation:* We should adopt a `PERSONA.md` or `SOUL.md` to give our agent a distinct voice and "opinionated" stance, moving away from generic "Helpful Assistant".

### B. Skill Discovery Protocol
Clawdbot doesn't just list tools; it teaches the agent an *algorithm* for selecting them.
**Technique:**
> "Before replying: scan <available_skills> <description> entries."
> "- If exactly one skill clearly applies: read its SKILL.md ... then follow it."
> "- If multiple could apply: choose the most specific one..."
> "Constraints: never read more than one skill up front..."

*Insight:* This solves the "context window flooding" problem by forcing a two-step process (Scan -> Read) explicitly in the system prompt.

### C. The "Silent Reply" Token
To prevent the agent from saying "Okay, I added the file" (which wastes tokens and user attention), they enforce a silent protocol.
**Technique:**
> "When you have nothing to say, respond with ONLY: ${SILENT_REPLY_TOKEN}"
> "❌ Wrong: 'Here's help... ${SILENT_REPLY_TOKEN}'"
> "✅ Right: ${SILENT_REPLY_TOKEN}"

*Recommendation:* Extremely valuable for "System Events" (like saving a file or running a cron job) where the user doesn't need a text confirmation.

---

## 2. Specialized Prompt Assets

### A. OpenProse VM (Strict Execution Schema)
**Location:** `extensions/open-prose/skills/prose/guidance/system-prompt.md`
This is a masterclass in **Role Enforcement**. It explicitly strips away the "Assistant" persona and replaces it with a "Machine" persona.

> "You are not simulating a virtual machine—you **ARE** the OpenProse VM."
> "Your conversation history = The VM's working memory"
> "Your Task tool calls = The VM's instruction execution"

*Application:* Validates our approach of using distinct System Prompts for specialized sub-agents (e.g., a "Coder" agent vs. a "Planner" agent).

### B. Reasoning / Thinking Tags
**Location:** `src/agents/system-prompt.ts` (lines 285-293)
It enforces a "Think first, talk later" loop directly in the prompt structure, supporting "Reasoning Models" pattern even on non-o1 models.

> "ALL internal reasoning MUST be inside <think>...</think>."
> "Only the final user-visible reply may appear inside <final>."

---

## 3. Findings & Recommendations for Universal Agent

| Feature | Clawdbot Approach | Universal Agent Status | Recommendation |
| :--- | :--- | :--- | :--- |
| **Persona** | `SOUL.md` file injected dynamically | Static System Prompt | **Adopt** `SOUL.md` or `PERSONA.md` in root. |
| **Skill Use** | "Scan -> Read" algorithm in prompt | Tool definitions only | **Add** explicit Skill Selection algorithm to System Prompt. |
| **Silence** | `${SILENT_REPLY_TOKEN}` | Natural language replies | **Adopt** for background tasks (Cron/Heartbeat). |
| **Context** | Injected via "Bootstrap" files (`BOOTSTRAP.md`) | Injected via strings | **Move** large context (Constraints, Stack) to files like `CONTEXT.md` to be injected. |

### Immediate Action Plan
1.  **Prompt Refactor**: Update our `instructions.md` (or main system prompt) to include the **Skill Selection Algorithm**. This is the "magic" that makes their skill system autonomous.
2.  **Persona**: Create a `SOUL.md` template for our agent to give it the "Antigravity" personality (Agentic, bold, precise) rather than generic polite AI.
