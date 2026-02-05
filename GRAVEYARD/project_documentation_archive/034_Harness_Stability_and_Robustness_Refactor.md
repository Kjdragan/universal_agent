# 034: Harness Stability & Report Generation Robustness Refactor

**Date:** 2026-01-30
**Status:** IMPLEMENTED
**Author:** Antigravity

## Overview

Following a series of real-world stress tests using the Universal Agent harness, several "fragility" points were identified that caused execution hangs, report generation failures, and subagent degraded behavior. This document records the stabilization efforts and the architectural shift toward granular tool exposure for increased resilience.

## 1. Harness Foundation & Core Stabilization

The initial harness runs encountered critical "hard" failures that prevented phase transitions and gateway communication.

### Key Fixes:
- **`ExecutionResult` Type Consistency**: Fixed a `TypeError` in `main.py` where the harness was instantiating `ExecutionResult` with incorrect field names.
- **Indentation & Logic Integrity**: Resolved an `IndentationError` in `main.py` that prevented the gateway from starting when processing non-massive requests.
- **Improved Plan Extraction**: Updated `interview.py`'s normalization logic to handle common LLM variations (e.g., `phase_number` vs `phaseNumber`), preventing the "Plan not detected" repair loops which had previously stalled the harness.

## 2. Real-time Observability & UI Event Streaming

Debugged the "Internal Logs" and "Activity" panels in the Web UI to ensure real-time feedback during long-running research tasks.

### Improvements:
- **`ContextVar` Integration**: Implemented `ContextVar` in `hooks.py` to reliably track `run_id` and `trace_id` across asynchronous boundaries.
- **Stdout Bridging**: Enhanced `StdoutToEventStream` to capture in-process tool outputs and bridge them immediately to the Web UI's activity log.
- **Thinking Blocks**: Fixed the extraction logic to ensure "Thinking..." blocks are emitted in real-time, improving the "wait time" experience for the user.

## 3. Report Generation Resilience

The legacy report generation pipeline was identified as a major point of failure due to LLM output inconsistency.

### Infrastructure Hardening:
- **Robust JSON Extraction**: Completely refactored `generate_outline.py` to use a multi-stage regex extraction method. It can now "find" a valid JSON object wrapped inside HTML, conversational filler, or multiple markdown code blocks.
- **JSON "Healing"**: Added basic self-repair logic to handle common small LLM errors like trailing commas in large outlines.
- **Prompt Tightening**: Updated the outline prompt with "Strict Instructions" to discourage conversational noise and enforce JSON compliance.

## 4. Agentic Architecture: Granular Recovery

Previously, report generation was a "black box" tool (`run_report_generation`). If any step failed (e.g., the outline), the entire task failed, often causing the subagent to enter a "wandering" state or emit empty `Bash` commands.

### Architectural Shift:
- **Granular Tool Exposure**: Individual pipeline steps (`generate_outline`, `draft_report_parallel`, `cleanup_report`, `compile_report`) are now exposed as first-class MCP tools.
- **Subagent Guardrails**:
    - **Recovery Protocol**: The `report-writer` subagent is now instructed to use granular tools as a backup if the composite tool fails.
    - **Bash Hygiene**: Strictly enforced a `command` parameter requirement in the subagent system prompts to prevent the `InputValidationError` (Empty Command) issue.
    - **Wandering Prevention**: Added clear workflow boundaries to prevent the `research-specialist` from entering the `report-writer`'s scope during failures.

## Verification Summary

A verification suite was executed to confirm the logic fixes:
- **JSON Test Suite**: 4/4 passing (Clean JSON, Markdown Wrapped, HTML Wrapped site, and Trailing Comma).
- **Harness Verification**: Successfully completed a full "future of agentic coding" research task with real-time logging and phase transitions.

---
*This document serves as a record of transition from integrated "black box" research tools toward a resilient, granular, and observable subagent ecosystem.*
