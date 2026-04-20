# 121. Test Strategy and Regression Prevention (2026-04-20)

> **Purpose**: Canonical source of truth for the Universal Agent test strategy — suite layout, recommended pre-ship commands, ContextVar isolation architecture, known test categories, and a regression prevention playbook.
>
> Written after the April 20, 2026 stabilization pass that brought the unit test suite from **21 failures to 0 failures** (1211 tests passing) by fixing stale assertions, deleting obsolete tests, and resolving systemic ContextVar state leakage.

---

## 1. Test Suite Layout

```
tests/                           # 301 test files total
├── conftest.py                  # Root conftest — Logfire suppression + ContextVar isolation
├── unit/                        # 156 files · 1211 tests · ~7 min · THE PRIMARY SAFETY NET
│   ├── test_hooks_*.py          # Hook guardrails (YouTube, workspace, research injection)
│   ├── test_tool_schema_*.py    # Tool schema guardrails (policy-mode, routing, normalization)
│   ├── test_task_hub_*.py       # Task Hub lifecycle, schema extensions, bridge actions
│   ├── test_heartbeat_*.py      # Heartbeat service (retry queue, env context)
│   ├── test_proactive_*.py      # Proactive signals pipeline (cards, feedback, sync)
│   ├── test_workspace_*.py      # Workspace env aliases, inspector tool
│   └── ...                      # AgentMail, dashboard, durable, VP, crash hooks, etc.
│
├── gateway/                     # 52 files · Gateway server integration tests
│   ├── test_todo_pipeline_*.py  # ToDo dispatch lifecycle
│   ├── test_cron_*.py           # Cron scheduler, notifications, API
│   ├── test_heartbeat_*.py      # Heartbeat delivery policy, timeout, seeding
│   └── ...                      # Env sanitization, continuity metrics, ops API
│
├── stabilization/               # 3 files · Smoke tests for Direct vs Gateway parity
├── durable/                     # 7 files · Durable state, ledger, persistence
├── integration/                 # 10 files · E2E flows (Composio, Web UI, workspace)
├── memory/                      # 14 files · Letta memory system
├── api/                         # 9 files · API endpoint tests
├── delegation/                  # 5 files · VP delegation
├── discord/                     # 2 files · Discord intelligence
├── letta/                       # 10 files · Letta subsystem
├── bot/                         # 2 files · Bot tests
├── contract/                    # 1 file  · Contract tests
├── skills/                      # 2 files · Skill tests
└── reproduction/                # 1 file  · Bug reproductions
```

## 2. Commands You Should Run

### 2.1 Before Every Ship (MANDATORY)

The unit test suite is the primary regression gate. It runs 1211 tests in ~7 minutes and catches the majority of architectural drift, stale assertions, and state leakage issues.

```bash
# THE command. Run this before every /ship.
uv run pytest tests/unit/ -q --ignore=tests/unit/test_zai_llm_connectivity.py
```

> [!IMPORTANT]
> `test_zai_llm_connectivity.py` is excluded because it requires live API tokens (ZAI/Anthropic). It tests external connectivity, not our code. Include it when you've refreshed those tokens.

**Expected output**: `1211 passed` with 0 failures.

If any test fails, **stop and fix it before shipping**. The `/ship` workflow should never deploy a red suite.

### 2.2 After Touching Specific Subsystems

| What you changed | What to run |
|---|---|
| `hooks.py`, `constants.py`, `.claude/agents/*.md` | `uv run pytest tests/unit/test_research_pipeline_drift.py tests/unit/test_hooks_youtube_guardrail.py -v` |
| `guardrails/tool_schema.py` | `uv run pytest tests/unit/test_tool_schema_guardrail.py -v` |
| `task_hub.py`, `todo_dispatch_service.py` | `uv run pytest tests/unit/test_task_hub_*.py -v` |
| `heartbeat_service.py` | `uv run pytest tests/unit/test_heartbeat_*.py -v` |
| `execution_context.py` | `uv run pytest tests/unit/test_workspace_*.py -v` |
| `gateway_server.py` | `uv run pytest tests/gateway/ -x -vv` |
| `proactive_signals.py` | `uv run pytest tests/unit/test_proactive_signals.py -v` |

### 2.3 Full Suite (Weekly / After Major Refactors)

