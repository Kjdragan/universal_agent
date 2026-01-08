import json

from universal_agent import main as agent_main


def test_interview_flow_updates_plan_after_mission_created(tmp_path):
    questions = [
        {
            "question": "What timeframe should I cover for the AI news summary?",
            "header": "Timeframe",
            "options": [],
            "multiSelect": False,
        }
    ]
    pending_interview_path = tmp_path / "pending_interview.json"
    pending_interview_path.write_text(
        json.dumps({"questions": questions}),
        encoding="utf-8",
    )

    def stub_answers(_questions):
        return {_questions[0]["question"]: "Last 30 days (Monthly)"}

    prompt = agent_main._process_pending_interview(str(tmp_path), ask_fn=stub_answers)
    assert prompt and "USER INTERVIEW ANSWERS" in prompt

    payload = json.loads((tmp_path / "interview_answers.json").read_text(encoding="utf-8"))
    assert payload["answers_by_header"]["timeframe"] == "Last 30 days (Monthly)"

    mission_data = {
        "mission_root": "Summarize AI news",
        "status": "PLANNING",
        "clarifications": {"timeframe": "Last 7 days (Recent)"},
        "tasks": [],
    }
    (tmp_path / "mission.json").write_text(json.dumps(mission_data), encoding="utf-8")

    updated, changed = agent_main._apply_interview_answers_to_mission(str(tmp_path))
    assert changed is True
    assert updated["clarifications"]["timeframe"] == "Last 30 days (Monthly)"
