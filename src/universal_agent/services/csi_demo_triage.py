"""CSI demo triage — SQLite-backed candidate store for tier 3+ Claude Code intel.

Single source of truth for all CSI actions awaiting a human decision. Replaces
the old auto-queue path (`queue_follow_up_tasks` invoked from cron / backfill),
which would have flooded Task Hub with ~120 historical candidates.

Discovery is wired in `claude_code_intel_replay.replay_packet()` — every replay
calls `sync_candidates_from_packet()` which `INSERT OR IGNORE`s every tier ≥ 3
action from `actions_refined.json` (or the original `actions.json`) into the
triage DB. The dashboard flyout reads `list_candidates()` and lets the operator
either approve a row (→ Task Hub via the shared `_build_followup_task_payload`
helper from `claude_code_intel`) or dismiss it.

After this lands, the ONLY path from "tweet identified" to "Cody/Atlas task
created" is the operator's approve click in the flyout.

Schema (SQLite):
    demo_triage_candidates(
      post_id              TEXT PRIMARY KEY,
      handle               TEXT,
      tier                 INTEGER,
      action_type          TEXT,
      post_url             TEXT,
      post_text            TEXT,
      summary              TEXT,
      linked_sources_json  TEXT,
      packet_dir           TEXT,
      first_seen_at        TEXT,
      state                TEXT  ('pending' | 'approved' | 'dismissed'),
      task_id              TEXT,
      decided_at           TEXT,
      decided_by           TEXT,
      ranking_score        REAL,
      ranking_rationale    TEXT,
      ranking_evaluated_at TEXT,
      ranking_run_id       TEXT
    )

DB path: <artifacts>/proactive/claude_code_intel/demo_triage.db
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.artifacts import resolve_artifacts_dir

logger = logging.getLogger(__name__)

DB_FILENAME = "demo_triage.db"
LANE_DIRNAME = "claude_code_intel"

STATE_PENDING = "pending"
STATE_APPROVED = "approved"
STATE_DISMISSED = "dismissed"
_VALID_STATES = {STATE_PENDING, STATE_APPROVED, STATE_DISMISSED}


# ── Path / connection helpers ────────────────────────────────────────────


def resolve_db_path(artifacts_root: Path | None = None) -> Path:
    """Return the canonical SQLite path under <artifacts>/proactive/claude_code_intel/."""
    root = (artifacts_root or resolve_artifacts_dir()) / "proactive" / LANE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root / DB_FILENAME


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the triage table + indexes."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS demo_triage_candidates (
          post_id              TEXT PRIMARY KEY,
          handle               TEXT NOT NULL,
          tier                 INTEGER NOT NULL,
          action_type          TEXT NOT NULL,
          post_url             TEXT NOT NULL DEFAULT '',
          post_text            TEXT NOT NULL DEFAULT '',
          summary              TEXT NOT NULL DEFAULT '',
          linked_sources_json  TEXT NOT NULL DEFAULT '[]',
          packet_dir           TEXT NOT NULL,
          first_seen_at        TEXT NOT NULL,
          state                TEXT NOT NULL DEFAULT 'pending',
          task_id              TEXT,
          decided_at           TEXT,
          decided_by           TEXT,
          ranking_score        REAL,
          ranking_rationale    TEXT,
          ranking_evaluated_at TEXT,
          ranking_run_id       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_state_score
          ON demo_triage_candidates(state, ranking_score DESC);
        CREATE INDEX IF NOT EXISTS idx_first_seen
          ON demo_triage_candidates(first_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tier_state
          ON demo_triage_candidates(tier, state);
        """
    )
    conn.commit()


def open_db(artifacts_root: Path | None = None) -> sqlite3.Connection:
    """Open the triage DB with row_factory + schema ensured."""
    path = resolve_db_path(artifacts_root)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Dataclass / row mapping ──────────────────────────────────────────────


