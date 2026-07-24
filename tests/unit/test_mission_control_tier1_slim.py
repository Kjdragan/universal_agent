"""Tier-1 evidence bundle stays lean: dedup raw *_json twins + cap big fields.

Guards the ZAI-cost fix — full untruncated bundles made tier-1 discovery the
largest ZAI token sink (~150k tokens/call) and a 429 driver.
"""

import json
import sqlite3

from universal_agent.services import mission_control_tier1 as t1


def _row(**cols) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    keys = ", ".join(cols)
    qs = ", ".join("?" for _ in cols)
    conn.execute(f"CREATE TABLE r ({', '.join(f'{k} TEXT' for k in cols)})")
    conn.execute(f"INSERT INTO r ({keys}) VALUES ({qs})", tuple(cols.values()))
    return conn.execute("SELECT * FROM r").fetchone()


def test_raw_json_twin_is_dropped_after_parse():
    row = _row(task_id="t1", metadata_json=json.dumps({"a": 1, "b": 2}))
    out = t1._row_to_dict(row)
    assert "metadata_json" not in out, "raw _json string must be dropped (dedup)"
    assert out["metadata"] == {"a": 1, "b": 2}, "parsed twin must survive"
    assert out["task_id"] == "t1"


def test_unparseable_json_column_is_kept():
    row = _row(task_id="t1", metadata_json="{not valid json")
    out = t1._row_to_dict(row)
    # Only copy — must be preserved (capped, but present).
    assert "metadata_json" in out


def test_long_description_is_capped():
    big = "x" * 50_000
    row = _row(task_id="t1", description=big)
    out = t1._row_to_dict(row)
    assert len(out["description"]) < len(big)
    assert out["description"].endswith("chars]")
    assert len(out["description"]) <= t1._STR_FIELD_CAP + 40


def test_short_identity_fields_untouched():
    # Signature-relevant fields are short and must pass through verbatim so
    # evidence_signature() (delta-gating) is unaffected.
    row = _row(task_id="t1", status="open", updated_at="2026-07-24T00:00:00Z")
    out = t1._row_to_dict(row)
    assert out == {"task_id": "t1", "status": "open", "updated_at": "2026-07-24T00:00:00Z"}


def test_big_nested_json_becomes_bounded_preview():
    row = _row(task_id="t1", metadata_json=json.dumps({"k": "y" * 5000}))
    out = t1._row_to_dict(row)
    val = out["metadata"]
    # Oversized nested JSON collapses to a truncated string preview.
    assert isinstance(val, str)
    assert len(val) <= t1._JSON_FIELD_CAP + 60
