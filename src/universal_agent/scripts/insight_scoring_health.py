"""Weekly health-check on the hourly-insight scoring pipeline.

Cron: ``0 8 * * 0`` (Sunday 8 AM Houston). Reads the last 7 days of
``proactive_brief_scoring_log`` rows, computes delivery / sub-threshold /
honorable-mention rates, and emails Kevin a one-paragraph LLM verdict
("scoring seems calibrated" / "tighten confidence floor" / etc.).

Best-effort — failures fall back to log dumps rather than crashing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import sys
from typing import Any

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services import proactive_scoring_log as _scoring

logger = logging.getLogger(__name__)


def _recipient() -> str:
    return (os.getenv("UA_INSIGHT_HOURLY_EMAIL_RECIPIENT") or "kevinjdragan@gmail.com").strip()


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate scoring-log rows into the report summary."""
    generated = len(rows)
    delivered_hourly = sum(1 for r in rows if int(r.get("delivered_hourly") or 0))
    delivered_briefing = sum(1 for r in rows if int(r.get("delivered_briefing") or 0))
    sub_threshold = sum(
        1
        for r in rows
        if int(r.get("delivered_hourly") or 0)
        and str(r.get("delivery_slot") or "") == _scoring.SLOT_SUB_THRESHOLD_FILLER
    )
    delivered_total = delivered_hourly or 1  # avoid div-by-zero
    sub_threshold_rate = sub_threshold / delivered_total

    honorable = [
        r
        for r in rows
        if str(r.get("delivery_slot") or "") == _scoring.SLOT_HONORABLE_MENTION
    ]
    honorable_with_rating = [r for r in honorable if r.get("operator_rating") is not None]
    algo_with_rating = [
        r
        for r in rows
        if int(r.get("delivered_hourly") or 0)
        and str(r.get("delivery_slot") or "") in (_scoring.SLOT_INSIGHT_1, _scoring.SLOT_INSIGHT_2)
        and r.get("operator_rating") is not None
    ]

    def _avg_rating(group: list[dict[str, Any]]) -> float | None:
        if not group:
            return None
        return sum(float(r.get("operator_rating") or 0) for r in group) / len(group)

    return {
        "generated": generated,
        "delivered_hourly": delivered_hourly,
        "delivered_briefing": delivered_briefing,
        "sub_threshold_deliveries": sub_threshold,
        "sub_threshold_rate": round(sub_threshold_rate, 3),
        "honorable_mention_count": len(honorable),
        "honorable_mention_avg_rating": _avg_rating(honorable_with_rating),
        "algorithm_pick_avg_rating": _avg_rating(algo_with_rating),
    }


async def _llm_verdict(summary: dict[str, Any]) -> str:
    """One-paragraph LLM-generated calibration verdict.

    Falls back to a deterministic rule-based verdict if the LLM call fails so
    the email always carries some actionable guidance.
    """
    try:
        from universal_agent.services.llm_classifier import _call_llm

        system = (
            "You are an analyst tuning a proactive intelligence delivery system. "
            "Given the weekly stats, produce ONE concise paragraph (3-5 sentences) "
            "that says whether the scoring looks calibrated or mis-firing, and if "
            "mis-firing, propose ONE specific tuning move (e.g. 'raise the "
            "confidence floor from 0.7 to 0.8' or 'lower the channel-breadth "
            "weight'). Do not return JSON or markdown — plain text only."
        )
        return (await _call_llm(system=system, user=json.dumps(summary), max_tokens=400)).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM verdict generation failed: %s", exc)

    rate = float(summary.get("sub_threshold_rate") or 0.0)
    if rate > 0.30:
        return (
            f"Sub-threshold delivery rate is {rate:.0%} — above the 30% target. "
            "Consider raising the confidence floor from 0.7 to 0.75 or "
            "requiring 4 supporting channels instead of 3, to reduce filler picks."
        )
    return (
        "Scoring appears calibrated: sub-threshold deliveries are below the 30% "
        "target and the algorithm picks are flowing through. No tuning recommended this week."
    )


def _render_report(*, summary: dict[str, Any], verdict: str, week_end: str) -> tuple[str, str]:
    """Return (subject, text_body) for the weekly health report email."""
    subject = f"[Weekly] Insight scoring health — week ending {week_end}"
    lines = [
        f"Weekly insight-scoring health report — week ending {week_end} (Houston).",
        "",
        "## Counts (last 7 days)",
        f"- Briefs scored:                  {summary['generated']}",
        f"- Delivered via hourly email:     {summary['delivered_hourly']}",
        f"- Delivered via briefing recap:   {summary['delivered_briefing']}",
        f"- Sub-threshold filler deliveries: {summary['sub_threshold_deliveries']}",
        f"- Sub-threshold delivery rate:    {summary['sub_threshold_rate'] * 100:.1f}%  (target <30%)",
        "",
        "## Operator feedback (when rated)",
    ]
    algo_avg = summary.get("algorithm_pick_avg_rating")
    hm_avg = summary.get("honorable_mention_avg_rating")
    if algo_avg is not None:
        lines.append(f"- Algorithm picks avg rating:     {algo_avg:.2f}")
    else:
        lines.append("- Algorithm picks avg rating:     (no rated deliveries this week)")
    if hm_avg is not None:
        lines.append(f"- Honorable mention avg rating:   {hm_avg:.2f}")
    else:
        lines.append("- Honorable mention avg rating:   (no rated honorable picks this week)")
    lines.extend(
        [
            "",
            "## Verdict",
            verdict,
            "",
        ]
    )
    return subject, "\n".join(lines)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    initialize_runtime_secrets(profile="local_workstation")

    conn = connect_runtime_db(get_activity_db_path())
    try:
        _scoring.ensure_schema(conn)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        rows = conn.execute(
            "SELECT * FROM proactive_brief_scoring_log WHERE logged_at >= ? ORDER BY logged_at DESC",
            (cutoff,),
        ).fetchall()
        row_dicts = [dict(r) for r in rows]
    finally:
        conn.close()

    summary = _summarize_rows(row_dicts)
    verdict = await _llm_verdict(summary)
    week_end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject, body = _render_report(summary=summary, verdict=verdict, week_end=week_end)

    try:
        from universal_agent.services.agentmail_service import AgentMailService
        from universal_agent.services.email_tags import ActionTag, KindTag

        mail = AgentMailService()
        await mail.startup()
        try:
            if not getattr(mail, "_started", False):
                logger.error("AgentMail not started — dumping report to log.")
                logger.info("Subject: %s", subject)
                logger.info("Body:\n%s", body)
                return
            await mail.send_email(
                to=_recipient(),
                subject=subject,
                text=body,
                force_send=True,
                require_approval=False,
                action=ActionTag.FYI,
                kind=KindTag.DIGEST,
                source="insight_scoring_health cron",
            )
            logger.info("insight_scoring_health: report sent.")
        finally:
            try:
                await mail.shutdown()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send weekly health report: %s", exc)
        logger.info("Subject: %s", subject)
        logger.info("Body:\n%s", body)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        logger.exception("insight_scoring_health failed: %s", exc)
        sys.exit(1)
