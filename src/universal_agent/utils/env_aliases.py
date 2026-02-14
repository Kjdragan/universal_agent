from __future__ import annotations

import os


def apply_xai_key_aliases() -> None:
    """
    Normalize X/Grok API key env var names across our ecosystem.

    Why:
    - Some scripts/tools expect `XAI_API_KEY`.
    - Our project standard is `GROK_API_KEY` (preferred), but both point at the same xAI key.

    Policy:
    - If GROK_API_KEY is set and XAI_API_KEY is not, set XAI_API_KEY.
    - If XAI_API_KEY is set and GROK_API_KEY is not, set GROK_API_KEY.

    This is safe because it does not change values when both are already set.
    """
    grok = (os.environ.get("GROK_API_KEY") or "").strip()
    xai = (os.environ.get("XAI_API_KEY") or "").strip()

    if grok and not xai:
        os.environ["XAI_API_KEY"] = grok
    elif xai and not grok:
        os.environ["GROK_API_KEY"] = xai

