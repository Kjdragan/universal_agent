import os

import pytest
from anthropic import Anthropic


@pytest.mark.llm
def test_zai_anthropic_compat_connectivity():
    api_key = (
        os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
    )
    if not api_key:
        pytest.skip("Missing ANTHROPIC_AUTH_TOKEN/ZAI_API_KEY for LLM test")

    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    model = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-4.7")

    client = Anthropic(api_key=api_key, base_url=base_url)
    response = client.messages.create(
        model=model,
        max_tokens=32,
        messages=[
            {"role": "user", "content": "Reply with the word 'Connectivity' and nothing else."}
        ],
    )

    text = response.content[0].text.strip()
    assert "Connectivity" in text
