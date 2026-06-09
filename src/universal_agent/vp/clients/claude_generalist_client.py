from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from universal_agent.execution_engine import EngineConfig, ProcessTurnAdapter
from universal_agent.feature_flags import vp_no_progress_kill_seconds
from universal_agent.vp.clients.base import (
    MissionOutcome,
    VpClient,
    consume_adapter_events_with_idle_timeout,
)


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
            final_text, error_text, trace_id = (
                await consume_adapter_events_with_idle_timeout(
                    adapter, prompt,
                    idle_timeout_seconds=vp_no_progress_kill_seconds(),
                )
            )
        finally:
            await adapter.close()

        # SDK-path poison-pill parking — see services/sdk_timeout_park.
        _task_id = str(payload.get("task_id") or "").strip() if isinstance(payload, dict) else ""
        _mission_id = str(mission.get("mission_id") or "")
        outcome_payload: dict[str, Any] = {"trace_id": trace_id, "final_text": final_text}

        if error_text:
            try:
                from universal_agent.services.sdk_timeout_park import (
                    is_sdk_timeout,
                    record_sdk_timeout_and_maybe_park,
                    reset_sdk_timeout_counter,
                )
                if is_sdk_timeout(error_text):
                    parked, count = record_sdk_timeout_and_maybe_park(
                        task_id=_task_id,
                        mission_id=_mission_id,
                        error_text=error_text,
                    )
                    outcome_payload["sdk_consecutive_timeouts"] = count
                    if parked:
                        outcome_payload["sdk_parked_for_review"] = True
                        error_text = (
                            f"{error_text} "
                            f"[parked after {count} consecutive SDK timeouts]"
                        )
                else:
                    reset_sdk_timeout_counter(task_id=_task_id, mission_id=_mission_id)
            except Exception:
                pass
            return MissionOutcome(
                status="failed",
                result_ref=f"workspace://{workspace_dir}",
                message=error_text,
                payload=outcome_payload,
            )

        try:
            from universal_agent.services.sdk_timeout_park import (
                reset_sdk_timeout_counter,
            )
            reset_sdk_timeout_counter(task_id=_task_id, mission_id=_mission_id)
        except Exception:
            pass

        return MissionOutcome(
            status="completed",
            result_ref=f"workspace://{workspace_dir}",
            payload=outcome_payload,
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

    # NLM-first artifact generation guidance
    lines.append("")
    lines.append("## Knowledge Base Artifact Generation (NLM-FIRST RULE)")
    lines.append("When generating artifacts from research or knowledge base content, "
                 "ALWAYS prefer NotebookLM (via `nlm` CLI) over generic tools:")
    lines.append("- Reports → `nlm report create <notebook> --confirm`")
    lines.append("- Infographics → `nlm infographic create <notebook> --orientation landscape --confirm`")
    lines.append("- Audio → `nlm audio create <notebook> --confirm`")
    lines.append("- Slides → `nlm slides create <notebook> --confirm`")
    lines.append("- DO NOT use `generate_image` for KB-related infographics.")
    lines.append("- DO NOT generate markdown reports manually when NLM can synthesize from sources.")
    lines.append("")
    lines.append("### Performance: Parallel Artifact Generation")
    lines.append("Fire ALL `nlm <type> create` commands FIRST (they run concurrently server-side), "
                 "then poll `nlm studio status <notebook>` once for all of them.")
    lines.append("")
    lines.append("### Performance: Adaptive Polling Intervals")
    lines.append("- Fast research: `sleep 5` between polls (completes in ~30s)")
    lines.append("- Deep research: `sleep 20` between polls (completes in ~5 min)")
    lines.append("- Studio artifacts (report/infographic/slides): `sleep 10`")
    lines.append("- Audio/video: `sleep 20`")
    lines.append("- Do NOT use a fixed `sleep 15` for everything.")
    lines.append("")

    lines.append("Work independently, produce durable outputs in the workspace, and provide a concise completion summary.")
    return "\n".join(lines)
