"""FastAPI router for the centralized three-panel viewer (Track B).

One endpoint:

  POST /api/viewer/resolve   — any identity hints → SessionViewTarget

Producers (Task Hub, Sessions, Calendar, Proactive, Chat) call /resolve
to normalize the various hint shapes (session_id-only, run_id-only,
workspace_name, ...) into a canonical {session_id, run_id, workspace_dir}
target. The web-ui then navigates to the live three-panel UI in
`app/page.tsx?session_id=...&run_id=...`, which already handles both
live attach (WebSocket) and rehydration (trace.json + run.log).

The earlier `/api/viewer/hydrate` endpoint and its companion
`/dashboard/viewer/[targetKind]/[targetId]/page.tsx` route rendered a
strictly inferior version of the three-panel view; both were removed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from universal_agent.viewer import resolve_session_view_target

logger = logging.getLogger(__name__)

router = APIRouter()


class ResolveBody(BaseModel):
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    workspace_dir: Optional[str] = None
    workspace_name: Optional[str] = None


@router.post("/api/viewer/resolve")
async def viewer_resolve(body: ResolveBody) -> JSONResponse:
    """Resolve any combination of identity hints to a canonical SessionViewTarget."""
    if not any(
        (
            body.session_id,
            body.run_id,
            body.workspace_dir,
            body.workspace_name,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of session_id, run_id, workspace_dir, workspace_name is required.",
        )

    trace: list[str] = []
    target = resolve_session_view_target(
        session_id=body.session_id,
        run_id=body.run_id,
        workspace_dir=body.workspace_dir,
        workspace_name=body.workspace_name,
        trace=trace,
    )
    if target is None:
        # Surface a per-branch diagnostic trace so the network tab tells us
        # exactly why this hint set didn't resolve.
        logger.warning(
            "Viewer resolver miss: hints=%s trace=%s",
            body.model_dump(exclude_none=True),
            trace,
        )
        return JSONResponse(
            status_code=404,
            content={
                "detail": {
                    "code": "viewer_target_not_found",
                    "message": "Could not resolve a viewer target from the given hints.",
                    "hints": body.model_dump(exclude_none=True),
                    "trace": trace,
                }
            },
        )
    return JSONResponse(content=target.to_dict())


@router.get("/api/viewer/health")
async def viewer_health() -> dict[str, Any]:
    """Sanity check + version probe for the viewer subsystem."""
    return {
        "ok": True,
        "subsystem": "viewer",
        "endpoints": [
            "POST /api/viewer/resolve",
        ],
    }
