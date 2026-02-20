from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from universal_agent.agent_core import EventType
from universal_agent.execution_engine import EngineConfig, ProcessTurnAdapter
from universal_agent.feature_flags import coder_vp_id, vp_handoff_root, vp_hard_block_ua_repo
from universal_agent.guardrails.workspace_guard import (
    WorkspaceGuardError,
    enforce_external_target_path,
)
from universal_agent.vp.clients.base import MissionOutcome, VpClient


class ClaudeCodeClient(VpClient):
    """External CODIE worker client.

    Executes missions in isolated external workspaces via ProcessTurnAdapter.
    """

    async def run_mission(
        self,
        *,
        mission: dict[str, Any],
        workspace_root: Path,
    ) -> MissionOutcome:
        payload = _payload(mission.get("payload_json"))
        constraints = payload.get("constraints") if isinstance(payload, dict) else {}
        if not isinstance(constraints, dict):
            constraints = {}

        workspace_dir = _resolve_workspace_dir(
            mission_id=str(mission.get("mission_id") or ""),
            workspace_root=workspace_root,
            constraints=constraints,
        )
        workspace_dir.mkdir(parents=True, exist_ok=True)
        objective = str(mission.get("objective") or "").strip()
        if not objective:
            return MissionOutcome(status="failed", message="missing objective")

        adapter = ProcessTurnAdapter(EngineConfig(workspace_dir=str(workspace_dir), user_id="vp.coder.worker"))
        trace_id: Optional[str] = None
        final_text = ""
        error_text: Optional[str] = None

        try:
            await adapter.initialize()
            adapter.config.__dict__["_run_source"] = "vp.coder.external"
            adapter.config.__dict__["_request_metadata"] = {
                "source": "vp.coder.external",
                "vp_context": {
                    "vp_id": coder_vp_id(),
                    "mission_id": str(mission.get("mission_id") or ""),
                },
            }
            async for event in adapter.execute(objective):
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


def _resolve_workspace_dir(
    *,
    mission_id: str,
    workspace_root: Path,
    constraints: dict[str, Any],
) -> Path:
    target_path = str(constraints.get("target_path") or "").strip()
    if target_path:
        resolved = Path(target_path).expanduser().resolve()
        _enforce_coder_target_guardrails(resolved)
        return resolved

    safe_mission = mission_id.replace("/", "_").replace("..", "_").strip() or "mission"
    return (workspace_root / safe_mission).resolve()


def _enforce_coder_target_guardrails(target: Path) -> None:
    if not vp_hard_block_ua_repo(default=True):
        return
    handoff = Path(vp_handoff_root()).expanduser().resolve()

    repo_root = Path(__file__).resolve().parents[4]
    blocked_roots = [
        repo_root.resolve(),
        (repo_root / "AGENT_RUN_WORKSPACES").resolve(),
        (repo_root / "artifacts").resolve(),
        (repo_root / "Memory_System").resolve(),
    ]
    try:
        enforce_external_target_path(
            target,
            blocked_roots=blocked_roots,
            allowlisted_roots=[handoff],
            operation="CODIE target path",
        )
    except WorkspaceGuardError as exc:
        raise ValueError(
            "CODIE target path is blocked inside UA repository/runtime roots. "
            f"Use handoff root {handoff} or another external path. ({exc})"
        ) from exc
