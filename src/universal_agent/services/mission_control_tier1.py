"""Mission Control Intelligence — Tier-1 narrative-card discovery.

Phase 2 deliverable. Defines:

  - `collect_tier1_evidence()` — bounded, NO-TRUNCATION evidence
    collection from task_hub, activity_events, workspace artifacts,
    proactive history, tier-0 tile state, and the existing CSI digest
    DB. The full text of every relevant item is included; storage is
    bounded at the retention boundary (Phase 8), not at collection.

  - `evidence_signature()` — cheap, deterministic hash of the evidence
    bundle's stable identifying parts. Used by the sweeper to skip
    expensive LLM calls when nothing operationally meaningful has moved.

  - `discover_tier1_cards()` — calls the dedicated `glm-4.7` lane with
    the evidence bundle plus the prior-pass live cards' subject_ids,
    parses the JSON response into `CardUpsert` payloads, returns the
    new card set.

  - `apply_tier1_discovery()` — orchestrates: persist new/updated cards
    via `upsert_card`, retire any prior live card whose subject_id was
    not re-emitted, and return a small action summary for logging.

The sweeper's `_run_tier1` (in mission_control_intelligence_sweeper)
calls these in order, gated by bundle-signature change.

See docs/02_Subsystems/Mission_Control_Intelligence_System.md §2 + §3.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from universal_agent.services.mission_control_cards import (
    CARD_STATE_LIVE,
    SEVERITY_CRITICAL,
    SEVERITY_INFORMATIONAL,
    SEVERITY_SUCCESS,
    SEVERITY_WARNING,
    SEVERITY_WATCHING,
    SUBJECT_ARTIFACT,
    SUBJECT_FAILURE_PATTERN,
    SUBJECT_IDEA,
    SUBJECT_INFRASTRUCTURE,
    SUBJECT_MISSION,
    SUBJECT_RUN,
    SUBJECT_TASK,
    CardUpsert,
    list_live_cards,
    make_card_id,
    retire_card,
    upsert_card,
)

logger = logging.getLogger(__name__)


VALID_SUBJECT_KINDS = {
    SUBJECT_TASK,
    SUBJECT_RUN,
    SUBJECT_MISSION,
    SUBJECT_ARTIFACT,
    SUBJECT_FAILURE_PATTERN,
    SUBJECT_INFRASTRUCTURE,
    SUBJECT_IDEA,
}

VALID_SEVERITIES = {
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    SEVERITY_WATCHING,
    SEVERITY_INFORMATIONAL,
    SEVERITY_SUCCESS,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Evidence collection (NO TRUNCATION) ────────────────────────────────


def collect_tier1_evidence(
    activity_conn: sqlite3.Connection,
    mc_conn: sqlite3.Connection,
    *,
    task_limit: int = 30,
    completed_task_limit: int = 15,
    event_limit: int = 60,
) -> dict[str, Any]:
    """Bounded but UNTRUNCATED evidence bundle for tier-1 discovery.

    Per the design contract (§2.4): we limit COUNTS of items in the
    bundle but never shorten individual text fields. The LLM gets full
    descriptions, full event payloads, full prior synthesis history.
    """
    evidence: dict[str, Any] = {
        "generated_at_utc": _utc_now_iso(),
        "source": "mission_control_tier1_evidence",
    }

    # Active + recently-completed Task Hub items (full text)
    try:
        rows = activity_conn.execute(
            f"""
            SELECT *
            FROM task_hub_items
            WHERE status IN ('open','queued','pending','in_progress','blocked','review','pending_review','delegated')
            ORDER BY priority DESC, updated_at DESC
            LIMIT {int(task_limit)}
            """
        ).fetchall()
        evidence["active_or_attention_tasks"] = [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError as exc:
        evidence["active_or_attention_tasks"] = []
        evidence["task_hub_unavailable"] = str(exc)

    try:
        rows = activity_conn.execute(
            f"""
            SELECT *
            FROM task_hub_items
            WHERE status IN ('completed','done')
              AND updated_at > datetime('now','-7 days')
            ORDER BY updated_at DESC
            LIMIT {int(completed_task_limit)}
            """
        ).fetchall()
        evidence["recent_completed_tasks"] = [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        evidence["recent_completed_tasks"] = []

    # Operator-relevant activity events (full text, full metadata)
    try:
        rows = activity_conn.execute(
            f"""
            SELECT *
            FROM activity_events
            WHERE (
                LOWER(COALESCE(severity, '')) IN ('warning','error','critical')
                OR COALESCE(requires_action, 0) = 1
                OR LOWER(COALESCE(source_domain, '')) NOT IN ('heartbeat')
            )
            ORDER BY created_at DESC, id DESC
            LIMIT {int(event_limit)}
            """
        ).fetchall()
        evidence["recent_events"] = [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        evidence["recent_events"] = []

    # Tier-0 tile state snapshot (operator's at-a-glance signal feeds
    # tier-1 narrative — esp. red tiles need card-level explanations)
    try:
        rows = mc_conn.execute(
            "SELECT * FROM mission_control_tile_states ORDER BY tile_id"
        ).fetchall()
        evidence["tier0_tiles"] = [_row_to_dict(r) for r in rows]
    except sqlite3.OperationalError:
        evidence["tier0_tiles"] = []

    # Prior live cards from previous sweep — fed back so the LLM can
    # mark them still-relevant / changed / resolved on this pass. This
    # is what gives subject_id continuity across sweeps.
    try:
        prior_cards = list_live_cards(mc_conn)
        evidence["prior_live_cards"] = [
            {
                "card_id": c["card_id"],
                "subject_kind": c["subject_kind"],
                "subject_id": c["subject_id"],
                "severity": c["severity"],
                "title": c["title"],
                "narrative": c["narrative"],
                "why_it_matters": c["why_it_matters"],
                "first_observed_at": c["first_observed_at"],
                "recurrence_count": c["recurrence_count"],
            }
            for c in prior_cards
        ]
    except sqlite3.OperationalError:
        evidence["prior_live_cards"] = []

    evidence["counts"] = {
        "active_or_attention_tasks": len(evidence.get("active_or_attention_tasks", [])),
        "recent_completed_tasks": len(evidence.get("recent_completed_tasks", [])),
        "recent_events": len(evidence.get("recent_events", [])),
        "tier0_tiles": len(evidence.get("tier0_tiles", [])),
        "prior_live_cards": len(evidence.get("prior_live_cards", [])),
    }
    return evidence


def evidence_signature(evidence: dict[str, Any]) -> str:
    """Deterministic hash of the evidence bundle's stable identifying parts.

    Used by the sweeper to gate the expensive LLM call: if the
    signature is unchanged since the last successful tier-1 pass, we
    skip the call entirely and bump a `last_checked_at` instead.

    The hash deliberately excludes volatile fields (timestamps,
    `last_checked_at`) and counts only the IDENTITY of the items in
    the bundle (task_id, event id, tile_id+state, subject_id+severity).
    Two evidence bundles with the same identifying set hash equal even
    if one was collected a second later than the other.
    """
    components = []

    for task in evidence.get("active_or_attention_tasks", []):
        components.append(f"task:{task.get('task_id')}:{task.get('status')}:{task.get('updated_at')}")
    for task in evidence.get("recent_completed_tasks", []):
        components.append(f"completed:{task.get('task_id')}:{task.get('status')}:{task.get('updated_at')}")
    for event in evidence.get("recent_events", []):
        components.append(f"event:{event.get('id')}:{event.get('severity')}:{event.get('updated_at')}")
    for tile in evidence.get("tier0_tiles", []):
        components.append(f"tile:{tile.get('tile_id')}:{tile.get('current_state')}:{tile.get('last_signature')}")
    for card in evidence.get("prior_live_cards", []):
        components.append(f"card:{card.get('subject_id')}:{card.get('severity')}:{card.get('recurrence_count')}")

    payload = "|".join(sorted(components))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── LLM discovery ──────────────────────────────────────────────────────


def _llm_prompt(evidence: dict[str, Any]) -> str:
    """Tier-1 discovery prompt. Asks the LLM to surface meaning, NOT
    summarize raw logs. Anchors emitted cards on stable subject entities
    so identity continuity works across sweeps.
    """
    prior_subject_ids = sorted(
        {c.get("subject_id") for c in evidence.get("prior_live_cards", []) if c.get("subject_id")}
    )
    return (
        "You are Universal Agent Mission Control, tier-1 narrative card discovery.\n\n"
        "Your job: read the evidence bundle and emit a JSON list of cards that capture "
        "what the operator should understand RIGHT NOW. Identify what is meaningful — "
        "stuck tasks, failure patterns, interesting completions, infrastructure issues, "
        "noteworthy artifacts, ideas worth keeping for later. Do NOT just summarize raw logs.\n\n"
        "Identity contract (CRITICAL):\n"
        "  - Each card MUST have a stable `subject_id` tied to a real entity:\n"
        "    - task:<task_id>          — Task Hub items needing operator attention\n"
        "    - run:<run_id>            — interesting individual runs\n"
        "    - mission:<mission_id>    — VP missions in flight\n"
        "    - artifact:<path|hash>    — noteworthy artifacts produced\n"
        "    - failure_pattern:<slug>  — recurring failure patterns spanning multiple runs\n"
        "    - infrastructure:<slug>   — system component health (only if NOT already a tile)\n"
        "    - idea:<slug>             — observations / ideas worth keeping for later\n"
        "  - If a `prior_live_cards` entry is still relevant, RE-EMIT it with the SAME\n"
        "    subject_id (the schema collapses on subject identity to preserve recurrence).\n"
        "  - Cards you DON'T re-emit will auto-retire.\n"
        "  - DO NOT re-emit infrastructure cards that the tier-0 tile system already creates;\n"
        "    those are already produced mechanically. Only emit `infrastructure` if the issue\n"
        "    spans multiple tiles or the tile-level card lacks needed narrative depth.\n\n"
        "Severity vocabulary: critical | warning | watching | informational | success.\n"
        "  - critical:      operator should investigate immediately\n"
        "  - warning:       investigate when convenient\n"
        "  - watching:      pattern worth eyes on, not actionable yet\n"
        "  - informational: neutral status fact / FYI\n"
        "  - success:       a successful work product was produced; FYI / celebratory\n\n"
        "Quality bar: prefer DEPTH on a few important cards over breadth across many trivial\n"
        "ones. Empty list is acceptable when the system is genuinely calm — do not invent.\n\n"
        "Operational note: Routine successful heartbeat/cron noise should NOT produce cards.\n"
        "Only surface heartbeat/cron when there is real failure or pattern.\n\n"
        f"Prior pass surfaced these subject_ids (re-emit if still relevant): {prior_subject_ids}\n\n"
        "Return ONLY valid JSON — a single object with this exact schema:\n"
        "{\n"
        '  "cards": [\n'
        "    {\n"
        '      "subject_kind": "task|run|mission|artifact|failure_pattern|infrastructure|idea",\n'
        '      "subject_id": "stable identifier",\n'
        '      "severity": "critical|warning|watching|informational|success",\n'
        '      "title": "concise headline (~120 chars, single sentence)",\n'
        '      "narrative": "full multi-paragraph synthesis. NO LIMIT.",\n'
        '      "why_it_matters": "operator-relevance paragraph. NO LIMIT.",\n'
        '      "recommended_next_step": "free-form next step or null",\n'
        '      "tags": ["short", "tags"],\n'
        '      "evidence_refs": [\n'
        '        {"kind": "task|event|run|artifact|tile",\n'
        '         "id": "...", "uri": "...", "label": "..."}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Evidence bundle:\n"
        f"{json.dumps(evidence, ensure_ascii=False, indent=2, default=str)}"
    )


async def discover_tier1_cards(evidence: dict[str, Any]) -> tuple[list[CardUpsert], str | None]:
    """Call the dedicated glm-4.7 lane to discover this sweep's tier-1
    cards. Returns (cards, model_used) on success or ([], None) on
    failure (rate-limit, parse error, no API key, etc.) — the sweeper
    treats failure as "skip this pass" rather than crashing.
    """
    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        logger.info("Tier-1 discovery skipped: no API key configured")
        return [], None

    try:
        from anthropic import AsyncAnthropic
    except Exception as exc:
        logger.warning("anthropic package unavailable for tier-1: %s", exc)
        return [], None

    from universal_agent.utils.model_resolution import (
        mission_control_call_timeout_seconds,
        resolve_mission_control_model,
    )

    model = resolve_mission_control_model()
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "max_retries": 0,
        "timeout": mission_control_call_timeout_seconds(),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
    elif os.getenv("ZAI_API_KEY") and api_key == os.getenv("ZAI_API_KEY"):
        client_kwargs["base_url"] = "https://api.z.ai/api/anthropic"

    client = AsyncAnthropic(**client_kwargs)
    max_tokens = int(os.getenv("UA_MISSION_CONTROL_TIER1_MAX_TOKENS", "16000"))
    prompt = _llm_prompt(evidence)

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning("Tier-1 LLM call failed: %s", exc)
        return [], model

    text = "".join(getattr(block, "text", "") for block in response.content).strip()
    try:
        parsed = _extract_json_object(text)
    except Exception as exc:
        logger.warning("Tier-1 LLM response did not parse as JSON: %s", exc)
        return [], model

    raw_cards = parsed.get("cards")
    if not isinstance(raw_cards, list):
        logger.warning("Tier-1 LLM response missing `cards` list; got: %s", type(raw_cards).__name__)
        return [], model

    upserts: list[CardUpsert] = []
    for raw in raw_cards:
        if not isinstance(raw, dict):
            continue
        try:
            upserts.append(_card_upsert_from_llm(raw, model))
        except ValueError as exc:
            logger.info("Skipping malformed tier-1 card: %s | raw=%s", exc, raw)
    return upserts, model


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _card_upsert_from_llm(raw: dict[str, Any], model: str) -> CardUpsert:
    subject_kind = str(raw.get("subject_kind") or "").strip()
    subject_id = str(raw.get("subject_id") or "").strip()
    severity = str(raw.get("severity") or "").strip()
    title = str(raw.get("title") or "").strip()
    narrative = str(raw.get("narrative") or "").strip()
    why_it_matters = str(raw.get("why_it_matters") or "").strip()
    if subject_kind not in VALID_SUBJECT_KINDS:
        raise ValueError(f"invalid subject_kind {subject_kind!r}")
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"invalid severity {severity!r}")
    if not subject_id or not title or not narrative:
        raise ValueError("missing subject_id / title / narrative")
    next_step = raw.get("recommended_next_step")
    if next_step is not None:
        next_step = str(next_step).strip() or None
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    evidence_refs = raw.get("evidence_refs") or []
    if not isinstance(evidence_refs, list):
        evidence_refs = []
    return CardUpsert(
        subject_kind=subject_kind,
        subject_id=subject_id,
        severity=severity,
        title=title,
        narrative=narrative,
        why_it_matters=why_it_matters or "(LLM emitted no why_it_matters; this is a quality gap.)",
        recommended_next_step=next_step,
        tags=[str(t).strip() for t in tags if str(t).strip()][:8],
        evidence_refs=[r for r in evidence_refs if isinstance(r, dict)],
        synthesis_model=model,
    )


# ── Orchestration ──────────────────────────────────────────────────────


def apply_tier1_discovery(
    mc_conn: sqlite3.Connection,
    upserts: list[CardUpsert],
    *,
    retire_unmarked: bool = True,
) -> dict[str, Any]:
    """Persist the LLM-discovered cards and retire any prior live card
    whose subject was not re-emitted on this pass.

    `retire_unmarked=True` is the standard sweep behavior — cards the
    LLM doesn't re-emit are considered no-longer-relevant and move into
    the Knowledge Ledger via state=retired.

    Returns an action summary suitable for logging.
    """
    summary = {"created_or_updated": [], "retired": [], "errors": []}

    # Snapshot prior live cards BEFORE upserts so we can identify which
    # were not re-emitted.
    prior_subject_ids: set[tuple[str, str]] = set()
    try:
        for c in list_live_cards(mc_conn):
            prior_subject_ids.add((c["subject_kind"], c["subject_id"]))
    except sqlite3.OperationalError as exc:
        summary["errors"].append(f"list_live_cards: {exc}")
        return summary

    emitted_subject_ids: set[tuple[str, str]] = set()
    for payload in upserts:
        try:
            upsert_card(mc_conn, payload)
            emitted_subject_ids.add((payload.subject_kind, payload.subject_id))
            summary["created_or_updated"].append(f"{payload.subject_kind}:{payload.subject_id}")
        except Exception as exc:
            logger.exception("tier-1 card upsert failed for %s:%s",
                             payload.subject_kind, payload.subject_id)
            summary["errors"].append(f"upsert {payload.subject_kind}:{payload.subject_id}: {exc}")

    if retire_unmarked:
        # Don't auto-retire infrastructure cards — those are owned by the
        # tier-0 tile invariant. Only retire LLM-discovered subjects that
        # the LLM dropped this pass.
        for subject in prior_subject_ids - emitted_subject_ids:
            kind, sid = subject
            if kind == SUBJECT_INFRASTRUCTURE:
                continue  # owned by tier-0, skip
            try:
                retire_card(mc_conn, make_card_id(kind, sid))
                summary["retired"].append(f"{kind}:{sid}")
            except Exception as exc:
                summary["errors"].append(f"retire {kind}:{sid}: {exc}")

    return summary


# ── Helpers ────────────────────────────────────────────────────────────


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite Row to a plain dict, parsing known JSON columns."""
    out = dict(row)
    for json_col in ("metadata_json", "actions_json", "channels_json", "entity_ref_json",
                     "labels_json", "evidence_refs_json", "evidence_payload_json"):
        if json_col in out and isinstance(out[json_col], str) and out[json_col]:
            try:
                out[json_col[:-5]] = json.loads(out[json_col])
            except (TypeError, json.JSONDecodeError):
                pass
    return out