@dataclass
class TriageCandidate:
    post_id: str
    handle: str
    tier: int
    action_type: str
    post_url: str
    post_text: str
    summary: str
    linked_sources: list[str]
    packet_dir: str
    first_seen_at: str
    state: str
    task_id: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    ranking_score: float | None = None
    ranking_rationale: str | None = None
    ranking_evaluated_at: str | None = None
    ranking_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "handle": self.handle,
            "tier": int(self.tier),
            "action_type": self.action_type,
            "post_url": self.post_url,
            "post_text": self.post_text,
            "summary": self.summary,
            "linked_sources": list(self.linked_sources or []),
            "packet_dir": self.packet_dir,
            "first_seen_at": self.first_seen_at,
            "state": self.state,
            "task_id": self.task_id,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "ranking_score": self.ranking_score,
            "ranking_rationale": self.ranking_rationale,
            "ranking_evaluated_at": self.ranking_evaluated_at,
            "ranking_run_id": self.ranking_run_id,
        }


def _row_to_candidate(row: sqlite3.Row | dict[str, Any]) -> TriageCandidate:
    raw_links = row["linked_sources_json"] if not isinstance(row, dict) else row.get("linked_sources_json")
    try:
        links = json.loads(raw_links) if raw_links else []
    except Exception:
        links = []
    if not isinstance(links, list):
        links = []
    score_raw = row["ranking_score"] if not isinstance(row, dict) else row.get("ranking_score")
    return TriageCandidate(
        post_id=str(row["post_id"]),
        handle=str(row["handle"] or ""),
        tier=int(row["tier"] or 0),
        action_type=str(row["action_type"] or ""),
        post_url=str(row["post_url"] or ""),
        post_text=str(row["post_text"] or ""),
        summary=str(row["summary"] or ""),
        linked_sources=[str(x) for x in links],
        packet_dir=str(row["packet_dir"] or ""),
        first_seen_at=str(row["first_seen_at"] or ""),
        state=str(row["state"] or STATE_PENDING),
        task_id=(str(row["task_id"]) if row["task_id"] else None),
        decided_at=(str(row["decided_at"]) if row["decided_at"] else None),
        decided_by=(str(row["decided_by"]) if row["decided_by"] else None),
        ranking_score=(float(score_raw) if score_raw is not None else None),
        ranking_rationale=(str(row["ranking_rationale"]) if row["ranking_rationale"] else None),
        ranking_evaluated_at=(str(row["ranking_evaluated_at"]) if row["ranking_evaluated_at"] else None),
        ranking_run_id=(str(row["ranking_run_id"]) if row["ranking_run_id"] else None),
    )


# ── Discovery: sync from a packet directory ──────────────────────────────


def _load_packet_actions(packet_dir: Path) -> tuple[list[dict[str, Any]], str]:
    """Prefer actions_refined.json, fall back to actions.json. Return (actions, handle)."""
    refined = packet_dir / "actions_refined.json"
    base = packet_dir / "actions.json"
    manifest = packet_dir / "manifest.json"
    actions: list[dict[str, Any]] = []
    src = refined if refined.exists() else base
    if src.exists():
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            if isinstance(data, list):
                actions = [a for a in data if isinstance(a, dict)]
        except Exception:
            logger.warning("triage: failed to parse %s", src, exc_info=True)
    handle = ""
    if manifest.exists():
        try:
            mf = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(mf, dict):
                handle = str(mf.get("handle") or "")
        except Exception:
            pass
    return actions, handle


def _summarize_action(action: dict[str, Any]) -> str:
    """Short human-friendly summary for the triage card."""
    classifier = action.get("classifier")
    if isinstance(classifier, dict):
        reasoning = str(classifier.get("reasoning") or "").strip()
        if reasoning:
            return reasoning[:480]
    text = str(action.get("text") or "").strip()
    if text:
        return text[:240]
    return ""


