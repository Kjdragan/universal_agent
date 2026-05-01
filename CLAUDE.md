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

## Working Rules
- Keep changes small and targeted.
- Do not commit secrets, credentials, or local state files.
- Prefer root-cause fixes over temporary workarounds.
- Update docs when behavior or operations change.

## Caveats
- _(Living section — add caveats as we discover them.)_
- Deployment is automated via GitHub Actions: `develop` is integration/review only, and a push to `main` triggers the single production deploy workflow. Do not use ad hoc scripts, `ssh`, `rsync`, or `git pull`. See [AI Coder Instructions](docs/deployment/ai_coder_instructions.md) for the full protocol.
