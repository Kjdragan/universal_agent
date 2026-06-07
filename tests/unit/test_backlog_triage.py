"""Unit tests for backlog_triage — pure functions + the reminder-seed lifecycle.

Network-free: no `gh`, no LLM, no AgentMail. The seed test drives the real
proactive_artifacts + reminder machinery against a temp activity DB.
"""
from __future__ import annotations

import json
import sqlite3

from universal_agent import backlog_triage as bt


def _data(skill_gap=None, deslop=None, prs=None):
    return {
        "repo": "owner/repo",
        "open_issues": {
            "skill-gap": skill_gap or [],
            "deslop-findings": deslop or [],
            "planning": [],
        },
        "recent_actioned_prs": prs or [],
    }


def test_markdown_contains_all_sections():
    triage = {
        "headline": "1 deslop finding open",
        "assessment": "Backlog is mostly clear.",
        "actions": [{"item": "Triage #798", "why": "open", "priority": "P1"}],
        "next_run_brief": "Resolve #798 and open a cleanup PR.",
    }
    md = bt._markdown(triage, _data(deslop=[{"number": 798, "title": "deslop findings: PR #797"}]))
    assert "1 deslop finding open" in md
    assert "Recommended actions" in md and "[P1]" in md and "Triage #798" in md
    assert "Recommended next run" in md and "Resolve #798" in md
    assert "Delivered by Simone" in md
    assert "#798" in md  # open backlog source listed


def test_deterministic_counts():
    t = bt._deterministic(_data(deslop=[{"number": 798, "title": "x"}], prs=[{"number": 800}]))
    assert "1 open deslop-findings" in t["assessment"]
    assert any("798" in a["item"] for a in t["actions"])


def test_recipient_defaults_to_gmail():
    assert bt._recipient() == "kevinjdragan@gmail.com"
    assert bt._recipient("other@example.com") == "other@example.com"


def test_seed_reminders_lifecycle(tmp_path, monkeypatch):
    db = tmp_path / "activity_state.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db))
    triage = {"headline": "1 deslop finding open", "assessment": "test"}
    open_data = _data(deslop=[{"number": 798, "title": "deslop findings: PR #797"}])

    # Run 1: seeds a new reminder burst.
    bt._seed_reminders(open_data, triage, "kevinjdragan@gmail.com", "msg1")
    conn = sqlite3.connect(str(db))
    status, md_json = conn.execute(
        "SELECT status, metadata_json FROM proactive_artifacts"
    ).fetchone()
    conn.close()
    reminder_1 = json.loads(md_json).get("reminder")
    assert status in ("surfaced", "produced")
    assert reminder_1, "first run must seed reminder cadence"

    # Run 2: same open backlog -> cadence MUST be preserved (not reset).
    bt._seed_reminders(open_data, triage, "kevinjdragan@gmail.com", "msg2")
    conn = sqlite3.connect(str(db))
    reminder_2 = json.loads(
        conn.execute("SELECT metadata_json FROM proactive_artifacts").fetchone()[0]
    ).get("reminder")
    conn.close()
    assert reminder_2 == reminder_1, "re-run must not reset the +4h/+72h cadence"

    # Run 3: backlog empty -> row accepted (stops the sweep).
    bt._seed_reminders(_data(), triage, "kevinjdragan@gmail.com", "")
    conn = sqlite3.connect(str(db))
    final_status = conn.execute("SELECT status FROM proactive_artifacts").fetchone()[0]
    conn.close()
    assert final_status == "accepted"
