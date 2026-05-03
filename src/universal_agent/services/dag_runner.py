from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = int(os.getenv("UA_DAG_MAX_ITERATIONS", "50") or 50)

# Named status constants -- single source of truth for DAG lifecycle states.
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_WAITING_ON_HUMAN = "waiting_for_human"  # DB-compatible value; see tests/test_status_constants.py
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


@dataclass
class DagState:
    """State of a DAG execution."""
    status: str = STATUS_PENDING  # pending, running, completed, waiting_on_human, failed
    current_node: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)


class DagRunner:
    """Native Python DAG Runner for executing deterministic workflows.

    The runner traverses a graph of nodes connected by edges, executing
    registered handlers for each node type.  Conditional edges allow
    branching based on handler results (e.g. binary LLM classifiers).
    """

    def __init__(
        self,
        workflow_def: Dict[str, Any],
        *,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    ):
        self.workflow_def = workflow_def
        self.nodes = {n["id"]: n for n in workflow_def.get("nodes", [])}
        self.edges = workflow_def.get("edges", [])
        self.max_iterations = max(1, int(max_iterations))
        self.handlers: Dict[
            str,
            Callable[[Dict[str, Any], DagState], Awaitable[Dict[str, Any]]],
        ] = {}

    def register_handler(
        self,
        node_type: str,
        handler: Callable[[Dict[str, Any], DagState], Awaitable[Dict[str, Any]]],
    ) -> None:
        """Register an async handler for a specific node type."""
        self.handlers[node_type] = handler

    # ------------------------------------------------------------------
    # Edge resolution
    # ------------------------------------------------------------------

    def _get_next_node(
        self, current_node_id: str, handler_result: Dict[str, Any]
    ) -> Optional[str]:
        """Determine the next node based on edges and handler results."""
        out_edges = [e for e in self.edges if e["from"] == current_node_id]
        if not out_edges:
            return None

        result_val = handler_result.get("result")

        for edge in out_edges:
            condition = edge.get("condition")
            if condition is None:
                return edge["to"]
            elif condition == result_val:
                return edge["to"]

        return None

    # ------------------------------------------------------------------
    # Main execution loop
    # ------------------------------------------------------------------

    async def run(self, initial_state: Optional[DagState] = None) -> DagState:
        """Run the DAG state machine until completion, failure, or human gate."""
        state = initial_state or DagState(
            status=STATUS_RUNNING,
            current_node=self.workflow_def.get("start"),
        )

        if state.status == STATUS_PENDING:
            state.status = STATUS_RUNNING

        iteration = 0

        while state.status == STATUS_RUNNING and state.current_node:
            # ── Safety valve: prevent infinite loops ──
            iteration += 1
            if iteration > self.max_iterations:
                state.status = STATUS_FAILED
                state.context["error"] = (
                    f"DAG execution exceeded max iterations ({self.max_iterations}). "
                    "Possible infinite loop detected."
                )
                logger.error(
                    "DAG runner halted: max iterations (%d) exceeded at node '%s'",
                    self.max_iterations,
                    state.current_node,
                )
                break

            node = self.nodes.get(state.current_node)
            if not node:
                state.status = STATUS_FAILED
                state.context["error"] = (
                    f"Node '{state.current_node}' not found in workflow."
                )
                break

            node_type = node.get("type", "action")
            logger.info("DAG node executing: id=%s type=%s", node["id"], node_type)

            if node_type == "human_gate":
                state.status = STATUS_WAITING_ON_HUMAN
                state.history.append(
                    {"node_id": node["id"], "status": STATUS_WAITING_ON_HUMAN}
                )
                break

            handler = self.handlers.get(node_type)
            if not handler:
                state.status = STATUS_FAILED
                state.context["error"] = (
                    f"No handler registered for node type '{node_type}'."
                )
                break

            try:
                result = await handler(node, state)

                # Merge any context updates
                if "context_update" in result:
                    state.context.update(result["context_update"])

                if result.get("status") == STATUS_FAILED:
                    state.status = STATUS_FAILED
                    state.context["error"] = result.get(
                        "error", "Node execution failed."
                    )
                    state.history.append({"node_id": node["id"], "status": STATUS_FAILED})
                    break

                state.history.append({"node_id": node["id"], "status": STATUS_SUCCESS})

                # Advance to next node
                next_node = self._get_next_node(state.current_node, result)
                if next_node:
                    state.current_node = next_node
                else:
                    state.current_node = None
                    state.status = STATUS_COMPLETED

            except Exception as e:
                state.status = STATUS_FAILED
                state.context["error"] = str(e)
                state.history.append({"node_id": node["id"], "status": STATUS_ERROR})
                logger.exception(
                    "DAG node '%s' raised an exception", node["id"]
                )
                break

        if state.status == STATUS_RUNNING and not state.current_node:
            state.status = STATUS_COMPLETED

        logger.info(
            "DAG execution finished: status=%s nodes_visited=%d",
            state.status,
            len(state.history),
        )
        return state