```bash
# Unit + Gateway + everything else (~15-20 min)
uv run pytest tests/ -q --ignore=tests/unit/test_zai_llm_connectivity.py
```

### 2.4 Research Pipeline Smoke Test

After any change to the research/report pipeline, verify the golden run drift tests:

```bash
uv run pytest tests/unit/test_research_pipeline_drift.py -v
```

See [003_REGRESSION_CONTROL_AND_GOLDEN_RUNS.md](../003_REGRESSION_CONTROL_AND_GOLDEN_RUNS.md) for the full golden run definition and recovery procedure.

## 3. Test Isolation Architecture

### 3.1 The ContextVar Problem

The single most common cause of test ordering failures in this codebase is `ContextVar` state leakage. The `_WORKSPACE_CONTEXT_VAR` in `execution_context.py` is set by tests that call `bind_workspace_env()` or `workspace_context()`, and the value persists across synchronous test functions in the same process.

**Symptom**: Tests pass individually (`uv run pytest tests/unit/test_workspace_env_aliases.py`) but fail in the full suite.

**Root cause**: A prior test (e.g., `test_toggle_session_creates_run`) sets `_WORKSPACE_CONTEXT_VAR` to `/tmp/.../run_phase_2`, and the ContextVar leaks to `test_get_current_workspace_prefers_current_run_workspace` which expects `/run_123`.

**Fix (already deployed)**: The root `tests/conftest.py` contains an `autouse` fixture:

```python
@pytest.fixture(autouse=True)
def _reset_workspace_context():
    from universal_agent.execution_context import _WORKSPACE_CONTEXT_VAR
    token = _WORKSPACE_CONTEXT_VAR.set(None)
    yield
    _WORKSPACE_CONTEXT_VAR.reset(token)
```

This resets the ContextVar before and after every test, preventing cross-test contamination.

### 3.2 Monkeypatch Best Practices

When patching module attributes in tests, **always use module-reference patching** rather than string-based patching:

```python
# ✅ GOOD — survives sys.modules inconsistencies
from universal_agent.tools import task_hub_bridge
monkeypatch.setattr(task_hub_bridge, "get_activity_db_path", lambda: db_path)

# ❌ BAD — fails with AttributeError if module traversal breaks
monkeypatch.setattr("universal_agent.tools.task_hub_bridge.get_activity_db_path", lambda: db_path)
```

String-based patching traverses the module hierarchy at runtime, which can fail when earlier tests have corrupted `sys.modules`.

### 3.3 SessionContext for Database Tests

Tests that interact with SQLite databases should use isolated `tmp_path` directories and explicit `UA_ACTIVITY_DB_PATH` environment variables:

```python
def test_something(tmp_path, monkeypatch):
    db_path = str(tmp_path / "activity.db")
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    # ... test logic ...
```

Never use shared or fixed database paths in tests.

## 4. Common Failure Patterns and Fixes

| Pattern | Symptom | Fix |
|---|---|---|
| **ContextVar leakage** | Test passes alone, fails in suite; assertion gets wrong workspace path | Ensure `_reset_workspace_context` fixture is active (in `tests/conftest.py`) |
| **Stale architectural assertion** | Test asserts old dispatch behavior (e.g., heartbeat claims tasks) | Verify current architecture in source code, update assertion |
| **`_strip_heredoc_bodies` evolution** | YouTube bash guard returns `{}` instead of `{"decision":"block"}` | Check if markers are inside a `python -c` body (stripped by design) |
| **Idempotency assertion** | `sync_*` function returns 0 on second call, test expects ≥1 | Second call is idempotent — assert `== 0` for repeated calls |
| **Module traversal failure** | `AttributeError: module has no attribute 'tools'` | Use module-ref monkeypatch instead of string-based |
| **SQLite locking** | `database is locked` during parallel tests | Use `tmp_path` isolation, never share DB files across tests |

## 5. Test Maintenance Checklist

### When Adding New Tests

1. Use `tmp_path` for any filesystem or database state
2. Use `monkeypatch.setenv` for environment variables (auto-cleaned by pytest)
3. Never set `os.environ` directly — it leaks
4. If your test calls `bind_workspace_env()`, the conftest autouse fixture will clean up
5. For async tests, use `@pytest.mark.anyio` (preferred) or `@pytest.mark.asyncio`

