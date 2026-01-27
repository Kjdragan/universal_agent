# Operations Guide

## Environment

- Prefer `.env` for secrets; scripts load it via `python-dotenv`.

## Running the API (Web UI backend)

- Script reference: `start_ui.sh`
- Backend command:
  - `PYTHONPATH=src uv run python -m universal_agent.api.server`

## Running the test suite

- `uv run python scripts/test_gateway_refactor.py --test all`

## Debugging tips

- If `universal_agent` imports fail:
  - Ensure the package is installed in the uv environment:
    - `uv pip install -e .`

- If live tests are skipped:
  - Ensure API keys are set in `.env` (e.g. `ANTHROPIC_API_KEY`).
