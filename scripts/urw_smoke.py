#!/usr/bin/env python
"""URW smoke script using mock adapter for quick validation."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from anthropic import Anthropic

from universal_agent.urw import URWConfig, URWOrchestrator, MockAgentAdapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="URW mock smoke test")
    parser.add_argument(
        "--workspace",
        default="./urw_smoke_workspace",
        help="Workspace directory for URW state",
    )
    parser.add_argument(
        "--request",
        default="Write a short report and confirm completion",
        help="Request to execute",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Use live LLM (Anthropic-compatible) for decomposition/evaluation.",
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-5"),
        help="Model name for decomposition/evaluation.",
    )
    return parser.parse_args()


class _DummyMessages:
    def create(self, **_kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"score": 1.0, "reasoning": "ok"}')]
        )


class DummyLLM:
    def __init__(self):
        self.messages = _DummyMessages()


async def run() -> None:
    args = parse_args()

    workspace_path = Path(args.workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    adapter = MockAgentAdapter({"success_rate": 1.0, "produce_artifacts": True})
    llm_client = DummyLLM()
    if args.use_llm:
        api_key = (
            os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ZAI_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
        )
        if not api_key:
            raise SystemExit("Missing ANTHROPIC_AUTH_TOKEN/ZAI_API_KEY for LLM mode")
        base_url = os.getenv("ANTHROPIC_BASE_URL")
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        llm_client = Anthropic(**client_kwargs)
    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=llm_client,
        workspace_path=workspace_path,
        config=URWConfig(
            verbose=True,
            llm_model=args.llm_model,
            max_total_iterations=15,
            max_iterations_per_task=5,
        ),
    )

    result = await orchestrator.run(args.request)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
