"""Daily morning briefing entrypoint (cron `30 6 * * *`).

Fetches autonomous-activity telemetry from the gateway, optionally
includes the nightly wiki output, optionally includes a Hacker News
context block (Phase 2 Lane A — see
`docs/integrations/hackernews_phase2_plan.md`), and dispatches a VP
mission to write the briefing markdown.

The Phase 2 HN block is gated by `UA_HACKERNEWS_BRIEFING_BLOCK_ENABLED`.
Set to `0` to disable without redeploying. Any helper exception is
swallowed — the briefing must never break because the HN block didn't.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import sys

import httpx

from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.hackernews_briefing_context import (
    build_briefing_block,
)
from universal_agent.services.hackernews_snapshot_service import (
    DEFAULT_TOPICS,
    _load_watchlist,
)
from universal_agent.services.watchdog_briefing_context import (
    build_briefing_block as build_watchdog_briefing_block,
)

logger = logging.getLogger(__name__)


def _get_hn_block_or_empty(watchlist: list[str]) -> str:
    """Return the HN briefing block, or "" on kill switch / failure.

    Kill switch: `UA_HACKERNEWS_BRIEFING_BLOCK_ENABLED=0` disables the
    block entirely (briefing proceeds without HN context). Any other
    value (including unset) means enabled.
    """
    if os.getenv("UA_HACKERNEWS_BRIEFING_BLOCK_ENABLED", "1") == "0":
        logger.info("HN briefing block disabled via UA_HACKERNEWS_BRIEFING_BLOCK_ENABLED=0")
        return ""
    try:
        return build_briefing_block(watchlist)
    except Exception as exc:  # noqa: BLE001 — never let HN break the briefing
        logger.warning("HN briefing block helper crashed: %s", exc)
        return ""


def _build_triage_block(*, pending: int, top: list[dict], oldest_days: int | None) -> str:
    """Render the markdown block for the morning briefing.

    Pulled out for unit-testing; called only when `pending > 0`.
    """
    age = f" (oldest first-seen {oldest_days} day{'s' if oldest_days != 1 else ''} ago)" if oldest_days is not None else ""
    lines = [
        "## Claude Code Demo Triage — Operator Decision Needed",
        "",
        f"- Pending tier-3 candidates: **{pending}**{age}",
    ]
    if top:
        lines.append("- Top by ranking score:")
        for cand in top:
            url = str(cand.get("post_url") or "").strip()
            text = str(cand.get("post_text") or cand.get("summary") or "").strip().replace("\n", " ")
            if len(text) > 140:
                text = text[:137] + "..."
            score = cand.get("ranking_score")
            score_str = f" (score {score:.2f})" if isinstance(score, (int, float)) else ""
            label = text or url or "(no text)"
            lines.append(f"  - {label}{score_str} — {url}" if url else f"  - {label}{score_str}")
    lines += [
        "",
        "Drawer: open the Claude Code Intel dashboard tab and click **Demo Triage** in the right panel. "
        "Approval is the only path from a tier-3 candidate to a `cody_scaffold_request` Task Hub row "
        "(auto-queue path was removed in 2026-05-09).",
    ]
    return "\n".join(lines)


def _get_triage_block_or_empty() -> str:
    """Return the demo-triage briefing block, or "" when nothing pending / kill switch / failure.

    Kill switch: `UA_TRIAGE_BRIEFING_BLOCK_ENABLED=0` disables the block
    entirely. Best-effort: any import or DB error returns "" so the
    briefing can never break because triage isn't ready.
    """
    if os.getenv("UA_TRIAGE_BRIEFING_BLOCK_ENABLED", "1") == "0":
        logger.info("Triage briefing block disabled via UA_TRIAGE_BRIEFING_BLOCK_ENABLED=0")
        return ""
    try:
        from datetime import datetime as _dt, timezone as _tz

        from universal_agent.services import csi_demo_triage as _triage

        conn = _triage.open_db()
        try:
            counts = _triage.get_counts(conn=conn)
            pending = int(counts.get("pending") or 0)
            if pending <= 0:
                return ""
            top = [c.to_dict() for c in _triage.get_top_recommendations(conn=conn, n=3)]
            pending_candidates = _triage.list_candidates(conn=conn, state=_triage.STATE_PENDING)
        finally:
            conn.close()

        oldest_days: int | None = None
        oldest_iso = ""
        for cand in pending_candidates:
            iso = str(cand.first_seen_at or "").strip()
            if iso and (not oldest_iso or iso < oldest_iso):
                oldest_iso = iso
        if oldest_iso:
            try:
                parsed = _dt.fromisoformat(oldest_iso.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=_tz.utc)
                delta = _dt.now(_tz.utc) - parsed
                oldest_days = max(0, delta.days)
            except Exception:  # noqa: BLE001
                oldest_days = None

        return _build_triage_block(pending=pending, top=top, oldest_days=oldest_days)
    except Exception as exc:  # noqa: BLE001 — never let triage break the briefing
        logger.warning("Triage briefing block helper crashed: %s", exc)
        return ""


def _get_watchdog_block_or_empty() -> str:
    """Return the watchdog briefing block, or "" on kill switch / no findings.

    Kill switch: `UA_BRIEFING_WATCHDOG_BLOCK_ENABLED=0` disables. Any helper
    failure is swallowed — the briefing must never break because the
    watchdog query did. P6 (2026-05-20): closes the integration gap where
    Simone's morning digest had zero awareness of active watchdog findings.
    """
    try:
        return build_watchdog_briefing_block()
    except Exception as exc:  # noqa: BLE001 — never let watchdog break the briefing
        logger.warning("Watchdog briefing block helper crashed: %s", exc)
        return ""


def _build_atlas_briefs_block(briefs: list[dict[str, str]]) -> str:
    """Render the markdown block for the morning briefing.

    Pulled out for unit-testing; called only when `briefs` is non-empty.
    """
    lines = [
        "## ATLAS Insight Briefs — Awaiting Operator Triage",
        "",
        f"- New since last briefing: **{len(briefs)}**",
        "",
    ]
    for brief in briefs:
        title = (brief.get("title") or "").replace("ATLAS insight brief: ", "").strip() or "(untitled)"
        summary = (brief.get("summary") or "").strip()
        if summary:
            lines.append(f"- **{title}** — {summary}")
        else:
            lines.append(f"- **{title}**")
    return "\n".join(lines)


def _get_atlas_briefs_block_or_empty(
    *,
    max_age_hours: int = 168,
    limit: int = 5,
) -> str:
    """Return the Atlas insight-briefs block, or "" on kill switch / nothing surfaceable.

    Surfaces the top N=`limit` ATLAS insight briefs that are:
      - artifact_type='insight_brief_task'
      - status='candidate' (not yet acted on by Simone/operator)
      - delivery_state='not_surfaced' (not already mentioned in a prior briefing)
      - created within the last `max_age_hours` so a long outage doesn't dump
        ancient briefs into the briefing once the helper comes back online

    Window defaults to 7 days (168h). The convergence cron generates 100-200
    briefs/day at steady state and we only surface the freshest `limit=5`,
    so the daily briefing always carries the top 5 *newest* briefs regardless
    of when the previous briefing actually ran. A shorter window (e.g. 36h)
    would leave the briefing empty whenever the convergence cron stalled
    overnight or the briefing crashed — undesirable for a daily morning surface.

    Side effect: after rendering, each surfaced brief is transitioned to
    `delivery_state='digest_queued'` + `surfaced_at=now()` so the next
    morning won't re-list it. This is per-brief, NOT per-briefing-run, so
    a briefing that crashes mid-render won't silently consume briefs that
    never reached the operator.

    Kill switch: `UA_ATLAS_BRIEFING_BLOCK_ENABLED=0` disables the block
    entirely. Best-effort: any DB or import error returns "" so the
    briefing can never break because Atlas isn't ready.
    """
    if os.getenv("UA_ATLAS_BRIEFING_BLOCK_ENABLED", "1") == "0":
        logger.info("Atlas briefing block disabled via UA_ATLAS_BRIEFING_BLOCK_ENABLED=0")
        return ""
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
        from universal_agent.services import proactive_artifacts as _pa

        cutoff = (_dt.now(_tz.utc) - _td(hours=max_age_hours)).isoformat()
        with connect_runtime_db(get_activity_db_path()) as conn:
            import sqlite3 as _sqlite3
            conn.row_factory = _sqlite3.Row
            _pa.ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT artifact_id, title, summary, created_at
                FROM proactive_artifacts
                WHERE artifact_type = ?
                  AND status = ?
                  AND delivery_state = ?
                  AND created_at > ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (
                    "insight_brief_task",
                    _pa.ARTIFACT_STATUS_CANDIDATE,
                    _pa.DELIVERY_NOT_SURFACED,
                    cutoff,
                    int(limit),
                ),
            ).fetchall()
            if not rows:
                return ""

            briefs = [
                {
                    "artifact_id": r["artifact_id"],
                    "title": r["title"] or "",
                    "summary": r["summary"] or "",
                }
                for r in rows
            ]
            block = _build_atlas_briefs_block(briefs)

            # Mark each surfaced brief so it doesn't appear in tomorrow's
            # briefing. Per-brief, so a render crash doesn't burn briefs.
            for brief in briefs:
                try:
                    _pa.update_artifact_state(
                        conn,
                        artifact_id=brief["artifact_id"],
                        delivery_state=_pa.DELIVERY_DIGEST_QUEUED,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Atlas briefing: failed to flag artifact %s as digest_queued",
                        brief["artifact_id"],
                    )
            return block
    except Exception as exc:  # noqa: BLE001 — never let Atlas break the briefing
        logger.warning("Atlas briefs block helper crashed: %s", exc)
        return ""


def _build_pending_artifacts_block(artifacts: list[dict]) -> str:
    """Render the 'Pending your review' block for cron-disclosure artifacts.

    Pulled out for unit-testing; called only when ``artifacts`` is
    non-empty. Each artifact shows its title, age (days since creation),
    and an acknowledge / dashboard URL hint so the operator knows where
    to act.
    """
    lines = [
        "## Pending Your Review — Cron-Produced Artifacts",
        "",
        f"- Unacknowledged artifacts: **{len(artifacts)}**",
        "",
    ]
    for art in artifacts:
        title = str(art.get("title") or "").strip() or "(untitled)"
        artifact_id = str(art.get("artifact_id") or "").strip()
        summary = str(art.get("summary") or "").strip()
        age_part = ""
        created = str(art.get("created_at") or "").strip()
        if created:
            try:
                parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                delta = datetime.now(timezone.utc) - parsed
                age_days = max(0, delta.days)
                age_part = f" — {age_days}d old" if age_days else " — today"
            except Exception:  # noqa: BLE001
                age_part = ""
        if summary:
            summary = summary[:200] + ("…" if len(summary) > 200 else "")
            lines.append(f"- **{title}**{age_part} — {summary}")
        else:
            lines.append(f"- **{title}**{age_part}")
        if artifact_id:
            lines.append(f"  - Dashboard: `?artifact={artifact_id}`")
    return "\n".join(lines)


def _get_pending_artifacts_block_or_empty(*, limit: int = 10) -> str:
    """Return the pending-artifact-review block, or "" on kill switch / nothing pending.

    Surfaces cron-produced artifacts (``artifact_type='cron_run_output'``)
    that have been emailed but not yet acknowledged. Stays visible in the
    morning briefing until the operator clicks the ack link or hits the
    dashboard button, even after the Day-7 reminder loop has stopped
    sending emails.

    Kill switch: ``UA_PENDING_ARTIFACTS_BRIEFING_BLOCK_ENABLED=0``.
    """
    if os.getenv("UA_PENDING_ARTIFACTS_BRIEFING_BLOCK_ENABLED", "1") == "0":
        logger.info(
            "Pending-artifacts briefing block disabled via "
            "UA_PENDING_ARTIFACTS_BRIEFING_BLOCK_ENABLED=0"
        )
        return ""
    try:
        from universal_agent.durable.db import (
            connect_runtime_db,
            get_activity_db_path,
        )
        from universal_agent.services import proactive_artifacts as _pa

        conn = connect_runtime_db(get_activity_db_path())
        try:
            _pa.ensure_schema(conn)
            # Surface artifacts that have been emailed (so the operator
            # has at least one initial touch) and are not yet acked.
            rows = conn.execute(
                """
                SELECT artifact_id, title, summary, created_at, updated_at, metadata_json
                FROM proactive_artifacts
                WHERE artifact_type = 'cron_run_output'
                  AND status IN ('produced', 'surfaced', 'candidate')
                  AND delivery_state = 'emailed'
                  AND accepted_at = ''
                  AND rejected_at = ''
                  AND archived_at = ''
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        finally:
            conn.close()
        artifacts = [dict(r) for r in rows]
        if not artifacts:
            return ""
        return _build_pending_artifacts_block(artifacts)
    except Exception as exc:  # noqa: BLE001 — never let pending-artifacts break the briefing
        logger.warning("Pending-artifacts briefing block helper crashed: %s", exc)
        return ""


