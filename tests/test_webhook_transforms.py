from webhook_transforms.manual_youtube_transform import transform as manual_transform


def test_manual_transform_from_video_url():
    ctx = {"payload": {"video_url": "https://youtu.be/xyz987abc12"}}
    out = manual_transform(ctx)
    assert out is not None
    assert out["kind"] == "agent"
    assert out["name"] == "ManualYouTubeWebhook"
    assert "xyz987abc12" in out["message"]
    assert out["to"] == "youtube-expert"
    assert "mode: explainer_only" in out["message"]
    assert "learning_mode: concept_only" in out["message"]
    assert "resolved_artifacts_root:" in out["message"]
    assert "Invalid paths: /opt/universal_agent/UA_ARTIFACTS_DIR/... and UA_ARTIFACTS_DIR/..." in out["message"]
    assert "never leave empty run dirs" in out["message"]


def test_manual_transform_normalizes_mode_and_degraded_flag():
    ctx = {
        "payload": {
            "video_url": "https://youtu.be/xyz987abc12",
            "mode": "with_code",
            "allow_degraded_transcript_only": "0",
        }
    }
    out = manual_transform(ctx)
    assert out is not None
    assert "mode: explainer_plus_code" in out["message"]
    assert "allow_degraded_transcript_only: false" in out["message"]


def test_manual_transform_requires_target():
    ctx = {"payload": {}}
    assert manual_transform(ctx) is None


def test_manual_transform_passes_description_to_prompt():
    """Description should appear in the agent prompt when provided."""
    ctx = {
        "payload": {
            "video_url": "https://youtu.be/xyz987abc12",
            "description": "Check out the code: https://github.com/user/repo and data at https://kaggle.com/comp/123",
        }
    }
    out = manual_transform(ctx)
    assert out is not None
    assert "description_hint:" in out["message"]
    assert "github.com/user/repo" in out["message"]


def test_manual_transform_empty_description_is_safe():
    """Empty or missing description should not break the transform."""
    ctx = {"payload": {"video_url": "https://youtu.be/xyz987abc12"}}
    out = manual_transform(ctx)
    assert out is not None
    assert "description_hint:" in out["message"]


def test_manual_transform_includes_description_link_instructions():
    """The agent prompt should include instructions for description link analysis."""
    ctx = {"payload": {"video_url": "https://youtu.be/xyz987abc12"}}
    out = manual_transform(ctx)
    assert out is not None
    assert "DESCRIPTION LINK ANALYSIS" in out["message"]
    assert "direct connections" in out["message"].lower() or "DIRECT connections" in out["message"]
    assert "residential proxy" in out["message"].lower()


def test_manual_transform_description_improves_code_detection():
    """A description mentioning 'github' or 'python' should trigger code mode even if title is generic."""
    ctx = {
        "payload": {
            "video_url": "https://youtu.be/xyz987abc12",
            "title": "Interesting Tutorial",
            "description": "Full python source code at https://github.com/user/project",
        }
    }
    out = manual_transform(ctx)
    assert out is not None
    assert "mode: explainer_plus_code" in out["message"]
    assert "learning_mode: concept_plus_implementation" in out["message"]


def test_manual_transform_description_truncated_in_hint():
    """Long descriptions should be truncated to 500 chars in the hint."""
    long_desc = "x" * 1000
    ctx = {
        "payload": {
            "video_url": "https://youtu.be/xyz987abc12",
            "description": long_desc,
        }
    }
    out = manual_transform(ctx)
    assert out is not None
    # The hint should not contain the full 1000-char string
    hint_line = [l for l in out["message"].split("\n") if "description_hint:" in l][0]
    hint_value = hint_line.split("description_hint: ", 1)[1]
    assert len(hint_value) == 500
