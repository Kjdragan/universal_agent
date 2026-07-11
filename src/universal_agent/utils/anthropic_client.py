"""Shared Anthropic-compatible client construction + sync call helpers.

Consolidates the client block that was copy-pasted across
``csi_url_judge._call_llm_structured``, ``csi_intelligence_pass.
_call_llm_structured``, ``claude_code_intel._call_sync_llm`` and
``wiki/llm._get_anthropic_client``: the api-key fallback order
(``ANTHROPIC_API_KEY`` → ``ANTHROPIC_AUTH_TOKEN`` → ``ZAI_API_KEY``), the
``ANTHROPIC_BASE_URL`` passthrough, the GLM-5.2 thinking-disable, and the
forced-tool_use retry loop. Token budgets stay per-call-site parameters —
they differ deliberately (1000 / 4096 / 600 / 2048).

Two call shapes, kept separate on purpose:

- ``call_llm_structured`` — forced ``tool_choice`` structured output with a
  bounded retry loop (the two CSI sites).
- ``call_llm_text`` — plain-text SINGLE-SHOT, no retry, no thinking param
  (claude_code_intel's classify path and wiki/llm) — their callers own the
  fallback-to-heuristic behavior, so absorbing a retry loop here would
  change effective retry counts.

Sync only. The many ``AsyncAnthropic`` construction sites around the repo
are a different lineage (per-module constants, async) and out of scope.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from universal_agent.utils.model_resolution import resolve_opus

logger = logging.getLogger(__name__)

_API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZAI_API_KEY")


def resolve_llm_api_key() -> Optional[str]:
    """First non-empty key in the standard precedence order, else None."""
    for var in _API_KEY_VARS:
        value = str(os.getenv(var) or "").strip()
        if value:
            return value
    return None


def has_llm_key() -> bool:
    return resolve_llm_api_key() is not None


def build_anthropic_client() -> Any:
    """Sync ``Anthropic`` client with standard key precedence + base-url.

    Raises ``RuntimeError`` when no key is available (callers that need a
    different exception type — e.g. wiki/llm's ``SemanticExtractionError`` —
    translate at their boundary).
    """
    from anthropic import Anthropic

    api_key = resolve_llm_api_key()
    if not api_key:
        raise RuntimeError(
            "No Anthropic-compatible API key available "
            "(checked ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ZAI_API_KEY)"
        )
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = str(os.getenv("ANTHROPIC_BASE_URL") or "").strip()
    if base_url:
        client_kwargs["base_url"] = base_url
    return Anthropic(**client_kwargs)


def call_llm_structured(
    *,
    system: str,
    user: str,
    tool: dict[str, Any],
    max_tokens: int,
    max_retries: int = 2,
    label: str = "LLM structured call",
) -> dict[str, Any]:
    """Forced-tool_use structured call with a bounded retry loop.

    Returns a copy of the tool_use block's input dict. Retries on both
    SDK/network exceptions and no-tool_use responses; after exhaustion,
    re-raises the last real exception if one occurred (preserving its type
    for callers that branch on it), else raises ``RuntimeError``.
    """
    client = build_anthropic_client()

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=resolve_opus(),  # → glm-5.2 (opus tier) via ZAI map
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                # glm-5.2 defaults thinking ON (10-24x tokens); these are cheap
                # structured-output passes with forced tool_choice, so keep it
                # disabled.
                thinking={"type": "disabled"},
            )
            for block in response.content:
                if getattr(block, "type", "") == "tool_use":
                    return dict(block.input)  # type: ignore[arg-type]
            logger.warning(
                "%s attempt %d: no tool_use block in response", label, attempt + 1
            )
        except Exception as exc:
            last_exc = exc
            logger.warning("%s attempt %d failed: %s", label, attempt + 1, exc)
            if attempt == max_retries - 1:
                raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{label} returned no tool_use block after {max_retries} attempts")


def call_llm_text(
    *,
    system: str,
    user: str,
    max_tokens: int,
    model: Optional[str] = None,
) -> str:
    """Plain-text SINGLE-SHOT call — no retry, no tool_use, no thinking param.

    Callers own their fallback behavior (heuristic classification etc.), so
    any SDK exception propagates untouched.
    """
    client = build_anthropic_client()
    response = client.messages.create(
        model=model or resolve_opus(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    chunks: list[str] = []
    for block in response.content:
        if hasattr(block, "text"):
            chunks.append(block.text)
    return "".join(chunks).strip()


__all__ = [
    "build_anthropic_client",
    "call_llm_structured",
    "call_llm_text",
    "has_llm_key",
    "resolve_llm_api_key",
]
