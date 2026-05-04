"""Mission Control Intelligence — card persistence helpers.

Phase 1 deliverable. Provides the minimum card-write surface needed for
tier-0 tile transitions to auto-create `infrastructure`-kind tier-1
cards. Phase 2 will build out the full LLM-discovery write path on top
of these primitives without changing the helpers' contracts.

Why a separate module: the sweeper writes cards from tier-0 transitions
(this phase), tier-1 LLM discovery writes them in Phase 2, and the
gateway endpoints will read them from Phase 1B onwards. Centralizing
the upsert + state-transition logic here keeps invariants consistent
across all writers.

See docs/02_Subsystems/Mission_Control_Intelligence_System.md §3.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from typing import Any

CARD_STATE_LIVE = "live"
CARD_STATE_RETIRED = "retired"
CARD_STATE_ARCHIVED = "archived"

# Severity vocabulary mirrors the schema CHECK constraint.
SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_WATCHING = "watching"
SEVERITY_INFORMATIONAL = "informational"
SEVERITY_SUCCESS = "success"

# Subject-kind vocabulary mirrors the schema CHECK constraint.
SUBJECT_TASK = "task"
SUBJECT_RUN = "run"
SUBJECT_MISSION = "mission"
SUBJECT_ARTIFACT = "artifact"
SUBJECT_FAILURE_PATTERN = "failure_pattern"
SUBJECT_INFRASTRUCTURE = "infrastructure"
SUBJECT_IDEA = "idea"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_card_id(subject_kind: str, subject_id: str) -> str:
    """Stable card id: SHA-256 of (subject_kind|subject_id).

    Same subject = same card_id forever, which is what gives revived
    cards their identity continuity.
    """
    return "card_" + hashlib.sha256(f"{subject_kind}|{subject_id}".encode("utf-8")).hexdigest()[:24]


@dataclass
class CardUpsert:
    """Input payload for `upsert_card`. Server-managed wraparound fields
    (synthesis_history, recurrence_count, etc.) are filled in here, not
    by callers — keeps invariants tight.
    """

    subject_kind: str
    subject_id: str
    severity: str
    title: str
    narrative: str
    why_it_matters: str
    recommended_next_step: str | None = None
    tags: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    evidence_payload: dict[str, Any] | None = None
    synthesis_model: str | None = None
    evidence_signature: str | None = None


def upsert_card(conn: sqlite3.Connection, payload: CardUpsert) -> dict[str, Any]:
    """Insert or update a card identified by its (subject_kind, subject_id).

    Behavior:
      * If no row exists → INSERT with state=live, recurrence_count=1
      * If row exists with state=live → UPDATE narrative + push prior
        synthesis into history, leave recurrence_count alone
      * If row exists with state in {retired, archived} → UPDATE state
        back to live, increment recurrence_count, push prior synthesis
        into history (the card is "reviving")

    Returns the canonical card dict reflecting the post-upsert state.
    """
    card_id = make_card_id(payload.subject_kind, payload.subject_id)
    now = _utc_now_iso()

    existing = conn.execute(
        """
        SELECT *
        FROM mission_control_cards
        WHERE card_id = ?
        """,
        (card_id,),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO mission_control_cards (
                card_id, subject_kind, subject_id, current_state, severity,
                title, narrative, why_it_matters, recommended_next_step,
                tags_json, evidence_refs_json, evidence_payload_json,
                synthesis_history_json, dispatch_history_json,
                operator_feedback_json, last_viewed_at_json,
                first_observed_at, last_synthesized_at, last_evidence_signature,
                recurrence_count, synthesis_model
            ) VALUES (
                ?, ?, ?, 'live', ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                '[]', '[]',
                '{"thumbs":null,"snoozed_until":null,"comments":[]}', '{}',
                ?, ?, ?,
                1, ?
            )
            """,
            (
                card_id,
                payload.subject_kind,
                payload.subject_id,
                payload.severity,
                payload.title,
                payload.narrative,
                payload.why_it_matters,
                payload.recommended_next_step,
                json.dumps(payload.tags),
                json.dumps(payload.evidence_refs),
                json.dumps(payload.evidence_payload) if payload.evidence_payload is not None else None,
                now,
                now,
                payload.evidence_signature,
                payload.synthesis_model,
            ),
        )
    else:
        prior_state = existing["current_state"]
        history_json = existing["synthesis_history_json"] or "[]"
        try:
            history = json.loads(history_json)
            if not isinstance(history, list):
                history = []
        except json.JSONDecodeError:
            history = []
        history.insert(
            0,
            {
                "ts": existing["last_synthesized_at"],
                "narrative": existing["narrative"],
                "evidence_signature": existing["last_evidence_signature"],
                "model": existing["synthesis_model"],
            },
        )
        history = history[:10]  # cap retention per card

        recurrence = int(existing["recurrence_count"] or 1)
        if prior_state in {CARD_STATE_RETIRED, CARD_STATE_ARCHIVED}:
            recurrence += 1

        conn.execute(
            """
            UPDATE mission_control_cards
            SET current_state = 'live',
                severity = ?,
                title = ?,
                narrative = ?,
                why_it_matters = ?,
                recommended_next_step = ?,
                tags_json = ?,
                evidence_refs_json = ?,
                evidence_payload_json = ?,
                synthesis_history_json = ?,
                last_synthesized_at = ?,
                last_evidence_signature = ?,
                recurrence_count = ?,
                synthesis_model = ?
            WHERE card_id = ?
            """,
            (
                payload.severity,
                payload.title,
                payload.narrative,
                payload.why_it_matters,
                payload.recommended_next_step,
                json.dumps(payload.tags),
                json.dumps(payload.evidence_refs),
                json.dumps(payload.evidence_payload) if payload.evidence_payload is not None else None,
                json.dumps(history),
                now,
                payload.evidence_signature,
                recurrence,
                payload.synthesis_model,
                card_id,
            ),
        )

    return get_card(conn, card_id)  # type: ignore[return-value]


