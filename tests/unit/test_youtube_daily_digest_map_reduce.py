"""Unit tests for the map-reduce pipeline added to youtube_daily_digest.py.

We mock the AsyncAnthropic client at the boundary (the messages.create call)
so the tests don't need network access. The point is to exercise the
parse/assemble/dispatch logic and verify the env-var knobs flow correctly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from universal_agent.scripts import youtube_daily_digest as ydd


def test_parse_map_output_extracts_classification_and_thesis():
    raw = """## Video Title Here

**Video ID:** abc123

### Retelling
Some retelling text here.

### Actionable Insights
- Do thing A.
- Do thing B.

### Thesis
The video argues that X is the way forward because of reasons.

```per_video_classification
{
  "video_id": "abc123",
  "value_score": 87,
  "value_tier": "high",
  "code_implementation_prospect": true,
  "concept_only": false,
  "evidence_quality": "transcript",
  "reason": "Concrete code walkthrough."
}
```
"""
    parsed = ydd._parse_map_output(raw, video_id="abc123", fallback_title="Title")
    assert parsed["classification"]["value_score"] == 87
    assert parsed["classification"]["value_tier"] == "high"
    assert parsed["classification"]["code_implementation_prospect"] is True
    assert "X is the way forward" in parsed["thesis_line"]
    # Classification block should be stripped from the markdown.
    assert "per_video_classification" not in parsed["retell_markdown"]
    assert "### Retelling" in parsed["retell_markdown"]


def test_parse_map_output_degrades_gracefully_on_bad_json():
    raw = """## Title

### Retelling
Body.

### Thesis
Short thesis line.

```per_video_classification
{ "video_id": "abc", "value_score": "not-a-number"  // bad json
```
"""
    parsed = ydd._parse_map_output(raw, video_id="abc", fallback_title="Title")
    # On bad JSON we still get a default classification so the digest can proceed.
    assert parsed["classification"]["video_id"] == "abc"
    assert parsed["classification"]["value_tier"] == "unknown"
    assert parsed["classification"]["code_implementation_prospect"] is False


def test_parse_map_output_thesis_falls_back_to_reason_when_missing():
    raw = """## Title

```per_video_classification
{
  "video_id": "abc",
  "value_score": 50,
  "value_tier": "medium",
  "code_implementation_prospect": false,
  "concept_only": true,
  "evidence_quality": "transcript",
  "reason": "Concept-only commentary."
}
```
"""
    parsed = ydd._parse_map_output(raw, video_id="abc", fallback_title="Title")
    assert parsed["thesis_line"] == "Concept-only commentary."


def test_is_rate_limit_error_matches_zai_fup_1313():
    fup_err = Exception(
        "Error code: 429 - {'error': {'code': '1313', 'message': 'Fair Usage Policy'}}"
    )
    assert ydd._is_rate_limit_error(fup_err) is True
    assert ydd._is_rate_limit_error(Exception("connection refused")) is False
    assert ydd._is_rate_limit_error(Exception("too many requests")) is True
    assert ydd._is_rate_limit_error(Exception("high concurrency detected")) is True


def test_assemble_map_reduce_digest_threads_retellings_before_json_block():
    map_results = [
        ydd.MapResult(
            video_id="a",
            title="A",
            retell_markdown="## A\n\n### Retelling\nA's retelling body.",
            thesis_line="A's thesis.",
            classification={"video_id": "a", "value_score": 90},
        ),
        ydd.MapResult(
            video_id="b",
            title="B",
            retell_markdown="## B\n\n### Retelling\nB's retelling body.",
            thesis_line="B's thesis.",
            classification={"video_id": "b", "value_score": 70},
        ),
    ]
    reduce_output = (
        "# Daily YouTube Digest: Sunday, 2026-05-18\n\n"
        "## Meta-Synthesis: Daily Digest\n\n"
        "### Cross-Video Themes\nSomething about both videos.\n\n"
        "```youtube_digest_decisions\n"
        '{ "ranked_videos": [] }\n'
        "```\n"
    )
    digest = ydd._assemble_map_reduce_digest(
        reduce_output=reduce_output, map_results=map_results
    )
    # Both retellings must appear in the body.
    assert "A's retelling body." in digest
    assert "B's retelling body." in digest
    # And the JSON block must still be at the end, intact.
    jsonidx = digest.index("```youtube_digest_decisions")
    retellings_idx = digest.index("## Per-Video Retellings")
    assert retellings_idx < jsonidx, "retellings must precede the JSON block so _extract_decision_json still works"


def test_assemble_map_reduce_digest_handles_missing_json_block():
    map_results = [
        ydd.MapResult(
            video_id="a",
            title="A",
            retell_markdown="## A retelling",
            thesis_line="A.",
            classification={"video_id": "a"},
        )
    ]
    # No JSON block at all in reducer output (degenerate but should not crash).
    digest = ydd._assemble_map_reduce_digest(
        reduce_output="# Just meta-synthesis with no JSON.\n", map_results=map_results
    )
    assert "A retelling" in digest


@pytest.mark.asyncio
async def test_retell_one_video_calls_llm_and_parses_response(monkeypatch):
    """Exercise the map call end-to-end with a stubbed Anthropic client."""

    class _FakeBlock:
        def __init__(self, text: str):
            self.text = text

    class _FakeResponse:
        def __init__(self, text: str):
            self.content = [_FakeBlock(text)]

    canned = """## Test Video