### When Deleting Tests

Before deleting, verify that the test is truly stale:

1. Check the source code the test was guarding — has the feature been removed or refactored?
2. If the feature still exists but the interface changed, **update the test** rather than delete
3. Document what you deleted and why in the commit message

### When Refactoring Architecture

If you move dispatch logic, change hook signatures, or refactor module structure:

1. Run the full unit suite **before** your change to establish a baseline
2. Make your change
3. Run the full unit suite **after** and fix every failure
4. Every failure you fix is a regression you prevented from reaching production

## 6. CI/CD Integration Gap

> [!WARNING]
> The current CI/CD pipeline (`.github/workflows/deploy.yml`) does **not** run tests before deploying. The deploy workflow goes straight from `git pull` → `service restart` without any test gate.

### Current State

- Tests are run **manually** before `/ship` by the developer
- There is no automated test gate that blocks a bad deploy

### Recommended Future Improvement

Add a `test` job that runs before `deploy-production` in the workflow:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run pytest tests/unit/ -q --ignore=tests/unit/test_zai_llm_connectivity.py

  deploy-production:
    needs: test
    # ... existing deploy steps
```

Until this is implemented, the **manual pre-ship test run is the only safety net**. Treat it as mandatory.

## 7. Test Categories Reference

### Tests That Require External Services

| Test | Service | Skip When |
|---|---|---|
| `test_zai_llm_connectivity.py` | ZAI/Anthropic API | API tokens expired |
| `tests/integration/` | Various external services | Running offline |
| `tests/gateway/` | Gateway subprocess | Quick iteration only |

### Tests That Are Timing-Sensitive

| Test | Concern |
|---|---|
| `test_heartbeat_exec_timeout.py` | Real async sleep timing |
| `test_cron_scheduler.py` | Cron interval edge cases |
| Gateway subprocess tests | Startup latency variance |

### Tests With Autouse Fixtures (Automatic)

| Fixture | Scope | Purpose |
|---|---|---|
| `_reset_workspace_context` | Every test (root conftest) | Prevents ContextVar leakage |
| `_enable_strict_policy_guardrails_for_legacy_contract` | `test_tool_schema_guardrail.py` only | Forces strict guardrail mode |

## 8. Stabilization History

### April 20, 2026 — Unit Suite Stabilization

**Before**: 21 failures across the unit suite.

**Root causes identified and fixed**:

| Category | Count | Fix |
|---|---|---|
| ContextVar state leakage | 10 | Autouse fixture in `tests/conftest.py` |
| Stale architectural assertions | 4 | Updated to match current dispatch architecture |
| Obsolete tests (deleted infrastructure) | 3 | Deleted `test_heartbeat_task_hub_claims.py`, `test_csi_dashboard_deeplinks.py` |
| `_strip_heredoc_bodies` refinement | 2 | Updated YouTube bash guardrail expectations |
| Idempotency assertion error | 1 | Fixed `sync_topic_signatures` repeated call assertion |
| Module traversal in monkeypatch | 1 | Switched to module-ref patching |

**After**: 1211 passed, 0 failed.

### April 6, 2026 — Gateway Test Hardening

See [108_Gateway_Test_Hardening](108_Gateway_Test_Hardening_And_Regression_Followup_2026-04-06.md) for the gateway-specific regression pass.

### February 23, 2026 — Golden Run Baseline

See [003_REGRESSION_CONTROL_AND_GOLDEN_RUNS](../003_REGRESSION_CONTROL_AND_GOLDEN_RUNS.md) for the original pipeline drift incident and the 41-test regression suite.

## 9. Cross-References

| Topic | Document |
|---|---|
| Research pipeline golden run | [003_REGRESSION_CONTROL_AND_GOLDEN_RUNS.md](../003_REGRESSION_CONTROL_AND_GOLDEN_RUNS.md) |
| Gateway test hardening | [108_Gateway_Test_Hardening](108_Gateway_Test_Hardening_And_Regression_Followup_2026-04-06.md) |
| CI/CD pipeline | [deployment/ci_cd_pipeline.md](../deployment/ci_cd_pipeline.md) |
| SDK permissions & hooks | [002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md](../002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md) |
| Execution context module | `src/universal_agent/execution_context.py` |
| Root test conftest | `tests/conftest.py` |
