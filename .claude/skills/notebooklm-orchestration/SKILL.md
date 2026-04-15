---
name: notebooklm-orchestration
description: >
  Orchestrate NotebookLM operations for UA with a hybrid MCP-first and CLI-fallback
  execution model, backed by Infisical-injected auth seed and VPS-safe guardrails.
  Use whenever the user mentions NotebookLM/notebooklm/nlm, notebooks, NotebookLM
  source ingestion, NotebookLM research, podcast/audio overview generation, report/quiz
  creation, flashcards, slide decks, infographics, downloads, sharing, or NotebookLM
  automation workflows. Route execution to `notebooklm-operator` by default.
---

# NotebookLM Orchestration

Use this skill whenever the user intends to operate NotebookLM via UA.

## Routing Contract

1. Detect NotebookLM intent.
2. Delegate to sub-agent by default:
   - `Task(subagent_type='notebooklm-operator', ...)`
3. Keep primary-agent behavior concise: gather intent, request confirmation when needed, then delegate.
4. **Pass the full pipeline to the operator in a single delegation.** Do NOT micro-manage individual NLM MCP calls from the primary agent.

## Runtime Model

Use hybrid execution:

1. MCP first when NotebookLM MCP tools are available.
2. CLI fallback (`nlm`) when MCP is unavailable, needs recovery, or user explicitly requests CLI.
3. Auth/profile setup and recovery may use CLI even when MCP is available.

## Auth Recovery (MCP-First)

Follow this exact sequence before any NotebookLM MCP operation:

1. **Try `refresh_auth` first** (fast path):
   ```
   mcp__notebooklm-mcp__refresh_auth()
   ```
   If status is "ok" or "success", proceed to operations.

2. **If refresh fails**, inject cookies from environment:
   ```bash
   echo "$NOTEBOOKLM_AUTH_COOKIE_HEADER" | head -c 100
   ```
   If set, call: `save_auth_tokens(cookies=<value>)` then retry `refresh_auth`.

3. **CLI fallback** (only if MCP tools are completely unavailable):
   ```bash
   nlm login --manual --file <(echo "$NOTEBOOKLM_AUTH_COOKIE_HEADER") --profile "${UA_NOTEBOOKLM_PROFILE:-vps}"
   ```

4. **NEVER** run `uv run python scripts/notebooklm_auth_preflight.py` or `nlm login` without `--manual`.

## Confirm-Before-Action Guardrails

Require explicit user confirmation before:
1. Notebook/source/studio delete actions.
2. Drive sync actions that mutate source state.
3. Public sharing toggles.
4. Share invite actions.

## ⚠️ Critical MCP Parameter Rules

**List/array parameters MUST be actual JSON arrays, NOT stringified arrays.**
- ✅ Correct: `source_indices: [0, 1, 2]`
- ❌ Wrong: `source_indices: "[0, 1, 2]"`

## Performance Guidelines (Pass to Operator)

When delegating to the operator, include these hints in your prompt:

1. **Adaptive polling** — do NOT use fixed `sleep 15`:
   - Fast research: `sleep 5` (max 6 polls)
   - Deep research: `sleep 20` (max 20 polls)
   - Studio artifacts: `sleep 10` (max 30 polls)
   - Audio/video: `sleep 20` (max 20 polls)

2. **Parallel artifact generation** — fire ALL `studio_create` calls first, THEN poll `studio_status` once for all of them. Do NOT wait between individual create calls.

3. **Default to `mode="fast"` for research** — only use deep when user says "comprehensive/thorough/exhaustive."

## CLI Fallback Patterns

Use these only when MCP is unavailable:

```bash
nlm login --check --profile "$UA_NOTEBOOKLM_PROFILE"
nlm notebook list --json --profile "$UA_NOTEBOOKLM_PROFILE"
nlm source add <notebook> --url "https://..." --wait --profile "$UA_NOTEBOOKLM_PROFILE"
nlm studio status <notebook> --profile "$UA_NOTEBOOKLM_PROFILE"
```

## Output Expectations

The delegated sub-agent should return a compact contract with:

1. `status`
2. `path_used` (`mcp|cli|hybrid`)
3. `operation_summary`
4. `artifacts`
5. `warnings`
6. `next_step_if_blocked`
