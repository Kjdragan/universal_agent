"""Live connectivity tests against the Z.AI API.

Runs one call per model tier to confirm each model name is accepted and returns
a coherent response. Requires ZAI_API_KEY (or ANTHROPIC_AUTH_TOKEN) in env.

Mark: pytest -m llm
"""

import os

import pytest
from anthropic import Anthropic

from universal_agent.utils.model_resolution import ZAI_MODEL_MAP


def _client() -> Anthropic:
    api_key = (
        os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
    )
    if not api_key:
        pytest.skip("Missing ANTHROPIC_AUTH_TOKEN/ZAI_API_KEY for LLM test")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    return Anthropic(api_key=api_key, base_url=base_url)


def _ping(model: str) -> str:
    client = _client()
    response = client.messages.create(
        model=model,
        max_tokens=32,
        messages=[{"role": "user", "content": "Reply with the word 'Connectivity' and nothing else."}],
    )
    return response.content[0].text.strip()


@pytest.mark.llm
def test_zai_haiku_connectivity():
    """glm-4.5-air (haiku tier) — lightweight / fast model."""
    model = ZAI_MODEL_MAP["haiku"]
    text = _ping(model)
    assert "Connectivity" in text, f"{model!r} returned unexpected: {text!r}"


@pytest.mark.llm
def test_zai_sonnet_connectivity():
    """glm-5-turbo (sonnet tier) — standard model."""
    model = ZAI_MODEL_MAP["sonnet"]
    text = _ping(model)
    assert "Connectivity" in text, f"{model!r} returned unexpected: {text!r}"


@pytest.mark.llm
def test_zai_opus_connectivity():
    """glm-5-turbo (opus tier) — same model as sonnet, validates routing consistency."""
    model = ZAI_MODEL_MAP["opus"]
    text = _ping(model)
    assert "Connectivity" in text, f"{model!r} returned unexpected: {text!r}"
