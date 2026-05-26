"""Regression guard for the nightly_wiki_agent cron's DB-path choice.

Background: proactive_signal_cards lives in activity_state.db, NOT
runtime_state.db. The dashboard "Create Wiki" button + gateway endpoint
write there. Before this fix, nightly_wiki_agent.py read runtime_state.db
and silently saw zero pending cards while the dashboard showed dozens.

Same root-cause pattern as the May-20 watchdog incident
(PRs #389/#390/#392/#396 fixed four invariants pointing at runtime_conn
when the data lived in activity_conn). The nightly_wiki_agent callsite
was missed and only caught on 2026-05-24 during the Knowledge Vault
explainer investigation.

These tests are static-source assertions. They are cheap, deterministic,
and catch the exact regression mode (someone swaps the import back).
"""

from __future__ import annotations

from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "universal_agent"
    / "scripts"
    / "nightly_wiki_agent.py"
)


def _script_source() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def test_nightly_wiki_imports_activity_db_path() -> None:
    """The cron must import get_activity_db_path, not get_runtime_db_path,
    because proactive_signal_cards rows are written to activity_state.db
    by the dashboard endpoint and the Create Wiki button.
    """
    source = _script_source()
    assert "get_activity_db_path" in source, (
        "nightly_wiki_agent must import get_activity_db_path — "
        "proactive_signal_cards lives in activity_state.db"
    )
    assert "from universal_agent.durable.db import" in source
    # The wrong import must NOT be present. If someone reintroduces it,
    # the cron will silently read zero pending cards again.
    import_line = next(
        line for line in source.splitlines()
        if "from universal_agent.durable.db import" in line
    )
    assert "get_runtime_db_path" not in import_line, (
        "nightly_wiki_agent must NOT import get_runtime_db_path — "
        "that points at runtime_state.db where pending cards do not live"
    )


def test_nightly_wiki_calls_get_activity_db_path() -> None:
    """The actual call site must use get_activity_db_path()."""
    source = _script_source()
    assert "db_path = get_activity_db_path()" in source, (
        "nightly_wiki_agent must call get_activity_db_path() to resolve "
        "the proactive_signal_cards database"
    )


def test_nightly_wiki_documents_db_path_rationale() -> None:
    """A comment near the connection call must explain why activity_state.db
    is the right DB. The May-20 watchdog incident showed this footgun is
    easy to re-introduce when the explanatory comment is missing.
    """
    source = _script_source()
    assert "activity_state.db" in source, (
        "nightly_wiki_agent must document the activity_state.db choice "
        "in a comment near the connection call so future edits do not "
        "re-introduce the runtime_state.db regression"
    )
