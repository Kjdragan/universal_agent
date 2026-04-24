"""RED/GREEN test for Bug 2: missing logger in research_bridge.py.

The ``run_report_generation_wrapper`` handler calls ``logger.warning()`` when
``corpus_data`` is a short, non-content string (e.g. ``"refined"``).  Before the
fix, ``logger`` is undefined in research_bridge.py and a ``NameError`` crashes
the tool call.
"""

import asyncio

from universal_agent.tools import research_bridge as rb


def _run(coro):
    return asyncio.run(coro)


def test_report_gen_wrapper_does_not_crash_on_short_corpus_data(monkeypatch, tmp_path):
    """Short non-content corpus_data should be silently rejected, not crash."""
    workspace = tmp_path / "session_logger_test"
    workspace.mkdir()
    (workspace / "work_products").mkdir()
    (workspace / "session_policy.json").write_text("{}", encoding="utf-8")
    # Ensure refined_corpus.md exists so the downstream function can proceed
    corpus_dir = workspace / "tasks" / "default" / "refined_corpus.md"
    corpus_dir.parent.mkdir(parents=True)
    corpus_dir.write_text("# real corpus content here\n" * 50, encoding="utf-8")

    captured: dict = {}

    async def _fake_report(query, task_name, corpus_data=None, workspace_dir=None):
        captured["corpus_data"] = corpus_data
        return "ok-report-logger"

    monkeypatch.setattr(rb, "report_gen_core", _fake_report)

    # This should NOT raise NameError for 'logger'
    result = _run(
        rb.run_report_generation_wrapper.handler(
            {
                "query": "test query",
                "corpus_data": "refined",  # short non-content string
                "workspace_dir": str(workspace),
            }
        )
    )

    # corpus_data should have been rejected (set to None) with a logger.warning
    assert captured["corpus_data"] is None
    assert result["content"][0]["text"] == "ok-report-logger"
