"""ideation_report.py — compose + deliver the morning ideation report.

Surfaces the held reflection proposals that Simone's autonomous ideation engine
generated overnight (source_kind='reflection', status='open', agent_ready=0) as a
rendered report with one-click **promote** / **dismiss** action links. The links
are HMAC-signed (``cron_artifact_notifier.sign_ideation_token``) and verified by
``gateway_server.ideation_action_get`` — promote flips a proposal into the live
dispatch queue, dismiss parks it. "Refine" is the scratchpad review toolbar that
every published page already carries.

This is the separate-from-CSI proactive-idea channel: CSI Track-B convergence is
its own pipeline; this is Simone's idle-time ideation. Delivered link-first
(scratchpad URL) with the cards inline in the email as a convenience.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html as _html
import logging
import os
import re
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from universal_agent import task_hub
from universal_agent.services.cron_artifact_notifier import sign_ideation_token
from universal_agent.services.scratch_publish import publish_html_to_scratch

logger = logging.getLogger(__name__)

_CENTRAL = ZoneInfo("America/Chicago")


def _action_base_url() -> str:
    """Operator-facing base URL the signed action links point at (the gateway)."""
    return (
        os.getenv("FRONTEND_URL", "")
        or os.getenv("UA_PUBLIC_BASE_URL", "")
        or os.getenv("UA_GATEWAY_BASE_URL", "")
        or "https://app.clearspringcg.com"
    ).strip().rstrip("/")


def get_held_proposals(conn: sqlite3.Connection, *, limit: int = 25) -> list[dict[str, Any]]:
    """Held reflection proposals awaiting an operator decision, newest first."""
    task_hub.ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT task_id, title, description, priority, labels_json, score, created_at
        FROM task_hub_items
        WHERE source_kind = 'reflection'
          AND status = 'open'
          AND agent_ready = 0
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


DEFAULT_STALE_PROPOSAL_SURFACE_HOURS = 72


def get_stale_proposals(
    conn: sqlite3.Connection,
    *,
    max_age_hours: int = DEFAULT_STALE_PROPOSAL_SURFACE_HOURS,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """OPEN reflection/brainstorm proposals older than ``max_age_hours``, OLDEST FIRST.

    Distinct from ``get_held_proposals`` (newest-first, ``agent_ready=0``,
    reflection-only): this surfaces every OPEN reflection OR brainstorm item that
    has sat without a verdict past the surface threshold, so the morning report
    can flag the backlog the operator hasn't actioned. No ``agent_ready`` filter
    (a promoted-but-never-dispatched proposal is just as stale) and no
    priority/label exclusion — the HARD GATE belongs to the reaper
    (``task_hub.reap_stale_proposals``), not the surface: the report must SHOW
    high-priority and human-only stale items, not hide them.
    """
    task_hub.ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT task_id, title, description, priority, labels_json, score, created_at
        FROM task_hub_items
        WHERE source_kind IN ('reflection', 'brainstorm')
          AND status = 'open'
        ORDER BY created_at ASC
        """,
    ).fetchall()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(max_age_hours))
    out: list[dict[str, Any]] = []
    for r in rows:
        created = task_hub._parse_iso(r["created_at"])
        if created is None:
            continue  # malformed — can't age it; don't surface
        if created < cutoff:
            out.append(dict(r))
        if len(out) >= limit:
            break
    return out


def _md_to_html(text: str) -> str:
    """Tiny, injection-safe markdown: escape, then ``**bold**`` + line breaks."""
    safe = _html.escape(text or "")
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"`([^`]+?)`", r"<code>\1</code>", safe)
    return safe.replace("\n", "<br>")


def _labels(labels_json: str | None) -> list[str]:
    import json
    try:
        val = json.loads(labels_json or "[]")
        return [str(x) for x in val] if isinstance(val, list) else []
    except Exception:
        return []


