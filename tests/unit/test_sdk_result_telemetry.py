from __future__ import annotations

from universal_agent.agent_core import _extract_result_message_telemetry as core_extract
from universal_agent.main import _extract_result_message_telemetry as main_extract


class _Msg:
    subtype = "result"
    stop_reason = "end_turn"
    duration_ms = 123
    total_cost_usd = 0.1
    num_turns = 2
    session_id = "sess_1"
    is_error = False
    usage = {"input_tokens": 10, "output_tokens": 20}
    model_usage = {"cache_read_input_tokens": 3}


def test_main_extract_result_includes_stop_reason():
    payload = main_extract(_Msg())
    assert payload["stop_reason"] == "end_turn"
    assert payload["usage"]["input_tokens"] == 10


def test_agent_core_extract_result_includes_stop_reason():
    payload = core_extract(_Msg())
    assert payload["stop_reason"] == "end_turn"
    assert payload["model_usage"]["cache_read_input_tokens"] == 3
