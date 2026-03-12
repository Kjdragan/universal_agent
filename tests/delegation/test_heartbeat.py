"""Tests for the factory heartbeat sender."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent.delegation.heartbeat import FactoryHeartbeat, HeartbeatConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _config(**overrides) -> HeartbeatConfig:
    defaults = dict(
        hq_base_url="http://127.0.0.1:8002",
        ops_token="test-token",
        factory_id="test-desktop",
        factory_role="LOCAL_WORKER",
        deployment_profile="local_workstation",
        capabilities=["delegation_redis", "vp_coder"],
        interval_seconds=60.0,
        timeout_seconds=5.0,
    )
    defaults.update(overrides)
    return HeartbeatConfig(**defaults)


# ---------------------------------------------------------------------------
# HeartbeatConfig
# ---------------------------------------------------------------------------

class TestHeartbeatConfig:
    def test_from_env_with_all_vars(self, monkeypatch):
        monkeypatch.setenv("UA_HQ_BASE_URL", "http://10.0.0.1:8002")
        monkeypatch.setenv("UA_OPS_TOKEN", "my-token")
        monkeypatch.setenv("UA_FACTORY_ID", "my-factory")
        monkeypatch.setenv("FACTORY_ROLE", "LOCAL_WORKER")
        monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "local_workstation")
        monkeypatch.setenv("UA_DELEGATION_REDIS_ENABLED", "1")
        monkeypatch.setenv("UA_ENABLE_CODER_VP", "true")
        monkeypatch.setenv("UA_HEARTBEAT_INTERVAL_SECONDS", "30")

        cfg = HeartbeatConfig.from_env()
        assert cfg.hq_base_url == "http://10.0.0.1:8002"
        assert cfg.ops_token == "my-token"
        assert cfg.factory_id == "my-factory"
        assert cfg.interval_seconds == 30.0
        assert "delegation_redis" in cfg.capabilities
        assert "vp_coder" in cfg.capabilities

    def test_from_env_fallback_to_hostname(self, monkeypatch):
        monkeypatch.delenv("UA_HQ_BASE_URL", raising=False)
        monkeypatch.delenv("UA_BASE_URL", raising=False)
        monkeypatch.delenv("UA_GATEWAY_URL", raising=False)
        monkeypatch.delenv("UA_FACTORY_ID", raising=False)
        monkeypatch.delenv("INFISICAL_ENVIRONMENT", raising=False)

        cfg = HeartbeatConfig.from_env()
        assert cfg.hq_base_url == ""
        assert cfg.factory_id  # Falls back to hostname

    def test_interval_clamped_minimum(self, monkeypatch):
        monkeypatch.setenv("UA_HEARTBEAT_INTERVAL_SECONDS", "1")
        cfg = HeartbeatConfig.from_env()
        assert cfg.interval_seconds >= 10.0


# ---------------------------------------------------------------------------
# FactoryHeartbeat
# ---------------------------------------------------------------------------

class TestFactoryHeartbeat:
    def test_is_healthy_initially(self):
        hb = FactoryHeartbeat(_config())
        assert hb.is_healthy is True
        assert hb.consecutive_failures == 0

    def test_is_unhealthy_after_three_failures(self):
        hb = FactoryHeartbeat(_config())
        hb._consecutive_failures = 3
        assert hb.is_healthy is False

    def test_effective_interval_no_failures(self):
        hb = FactoryHeartbeat(_config(interval_seconds=60))
        assert hb._effective_interval() == 60.0

    def test_effective_interval_with_backoff(self):
        hb = FactoryHeartbeat(_config(interval_seconds=60))
        hb._consecutive_failures = 1
        assert hb._effective_interval() == 120.0
        hb._consecutive_failures = 2
        assert hb._effective_interval() == 240.0

    def test_effective_interval_capped_at_5min(self):
        hb = FactoryHeartbeat(_config(interval_seconds=60))
        hb._consecutive_failures = 10
        assert hb._effective_interval() == 300.0

    def test_build_payload(self):
        hb = FactoryHeartbeat(_config(factory_id="test-desktop"))
        payload = hb._build_payload(latency_ms=42.5)
        assert payload["factory_id"] == "test-desktop"
        assert payload["factory_role"] == "LOCAL_WORKER"
        assert payload["deployment_profile"] == "local_workstation"
        assert payload["registration_status"] == "online"
        assert payload["heartbeat_latency_ms"] == 42.5
        assert "hostname" in payload["metadata"]
        assert "pid" in payload["metadata"]
        assert "uptime_seconds" in payload["metadata"]

    @pytest.mark.asyncio
    async def test_send_success(self):
        hb = FactoryHeartbeat(_config())
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("universal_agent.delegation.heartbeat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await hb.send()

        assert result is True
        assert hb.consecutive_failures == 0
        assert hb.last_sent_at > 0

    @pytest.mark.asyncio
    async def test_send_failure_increments_counter(self):
        hb = FactoryHeartbeat(_config())
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "forbidden"

        with patch("universal_agent.delegation.heartbeat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await hb.send()

        assert result is False
        assert hb.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_send_exception_increments_counter(self):
        hb = FactoryHeartbeat(_config())

        with patch("universal_agent.delegation.heartbeat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.side_effect = ConnectionError("refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await hb.send()

        assert result is False
        assert hb.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_send_resets_failures_on_success(self):
        hb = FactoryHeartbeat(_config())
        hb._consecutive_failures = 5

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("universal_agent.delegation.heartbeat.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await hb.send()

        assert result is True
        assert hb.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_send_skips_when_no_hq_url(self):
        hb = FactoryHeartbeat(_config(hq_base_url=""))
        result = await hb.send()
        assert result is False
        assert hb.consecutive_failures == 0  # Not counted as failure
