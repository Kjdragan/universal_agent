"""Async HTTP client for calling the UA gateway REST API.

Used by the CC bot to perform Task Hub actions (approve, reject, etc.)
through the gateway instead of direct SQLite access, ensuring concurrency
safety and consistent behavior with the web dashboard.

Auth uses the internal service token (UA_INTERNAL_API_TOKEN), the same
pattern as the Telegram bot (see src/universal_agent/api/gateway_bridge.py).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080"
_DEFAULT_TIMEOUT = 15.0


def _gateway_url() -> str:
    return (os.getenv("UA_GATEWAY_URL") or _DEFAULT_GATEWAY_URL).rstrip("/")


def _auth_headers() -> dict[str, str]:
    token = (
        (os.getenv("UA_INTERNAL_API_TOKEN") or "").strip()
        or (os.getenv("UA_OPS_TOKEN") or "").strip()
    )
    if not token:
        logger.warning("No UA_INTERNAL_API_TOKEN or UA_OPS_TOKEN configured")
        return {}
    return {"x-ua-internal-token": token}


async def get_review_tasks() -> list[dict[str, Any]]:
    """Fetch tasks requiring human review from the gateway.

    Calls GET /api/v1/dashboard/human-actions/highlight and returns
    the ``human_tasks`` list (tasks in needs_review / pending_review
    that require human approval).
    """
    url = f"{_gateway_url()}/api/v1/dashboard/human-actions/highlight"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers=_auth_headers())
            resp.raise_for_status()
            data = resp.json()
            return data.get("human_tasks", [])
    except Exception as exc:
        logger.error("Failed to fetch review tasks: %s", exc)
        return []


async def approve_task(task_id: str) -> dict[str, Any]:
    """Approve a task via the gateway (dashboard 'Approve' path).

    Calls POST /api/v1/dashboard/todolist/tasks/{task_id}/approve which
    transitions the task to open+human_approved and immediately claims it.
    """
    url = f"{_gateway_url()}/api/v1/dashboard/todolist/tasks/{task_id}/approve"
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        resp = await client.post(url, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


async def task_action(
    task_id: str,
    action: str,
    *,
    reason: str = "",
    note: str = "",
    agent_id: str = "discord_operator",
) -> dict[str, Any]:
    """Perform a task lifecycle action via the gateway.

    Calls POST /api/v1/dashboard/todolist/tasks/{task_id}/action with
    the given action (e.g. park, reject, complete, review, snooze).
    """
    url = f"{_gateway_url()}/api/v1/dashboard/todolist/tasks/{task_id}/action"
    payload = {
        "action": action,
        "reason": reason,
        "note": note,
        "agent_id": agent_id,
    }
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        return resp.json()


async def get_dispatch_queue(limit: int = 20) -> dict[str, Any]:
    """Fetch the current dispatch queue summary."""
    url = f"{_gateway_url()}/api/v1/dashboard/todolist/dispatch-queue"
    params = {"limit": limit}
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, params=params, headers=_auth_headers())
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("Failed to fetch dispatch queue: %s", exc)
        return {}


async def get_approvals_highlight() -> dict[str, Any]:
    """Fetch the approvals highlight (pending count + list)."""
    url = f"{_gateway_url()}/api/v1/dashboard/approvals/highlight"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers=_auth_headers())
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("Failed to fetch approvals highlight: %s", exc)
        return {}
