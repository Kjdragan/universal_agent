#!/usr/bin/env python
"""Run the URW orchestrator with the Universal Agent adapter."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic

from universal_agent.urw import (
    URWConfig,
    URWOrchestrator,
    UniversalAgentAdapter,
    MockAgentAdapter,
)

class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data: str) -> None:
        for stream in self.streams:
            stream.write(data)
        self.flush()

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()

    def isatty(self) -> bool:
        return any(getattr(stream, "isatty", lambda: False)() for stream in self.streams)


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
    parser.add_argument(
        "--require-binary",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Require binary checks to pass for completion (override).",
    )
    parser.add_argument(
        "--require-constraints",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Require constraints to pass for completion (override).",
    )
    parser.add_argument(
        "--require-qualitative",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Require qualitative rubric to pass for completion (override).",
    )
    parser.add_argument(
        "--qualitative-min-score",
        type=float,
        default=None,
        help="Minimum qualitative score required (override).",
    )
    parser.add_argument(
        "--overall-min-score",
        type=float,
        default=None,
        help="Minimum overall score required (override).",
    )
    parser.add_argument(
        "--log-file",
        default=os.getenv("URW_LOG_FILE"),
        help="Append stdout/stderr to a log file (optional).",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()

    workspace_path = Path(args.workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    log_handle = None
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    try:
        if args.log_file:
            log_path = Path(args.log_file)
            if not log_path.is_absolute():
                log_path = (workspace_path / log_path).resolve()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = log_path.open("a", encoding="utf-8")
            sys.stdout = Tee(original_stdout, log_handle)
            sys.stderr = Tee(original_stderr, log_handle)
            print(f"[URW] Logging to {log_path}")

        api_key = (
            os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or os.getenv("ZAI_API_KEY")
        )
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY is required to run URW")

        base_url = os.getenv("ANTHROPIC_BASE_URL")
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        llm_client = Anthropic(**client_kwargs)

        adapter: object
        if args.mock:
            adapter = MockAgentAdapter({"success_rate": 0.9, "produce_artifacts": True})
        else:
            adapter = UniversalAgentAdapter({"model": args.model})

        evaluation_policy = {}
        if args.require_binary is not None:
            evaluation_policy["require_binary"] = args.require_binary
        if args.require_constraints is not None:
            evaluation_policy["require_constraints"] = args.require_constraints
        if args.require_qualitative is not None:
            evaluation_policy["require_qualitative"] = args.require_qualitative
        if args.qualitative_min_score is not None:
            evaluation_policy["qualitative_min_score"] = args.qualitative_min_score
        if args.overall_min_score is not None:
            evaluation_policy["overall_min_score"] = args.overall_min_score

        config = URWConfig(
            max_iterations_per_task=args.max_iterations_per_task,
            max_total_iterations=args.max_total_iterations,
            iteration_timeout=args.iteration_timeout,
            task_timeout=args.task_timeout,
            verbose=args.verbose,
            llm_model=args.model,
            evaluation_policy=evaluation_policy,
        )

        orchestrator = URWOrchestrator(
            agent_loop=adapter,
            llm_client=llm_client,
            workspace_path=workspace_path,
            config=config,
        )

        result = await orchestrator.run(args.request)
        print(json.dumps(result, indent=2))
    finally:
        if log_handle:
            log_handle.flush()
            log_handle.close()
        sys.stdout = original_stdout
        sys.stderr = original_stderr


if __name__ == "__main__":
    asyncio.run(run())
