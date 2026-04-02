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

## Working Rules
- Keep changes small and targeted.
- Do not commit secrets, credentials, or local state files.
- Prefer root-cause fixes over temporary workarounds.
- Update docs when behavior or operations change.

## Caveats
- _(Living section — add caveats as we discover them.)_
- Deployment is automated via GitHub Actions: merge to `develop` for staging, and promote from `develop` to `main` for production. Do not use ad hoc scripts, `ssh`, `rsync`, or `git pull`.
