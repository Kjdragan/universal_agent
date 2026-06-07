"""`_call_llm` must close its AsyncAnthropic client deterministically.

Regression guard for the convergence "Event loop is closed" leak: the per-call
client was never closed, so its httpx AsyncClient was GC-finalized after the
cron's event loop closed (~12 RuntimeError('Event loop is closed') per ideation
run). The fix closes the client in a try/finally; these tests assert that, on
both the success and error paths.
"""

from __future__ import annotations

import asyncio

import pytest

from universal_agent.services import llm_classifier


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self) -> None:
        self.content = [_FakeBlock("hello world")]


class _FakeMessages:
    def __init__(self, *, raises: bool = False) -> None:
        self._raises = raises

    async def create(self, **_kwargs):
        if self._raises:
            raise RuntimeError("upstream 500")
        return _FakeResponse()


class _FakeClient:
    def __init__(self, *, raises: bool = False) -> None:
        self.messages = _FakeMessages(raises=raises)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_call_llm_closes_client_on_success(monkeypatch):
    fake = _FakeClient()

    async def _fake_get():
        return fake

    monkeypatch.setattr(llm_classifier, "_get_anthropic_client", _fake_get)
    out = asyncio.run(llm_classifier._call_llm(system="s", user="u", model="test-model"))
    assert out == "hello world"
    assert fake.closed is True, "client must be closed after a successful call"


def test_call_llm_closes_client_on_error(monkeypatch):
    fake = _FakeClient(raises=True)

    async def _fake_get():
        return fake

    monkeypatch.setattr(llm_classifier, "_get_anthropic_client", _fake_get)
    with pytest.raises(RuntimeError, match="upstream 500"):
        asyncio.run(llm_classifier._call_llm(system="s", user="u", model="test-model"))
    assert fake.closed is True, "client must be closed even when the call raises"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
