# Potential Skill Candidate Process & Architecture

**Date:** 2026-02-14
**Author:** Universal Agent Team
**Status:** Implemented (CLI & Harness Verified)

## 1. Overview

The "Potential Skill Candidate" process is an automated mechanism designed to identify repetitive or complex tool usage patterns that suggest a need for new, specialized skills. When an agent executes a sequence of tools exceeding a defined threshold within a single turn, the system flags this as a "Skill Candidate" for future analysis and refactoring.

## 2. Architecture & Implementation

### 2.1. The Hook System (`hooks.py`)

The core logic resides in `src/universal_agent/hooks.py`, specifically the `on_pre_tool_use_skill_detection` method within the `AgentHookSet` class.

* **Mechanism**: It tracks `_current_turn_tool_count` for the active agent turn.
* **Threshold**: Configurable via `UA_SKILL_CANDIDATE_THRESHOLD` (Default: **5**).
* **Trigger**: On the Nth tool call (where N = threshold), it emits a log event.
* **Output**:
  * **File**: Writes to centralized directory `<repo_root>/logs/skill_candidates/`.
  * **Filename**: `candidate_<timestamp>_<session_id>.log` (Unique per session).
  * **Content**: Includes full history of tool calls (tool name + input) leading up to the trigger.
  * **Telemetry**: Sends a `skill_candidate_detected` event to Logfire.

### 2.2. Integration Points

The architecture differs slightly between the standard CLI traversal and the programmatic Test Harness. This distinction was critical during recent verification.

#### A. Standard CLI (`main.py`)

* **Usage**: When running an agent via `python -m universal_agent`.
* **Wiring**: The CLI explicitly instantiates `AgentHookSet` and passes it to `UniversalAgent`.
* **Status**: ✅ Native support enabled by default.

#### B. Test Harness / Adapter (`UniversalAgentAdapter`)

* **Usage**: When running programmatic tests (e.g., `tests/final_integration_test.py`) or using the `UniversalAgentAdapter` class.
* **Wiring**: Previously, the adapter initialized `AgentSetup` with *only* default hooks (which excluded the skill detector).
* **Fix (2026-02-14)**: The adapter in `src/universal_agent/urw/integration.py` was patched to explicitly instantiate `AgentHookSet` and register its hooks into `AgentSetup` before agent creation. This ensures parity between CLI and Harness behavior.

## 3. Verified Behavior

Recent testing confirmed the following behavior:

1. **Under Threshold**: A run with 4 tool calls (e.g., `final_integration_test.py`) does **NOT** trigger the hook.
2. **At Threshold**: A run with 5 or more tool calls (e.g., `verify_cli_and_hooks.py` with 6 sequential Bash commands) **DOES** trigger the hook, logging:
    `Tool Count: 5 (Threshold: 5) | Trigger Tool: Bash | Potential Skill Candidate Detected`
3. **Enhanced Logging**: The centralized log file now captures the **full sequence** of tool inputs (e.g., `find` -> `wc` -> `head`), providing context for *why* the threshold was hit.

## 4. Outstanding Issues & Future Work

While the detection mechanism is active, the following areas require future focus:

1. **Automated Refactoring**: Currently, the system only *logs* the candidate. It does not yet automatically propose or scaffold a new skill (XML/Python) based on the detected pattern.
2. **Contextual Analysis**: The hook counts raw tool usage but doesn't analyze *intent*. A "dumb" loop of `ls` commands triggers it just as easily as a complex, novel workflow. Future versions should use an LLM or heuristic to filter for "useful" patterns.
3. **Future**: Use the centralized logs to train a specialized "Skill Generator" model.

## 5. How to Help

To advance this process, we need assistance with:

* **Pattern Analysis**: Reviewing `skill_candidates.log` to identify *actual* useful skills vs. noise.
* **Skill Generator Agent**: Designing an agent that can consume a `skill_candidates.log` entry (and associated trace) and output a draft `skill_definition.xml`.
