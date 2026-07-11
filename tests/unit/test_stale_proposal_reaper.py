"""Unit tests for stale_proposal_reaper: reapable query, protection gate, digest.

The gate is the non-negotiable safety property: never prune priority>=2 or
human-only items. These tests pin it for eligible / priority / human-only / both.
"""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

from universal_agent import task_hub
from universal_agent.scripts import stale_proposal_reaper as reaper


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _insert(
    conn,
    *,
    task_id,
    title="t",
    source_kind="reflection",
    priority=1,
    labels=None,
    status="open",
    days_ago=0.0,
):
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "title": title,
            "description": "d",
            "source_kind": source_kind,
            "status": status,
            "priority": priority,
            "labels": labels or [],
            "agent_ready": False,
            "trigger_type": "autonomous",
            "created_at": created.isoformat(),
        },
    )


# ---- reapable query (14d default): only old, open, reflection/brainstorm, oldest-first ----

def test_reapable_query_returns_only_old_open_reflection_brainstorm_oldest_first():
    conn = _mem()
    _insert(conn, task_id="old2", source_kind="brainstorm", days_ago=16)
    _insert(conn, task_id="old1", source_kind="reflection", days_ago=20)
    _insert(conn, task_id="young", source_kind="reflection", days_ago=3)  # too young
    _insert(conn, task_id="parked", source_kind="reflection", days_ago=20, status="parked")  # not open
    _insert(conn, task_id="csi", source_kind="csi", days_ago=20)  # wrong source_kind
    out = reaper.get_reapable_proposals(conn)
    ids = [r["task_id"] for r in out]
    assert ids == ["old1", "old2"]  # created_at ASC: 20d before 16d


# ---- gate: prunes eligible ----

def test_reaper_prunes_eligible_proposal_parks_not_deletes():
    conn = _mem()
    _insert(conn, task_id="r1", source_kind="reflection", priority=1, days_ago=20)
    records = reaper.reap_stale_proposals(conn)
    by_id = {r["id"]: r for r in records}
    assert by_id["r1"]["disposition"] == "pruned"
    row = task_hub.get_item(conn, "r1")
    assert row is not None  # NEVER deleted
    assert row["status"] == "parked"  # parked, not deleted


# ---- gate: skips priority>=2 ----

def test_reaper_skips_priority_gte_2():
    conn = _mem()
    _insert(conn, task_id="p2", source_kind="reflection", priority=2, days_ago=20)
    _insert(conn, task_id="p3", source_kind="brainstorm", priority=3, days_ago=20)
    records = reaper.reap_stale_proposals(conn)
    by_id = {r["id"]: r for r in records}
    assert by_id["p2"]["disposition"] == "skipped"
    assert "priority>=2" in by_id["p2"]["reason"]
    assert by_id["p3"]["disposition"] == "skipped"
    assert task_hub.get_item(conn, "p2")["status"] == "open"  # untouched
    assert task_hub.get_item(conn, "p3")["status"] == "open"


# ---- gate: skips human-only label ----

def test_reaper_skips_human_only_label():
    conn = _mem()
    _insert(conn, task_id="h1", source_kind="reflection", priority=1, labels=["human-only"], days_ago=20)
    records = reaper.reap_stale_proposals(conn)
    by_id = {r["id"]: r for r in records}
    assert by_id["h1"]["disposition"] == "skipped"
    assert "human-only" in by_id["h1"]["reason"]
    assert task_hub.get_item(conn, "h1")["status"] == "open"  # untouched


# ---- gate: skips both (priority>=2 AND human-only) ----

def test_reaper_skips_both_protected():
    conn = _mem()
    _insert(conn, task_id="b1", source_kind="reflection", priority=2, labels=["human-only"], days_ago=20)
    records = reaper.reap_stale_proposals(conn)
    by_id = {r["id"]: r for r in records}
    assert by_id["b1"]["disposition"] == "skipped"
    assert "priority>=2" in by_id["b1"]["reason"]
    assert "human-only" in by_id["b1"]["reason"]
    assert task_hub.get_item(conn, "b1")["status"] == "open"  # untouched


# ---- mixed pass: prunes eligible + skips protected in one run ----

def test_reaper_mixed_pass_prunes_and_skips():
    conn = _mem()
    _insert(conn, task_id="ok", source_kind="reflection", priority=1, days_ago=20)
    _insert(conn, task_id="prot", source_kind="reflection", priority=2, days_ago=25)
    _insert(conn, task_id="ho", source_kind="brainstorm", priority=1, labels=["human-only"], days_ago=25)
    records = reaper.reap_stale_proposals(conn)
    by_id = {r["id"]: r for r in records}
    assert by_id["ok"]["disposition"] == "pruned"
    assert by_id["prot"]["disposition"] == "skipped"
    assert by_id["ho"]["disposition"] == "skipped"
    assert task_hub.get_item(conn, "ok")["status"] == "parked"
    assert task_hub.get_item(conn, "prot")["status"] == "open"
    assert task_hub.get_item(conn, "ho")["status"] == "open"


# ---- digest emission: md + json, correct fields ----

def test_digest_emission_md_and_json_correct_fields(tmp_path):
    conn = _mem()
    _insert(conn, task_id="d1", title="Prune me", source_kind="reflection", priority=1, days_ago=20)
    _insert(conn, task_id="d2", title="Keep me", source_kind="reflection", priority=2, days_ago=20)
    records = reaper.reap_stale_proposals(conn)
    md_path, json_path = reaper.write_digest(records, tmp_path, date_str="20260711")

    assert md_path.exists() and json_path.exists()
    assert md_path.name == "stale_proposal_reaper_20260711.md"
    assert json_path.name == "stale_proposal_reaper_20260711.json"

    data = json.loads(json_path.read_text())
    assert data["total"] == 2
    assert data["pruned"] == 1
    assert data["skipped"] == 1
    # every record carries the spec fields
    for r in data["records"]:
        for field in ("id", "title", "source_kind", "created_at", "age", "disposition", "reason"):
            assert field in r, f"missing field {field}"
    pruned_rec = next(r for r in data["records"] if r["id"] == "d1")
    assert pruned_rec["disposition"] == "pruned"
    assert pruned_rec["source_kind"] == "reflection"

    md = md_path.read_text()
    assert "Prune me" in md
    assert "Keep me" in md
    assert "Pruned (parked)" in md
    assert "Skipped (protected)" in md
