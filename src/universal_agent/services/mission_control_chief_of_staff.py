"""Mission Control Chief-of-Staff readout service.

The service gathers bounded operational evidence and asks an LLM to synthesize
meaning. Deterministic code is limited to collection, storage, retention, and
fallback reporting; it does not try to score or invent operational themes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.durable.db import (
    connect_runtime_db,
    get_activity_db_path,
    get_sqlite_busy_timeout_ms,
)
from universal_agent.rate_limiter import ZAIRateLimiter
from universal_agent.utils.model_resolution import resolve_model

logger = logging.getLogger(__name__)

SERVICE_SOURCE = "mission_control_chief_of_staff"
DEFAULT_DB_FILENAME = "mission_control_chief_of_staff.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def workspace_root() -> Path:
    configured = os.getenv("UA_WORKSPACES_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[3] / "AGENT_RUN_WORKSPACES").resolve()


def default_db_path() -> Path:
    configured = os.getenv("UA_MISSION_CONTROL_COS_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    root = workspace_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / DEFAULT_DB_FILENAME


def open_store(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=get_sqlite_busy_timeout_ms() / 1000.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={get_sqlite_busy_timeout_ms()};")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mission_control_readouts (
            id TEXT PRIMARY KEY,
            generated_at_utc TEXT NOT NULL,
            source TEXT NOT NULL,
            model TEXT,
            readout_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mission_control_readouts_generated
            ON mission_control_readouts(generated_at_utc DESC);

        CREATE TABLE IF NOT EXISTS mission_control_journal (
            id TEXT PRIMARY KEY,
            generated_at_utc TEXT NOT NULL,
            summary TEXT NOT NULL,
            readout_id TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mission_control_journal_generated
            ON mission_control_journal(generated_at_utc DESC);
        """
    )


