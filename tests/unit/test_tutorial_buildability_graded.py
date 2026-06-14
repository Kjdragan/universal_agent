"""Phase 2 of the graded-judge redesign: the tutorial-buildability judge gains an
opt-in graded 0-100 score + code-side threshold, activated ONLY when
``UA_TUTORIAL_BUILD_THRESHOLD`` is set. Unset ⇒ the legacy binary buildable/not
path (pinned green by ``test_tutorial_buildability_batched.py``).

Covers both the single-video ``classify_tutorial_buildability`` and the batched
``classify_tutorial_buildability_batched``, plus temperature forwarding.
"""

from __future__ import annotations

import asyncio
import json

from universal_agent.services import llm_classifier as lc

# ── single-video classify_tutorial_buildability (graded) ────────────────────


def _patch_single(monkeypatch, payload, record=None):
    text = payload if isinstance(payload, str) else json.dumps(payload)

    async def fake(*, system, user, max_tokens, model=None, temperature=None, **kw):
        if record is not None:
            record.append({"system": system, "temperature": temperature, "model": model})
        return text

    monkeypatch.setattr(lc, "_call_llm", fake)


def test_graded_single_buildable(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    record: list = []
    _patch_single(monkeypatch, {"score": 88, "reasoning": "specific tools + steps"}, record)
    out = asyncio.run(lc.classify_tutorial_buildability(title="t", channel_name="c", summary_text="build an agent"))
    assert out["buildable"] is True
    assert out["method"] == "llm"
    assert "HOW BUILDABLE" in record[0]["system"]  # graded prompt


def test_graded_single_not_buildable_below_threshold(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    _patch_single(monkeypatch, {"score": 40, "reasoning": "newsy"})
    out = asyncio.run(lc.classify_tutorial_buildability(title="t", summary_text="a news piece"))
    assert out["buildable"] is False
    assert out["method"] == "llm"


def test_graded_single_missing_score_not_buildable(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    _patch_single(monkeypatch, {"reasoning": "no score field"})
    out = asyncio.run(lc.classify_tutorial_buildability(title="t", summary_text="x"))
    # Undecidable ⇒ fail closed to not-buildable, but a present verdict ⇒ cacheable.
    assert out["buildable"] is False
    assert out["method"] == "llm"


def test_graded_single_forwards_temperature(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0")
    record: list = []
    _patch_single(monkeypatch, {"score": 50, "reasoning": "x"}, record)
    asyncio.run(lc.classify_tutorial_buildability(title="t", summary_text="x"))
    assert record[0]["temperature"] == 0.0


def test_graded_single_per_gate_temperature_wins(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0.5")
    monkeypatch.setenv("UA_TUTORIAL_BUILD_TEMPERATURE", "0")
    record: list = []
    _patch_single(monkeypatch, {"score": 50, "reasoning": "x"}, record)
    asyncio.run(lc.classify_tutorial_buildability(title="t", summary_text="x"))
    assert record[0]["temperature"] == 0.0  # per-gate overrides global


def test_graded_single_score_at_threshold_buildable(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    _patch_single(monkeypatch, {"score": 70, "reasoning": "exactly at cutoff"})
    out = asyncio.run(lc.classify_tutorial_buildability(title="t", summary_text="build"))
    assert out["buildable"] is True  # >= cutoff


def test_graded_batched_per_gate_temperature_wins(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0.5")
    monkeypatch.setenv("UA_TUTORIAL_BUILD_TEMPERATURE", "0")
    record: list = []
    _install_fake_graded_batch(monkeypatch, record=record)
    asyncio.run(
        lc.classify_tutorial_buildability_batched(
            [{"video_id": "a", "title": "t", "channel_name": "c", "summary_text": "build"}], batch_size=20
        )
    )
    assert record[0]["overrides"].get("temperature") == 0.0  # per-gate overrides global


def test_categorical_single_unaffected(monkeypatch):
    monkeypatch.delenv("UA_TUTORIAL_BUILD_THRESHOLD", raising=False)
    monkeypatch.delenv("UA_LLM_JUDGE_TEMPERATURE", raising=False)
    record: list = []
    _patch_single(monkeypatch, {"buildable": True, "reasoning": "x"}, record)
    out = asyncio.run(lc.classify_tutorial_buildability(title="t", summary_text="build an agent"))
    assert out["buildable"] is True
    assert record[0]["temperature"] is None
    assert "HOW BUILDABLE" not in record[0]["system"]  # binary prompt


# ── batched classify_tutorial_buildability_batched (graded) ─────────────────


def _install_fake_graded_batch(monkeypatch, *, record=None, score_for=None):
    import universal_agent.services.llm_classifier as m

    async def fake(*, system, user, max_tokens, **overrides):
        if record is not None:
            record.append({"system": system, "overrides": dict(overrides)})
        payload = json.loads(user)
        verdicts = [
            {"index": v["index"], "score": (score_for(v) if score_for else 90), "reasoning": f"r{v['index']}"}
            for v in payload["videos"]
        ]
        return json.dumps({"verdicts": verdicts})

    monkeypatch.setattr(m, "_call_llm", fake)


def test_graded_batched_threshold(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    record: list = []
    _install_fake_graded_batch(
        monkeypatch, record=record, score_for=lambda v: 90 if "build" in v["summary"].lower() else 30
    )
    items = [
        {"video_id": "a", "title": "t", "channel_name": "c", "summary_text": "build an agent"},
        {"video_id": "b", "title": "t", "channel_name": "c", "summary_text": "a news piece"},
    ]
    out = asyncio.run(lc.classify_tutorial_buildability_batched(items, batch_size=20))
    assert out["a"]["buildable"] is True
    assert out["b"]["buildable"] is False
    assert out["a"]["method"] == "llm"
    assert "SCORE EACH video" in record[0]["system"]  # graded batch prompt
    assert "temperature" not in record[0]["overrides"]


def test_graded_batched_forwards_temperature(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    monkeypatch.setenv("UA_LLM_JUDGE_TEMPERATURE", "0")
    record: list = []
    _install_fake_graded_batch(monkeypatch, record=record)
    asyncio.run(
        lc.classify_tutorial_buildability_batched(
            [{"video_id": "a", "title": "t", "channel_name": "c", "summary_text": "build"}], batch_size=20
        )
    )
    assert record[0]["overrides"].get("temperature") == 0.0


def test_graded_batched_missing_score_not_buildable(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILD_THRESHOLD", "70")
    import universal_agent.services.llm_classifier as m

    async def fake(*, system, user, max_tokens, **overrides):
        payload = json.loads(user)
        return json.dumps({"verdicts": [{"index": v["index"], "reasoning": "no score"} for v in payload["videos"]]})

    monkeypatch.setattr(m, "_call_llm", fake)
    out = asyncio.run(
        lc.classify_tutorial_buildability_batched(
            [{"video_id": "a", "title": "t", "channel_name": "c", "summary_text": "build"}], batch_size=20
        )
    )
    assert out["a"]["buildable"] is False
    assert out["a"]["method"] == "llm"  # present verdict ⇒ cacheable
