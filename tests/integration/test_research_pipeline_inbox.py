import json
from pathlib import Path

import pytest

import mcp_server


@pytest.mark.asyncio
async def test_finalize_research_creates_refined_corpus(tmp_path, monkeypatch):
    workspace = tmp_path
    marker_path = workspace / "missing_workspace_marker.txt"
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE_FILE", str(marker_path))
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(workspace))
    search_dir = workspace / "search_results"
    search_dir.mkdir(parents=True, exist_ok=True)

    search_payload = {
        "tool": "COMPOSIO_SEARCH_WEB",
        "query": "silver market test query",
        "results": [
            {
                "title": "Example Silver Market Analysis",
                "url": "http://example.com/silver-market",
                "snippet": "Sample snippet for testing.",
            }
        ],
    }
    (search_dir / "COMPOSIO_SEARCH_WEB_0.json").write_text(
        json.dumps(search_payload)
    )

    body = (
        "This analysis explains the silver market dynamics and industrial demand "
        "trends in detail, providing context on pricing, supply constraints, and "
        "investment flows across sectors. "
    )
    long_body = (body * 40).strip()
    crawl_text = (
        "---\n"
        "title: Example Silver Market Analysis\n"
        "source: https://example.com/silver-market\n"
        "date: 2026-02-01\n"
        "---\n\n"
        f"{long_body}\n"
    )
    crawl_path = search_dir / "crawl_example.md"
    crawl_path.write_text(crawl_text)

    async def fake_crawl(urls, session_dir):
        assert urls, "Expected URLs extracted from search results."
        return json.dumps(
            {
                "total": len(urls),
                "successful": 1,
                "failed": 0,
                "saved_files": [{"path": str(crawl_path)}],
                "errors": [],
            }
        )

    async def fake_refine(corpus_dir, output_file, accelerated=False):
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("Refined corpus placeholder.")
        return {
            "output_file": str(output_path),
            "output_words": 3,
            "original_words": 400,
            "compression_ratio": 1.0,
            "total_time_ms": 1,
        }

    monkeypatch.setattr(mcp_server, "_crawl_core", fake_crawl)
    monkeypatch.setattr(mcp_server, "refine_corpus_programmatic", fake_refine)

    result = await mcp_server.finalize_research(
        session_dir=str(workspace),
        task_name="test_task",
        enable_topic_filter=False,
    )
    payload = json.loads(result)

    refined_path = workspace / "tasks" / "test_task" / "refined_corpus.md"
    processed_dir = search_dir / "processed_json"

    assert payload.get("status", "").startswith("Research Corpus Finalized")
    assert refined_path.exists()
    assert processed_dir.exists()
    assert any(processed_dir.glob("*.json"))


@pytest.mark.asyncio
async def test_finalize_research_blocks_session_scope_violation(tmp_path, monkeypatch):
    active_workspace = tmp_path / "active_workspace"
    requested_workspace = tmp_path / "requested_workspace"
    active_workspace.mkdir(parents=True, exist_ok=True)
    requested_workspace.mkdir(parents=True, exist_ok=True)
    (requested_workspace / "search_results").mkdir(parents=True, exist_ok=True)

    marker_path = tmp_path / "missing_workspace_marker.txt"
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE_FILE", str(marker_path))
    monkeypatch.setenv("CURRENT_SESSION_WORKSPACE", str(active_workspace))

    result = await mcp_server.finalize_research(
        session_dir=str(requested_workspace),
        task_name="test_task",
        enable_topic_filter=False,
    )
    payload = json.loads(result)
    assert "Session scope violation" in payload.get("error", "")
