from __future__ import annotations

import pytest

from universal_agent import gateway_server


@pytest.mark.asyncio
async def test_resolve_telegram_service_status_prefers_system(monkeypatch):
    async def _fake_systemd_unit_active(unit_name: str, *, user_scope: bool = False, timeout_seconds: float = 5.0) -> bool:
        assert unit_name == "universal-agent-telegram"
        return not user_scope

    monkeypatch.setattr(gateway_server, "_systemd_unit_active", _fake_systemd_unit_active)
    status = await gateway_server._resolve_telegram_service_status()
    assert status["active"] is True
    assert status["scope"] == "system"
    assert status["unit"] == "universal-agent-telegram"


@pytest.mark.asyncio
async def test_resolve_telegram_service_status_falls_back_to_user(monkeypatch):
    async def _fake_systemd_unit_active(unit_name: str, *, user_scope: bool = False, timeout_seconds: float = 5.0) -> bool:
        assert unit_name == "universal-agent-telegram"
        return user_scope

    monkeypatch.setattr(gateway_server, "_systemd_unit_active", _fake_systemd_unit_active)
    status = await gateway_server._resolve_telegram_service_status()
    assert status["active"] is True
    assert status["scope"] == "user"


@pytest.mark.asyncio
async def test_resolve_telegram_service_status_none_when_inactive(monkeypatch):
    async def _fake_systemd_unit_active(unit_name: str, *, user_scope: bool = False, timeout_seconds: float = 5.0) -> bool:
        assert unit_name == "universal-agent-telegram"
        return False

    monkeypatch.setattr(gateway_server, "_systemd_unit_active", _fake_systemd_unit_active)
    status = await gateway_server._resolve_telegram_service_status()
    assert status["active"] is False
    assert status["scope"] == "none"
