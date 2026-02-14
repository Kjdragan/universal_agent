import json


async def test_x_trends_posts_wrapper_uses_posts_only(monkeypatch):
    # Import after monkeypatching the module-level importer.
    from universal_agent.tools import x_trends_bridge as mod

    calls = {}

    def fake_trends_for_query(**kwargs):
        calls["trends_for_query"] = kwargs
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"themes\": [], \"posts\": []}"}]}]}

    def fake_global_trends(**kwargs):
        calls["global_trends"] = kwargs
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"themes\": [], \"posts\": []}"}]}]}

    def fake_parse(resp):
        return {"themes": [], "posts": [{"url": "https://x.com/a/status/1"}], "raw_text": ""}

    monkeypatch.setattr(mod, "_env_api_key", lambda: "xai-test-key")
    monkeypatch.setattr(mod, "_import_skill_lib", lambda: (fake_trends_for_query, fake_global_trends, fake_parse))

    res = await mod._x_trends_posts_impl(
        {
            "query": "OpenAI",
            "global_mode": False,
            "days": 1,
            "depth": "quick",
            "allowed_x_handles": [],
            "excluded_x_handles": [],
            "enable_image_understanding": False,
            "enable_video_understanding": False,
            "model": "grok-4-1-fast",
            "max_posts": 5,
        }
    )
    out = json.loads(res["content"][0]["text"])

    assert out["themes"] == []
    assert len(out["posts"]) == 1
    assert "trends_for_query" in calls
    assert calls["trends_for_query"]["posts_only"] is True


async def test_x_trends_posts_wrapper_requires_query_unless_global(monkeypatch):
    from universal_agent.tools import x_trends_bridge as mod

    monkeypatch.setattr(mod, "_env_api_key", lambda: "xai-test-key")
    monkeypatch.setattr(mod, "_import_skill_lib", lambda: (lambda **_: {}, lambda **_: {}, lambda _: {"themes": [], "posts": [], "raw_text": ""}))

    res = await mod._x_trends_posts_impl({"query": "", "global_mode": False})
    assert "error:" in res["content"][0]["text"]
