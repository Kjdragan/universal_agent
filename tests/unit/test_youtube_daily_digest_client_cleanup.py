"""Regression tests: the digest synthesis steps must CLOSE their AsyncAnthropic
client inside their own event loop.

Background (2026-06-03 investigation): each synthesis step builds an
AsyncAnthropic client and runs under its own ``asyncio.run(...)``.  The clients
were never closed, so their httpx connection pools outlived the loop.  When GC
finalized a pool during a *later* ``asyncio.run(...)`` (the email send), httpx
tried to schedule ``aclose()`` on the already-closed synthesis loop and asyncio
logged a spurious, misleading::

    ERROR - Task exception was never retrieved
    ... RuntimeError: Event loop is closed

on every digest run, immediately before the (successful) "Email sent
successfully." line.  These tests pin the fix: the client is closed exactly
once per synthesis step, and the close helper tolerates a client without a
``close`` method (so the existing fakes in other tests stay valid).
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from universal_agent.scripts import youtube_daily_digest as ydd


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


_CANNED_MAP_OUTPUT = """## Test Video

**Video ID:** vid1

### Retelling
Body.

### Thesis
Short thesis.

```per_video_classification
{
  "video_id": "vid1",
  "value_score": 60,
  "value_tier": "medium",
  "code_implementation_prospect": false,
  "concept_only": true,
  "evidence_quality": "transcript",
  "reason": "r"
}
```
"""


class _RecordingClient:
    """An AsyncAnthropic stand-in that records how many times it was closed."""

    def __init__(self, response_text: str):
        self._response_text = response_text
        self.close_calls = 0

        outer = self

        class _Messages:
            async def create(self, **kwargs):
                return _FakeResponse(outer._response_text)

        self.messages = _Messages()

    async def close(self):
        self.close_calls += 1


class _NoopLimiter:
    """Mirror the real ZAIRateLimiter contract without network/timing."""

    def acquire(self, *a, **k):
        class _Ctx:
            async def __aenter__(_self):
                return None

            async def __aexit__(_self, *a):
                return False

        return _Ctx()

    async def record_success(self):
        pass

    async def record_429(self, *a):
        pass

    def get_backoff(self, attempt):
        return 0.0


@pytest.fixture(autouse=True)
def _noop_limiter(monkeypatch):
    monkeypatch.setattr(ydd.ZAIRateLimiter, "get_instance", lambda: _NoopLimiter())


def test_map_step_closes_its_client(monkeypatch):
    client = _RecordingClient(_CANNED_MAP_OUTPUT)
    monkeypatch.setattr(
        ydd, "_build_anthropic_client_for_zai", lambda timeout_seconds: client
    )
    videos = [
        ydd.VideoTranscriptPayload(video_id="vid1", title="Test Video", transcript_text="t")
    ]
    results = asyncio.run(ydd._map_retell_videos(videos, concurrency=2))
    assert len(results) == 1
    assert client.close_calls == 1, "map step must close its AsyncAnthropic client exactly once"


def test_reduce_step_closes_its_client(monkeypatch):
    client = _RecordingClient("## Meta-Synthesis: Daily Digest\n\nthemes.\n")
    monkeypatch.setattr(
        ydd, "_build_anthropic_client_for_zai", lambda timeout_seconds: client
    )
    map_results = [
        ydd.MapResult(
            video_id="a",
            title="A",
            retell_markdown="## A",
            thesis_line="A.",
            classification={"video_id": "a", "value_score": 50, "value_tier": "medium"},
        )
    ]
    out = asyncio.run(
        ydd._reduce_meta_synthesize(
            map_results, day_name="TUESDAY", date_str=date.today().isoformat()
        )
    )
    assert "Meta-Synthesis" in out
    assert client.close_calls == 1, "reduce step must close its AsyncAnthropic client exactly once"


def test_single_call_pipeline_closes_its_client(monkeypatch):
    # Pin the model so the test never depends on resolve_model().
    monkeypatch.setenv("UA_YOUTUBE_DIGEST_MODEL", "glm-5.1")
    client = _RecordingClient("single-call digest output")
    monkeypatch.setattr(
        ydd, "_build_anthropic_client_for_zai", lambda timeout_seconds: client
    )
    out = asyncio.run(ydd._generate_digest_content_single_call("a full prompt"))
    assert out == "single-call digest output"
    assert client.close_calls == 1, "single_call pipeline must close its client exactly once"


def test_map_step_closes_client_even_when_a_call_raises(monkeypatch):
    """`_retell_one_video` swallows per-video errors, but the finally block must
    still close the client regardless of what happens inside the gather."""

    class _ExplodingClient(_RecordingClient):
        def __init__(self):
            super().__init__("")

            class _Messages:
                async def create(self, **kwargs):
                    raise RuntimeError("boom")

            self.messages = _Messages()

    client = _ExplodingClient()
    monkeypatch.setattr(
        ydd, "_build_anthropic_client_for_zai", lambda timeout_seconds: client
    )
    videos = [ydd.VideoTranscriptPayload(video_id="v", title="T", transcript_text="t")]
    results = asyncio.run(ydd._map_retell_videos(videos, concurrency=1))
    assert results[0].error is not None  # per-video failure captured, not raised
    assert client.close_calls == 1, "client must be closed even when a map call fails"


def test_aclose_client_tolerates_missing_close():
    """The helper must no-op (not raise) for a client without a close method —
    this keeps the fake clients used by other digest tests valid."""

    class _NoCloseClient:
        pass

    # Must complete without raising.
    asyncio.run(ydd._aclose_client(_NoCloseClient()))


def test_aclose_client_swallows_close_errors():
    """A failing close must never propagate (cleanup is best-effort)."""

    class _BadClient:
        async def close(self):
            raise RuntimeError("close failed")

    asyncio.run(ydd._aclose_client(_BadClient()))