def _shorten(value: Any, *, max_chars: int = 600) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _safe_json_loads(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return fallback


def _row_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _task_summary(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "task_id": item.get("task_id"),
        "title": _shorten(item.get("title"), max_chars=220),
        "description": _shorten(item.get("description"), max_chars=700),
        "status": item.get("status"),
        "priority": item.get("priority"),
        "source_kind": item.get("source_kind"),
        "project_key": item.get("project_key"),
        "labels": (item.get("labels") or [])[:8],
        "must_complete": bool(item.get("must_complete")),
        "due_at": item.get("due_at"),
        "updated_at": item.get("updated_at"),
        "created_at": item.get("created_at"),
        "stale_state": item.get("stale_state"),
        "metadata_keys": sorted(metadata.keys())[:12],
        "dispatch": metadata.get("dispatch") if isinstance(metadata.get("dispatch"), dict) else None,
        "delegation": metadata.get("delegation") if isinstance(metadata.get("delegation"), dict) else None,
    }


def collect_task_hub_evidence(*, limit: int = 20, completed_limit: int = 12) -> dict[str, Any]:
    with connect_runtime_db(get_activity_db_path()) as conn:
        task_hub.ensure_schema(conn)
        queue = task_hub.list_agent_queue(
            conn,
            limit=limit,
            include_csi=True,
            collapse_csi=True,
            include_not_ready=True,
        )
        active_items = [_task_summary(item) for item in queue.get("items", [])[:limit]]
        completed_items = [
            _task_summary(item)
            for item in task_hub.list_completed_tasks(conn, limit=completed_limit)[:completed_limit]
        ]
    return {
        "active_or_attention_items": active_items,
        "recent_completed_items": completed_items,
        "counts": {
            "active_or_attention_items": len(active_items),
            "recent_completed_items": len(completed_items),
        },
    }


def collect_activity_evidence(*, limit: int = 80) -> dict[str, Any]:
    try:
        with connect_runtime_db(get_activity_db_path()) as conn:
            rows = conn.execute(
                """
                SELECT id, event_class, source_domain, kind, title, summary,
                       full_message, severity, status, requires_action, session_id,
                       created_at, updated_at, metadata_json
                FROM activity_events
                WHERE (
                    LOWER(COALESCE(severity, '')) IN ('warning', 'error', 'critical')
                    OR COALESCE(requires_action, 0) = 1
                    OR LOWER(COALESCE(source_domain, '')) NOT IN ('heartbeat')
                )
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 300)),),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.info("Mission Control activity evidence unavailable: %s", exc)
        return {"items": [], "counts": {"items": 0}, "unavailable": str(exc)}

    items: list[dict[str, Any]] = []
    for row in rows:
        data = _row_dict(row)
        metadata = _safe_json_loads(data.pop("metadata_json", None), {})
        kind = str(data.get("kind") or "").lower()
        source = str(data.get("source_domain") or "").lower()
        severity = str(data.get("severity") or "").lower()
        if source == "heartbeat" and severity in {"", "info", "success"} and "fail" not in kind and "error" not in kind:
            continue
        data["summary"] = _shorten(data.get("summary") or data.get("full_message"), max_chars=700)
        data["full_message"] = _shorten(data.get("full_message"), max_chars=900)
        data["metadata"] = metadata
        items.append(data)
    return {"items": items, "counts": {"items": len(items)}}


def collect_tutorial_evidence(*, limit: int = 12) -> dict[str, Any]:
    roots = [
        workspace_root() / "youtube_tutorial_pipeline",
        workspace_root() / "tutorial_pipeline",
        workspace_root() / "youtube_tutorial_runs",
    ]
    manifests: list[tuple[float, Path, dict[str, Any]]] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for manifest_path in root.rglob("manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mtime = manifest_path.stat().st_mtime
            except Exception:
                continue
            manifests.append((mtime, manifest_path, manifest))
    manifests.sort(key=lambda item: item[0], reverse=True)

    items: list[dict[str, Any]] = []
    for mtime, manifest_path, manifest in manifests[: max(1, min(int(limit), 50))]:
        items.append(
            {
                "run_name": manifest_path.parent.name,
                "run_dir": str(manifest_path.parent),
                "created_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "title": _shorten(manifest.get("title") or manifest_path.parent.name, max_chars=220),
                "video_url": manifest.get("video_url"),
                "learning_mode": manifest.get("learning_mode") or manifest.get("mode"),
                "status": manifest.get("status") or manifest.get("pipeline_status"),
                "implementation_required": manifest.get("implementation_required"),
            }
        )
    return {"items": items, "counts": {"items": len(items)}}


def collect_csi_evidence(*, limit: int = 12) -> dict[str, Any]:
    db_path = workspace_root() / ".csi_digests.db"
    if not db_path.exists():
        return {"items": [], "counts": {"items": 0}}
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, event_id, source, event_type, title, summary,
                       source_types, created_at
                FROM csi_digests
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.info("Mission Control CSI evidence unavailable: %s", exc)
        return {"items": [], "counts": {"items": 0}, "unavailable": str(exc)}

    items = []
    for row in rows:
        data = _row_dict(row)
        data["summary"] = _shorten(data.get("summary"), max_chars=900)
        data["source_types"] = _safe_json_loads(data.get("source_types"), [])
        items.append(data)
    return {"items": items, "counts": {"items": len(items)}}


def collect_workspace_artifact_evidence(*, limit: int = 18) -> dict[str, Any]:
    root = workspace_root()
    if not root.exists():
        return {"items": [], "counts": {"items": 0}}
    candidates: list[tuple[float, Path]] = []
    allowed_names = {
        "summary.md",
        "report.md",
        "digest.md",
        "manifest.json",
        "handoff.md",
        "completion.md",
        "operator_report.md",
    }
    for path in root.rglob("*"):
        if not path.is_file() or path.name not in allowed_names:
            continue
        try:
            mtime = path.stat().st_mtime
        except Exception:
            continue
        candidates.append((mtime, path))
    candidates.sort(key=lambda item: item[0], reverse=True)

    items: list[dict[str, Any]] = []
    for mtime, path in candidates[: max(1, min(int(limit), 60))]:
        preview = ""
        try:
            preview = _shorten(path.read_text(encoding="utf-8", errors="ignore"), max_chars=900)
        except Exception:
            pass
        items.append(
            {
                "path": str(path),
                "workspace": path.parent.name,
                "name": path.name,
                "updated_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "preview": preview,
            }
        )
    return {"items": items, "counts": {"items": len(items)}}


def collect_mission_control_cards_evidence(
    *,
    live_limit: int = 30,
    retired_window_hours: int = 48,
    retired_limit: int = 30,
    recurrence_threshold: int = 2,
) -> dict[str, Any]:
    """Phase 3 — surface the tier-1 Mission Control cards into Chief-of-Staff
    synthesis context.

    The Phase 1/2 sweeper produces tier-1 narrative cards (LLM-discovered)
    and tier-0 infrastructure cards (mechanically auto-created). The
    Chief-of-Staff readout was previously written before those existed
    and synthesizes meaning from raw evidence each pass. Phase 3 feeds
    the EXISTING cards back into the readout's context so:

      - The readout doesn't re-synthesize what Mission Control already
        synthesized — it can cite/reference and zoom out.
      - Recurring subjects (recurrence_count >= threshold) are flagged
        explicitly so the operator picture surfaces patterns, not just
        snapshots.
      - Recently retired cards (last 48h) provide RECENCY context — the
        readout can say "these were resolved this morning" rather than
        ignoring them.

    Returns three lists:
      - live: current state of the Mission Control card grid
      - retired_recent: cards retired within the lookback window (auto-
        retired by Phase 2 tier-1 retire-unmarked OR operator dismissal)
      - recurring: cards with recurrence_count >= threshold (revival
        history captures repeating patterns)

    Failure-tolerant: if the MC store doesn't exist yet (Phase 1 not
    enabled, fresh boot, etc.) returns empty lists with an "unavailable"
    marker so the COS readout still produces output without cards.
    """
    try:
        from universal_agent.services.mission_control_db import open_store
    except Exception as exc:  # pragma: no cover — defensive import
        return {
            "live": [],
            "retired_recent": [],
            "recurring": [],
            "counts": {"live": 0, "retired_recent": 0, "recurring": 0},
            "unavailable": f"mission_control_db import failed: {exc}",
        }

    try:
        conn = open_store()
    except Exception as exc:
        return {
            "live": [],
            "retired_recent": [],
            "recurring": [],
            "counts": {"live": 0, "retired_recent": 0, "recurring": 0},
            "unavailable": f"mission_control_db open failed: {exc}",
        }

    try:
        # Live cards (Phase 1 + Phase 2 cards currently visible to operator)
        live_rows = conn.execute(
            """
            SELECT * FROM mission_control_cards
            WHERE current_state = 'live'
            ORDER BY
              CASE severity
                WHEN 'critical' THEN 0
                WHEN 'warning' THEN 1
                WHEN 'watching' THEN 2
                WHEN 'informational' THEN 3
                WHEN 'success' THEN 4
                ELSE 5
              END,
              recurrence_count DESC,
              last_synthesized_at DESC
            LIMIT ?
            """,
            (max(1, min(int(live_limit), 100)),),
        ).fetchall()
        live_items = [_card_row_to_summary(row) for row in live_rows]

        # Retired in last 48h — gives the COS readout RECENCY context
        # (operator-facing "what happened today" framing).
        retired_rows = conn.execute(
            """
            SELECT * FROM mission_control_cards
            WHERE current_state = 'retired'
              AND last_synthesized_at > datetime('now', ?)
            ORDER BY last_synthesized_at DESC
            LIMIT ?
            """,
            (f"-{int(retired_window_hours)} hours", max(1, min(int(retired_limit), 100))),
        ).fetchall()
        retired_items = [_card_row_to_summary(row) for row in retired_rows]

        # Recurring subjects (recurrence_count >= threshold). Pulled
        # separately from `live` so even retired cards surface as
        # patterns when relevant — e.g. a CSI failure that has revived
        # 4 times this week is operationally important even if the
        # most recent occurrence is currently retired.
        recurring_rows = conn.execute(
            """
            SELECT * FROM mission_control_cards
            WHERE recurrence_count >= ?
            ORDER BY recurrence_count DESC, last_synthesized_at DESC
            LIMIT 50
            """,
            (max(1, int(recurrence_threshold)),),
        ).fetchall()
        recurring_items = [_card_row_to_summary(row) for row in recurring_rows]
    except sqlite3.OperationalError as exc:
        # Schema may not exist yet (Phase 0 hadn't deployed when this
        # was first invoked, etc.). Degrade gracefully.
        return {
            "live": [],
            "retired_recent": [],
            "recurring": [],
            "counts": {"live": 0, "retired_recent": 0, "recurring": 0},
            "unavailable": f"mission_control_cards table missing: {exc}",
        }
    finally:
        conn.close()

    return {
        "live": live_items,
        "retired_recent": retired_items,
        "recurring": recurring_items,
        "counts": {
            "live": len(live_items),
            "retired_recent": len(retired_items),
            "recurring": len(recurring_items),
            # Aggregate for evidence_payload.source_counts compatibility
            "items": len(live_items) + len(retired_items),
        },
    }


def _card_row_to_summary(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Reduce a card row to a synthesis-friendly summary.

    The Chief-of-Staff prompt doesn't need the full operator-feedback
    JSON or full synthesis_history (those bloat the prompt). It needs:
    title, narrative, why_it_matters, severity, subject + recurrence
    + comments-text-only (operator voice as preference signal).
    """
    data = dict(row)
    feedback_raw = data.get("operator_feedback_json") or "{}"
    try:
        feedback = json.loads(feedback_raw) if isinstance(feedback_raw, str) else feedback_raw
    except (TypeError, json.JSONDecodeError):
        feedback = {}
    if not isinstance(feedback, dict):
        feedback = {}
    comments_text = []
    for c in (feedback.get("comments") or []):
        if isinstance(c, dict) and c.get("text"):
            comments_text.append({"ts": c.get("ts"), "text": c.get("text")})
    return {
        "card_id": data.get("card_id"),
        "subject_kind": data.get("subject_kind"),
        "subject_id": data.get("subject_id"),
        "current_state": data.get("current_state"),
        "severity": data.get("severity"),
        "title": data.get("title"),
        "narrative": data.get("narrative"),
        "why_it_matters": data.get("why_it_matters"),
        "recommended_next_step": data.get("recommended_next_step"),
        "recurrence_count": data.get("recurrence_count"),
        "first_observed_at": data.get("first_observed_at"),
        "last_synthesized_at": data.get("last_synthesized_at"),
        "synthesis_model": data.get("synthesis_model"),
        "operator_thumbs": feedback.get("thumbs"),
        "operator_comments": comments_text[-5:],  # last 5 comments
    }


def collect_evidence_bundle() -> dict[str, Any]:
    generated_at = utc_now_iso()
    evidence = {
        "generated_at_utc": generated_at,
        "source": SERVICE_SOURCE,
        "collection_policy": {
            "purpose": "Bound evidence for LLM operator synthesis, not programmatic pseudo-reasoning.",
            "suppressed_noise": "Routine heartbeat success is excluded unless tied to warnings, errors, or required action.",
            "phase3_layering": (
                "Mission Control tier-1 cards (already-synthesized intelligence) are included so "
                "the readout can ZOOM OUT and reference them rather than re-synthesizing the "
                "same material. Recurring/retired cards surface PATTERNS the readout should "
                "weave into its narrative."
            ),
        },
        "sources": {
            "task_hub": collect_task_hub_evidence(),
            "activity_events": collect_activity_evidence(),
            "tutorial_pipeline": collect_tutorial_evidence(),
            "csi_digests": collect_csi_evidence(),
            "workspace_artifacts": collect_workspace_artifact_evidence(),
            "mission_control_cards": collect_mission_control_cards_evidence(),
        },
    }
    source_counts = {
        name: int((source.get("counts") or {}).get("items") or 0)
        for name, source in evidence["sources"].items()
    }
    source_counts["task_hub_active_or_attention_items"] = int(
        evidence["sources"]["task_hub"]["counts"].get("active_or_attention_items") or 0
    )
    source_counts["task_hub_recent_completed_items"] = int(
        evidence["sources"]["task_hub"]["counts"].get("recent_completed_items") or 0
    )
    # Phase 3: surface mission_control cards counts at the top level for
    # operator-visible audit (the diagnostics endpoint reads source_counts).
    mc_counts = (evidence["sources"]["mission_control_cards"].get("counts") or {})
    source_counts["mission_control_cards_live"] = int(mc_counts.get("live") or 0)
    source_counts["mission_control_cards_retired_recent"] = int(mc_counts.get("retired_recent") or 0)
    source_counts["mission_control_cards_recurring"] = int(mc_counts.get("recurring") or 0)
    evidence["source_counts"] = source_counts
    return evidence


def _llm_prompt(evidence: dict[str, Any]) -> str:
    # Phase 3 — surface the Mission Control card layer EXPLICITLY in the
    # prompt. The readout is now a tier-2 synthesis ON TOP of tier-1 cards
    # rather than a parallel synthesis from raw evidence. Concretely the
    # prompt instructs the LLM to:
    #   - Reference live cards by subject_id rather than re-summarizing
    #     their evidence (operator already saw them on the dashboard).
    #   - Surface RECURRING patterns (recurrence_count >= 2) explicitly
    #     in the narrative — those are the most actionable signals.
    #   - Note recently retired cards as RECENCY context ("these resolved
    #     this morning").
    #   - Use operator comments + thumbs as preference signal (e.g. if
    #     operator thumbed-down a card kind, similar future surfaces
    #     should be deprioritized in the readout).
    mc_cards = evidence.get("sources", {}).get("mission_control_cards") or {}
    mc_card_layering_block = (
        "\n\nMission Control card layering (Phase 3):\n"
        "  - The `mission_control_cards` source contains tier-1 cards that the "
        "operator can already see on the dashboard. Treat them as ALREADY-"
        "SYNTHESIZED intelligence; do not re-summarize their internal evidence.\n"
        "  - Reference live cards by `subject_id` (e.g. 'task:vp-mission-...'). "
        "Operator already has the click-through.\n"
        "  - Cards in `recurring` (recurrence_count >= 2) are PATTERN signals. "
        "Weave them into the narrative as patterns, not snapshots — say 'this "
        "is the third time this week the cron health report has stuck' rather "
        "than just 'cron health report stuck'.\n"
        "  - Cards in `retired_recent` are RECENCY context — recent things that "
        "resolved/were dismissed in the last 48h. Use them to give the operator "
        "a sense of forward motion ('three blockers from yesterday cleared').\n"
        "  - `operator_thumbs` and `operator_comments` are PREFERENCE signal. "
        "If operator thumbed-down a kind of card or commented 'less of this', "
        "deprioritize similar surfaces in the readout.\n"
        f"  - Counts: live={int((mc_cards.get('counts') or {}).get('live') or 0)}, "
        f"retired_recent={int((mc_cards.get('counts') or {}).get('retired_recent') or 0)}, "
        f"recurring={int((mc_cards.get('counts') or {}).get('recurring') or 0)}.\n"
    )
    return (
        "You are Universal Agent Mission Control Chief of Staff. Produce a concise operator "
        "intelligence readout for Kevin.\n\n"
        "Design rule: do not summarize raw logs by source. Identify what seems meaningful "
        "about missions, completed work, failures, follow-up needs, created artifacts, "
        "upcoming obligations, or useful ideas. Routine successful heartbeat/system noise "
        "should disappear unless it changes what the operator should understand.\n\n"
        "Use the evidence exactly as evidence. If a claim is uncertain, say so. Preserve "
        "handoff utility by referencing evidence refs that another agent could inspect."
        + mc_card_layering_block + "\n\n"
        "Return ONLY valid JSON with this schema:\n"
        "{\n"
        '  "headline": "one sentence current operating picture",\n'
        '  "generated_at_utc": "ISO timestamp",\n'
        '  "executive_snapshot": ["2-5 concise bullets"],\n'
        '  "sections": [\n'
        "    {\n"
        '      "title": "meaning-based section title",\n'
        '      "summary": "short synthesis",\n'
        '      "items": [\n'
        "        {\n"
        '          "title": "specific evaluated item title",\n'
        '          "body": "what is going on",\n'
        '          "why_it_matters": "why Kevin might care",\n'
        '          "recommended_next_step": "optional next step",\n'
        '          "tags": ["short tags"],\n'
        '          "evidence_refs": ["source:id/path/task_id/etc"]\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "watchlist": [{"title": "thing to watch", "reason": "why", "evidence_refs": []}],\n'
        '  "action_candidates": [{"title": "candidate only, not executed", "rationale": "why", "gate": "existing Task Hub/proactive gate to use", "evidence_refs": []}],\n'
        '  "journal_entry": "short durable paragraph for later briefing context",\n'
        '  "source_counts": {}\n'
        "}\n\n"
        "Evidence bundle:\n"
        f"{json.dumps(evidence, ensure_ascii=False, indent=2)}"
    )


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


def fallback_readout(evidence: dict[str, Any], *, error: str | None = None) -> dict[str, Any]:
    generated_at = str(evidence.get("generated_at_utc") or utc_now_iso())
    counts = evidence.get("source_counts") if isinstance(evidence.get("source_counts"), dict) else {}
    message = "Chief-of-Staff LLM synthesis is unavailable; showing bounded evidence inventory."
    if error:
        message = f"{message} Error: {_shorten(error, max_chars=240)}"
    return {
        "id": f"mcos_{generated_at.replace(':', '').replace('+', 'Z')}",
        "headline": "Mission Control evidence is collected; synthesis needs a successful LLM pass.",
        "generated_at_utc": generated_at,
        "executive_snapshot": [
            message,
            f"Evidence counts: {json.dumps(counts, sort_keys=True)}",
        ],
        "sections": [
            {
                "title": "Evidence Awaiting Synthesis",
                "summary": "Raw evidence was bounded and preserved for a future Chief-of-Staff pass.",
                "items": [],
            }
        ],
        "watchlist": [],
        "action_candidates": [],
        "journal_entry": message,
        "source_counts": counts,
        "synthesis_status": "fallback",
    }


async def synthesize_readout(evidence: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        return fallback_readout(evidence, error="No Anthropic-compatible API key configured."), None

    try:
        from anthropic import AsyncAnthropic
    except Exception as exc:
        return fallback_readout(evidence, error=f"anthropic package unavailable: {exc}"), None

    # Promoted to opus tier per the post-atom-poem audit. Mission
    # Control "Chief of Staff" synthesizes priorities and recommendations
    # across many simultaneous signals (CSI, Task Hub, supervisors,
    # heartbeat findings) and is the canonical human-facing
    # operational summary. Strategic synthesis quality matters more
    # than per-call cost here; per-tier env override
    # (UA_MISSION_CONTROL_COS_MODEL) still wins if the operator wants
    # to dial back.
    model = os.getenv("UA_MISSION_CONTROL_COS_MODEL") or resolve_model("opus")
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "max_retries": 0,
        "timeout": float(os.getenv("UA_MISSION_CONTROL_COS_TIMEOUT_SECONDS", "180")),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
    elif os.getenv("ZAI_API_KEY") and api_key == os.getenv("ZAI_API_KEY"):
        client_kwargs["base_url"] = "https://api.z.ai/api/anthropic"

    client = AsyncAnthropic(**client_kwargs)
    limiter = ZAIRateLimiter.get_instance()
    max_retries = int(os.getenv("UA_MISSION_CONTROL_COS_MAX_RETRIES", "3"))
    max_tokens = int(os.getenv("UA_MISSION_CONTROL_COS_MAX_TOKENS", "18000"))  # doubled from 9000 per audit
    prompt = _llm_prompt(evidence)
    last_error: Exception | None = None

    for attempt in range(max_retries):
        async with limiter.acquire("mission_control_chief_of_staff"):
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0.3,
                    messages=[{"role": "user", "content": prompt}],
                )
                await limiter.record_success()
                text = "".join(getattr(block, "text", "") for block in response.content).strip()
                readout = _extract_json_object(text)
                readout.setdefault("generated_at_utc", evidence.get("generated_at_utc") or utc_now_iso())
                readout.setdefault("source_counts", evidence.get("source_counts") or {})
                readout["synthesis_status"] = "ok"
                return readout, model
            except Exception as exc:
                last_error = exc
                if "429" in str(exc) or "rate" in str(exc).lower():
                    await limiter.record_429("mission_control_chief_of_staff")
                if attempt < max_retries - 1:
                    import asyncio

                    await asyncio.sleep(limiter.get_backoff(attempt))

    return fallback_readout(evidence, error=str(last_error or "unknown LLM error")), model


def persist_readout(
    readout: dict[str, Any],
    evidence: dict[str, Any],
    *,
    model: str | None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    generated_at = str(readout.get("generated_at_utc") or evidence.get("generated_at_utc") or utc_now_iso())
    readout_id = str(readout.get("id") or f"mcos_{generated_at.replace(':', '').replace('+', 'Z')}")
    readout["id"] = readout_id
    now = utc_now_iso()
    journal_entry = _shorten(readout.get("journal_entry") or readout.get("headline"), max_chars=1800)
    with open_store(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO mission_control_readouts
                (id, generated_at_utc, source, model, readout_json, evidence_json, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                readout_id,
                generated_at,
                SERVICE_SOURCE,
                model,
                json.dumps(readout, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                now,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO mission_control_journal
                (id, generated_at_utc, summary, readout_id, created_at_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"journal_{readout_id}", generated_at, journal_entry, readout_id, now),
        )
        retention = max(10, int(os.getenv("UA_MISSION_CONTROL_COS_RETENTION", "200")))
        conn.execute(
            """
            DELETE FROM mission_control_readouts
            WHERE id NOT IN (
                SELECT id FROM mission_control_readouts
                ORDER BY generated_at_utc DESC
                LIMIT ?
            )
            """,
            (retention,),
        )
        conn.execute(
            """
            DELETE FROM mission_control_journal
            WHERE id NOT IN (
                SELECT id FROM mission_control_journal
                ORDER BY generated_at_utc DESC
                LIMIT ?
            )
            """,
            (retention,),
        )
    return readout


async def generate_and_store_readout(*, db_path: Path | None = None) -> dict[str, Any]:
    evidence = collect_evidence_bundle()
    readout, model = await synthesize_readout(evidence)
    return persist_readout(readout, evidence, model=model, db_path=db_path)


def get_latest_readout(*, include_evidence: bool = False, db_path: Path | None = None) -> dict[str, Any] | None:
    try:
        with open_store(db_path) as conn:
            row = conn.execute(
                """
                SELECT id, generated_at_utc, source, model, readout_json, evidence_json, created_at_utc
                FROM mission_control_readouts
                ORDER BY generated_at_utc DESC
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("Mission Control readout store unavailable: %s", exc)
        return None

    if not row:
        return None
    readout = _safe_json_loads(row["readout_json"], {})
    if not isinstance(readout, dict):
        return None
    readout.setdefault("id", row["id"])
    readout.setdefault("generated_at_utc", row["generated_at_utc"])
    readout["model"] = row["model"]
    readout["source"] = row["source"]
    if include_evidence:
        readout["evidence_bundle"] = _safe_json_loads(row["evidence_json"], {})
    return readout


def get_recent_journal(*, limit: int = 20, db_path: Path | None = None) -> list[dict[str, Any]]:
    with open_store(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, generated_at_utc, summary, readout_id, created_at_utc
            FROM mission_control_journal
            ORDER BY generated_at_utc DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [_row_dict(row) for row in rows]