def _build_objective(
    *,
    telemetry_json: str,
    wiki_content: str,
    hn_block: str,
    artifacts_dir: str,
    today: str,
    triage_block: str = "",
    watchdog_block: str = "",
    atlas_block: str = "",
    pending_artifacts_block: str = "",
) -> str:
    """Assemble the briefing prompt from the context sources.

    The HN section + Atlas tool-use instructions are only included when
    `hn_block` is non-empty. The triage section is only included when
    `triage_block` is non-empty. When both are empty, the prompt looks
    identical to the pre-Phase-2 version (no spurious mention of
    webReader, no triage line, etc.).
    """
    triage_section = ""
    triage_instructions = ""
    if triage_block:
        triage_section = f"\n\n{triage_block}\n"
        triage_instructions = (
            "\n- Surface the demo-triage queue depth at the top of the briefing if non-empty. "
            "The operator approval drawer is the only path from a tier-3 candidate to Cody — "
            "if pending count is growing, call it out as an action item for Kevin."
        )

    # P6 (2026-05-20): watchdog block. The watchdog parks proactive_health:*
    # Task Hub rows for every critical finding and escalates persistent warns
    # after 3 ticks. Without surfacing this in the briefing the operator can
    # have hours-old critical alerts that never reach the morning report.
    watchdog_section = ""
    watchdog_instructions = ""
    if watchdog_block:
        watchdog_section = f"\n\n{watchdog_block}\n"
        watchdog_instructions = (
            "\n- **Lead the briefing with a Watchdog Status section.** Critical findings ARE the "
            "operator's top priority for the day — surface every CRITICAL row from the watchdog "
            "block prominently with its runbook command. Warn-tier findings get a brief mention. "
            "The Task Hub backlog (`proactive_health:*` rows) is the actionable list of unresolved "
            "issues — quote the task_ids so the operator can triage from the dashboard. If the "
            "watchdog reports `overall: ok` and the backlog is empty, say so in one line and move on."
        )

    atlas_section = ""
    atlas_instructions = ""
    if atlas_block:
        atlas_section = f"\n\n{atlas_block}\n"
        atlas_instructions = (
            "\n- **Include an 'ATLAS Insight Briefs' section.** These are non-obvious patterns "
            "ATLAS detected from CSI/YouTube signals and queued as proactive insight tasks. "
            "List each brief by title with its one-line summary. After the list, in 2-3 sentences, "
            "name any cross-cutting theme that emerges across the briefs (if any) and recommend "
            "whether the operator should (a) promote one or two to active work, (b) deepen the "
            "synthesis with another ATLAS pass, or (c) close out the queue if the briefs are "
            "drifting from current strategic priorities. Keep the recommendation concrete — the "
            "operator wants to decide what to do, not read more abstract trend prose."
        )

    pending_artifacts_section = ""
    pending_artifacts_instructions = ""
    if pending_artifacts_block:
        pending_artifacts_section = f"\n\n{pending_artifacts_block}\n"
        pending_artifacts_instructions = (
            "\n- **Include a 'Pending Your Review' section.** List every "
            "cron-produced artifact that has been emailed but not yet "
            "acknowledged. For each artifact say one sentence on what it "
            "is and why it might still be worth opening. These items stay "
            "visible until the operator clicks the email Acknowledge link "
            "or the dashboard ack button — surface them every morning so "
            "they don't quietly age out of attention."
        )

    hn_section = ""
    hn_instructions = ""
    if hn_block:
        hn_section = f"\n\n{hn_block}\n"
        hn_instructions = (
            "\n- Include a 'Hacker News This Week' section. **Read the actual content "
            "(comments, post bodies, Algolia mentions)** — do NOT just paraphrase titles. "
            "For items where the comments suggest the article body would clarify whether "
            "the substance matters to active work, **call the `webReader` tool with the "
            "article URL** (the ZAI-native MCP) to fetch the article body before deciding "
            "whether to surface it. Be selective — typically 2-5 of the 10 candidates "
            "warrant a fetch. If a comment thread teases a follow-up topic worth surfacing, "
            "**call `webSearchPrime`** for that follow-up — but only when warranted, not "
            "as default behavior. Surface 1-3 items where the substance aligns with active "
            "work or open questions; quote a comment or article excerpt where it illuminates "
            "'why this matters.' If nothing in the HN block lands on relevant ground, say so "
            "in one line and move on. 'Nothing relevant to active work today' is a valid answer."
        )

    return f"""Generate the daily autonomous operations briefing for the last 24 hours.
Focus only on work executed without direct user prompting (scheduled/proactive flows).

Here is the raw telemetry data:
```json
{telemetry_json}
```

Here is the external Nightly Wiki Proactive Generation output (if any):
```markdown
{wiki_content}
```
{watchdog_section}{triage_section}{atlas_section}{pending_artifacts_section}{hn_section}
Instructions:
- Summarize tasks completed, attempted, and failed.
- Include links/paths to any artifacts produced.
- MUST explicitly include a "Latest Proactive Knowledge Base Additions" section referencing the Nightly Wiki external paths and files if any are provided.
- Highlight any items requiring user decisions.
- If there are data quality warnings, mention them.{watchdog_instructions}{triage_instructions}{atlas_instructions}{pending_artifacts_instructions}{hn_instructions}

Write a concise markdown report to '{artifacts_dir}/autonomous-briefings/{today}/DAILY_BRIEFING.md'.
Make sure to provide a short completion message suitable for a dashboard notification.
"""


