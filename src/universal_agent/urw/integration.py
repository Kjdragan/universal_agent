"""
URW integration adapters for the Universal Agent system.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .orchestrator import AgentExecutionResult, AgentLoopInterface
from .state import Task


class BaseAgentAdapter(AgentLoopInterface):
    """Base class for agent loop adapters."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._current_agent: Any = None
        self._cancelled = False
        self._workspace_path: Optional[Path] = None

    async def _create_agent(self) -> Any:
        raise NotImplementedError

    async def _run_agent(
        self, agent: Any, prompt: str, workspace_path: Path
    ) -> AgentExecutionResult:
        raise NotImplementedError

    async def execute_task(
        self, task: Task, context: str, workspace_path: Path
    ) -> AgentExecutionResult:
        self._cancelled = False
        self._workspace_path = workspace_path

        prompt = self._build_prompt(task, context)
        agent = await self._create_agent()
        self._current_agent = agent

        try:
            start_time = time.time()
            result = await self._run_agent(agent, prompt, workspace_path)
            result.execution_time_seconds = time.time() - start_time
            return result
        finally:
            self._current_agent = None

    async def cancel(self):
        self._cancelled = True

    def _build_prompt(self, task: Task, context: str) -> str:
        return f"""# Universal Ralph Wrapper Task

{context}

---

## Instructions

You are executing a task as part of a larger plan. Your context window is fresh -
all relevant history is provided above in the context section.

**Your Task:** {task.title}

{task.description}

**Success Criteria:**
{self._format_criteria(task)}

## Guidelines

1. Work towards completing the task fully
2. Save all outputs to the workspace artifacts directory
3. If you encounter blockers, document them clearly
4. Extract learnings that might help future iterations
5. If an approach fails, note it so it won't be repeated

## Output Format

When complete, provide a summary of:
- What was accomplished
- Files created (if any)
- Any actions taken (emails sent, APIs called, etc.)
- Key learnings
- Any approaches that failed
"""

    def _format_criteria(self, task: Task) -> str:
        criteria: List[str] = []
        if task.binary_checks:
            for check in task.binary_checks:
                criteria.append(f"- {check}")
        if task.constraints:
            for constraint in task.constraints:
                criteria.append(f"- {constraint['type']}: {constraint.get('value', '')}")
        if task.evaluation_rubric:
            criteria.append(f"- Qualitative: {task.evaluation_rubric}")
        return "\n".join(criteria) if criteria else "- Complete the task as described"


