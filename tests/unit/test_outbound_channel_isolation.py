"""Regression tests for the session-wide outbound-channel isolation.

2026-07-11: a desktop test run (real Infisical secrets in the developer
environment) sent five real "[ERROR] unit_test_raiser / heartbeat_service"
fixture emails to the operator through Simone's live AgentMail inbox. The
``_isolate_outbound_channels`` conftest fixture is the systemic backstop;
these tests fail if it is ever removed or stops covering a channel.
"""

from __future__ import annotations

import os


def test_agentmail_api_key_is_scrubbed():
    assert os.environ.get("AGENTMAIL_API_KEY") is None


def test_telegram_bot_token_is_scrubbed():
    assert os.environ.get("TELEGRAM_BOT_TOKEN") is None


def test_gmail_cli_fallback_is_forced_off():
    assert os.environ.get("UA_AGENTMAIL_GMAIL_FALLBACK") == "0"


def test_infisical_refetch_is_disabled():
    # Without this, initialize_runtime_secrets() inside a test process could
    # re-load the scrubbed keys from Infisical and re-open the leak.
    assert os.environ.get("UA_INFISICAL_ENABLED") == "0"


def test_agentmail_service_cannot_start_a_real_client(monkeypatch):
    import asyncio

    from universal_agent.services.agentmail_service import AgentMailService

    # Force the service-enable gate ON so this pins the API-key gate: even a
    # fully-enabled service must refuse to build a real client under pytest.
    monkeypatch.setenv("UA_AGENTMAIL_ENABLED", "1")
    service = AgentMailService()
    asyncio.run(service.startup())
    assert service._client is None
    assert service._last_error == "missing_api_key"