**Video ID:** vid1

### Retelling
This is a retelling.

### Thesis
This is the thesis.

```per_video_classification
{
  "video_id": "vid1",
  "value_score": 80,
  "value_tier": "high",
  "code_implementation_prospect": true,
  "concept_only": false,
  "evidence_quality": "transcript",
  "reason": "Concrete tutorial."
}
```
"""

    class _FakeMessages:
        async def create(self, **kwargs):
            return _FakeResponse(canned)

    class _FakeClient:
        messages = _FakeMessages()

    # Stub the rate limiter to no-op.
    class _NoopLimiter:
        def acquire(self, *a, **k):
            # The real limiter's acquire() returns an async context manager
            # directly (it's NOT an async function). Mirror that here so
            # `async with limiter.acquire(context):` works.
            class _Ctx:
                async def __aenter__(_self): return None
                async def __aexit__(_self, *a): return False
            return _Ctx()
        async def record_success(self): pass
        async def record_429(self, *a): pass
        def get_backoff(self, attempt): return 0.0

    monkeypatch.setattr(ydd.ZAIRateLimiter, "get_instance", lambda: _NoopLimiter())

    result = await ydd._retell_one_video(
        client=_FakeClient(),
        model="glm-4.5-air",
        video=ydd.VideoTranscriptPayload(
            video_id="vid1",
            title="Test Video",
            transcript_text="Original transcript content",
        ),
        max_tokens=2000,
        transcript_char_limit=50_000,
    )
    assert result.video_id == "vid1"
    assert result.classification["value_score"] == 80
    assert result.thesis_line == "This is the thesis."
    assert result.error is None
    assert result.map_model == "glm-4.5-air"


@pytest.mark.asyncio
async def test_retell_one_video_returns_error_result_when_llm_fails(monkeypatch):
    class _FailingMessages:
        async def create(self, **kwargs):
            raise RuntimeError("API exploded")

    class _FailingClient:
        messages = _FailingMessages()

    class _NoopLimiter:
        def acquire(self, *a, **k):
            # The real limiter's acquire() returns an async context manager
            # directly (it's NOT an async function). Mirror that here so
            # `async with limiter.acquire(context):` works.
            class _Ctx:
                async def __aenter__(_self): return None
                async def __aexit__(_self, *a): return False
            return _Ctx()
        async def record_success(self): pass
        async def record_429(self, *a): pass
        def get_backoff(self, attempt): return 0.0

    monkeypatch.setattr(ydd.ZAIRateLimiter, "get_instance", lambda: _NoopLimiter())

    result = await ydd._retell_one_video(
        client=_FailingClient(),
        model="glm-4.5-air",
        video=ydd.VideoTranscriptPayload(video_id="vid", title="Title", transcript_text="x"),
        max_tokens=2000,
        transcript_char_limit=50_000,
    )
    assert result.error is not None
    assert "API exploded" in result.error
    assert result.classification["value_score"] == 0
    assert result.classification["code_implementation_prospect"] is False


def test_dispatcher_routes_to_single_call_when_env_says_so(monkeypatch):
    monkeypatch.setenv("UA_YOUTUBE_DIGEST_PIPELINE", "single_call")
    calls = {}

    async def _fake_single(prompt):
        calls["got_prompt"] = prompt
        return "single-call output"

    async def _fake_mapreduce(**kwargs):
        calls["got_mapreduce"] = kwargs
        return "map-reduce output"

    monkeypatch.setattr(ydd, "_generate_digest_content_single_call", _fake_single)
    monkeypatch.setattr(ydd, "_generate_digest_content_map_reduce", _fake_mapreduce)

    result = asyncio.run(
        ydd._generate_digest_content(full_prompt="hello world")
    )
    assert result == "single-call output"
    assert calls["got_prompt"] == "hello world"
    assert "got_mapreduce" not in calls


def test_dispatcher_routes_to_map_reduce_by_default(monkeypatch):
    monkeypatch.delenv("UA_YOUTUBE_DIGEST_PIPELINE", raising=False)
    calls = {}

    async def _fake_single(prompt):
        calls["single"] = prompt
        return "single"

    async def _fake_mapreduce(**kwargs):
        calls["mapreduce"] = kwargs
        return "mapreduce"

    monkeypatch.setattr(ydd, "_generate_digest_content_single_call", _fake_single)
    monkeypatch.setattr(ydd, "_generate_digest_content_map_reduce", _fake_mapreduce)

    result = asyncio.run(
        ydd._generate_digest_content(
            videos=[ydd.VideoTranscriptPayload(video_id="a", title="A", transcript_text="t")],
            day_name="MONDAY",
            date_str="2026-05-18",
        )
    )
    assert result == "mapreduce"
    assert "mapreduce" in calls
    assert "single" not in calls


def test_dispatcher_explicit_override_wins_over_env(monkeypatch):
    monkeypatch.setenv("UA_YOUTUBE_DIGEST_PIPELINE", "map_reduce")
    calls = {}

    async def _fake_single(prompt):
        calls["single"] = True
        return "x"

    monkeypatch.setattr(ydd, "_generate_digest_content_single_call", _fake_single)
    asyncio.run(ydd._generate_digest_content(full_prompt="p", pipeline_override="single_call"))
    assert calls.get("single") is True


def test_dispatcher_raises_when_missing_required_args():
    # map_reduce path needs videos+day_name+date_str
    with pytest.raises(ValueError):
        asyncio.run(ydd._generate_digest_content(pipeline_override="map_reduce"))
    # single_call path needs full_prompt
    with pytest.raises(ValueError):
        asyncio.run(ydd._generate_digest_content(pipeline_override="single_call"))


@pytest.mark.asyncio
async def test_map_step_respects_concurrency_semaphore(monkeypatch):
    """At map_concurrency=2 we should never have >2 in-flight calls at once."""
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def _slow_create(**kwargs):
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1

        class _Resp:
            content = [type("B", (), {"text": "## T\n\n### Retelling\nbody.\n\n### Thesis\nshort thesis.\n\n```per_video_classification\n{\"video_id\":\"x\",\"value_score\":50,\"value_tier\":\"medium\",\"code_implementation_prospect\":false,\"concept_only\":true,\"evidence_quality\":\"transcript\",\"reason\":\"r\"}\n```"})()]
        return _Resp()

    class _FakeMessages:
        create = _slow_create

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(ydd, "_build_anthropic_client_for_zai", lambda timeout_seconds: _FakeClient())

    class _NoopLimiter:
        def acquire(self, *a, **k):
            class _Ctx:
                async def __aenter__(_self): return None
                async def __aexit__(_self, *a): return False
            return _Ctx()
        async def record_success(self): pass
        async def record_429(self, *a): pass
        def get_backoff(self, attempt): return 0.0

    monkeypatch.setattr(ydd.ZAIRateLimiter, "get_instance", lambda: _NoopLimiter())

    videos = [
        ydd.VideoTranscriptPayload(video_id=f"v{i}", title=f"T{i}", transcript_text="t")
        for i in range(6)
    ]
    results = await ydd._map_retell_videos(videos, concurrency=2)
    assert len(results) == 6
    assert peak <= 2, f"semaphore breached: peak={peak}"