def retire_card(conn: sqlite3.Connection, card_id: str) -> None:
    """Mark a live card as retired. Idempotent."""
    conn.execute(
        """
        UPDATE mission_control_cards
        SET current_state = 'retired'
        WHERE card_id = ? AND current_state = 'live'
        """,
        (card_id,),
    )


def get_card(conn: sqlite3.Connection, card_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM mission_control_cards WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    return dict(row) if row else None


def live_card_exists_for_subject(
    conn: sqlite3.Connection, subject_kind: str, subject_id: str
) -> bool:
    """True iff a card exists for this subject in current_state='live'.

    Used by the sweeper's invariant check: a non-green tile must have a
    corresponding live infrastructure card. Cheap PK lookup.
    """
    card_id = make_card_id(subject_kind, subject_id)
    row = conn.execute(
        "SELECT 1 FROM mission_control_cards WHERE card_id = ? AND current_state = 'live'",
        (card_id,),
    ).fetchone()
    return row is not None


# ── Operator feedback mutations (Phase 2) ────────────────────────────────

VALID_THUMBS = {"up", "down", None}
VALID_SNOOZE_DURATIONS = {"1h", "4h", "1d", "1w"}
_SNOOZE_SECONDS = {"1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}


def _load_operator_feedback(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    parsed.setdefault("thumbs", None)
    parsed.setdefault("snoozed_until", None)
    parsed.setdefault("comments", [])
    if not isinstance(parsed["comments"], list):
        parsed["comments"] = []
    return parsed


def set_card_thumbs(
    conn: sqlite3.Connection, card_id: str, direction: str | None
) -> dict[str, Any]:
    """Set the thumbs feedback signal on a card.

    direction = "up" / "down" / None (clears).

    Phase 2 contract: thumbs aggregate as a lightweight reinforcement
    signal that gets fed back into Chief-of-Staff prompt context. We
    persist immediately; consumers read the latest value.
    """
    if direction not in VALID_THUMBS:
        raise ValueError(f"thumbs must be one of ['up', 'down', None], got {direction!r}")
    row = conn.execute(
        "SELECT operator_feedback_json FROM mission_control_cards WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"card not found: {card_id}")
    feedback = _load_operator_feedback(row["operator_feedback_json"])
    feedback["thumbs"] = direction
    conn.execute(
        "UPDATE mission_control_cards SET operator_feedback_json = ? WHERE card_id = ?",
        (json.dumps(feedback), card_id),
    )
    return feedback


def snooze_card(
    conn: sqlite3.Connection, card_id: str, duration: str
) -> dict[str, Any]:
    """Snooze a card for a fixed duration. Auto-revives on expiry
    (consumers compare snoozed_until to now and treat the card as
    visible-again when the timestamp has passed).
    """
    if duration not in VALID_SNOOZE_DURATIONS:
        raise ValueError(
            f"duration must be one of {sorted(VALID_SNOOZE_DURATIONS)!s}, got {duration!r}"
        )
    row = conn.execute(
        "SELECT operator_feedback_json FROM mission_control_cards WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"card not found: {card_id}")
    feedback = _load_operator_feedback(row["operator_feedback_json"])
    expiry = datetime.now(timezone.utc) + timedelta(seconds=_SNOOZE_SECONDS[duration])
    feedback["snoozed_until"] = expiry.isoformat()
    conn.execute(
        "UPDATE mission_control_cards SET operator_feedback_json = ? WHERE card_id = ?",
        (json.dumps(feedback), card_id),
    )
    return feedback


def add_card_comment(
    conn: sqlite3.Connection, card_id: str, text: str
) -> dict[str, Any]:
    """Append a timestamped operator comment to a card.

    Comments are first-class memory: never overwritten, never truncated
    here. They feed back into future LLM synthesis prompts so the
    operator's voice shapes the system's read of similar situations.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("comment text must be non-empty")
    row = conn.execute(
        "SELECT operator_feedback_json FROM mission_control_cards WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"card not found: {card_id}")
    feedback = _load_operator_feedback(row["operator_feedback_json"])
    feedback["comments"].append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "text": cleaned,
    })
    conn.execute(
        "UPDATE mission_control_cards SET operator_feedback_json = ? WHERE card_id = ?",
        (json.dumps(feedback), card_id),
    )
    return feedback


def mark_card_viewed(
    conn: sqlite3.Connection, card_id: str, viewer: str = "operator"
) -> dict[str, Any]:
    """Stamp last_viewed_at for a per-user view tracker (F#6).

    Phase 2 ships single-operator support; the JSON column is
    structured per-user so multi-operator support is a no-op upgrade.
    """
    row = conn.execute(
        "SELECT last_viewed_at_json FROM mission_control_cards WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"card not found: {card_id}")
    try:
        viewed = json.loads(row["last_viewed_at_json"] or "{}")
        if not isinstance(viewed, dict):
            viewed = {}
    except (TypeError, json.JSONDecodeError):
        viewed = {}
    viewed[str(viewer or "operator")] = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE mission_control_cards SET last_viewed_at_json = ? WHERE card_id = ?",
        (json.dumps(viewed), card_id),
    )
    return viewed


# ── Dispatch history mutations (Phase 4) ─────────────────────────────────

VALID_DISPATCH_ACTIONS = {"prompt_generated_for_external", "dispatched_to_codie"}


def append_dispatch_history(
    conn: sqlite3.Connection,
    *,
    card_id: str,
    action: str,
    prompt_text: str,
    operator_steering_text: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Append an entry to a card's dispatch history (Phase 4).

    Two action shapes per the schema CHECK constraint:
      - prompt_generated_for_external: operator copy-pasted the prompt
        to an external AI coder. No task_id; no Task Hub state change.
      - dispatched_to_codie: operator sent the prompt to Codie via
        Task Hub. task_id captures the new Task Hub item id.

    The append also writes into mission_control_dispatch_history (the
    long-form audit log) AND mirrors a summary into the card's
    dispatch_history_json JSON column for fast read-side rendering.
    """
    if action not in VALID_DISPATCH_ACTIONS:
        raise ValueError(
            f"action must be one of {sorted(VALID_DISPATCH_ACTIONS)!s}, got {action!r}"
        )
    if not (prompt_text or "").strip():
        raise ValueError("prompt_text must be non-empty")

    row = conn.execute(
        "SELECT dispatch_history_json FROM mission_control_cards WHERE card_id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"card not found: {card_id}")

    now = datetime.now(timezone.utc).isoformat()
    dispatch_id = "disp_" + hashlib.sha256(
        f"{card_id}|{action}|{now}".encode("utf-8")
    ).hexdigest()[:24]

    # Long-form audit row
    conn.execute(
        """
        INSERT INTO mission_control_dispatch_history
            (dispatch_id, card_id, action, ts, prompt_text, operator_steering_text, task_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (dispatch_id, card_id, action, now, prompt_text,
         operator_steering_text, task_id),
    )

    # Card-side mirror (summary entries; full prompt stays in the long-form
    # table). Cap the in-card list at 20 entries so it doesn't bloat;
    # earlier entries remain queryable in mission_control_dispatch_history.
    try:
        history = json.loads(row["dispatch_history_json"] or "[]")
        if not isinstance(history, list):
            history = []
    except (TypeError, json.JSONDecodeError):
        history = []
    summary_entry = {
        "dispatch_id": dispatch_id,
        "ts": now,
        "action": action,
        "prompt_text_chars": len(prompt_text),
        "task_id": task_id,
        "operator_steering_text": operator_steering_text,
    }
    history.insert(0, summary_entry)
    history = history[:20]
    conn.execute(
        "UPDATE mission_control_cards SET dispatch_history_json = ? WHERE card_id = ?",
        (json.dumps(history), card_id),
    )
    return {"dispatch_id": dispatch_id, "ts": now, "action": action,
            "task_id": task_id}


def list_dispatch_history(
    conn: sqlite3.Connection, card_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    """Return the long-form dispatch history for a card, newest first."""
    rows = conn.execute(
        """
        SELECT * FROM mission_control_dispatch_history
        WHERE card_id = ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (card_id, max(1, min(int(limit), 200))),
    ).fetchall()
    return [dict(r) for r in rows]


def list_live_cards(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM mission_control_cards
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
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_ledger_cards(
    conn: sqlite3.Connection,
    *,
    subject_kind: str | None = None,
    min_recurrence: int = 0,
    state: str | None = None,
    since_iso: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return retired + archived cards (the Knowledge Ledger view).

    Phase 6 deliverable. Mirrors ``list_live_cards`` semantics for the
    inactive corpus.

    Filters (all optional):
      * ``subject_kind`` — restrict to one subject_kind (e.g. ``'failure_pattern'``).
      * ``min_recurrence`` — only cards with ``recurrence_count >= N`` (useful
        for "things that recur").
      * ``state`` — ``'retired'`` or ``'archived'``; default both.
      * ``since_iso`` — only cards last synthesized at or after this UTC ISO ts.
      * ``limit`` — hard cap (default 200; clamped 1..2000).
    """
    clauses: list[str] = []
    params: list[Any] = []

    if state in {CARD_STATE_RETIRED, CARD_STATE_ARCHIVED}:
        clauses.append("current_state = ?")
        params.append(state)
    else:
        clauses.append("current_state IN (?, ?)")
        params.extend([CARD_STATE_RETIRED, CARD_STATE_ARCHIVED])

    if subject_kind:
        clauses.append("subject_kind = ?")
        params.append(str(subject_kind))

    if min_recurrence and int(min_recurrence) > 0:
        clauses.append("recurrence_count >= ?")
        params.append(int(min_recurrence))

    if since_iso:
        clauses.append("last_synthesized_at >= ?")
        params.append(str(since_iso))

    where_sql = " AND ".join(clauses) if clauses else "1=1"
    bounded_limit = max(1, min(int(limit), 2000))

    rows = conn.execute(
        f"""
        SELECT *
        FROM mission_control_cards
        WHERE {where_sql}
        ORDER BY
          recurrence_count DESC,
          last_synthesized_at DESC,
          first_observed_at DESC
        LIMIT ?
        """,
        (*params, bounded_limit),
    ).fetchall()
    return [dict(row) for row in rows]


def ledger_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compact stats for the ledger landing header (totals, recurrence buckets)."""
    counts = {row["state"]: int(row["n"]) for row in conn.execute(
        """
        SELECT current_state AS state, COUNT(*) AS n
        FROM mission_control_cards
        WHERE current_state IN (?, ?)
        GROUP BY current_state
        """,
        (CARD_STATE_RETIRED, CARD_STATE_ARCHIVED),
    ).fetchall()}

    recurring_row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM mission_control_cards
        WHERE current_state IN (?, ?) AND recurrence_count >= 2
        """,
        (CARD_STATE_RETIRED, CARD_STATE_ARCHIVED),
    ).fetchone()

    last_retired_row = conn.execute(
        """
        SELECT MAX(last_synthesized_at) AS ts
        FROM mission_control_cards
        WHERE current_state = ?
        """,
        (CARD_STATE_RETIRED,),
    ).fetchone()

    return {
        "retired_count": int(counts.get(CARD_STATE_RETIRED, 0)),
        "archived_count": int(counts.get(CARD_STATE_ARCHIVED, 0)),
        "recurring_count": int(recurring_row["n"] or 0) if recurring_row else 0,
        "most_recent_retired_iso": (last_retired_row["ts"] if last_retired_row else None),
    }
