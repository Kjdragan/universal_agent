"""Tests for the P3 batched tutorial-buildability judge:
- ``llm_classifier.classify_tutorial_buildability_batched`` (one call per chunk,
  video_id mapping, method='llm'/'fallback' semantics, empty-summary drop, tier);
- ``proactive_tutorial_builds._judge_buildable_ids`` cache-read-first batched gate
  (cache hits skip the LLM; method='fallback' is not cached) and the legacy path.

Mirrors ``test_wiki_facets_batched.py``: the fake patches the imported
``llm_classifier._call_llm`` OBJECT (the seam ``batched_judge`` lazily imports).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from universal_agent.services import (
    llm_classifier as lc,
    proactive_tutorial_builds as ptb,
)


def _install_fake_buildability_llm(monkeypatch, *, record=None, raise_exc=None, buildable_for=None):
    import universal_agent.services.llm_classifier as llm_classifier

    async def fake_call_llm(*, system, user, max_tokens, **overrides):
        if record is not None:
            record.append({"system": system, "user": user, "overrides": dict(overrides)})
        if raise_exc is not None:
            raise raise_exc
        payload = json.loads(user)
        verdicts = [
            {
                "index": v["index"],
                "buildable": (buildable_for(v) if buildable_for else True),
                "reasoning": f"r{v['index']}",
            }
            for v in payload["videos"]
        ]
        return json.dumps({"verdicts": verdicts})

    monkeypatch.setattr(llm_classifier, "_call_llm", fake_call_llm)


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


# ── classify_tutorial_buildability_batched ─────────────────────────────────


def test_buildability_batched_maps_each_video(monkeypatch):
    _install_fake_buildability_llm(monkeypatch, buildable_for=lambda v: "build" in v["summary"].lower())
    items = [
        {"video_id": "a", "title": "t", "channel_name": "c", "summary_text": "build an agent"},
        {"video_id": "b", "title": "t", "channel_name": "c", "summary_text": "a news piece"},
    ]
    out = asyncio.run(lc.classify_tutorial_buildability_batched(items, batch_size=20))
    assert out["a"] == {"buildable": True, "reasoning": "r0", "method": "llm"}
    assert out["b"]["buildable"] is False
    assert out["b"]["method"] == "llm"  # a real (negative) judgement → cacheable


def test_buildability_batched_single_call_for_chunk(monkeypatch):
    record: list = []
    _install_fake_buildability_llm(monkeypatch, record=record)
    items = [{"video_id": f"v{i}", "title": "t", "channel_name": "c", "summary_text": "build"} for i in range(5)]
    out = asyncio.run(lc.classify_tutorial_buildability_batched(items, batch_size=20))
    assert len(out) == 5
    assert len(record) == 1  # one batched call for five videos


def test_buildability_batched_call_failure_all_fallback(monkeypatch):
    _install_fake_buildability_llm(monkeypatch, raise_exc=RuntimeError("llm down, not fair-usage"))
    items = [{"video_id": f"v{i}", "title": "t", "channel_name": "c", "summary_text": "build"} for i in range(3)]
    out = asyncio.run(lc.classify_tutorial_buildability_batched(items, batch_size=20))
    assert len(out) == 3
    # whole-chunk failure → fail-closed: method='fallback' (caller must NOT cache).
    assert all(v["method"] == "fallback" and v["buildable"] is False for v in out.values())


def test_buildability_batched_drops_empty_summary(monkeypatch):
    record: list = []
    _install_fake_buildability_llm(monkeypatch, record=record)
    items = [
        {"video_id": "a", "title": "t", "channel_name": "c", "summary_text": "build"},
        {"video_id": "b", "title": "t", "channel_name": "c", "summary_text": "   "},
    ]
    out = asyncio.run(lc.classify_tutorial_buildability_batched(items, batch_size=20))
    assert set(out) == {"a"}  # empty-summary video never judged
    assert len(json.loads(record[0]["user"])["videos"]) == 1


def test_buildability_batched_uses_haiku_tier(monkeypatch):
    monkeypatch.delenv("UA_TUTORIAL_BUILDABILITY_MODEL", raising=False)
    record: list = []
    _install_fake_buildability_llm(monkeypatch, record=record)
    asyncio.run(
        lc.classify_tutorial_buildability_batched(
            [{"video_id": "a", "title": "t", "channel_name": "c", "summary_text": "build"}], batch_size=20
        )
    )
    from universal_agent.utils.model_resolution import resolve_haiku

    assert record[0]["overrides"].get("model") == resolve_haiku()


def test_buildability_batched_model_override_env(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_MODEL", "custom-build-model")
    record: list = []
    _install_fake_buildability_llm(monkeypatch, record=record)
    asyncio.run(
        lc.classify_tutorial_buildability_batched(
            [{"video_id": "a", "title": "t", "channel_name": "c", "summary_text": "build"}], batch_size=20
        )
    )
    assert record[0]["overrides"].get("model") == "custom-build-model"


def test_buildability_batched_empty_input(monkeypatch):
    record: list = []
    _install_fake_buildability_llm(monkeypatch, record=record)
    assert asyncio.run(lc.classify_tutorial_buildability_batched([], batch_size=20)) == {}
    assert record == []


# ── _judge_buildable_ids (driver gate) ─────────────────────────────────────


def _prelim(video_id: str, summary: str = "build an agent") -> dict:
    return {"video_id": video_id, "title": "t", "channel": "c", "summary": summary}


def test_judge_ids_batched_cache_read_first(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "1")
    record: list = []
    _install_fake_buildability_llm(monkeypatch, record=record, buildable_for=lambda v: True)
    conn = _conn()
    ptb._cache_judge_verdict(conn, video_id="cached_yes", buildable=True, reasoning="", method="llm")
    ptb._cache_judge_verdict(conn, video_id="cached_no", buildable=False, reasoning="", method="llm")
    prelim = [_prelim("cached_yes"), _prelim("cached_no"), _prelim("fresh")]
    ids = ptb._judge_buildable_ids(conn, prelim)
    assert "cached_yes" in ids
    assert "cached_no" not in ids
    assert "fresh" in ids  # uncached, judged buildable
    # Only the UNCACHED video reached the LLM (the token win at steady state).
    assert len(record) == 1
    assert len(json.loads(record[0]["user"])["videos"]) == 1


def test_judge_ids_batched_fallback_not_cached(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "1")
    _install_fake_buildability_llm(monkeypatch, raise_exc=RuntimeError("llm down, not fair-usage"))
    conn = _conn()
    ids = ptb._judge_buildable_ids(conn, [_prelim("v0")])
    assert ids == set()
    # method='fallback' ⇒ NOT cached ⇒ re-judged next sweep.
    assert ptb._get_cached_judge_verdict(conn, "v0") is None


def test_judge_ids_batched_caches_llm_verdict(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "1")
    _install_fake_buildability_llm(monkeypatch, buildable_for=lambda v: True)
    conn = _conn()
    ptb._judge_buildable_ids(conn, [_prelim("v0")])
    cached = ptb._get_cached_judge_verdict(conn, "v0")
    assert cached is not None
    assert cached["buildable"] is True
    assert cached["method"] == "llm"


def test_judge_ids_batched_excludes_empty_summary(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "1")
    record: list = []
    _install_fake_buildability_llm(monkeypatch, record=record, buildable_for=lambda v: True)
    conn = _conn()
    ids = ptb._judge_buildable_ids(conn, [_prelim("empty", summary="   "), _prelim("ok")])
    assert ids == {"ok"}
    assert len(json.loads(record[0]["user"])["videos"]) == 1


def test_judge_ids_legacy_uses_per_video(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "1")  # legacy
    calls: list = []

    def _stub(conn, *, video_id, title, channel_name, summary_text):
        calls.append(video_id)
        return video_id == "yes"

    monkeypatch.setattr(ptb, "is_video_buildable_with_judge", _stub)
    conn = _conn()
    ids = ptb._judge_buildable_ids(conn, [_prelim("yes"), _prelim("no")])
    assert ids == {"yes"}
    assert set(calls) == {"yes", "no"}  # legacy path judges each video individually


# ── re-ingestion / analysis-lag duplicate (newest row empty-summary) ───────
# Rows arrive newest-first (ORDER BY e.id DESC). A re-ingested video's NEWEST row
# often carries an empty summary (analysis not yet backfilled) while an OLDER row
# has the real summary. The video must NOT be starved, and the empty duplicate row
# must NOT become a second candidate (it would steal a daily-ceiling position).


def test_judge_ids_legacy_empty_first_duplicate_not_starved(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "1")  # default/legacy
    monkeypatch.setenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "1")
    seen: list = []

    def _stub(conn, *, video_id, title, channel_name, summary_text):
        seen.append((video_id, summary_text))
        return bool(str(summary_text).strip())  # buildable iff non-empty summary

    monkeypatch.setattr(ptb, "is_video_buildable_with_judge", _stub)
    conn = _conn()
    # newest-first: empty-summary row precedes the real-summary row of the SAME video
    prelim = [_prelim("V", summary="   "), _prelim("V", summary="build an agent")]
    ids = ptb._judge_buildable_ids(conn, prelim)
    assert ids == {"V"}  # NOT starved
    # the empty row was skipped before marking judged; the judge ran once with the REAL summary
    assert seen == [("V", "build an agent")]


def test_judge_ids_batched_empty_first_duplicate_not_starved(monkeypatch):
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "20")
    monkeypatch.setenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "1")
    record: list = []
    _install_fake_buildability_llm(monkeypatch, record=record, buildable_for=lambda v: True)
    conn = _conn()
    prelim = [_prelim("V", summary="   "), _prelim("V", summary="build an agent")]
    ids = ptb._judge_buildable_ids(conn, prelim)
    assert ids == {"V"}  # batched branch agrees with legacy
    sent = json.loads(record[0]["user"])["videos"]
    assert len(sent) == 1 and sent[0]["summary"] == "build an agent"  # only the real summary judged


def test_driver_empty_summary_duplicate_neither_starved_nor_overcounted(monkeypatch, tmp_path):
    """Full-driver regression (default path): a video with a newest empty-summary
    row + an older real-summary row appears EXACTLY ONCE in the candidates handed to
    the ceiling — not starved (the bug if it were dropped) and not doubled (the bug
    if the empty row survived the post-filter)."""
    monkeypatch.setenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "1")  # shipped default
    monkeypatch.setenv("UA_TUTORIAL_BUILD_JUDGE_ENABLED", "1")
    monkeypatch.setenv("UA_PROACTIVE_TUTORIAL_AUTO_ROUTE", "1")
    monkeypatch.setattr(ptb, "_looks_build_oriented", lambda **k: True)
    monkeypatch.setattr(
        ptb,
        "is_video_buildable_with_judge",
        lambda conn, *, video_id, title, channel_name, summary_text: bool(str(summary_text).strip()),
    )
    captured: dict = {}

    def _capture_ceiling(conn, candidates, *, source):
        captured["candidates"] = candidates
        return {"auto_queued": 0, "auto_new": 0, "auto_reaffirmed": 0, "pending_approval": 0, "ceiling": 10, "today_count": 0, "remaining": 10}

    monkeypatch.setattr(ptb, "queue_tutorial_builds_with_ceiling", _capture_ceiling)

    csi = tmp_path / "csi.db"
    db = sqlite3.connect(csi)
    db.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, occurred_at TEXT,
            subject_json TEXT, source TEXT
        );
        CREATE TABLE rss_event_analysis (
            event_id TEXT, category TEXT, summary_text TEXT, analysis_json TEXT, transcript_status TEXT
        );
        """
    )
    subj_v = json.dumps({"video_id": "V", "title": "tV", "channel_name": "c", "url": "uV"})
    subj_w = json.dumps({"video_id": "W", "title": "tW", "channel_name": "c", "url": "uW"})
    # OLDER real-summary V (lowest id) → NEWEST empty-summary V (higher id) → W.
    db.execute("INSERT INTO events (event_id, occurred_at, subject_json, source) VALUES (?,?,?,?)", ("evV_old", "2026-06-10T00:00:00Z", subj_v, "youtube_channel_rss"))
    db.execute("INSERT INTO rss_event_analysis VALUES (?,?,?,?,?)", ("evV_old", "tech", "build an agent with code", "{}", "ok"))
    db.execute("INSERT INTO events (event_id, occurred_at, subject_json, source) VALUES (?,?,?,?)", ("evV_new", "2026-06-12T00:00:00Z", subj_v, "youtube_channel_rss"))
    db.execute("INSERT INTO rss_event_analysis VALUES (?,?,?,?,?)", ("evV_new", "tech", "   ", "{}", ""))
    db.execute("INSERT INTO events (event_id, occurred_at, subject_json, source) VALUES (?,?,?,?)", ("evW", "2026-06-11T00:00:00Z", subj_w, "youtube_channel_rss"))
    db.execute("INSERT INTO rss_event_analysis VALUES (?,?,?,?,?)", ("evW", "tech", "build a tool", "{}", "ok"))
    db.commit()
    db.close()

    from pathlib import Path

    ptb.sync_build_oriented_csi_videos(_conn(), csi_db_path=Path(csi), limit=100)
    vids = sorted(c["video_id"] for c in captured["candidates"])
    assert vids == ["V", "W"]  # V exactly once (empty dup excluded, real row kept), W once