def _action_button(base: str, task_id: str, action: str, label: str, color: str) -> str:
    token = sign_ideation_token(task_id, action)
    if not base or not token:
        return ""  # no secret/base configured — omit rather than render a dead link
    url = f"{base}/api/v1/ideation/{_html.escape(task_id)}/action?a={action}&t={token}"
    return (
        f'<a href="{url}" style="display:inline-block;padding:8px 16px;margin:0 8px 0 0;'
        f'border-radius:6px;background:{color};color:#fff;font-weight:600;font-size:13px;'
        f'text-decoration:none;">{label}</a>'
    )


def _render_card(p: dict[str, Any], base: str) -> str:
    title = _html.escape(str(p.get("title") or "Untitled proposal"))
    desc = _md_to_html(str(p.get("description") or ""))
    labels = "".join(
        f'<span style="display:inline-block;background:#eef1f4;color:#57606a;border-radius:5px;'
        f'padding:2px 8px;font-size:11px;margin:0 6px 4px 0;">{_html.escape(l)}</span>'
        for l in _labels(p.get("labels_json"))
    )
    promote = _action_button(base, str(p.get("task_id")), "promote", "✓ Promote", "#1a7f37")
    dismiss = _action_button(base, str(p.get("task_id")), "dismiss", "✕ Dismiss", "#6e7781")
    buttons = (
        f'<div style="margin-top:14px;">{promote}{dismiss}</div>'
        if (promote or dismiss)
        else '<div style="margin-top:14px;color:#cf222e;font-size:12px;">'
        "(action links unavailable — promote/dismiss from the dashboard)</div>"
    )
    return (
        '<div style="border:1px solid #d0d7de;border-radius:10px;padding:18px 20px;margin:0 0 16px 0;'
        'background:#ffffff;">'
        f'<div style="font-size:16px;font-weight:680;color:#1f2328;margin-bottom:8px;">{title}</div>'
        f'<div style="margin-bottom:10px;">{labels}</div>'
        f'<div style="font-size:13.5px;line-height:1.6;color:#3b4148;">{desc}</div>'
        f'{buttons}'
        "</div>"
    )


def _render_cards(proposals: list[dict[str, Any]], base: str) -> str:
    return "".join(_render_card(p, base) for p in proposals)


def _render_stale_section(stale: list[dict[str, Any]], base: str) -> str:
    if not stale:
        return ""
    header = (
        '<div style="margin:32px 0 14px;padding:14px 16px;border:1px solid #d4a72c;'
        'border-radius:10px;background:#fff8c5;">'
        '<div style="font-size:15px;font-weight:700;color:#7d4e00;">'
        f"STALE PROPOSALS NEEDING VERDICT (>72h) — {len(stale)} awaiting your call</div>"
        '<div style="font-size:12px;color:#7d4e00;margin-top:4px;">'
        "Oldest first. Promote to dispatch, or Dismiss to park. The weekly reaper "
        "auto-parks these at 14 days (priority >= 2 and human-only items are always spared).</div>"
        "</div>"
    )
    return header + _render_cards(stale, base)


def _shell(inner: str, *, count: int, generated_ct: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Morning Ideation Report</title></head>"
        "<body style=\"margin:0;background:#f6f8fa;font-family:-apple-system,BlinkMacSystemFont,"
        "'Segoe UI',Roboto,sans-serif;color:#1f2328;\">"
        "<div style=\"max-width:720px;margin:0 auto;padding:28px 20px 60px;\">"
        "<div style=\"font-size:12px;letter-spacing:0.08em;text-transform:uppercase;color:#8250df;\">"
        "UA · Autonomous Ideation</div>"
        "<h1 style=\"font-size:23px;margin:6px 0 4px;\">Morning Ideation Report</h1>"
        f"<div style=\"color:#57606a;font-size:13px;margin-bottom:22px;\">{count} proposal(s) awaiting "
        f"your call · generated {generated_ct} · separate from the CSI pipeline</div>"
        f"{inner}"
        "<div style=\"color:#8c959f;font-size:12px;margin-top:24px;border-top:1px solid #d0d7de;"
        "padding-top:14px;\">Promote sends a proposal to the live dispatch queue; Dismiss parks it. "
        "To <strong>refine</strong> instead, use the highlight-and-comment toolbar on this page. "
        "Proposals you don't action stay here for the next report.</div>"
        "</div></body></html>"
    )


