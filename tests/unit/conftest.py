"""Autouse isolation for the unit-test partition.

Root cause of the 2026-05-30 38-minute `pytest tests/unit` hang: a "unit"
test ran fsync-bearing SQLite DDL against the REAL
``AGENT_RUN_WORKSPACES/activity_state.db`` (via a fixture that omitted the
``UA_ACTIVITY_DB_PATH`` tmp redirect), and on a host saturated by concurrent
agent sessions each fsync stalled for seconds. See
``docs/03_Operations/135_Test_Suite_Hardening_And_Local_Run_Runbook.md``.

This autouse fixture is the systemic backstop: it points every UA SQLite
DB-path env var at a per-test tmp directory so **no unit test can ever touch a
real on-disk DB**, regardless of whether the test sets up its own redirect.
It also lowers the SQLite busy-timeout so a contended lock fails fast in tests
instead of waiting the 15s production default.

Notes
-----
- The DBs live under ``<tmp>/ua_test_dbs/AGENT_RUN_WORKSPACES/`` — nested so we
  don't occupy ``<tmp>/AGENT_RUN_WORKSPACES`` (which some tests ``mkdir``
  themselves), while still keeping the ``AGENT_RUN_WORKSPACES`` component so the
  rare test asserting ``"AGENT_RUN_WORKSPACES" in <db path>`` keeps passing.
- Guardrail tests that verify *default* (unset-env) resolution already call
  ``monkeypatch.delenv(...)`` themselves; because ``delenv`` removes the key
  from ``os.environ``, it cleanly undoes this redirect within those tests, so
  they still test the genuine default (see
  ``tests/unit/test_runtime_db_path_guardrails.py``). A test that genuinely
  needs the real default without delenv'ing can opt out with
  ``@pytest.mark.no_db_redirect``.
- A test that wants its own DB path still wins: its ``monkeypatch.setenv``
  runs after this fixture and overrides the redirect (same tmp-dir intent).
"""

from __future__ import annotations

import pytest

# Every UA SQLite DB-path env var resolved anywhere in src/ (enumerated via
# grep of getenv/environ usages). Keep in sync if a new *_DB_PATH var is added.
_DB_PATH_ENV_VARS = (
    "UA_ACTIVITY_DB_PATH",
    "UA_RUNTIME_DB_PATH",
    "UA_VP_DB_PATH",
    "UA_CODER_VP_DB_PATH",
    "UA_DB_PATH",
    "CSI_DB_PATH",
    "UA_MISSION_CONTROL_INTEL_DB_PATH",
    "UA_MISSION_CONTROL_COS_DB_PATH",
    "UA_LOSSLESS_DB_PATH",
    "UA_FACTORY_REGISTRY_DB_PATH",
)

# Filename to use for each env var (cosmetic — keeps tmp dirs readable and
# avoids cross-var collisions if any code opens two of them at once).
_DB_FILENAMES = {
    "UA_ACTIVITY_DB_PATH": "activity_state.db",
    "UA_RUNTIME_DB_PATH": "runtime_state.db",
    "UA_VP_DB_PATH": "vp_state.db",
    "UA_CODER_VP_DB_PATH": "coder_vp_state.db",
    "UA_DB_PATH": "ua_state.db",
    "CSI_DB_PATH": "csi_state.db",
    "UA_MISSION_CONTROL_INTEL_DB_PATH": "mission_control_intel.db",
    "UA_MISSION_CONTROL_COS_DB_PATH": "mission_control_cos.db",
    "UA_LOSSLESS_DB_PATH": "lossless_memory.db",
    "UA_FACTORY_REGISTRY_DB_PATH": "factory_registry.db",
}


@pytest.fixture(autouse=True)
def _isolate_unit_test_databases(request, monkeypatch, tmp_path):
    """Redirect all UA SQLite DB paths to a per-test tmp dir; fail-fast locks.

    Opt out with ``@pytest.mark.no_db_redirect`` for the rare test that must
    observe the real default path without calling ``monkeypatch.delenv``.
    """
    # Intel triage (write_convergence_candidate) makes a live LLM call by
    # default. Force it OFF for the whole unit suite so no test accidentally
    # hits the network — same "no real resources in unit tests" backstop as the
    # DB redirect below. Triage's own tests opt back in via
    # monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED","1") + a mocked LLM.
    monkeypatch.setenv("UA_INTEL_TRIAGE_ENABLED", "0")

    if request.node.get_closest_marker("no_db_redirect") is not None:
        # Still cap the busy-timeout so even an opted-out test can't wait 15s
        # on a lock; the redirect of DB paths is what we skip.
        monkeypatch.setenv("UA_SQLITE_BUSY_TIMEOUT_MS", "250")
        return

    # Nest under a unique subdir so we don't occupy `tmp_path/AGENT_RUN_WORKSPACES`
    # itself — some tests `mkdir()` that exact path (without exist_ok) and would
    # hit FileExistsError if we pre-created it. The trailing AGENT_RUN_WORKSPACES
    # component is kept so the rare test asserting it appears in a redirected DB
    # path still passes.
    db_dir = tmp_path / "ua_test_dbs" / "AGENT_RUN_WORKSPACES"
    db_dir.mkdir(parents=True, exist_ok=True)
    for var in _DB_PATH_ENV_VARS:
        monkeypatch.setenv(var, str(db_dir / _DB_FILENAMES[var]))

    # Isolate the recent-briefs index file per test. It resolves to a single
    # shared default path (``$AGENT_RUN_WORKSPACES/recent_briefs_index.md`` or
    # repo ``artifacts/``) — under parallel xdist, concurrent read/rebuild/write
    # of that one file corrupts it, and the convergence sync's try/except then
    # swallows the error → candidates silently dropped. Per-test path fixes it.
    monkeypatch.setenv("UA_RECENT_BRIEFS_INDEX_PATH", str(db_dir / "recent_briefs_index.md"))

    # A contended lock should fail fast in tests, not block for the 15s prod
    # default. Tests that exercise the busy-timeout itself set/delenv this var
    # themselves (their value wins / restores the default).
    monkeypatch.setenv("UA_SQLITE_BUSY_TIMEOUT_MS", "250")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_db_redirect: opt out of the autouse DB-path tmp redirect "
        "(tests/unit/conftest.py) to observe real default path resolution",
    )
