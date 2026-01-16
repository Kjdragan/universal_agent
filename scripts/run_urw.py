#!/usr/bin/env python
"""Run the URW orchestrator with the Universal Agent adapter."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from anthropic import Anthropic

from universal_agent.urw import (
    URWConfig,
    URWOrchestrator,
    UniversalAgentAdapter,
    MockAgentAdapter,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run URW orchestrator")
    parser.add_argument("request", help="User request to execute")
    parser.add_argument(
        "--workspace",
        default="./urw_workspace",
        help="Workspace directory for URW state",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock adapter instead of Universal Agent",
    )
    parser.add_argument(
        "--max-iterations-per-task",
        type=int,
        default=15,
        help="Max iterations per task",
    )
    parser.add_argument(
        "--max-total-iterations",
        type=int,
        default=200,
        help="Max total iterations",
    )
    parser.add_argument(
        "--iteration-timeout",
        type=int,
        default=600,
        help="Iteration timeout in seconds",
    )
    parser.add_argument(
        "--task-timeout",
        type=int,
        default=3600,
        help="Task timeout in seconds",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model for decomposition/evaluation",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose URW logging",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()

    workspace_path = Path(args.workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is required to run URW")

    llm_client = Anthropic(api_key=api_key)

    adapter: object
    if args.mock:
        adapter = MockAgentAdapter({"success_rate": 0.9, "produce_artifacts": True})
    else:
        adapter = UniversalAgentAdapter({"model": args.model})

    config = URWConfig(
        max_iterations_per_task=args.max_iterations_per_task,
        max_total_iterations=args.max_total_iterations,
        iteration_timeout=args.iteration_timeout,
        task_timeout=args.task_timeout,
        verbose=args.verbose,
    )

    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=llm_client,
        workspace_path=workspace_path,
        config=config,
    )

    result = await orchestrator.run(args.request)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
