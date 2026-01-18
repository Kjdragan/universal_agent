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
            if self.config.get("verbose"):
                print(f"[DEBUG EVENT] {event.type} Data: {str(event.data)[:200]}", flush=True)

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

        if tool_result.get("is_error"):
            return

        if not tool_name:
            return

        effects = []
        if "COMPOSIO_MULTI_EXECUTE_TOOL" in tool_name:
            effects = self._extract_multi_execute_effects(
                tool_call.get("input") or {}, content_preview, tool_name
            )
        else:
            effect_type = self._match_effect_type(tool_name)
            if effect_type:
                effects = [
                    {
                        "effect_type": effect_type,
                        "tool_label": tool_name,
                        "payload": content_preview,
                    }
                ]

        for effect in effects:
            effect_type = effect.get("effect_type")
            tool_label = effect.get("tool_label") or tool_name
            payload = effect.get("payload") or content_preview
            if not effect_type:
                continue

            message_id = self._extract_message_id(payload)
            idempotency_key = message_id or f"{tool_label}:{tool_use_id}"

            if idempotency_key in side_effect_keys:
                continue

            side_effect_keys.add(idempotency_key)
            side_effects.append(
                {
                    "type": effect_type,
                    "key": idempotency_key,
                    "details": {"tool": tool_label, "message_id": message_id},
                }
            )

    def _match_effect_type(self, tool_name: str) -> Optional[str]:
        if not tool_name:
            return None
        normalized = tool_name.upper()
        if "GMAIL_SEND_EMAIL" in normalized:
            return "email_sent"
        if "SLACK_SEND_MESSAGE" in normalized:
            return "slack_message_sent"
        return None

    def _extract_multi_execute_effects(
        self,
        tool_input: Dict[str, Any],
        content_preview: str,
        tool_name: str,
    ) -> List[Dict[str, Any]]:
        tool_entries = tool_input.get("tools") if isinstance(tool_input, dict) else []
        slugs = [
            entry.get("tool_slug")
            for entry in tool_entries
            if isinstance(entry, dict) and entry.get("tool_slug")
        ]

        parsed = self._try_parse_json(content_preview)
        entries: List[Any] = []
        if isinstance(parsed, list):
            entries = parsed
        elif isinstance(parsed, dict):
            for key in ("data", "results", "response", "output"):
                value = parsed.get(key)
                if isinstance(value, list):
                    entries = value
                    break

        effects: List[Dict[str, Any]] = []
        if entries:
            for idx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                slug = (
                    entry.get("tool_slug")
                    or entry.get("tool")
                    or entry.get("name")
                    or entry.get("tool_name")
                )
                if not slug and idx < len(slugs):
                    slug = slugs[idx]
                effect_type = self._match_effect_type(slug or "")
                if not effect_type:
                    continue
                status = str(entry.get("status") or entry.get("state") or "").lower()
                if status in {"error", "failed", "failure"}:
                    continue
                if entry.get("error") or entry.get("errors"):
                    continue
                data = (
                    entry.get("data")
                    or entry.get("response")
                    or entry.get("result")
                    or entry.get("output")
                    or entry.get("content")
                )
                payload = (
                    json.dumps(data)
                    if isinstance(data, (dict, list))
                    else str(data) if data is not None else content_preview
                )
                effects.append(
                    {
                        "effect_type": effect_type,
                        "tool_label": slug or tool_name,
                        "payload": payload,
                    }
                )
        elif slugs:
            for slug in slugs:
                effect_type = self._match_effect_type(slug)
                if effect_type:
                    effects.append(
                        {
                            "effect_type": effect_type,
                            "tool_label": slug,
                            "payload": content_preview,
                        }
                    )

        return effects

    def _try_parse_json(self, content_preview: str) -> Optional[Any]:
        if not content_preview:
            return None
        cleaned = content_preview
        first_brace = cleaned.find("{")
        first_bracket = cleaned.find("[")
        if first_brace == -1 and first_bracket == -1:
            return None
        if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
            cleaned = cleaned[first_bracket:]
        elif first_brace != -1:
            cleaned = cleaned[first_brace:]
        try:
            return json.loads(cleaned)
        except Exception:
            return None

    def _extract_message_id(self, content_preview: str) -> Optional[str]:
        if not content_preview:
            return None

        cleaned = content_preview
        json_start = cleaned.find("{")
        if json_start != -1:
            cleaned = cleaned[json_start:]

        try:
            payload = json.loads(cleaned)
            for key in ["message_id", "id", "messageId", "message_ts", "ts"]:
                value = payload.get(key) if isinstance(payload, dict) else None
                if value:
                    return str(value)
            if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
                for key in ["message_id", "id", "messageId", "message_ts", "ts"]:
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

        task_title = self._extract_task_title(prompt)
        original_request = self._extract_original_request(prompt)
        file_targets = self._extract_file_targets(prompt)
        min_length = self._extract_min_length(prompt)
        content = self._generate_content(task_title, original_request)
        content = self._ensure_min_length(content, min_length)

        artifacts = []
        if self.config.get("produce_artifacts", True) and success:
            targets = file_targets or [f"mock_output_{self.execution_count}.md"]
            for target in targets:
                full_path = workspace_path / ".urw" / "artifacts" / target
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content)
                artifacts.append({"path": target, "type": "file", "metadata": {"mock": True}})

        learnings = [f"Mock learning from execution {self.execution_count}"]

        failed_approaches = []
        if not success:
            failed_approaches.append(
                {"approach": f"Mock approach {self.execution_count}", "why_failed": "Simulated failure"}
            )

        output = (
            content
            if success
            else f"Mock execution {self.execution_count} completed. Success: {success}"
        )

        return AgentExecutionResult(
            success=success,
            output=output,
            error=None if success else "Simulated failure",
            artifacts_produced=artifacts,
            side_effects=[],
            learnings=learnings,
            failed_approaches=failed_approaches,
            tools_invoked=["mock_tool_1", "mock_tool_2"],
            context_tokens_used=len(prompt.split()) * 2,
        )

    def _extract_task_title(self, prompt: str) -> str:
        match = re.search(r"\*\*Your Task:\*\*\s*(.+)", prompt)
        if match:
            return match.group(1).strip()
        return "Mock Task"

    def _extract_file_targets(self, prompt: str) -> List[str]:
        return re.findall(r"file_exists:([^\s]+)", prompt)

    def _extract_min_length(self, prompt: str) -> int:
        values = [int(v) for v in re.findall(r"min_length:\s*(\d+)", prompt)]
        return max(values) if values else 0

    def _generate_content(self, task_title: str, original_request: str) -> str:
        title = task_title.lower()
        topic = self._derive_topic(original_request)
        if "scope" in title:
            return (
                "# Research Scope\n\n"
                f"**Topic:** {topic}\n\n"
                "## Research Questions\n"
                f"- What is the current status of {topic} and what changed in the last 30 days?\n"
                f"- Which blockers prevent completion of {topic}, and what evidence confirms them?\n"
                f"- What artifacts or metrics would prove {topic} is complete and working?\n"
                "## Boundaries\n"
                "- Timeframe: focus on the most recent 12 months of relevant changes\n"
                "- Scope: include only information directly tied to the report goal and delivery\n"
                "- Exclusions: omit unrelated macro trends and speculative claims\n"
                "## Success Criteria\n"
                "- Report includes a clear executive summary, findings, and next steps\n"
                "- At least 3 concrete, verifiable insights are documented\n"
                "- Completion criteria and deliverable format are explicitly stated\n"
            )
        if "gather" in title:
            return (
                "# Research Notes\n\n"
                f"## Topic\n{topic}\n\n"
                "## Sources\n"
                "- Internal project notes (2026-01-15)\n"
                "- System run logs and URW documentation\n"
                "- Engineering checklist for harness integration\n\n"
                "## Notes\n"
                "- Identified current blockers and failure modes.\n"
                "- Captured evidence requirements for completion.\n"
                "- Recorded metrics to confirm successful completion.\n"
            )
        if "analyze" in title:
            return (
                "# Analysis\n\n"
                f"## Focus\n{topic}\n\n"
                "## Patterns\n"
                "- Repeated failures correlate with missing specificity in scope definitions.\n"
                "- Completion improves when outputs map directly to evaluation criteria.\n\n"
                "## Insights\n"
                "- The report needs explicit scope and measurable completion artifacts.\n"
                "- Evaluation passes when outputs reference the requested deliverable directly.\n\n"
                "## Recommended Direction\n"
                "- Anchor every section to the stated topic and acceptance criteria.\n"
            )
        if "report" in title:
            return (
                "# Final Report\n\n"
                f"## Executive Summary\nThis report addresses: {topic}. It summarizes the current status, key"
                " findings, and actionable next steps to verify completion.\n\n"
                "## Scope and Objectives\n"
                "- Confirm the harness integration can complete end-to-end without deadlocks.\n"
                "- Validate evidence artifacts and evaluator decisions are recorded correctly.\n"
                "- Ensure smoke runs terminate within configured iteration limits.\n\n"
                "## Findings\n"
                "1. The harness successfully completes qualitative scope tasks with request-specific output.\n"
                "2. Composite tasks now pass when binary and constraint checks succeed.\n"
                "3. Replan flows no longer collide on task IDs after uniqueness fixes.\n"
                "4. Evaluation outcomes are persisted with explicit verification evidence.\n"
                "5. Remaining failures are limited to report quality, not orchestration flow.\n\n"
                "## Detailed Evidence\n"
                "- Artifacts were generated for scope, notes, analysis, and report stages.\n"
                "- Verification records show binary/constraint checks passing for required files.\n"
                "- Iteration logs confirm task transitions from pending to complete.\n\n"
                "## Recommendations\n"
                "- Keep report content specific to the harness status and acceptance criteria.\n"
                "- Expand actionable recommendations with owners, timelines, and success metrics.\n"
                "- Continue capturing verification artifacts for auditability.\n\n"
                "## Implementation Plan\n"
                "1. Run the smoke test with capped iterations to confirm deterministic completion.\n"
                "2. Review generated artifacts to ensure required evidence is present.\n"
                "3. Promote the verified flow into the main harness execution path.\n\n"
                "## Risks and Mitigations\n"
                "- Risk: Evaluation drift due to vague outputs. Mitigation: enforce request-specific content.\n"
                "- Risk: Replan loops. Mitigation: cap iterations and monitor failure counters.\n"
                "- Risk: Missing artifacts. Mitigation: assert file outputs on composite tasks.\n"
            )
        return f"# {task_title}\n\nTask completed for: {topic}.\n"

    def _extract_original_request(self, prompt: str) -> str:
        match = re.search(r"\*\*Original Request:\*\*\s*(.+)", prompt)
        if match:
            return match.group(1).strip()
        return ""

    def _derive_topic(self, original_request: str) -> str:
        request = (original_request or "").strip()
        if not request:
            return "the Universal Agent harness integration"
        lowered = request.lower()
        generic_markers = ["write a short report", "confirm completion", "short report"]
        if any(marker in lowered for marker in generic_markers):
            return "the Universal Agent harness integration status"
        return request

    def _ensure_min_length(self, content: str, min_length: int) -> str:
        if min_length <= 0 or len(content) >= min_length:
            return content
        topic = self._extract_topic_from_content(content)
        needed = min_length - len(content)
        extra_lines: List[str] = []
        index = 1
        while needed > 0:
            line = (
                f"Supplemental detail {index}: This section expands on {topic} with concrete"
                " evidence, decisions, and expected outcomes for the harness workflow."
            )
            extra_lines.append(line)
            needed -= len(line) + 2
            index += 1
        return content + "\n\n" + "\n\n".join(extra_lines)

    def _extract_topic_from_content(self, content: str) -> str:
        match = re.search(r"\*\*Topic:\*\*\s*(.+)", content)
        if match:
            return match.group(1).strip()
        match = re.search(r"addresses:\s*(.+?)\.", content)
        if match:
            return match.group(1).strip()
        return "the Universal Agent harness integration"


def create_adapter_for_system(system_type: str, config: Dict[str, Any]) -> BaseAgentAdapter:
    adapters = {
        "universal_agent": UniversalAgentAdapter,
        "mock": MockAgentAdapter,
    }
    adapter_class = adapters.get(system_type)
    if not adapter_class:
        raise ValueError(f"Unknown system type: {system_type}. Options: {list(adapters.keys())}")
    return adapter_class(config)
