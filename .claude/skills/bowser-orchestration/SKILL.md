---
name: bowser-orchestration
description: Orchestrate browser-native execution using Bowser's layered stack (skills + subagents + commands) for UI validation, authenticated web operations, and parallel browser workflows.
commands:
  - /ui-review
  - /bowser:hop-automate
  - /bowser:amazon-add-to-cart
  - /bowser:blog-summarizer
agents:
  - bowser-qa-agent
  - claude-bowser-agent
  - playwright-bowser-agent
skills:
  - claude-bowser
  - playwright-bowser
  - just
---

# Bowser Orchestration

## Purpose

Use this skill when the user task requires browser-native execution, UI testing, or web workflows that should be reusable and scalable.

This skill helps the coordinator reason at the system level:
- choose the right browser lane
- route work to the right subagent
- elevate one-off actions into reusable command workflows

## Routing Matrix

1. Use `claude-bowser` / `claude-bowser-agent` for authenticated workflows that require real Chrome session identity (cookies, extensions, logged-in state).
2. Use `playwright-bowser` / `playwright-bowser-agent` for isolated, repeatable, and parallel workflows.
3. Use `bowser-qa-agent` for structured user-story validation with step-level screenshots and pass/fail reporting.

## Command Selection

- `/ui-review`: Run parallel QA across story definitions.
- `/bowser:hop-automate`: Higher-order workflow executor that resolves skill/mode/vision.
- `/bowser:amazon-add-to-cart`: Authenticated shopping-cart flow that stops before order submission.
- `/bowser:blog-summarizer`: Headless website reading + summary workflow.

## Workflow

1. Classify the user request:
   - authenticated browser operation?
   - UI validation/testing?
   - parallel multi-target browser run?
2. Choose lane (`claude-bowser` vs `playwright-bowser`) based on identity and concurrency needs.
3. Decide direct skill call vs subagent delegation vs command orchestration.
4. Execute and collect evidence (screenshots, step reports, console/network data as needed).
5. Chain outputs into downstream specialists (analysis/reporting/delivery) where valuable.

## Guardrails

- Do not parallelize `claude-bowser`; Chrome MCP is single-controller.
- Close Playwright sessions when done.
- For purchase/payment flows, stop before irreversible submission unless explicitly requested.
- Prefer evidence-backed results over speculative assertions.
