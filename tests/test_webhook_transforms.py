from webhook_transforms.composio_youtube_transform import transform as composio_transform
from webhook_transforms.manual_youtube_transform import transform as manual_transform


def test_composio_transform_youtube_event():
    ctx = {
        "payload": {
            "type": "composio.trigger.message",
            "data": {
                "trigger_slug": "YOUTUBE_NEW_ACTIVITY_TRIGGER",
                "toolkit_slug": "youtube",
                "data": {
                    "video_url": "https://www.youtube.com/watch?v=abc123xyz00",
                    "channel_id": "UC_123",
                    "title": "New Video",
                },
            },
        }
    }

    out = composio_transform(ctx)
    assert out is not None
    assert out["kind"] == "agent"
    assert out["name"] == "ComposioYouTubeTrigger"
    assert "abc123xyz00" in out["message"]
    assert out["session_key"].startswith("yt_")


def test_composio_transform_non_youtube_returns_none():
    ctx = {
        "payload": {
            "type": "composio.trigger.message",
            "data": {
                "trigger_slug": "GITHUB_COMMIT_EVENT",
                "toolkit_slug": "github",
                "data": {"repo": "x/y"},
            },
        }
    }
    assert composio_transform(ctx) is None


def test_manual_transform_from_video_url():
    ctx = {"payload": {"video_url": "https://youtu.be/xyz987abc12"}}
    out = manual_transform(ctx)
    assert out is not None
    assert out["kind"] == "agent"
    assert out["name"] == "ManualYouTubeWebhook"
    assert "xyz987abc12" in out["message"]


def test_manual_transform_requires_target():
    ctx = {"payload": {}}
    assert manual_transform(ctx) is None
