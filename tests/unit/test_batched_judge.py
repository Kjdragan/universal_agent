"""Unit tests for the reusable structured-output batching helper
(``services.batched_judge.batched_judge``). Mirrors the contract proven on the
convergence judge in PR #989: chunking + call-count collapse, verdict-array
mapping by index, single-item bare-verdict fallback, fail-closed PER CHUNK on a
non-FUP error, the one-shot FUP circuit breaker (re-raise -> skip the rest), the
deadline skip, the ``ok`` cache-safety flag, and ``model_overrides`` passthrough.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from universal_agent.services.batched_judge import BatchedResult, batched_judge


def _make_call_llm(responses, *, record=None):
    """Sequential mock for the injected ``call_llm``. With concurrency=1 the
    chunks run in order, so ``responses[i]`` is the answer for the i-th call that
    actually fires. An ``Exception`` entry is raised instead of returned."""
    state = {"i": 0}

    async def _call(*, system, user, max_tokens, **overrides):
        i = state["i"]
        state["i"] += 1
        if record is not None:
            record.append({"i": i, "max_tokens": max_tokens, "overrides": overrides, "user": user})
        r = responses[i]
        if isinstance(r, BaseException):
            raise r
        return r

    return _call


def _verdicts(*pairs):
    """Build a ``{"verdicts": [...]}`` JSON string from (index, value) pairs."""
    return json.dumps({"verdicts": [{"index": i, "v": v} for i, v in pairs]})


# A simple caller `parse`: echo the item + verdict value. Returns None when the
# verdict value is the sentinel "NEG" (a clean negative — cacheable).
def _parse(item, verdict):
    v = verdict.get("v")
    return None if v == "NEG" else (item, v)


def _run(coro):
    return asyncio.run(coro)


def test_chunks_collapse_calls_and_align_results():
    items = list(range(45))  # 45 items @ batch_size 20 -> 3 chunks
    record = []
    responses = [
        _verdicts(*[(j, f"a{j}") for j in range(20)]),   # chunk 0: items 0..19
        _verdicts(*[(j, f"b{j}") for j in range(20)]),   # chunk 1: items 20..39
        _verdicts(*[(j, f"c{j}") for j in range(5)]),    # chunk 2: items 40..44
    ]
    stats = {}
    out = _run(batched_judge(
        items,
        build_prompt=lambda chunk: "p",
        parse=_parse,
        fail_closed="FC",
        batch_size=20,
        call_llm=_make_call_llm(responses, record=record),
        stats=stats,
    ))
    assert stats["calls_made"] == 3  # N -> ceil(45/20)
    assert len(out) == 45
    assert all(isinstance(r, BatchedResult) and r.ok for r in out)
    assert out[0].value == (0, "a0")
    assert out[19].value == (19, "a19")
    assert out[20].value == (20, "b0")   # chunk-local index 0 -> global 20
    assert out[44].value == (44, "c4")


def test_missing_index_leaves_that_item_fail_closed():
    items = [0, 1, 2]
    responses = [_verdicts((0, "x"), (2, "z"))]  # index 1 omitted
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=20, call_llm=_make_call_llm(responses),
    ))
    assert out[0].ok and out[0].value == (0, "x")
    assert out[1].ok is False and out[1].value == "FC"   # not in verdicts -> fail closed
    assert out[2].ok and out[2].value == (2, "z")


def test_non_fup_error_fails_chunk_closed_others_survive():
    items = list(range(40))  # 2 chunks
    responses = [
        RuntimeError("transient parse error, not fair-usage"),  # chunk 0 fails
        _verdicts(*[(j, f"b{j}") for j in range(20)]),          # chunk 1 ok
    ]
    stats = {}
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=20, call_llm=_make_call_llm(responses),
        is_fup=lambda s: "1313" in s or "fair usage" in s.lower(), stats=stats,
    ))
    # chunk 0 (items 0..19) all fail closed; chunk 1 (20..39) all ok
    assert all((not out[i].ok) and out[i].value == "FC" for i in range(20))
    assert all(out[i].ok for i in range(20, 40))
    assert stats["calls_made"] == 1        # only chunk 1 completed a call
    assert stats["chunks_failed"] == 1


def test_fup_signal_trips_breaker_and_skips_remaining_chunks():
    items = list(range(60))  # 3 chunks @ 20
    responses = [
        RuntimeError("HTTP 429: [1313] Fair Usage Policy"),  # chunk 0 -> FUP
        _verdicts((0, "should-not-run")),                    # never reached
        _verdicts((0, "should-not-run")),
    ]
    stats = {}
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=20, call_llm=_make_call_llm(responses),
        is_fup=lambda s: "1313" in s, stats=stats,
    ))
    # Every item fails closed; only one call was attempted (chunk 0), the rest skipped.
    assert all((not r.ok) and r.value == "FC" for r in out)
    assert stats["calls_made"] == 0
    assert stats["chunks_failed"] == 1
    assert stats["skipped"] == 40  # chunks 1 and 2 (20 each) skipped without a call


def test_single_item_bare_verdict_fallback():
    items = [99]
    # Model answers a 1-item chunk with a BARE object, no "verdicts" array.
    responses = [json.dumps({"v": "solo"})]
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=1, call_llm=_make_call_llm(responses),
    ))
    assert out[0].ok and out[0].value == (99, "solo")


def test_multi_item_chunk_without_verdicts_array_fails_closed():
    items = [0, 1]
    responses = [json.dumps({"v": "bare"})]  # bare object but chunk len != 1
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=20, call_llm=_make_call_llm(responses),
    ))
    assert all((not r.ok) and r.value == "FC" for r in out)


def test_ok_flag_distinguishes_clean_negative_from_fail_closed():
    items = [0, 1]
    responses = [_verdicts((0, "NEG"), (1, "yes"))]  # item 0 -> clean negative
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=20, call_llm=_make_call_llm(responses),
    ))
    # Clean negative: ok=True, value=None (cacheable). Fail-closed would be ok=False.
    assert out[0].ok is True and out[0].value is None
    assert out[1].ok is True and out[1].value == (1, "yes")


def test_fail_closed_callable_is_invoked_per_item():
    items = ["a", "b"]
    responses = [RuntimeError("boom")]
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse,
        fail_closed=lambda item: f"fc:{item}",
        batch_size=20, call_llm=_make_call_llm(responses),
        is_fup=lambda s: False,
    ))
    assert out[0].value == "fc:a" and out[0].ok is False
    assert out[1].value == "fc:b" and out[1].ok is False


def test_model_overrides_and_max_tokens_passed_through():
    items = [0, 1, 2]
    record = []
    responses = [_verdicts((0, "x"), (1, "y"), (2, "z"))]
    _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=20,
        model_overrides={"model": "glm-5-turbo", "base_url": "https://x", "api_key": "k"},
        max_tokens_for=lambda chunk: 1234,
        call_llm=_make_call_llm(responses, record=record),
    ))
    assert record[0]["overrides"] == {"model": "glm-5-turbo", "base_url": "https://x", "api_key": "k"}
    assert record[0]["max_tokens"] == 1234


def test_deadline_skips_all_chunks():
    items = list(range(20))
    stats = {}
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=20, call_llm=_make_call_llm([_verdicts((0, "x"))]),
        deadline=time.monotonic() - 1.0, stats=stats,
    ))
    assert all((not r.ok) and r.value == "FC" for r in out)
    assert stats["calls_made"] == 0
    assert stats["skipped"] == 20


def test_parse_raising_leaves_only_that_item_fail_closed():
    items = [0, 1, 2]
    responses = [_verdicts((0, "x"), (1, "boom"), (2, "z"))]

    def parse(item, verdict):
        if verdict.get("v") == "boom":
            raise ValueError("bad verdict shape")
        return (item, verdict.get("v"))

    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=parse, fail_closed="FC",
        batch_size=20, call_llm=_make_call_llm(responses),
    ))
    assert out[0].ok and out[0].value == (0, "x")
    assert out[1].ok is False and out[1].value == "FC"  # parse raised -> this item only
    assert out[2].ok and out[2].value == (2, "z")


def test_empty_items_returns_empty():
    out = _run(batched_judge(
        [], build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        call_llm=_make_call_llm([]),
    ))
    assert out == []


def test_batch_size_one_is_legacy_per_item():
    items = [0, 1, 2]
    # Each chunk is one item; model returns a bare verdict each time.
    responses = [json.dumps({"v": f"s{i}"}) for i in range(3)]
    stats = {}
    out = _run(batched_judge(
        items, build_prompt=lambda c: "p", parse=_parse, fail_closed="FC",
        batch_size=1, call_llm=_make_call_llm(responses), stats=stats,
    ))
    assert stats["calls_made"] == 3
    assert [r.value for r in out] == [(0, "s0"), (1, "s1"), (2, "s2")]
