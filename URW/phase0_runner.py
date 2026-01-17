#!/usr/bin/env python
"""Phase 0 runner: confirm adapter can execute a single phase via the multi-agent system."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import Anthropic

from universal_agent.urw import (
    URWConfig,
    URWOrchestrator,
    UniversalAgentAdapter,
)
from universal_agent.urw.decomposer import Decomposer
from universal_agent.urw.state import Task, TaskStatus


class SingleTaskDecomposer(Decomposer):
    """Always returns a single task to validate the adapter baseline."""

    def __init__(self, output_filename: str):
        self.output_filename = output_filename

    def can_handle(self, _request: str) -> bool:
        return True

    def decompose(self, request: str, _context: Optional[Dict] = None) -> List[Task]:
        task_id = f"phase0_{uuid.uuid4().hex[:8]}"
        description = (
            f"{request}\n\n"
            "Deliverable:\n"
            f"- Save the output to {self.output_filename}\n"
            "- Use local file write tools only (no external APIs).\n"
        )
        return [
            Task(
                id=task_id,
                title="Phase 0 Adapter Baseline",
                description=description,
                status=TaskStatus.PENDING,
                verification_type="binary",
                binary_checks=[f"file_exists:{self.output_filename}"],
                max_iterations=5,
            )
        ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="URW Phase 0 adapter baseline")
    parser.add_argument(
        "--workspace",
        default="./urw_phase0_workspace",
        help="Workspace directory for URW state",
    )
    parser.add_argument(
        "--request",
        default=(
            "Write a short harness status summary and save it to phase0_report.md. "
            "Do not call external APIs or send messages."
        ),
        help="Request to execute",
    )
    parser.add_argument(
        "--output-file",
        default="phase0_report.md",
        help="Expected output filename for binary verification",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-20250514"),
        help="Model for decomposition/evaluation",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()

    workspace_path = Path(args.workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY or ZAI_API_KEY is required to run Phase 0")

    base_url = os.getenv("ANTHROPIC_BASE_URL")
    client_kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    llm_client = Anthropic(**client_kwargs)

    adapter = UniversalAgentAdapter({"model": args.model})
    decomposer = SingleTaskDecomposer(args.output_file)

    config = URWConfig(
        max_iterations_per_task=5,
        max_total_iterations=5,
        verbose=True,
        llm_model=args.model,
    )

    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=llm_client,
        workspace_path=workspace_path,
        config=config,
        decomposer=decomposer,
    )

    result = await orchestrator.run(args.request)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
