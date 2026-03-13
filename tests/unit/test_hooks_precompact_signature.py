from __future__ import annotations

import pytest

from universal_agent.hooks import AgentHookSet


@pytest.mark.asyncio
async def test_on_pre_compact_capture_accepts_legacy_and_new_signatures():
    hooks = AgentHookSet(run_id="unit-precompact-signature")

    # Older shape: (input_data, context)
    result_legacy = await hooks.on_pre_compact_capture({"trigger": "manual"}, {"session_id": "abc"})
    assert isinstance(result_legacy, dict)

    # Newer SDK shape can include additional positional metadata args.
    result_extended = await hooks.on_pre_compact_capture(
        {"trigger": "auto"},
        "hook_call_id_123",
        {"session_id": "def"},
    )
    assert isinstance(result_extended, dict)
