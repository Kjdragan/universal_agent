import json
from types import SimpleNamespace
from typing import Optional

import pytest

from universal_agent.urw import URWConfig, URWOrchestrator
from universal_agent.urw.decomposer import Decomposer
from universal_agent.urw.orchestrator import AgentExecutionResult, AgentLoopInterface
from universal_agent.urw.state import Task, TaskStatus


class DummyMessages:
    def create(self, **kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"score": 1.0, "reasoning": "ok"}')]
        )


class DummyLLM:
    def __init__(self):
        self.messages = DummyMessages()


class SingleTaskDecomposer(Decomposer):
    def decompose(self, request: str, context: Optional[dict] = None):
        return [
            Task(
                id="task_001",
                title="Send report",
                description="Generate report and send email.",
                status=TaskStatus.PENDING,
                verification_type="binary",
                binary_checks=["file_exists:report.md", "side_effect:email_sent"],
                max_iterations=1,
            )
        ]

    def can_handle(self, request: str) -> bool:  # pragma: no cover - required by interface
        return True


class StaticAdapter(AgentLoopInterface):
    async def execute_task(self, task, context, workspace_path):
        return AgentExecutionResult(
            success=True,
            output="Report generated and emailed.",
            artifacts_produced=[{"path": "report.md", "type": "file"}],
            side_effects=[
                {
                    "type": "email_sent",
                    "key": "gmail:123",
                    "details": {"message_id": "123"},
                }
            ],
            tools_invoked=["mock_tool"],
        )

    async def cancel(self):
        return None


@pytest.mark.asyncio
async def test_urw_verification_finding_written(tmp_path):
    llm_client = DummyLLM()
    orchestrator = URWOrchestrator(
        agent_loop=StaticAdapter(),
        llm_client=llm_client,
        workspace_path=tmp_path,
        config=URWConfig(max_iterations_per_task=1, max_total_iterations=3, verbose=False),
        decomposer=SingleTaskDecomposer(),
    )

    result = await orchestrator.run("Send a report")

    assert result["status"] == "complete"

    verification_dir = tmp_path / ".urw" / "verification"
    files = list(verification_dir.glob("verify_*.json"))
    assert files, "verification findings artifact not written"

    payload = json.loads(files[0].read_text())
    assert payload["evidence_type"] == "hybrid"
    assert "report.md" in payload["evidence_refs"]
    assert "gmail:123" in payload["evidence_refs"]