def sync_candidates_from_packet(
    *,
    packet_dir: Path,
    conn: sqlite3.Connection | None = None,
    artifacts_root: Path | None = None,
) -> dict[str, int]:
    """Insert (idempotently) every tier ≥ 3 action in the packet as 'pending'.

    Returns ``{'inserted': N, 'skipped': M}``. Safe to call multiple times
    per packet — uses ``INSERT OR IGNORE`` keyed on post_id.
    """
    own_conn = conn is None
    if conn is None:
        conn = open_db(artifacts_root)
    else:
        ensure_schema(conn)
    try:
        actions, packet_handle = _load_packet_actions(packet_dir)
        inserted = 0
        skipped = 0
        now = _now_iso()
        for action in actions:
            try:
                tier = int(action.get("tier") or 0)
            except Exception:
                tier = 0
            if tier < 3:
                continue
            post_id = str(action.get("post_id") or "").strip()
            if not post_id:
                continue
            handle = packet_handle or str(action.get("handle") or "")
            row = {
                "post_id": post_id,
                "handle": handle,
                "tier": tier,
                "action_type": str(action.get("action_type") or ""),
                "post_url": str(action.get("url") or ""),
                "post_text": str(action.get("text") or ""),
                "summary": _summarize_action(action),
                "linked_sources_json": json.dumps(
                    [str(u) for u in (action.get("links") or []) if u],
                    ensure_ascii=True,
                ),
                "packet_dir": str(packet_dir),
                "first_seen_at": now,
            }
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO demo_triage_candidates
                  (post_id, handle, tier, action_type, post_url, post_text, summary,
                   linked_sources_json, packet_dir, first_seen_at, state)
                VALUES
                  (:post_id, :handle, :tier, :action_type, :post_url, :post_text, :summary,
                   :linked_sources_json, :packet_dir, :first_seen_at, 'pending')
                """,
                row,
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        conn.commit()
        return {"inserted": inserted, "skipped": skipped}
    finally:
        if own_conn:
            conn.close()


# ── Read API ─────────────────────────────────────────────────────────────


def list_candidates(
    *,
    conn: sqlite3.Connection | None = None,
    artifacts_root: Path | None = None,
    state: str | None = None,
    tier: int | None = None,
    limit: int | None = None,
) -> list[TriageCandidate]:
    """Newest-first by first_seen_at. Optional state/tier filters."""
    own_conn = conn is None
    if conn is None:
        conn = open_db(artifacts_root)
    else:
        ensure_schema(conn)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if tier is not None:
            clauses.append("tier = ?")
            params.append(int(tier))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM demo_triage_candidates {where} ORDER BY first_seen_at DESC"
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_candidate(r) for r in rows]
    finally:
        if own_conn:
            conn.close()


# ── Feature-key extraction (for Top-N dedup) ─────────────────────────────

_URL_RE = re.compile(r"https?://\S+")
# Slash-commands like /ultrareview, /fewer-permission-prompts. Requires a
# non-word boundary before the "/" so URL paths previously stripped, plus
# inline literals like "use /loop" both match. Min length 3 avoids false
# positives on stray "/a" tokens.
_SLASH_RE = re.compile(r"(?:^|[^\w/])/([a-zA-Z][a-zA-Z0-9_-]{2,})")
# Long flags like --agent, --no-cache.
_FLAG_RE = re.compile(r"--([a-zA-Z][a-zA-Z0-9_-]{2,})")


def _extract_feature_key(post_text: str, summary: str = "", post_id: str = "") -> str:
    """Normalised dedup key for the Top-N panel.

    Picks the first slash-command, then the first long-flag, falling back to
    the post_id so posts with no canonical feature anchor each surface on
    their own (no silent collapse).
    """
    haystack = _URL_RE.sub(" ", f"{post_text}\n{summary}")
    m = _SLASH_RE.search(haystack)
    if m:
        return f"slash:{m.group(1).lower()}"
    m = _FLAG_RE.search(haystack)
    if m:
        return f"flag:{m.group(1).lower()}"
    return f"post:{post_id}"


def get_top_recommendations(
    *,
    conn: sqlite3.Connection | None = None,
    artifacts_root: Path | None = None,
    n: int = 5,
) -> list[TriageCandidate]:
    """Top-scoring pending candidates, deduped by extracted feature key.

    Posts referencing the same slash-command (e.g. ``/ultrareview``) or long
    flag (e.g. ``--agent``) collapse to the highest-scored representative
    so the panel surfaces N *independent* features. Posts with no
    recognisable command/flag anchor each get their own slot — they are not
    silently dropped.
    """
    own_conn = conn is None
    if conn is None:
        conn = open_db(artifacts_root)
    else:
        ensure_schema(conn)
    try:
        rows = conn.execute(
            """
            SELECT * FROM demo_triage_candidates
            WHERE state = 'pending' AND ranking_score IS NOT NULL
            ORDER BY ranking_score DESC, first_seen_at DESC
            """
        ).fetchall()
        target = max(1, int(n))
        seen_keys: set[str] = set()
        top: list[TriageCandidate] = []
        for r in rows:
            cand = _row_to_candidate(r)
            key = _extract_feature_key(cand.post_text, cand.summary, cand.post_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            top.append(cand)
            if len(top) >= target:
                break
        return top
    finally:
        if own_conn:
            conn.close()


def get_counts(
    *,
    conn: sqlite3.Connection | None = None,
    artifacts_root: Path | None = None,
) -> dict[str, int]:
    """High-level counts for the dashboard chips."""
    own_conn = conn is None
    if conn is None:
        conn = open_db(artifacts_root)
    else:
        ensure_schema(conn)
    try:
        result = {
            "pending": 0,
            "approved": 0,
            "dismissed": 0,
            "tier3_pending": 0,
            "tier4_pending": 0,
            "unranked_pending": 0,
        }
        for row in conn.execute(
            "SELECT state, COUNT(*) AS n FROM demo_triage_candidates GROUP BY state"
        ):
            state = str(row["state"] or "")
            if state in result:
                result[state] = int(row["n"] or 0)
        for row in conn.execute(
            """
            SELECT tier, COUNT(*) AS n FROM demo_triage_candidates
            WHERE state = 'pending' GROUP BY tier
            """
        ):
            tier = int(row["tier"] or 0)
            if tier == 3:
                result["tier3_pending"] = int(row["n"] or 0)
            elif tier == 4:
                result["tier4_pending"] = int(row["n"] or 0)
        unranked = conn.execute(
            """
            SELECT COUNT(*) AS n FROM demo_triage_candidates
            WHERE state = 'pending' AND ranking_score IS NULL
            """
        ).fetchone()
        if unranked is not None:
            result["unranked_pending"] = int(unranked["n"] or 0)
        return result
    finally:
        if own_conn:
            conn.close()


# ── Decision API: approve / dismiss / restore ────────────────────────────


def _get_one(conn: sqlite3.Connection, post_id: str) -> TriageCandidate | None:
    row = conn.execute(
        "SELECT * FROM demo_triage_candidates WHERE post_id = ?",
        (post_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_candidate(row)


def approve_candidate(
    *,
    post_id: str,
    decided_by: str = "kevin",
    conn: sqlite3.Connection | None = None,
    artifacts_root: Path | None = None,
    task_hub_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Approve a pending triage candidate.

    Builds the canonical Task Hub payload via
    ``claude_code_intel._build_followup_task_payload`` so it's identical to
    the legacy auto-queue path. Updates the row to state='approved' with
    ``task_id``, ``decided_at``, and ``decided_by``.

    Returns ``{'ok': True, 'task_id': '...', 'state': 'approved'}`` on
    success. If the candidate is already approved or dismissed, returns
    ``{'ok': False, 'reason': 'already_<state>'}`` without mutation.
    """
    own_conn = conn is None
    if conn is None:
        conn = open_db(artifacts_root)
    else:
        ensure_schema(conn)
    own_task_hub_conn = False
    try:
        candidate = _get_one(conn, post_id)
        if candidate is None:
            return {"ok": False, "reason": "not_found"}
        if candidate.state == STATE_APPROVED:
            return {
                "ok": False,
                "reason": "already_approved",
                "task_id": candidate.task_id,
                "state": candidate.state,
            }
        if candidate.state == STATE_DISMISSED:
            return {
                "ok": False,
                "reason": "already_dismissed",
                "state": candidate.state,
            }

        # Lazy import to avoid a circular dep at module load:
        # claude_code_intel imports from artifacts/task_hub directly,
        # but the helper closure brings in proactive_artifacts which is
        # heavy. Import inside the function so simple list/get/dismiss
        # paths don't pay that cost.
        from universal_agent.services.claude_code_intel import (
            _build_followup_task_payload,
        )

        action = {
            "post_id": candidate.post_id,
            "tier": candidate.tier,
            "action_type": candidate.action_type,
            "url": candidate.post_url,
            "text": candidate.post_text,
            "links": list(candidate.linked_sources),
        }
        payload = _build_followup_task_payload(
            handle=candidate.handle,
            packet_dir=Path(candidate.packet_dir),
            action=action,
            tier=candidate.tier,
            post_id=candidate.post_id,
        )

        if task_hub_conn is None:
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )

            task_hub_conn = connect_runtime_db(get_activity_db_path())
            task_hub_conn.row_factory = sqlite3.Row
            own_task_hub_conn = True

        task_hub.upsert_item(task_hub_conn, payload)
        task_id = str(payload["task_id"])
        now = _now_iso()
        conn.execute(
            """
            UPDATE demo_triage_candidates
               SET state = 'approved',
                   task_id = ?,
                   decided_at = ?,
                   decided_by = ?
             WHERE post_id = ?
            """,
            (task_id, now, decided_by, post_id),
        )
        conn.commit()
        return {"ok": True, "task_id": task_id, "state": STATE_APPROVED}
    finally:
        if own_task_hub_conn and task_hub_conn is not None:
            try:
                task_hub_conn.close()
            except Exception:
                pass
        if own_conn:
            conn.close()


