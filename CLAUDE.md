# CLAUDE.md

This file provides quick working context for Claude (and other coding agents) in this repository.

## Project Description
`universal_agent` is a Python-based agent runtime and orchestration project.

It includes:
- Agent execution and orchestration logic under `src/universal_agent/`
- Operational docs under `docs/`
- Environment-driven feature flags and scheduler controls via `.env`

## Key Commands
- Install deps: `uv sync`
- Run app: `uv run python -m src.universal_agent.main`
- Run tests: `uv run pytest`
- Lint/format (if configured): `uv run ruff check .` / `uv run ruff format .`

## Git Workflow (MUST READ)
- **Read [`docs/deployment/ai_coder_instructions.md`](docs/deployment/ai_coder_instructions.md) before your first commit.** It defines the branch discipline, commit conventions, and `/ship` handoff protocol that all AI coders must follow.
- TL;DR: Work on `feature/latest2`. Push there. Never touch `develop` or `main`. Someone else runs `/ship`.

## Claude Execution Environments (MUST READ before touching anything Claude-related)
UA runs **TWO separate Claude environments side-by-side on the VPS**:

1. **ZAI-mapped (default everywhere except `/opt/ua_demos/`)** — cheap GLM models via the ZAI proxy. Used for all routine UA work, Cody's normal coding tasks, the ClaudeDevs intel cron, Simone heartbeats, etc.
2. **Anthropic-native (only inside `/opt/ua_demos/<demo-id>/`)** — real Claude models (Opus/Sonnet/Haiku) via the Max plan OAuth session. Used **only** for Phase 3 demo execution where the demo needs to exercise brand-new Anthropic features that the ZAI proxy may not have yet.

Mistaking one for the other is the #1 source of confusion. Before debugging anything Claude-related, **read [`docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md`](docs/06_Deployment_And_Environments/09_Demo_Execution_Environments.md)** — especially the decision tree and the CLI-vs-SDK auth wrinkle.

Operational runbook: [`docs/operations/demo_workspace_provisioning.md`](docs/operations/demo_workspace_provisioning.md).

## ClaudeDevs Intelligence v2 — Active Implementation Plan
The ClaudeDevs X intel pipeline is undergoing a v2 rebuild. Two living docs track it:

- **Design (what we're building):** [`docs/proactive_signals/claudedevs_intel_v2_design.md`](docs/proactive_signals/claudedevs_intel_v2_design.md) — the original 13-PR design with vault-as-canonical-product, append-dominant Memex maintenance, Phase 0–5 pipeline, Simone↔Cody orchestration.
- **Plan (what's left):** [`docs/proactive_signals/claudedevs_intel_v2_remaining_work.md`](docs/proactive_signals/claudedevs_intel_v2_remaining_work.md) — reconciled execution catalog. Cross-references original design § 16 PRs to the actual reconciled PRs (some shipped scaffolding only; their wiring is tracked separately). Lists what's shipped vs what's left across four phases. Updated after every ship — read this first when picking up the work.

## Working Rules
- Keep changes small and targeted.
- Do not commit secrets, credentials, or local state files.
- Prefer root-cause fixes over temporary workarounds.
- Update docs when behavior or operations change.

## Caveats
- _(Living section — add caveats as we discover them.)_
- Deployment is automated via GitHub Actions: `develop` is integration/review only, and a push to `main` triggers the single production deploy workflow. Do not use ad hoc scripts, `ssh`, `rsync`, or `git pull`. See [AI Coder Instructions](docs/deployment/ai_coder_instructions.md) for the full protocol.
