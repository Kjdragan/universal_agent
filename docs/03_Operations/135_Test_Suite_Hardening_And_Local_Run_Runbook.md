# 135 — Test Suite Hardening & "Run Tests Locally Without Stalling" Runbook

**Last updated:** 2026-05-30
**Status:** Living. PR1 (fail-fast), PR2 (unit DB-I/O isolation) and PR3 (fix the `service` fixture) all shipped 2026-05-30. Socket-block split to a measured follow-up; `-n auto` deferred (see §4).
**Owner area:** test infrastructure (`pyproject.toml [tool.pytest.ini_options]`, `tests/`, `.github/workflows/pr-validate.yml`, `justfile`)

> TL;DR for a hurry: **never background a bare `pytest -q` on the full suite.** Run `just test-fast` (scoped to your change), which adds a 60s per-test fail-fast timeout. If a run ever stalls, the timeout now dumps a thread traceback naming the exact blocking call instead of hanging silently.

---

## 1. What happened (2026-05-30)

`uv run pytest tests/unit/ -q` was launched in the background by an agent session and **hung for 38 minutes with zero output** before being killed. `pytest tests/unit --collect-only` alone took ~120s and collected ~4530 tests. CI (`pr-validate.yml`) had run the *same* `pytest tests/unit` clean in ~5m18s — so this was **not** a real assertion failure; it was environment-dependent.

At the time:
- **pytest-timeout was not installed** → a hung test ran until externally killed.
- **No global timeout** existed in `pyproject.toml` or the CI invocation.
- `-q` output buffered under `run_in_background` makes a hung run indistinguishable from a slow one.
- Multiple concurrent agent sessions each spawn full-suite runs, compounding host load.

## 2. Root cause — two layers

This was **not** a single infinite-hang test. It was a slow class of test amplified by host saturation, with no fail-fast to convert the stall into a signal.

### Layer 1 (trigger): "unit" tests do real, fsync-bearing SQLite I/O

`faulthandler` pinned the exact blocking frame:

```
test_status_fields            tests/unit/test_agentmail_service.py:425
  → AgentMailService.status()           src/universal_agent/services/agentmail_service.py:3752
  → _trusted_queue_overview()           src/universal_agent/services/agentmail_service.py:2506
  → _ensure_queue_schema() → conn.executescript(...)   agentmail_service.py:2419   ← blocked
```

The `service` fixture (`tests/unit/test_agentmail_service.py:90`) **does not redirect `UA_ACTIVITY_DB_PATH`** — unlike its sibling `service_with_queue` (`:113`) which does `monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity_state.db"))`. So every `service`-fixture test that reaches the queue/status code path runs **write-DDL against the real ~13 MB `AGENT_RUN_WORKSPACES/activity_state.db`**.

`connect_runtime_db` (`src/universal_agent/durable/db.py:88`) issues **fsync-bearing PRAGMAs on every connection**:
- `PRAGMA auto_vacuum=INCREMENTAL` (`db.py:112`)
- `PRAGMA journal_mode=WAL` (`db.py:114`)

with a 15s `busy_timeout` (`db.py:9`, `DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 15000`).

### Layer 2 (amplifier): host saturation

The desktop is shared by the live `just dev` stack **and multiple concurrent Claude Code agent sessions**. During reproduction the host sat at **load ~110 on 8 cores** (~14× oversubscribed; 64 python/uv/node processes).

Isolated measurement (no pytest, fresh **empty** tmp DB, nothing contending):

| Operation | Run 1 | Run 2 |
|---|---|---|
| `sqlite3.connect()` | 0.000s | 0.000s |
| `PRAGMA foreign_keys=ON` | 0.000s | 0.000s |
| `PRAGMA auto_vacuum=INCREMENTAL` | **5.116s** | **2.960s** |
| `PRAGMA journal_mode=WAL` | **5.345s** | **5.667s** |
| `CREATE TABLE` DDL | 1.523s | 4.534s |

The cost lands **non-deterministically on exactly the two fsync PRAGMAs** — the signature of CPU/IO starvation, not a code path. On a quiet host (and in CI) these are sub-millisecond, which is why CI passes in ~5 minutes.

### Why 38 minutes, zero output

4499 tests × per-test fsync ops × host saturation × **no fail-fast timeout** × `-q` buffered under `run_in_background`. For the `service`-fixture tests, multiple sessions writing the **same** real `activity_state.db` add SQLite write-lock contention (15s busy_timeout each) on top.

## 3. Setup inventory (as of 2026-05-30, pre-hardening)

| Item | Value |
|---|---|
| `tests/unit` files | 382 |
| Collected (whole `tests/`) | 4502 items / 3 deselected (the `llm` mark) / 1 skipped / 4499 selected |
| `asyncio_mode` | `auto` (pytest-asyncio `1.4.0a1`) |
| `addopts` | `-m 'not llm'` |
| Markers registered | `llm` (pyproject); `slow`, `integration`, `e2e` (root `tests/conftest.py:14-16`) |
| Markers actually applied in `tests/unit` | `asyncio`×289, `parametrize`×53, `anyio`×39, `skip`×9, `llm`×3 — **`slow`/`integration`/`network` are registered but never applied** |
| `tests/unit/conftest.py` | **does not exist** — `tests/unit` inherits only root `tests/conftest.py`, which has **no socket/network guard** |
| pytest-timeout / pytest-socket / pytest-xdist | **none installed** (pre-PR1) |
| CI invocation (`pr-validate.yml`) | `uv run pytest tests/unit -x -q --no-header`, 15-min job cap, on `ubuntu-latest` with `uv sync --frozen` and **no Infisical creds / no `.env`** |

The CI-vs-local difference is the env-dependence: CI has no creds and a dedicated, unloaded host, so fsync is fast and any credentialed network call fails fast; locally the operator has creds, the live stack, a real `activity_state.db`, and a saturated host.

