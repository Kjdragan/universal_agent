"""Regression tests for the dispatch memory-relevance gate.

`rebuild_dispatch_queue` calls `score_task` for every non-terminal task, and
`score_task` previously ran a per-task memory-orchestrator `broker.search()`
unconditionally. On a large board (~320 tasks) that made a single queue rebuild
a ~14s synchronous block on the asyncio event loop, invoked from hot paths
(every claim + the per-autonomous-cron wake check). That starved the
todo-dispatch loop and halted intel-brief authoring (2026-05-31 incident).

The per-task memory search is now OFF by default and gated behind
`UA_DISPATCH_MEMORY_RELEVANCE_ENABLED`. These tests pin that behavior.
"""

from __future__ import annotations

from universal_agent import task_hub
import universal_agent.memory.orchestrator as orchestrator_module

_LONG_TITLE = {"title": "Evaluate convergence candidate: agentic coding tooling"}


def test_memory_relevance_off_by_default_does_not_query_memory(monkeypatch):
    monkeypatch.delenv("UA_DISPATCH_MEMORY_RELEVANCE_ENABLED", raising=False)

    def _must_not_be_called():
        raise AssertionError(
            "memory orchestrator must not be queried during scoring when the "
            "UA_DISPATCH_MEMORY_RELEVANCE_ENABLED gate is off"
        )

    monkeypatch.setattr(
        orchestrator_module, "get_memory_orchestrator", _must_not_be_called, raising=False
    )

    assert task_hub._memory_relevance_bonus(_LONG_TITLE) == 0.0


def test_memory_relevance_on_when_flag_enabled(monkeypatch):
    monkeypatch.setenv("UA_DISPATCH_MEMORY_RELEVANCE_ENABLED", "1")

    class _StubBroker:
        def search(self, **_kwargs):
            return [{"id": "mem-1"}]

    monkeypatch.setattr(
        orchestrator_module, "get_memory_orchestrator", lambda: _StubBroker(), raising=False
    )

    assert task_hub._memory_relevance_bonus(_LONG_TITLE) == 0.4


def test_memory_relevance_on_but_no_hits_returns_zero(monkeypatch):
    monkeypatch.setenv("UA_DISPATCH_MEMORY_RELEVANCE_ENABLED", "true")

    class _EmptyBroker:
        def search(self, **_kwargs):
            return []

    monkeypatch.setattr(
        orchestrator_module, "get_memory_orchestrator", lambda: _EmptyBroker(), raising=False
    )

    assert task_hub._memory_relevance_bonus(_LONG_TITLE) == 0.0


def test_short_title_skips_memory_even_when_enabled(monkeypatch):
    monkeypatch.setenv("UA_DISPATCH_MEMORY_RELEVANCE_ENABLED", "1")

    def _must_not_be_called():
        raise AssertionError("short titles must short-circuit before any memory query")

    monkeypatch.setattr(
        orchestrator_module, "get_memory_orchestrator", _must_not_be_called, raising=False
    )

    assert task_hub._memory_relevance_bonus({"title": "hi"}) == 0.0