def render_report_html(
    proposals: list[dict[str, Any]],
    *,
    base: str,
    generated_ct: str,
    stale: list[dict[str, Any]] | None = None,
) -> str:
    stale = stale or []
    inner = _render_cards(proposals, base) + _render_stale_section(stale, base)
    return _shell(inner, count=len(proposals) + len(stale), generated_ct=generated_ct)


def _email_html(
    proposals: list[dict[str, Any]],
    *,
    base: str,
    generated_ct: str,
    scratch_url: str | None,
    stale: list[dict[str, Any]] | None = None,
) -> str:
    stale = stale or []
    link = (
        f'<p style="margin:0 0 18px;"><a href="{scratch_url}" style="color:#8250df;font-weight:600;">'
        f'Open the full report →</a></p>'
        if scratch_url
        else ""
    )
    inner = link + _render_cards(proposals, base) + _render_stale_section(stale, base)
    return _shell(inner, count=len(proposals) + len(stale), generated_ct=generated_ct)


async def deliver_ideation_report(conn: sqlite3.Connection, mail_service: Any, recipient: str) -> dict[str, Any]:
    """Compose + deliver the morning ideation report. Skips delivery when empty."""
    proposals = get_held_proposals(conn)
    stale = get_stale_proposals(conn, max_age_hours=DEFAULT_STALE_PROPOSAL_SURFACE_HOURS)
    now_ct = datetime.now(timezone.utc).astimezone(_CENTRAL)
    generated_ct = now_ct.strftime("%a %b %-d, %-I:%M %p %Z")

    if not proposals and not stale:
        logger.info("ideation_report: no held or stale proposals — nothing to send")
        return {"status": "no_proposals", "count": 0, "email_sent": False, "scratch_url": None}

    total = len(proposals) + len(stale)
    base = _action_base_url()
    html_doc = render_report_html(proposals, base=base, generated_ct=generated_ct, stale=stale)

    scratch_url = publish_html_to_scratch(
        html_doc,
        slug=now_ct.strftime("ideation-%Y%m%d"),
        filename="report.html",
        title="Morning Ideation Report",
        description=f"{total} proposal(s) awaiting review",
    )

    subject = f"[FYI/IDEATION] Morning Ideation Report — {total} proposal(s)"
    lines = [
        f"- {p.get('title')}\n  {(' '.join(str(p.get('description') or '').split()))[:240]}"
        for p in proposals
    ] + [
        f"- [STALE >72h] {s.get('title')}\n  {(' '.join(str(s.get('description') or '').split()))[:240]}"
        for s in stale
    ]
    text = (
        f"{total} proposal(s) awaiting your call ({len(proposals)} new, {len(stale)} stale >72h) "
        f"(generated {generated_ct}).\n"
        + ("Full report: " + scratch_url + "\n\n" if scratch_url else "\n")
        + "\n\n".join(lines)
    )
    email_html = _email_html(
        proposals, base=base, generated_ct=generated_ct, scratch_url=scratch_url, stale=stale,
    )

    email_sent = False
    message_id = ""
    try:
        result = await mail_service.send_email(
            to=recipient,
            subject=subject,
            text=text,
            html=email_html,
            source="morning_ideation_report cron",
        )
        email_sent = bool(result and result.get("status") not in {"error", "failed"})
        message_id = str((result or {}).get("message_id") or "")
    except Exception as exc:  # noqa: BLE001 — degrade gracefully; report still on scratch
        logger.error("ideation_report: email send failed: %s", exc, exc_info=True)

    logger.info(
        "ideation_report delivered: proposals=%d stale=%d email_sent=%s scratch=%s",
        len(proposals), len(stale), email_sent, scratch_url,
    )
    return {
        "status": "delivered",
        "count": total,
        "email_sent": email_sent,
        "email_message_id": message_id,
        "scratch_url": scratch_url,
    }