## 4. Remediation plan

| PR | Concern | Status |
|---|---|---|
| **PR1** | Fail-fast: `pytest-timeout` (global `timeout=60`, `timeout_method=thread`) in `pyproject.toml` — CI (`pr-validate.yml` runs `uv run pytest tests/unit`) inherits this from the ini, so a CI hang now fails fast at 60s too; `just test-fast` recipe; this runbook | **Shipped 2026-05-30** |
| **PR2** | Isolate unit I/O at the source: `tests/unit/conftest.py` autouse fixture redirecting **all 10** UA SQLite DB-path env vars (`UA_ACTIVITY_DB_PATH`, `UA_RUNTIME_DB_PATH`, `UA_VP_DB_PATH`, `UA_CODER_VP_DB_PATH`, `UA_DB_PATH`, `CSI_DB_PATH`, `UA_MISSION_CONTROL_INTEL_DB_PATH`, `UA_MISSION_CONTROL_COS_DB_PATH`, `UA_LOSSLESS_DB_PATH`, `UA_FACTORY_REGISTRY_DB_PATH`) to a per-test `tmp_path/AGENT_RUN_WORKSPACES/` dir + `UA_SQLITE_BUSY_TIMEOUT_MS=250`; `@pytest.mark.no_db_redirect` opt-out. Test-only, no prod code change. | **Shipped 2026-05-30** |
| **PR3** | Fix the specific offender: give the `service` fixture the same tmp-DB redirect as `service_with_queue` | **Shipped 2026-05-30** (PR #617) |

**Socket-block split to a measured follow-up (not in PR2).** `pytest-socket` `--disable-socket` is valuable but surfaces previously-hidden real-network "unit" tests as *failures* — it needs a full-suite breakage census before landing, and each offender must be fixed or quarantined-with-reason (not mass-disabled). That census is impractical to run cleanly while the host sits at load ~100, and the *proven* root cause of the hang is DB I/O, not network — which PR2's DB isolation already addresses. Tracked as a follow-up; do it on an unloaded host so the breakage count is trustworthy.

**Deliberately deferred — `pytest-xdist -n auto`.** The host is already oversubscribed by concurrent sessions; adding intra-suite parallelism worsens load. Per-test `tmp_path` DB isolation is now in place (PR2), but `-n auto` is only worth revisiting **after** cross-session concurrency is controlled.

### Why `timeout_method = "thread"` (not the default `signal`)

This is an asyncio-heavy suite (`asyncio_mode=auto`, `pytest-asyncio`). pytest-timeout's default `signal` method uses `SIGALRM`, which only fires on the **main thread** and misbehaves when work runs off the event loop's thread. The `thread` method runs a watchdog thread that dumps every thread's traceback and fails the test — it works regardless of where the block happens. Verified: a synthetic `time.sleep(120)` test aborts at the configured limit with a traceback naming the exact line.

## 5. Runbook — run tests locally without stalling

**Do:**

```bash
# Scoped, fail-fast, no cache plugin — the default local loop.
# The first positional arg is the TARGET path (it REPLACES the default
# tests/unit); anything after is extra pytest flags.
just test-fast                                       # whole unit suite, with --timeout=60
just test-fast tests/unit/test_loop_control.py       # one file
just test-fast tests/unit -k agentmail               # keyword filter within unit

# Reproduce exactly what CI sees (strips operator .env feature-gate vars):
just test-ci-env

# Pre-ship gate (sync + pr-scope lint + ci-env tests + canvas verify):
just preship
```

**Don't:**

- ❌ **Don't background a bare `pytest -q` on the full suite.** Output buffers until the end, so a hang looks like slowness, and N concurrent full-suite runs saturate the host. If you must background, keep `--timeout=60` and prefer `-rf`/`-v` so progress and failures are visible.
- ❌ Don't run several full-suite sessions at once. Check `uptime` first — if load already exceeds core count (`nproc`), scope your run to the changed area.

**If a run still stalls:** the global `timeout=60` now aborts the offending test and prints a thread traceback naming the blocking call (`pyproject.toml [tool.pytest.ini_options] timeout/timeout_method`). Read the top user frame — that's the test and the exact line. To get a dump *before* the 60s limit while investigating, add `-o faulthandler_timeout=20` (built into pytest, no plugin needed).

**To diagnose a suspected real-network/real-DB "unit" test:** confirm it redirects DB paths to `tmp_path` (grep the fixture for `UA_ACTIVITY_DB_PATH` / `UA_RUNTIME_DB_PATH`) and mocks any SDK/HTTP client. The canonical safe pattern is `service_with_queue` in `tests/unit/test_agentmail_service.py:110`.

## 6. Code citations

- `src/universal_agent/durable/db.py:88` — `connect_runtime_db` (fsync PRAGMAs, busy_timeout)
- `src/universal_agent/durable/db.py:9` — `DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 15000`
- `src/universal_agent/durable/db.py:69` — `get_activity_db_path` (default `AGENT_RUN_WORKSPACES/activity_state.db`)
- `src/universal_agent/services/agentmail_service.py:2417` — `_ensure_queue_schema`
- `tests/unit/test_agentmail_service.py:90` — `service` fixture (no DB redirect — PR3 target)
- `tests/unit/test_agentmail_service.py:110` — `service_with_queue` fixture (correct pattern)
- `pyproject.toml [tool.pytest.ini_options]` — `timeout`, `timeout_method`
- `.github/workflows/pr-validate.yml` — "Run unit tests" step (`uv run pytest tests/unit`; inherits `timeout=60` from the pyproject ini — no explicit flag needed)
- `justfile` — `test-fast`, `test-one`, `test-ci-env`, `preship`
