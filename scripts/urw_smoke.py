#!/usr/bin/env python
"""URW smoke script using mock adapter for quick validation."""

import argparse
import asyncio
import json
from pathlib import Path

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
    return parser.parse_args()


async def run() -> None:
    args = parse_args()

    workspace_path = Path(args.workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    adapter = MockAgentAdapter({"success_rate": 1.0, "produce_artifacts": True})
    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=None,
        workspace_path=workspace_path,
        config=URWConfig(verbose=True),
    )

    result = await orchestrator.run(args.request)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
