"""DagClient — VpClient implementation for deterministic DAG execution.

This client replaces the autonomous Claude Code loop with a deterministic
state machine driven by the DagRunner.  It is selected when a mission's
``execution_mode`` is ``"dag"``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from universal_agent.services.dag_handlers import (
    make_subprocess_handler,
)
from universal_agent.services.dag_runner import DagRunner, DagState
from universal_agent.vp.clients.base import MissionOutcome, VpClient

logger = logging.getLogger(__name__)


class DagClient(VpClient):
    """Execute VP missions via deterministic DAG workflows."""

    async def run_mission(
        self,
        *,
        mission: dict[str, Any],
        workspace_root: Path,
    ) -> MissionOutcome:
        # ── Extract workflow definition ──────────────────────────────
        workflow_def = self._extract_workflow(mission)
        if workflow_def is None:
            return MissionOutcome(
                status="failed",
                message="No dag_definition found in mission payload.",
                result_ref=f"workspace://{workspace_root}",
            )

        workspace_root.mkdir(parents=True, exist_ok=True)

        # ── Build runner and register handlers ───────────────────────
        runner = DagRunner(workflow_def)

        # Register subprocess handler (runs shell commands in workspace)
        runner.register_handler(
            "subprocess",
            make_subprocess_handler(workspace_root=workspace_root),
        )

        # Register generic action handler (placeholder — logs and succeeds)
        runner.register_handler("action", _action_handler)

        mission_id = str(mission.get("mission_id") or "unknown")
        logger.info(
            "DagClient starting: mission_id=%s workspace=%s nodes=%d",
            mission_id,
            workspace_root,
            len(workflow_def.get("nodes", [])),
        )

        # ── Execute ──────────────────────────────────────────────────
        try:
            state = await runner.run()
        except Exception as exc:
            logger.exception("DagClient execution failed: mission_id=%s", mission_id)
            return MissionOutcome(
                status="failed",
                message=f"DAG execution error: {exc}",
                result_ref=f"workspace://{workspace_root}",
            )

        # ── Translate DagState → MissionOutcome ──────────────────────
        return self._state_to_outcome(state, workspace_root)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_workflow(mission: dict[str, Any]) -> dict[str, Any] | None:
        """Extract the DAG workflow definition from the mission payload.

        Supports two modes:
        - Inline: ``payload_json.dag_definition`` contains the workflow dict.
        - File:   ``payload_json.dag_definition_path`` points to a YAML file.
        """
        payload = _parse_payload(mission)

        # Mode 1: inline definition
        dag_def = payload.get("dag_definition")
        if isinstance(dag_def, dict) and dag_def:
            return dag_def

        # Mode 2: YAML file path
        dag_path = str(payload.get("dag_definition_path") or "").strip()
        if dag_path:
            from universal_agent.services.dag_loader import load_workflow
            try:
                return load_workflow(Path(dag_path))
            except Exception as exc:
                logger.error("Failed to load DAG workflow from '%s': %s", dag_path, exc)
                return None

        return None

    @staticmethod
    def _state_to_outcome(
        state: DagState, workspace_root: Path
    ) -> MissionOutcome:
        """Map a DagState to a MissionOutcome."""
        result_ref = f"workspace://{workspace_root}"

        if state.status == "completed":
            return MissionOutcome(
                status="completed",
                result_ref=result_ref,
                payload={
                    "dag_history": state.history,
                    "dag_context": state.context,
                },
            )
        elif state.status == "waiting_for_human":
            return MissionOutcome(
                status="completed",
                result_ref=result_ref,
                message=f"DAG paused at human gate: {state.current_node}",
                payload={
                    "dag_status": "waiting_for_human",
                    "dag_current_node": state.current_node,
                    "dag_history": state.history,
                    "dag_context": state.context,
                },
            )
        else:
            error = state.context.get("error", "Unknown DAG failure")
            return MissionOutcome(
                status="failed",
                result_ref=result_ref,
                message=error,
                payload={
                    "dag_history": state.history,
                    "dag_context": state.context,
                },
            )


# ---------------------------------------------------------------------- #
# Default action handler (placeholder)
# ---------------------------------------------------------------------- #

async def _action_handler(
    node: dict[str, Any], state: DagState
) -> dict[str, Any]:
    """Default 'action' handler — logs the node and succeeds.

    In production, this would be replaced with an LLM prompt handler
    that sends the node's prompt to ZAI.
    """
    prompt = str(node.get("prompt") or "").strip()
    logger.info("DAG action node: id=%s prompt='%s'", node["id"], prompt[:100])
    return {"status": "success", "context_update": {"last_action": node["id"]}}


# ---------------------------------------------------------------------- #
# Payload parsing
# ---------------------------------------------------------------------- #

def _parse_payload(mission: dict[str, Any]) -> dict[str, Any]:
    """Parse payload_json from a mission dict."""
    raw = mission.get("payload_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, Exception):
            pass
    return {}