class UniversalAgentAdapter(BaseAgentAdapter):
    """Adapter for the existing Universal Agent system."""

    async def _create_agent(self) -> Any:
        from universal_agent.agent_core import UniversalAgent

        workspace_dir = str(self._workspace_path) if self._workspace_path else None
        return UniversalAgent(workspace_dir=workspace_dir)

    async def _run_agent(
        self, agent: Any, prompt: str, workspace_path: Path
    ) -> AgentExecutionResult:
        from universal_agent.agent_core import EventType

        output_chunks: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        tool_call_by_id: Dict[str, Dict[str, Any]] = {}
        tool_results: List[Dict[str, Any]] = []
        artifacts: List[Dict[str, Any]] = []
        side_effects: List[Dict[str, Any]] = []
        side_effect_keys: set[str] = set()
        tools_invoked: List[str] = []
        auth_required = False

        async for event in agent.run_query(prompt):
            if event.type == EventType.TEXT:
                text = event.data.get("text")
                if text:
                    output_chunks.append(text)
            elif event.type == EventType.TOOL_CALL:
                tool_calls.append(event.data)
                tool_id = event.data.get("id")
                if tool_id:
                    tool_call_by_id[str(tool_id)] = event.data
            elif event.type == EventType.TOOL_RESULT:
                tool_results.append(event.data)
                self._capture_side_effects(
                    event.data,
                    tool_call_by_id,
                    side_effects,
                    side_effect_keys,
                )
            elif event.type == EventType.WORK_PRODUCT:
                path = event.data.get("path") or event.data.get("filename")
                if path:
                    artifacts.append({"path": path, "type": "file", "metadata": event.data})
            elif event.type == EventType.AUTH_REQUIRED:
                auth_required = True

        # capture file artifacts from tool calls
        artifacts.extend(self._capture_file_writes(tool_calls))

        tools_invoked = [tc.get("name") for tc in tool_calls if tc.get("name")]

        output = "\n".join(output_chunks).strip()
        if not output:
            output = "(No text response returned by agent)"

        if auth_required:
            return AgentExecutionResult(
                success=False,
                output=output,
                error="Authentication required",
                artifacts_produced=artifacts,
                side_effects=side_effects,
                tools_invoked=tools_invoked,
            )

        return AgentExecutionResult(
            success=True,
            output=output,
            artifacts_produced=artifacts,
            side_effects=side_effects,
            tools_invoked=tools_invoked,
            context_tokens_used=self._estimate_tokens(prompt, output),
        )

    def _capture_file_writes(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for call in tool_calls:
            name = (call.get("name") or "").lower()
            tool_input = call.get("input") or {}
            if not isinstance(tool_input, dict):
                continue
            if "write" in name or "append_to_file" in name:
                file_path = tool_input.get("file_path") or tool_input.get("path")
                if file_path:
                    artifacts.append({"path": file_path, "type": "file", "metadata": {"tool": name}})
        return artifacts

    def _capture_side_effects(
        self,
        tool_result: Dict[str, Any],
        tool_call_by_id: Dict[str, Dict[str, Any]],
        side_effects: List[Dict[str, Any]],
        side_effect_keys: set,
    ) -> None:
        tool_use_id = tool_result.get("tool_use_id")
        tool_call = tool_call_by_id.get(str(tool_use_id), {}) if tool_use_id else {}
        tool_name = tool_call.get("name", "")
        content_preview = tool_result.get("content_preview") or ""

        if not tool_name:
            return

        effect_type = None
        if "GMAIL_SEND_EMAIL" in tool_name:
            effect_type = "email_sent"
        elif "SLACK_SEND_MESSAGE" in tool_name:
            effect_type = "slack_message_sent"

        if not effect_type:
            return

        message_id = self._extract_message_id(content_preview)
        idempotency_key = message_id or f"{tool_name}:{tool_use_id}"

        if idempotency_key in side_effect_keys:
            return

        side_effect_keys.add(idempotency_key)
        side_effects.append(
            {
                "type": effect_type,
                "key": idempotency_key,
                "details": {"tool": tool_name, "message_id": message_id},
            }
        )

    def _extract_message_id(self, content_preview: str) -> Optional[str]:
        if not content_preview:
            return None

        cleaned = content_preview
        json_start = cleaned.find("{")
        if json_start != -1:
            cleaned = cleaned[json_start:]

        try:
            payload = json.loads(cleaned)
            for key in ["message_id", "id", "messageId"]:
                value = payload.get(key) if isinstance(payload, dict) else None
                if value:
                    return str(value)
            if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
                for key in ["message_id", "id", "messageId"]:
                    value = payload["data"].get(key)
                    if value:
                        return str(value)
        except Exception:
            pass

        match = re.search(r"message_id\s*[:=]\s*['\"]?([\w-]+)", content_preview)
        if match:
            return match.group(1)

        return None

    def _estimate_tokens(self, prompt: str, output: str) -> int:
        return int((len(prompt) + len(output)) / 4)


class MockAgentAdapter(BaseAgentAdapter):
    """Mock adapter for testing URW without a real agent system."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.execution_count = 0

    async def _create_agent(self) -> Any:
        return {"mock": True, "execution": self.execution_count}

    async def _run_agent(self, agent: Any, prompt: str, workspace_path: Path) -> AgentExecutionResult:
        self.execution_count += 1
        await asyncio.sleep(self.config.get("simulate_delay", 0.5))
        success_rate = self.config.get("success_rate", 0.8)
        success = (self.execution_count % 100) / 100 <= success_rate

        artifacts = []
        if self.config.get("produce_artifacts", True) and success:
            artifact_path = f"mock_output_{self.execution_count}.md"
            full_path = workspace_path / ".urw" / "artifacts" / artifact_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(f"# Mock Output\n\nExecution {self.execution_count}\n\n{prompt[:500]}")
            artifacts.append({"path": artifact_path, "type": "file", "metadata": {"mock": True}})

        learnings = [f"Mock learning from execution {self.execution_count}"]

        failed_approaches = []
        if not success:
            failed_approaches.append(
                {"approach": f"Mock approach {self.execution_count}", "why_failed": "Simulated failure"}
            )

        return AgentExecutionResult(
            success=success,
            output=f"Mock execution {self.execution_count} completed. Success: {success}",
            error=None if success else "Simulated failure",
            artifacts_produced=artifacts,
            side_effects=[],
            learnings=learnings,
            failed_approaches=failed_approaches,
            tools_invoked=["mock_tool_1", "mock_tool_2"],
            context_tokens_used=len(prompt.split()) * 2,
        )


def create_adapter_for_system(system_type: str, config: Dict[str, Any]) -> BaseAgentAdapter:
    adapters = {
        "universal_agent": UniversalAgentAdapter,
        "mock": MockAgentAdapter,
    }
    adapter_class = adapters.get(system_type)
    if not adapter_class:
        raise ValueError(f"Unknown system type: {system_type}. Options: {list(adapters.keys())}")
    return adapter_class(config)
