"""demo_built_notifier.py — deterministic "demo built" operator notification.

When a proactive demo build completes (`cody_demo_task` → `vp.mission.completed`),
email the operator a FYI with a link to the **playable explainer video** when one
exists, falling back to the exhibit / workspace otherwise.

Properties:
- **Deterministic** — called from the worker_loop completion seam, NOT dependent on
  any agent obeying an email directive (the demo lane had no notification at all).
- **Engine-agnostic** — fires for the bespoke builder AND the demo_factory engine.
- **Best-effort & non-raising** — every step is guarded; a failure here never breaks
  demo completion.

The explainer video is rendered by demo_factory/ClearSpring as
``<workspace>/video/<demo-id>-explainer.mp4`` — but only when ClearSpring + its
toolchain are present (today desktop-only). So the video link is best-effort:
present when the mp4 exists (then published to the tailnet scratchpad for a playable
URL), otherwise the email links the exhibit/workspace and notes the video is pending.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _find_explainer_video(workspace: Path) -> Optional[Path]:
    """Best-effort: find the rendered explainer mp4 under the workspace.

    demo_factory writes ``<demo-dir>/video/<demo-id>-explainer.mp4`` (and a
    ``*.musiconly.mp4`` sidecar we must NOT pick). The demo dir may be the workspace
    itself or a ``demo-<slug>/`` subdir, so search recursively but bounded.
    """
    if not workspace.is_dir():
        return None
    cands: list[Path] = []
    try:
        for p in workspace.rglob("*-explainer.mp4"):
            if p.is_file() and not p.name.endswith(".musiconly.mp4"):
                cands.append(p)
    except OSError:
        return None
    if not cands:
        return None
    # Prefer the largest (the ducked, narrated canonical file).
    return max(cands, key=lambda p: p.stat().st_size if p.exists() else 0)


def _read_manifest_url(workspace: Path) -> str:
    """Best-effort: pull ``exhibit_url`` from the demo's manifest.json (root or one
    level down). Returns '' when absent."""
    for mf in (workspace / "manifest.json", *workspace.glob("*/manifest.json")):
        try:
            if mf.is_file():
                data = json.loads(mf.read_text(encoding="utf-8"))
                url = str(data.get("exhibit_url") or "").strip()
                if url:
                    return url
        except (OSError, ValueError):
            continue
    return ""


def compose_demo_built_email(
    *,
    title: str,
    capability: str,
    build_engine: str,
    video_url: str,
    exhibit_url: str,
    workspace_dir: str,
    review_required: bool,
) -> tuple[str, str, str]:
    """Pure: build (subject, text, html). No I/O — unit-testable."""
    review_note = (
        "Curated demo — awaiting your review." if review_required
        else "Direct demo — auto-finalized on the endpoint check."
    )
    subject = f"🎬 Demo built: {title}"

    if video_url:
        video_text = f"▶ Watch the explainer video:\n  {video_url}\n"
        video_html = (
            f'<p style="margin:16px 0"><a href="{video_url}" '
            'style="background:#1f6f50;color:#fff;padding:10px 18px;border-radius:8px;'
            'text-decoration:none;font-weight:600">▶ Watch the explainer video</a></p>'
        )
    else:
        video_text = "Explainer video: not rendered on this host yet (ClearSpring pending).\n"
        video_html = (
            '<p style="margin:16px 0;color:#9a5b00">Explainer video: not rendered on this '
            'host yet (ClearSpring video toolchain pending on the VPS).</p>'
        )

    exhibit_text = f"Exhibit: {exhibit_url}\n" if exhibit_url else ""
    exhibit_html = (
        f'<p>Exhibit: <a href="{exhibit_url}">{exhibit_url}</a></p>' if exhibit_url else ""
    )

    text = (
        f"A new demo was just built by the proactive pipeline.\n\n"
        f"Demo: {title}\n"
        f"Capability: {capability or '(n/a)'}\n"
        f"Engine: {build_engine}\n"
        f"{review_note}\n\n"
        f"{video_text}"
        f"{exhibit_text}"
        f"Workspace: {workspace_dir}\n\n"
        f"— UA proactive demo pipeline"
    )
    html = (
        f'<div style="font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#1c1c1a">'
        f"<h2 style=\"margin:0 0 4px\">🎬 Demo built</h2>"
        f'<p style="font-size:18px;font-weight:600;margin:0 0 8px">{title}</p>'
        f"<p style=\"color:#5d5d57;margin:2px 0\"><b>Capability:</b> {capability or '(n/a)'}</p>"
        f'<p style="color:#5d5d57;margin:2px 0"><b>Engine:</b> {build_engine}</p>'
        f'<p style="color:#5d5d57;margin:2px 0">{review_note}</p>'
        f"{video_html}"
        f"{exhibit_html}"
        f'<p style="color:#888;font-size:12px;margin-top:14px">Workspace: {workspace_dir}<br>'
        f"— UA proactive demo pipeline</p></div>"
    )
    return subject, text, html


def _recipient() -> str:
    try:
        from universal_agent.session_policy import _notification_email_default
        return _notification_email_default()
    except Exception:
        return "kevinjdragan@gmail.com"


def _publish_video(video: Path, demo_id: str) -> str:
    """Publish the mp4 to the tailnet scratchpad; return its URL or ''. Best-effort."""
    try:
        from universal_agent.services.scratch_publish import publish_file_to_scratch
        url = publish_file_to_scratch(
            video,
            slug=f"demo-video-{demo_id}",
            title=f"Explainer: {demo_id}",
            description="Auto-rendered demo explainer video.",
            artifact_id=f"demo-video-{demo_id}",
        )
        return str(url or "")
    except Exception:
        logger.warning("demo-built: video publish failed for %s", demo_id, exc_info=True)
        return ""


def _register_artifact(*, demo_id: str, title: str, video_url: str, exhibit_url: str,
                       build_engine: str) -> None:
    """Best-effort: record a 'built' artifact so the daily digest reflects built
    (not just surfaced) demos. Never raises."""
    try:
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
        from universal_agent.services.proactive_artifacts import upsert_artifact
        conn = connect_runtime_db(get_activity_db_path())
        try:
            upsert_artifact(
                conn,
                artifact_type="demo",
                source_kind="cody_demo_task",
                title=title,
                source_ref=demo_id,
                summary=f"Demo built ({build_engine}).",
                artifact_uri=video_url or exhibit_url,
                metadata={"build_state": "built", "build_engine": build_engine,
                          "video_url": video_url, "exhibit_url": exhibit_url},
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("demo-built: artifact upsert failed for %s", demo_id, exc_info=True)


async def notify_demo_built(
    *,
    demo_id: str,
    title: str,
    capability: str,
    workspace_dir: str,
    build_engine: str = "bespoke",
    review_required: bool = False,
) -> dict[str, Any]:
    """Send the deterministic 'demo built' email (best-effort, never raises)."""
    result: dict[str, Any] = {"emailed": False, "video_url": "", "exhibit_url": ""}
    try:
        ws = Path(str(workspace_dir or "")).expanduser()
        video = _find_explainer_video(ws) if workspace_dir else None
        video_url = _publish_video(video, demo_id) if video else ""
        exhibit_url = _read_manifest_url(ws) if workspace_dir else ""
        result["video_url"] = video_url
        result["exhibit_url"] = exhibit_url

        subject, text, html = compose_demo_built_email(
            title=title or demo_id, capability=capability, build_engine=build_engine,
            video_url=video_url, exhibit_url=exhibit_url, workspace_dir=str(workspace_dir),
            review_required=review_required,
        )

        from universal_agent.services.agentmail_service import AgentMailService
        from universal_agent.services.email_tags import ActionTag, KindTag
        mail = AgentMailService()
        await mail.startup()
        try:
            await mail.send_email(
                to=_recipient(), subject=subject, text=text, html=html,
                force_send=True, require_approval=False,
                action=ActionTag.FYI, kind=KindTag.PROACTIVE, source="demo_built_notifier",
            )
            result["emailed"] = True
        finally:
            await mail.shutdown()

        _register_artifact(demo_id=demo_id, title=title or demo_id, video_url=video_url,
                           exhibit_url=exhibit_url, build_engine=build_engine)
    except Exception:
        logger.warning("demo-built notification failed for %s", demo_id, exc_info=True)
    return result
