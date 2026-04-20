# Universal Agent Test Suite

## 🛡️ The Safety Net (Start Here)

The **unit test suite** is the primary regression gate. It runs **1211 tests in ~7 minutes** and is the most important command to run before shipping.

```bash
# ⚡ THE pre-ship command — run this before every /ship
uv run pytest tests/unit/ -q --ignore=tests/unit/test_zai_llm_connectivity.py

# Expected: 1211 passed, 0 failed
```

> **Note**: `test_zai_llm_connectivity.py` requires live API tokens. Include it when tokens are refreshed.

## 📂 Directory Structure

```text
tests/
├── conftest.py                  # Root — Logfire suppression + ContextVar isolation (autouse)
├── unit/              (156 files) # 🚨 PRIMARY: Hooks, guardrails, Task Hub, heartbeat, workspace
├── gateway/            (52 files) # Gateway server integration (subprocess-based)
├── stabilization/       (3 files) # Direct vs Gateway parity smoke tests
├── durable/             (7 files) # Durable state, ledger, persistence
├── integration/        (10 files) # E2E flows (Composio, Web UI, workspace)
├── memory/             (14 files) # Letta memory system
├── api/                 (9 files) # API endpoint tests
├── delegation/          (5 files) # VP delegation
├── discord/             (2 files) # Discord intelligence
├── letta/              (10 files) # Letta subsystem
├── bot/                 (2 files) # Bot tests
├── contract/            (1 file)  # Contract tests
├── skills/              (2 files) # Skill tests
└── reproduction/        (1 file)  # Bug reproductions
```

## Running Component Tests

```bash
# Unit tests (primary safety net)
uv run pytest tests/unit/ -q

# Gateway integration
uv run pytest tests/gateway/ -x -vv

# Durable execution logic
uv run pytest tests/durable/ -v

# Memory system
uv run pytest tests/letta/ -v

# Research pipeline drift detection (41 tests)
uv run pytest tests/unit/test_research_pipeline_drift.py -v

# Full suite (~15-20 min)
uv run pytest tests/ -q --ignore=tests/unit/test_zai_llm_connectivity.py
```

## Test Isolation

The root `conftest.py` provides an autouse fixture that resets the workspace `ContextVar` between every test. This prevents the most common class of ordering-dependent failures.

See [docs/03_Operations/121_Test_Strategy_And_Regression_Prevention_2026-04-20.md](../docs/03_Operations/121_Test_Strategy_And_Regression_Prevention_2026-04-20.md) for the full testing strategy, common failure patterns, and maintenance guidelines.

## CI Integration

The CI/CD pipeline currently does not include a test gate. Tests must be run **manually before `/ship`**. Adding an automated test step to `.github/workflows/deploy.yml` is a recommended future improvement.
