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

## Auth Recovery (MCP-First)

Follow this exact sequence before any NotebookLM MCP operation:

1. **Try `refresh_auth` first** (fast path):
   ```
   mcp__notebooklm-mcp__refresh_auth()
   ```
   If status is "ok" or "success", proceed to operations.

2. **If refresh fails**, inject cookies from environment:
   ```bash
   # Read the cookie header from Infisical-injected env
   echo "$NOTEBOOKLM_AUTH_COOKIE_HEADER" | head -c 100
   ```
   If the env var is set, call the MCP tool:
   ```
   mcp__notebooklm-mcp__save_auth_tokens(cookies=<value of NOTEBOOKLM_AUTH_COOKIE_HEADER>)
   ```
   Then retry `refresh_auth`.

3. **CLI fallback** (only if MCP tools are completely unavailable):
   ```bash
   nlm login --manual --file <(echo "$NOTEBOOKLM_AUTH_COOKIE_HEADER") --profile "${UA_NOTEBOOKLM_PROFILE:-vps}"
   ```

4. **NEVER run** `uv run python scripts/notebooklm_auth_preflight.py` — this rebuilds the venv and fails on headless VPS.
5. **NEVER run** `nlm login` without `--manual` — there is no browser on the VPS.
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

## ⚠️ Critical MCP Parameter Rules

**List/array parameters MUST be actual JSON arrays, NOT stringified arrays.**
- ✅ Correct: `source_indices: [0, 1, 2]`
- ❌ Wrong: `source_indices: "[0, 1, 2]"`
- ✅ Correct: `urls: ["https://a.com", "https://b.com"]`
- ❌ Wrong: `urls: '["https://a.com", "https://b.com"]'`

## Happy Path: Full Research Pipeline

Follow this exact sequence for research → artifact generation → download:

### Step 1: Create notebook
```
notebook_create(title="My Research Topic")
→ save notebook_id
```

### Step 2: Research and import (use built-in research, NOT web search)
```
research_start(notebook_id=<id>, query="...", source="web", mode="fast")
→ save task_id

# POLLING LOOP — MUST Bash sleep between calls!
# MCP transport does NOT block despite max_wait param.
research_status(notebook_id=<id>, task_id=<id>, max_wait=0)
→ if status != "completed": Bash("sleep 15") then poll again
→ repeat until status="completed" (deep ~5 min, fast ~30s)

research_import(notebook_id=<id>, task_id=<id>)
→ Do NOT pass source_indices — omitting it imports ALL sources automatically
```

### Step 3: Generate artifacts (confirm=true required)
```
studio_create(notebook_id=<id>, artifact_type="report", report_format="Briefing Doc", confirm=true)
studio_create(notebook_id=<id>, artifact_type="infographic", orientation="landscape", confirm=true)
studio_create(notebook_id=<id>, artifact_type="slide_deck", confirm=true)
studio_create(notebook_id=<id>, artifact_type="audio", audio_format="deep_dive", confirm=true)
```

### Step 4: Poll for completion
```
# POLLING LOOP — MUST Bash sleep between calls!
studio_status(notebook_id=<id>)
→ if any artifacts "in_progress": Bash("sleep 15") then poll again
→ repeat until ALL artifacts status="completed"
→ Audio ~3-5 min; reports/infographics ~1-2 min
```

### Step 5: Download each artifact
```
download_artifact(notebook_id=<id>, artifact_type="report", output_path="~/nlm_artifacts/briefing.md")
download_artifact(notebook_id=<id>, artifact_type="infographic", output_path="~/nlm_artifacts/infographic.png")
download_artifact(notebook_id=<id>, artifact_type="slide_deck", output_path="~/nlm_artifacts/slides.pdf")
download_artifact(notebook_id=<id>, artifact_type="audio", output_path="~/nlm_artifacts/audio.mp3")
```

### Common Mistakes to AVOID
1. **Do NOT pass `source_indices` to `research_import`** — omit it to import all.
2. **Do NOT use `urls` array in `source_add`** — add one URL at a time with `url` (singular).
3. **Do NOT stringify list parameters** — pass actual JSON arrays.
4. **Do NOT run `uv run python scripts/notebooklm_auth_preflight.py`** — it breaks.
5. **Do NOT call `nlm login` without `--manual`** — no browser on VPS.
6. **Do NOT poll `research_status` with `max_wait=0`** — use at least 60 seconds.

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
