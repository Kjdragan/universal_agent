"""Phase 2b — promote verb, ideation action signing, morning report rendering."""

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import ideation_report
from universal_agent.services.cron_artifact_notifier import (
    sign_ideation_token,
    verify_ideation_token,
)


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def test_promote_in_valid_actions():
    assert "promote" in task_hub.VALID_ACTIONS


def test_promote_makes_held_proposal_dispatchable():
    conn = _mem()
    task_hub.upsert_item(conn, {
        "task_id": "task_p1", "title": "Held idea", "description": "x",
        "source_kind": "reflection", "status": "open", "agent_ready": False,
        "trigger_type": "autonomous",
    })
    assert not task_hub.get_item(conn, "task_p1")["agent_ready"]
    task_hub.perform_task_action(conn, task_id="task_p1", action="promote", agent_id="kevin")
    after = task_hub.get_item(conn, "task_p1")
    assert after["agent_ready"]            # now claimable by dispatch
    assert after["status"] == "open"
    assert float(after.get("score") or 0) >= 3   # floored above the default threshold


@pytest.mark.parametrize("action", ["promote", "dismiss"])
def test_ideation_token_roundtrip(action, monkeypatch):
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret")
    tok = sign_ideation_token("task_x", action)
    assert tok
    assert verify_ideation_token("task_x", action, tok)
    assert not verify_ideation_token("task_x", action, "tampered")
    assert not verify_ideation_token("task_other", action, tok)   # bound to task_id
    other = "dismiss" if action == "promote" else "promote"
    assert not verify_ideation_token("task_x", other, tok)        # bound to action


def test_ideation_token_rejects_unknown_action(monkeypatch):
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret")
    assert sign_ideation_token("task_x", "delete") == ""


def test_report_queries_held_only_and_renders_buttons(monkeypatch):
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret")
    conn = _mem()
    task_hub.upsert_item(conn, {
        "task_id": "task_r1", "title": "Purge stale cards",
        "description": "**Rationale:** noise.\n**Effort:** S",
        "source_kind": "reflection", "status": "open", "agent_ready": False,
        "labels": ["ideation"], "trigger_type": "autonomous",
    })
    # already-promoted proposal must NOT show in the report
    task_hub.upsert_item(conn, {
        "task_id": "task_r2", "title": "Already promoted",
        "source_kind": "reflection", "status": "open", "agent_ready": True,
        "trigger_type": "autonomous",
    })
    held = ideation_report.get_held_proposals(conn)
    ids = {h["task_id"] for h in held}
    assert "task_r1" in ids and "task_r2" not in ids

    html = ideation_report.render_report_html(held, base="https://gw.example", generated_ct="now")
    assert "Purge stale cards" in html
    assert "/api/v1/ideation/task_r1/action?a=promote" in html
    assert "a=dismiss" in html


def test_stale_query_72h_returns_old_open_reflection_brainstorm_oldest_first():
    conn = _mem()
    old = datetime.now(timezone.utc) - timedelta(hours=100)
    young = datetime.now(timezone.utc) - timedelta(hours=10)
    task_hub.upsert_item(conn, {
        "task_id": "s_old", "title": "Old reflection", "description": "x",
        "source_kind": "reflection", "status": "open", "agent_ready": False,
        "trigger_type": "autonomous", "created_at": old.isoformat(),
    })
    task_hub.upsert_item(conn, {
        "task_id": "s_old_bs", "title": "Old brainstorm", "description": "x",
        "source_kind": "brainstorm", "status": "open", "agent_ready": False,
        "trigger_type": "autonomous", "created_at": (old - timedelta(hours=1)).isoformat(),
    })
    task_hub.upsert_item(conn, {
        "task_id": "s_young", "title": "Young", "description": "x",
        "source_kind": "reflection", "status": "open", "agent_ready": False,
        "trigger_type": "autonomous", "created_at": young.isoformat(),
    })
    task_hub.upsert_item(conn, {
        "task_id": "s_parked", "title": "Parked old", "description": "x",
        "source_kind": "reflection", "status": "parked", "agent_ready": False,
        "trigger_type": "autonomous", "created_at": old.isoformat(),
    })
    stale = ideation_report.get_stale_proposals(conn)
    ids = [s["task_id"] for s in stale]
    # oldest first (s_old_bs is 1h older than s_old); young + parked excluded
    assert ids == ["s_old_bs", "s_old"]
    # exclude_ids dedupes against the held section
    stale2 = ideation_report.get_stale_proposals(conn, exclude_ids={"s_old"})
    assert "s_old" not in {s["task_id"] for s in stale2}


def test_stale_section_prune_visible_for_eligible_hidden_for_protected(monkeypatch):
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret")
    base = "https://gw.example"
    old_ct = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    eligible = {
        "task_id": "st1", "title": "Stale eligible", "description": "x",
        "source_kind": "reflection", "priority": 1, "labels_json": '["ideation"]',
        "created_at": old_ct,
    }
    protected_priority = {
        "task_id": "st2", "title": "Stale protected pri", "description": "x",
        "source_kind": "reflection", "priority": 2, "labels_json": '[]',
        "created_at": old_ct,
    }
    protected_human = {
        "task_id": "st3", "title": "Stale protected ho", "description": "x",
        "source_kind": "brainstorm", "priority": 1, "labels_json": '["human-only"]',
        "created_at": old_ct,
    }
    html = ideation_report.render_report_html(
        [], base=base, generated_ct="now",
        stale_proposals=[eligible, protected_priority, protected_human],
    )
    # stale section header present
    assert "Stale proposals needing verdict" in html
    # eligible: promote + prune (dismiss action) both present
    assert "/api/v1/ideation/st1/action?a=promote" in html
    assert "/api/v1/ideation/st1/action?a=dismiss" in html
    # protected (priority>=2): promote present, NO prune/dismiss link
    assert "/api/v1/ideation/st2/action?a=promote" in html
    assert "/api/v1/ideation/st2/action?a=dismiss" not in html
    # protected (human-only): promote present, NO prune/dismiss link
    assert "/api/v1/ideation/st3/action?a=promote" in html
    assert "/api/v1/ideation/st3/action?a=dismiss" not in html
    # protected badge rendered
    assert "protected" in html
