from __future__ import annotations

from typing import Any


SUPERVISOR_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "factory-supervisor",
        "label": "Factory Supervisor",
        "enabled": True,
        "scope": "fleet",
        "default": True,
    },
    {
        "id": "csi-supervisor",
        "label": "CSI Supervisor",
        "enabled": True,
        "scope": "intelligence",
    },
]


def supervisor_registry() -> list[dict[str, Any]]:
    return [dict(row) for row in SUPERVISOR_REGISTRY]


def find_supervisor(supervisor_id: str) -> dict[str, Any] | None:
    key = str(supervisor_id or "").strip().lower()
    for row in SUPERVISOR_REGISTRY:
        if str(row.get("id") or "").strip().lower() == key:
            return dict(row)
    return None
