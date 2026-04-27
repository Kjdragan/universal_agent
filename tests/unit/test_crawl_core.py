import json
import os

import pytest

import src.mcp_server as mcp_server


@pytest.mark.asyncio
async def test_crawl_core_missing_api_key_fails_loudly(monkeypatch):
    """
    Test that if CRAWL4AI_API_KEY is missing and cannot be loaded,
    _crawl_core fails loudly and returns a proper error JSON instead of returning success 0/fail 0.
    """
    # 1. Remove the key from the environment
    monkeypatch.delenv("CRAWL4AI_API_KEY", raising=False)
    monkeypatch.delenv("CRAWL4AI_API_URL", raising=False)

    # Force the Crawl4AI engine — the default is now "jina" which doesn't
    # need an API key, so without this the test would take the Jina path
    # and succeed even when the key is missing.
    monkeypatch.setenv("CRAWL_ENGINE", "crawl4ai")
    import src.mcp_server as _mcp_mod
    monkeypatch.setattr(_mcp_mod, "_CRAWL_ENGINE", "crawl4ai")

    # 2. Mock infisical loader to do nothing (simulating the key is truly unretrievable)
    def fake_initialize(*args, **kwargs):
        pass

    import universal_agent.infisical_loader
    monkeypatch.setattr(universal_agent.infisical_loader, "initialize_runtime_secrets", fake_initialize)

    # 3. Call _crawl_core
    urls = ["https://example.com/test-article"]
    session_dir = "/tmp/fake_session"

    result_json = await mcp_server._crawl_core(urls, session_dir)
    result = json.loads(result_json)

    # 4. Verify it fails loudly
    assert "error" in result
    assert "CRAWL4AI_API_KEY is missing" in result["error"]
    assert result["failed"] == 1
    assert result["successful"] == 0
    assert result["total"] == 1
    assert len(result["errors"]) == 1
    assert "CRAWL4AI_API_KEY is missing" in result["errors"][0]
