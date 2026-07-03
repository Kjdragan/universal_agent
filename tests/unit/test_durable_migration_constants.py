"""Regression tests for ``universal_agent.durable.migrations`` column-type
constants.

The column-type strings passed to ``_add_column_if_missing`` during additive
migrations were extracted into named ``_COL_*`` constants. These args are only
emitted as real DDL when migrating a PRE-EXISTING database that predates the
column — on a fresh database ``SCHEMA_SQL`` already defines every column, so
``_add_column_if_missing`` no-ops and a typo in a constant would never surface
in a fresh-DB schema test. These tests pin the constants and record the
``column_type`` each additive call passes, so a mistyped/mis-defaulted constant
fails here instead of silently creating a bad column on legacy databases.
"""

from __future__ import annotations

import sqlite3
from unittest import mock

from universal_agent.durable import migrations as M

# Sentinel columns + the exact DDL fragment their additive migration must pass.
# Keyed by column name; every key is a unique column in an _add_column call.
EXPECTED_COLUMN_TYPES = {
    "attempt_count": "INTEGER DEFAULT 0",
    "iteration_count": "INTEGER DEFAULT 0",
    "total_tokens": "INTEGER DEFAULT 0",
    "cancel_requested": "INTEGER DEFAULT 0",
    "result_published": "INTEGER DEFAULT 0",
    "priority": "INTEGER DEFAULT 100",
    "max_iterations": "INTEGER",
    "policy_matched": "INTEGER",
    "replay_policy": "TEXT NOT NULL DEFAULT 'REPLAY_EXACT'",
    "priority_tier": "TEXT NOT NULL DEFAULT 'background'",
    "source": "TEXT DEFAULT 'gateway'",
    "workspace_dir": "TEXT",
}


def test_named_constants_equal_original_literals():
    """Constants must be byte-identical to the inline literals they replaced."""
    assert M._COL_TEXT == "TEXT"
    assert M._COL_INTEGER == "INTEGER"
    assert M._COL_INTEGER_DEFAULT_ZERO == "INTEGER DEFAULT 0"
    assert M._COL_INTEGER_DEFAULT_100 == "INTEGER DEFAULT 100"
    assert M._COL_TEXT_DEFAULT_GATEWAY == "TEXT DEFAULT 'gateway'"
    assert (
        M._COL_TEXT_NOT_NULL_DEFAULT_REPLAY_EXACT
        == "TEXT NOT NULL DEFAULT 'REPLAY_EXACT'"
    )
    assert (
        M._COL_TEXT_NOT_NULL_DEFAULT_BACKGROUND == "TEXT NOT NULL DEFAULT 'background'"
    )


def test_additive_calls_pass_expected_column_types():
    """Run ensure_schema on a real in-memory DB with _add_column_if_missing
    wrapped to record every column_type arg it is passed. The real helper still
    runs (and safely no-ops for columns SCHEMA_SQL already created), so this
    proves each constant resolves to the DDL fragment the column must receive
    when migrating a pre-existing database."""
    recorded: dict[str, str] = {}
    real_add_column = M._add_column_if_missing

    def _recording_add_column(conn, table, column, column_type):  # type: ignore[no-untyped-def]
        recorded[column] = column_type
        return real_add_column(conn, table, column, column_type)

    conn = sqlite3.connect(":memory:")
    with mock.patch.object(M, "_add_column_if_missing", _recording_add_column):
        M.ensure_schema(conn)

    missing = set(EXPECTED_COLUMN_TYPES) - recorded.keys()
    assert not missing, (
        f"expected columns never passed to _add_column_if_missing: {sorted(missing)}"
    )

    for column, expected in EXPECTED_COLUMN_TYPES.items():
        assert recorded[column] == expected, (
            f"column {column!r}: additive migration passed {recorded[column]!r}, "
            f"expected {expected!r}"
        )
