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
    assert out["to"] == "youtube-explainer-expert"
    assert "learning_mode: concept_plus_implementation" in out["message"]
    assert "resolved_artifacts_root:" in out["message"]
    assert "Invalid paths: /opt/universal_agent/UA_ARTIFACTS_DIR/... and UA_ARTIFACTS_DIR/..." in out["message"]
    assert "never leave empty run dirs" in out["message"]


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


def test_composio_transform_direct_new_playlist_item_payload():
    ctx = {
        "payload": {
            "event_type": "new_playlist_item",
            "item": {
                "snippet": {
                    "title": "Playlist Video",
                    "resourceId": {
                        "videoId": "dQw4w9WgXcQ",
                    },
                },
            },
        }
    }

    out = composio_transform(ctx)
    assert out is not None
    assert out["kind"] == "agent"
    assert "video_id: dQw4w9WgXcQ" in out["message"]
    assert "video_url: https://www.youtube.com/watch?v=dQw4w9WgXcQ" in out["message"]
    assert out["to"] == "youtube-explainer-expert"


def test_composio_transform_new_playlist_item_under_body_dict_payload():
    ctx = {
        "payload": {
            "body": {
                "event_type": "new_playlist_item",
                "item": {
                    "snippet": {
                        "title": "Wrapped Playlist Video",
                        "resourceId": {
                            "videoId": "5tOUilBTJ3Q",
                        },
                    },
                },
            }
        }
    }

    out = composio_transform(ctx)
    assert out is not None
    assert out["kind"] == "agent"
    assert "video_id: 5tOUilBTJ3Q" in out["message"]
    assert "video_url: https://www.youtube.com/watch?v=5tOUilBTJ3Q" in out["message"]


def test_composio_transform_new_playlist_item_under_body_json_string_payload():
    ctx = {
        "payload": {
            "body": '{"event_type":"new_playlist_item","item":{"snippet":{"resourceId":{"videoId":"5tOUilBTJ3Q"}}}}'
        }
    }

    out = composio_transform(ctx)
    assert out is not None
    assert out["kind"] == "agent"
    assert "video_id: 5tOUilBTJ3Q" in out["message"]


def test_composio_transform_prefers_resource_video_id_over_playlist_item_id():
    ctx = {
        "payload": {
            "event_type": "new_playlist_item",
            "item": {
                "id": "UExqTDNsaVFTaXh0c19ORDlXbEUwcjVmLXEwakdBREZJRy5DQUNERDQ2NkIzRUQxNTY1",
                "snippet": {
                    "title": "Pad Thai Tutorial",
                    "resourceId": {
                        "videoId": "dQw4w9WgXcQ",
                    },
                },
            },
        }
    }

    out = composio_transform(ctx)
    assert out is not None
    assert "video_id: dQw4w9WgXcQ" in out["message"]
    assert "video_url: https://www.youtube.com/watch?v=dQw4w9WgXcQ" in out["message"]
    assert "UExqTDNsaVFTaXh0c19ORDlXbEUwcjVmLXEwakdBREZJRy5DQUNERDQ2NkIzRUQxNTY1" not in out["message"]


def test_manual_transform_from_video_url():
    ctx = {"payload": {"video_url": "https://youtu.be/xyz987abc12"}}
    out = manual_transform(ctx)
    assert out is not None
    assert out["kind"] == "agent"
    assert out["name"] == "ManualYouTubeWebhook"
    assert "xyz987abc12" in out["message"]
    assert out["to"] == "youtube-explainer-expert"
    assert "mode: explainer_plus_code" in out["message"]
    assert "learning_mode: concept_plus_implementation" in out["message"]
    assert "resolved_artifacts_root:" in out["message"]
    assert "Invalid paths: /opt/universal_agent/UA_ARTIFACTS_DIR/... and UA_ARTIFACTS_DIR/..." in out["message"]
    assert "never leave empty run dirs" in out["message"]


def test_composio_transform_normalizes_mode_and_degraded_flag():
    ctx = {
        "payload": {
            "type": "composio.trigger.message",
            "data": {
                "trigger_slug": "youtube_new_playlist_item_trigger",
                "toolkit_slug": "youtube",
                "data": {
                    "video_url": "https://www.youtube.com/watch?v=abc123xyz00",
                    "mode": "code",
                    "allow_degraded_transcript_only": "false",
                },
            },
        }
    }

    out = composio_transform(ctx)
    assert out is not None
    assert "mode: explainer_plus_code" in out["message"]
    assert "allow_degraded_transcript_only: false" in out["message"]


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
