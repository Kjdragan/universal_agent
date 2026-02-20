from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from universal_agent.agent_core import EventType
from universal_agent.execution_engine import EngineConfig, ProcessTurnAdapter
from universal_agent.vp.clients.base import MissionOutcome, VpClient


class ClaudeGeneralistClient(VpClient):
    """External generalist VP worker client using Claude Agent SDK runtime."""

    async def run_mission(
        self,
        *,
        mission: dict[str, Any],
        workspace_root: Path,
    ) -> MissionOutcome:
        payload = _payload(mission.get("payload_json"))
        mission_id = str(mission.get("mission_id") or "").replace("/", "_").replace("..", "_")
        workspace_dir = (workspace_root / (mission_id or "mission")).resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)

        objective = str(mission.get("objective") or "").strip()
        if not objective:
            return MissionOutcome(status="failed", message="missing objective")

        prompt = _build_prompt(objective=objective, payload=payload)
        adapter = ProcessTurnAdapter(EngineConfig(workspace_dir=str(workspace_dir), user_id="vp.general.worker"))
        trace_id: Optional[str] = None
        final_text = ""
        error_text: Optional[str] = None

        try:
            await adapter.initialize()
            adapter.config.__dict__["_run_source"] = "vp.general.external"
            adapter.config.__dict__["_request_metadata"] = {
                "source": "vp.general.external",
                "vp_context": {
                    "vp_id": str(mission.get("vp_id") or "vp.general.primary"),
                    "mission_id": str(mission.get("mission_id") or ""),
                },
            }
            async for event in adapter.execute(prompt):
                if event.type == EventType.TEXT and isinstance(event.data, dict):
                    if event.data.get("final") is True:
                        final_text = str(event.data.get("text") or "")
                elif event.type == EventType.ERROR and isinstance(event.data, dict):
                    error_text = str(event.data.get("message") or event.data.get("error") or "mission failed").strip()
                elif event.type == EventType.ITERATION_END and isinstance(event.data, dict):
                    trace_id = str(event.data.get("trace_id") or "") or None
        finally:
            await adapter.close()

        if error_text:
            return MissionOutcome(
                status="failed",
                result_ref=f"workspace://{workspace_dir}",
                message=error_text,
                payload={"trace_id": trace_id, "final_text": final_text},
            )
        return MissionOutcome(
            status="completed",
            result_ref=f"workspace://{workspace_dir}",
            payload={"trace_id": trace_id, "final_text": final_text},
        )


def _payload(payload_json: Any) -> dict[str, Any]:
    if isinstance(payload_json, dict):
        return payload_json
    if isinstance(payload_json, str) and payload_json.strip():
        try:
            loaded = json.loads(payload_json)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            return {}
    return {}


def _build_prompt(*, objective: str, payload: dict[str, Any]) -> str:
    constraints = payload.get("constraints")
    budget = payload.get("budget")
    lines = [
        "You are the GENERALIST primary VP agent executing an autonomous mission.",
        f"Objective: {objective}",
    ]
    if isinstance(constraints, dict) and constraints:
        lines.append("Constraints:")
        for key, value in constraints.items():
            lines.append(f"- {key}: {value}")
    if isinstance(budget, dict) and budget:
        lines.append("Budget:")
        for key, value in budget.items():
            lines.append(f"- {key}: {value}")
    lines.append("Work independently, produce durable outputs in the workspace, and provide a concise completion summary.")
    return "\n".join(lines)