def dismiss_candidate(
    *,
    post_id: str,
    decided_by: str = "kevin",
    conn: sqlite3.Connection | None = None,
    artifacts_root: Path | None = None,
) -> dict[str, Any]:
    """Mark a candidate dismissed. No-op if already dismissed; refuses if approved."""
    own_conn = conn is None
    if conn is None:
        conn = open_db(artifacts_root)
    else:
        ensure_schema(conn)
    try:
        candidate = _get_one(conn, post_id)
        if candidate is None:
            return {"ok": False, "reason": "not_found"}
        if candidate.state == STATE_APPROVED:
            return {
                "ok": False,
                "reason": "already_approved",
                "state": candidate.state,
            }
        if candidate.state == STATE_DISMISSED:
            return {"ok": True, "state": candidate.state, "noop": True}
        now = _now_iso()
        conn.execute(
            """
            UPDATE demo_triage_candidates
               SET state = 'dismissed',
                   decided_at = ?,
                   decided_by = ?
             WHERE post_id = ?
            """,
            (now, decided_by, post_id),
        )
        conn.commit()
        return {"ok": True, "state": STATE_DISMISSED}
    finally:
        if own_conn:
            conn.close()


def restore_candidate(
    *,
    post_id: str,
    conn: sqlite3.Connection | None = None,
    artifacts_root: Path | None = None,
) -> dict[str, Any]:
    """Reverse a dismissal. Refuses if the candidate was approved or never dismissed."""
    own_conn = conn is None
    if conn is None:
        conn = open_db(artifacts_root)
    else:
        ensure_schema(conn)
    try:
        candidate = _get_one(conn, post_id)
        if candidate is None:
            return {"ok": False, "reason": "not_found"}
        if candidate.state != STATE_DISMISSED:
            return {
                "ok": False,
                "reason": "not_dismissed",
                "state": candidate.state,
            }
        conn.execute(
            """
            UPDATE demo_triage_candidates
               SET state = 'pending',
                   decided_at = NULL,
                   decided_by = NULL
             WHERE post_id = ?
            """,
            (post_id,),
        )
        conn.commit()
        return {"ok": True, "state": STATE_PENDING}
    finally:
        if own_conn:
            conn.close()