async def main():
    # 1. Initialize runtime secrets via Infisical (allowing dotenv fallback)
    initialize_runtime_secrets(profile="local_workstation")
    logging.basicConfig(level=logging.INFO)

    port = os.getenv("UA_GATEWAY_PORT", "8008")
    gateway_url = f"http://127.0.0.1:{port}/api/v1/ops/telemetry/briefing"

    api_key = os.getenv("UA_OPS_TOKEN", "")
    if not api_key:
        logger.error("UA_OPS_TOKEN is required to fetch telemetry.")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {api_key}"}

    logger.info(f"Fetching telemetry from gateway at {gateway_url}...")
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            resp = await client.get(gateway_url)
            resp.raise_for_status()
            data = resp.json()
            briefing_data = data.get("briefing_data", {})
    except Exception as exc:
        logger.error(f"Failed to fetch telemetry from gateway: {exc}")
        sys.exit(1)

    from universal_agent.artifacts import resolve_artifacts_dir
    artifacts_dir = str(resolve_artifacts_dir())
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    telemetry_json = json.dumps(briefing_data, indent=2)

    wiki_content = ""
    wiki_file = os.path.join(artifacts_dir, "nightly_wikis", f"nightly_wiki_{today}.md")
    if os.path.exists(wiki_file):
        try:
            with open(wiki_file) as f:
                wiki_content = f.read()
        except Exception as exc:
            logger.warning(f"Failed to read wiki content: {exc}")

    # Phase 2 Lane A — HN evidence block. Best-effort; never blocks the briefing.
    watchlist = _load_watchlist() if callable(_load_watchlist) else list(DEFAULT_TOPICS)
    hn_block = _get_hn_block_or_empty(watchlist)
    if hn_block:
        logger.info("HN briefing block included (~%d chars)", len(hn_block))
    else:
        logger.info("HN briefing block omitted (kill switch / no snapshot / no candidates)")

    # ClaudeDevs Intel v2 — pending operator-approval count. Best-effort; never blocks.
    triage_block = _get_triage_block_or_empty()
    if triage_block:
        logger.info("Triage briefing block included (~%d chars)", len(triage_block))
    else:
        logger.info("Triage briefing block omitted (kill switch / nothing pending)")

    # P6 (2026-05-20): watchdog block. Surfaces current proactive_health
    # findings + task_hub backlog so the briefing reflects what the watchdog
    # has been detecting all morning.
    watchdog_block = _get_watchdog_block_or_empty()
    if watchdog_block:
        logger.info("Watchdog briefing block included (~%d chars)", len(watchdog_block))
    else:
        logger.info("Watchdog briefing block omitted (kill switch / healthy state / no findings)")

    # ATLAS insight briefs (2026-05-22): surface the parked insight_brief_task
    # artifacts so the operator can decide whether to promote, deepen, or close
    # them. Marks each brief delivery_state='digest_queued' so it isn't re-listed
    # tomorrow morning.
    atlas_block = _get_atlas_briefs_block_or_empty()
    if atlas_block:
        logger.info("Atlas briefs block included (~%d chars)", len(atlas_block))
    else:
        logger.info("Atlas briefs block omitted (kill switch / no new briefs)")

    # Cron-disclosure pending artifacts (2026-05-24): surfaces unacknowledged
    # cron-produced artifacts so the operator can decide whether to act.
    # Stays visible until acked even after the reminder cadence has stopped.
    pending_artifacts_block = _get_pending_artifacts_block_or_empty()
    if pending_artifacts_block:
        logger.info(
            "Pending-artifacts block included (~%d chars)",
            len(pending_artifacts_block),
        )
    else:
        logger.info(
            "Pending-artifacts block omitted (kill switch / nothing pending)"
        )

    objective = _build_objective(
        telemetry_json=telemetry_json,
        wiki_content=wiki_content,
        hn_block=hn_block,
        artifacts_dir=artifacts_dir,
        today=today,
        triage_block=triage_block,
        watchdog_block=watchdog_block,
        atlas_block=atlas_block,
        pending_artifacts_block=pending_artifacts_block,
    )

    logger.info("Dispatching mission to vp.general.primary...")

    from universal_agent.tools.vp_orchestration import dispatch_vp_mission

    try:
        await dispatch_vp_mission(
            objective=objective,
            mission_type="briefing",
            idempotency_key=f"briefing-{today}",
        )
    except RuntimeError as exc:
        logger.error(f"Dispatch failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
