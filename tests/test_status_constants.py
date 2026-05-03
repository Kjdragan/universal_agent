"""Regression tests for run-status string constants.

Background: PR #141 (May 2026) extracted run-status magic strings into named
constants, but silently changed the value of one constant from
``"waiting_for_human"`` to ``"waiting_on_human"``. The rest of the codebase
(``main.py`` writers, ``run_workspace.py`` filters, ``urw/orchestrator.py``)
still reads/writes ``"waiting_for_human"`` to the runtime SQLite DB, so the
admission set membership check stopped recognizing in-flight human-gate runs.

These tests pin the string VALUES (not just the constant names) so a future
"magic-string refactor" cannot silently re-introduce the same drift.
"""

from universal_agent.services.dag_runner import (
    STATUS_WAITING_ON_HUMAN as DAG_STATUS_WAITING_ON_HUMAN,
)
from universal_agent.workflow_admission import (
    STATUS_WAITING_ON_HUMAN as ADMISSION_STATUS_WAITING_ON_HUMAN,
)


def test_workflow_admission_waiting_status_value_is_db_compatible():
    assert ADMISSION_STATUS_WAITING_ON_HUMAN == "waiting_for_human"


def test_dag_runner_waiting_status_value_is_db_compatible():
    assert DAG_STATUS_WAITING_ON_HUMAN == "waiting_for_human"


def test_admission_and_dag_waiting_constants_agree():
    assert ADMISSION_STATUS_WAITING_ON_HUMAN == DAG_STATUS_WAITING_ON_HUMAN
