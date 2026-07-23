"""Phase 2b — promote verb, ideation action signing, morning report rendering."""

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


def _add_eval(conn, task_id):
    conn.execute(
        "INSERT INTO task_hub_evaluations (id, task_id, evaluated_at, decision) VALUES (?,?,?,?)",
        (f"ev_{task_id}", task_id, "now", "score"),
    )


def test_dismiss_deletes_held_proposal_and_children():
    conn = _mem()
    task_hub.upsert_item(conn, {
        "task_id": "task_d1", "title": "Held idea", "description": "x",
        "source_kind": "reflection", "status": "open", "agent_ready": False,
        "trigger_type": "autonomous",
    })
    _add_eval(conn, "task_d1")
    assert task_hub.delete_held_proposal(conn, "task_d1") is True
    assert task_hub.get_item(conn, "task_d1") is None            # row gone
    assert conn.execute(
        "SELECT count(*) FROM task_hub_evaluations WHERE task_id='task_d1'"
    ).fetchone()[0] == 0                                          # no orphan children
    # gone from the report surface too
    assert not ideation_report.get_held_proposals(conn)


def test_dismiss_deletes_already_parked_proposal():
    conn = _mem()
    task_hub.upsert_item(conn, {
        "task_id": "task_d2", "title": "Parked idea", "source_kind": "reflection",
        "status": "parked", "agent_ready": False, "trigger_type": "autonomous",
    })
    assert task_hub.delete_held_proposal(conn, "task_d2") is True
    assert task_hub.get_item(conn, "task_d2") is None


def test_prune_deletes_brainstorm_and_promoted_open_proposals():
    """The stale section's Prune (same a=dismiss action) targets brainstorm items
    and promoted-but-unclaimed (open, agent_ready=1) ones — both must delete."""
    conn = _mem()
    task_hub.upsert_item(conn, {
        "task_id": "task_bs", "title": "Brainstorm idea", "source_kind": "brainstorm",
        "status": "open", "agent_ready": False, "trigger_type": "autonomous",
    })
    task_hub.upsert_item(conn, {
        "task_id": "task_promoted", "title": "Promoted, unclaimed",
        "source_kind": "reflection", "status": "open", "agent_ready": True,
        "trigger_type": "autonomous",
    })
    assert task_hub.delete_held_proposal(conn, "task_bs") is True
    assert task_hub.delete_held_proposal(conn, "task_promoted") is True
    assert task_hub.get_item(conn, "task_bs") is None
    assert task_hub.get_item(conn, "task_promoted") is None


def test_dismiss_refuses_inflight_or_non_proposal():
    conn = _mem()
    # claimed / in-flight — the status guard must protect live work
    task_hub.upsert_item(conn, {
        "task_id": "task_d3", "title": "In progress", "source_kind": "reflection",
        "status": "in_progress", "agent_ready": True, "trigger_type": "autonomous",
    })
    # not an ideation proposal at all
    task_hub.upsert_item(conn, {
        "task_id": "task_d4", "title": "Real task", "source_kind": "cody_demo_task",
        "status": "open", "agent_ready": False, "trigger_type": "autonomous",
    })
    for tid in ("task_d3", "task_d4", "task_missing"):
        with pytest.raises(ValueError):
            task_hub.delete_held_proposal(conn, tid)
    assert task_hub.get_item(conn, "task_d3") is not None         # survived
    assert task_hub.get_item(conn, "task_d4") is not None
