import json

from universal_agent import main as agent_main


def test_interview_answers_merge_updates_mission(tmp_path):
    mission_path = tmp_path / "mission.json"
    mission_data = {
        "mission_root": "Summarize AI news",
        "status": "PLANNING",
        "clarifications": {
            "timeframe": "Last 7 days (Recent)",
            "focus": "All Topics",
        },
        "tasks": [],
    }
    mission_path.write_text(json.dumps(mission_data), encoding="utf-8")

    questions = [
        {
            "question": "What timeframe should I cover for the AI news summary?",
            "header": "Timeframe",
            "options": [],
            "multiSelect": False,
        }
    ]
    answers = {questions[0]["question"]: "Last 30 days (Monthly)"}
    payload = agent_main._build_interview_answers_payload(questions, answers)
    (tmp_path / "interview_answers.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    updated, changed = agent_main._apply_interview_answers_to_mission(
        str(tmp_path),
        mission_data=mission_data,
    )

    assert changed is True
    assert updated["clarifications"]["timeframe"] == "Last 30 days (Monthly)"

    saved = json.loads(mission_path.read_text(encoding="utf-8"))
    assert saved["clarifications"]["timeframe"] == "Last 30 days (Monthly)"


def test_interview_answers_skip_when_not_planning(tmp_path):
    mission_path = tmp_path / "mission.json"
    mission_data = {
        "mission_root": "Summarize AI news",
        "status": "IN_PROGRESS",
        "clarifications": {"timeframe": "Last 7 days (Recent)"},
        "tasks": [],
    }
    mission_path.write_text(json.dumps(mission_data), encoding="utf-8")

    payload = {
        "answers_by_header": {"timeframe": "Last 30 days (Monthly)"},
        "answers": {},
        "questions": [],
    }
    (tmp_path / "interview_answers.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    updated, changed = agent_main._apply_interview_answers_to_mission(
        str(tmp_path),
        mission_data=mission_data,
    )

    assert changed is False
    assert updated["clarifications"]["timeframe"] == "Last 7 days (Recent)"


def test_interview_answers_normalizes_alias_keys(tmp_path):
    mission_path = tmp_path / "mission.json"
    mission_data = {
        "mission_root": "Summarize AI news",
        "status": "PLANNING",
        "clarifications": {
            "time_range": "Awaiting user selection",
            "ai_topics": "Awaiting user selection",
        },
        "tasks": [],
    }
    mission_path.write_text(json.dumps(mission_data), encoding="utf-8")

    payload = {
        "answers_by_header": {"timeframe": "Last 30 days (Monthly)"},
        "answers": {},
        "questions": [],
    }
    (tmp_path / "interview_answers.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    updated, changed = agent_main._apply_interview_answers_to_mission(
        str(tmp_path),
        mission_data=mission_data,
    )

    assert changed is True
    assert updated["clarifications"]["timeframe"] == "Last 30 days (Monthly)"
    assert "time_range" not in updated["clarifications"]
