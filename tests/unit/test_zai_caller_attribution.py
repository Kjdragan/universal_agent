"""The ZAI 429 monitor must name the real consumer — not the limiter/seam wrappers.

`_call_llm` (the llm_classifier seam) passes the raw SDK method into
`rate_limiter.with_rate_limit_retry`, so without help the stack-walk collapses
every limiter-routed call to `rate_limiter.py`. These tests pin that the
attribution walks PAST both wrapper frames to the true caller, while still
crediting a real `classify_*` consumer that happens to live in the seam file.
"""

from __future__ import annotations

import json
from traceback import FrameSummary


def _stack(*frames: tuple[str, int, str]) -> list[FrameSummary]:
    # extract_stack() returns oldest(outermost) → newest(innermost); the caller
    # walks it reversed. Pass frames outer→inner here.
    return [FrameSummary(fn, ln, name) for (fn, ln, name) in frames]


def test_attribution_skips_limiter_and_call_llm_seam(monkeypatch):
    import universal_agent.services.zai_observability as obs

    fake = _stack(
        ("/opt/ua/src/universal_agent/services/proactive_convergence.py", 100, "_refine_cluster_with_llm"),
        ("/opt/ua/src/universal_agent/services/llm_classifier.py", 240, "_call_llm"),
        ("/opt/ua/src/universal_agent/rate_limiter.py", 300, "with_rate_limit_retry"),
        ("/opt/ua/.venv/lib/python3.12/site-packages/anthropic/resources/messages.py", 50, "create"),
        ("/opt/ua/.venv/lib/python3.12/site-packages/httpx/_client.py", 900, "send"),
        ("/opt/ua/src/universal_agent/services/zai_observability.py", 200, "_on_request_async"),
    )
    monkeypatch.setattr(obs.traceback, "extract_stack", lambda: fake)
    assert obs._identify_caller() == "universal_agent/services/proactive_convergence.py"


def test_attribution_keeps_real_classifier_consumer(monkeypatch):
    """`classify_priority` IS the real consumer even though it routes via the
    `_call_llm` seam in the same file — only the seam frame is skipped, not the
    whole file."""
    import universal_agent.services.zai_observability as obs

    fake = _stack(
        ("/opt/ua/src/universal_agent/services/llm_classifier.py", 60, "classify_priority"),
        ("/opt/ua/src/universal_agent/services/llm_classifier.py", 240, "_call_llm"),
        ("/opt/ua/src/universal_agent/rate_limiter.py", 300, "with_rate_limit_retry"),
        ("/opt/ua/.venv/lib/anthropic/messages.py", 50, "create"),
        ("/opt/ua/src/universal_agent/services/zai_observability.py", 200, "_on_request_async"),
    )
    monkeypatch.setattr(obs.traceback, "extract_stack", lambda: fake)
    assert obs._identify_caller() == "universal_agent/services/llm_classifier.py"


def test_attribution_direct_limiter_caller(monkeypatch):
    import universal_agent.services.zai_observability as obs

    fake = _stack(
        ("/opt/ua/src/universal_agent/services/mission_control_chief_of_staff.py", 80, "readout"),
        ("/opt/ua/src/universal_agent/rate_limiter.py", 300, "with_rate_limit_retry"),
        ("/opt/ua/.venv/lib/anthropic/messages.py", 50, "create"),
        ("/opt/ua/src/universal_agent/services/zai_observability.py", 200, "_on_request_async"),
    )
    monkeypatch.setattr(obs.traceback, "extract_stack", lambda: fake)
    assert obs._identify_caller() == "universal_agent/services/mission_control_chief_of_staff.py"


def test_per_tier_fup_tracked_in_all_windows(tmp_path, monkeypatch):
    """Each tier bucket carries fup / fup_texted so the dashboard cards can show
    429 + FUP per window per tier."""
    monkeypatch.setenv("UA_ZAI_CONTROL_PATH", str(tmp_path / "ctl.json"))
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("UA_ZAI_EVENTS_PATH", str(tmp_path / "events.jsonl"))
    from universal_agent.services import zai_control
    from universal_agent.services.zai_status import build_status

    zai_control._invalidate_cache()
    import time

    now = time.time()
    events = [
        {"ts": now - 5, "category": "rate_limited_429", "model": "glm-4.5-air", "fup_texted": True},
        {"ts": now - 5, "category": "fup_signal", "model": "glm-4.5-air"},
        {"ts": now - 5, "category": "ok", "model": "glm-4.5-air"},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))

    status = build_status()
    haiku_10m = status["events"]["windows"]["10m"]["tiers"]["haiku"]
    assert haiku_10m["r429"] == 1
    assert haiku_10m["fup"] == 1
    assert haiku_10m["fup_texted"] == 1
    # same per-tier shape exists in the 1m and 60m windows
    assert "fup" in status["events"]["windows"]["1m"]["tiers"]["haiku"]
    assert "fup" in status["events"]["windows"]["60m"]["tiers"]["haiku"]
