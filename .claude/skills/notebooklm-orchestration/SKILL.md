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

## Runtime Model

Use hybrid execution:

1. MCP first when NotebookLM MCP tools are available.
2. CLI fallback (`nlm`) when MCP is unavailable, needs recovery, or user explicitly requests CLI.
3. Auth/profile setup and recovery may use CLI even when MCP is available.

## Infisical and Auth Rules

1. Secrets come only from runtime environment populated by UA Infisical bootstrap.
2. Use the dedicated profile policy:
   - `UA_NOTEBOOKLM_PROFILE` -> `NOTEBOOKLM_PROFILE` -> `vps`
3. Run auth preflight before NotebookLM calls.
   - Preferred: `uv run python scripts/notebooklm_auth_preflight.py --workspace "$CURRENT_SESSION_WORKSPACE"`
4. If auth fails and seeding is enabled, use `NOTEBOOKLM_AUTH_COOKIE_HEADER` and seed through manual login file flow.
5. After successful CLI seed/check, call MCP `refresh_auth` before MCP operations when MCP path is active.
6. Never print cookie/header secrets, and never persist them in repo files.

## Confirm-Before-Action Guardrails

Require explicit user confirmation before running any of these operations:

1. Notebook/source/studio delete actions.
2. Drive sync actions that mutate source state.
3. Public sharing toggles.
4. Share invite actions.

## Operation Coverage

### Core (Phase 1)

1. Auth: login check/recovery and refresh.
2. Notebooks: list/create/get/describe/rename/delete.
3. Sources: add/list-drive/sync/delete/describe/get-content.
4. Querying: notebook query and chat configuration.
5. Research: start/status/import.
6. Studio: create/status/delete.
7. Downloads: download artifact.

### Advanced (Phase 2)

1. Studio revise.
2. Notes (`note` unified tool).
3. Exports (`export_artifact`).
4. Sharing (`notebook_share_status`, `notebook_share_public`, `notebook_share_invite`).

## CLI Fallback Patterns

Use these only when needed and with safe flags:

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

## Skill-Creator Evaluation Workflow

For iterative quality work, keep evaluation assets in this directory:

1. `evals/evals.json` for test prompts.
2. Run with-skill vs baseline comparisons.
3. Update prompts/assertions after user review.
