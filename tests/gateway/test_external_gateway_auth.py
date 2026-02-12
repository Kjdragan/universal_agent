from __future__ import annotations

import types

import universal_agent.gateway as gateway_module


def _install_external_gateway_stubs(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "EXTERNAL_DEPS_AVAILABLE", True)

    class _DummyAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def aclose(self) -> None:
            return None

    def _connect(url: str, additional_headers=None):  # pragma: no cover - signature probe only
        raise RuntimeError("stub")

    monkeypatch.setattr(
        gateway_module,
        "httpx",
        types.SimpleNamespace(AsyncClient=_DummyAsyncClient),
    )
    monkeypatch.setattr(
        gateway_module,
        "websockets",
        types.SimpleNamespace(connect=_connect),
    )


def test_external_gateway_uses_internal_api_token(monkeypatch):
    _install_external_gateway_stubs(monkeypatch)
    monkeypatch.setenv("UA_INTERNAL_API_TOKEN", "internal-token")
    monkeypatch.delenv("UA_OPS_TOKEN", raising=False)

    gateway = gateway_module.ExternalGateway(base_url="http://127.0.0.1:8002")

    assert gateway._auth_headers["authorization"] == "Bearer internal-token"
    assert gateway._auth_headers["x-ua-internal-token"] == "internal-token"
    assert gateway._auth_headers["x-ua-ops-token"] == "internal-token"
    assert gateway._ws_headers_param == "additional_headers"


def test_external_gateway_falls_back_to_ops_token(monkeypatch):
    _install_external_gateway_stubs(monkeypatch)
    monkeypatch.delenv("UA_INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("UA_OPS_TOKEN", "ops-token")

    gateway = gateway_module.ExternalGateway(base_url="http://127.0.0.1:8002")

    assert gateway._auth_headers["authorization"] == "Bearer ops-token"
    assert gateway._auth_headers["x-ua-internal-token"] == "ops-token"
