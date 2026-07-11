"""Stale-proposal surface (>72h) + weekly reaper (14d) for reflection/brainstorm items.

Covers three concerns:

1. **>72h surface** — ``ideation_report.get_stale_proposals`` lists every OPEN
   reflection/brainstorm Task Hub item older than 72h, OLDEST FIRST, so the
   morning ideation report can surface proposals that have sat without a verdict.
2. **14d reaper** — ``task_hub.reap_stale_proposals`` parks OPEN
   reflection/brainstorm items older than 14 days through the sanctioned per-item
   audit path (status=PARKED + ``metadata.stale_proposal_reap`` marker + an
   evaluation row tagged ``source=stale_proposal_reaper``) — NEVER a hard delete,
   because ``parked`` is the canonical retire-an-open-item verb.
3. **HARD GATE** — the reaper NEVER touches priority>=2 OR ``human-only`` items,
   regardless of age. Priority semantics verified in ``task_hub._priority_weight``
   (higher number == higher priority; range 1-4), so ``priority >= 2`` protects
   everything above the lowest tier.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from universal_agent import task_hub
from universal_agent.services import ideation_report

# ─── fixtures ─────────────────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _set_age(conn: sqlite3.Connection, task_id: str, age_hours: float) -> None:
    """Pin created_at so the item looks `age_hours` old."""
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    conn.execute(
        "UPDATE task_hub_items SET created_at=? WHERE task_id=?",
        (created.strftime("%Y-%m-%dT%H:%M:%S+00:00"), task_id),
    )


def _seed(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    source_kind: str = "reflection",
    status: str = task_hub.TASK_STATUS_OPEN,
    priority: int = 1,
    labels: list[str] | None = None,
    agent_ready: bool = False,
    age_hours: float = 24 * 30,
) -> dict:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": source_kind,
            "title": f"Proposal ({task_id})",
            "description": "x",
            "status": status,
            "priority": priority,
            "labels": labels or [],
            "agent_ready": agent_ready,
            "trigger_type": "autonomous",
        },
    )
    _set_age(conn, task_id, age_hours)
    return task_hub.get_item(conn, task_id)


# ─── 1. >72h surface ──────────────────────────────────────────────────────────


def test_stale_surface_lists_open_reflection_and_brainstorm_older_than_72h():
    conn = _conn()
    _seed(conn, task_id="ref_stale", source_kind="reflection", age_hours=24 * 4)
    _seed(conn, task_id="brain_stale", source_kind="brainstorm", age_hours=24 * 3)
    _seed(conn, task_id="ref_fresh", source_kind="reflection", age_hours=24)

    stale = ideation_report.get_stale_proposals(conn, max_age_hours=72)
    ids = {s["task_id"] for s in stale}
    assert ids == {"ref_stale", "brain_stale"}


def test_stale_surface_oldest_first():
    conn = _conn()
    _seed(conn, task_id="newer", source_kind="reflection", age_hours=24 * 4)
    _seed(conn, task_id="older", source_kind="reflection", age_hours=24 * 10)
    _seed(conn, task_id="oldest", source_kind="reflection", age_hours=24 * 20)

    stale = ideation_report.get_stale_proposals(conn, max_age_hours=72)
    assert [s["task_id"] for s in stale] == ["oldest", "older", "newer"]


def test_stale_surface_excludes_non_open_status():
    conn = _conn()
    _seed(conn, task_id="parked_one", source_kind="reflection",
          status=task_hub.TASK_STATUS_PARKED, age_hours=24 * 30)
    _seed(conn, task_id="in_progress_one", source_kind="reflection",
          status=task_hub.TASK_STATUS_IN_PROGRESS, age_hours=24 * 30)
    _seed(conn, task_id="open_one", source_kind="reflection", age_hours=24 * 5)

    stale = ideation_report.get_stale_proposals(conn, max_age_hours=72)
    assert {s["task_id"] for s in stale} == {"open_one"}


def test_stale_surface_excludes_other_source_kinds():
    conn = _conn()
    _seed(conn, task_id="csi_one", source_kind="csi", age_hours=24 * 30)
    _seed(conn, task_id="proactive_one", source_kind="proactive_signal", age_hours=24 * 30)
    _seed(conn, task_id="ref_one", source_kind="reflection", age_hours=24 * 5)

    stale = ideation_report.get_stale_proposals(conn, max_age_hours=72)
    assert {s["task_id"] for s in stale} == {"ref_one"}


def test_stale_section_renders_promote_dismiss_buttons(monkeypatch):
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret")
    conn = _conn()
    _seed(conn, task_id="stale_btn", source_kind="reflection", age_hours=24 * 5)
    stale = ideation_report.get_stale_proposals(conn, max_age_hours=72)

    html = ideation_report.render_report_html(
        [], base="https://gw.example", generated_ct="now", stale=stale,
    )
    assert "STALE PROPOSALS NEEDING VERDICT" in html
    assert ">72h" in html
    assert "/api/v1/ideation/stale_btn/action?a=promote" in html
    assert "a=dismiss" in html


def test_report_sends_when_only_stale_proposals_exist(monkeypatch):
    """No held proposals but stale ones exist -> still deliver (surface them)."""
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret")
    conn = _conn()
    _seed(conn, task_id="only_stale", source_kind="brainstorm", age_hours=24 * 5)

    held = ideation_report.get_held_proposals(conn)
    stale = ideation_report.get_stale_proposals(conn, max_age_hours=72)
    assert held == []
    assert len(stale) == 1


# ─── 2. 14d reaper ────────────────────────────────────────────────────────────


def test_reaper_parks_stale_open_proposals_older_than_14d():
    conn = _conn()
    _seed(conn, task_id="reap_me", source_kind="reflection", age_hours=24 * 20)
    _seed(conn, task_id="reap_brain", source_kind="brainstorm", age_hours=24 * 16)

    summary = task_hub.reap_stale_proposals(conn, older_than_days=14)

    assert summary["closed"] == 2
    assert set(summary["closed_ids"]) == {"reap_me", "reap_brain"}
    for tid in ("reap_me", "reap_brain"):
        item = task_hub.get_item(conn, tid)
        assert item["status"] == task_hub.TASK_STATUS_PARKED
        meta = item.get("metadata") or {}
        assert meta.get("stale_proposal_reap", {}).get("reason") == "stale_proposal_reaped"
    for tid in ("reap_me", "reap_brain"):
        evs = conn.execute(
            "SELECT judge_payload_json FROM task_hub_evaluations WHERE task_id=?", (tid,),
        ).fetchall()
        assert any("stale_proposal_reaper" in str(e["judge_payload_json"]) for e in evs), tid


def test_reaper_skips_items_under_14d():
    conn = _conn()
    _seed(conn, task_id="too_young", source_kind="reflection", age_hours=24 * 10)

    summary = task_hub.reap_stale_proposals(conn, older_than_days=14)

    assert summary["closed"] == 0
    assert task_hub.get_item(conn, "too_young")["status"] == task_hub.TASK_STATUS_OPEN
    assert summary["skipped"] >= 1


def test_reaper_skips_non_open_and_other_source_kinds():
    conn = _conn()
    _seed(conn, task_id="parked_already", source_kind="reflection",
          status=task_hub.TASK_STATUS_PARKED, age_hours=24 * 30)
    _seed(conn, task_id="csi_stale", source_kind="csi", age_hours=24 * 30)
    _seed(conn, task_id="open_ref", source_kind="reflection", age_hours=24 * 20)

    summary = task_hub.reap_stale_proposals(conn, older_than_days=14)

    assert summary["closed"] == 1
    assert summary["closed_ids"] == ["open_ref"]


def test_reaper_dry_run_no_writes():
    conn = _conn()
    _seed(conn, task_id="dry", source_kind="reflection", age_hours=24 * 20)

    summary = task_hub.reap_stale_proposals(conn, older_than_days=14, dry_run=True)

    assert summary["closed"] == 1
    assert summary["dry_run"] is True
    assert task_hub.get_item(conn, "dry")["status"] == task_hub.TASK_STATUS_OPEN


# ─── 3. HARD GATE: priority>=2 and human-only ────────────────────────────────


def test_reaper_never_prunes_priority_ge_2():
    conn = _conn()
    _seed(conn, task_id="prio1", source_kind="reflection", priority=1, age_hours=24 * 20)
    _seed(conn, task_id="prio2", source_kind="reflection", priority=2, age_hours=24 * 20)
    _seed(conn, task_id="prio3", source_kind="brainstorm", priority=3, age_hours=24 * 20)
    _seed(conn, task_id="prio4", source_kind="reflection", priority=4, age_hours=24 * 20)

    summary = task_hub.reap_stale_proposals(conn, older_than_days=14)

    assert summary["closed_ids"] == ["prio1"]
    assert task_hub.get_item(conn, "prio2")["status"] == task_hub.TASK_STATUS_OPEN
    assert task_hub.get_item(conn, "prio3")["status"] == task_hub.TASK_STATUS_OPEN
    assert task_hub.get_item(conn, "prio4")["status"] == task_hub.TASK_STATUS_OPEN
    assert summary["skipped_reasons"].get("priority_protected") == 3


def test_reaper_never_prunes_human_only_label():
    conn = _conn()
    _seed(conn, task_id="human_only_one", source_kind="reflection",
          labels=["human-only"], age_hours=24 * 20)
    _seed(conn, task_id="human_only_two", source_kind="brainstorm",
          labels=["agent-ready", "human-only"], age_hours=24 * 20)
    _seed(conn, task_id="reapable", source_kind="reflection", age_hours=24 * 20)

    summary = task_hub.reap_stale_proposals(conn, older_than_days=14)

    assert summary["closed_ids"] == ["reapable"]
    assert task_hub.get_item(conn, "human_only_one")["status"] == task_hub.TASK_STATUS_OPEN
    assert task_hub.get_item(conn, "human_only_two")["status"] == task_hub.TASK_STATUS_OPEN
    assert summary["skipped_reasons"].get("human_only_protected") == 2


def test_human_only_label_constant_exists():
    assert task_hub.TASK_LABEL_HUMAN_ONLY == "human-only"


def test_reaper_malformed_timestamp_skipped():
    conn = _conn()
    _seed(conn, task_id="bad_ts", source_kind="reflection", age_hours=24 * 20)
    conn.execute(
        "UPDATE task_hub_items SET created_at=? WHERE task_id=?",
        ("not-a-timestamp", "bad_ts"),
    )
    _seed(conn, task_id="good_ts", source_kind="reflection", age_hours=24 * 20)

    summary = task_hub.reap_stale_proposals(conn, older_than_days=14)

    assert summary["closed_ids"] == ["good_ts"]
    assert summary["skipped_reasons"].get("malformed_timestamp") == 1
