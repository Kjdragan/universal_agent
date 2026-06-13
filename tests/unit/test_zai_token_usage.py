"""Per-process ZAI token capture + aggregation (the ZAI-Control token panel data).

Covers:
- ``zai_observability._usage_from_response`` — Anthropic- and OpenAI-compatible
  ``usage`` shapes, fail-soft to zeros.
- ``zai_observability._is_streaming_response`` — the SSE guard that keeps the
  force-read off streaming bodies.
- ``zai_observability._identify_caller_fn`` — ``file::function`` stage key.
- ``zai_status.analyze_token_usage`` — per-process token sums, retry-waste +
  retry-multiplier churn signal, per-stage breakdown, window filtering, and
  graceful degradation when events carry no token fields (request-count proxy).
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from universal_agent.services import zai_observability as obs, zai_status


def _fake_response(*, content: bytes, content_type: str = "application/json"):
    return SimpleNamespace(content=content, headers={"content-type": content_type})


# ── capture: usage extraction ───────────────────────────────────────────────

def test_usage_from_response_anthropic_shape():
    body = json.dumps({
        "model": "glm-5.1",
        "usage": {"input_tokens": 1234, "output_tokens": 56,
                  "cache_read_input_tokens": 700},
    }).encode()
    u = obs._usage_from_response(_fake_response(content=body))
    assert u["input_tokens"] == 1234
    assert u["output_tokens"] == 56
    assert u["cache_read_input_tokens"] == 700
    assert u["cache_creation_input_tokens"] == 0


def test_usage_from_response_openai_shape():
    body = json.dumps({"usage": {"prompt_tokens": 800, "completion_tokens": 40}}).encode()
    u = obs._usage_from_response(_fake_response(content=body))
    assert u["input_tokens"] == 800
    assert u["output_tokens"] == 40


def test_usage_from_response_failsoft_to_zero():
    # No usage block, empty body, and non-JSON all fail soft to zeros.
    assert obs._usage_from_response(_fake_response(content=b"")) == {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    assert obs._usage_from_response(_fake_response(content=b"not json")) ["input_tokens"] == 0
    no_usage = json.dumps({"model": "glm-5.1"}).encode()
    assert obs._usage_from_response(_fake_response(content=no_usage))["output_tokens"] == 0


def test_is_streaming_response_guard():
    assert obs._is_streaming_response(_fake_response(content=b"", content_type="text/event-stream")) is True
    assert obs._is_streaming_response(_fake_response(content=b"", content_type="application/json")) is False


def test_identify_caller_fn_is_file_colon_function():
    # Called from this test function, the resolved caller_fn should end with the
    # current function name (this frame is the first non-framework UA frame).
    fn = obs._identify_caller_fn()
    assert "::" in fn
    assert fn.rsplit("::", 1)[-1] == "test_identify_caller_fn_is_file_colon_function"


# ── aggregation ─────────────────────────────────────────────────────────────

def _write_events(tmp_path, monkeypatch, events):
    path = tmp_path / "zai_events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    monkeypatch.setenv("UA_ZAI_EVENTS_PATH", str(path))
    return path


def test_token_aggregation_sums_retry_waste_and_stages(tmp_path, monkeypatch):
    now = time.time()
    conv = "universal_agent/services/proactive_convergence.py"
    events = [
        # convergence brief stage: one 429 (wasted resend) then a success
        {"ts": now - 100, "category": "rate_limited_429", "model": "glm-5.1",
         "caller": conv, "caller_fn": f"{conv}::_brief_writer",
         "input_tokens": 5000, "output_tokens": 0},
        {"ts": now - 90, "category": "ok", "model": "glm-5.1",
         "caller": conv, "caller_fn": f"{conv}::_brief_writer",
         "input_tokens": 5000, "output_tokens": 1200},
        # convergence judge stage (different caller_fn)
        {"ts": now - 80, "category": "ok", "model": "glm-5-turbo",
         "caller": conv, "caller_fn": f"{conv}::_detect_clusters_llm_async",
         "input_tokens": 800, "output_tokens": 50},
    ]
    _write_events(tmp_path, monkeypatch, events)

    rep = zai_status.analyze_token_usage(now, window_seconds=3600)
    assert rep["available"] is True
    assert rep["token_events_seen"] == 3
    assert rep["totals"]["input_tokens"] == 10800
    assert rep["totals"]["output_tokens"] == 1250
    assert rep["totals"]["retry_input_tokens"] == 5000  # the 429'd attempt

    procs = {p["caller"]: p for p in rep["processes"]}
    p = procs[conv]
    assert p["requests"] == 3
    assert p["r429"] == 1
    assert p["input_tokens"] == 10800
    assert p["retry_input_tokens"] == 5000
    # retry_multiplier ≈ total_input / first_attempt_input = 10800 / 5800
    assert p["retry_multiplier"] == round(10800 / 5800, 2)
    # two stages, the brief writer is the heavier one and ranks first
    stage_fns = [s["caller_fn"].rsplit("::", 1)[-1] for s in p["stages"]]
    assert stage_fns[0] == "_brief_writer"
    assert "_detect_clusters_llm_async" in stage_fns


def test_token_aggregation_window_filtering(tmp_path, monkeypatch):
    now = time.time()
    c = "universal_agent/services/x.py"
    events = [
        {"ts": now - 30, "category": "ok", "model": "glm-5-turbo", "caller": c,
         "caller_fn": f"{c}::f", "input_tokens": 100, "output_tokens": 10},
        {"ts": now - 7200, "category": "ok", "model": "glm-5-turbo", "caller": c,
         "caller_fn": f"{c}::f", "input_tokens": 999, "output_tokens": 999},  # 2h old
    ]
    _write_events(tmp_path, monkeypatch, events)
    rep = zai_status.analyze_token_usage(now, window_seconds=3600)  # 1h window
    assert rep["totals"]["requests"] == 1
    assert rep["totals"]["input_tokens"] == 100


def test_token_aggregation_graceful_without_token_fields(tmp_path, monkeypatch):
    # Pre-upgrade events (no token fields) → request-count proxy, 0 tokens,
    # ranked by call volume, token_events_seen == 0.
    now = time.time()
    events = [
        {"ts": now - 10, "category": "ok", "model": "glm-5.1",
         "caller": "universal_agent/a.py"},
        {"ts": now - 11, "category": "rate_limited_429", "model": "glm-5.1",
         "caller": "universal_agent/a.py"},
        {"ts": now - 12, "category": "ok", "model": "glm-5-turbo",
         "caller": "universal_agent/b.py"},
    ]
    _write_events(tmp_path, monkeypatch, events)
    rep = zai_status.analyze_token_usage(now, window_seconds=3600)
    assert rep["available"] is True
    assert rep["token_events_seen"] == 0
    assert rep["totals"]["input_tokens"] == 0
    assert rep["totals"]["requests"] == 3
    # a.py has 2 calls, ranks above b.py (1 call) when tokens tie at 0
    assert rep["processes"][0]["caller"] == "universal_agent/a.py"
    assert rep["processes"][0]["reject_pct"] == 50.0
