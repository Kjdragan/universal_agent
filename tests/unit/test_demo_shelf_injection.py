"""The capability-shelf block is injected into BOTH proactive-demo judges when a
shelf is available, and leaves each judge's system prompt byte-identical when it
is not (fail-safe / additive — S3 eureka scoring).
"""

from __future__ import annotations

import asyncio

import universal_agent.services.demo_shelf_context as dsc
import universal_agent.services.llm_classifier as llm_classifier
import universal_agent.services.proactive_demo_nuggets as nuggets

_SENTINEL = "\n\n<<CAPABILITY-SHELF-SENTINEL>>"


# ── buildability judge (llm_classifier.classify_tutorial_buildability) ──────────
def _capture_call_llm(box: dict):
    async def _stub(*, system: str, user: str, **_kwargs) -> str:
        box["system"] = system
        return '{"buildable": false, "reasoning": "stub"}'

    return _stub


def test_buildability_prompt_carries_shelf_when_available(monkeypatch):
    box: dict = {}
    monkeypatch.setattr(llm_classifier, "_call_llm", _capture_call_llm(box))
    monkeypatch.setattr(dsc, "capability_shelf_block", lambda root=None: _SENTINEL)
    # No threshold set → binary variant is the base prompt.
    monkeypatch.delenv("UA_TUTORIAL_BUILD_THRESHOLD", raising=False)

    asyncio.run(
        llm_classifier.classify_tutorial_buildability(
            title="Build an agent", channel_name="chan", summary_text="uses an SDK to build a tool"
        )
    )

    assert box["system"].endswith(_SENTINEL)
    assert box["system"] == llm_classifier._TUTORIAL_BUILDABILITY_SYSTEM + _SENTINEL


def test_buildability_prompt_unchanged_when_shelf_empty(monkeypatch):
    box: dict = {}
    monkeypatch.setattr(llm_classifier, "_call_llm", _capture_call_llm(box))
    monkeypatch.setattr(dsc, "capability_shelf_block", lambda root=None: "")
    monkeypatch.delenv("UA_TUTORIAL_BUILD_THRESHOLD", raising=False)

    asyncio.run(
        llm_classifier.classify_tutorial_buildability(
            title="Build an agent", channel_name="chan", summary_text="uses an SDK to build a tool"
        )
    )

    assert box["system"] == llm_classifier._TUTORIAL_BUILDABILITY_SYSTEM


# ── golden-nuggets judge (proactive_demo_nuggets._judge_candidates) ─────────────
def _capture_nuggets_call(box: dict):
    def _call(system: str, user: str) -> str:
        box["system"] = system
        return '{"index": 0, "score": 3.0, "build": false, "reason": "stub"}'

    return _call


def test_nuggets_prompt_carries_shelf_when_available(monkeypatch):
    box: dict = {}
    monkeypatch.setattr(dsc, "capability_shelf_block", lambda root=None: _SENTINEL)

    nuggets._judge_candidates(
        [{"video_title": "A demo", "channel_name": "c", "summary": "does a thing"}],
        call_llm=_capture_nuggets_call(box),
    )

    assert box["system"].endswith(_SENTINEL)
    assert box["system"] == nuggets._JUDGE_SYSTEM_PROMPT + _SENTINEL


def test_nuggets_prompt_unchanged_when_shelf_empty(monkeypatch):
    box: dict = {}
    monkeypatch.setattr(dsc, "capability_shelf_block", lambda root=None: "")

    nuggets._judge_candidates(
        [{"video_title": "A demo", "channel_name": "c", "summary": "does a thing"}],
        call_llm=_capture_nuggets_call(box),
    )

    assert box["system"] == nuggets._JUDGE_SYSTEM_PROMPT
