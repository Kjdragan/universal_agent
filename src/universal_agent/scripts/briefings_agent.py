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


def _build_objective(
    *,
    telemetry_json: str,
    wiki_content: str,
    hn_block: str,
    artifacts_dir: str,
    today: str,
    triage_block: str = "",
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
{triage_section}{hn_section}
Instructions:
- Summarize tasks completed, attempted, and failed.
- Include links/paths to any artifacts produced.
- MUST explicitly include a "Latest Proactive Knowledge Base Additions" section referencing the Nightly Wiki external paths and files if any are provided.
- Highlight any items requiring user decisions.
- If there are data quality warnings, mention them.{triage_instructions}{hn_instructions}

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

    objective = _build_objective(
        telemetry_json=telemetry_json,
        wiki_content=wiki_content,
        hn_block=hn_block,
        artifacts_dir=artifacts_dir,
        today=today,
        triage_block=triage_block,
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
