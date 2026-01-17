#!/usr/bin/env python
"""Phase 1 runner: confirm receipt-based verification through a side-effect task."""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import Anthropic

from universal_agent.urw import URWConfig, URWOrchestrator, UniversalAgentAdapter
from universal_agent.urw.decomposer import Decomposer
from universal_agent.urw.state import Task, TaskStatus


class SingleReceiptTaskDecomposer(Decomposer):
    """Always returns one receipt-verified task to validate Phase 1 evidence."""

    def __init__(self, to_email: str, subject: str, body: str, connection_hint: str):
        self.to_email = to_email
        self.subject = subject
        self.body = body
        self.connection_hint = connection_hint

    def can_handle(self, _request: str) -> bool:
        return True

    def decompose(self, request: str, _context: Optional[Dict] = None) -> List[Task]:
        task_id = f"phase1_{uuid.uuid4().hex[:8]}"
        description = (
            f"{request}\n\n"
            "Deliverable:\n"
            f"- Send exactly one test email to {self.to_email}.\n"
            f"- Subject: {self.subject}\n"
            f"- Body: {self.body}\n"
            "- Use the Composio Gmail tool and record the message ID.\n"
            f"- Preferred connection: {self.connection_hint}.\n"
        )
        return [
            Task(
                id=task_id,
                title="Phase 1 Receipt Test",
                description=description,
                status=TaskStatus.PENDING,
                verification_type="binary",
                binary_checks=["side_effect:email_sent"],
                max_iterations=3,
            )
        ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="URW Phase 1 receipt verification")
    parser.add_argument(
        "--workspace",
        default="./urw_phase1_workspace",
        help="Workspace directory for URW state",
    )
    parser.add_argument(
        "--to",
        required=True,
        help="Recipient email address",
    )
    parser.add_argument(
        "--subject",
        default="URW Phase 1 Receipt Test",
        help="Email subject",
    )
    parser.add_argument(
        "--body",
        default="This is a Phase 1 receipt verification email from URW.",
        help="Email body",
    )
    parser.add_argument(
        "--connection",
        default="clearspring-cg / all-clearspring-cg",
        help="Composio connection hint",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-20250514"),
        help="Model for evaluation",
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
        raise SystemExit("ANTHROPIC_API_KEY or ZAI_API_KEY is required to run Phase 1")

    base_url = os.getenv("ANTHROPIC_BASE_URL")
    client_kwargs: Dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    llm_client = Anthropic(**client_kwargs)

    adapter = UniversalAgentAdapter({"model": args.model})
    decomposer = SingleReceiptTaskDecomposer(
        to_email=args.to,
        subject=args.subject,
        body=args.body,
        connection_hint=args.connection,
    )

    config = URWConfig(
        max_iterations_per_task=3,
        max_total_iterations=3,
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

    await orchestrator.run(
        "Send a single receipt-verifiable email via Composio Gmail."
    )


if __name__ == "__main__":
    asyncio.run(run())
