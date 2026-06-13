"""P3 guard: the Tutorial tier is teaching-doc only — no hook/transform/skill
surface may request a runnable implementation build."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from webhook_transforms.manual_youtube_transform import transform as manual_transform

from universal_agent.gateway import InProcessGateway
from universal_agent.hooks_service import (
    HookAction,
    HooksService,
    build_manual_youtube_action,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_CODE_PAYLOAD = {
    "video_url": "https://www.youtube.com/watch?v=demo1234567",
    "video_id": "demo1234567",
    "channel_id": "UCdemo",
    "title": "Python MCP automation walkthrough",
    "mode": "explainer_plus_code",
}


def test_manual_action_pins_concept_only_even_for_code_mode():
    action = build_manual_youtube_action(dict(_CODE_PAYLOAD))
    assert action is not None
    msg = action["message"]
    assert "mode: explainer_plus_code" in msg
    assert "learning_mode: concept_only" in msg
    assert "concept_plus_implementation" not in msg


def test_manual_action_never_requests_implementation_build():
    msg = build_manual_youtube_action(dict(_CODE_PAYLOAD))["message"]
    assert "implementation/ with runnable code" not in msg
    assert "Do NOT create an implementation/ folder" in msg
    assert "implementation_required=false" in msg
    assert "/opt/ua_demos" in msg


def test_manual_transform_is_teaching_doc_only():
    out = manual_transform({"payload": dict(_CODE_PAYLOAD)})
    assert out is not None
    msg = out["message"]
    assert "mode: explainer_plus_code" in msg
    assert "learning_mode: concept_only" in msg
    assert "concept_plus_implementation" not in msg
    assert "implementation/ with runnable code" not in msg
    assert "Do NOT create an implementation/ folder" in msg
    assert "/opt/ua_demos" in msg


def test_agent_user_input_extra_lines_forbid_implementation(tmp_path):
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict("os.environ", {"UA_RUNTIME_DB_PATH": runtime_db_path}, clear=False),
    ):
        service = HooksService(MagicMock(spec=InProcessGateway))
    action = HookAction(
        kind="agent",
        name="ManualYouTubeWebhook",
        session_key="yt_chan__vid",
        to="youtube-expert",
        message="video_id: demo1234567\nvideo_url: https://www.youtube.com/watch?v=demo1234567",
    )
    prompt = service._build_agent_user_input(action)
    assert "Only create runnable implementation artifacts" not in prompt
    assert "Never create runnable implementation artifacts" in prompt
    assert "/opt/ua_demos" in prompt


def test_skill_md_is_teaching_doc_only():
    skill = (
        _REPO_ROOT / ".claude" / "skills" / "youtube-tutorial-creation" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "concept_plus_implementation" not in skill
    assert "create_new_repo" not in skill
    assert "/opt/ua_demos" in skill
    assert '"implementation_required": false' in skill
