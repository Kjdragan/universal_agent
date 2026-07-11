"""stale_proposal_reaper.py — weekly cron that auto-prunes aged reflection/brainstorm
proposals from the Task Hub reflection lane.

Parks (NEVER deletes) open ``source_kind IN ('reflection','brainstorm')`` items older
than ``UA_STALE_PROPOSAL_REAPER_MAX_AGE_DAYS`` (default 14) via
``task_hub.perform_task_action(action="park")``.

GATE (non-negotiable): never prunes items with ``priority >= 2`` OR a ``human-only``
label — those are logged as ``disposition="skipped"`` (protected) and left untouched.
The protected check reuses ``ideation_report.is_protected_proposal`` so the report's
"protected" badge and the reaper's skip rule are the SAME rule.

Emits a digest to ``<cron-workspace>/work_products/stale_proposal_reaper_<YYYYMMDD>.md``
AND ``.json`` (fields: id, title, source_kind, created_at, age, age_hours,
disposition, reason) so nothing is lost silently.

Usage in cron_jobs.json:
  "command": "!script universal_agent.scripts.stale_proposal_reaper"

Fixed-time weekly cron (Sun 07:00 CT) — fixed-time crons are exempt from dormancy.
Registered with ``lightweight=True``: pure sqlite3 + file I/O, no agent session.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any

from universal_agent import task_hub
from universal_agent.services.ideation_report import (
    is_protected_proposal,
    parse_created_at,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 14
REAPER_AGENT_ID = "stale_proposal_reaper"
_PROTECT_REASON_PRIORITY = "skipped (protected: priority>=2)"
_PROTECT_REASON_HUMAN_ONLY = "skipped (protected: human-only label)"
_PROTECT_REASON_BOTH = "skipped (protected: priority>=2 + human-only label)"


def _max_age_days() -> int:
    raw = (os.getenv("UA_STALE_PROPOSAL_REAPER_MAX_AGE_DAYS") or "").strip()
    if not raw:
        return DEFAULT_MAX_AGE_DAYS
    try:
        val = int(raw)
        return val if val > 0 else DEFAULT_MAX_AGE_DAYS
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_DAYS


def get_reapable_proposals(
    conn: sqlite3.Connection, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS
) -> list[dict[str, Any]]:
    """Open reflection/brainstorm items older than ``max_age_days``, oldest first.

    Age is computed by parsing ``created_at`` (UTC ISO-8601 from
    ``task_hub._now_iso``) in Python so the filter is robust to any timestamp
    format drift — SQL lexicographic comparison would require uniform formatting.
    """
    task_hub.ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT task_id, title, source_kind, priority, labels_json, created_at
        FROM task_hub_items
        WHERE source_kind IN ('reflection', 'brainstorm')
          AND status = 'open'
        ORDER BY created_at ASC
        """,
    ).fetchall()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out: list[dict[str, Any]] = []
    for r in rows:
        created = parse_created_at(str(r["created_at"])) if r["created_at"] else None
        if created is None or created >= cutoff:
            continue
        out.append(dict(r))
    return out


def _labels(labels_json: str | None) -> list[str]:
    try:
        val = json.loads(labels_json or "[]")
        return [str(x) for x in val] if isinstance(val, list) else []
    except Exception:
        return []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _protect_reason(item: dict[str, Any]) -> str:
    has_priority = _safe_int(item.get("priority"), 0) >= 2
    labels = _labels(item.get("labels_json"))
    has_human_only = "human-only" in {str(lbl).strip().lower() for lbl in labels}
    if has_priority and has_human_only:
        return _PROTECT_REASON_BOTH
    if has_priority:
        return _PROTECT_REASON_PRIORITY
    return _PROTECT_REASON_HUMAN_ONLY


def _age_str(created_at_raw: str) -> tuple[str, float]:
    """Return (human-readable age, age_hours). '—' / 0.0 when unparseable."""
    created = parse_created_at(created_at_raw)
    if created is None:
        return "—", 0.0
    hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
    days = hours / 24.0
    if days >= 1.0:
        return f"{days:.1f} days", round(hours, 1)
    return f"{hours:.1f} hours", round(hours, 1)


def reap_stale_proposals(
    conn: sqlite3.Connection,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Park eligible stale proposals; skip protected ones. Returns digest records.

    Each record: {id, title, source_kind, created_at, age, age_hours,
    disposition, reason}. Never deletes rows — ``action="park"`` only.
    """
    records: list[dict[str, Any]] = []
    for item in get_reapable_proposals(conn, max_age_days=max_age_days):
        task_id = str(item.get("task_id") or "")
        title = str(item.get("title") or "Untitled proposal")
        source_kind = str(item.get("source_kind") or "")
        created_at = str(item.get("created_at") or "")
        age, age_hours = _age_str(created_at)
        base_record = {
            "id": task_id,
            "title": title,
            "source_kind": source_kind,
            "created_at": created_at,
            "age": age,
            "age_hours": age_hours,
        }
        if is_protected_proposal(item):
            records.append({**base_record, "disposition": "skipped", "reason": _protect_reason(item)})
            continue
        if dry_run:
            records.append({**base_record, "disposition": "pruned", "reason": "dry_run: would park"})
            continue
        try:
            task_hub.perform_task_action(
                conn,
                task_id=task_id,
                action="park",
                agent_id=REAPER_AGENT_ID,
                reason=f"auto-prune: open {source_kind} proposal older than {max_age_days}d",
            )
            conn.commit()
            records.append({
                **base_record,
                "disposition": "pruned",
                "reason": f"auto-prune: open {source_kind} proposal older than {max_age_days}d",
            })
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the run
            logger.error("stale_proposal_reaper: park failed for %s: %s", task_id, exc, exc_info=True)
            records.append({**base_record, "disposition": "skipped", "reason": f"error: {exc}"})
    return records


def _digest_date_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def write_digest(
    records: list[dict[str, Any]],
    out_dir: Path,
    *,
    date_str: str | None = None,
) -> tuple[Path, Path]:
    """Write the pruned-proposals digest as both ``.md`` and ``.json``.

    Returns ``(md_path, json_path)``. ``out_dir`` is created if missing.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ds = date_str or _digest_date_str()
    md_path = out_dir / f"stale_proposal_reaper_{ds}.md"
    json_path = out_dir / f"stale_proposal_reaper_{ds}.json"

    pruned = [r for r in records if r.get("disposition") == "pruned"]
    skipped = [r for r in records if r.get("disposition") == "skipped"]

    json_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": ds,
        "total": len(records),
        "pruned": len(pruned),
        "skipped": len(skipped),
        "records": records,
    }, indent=2), encoding="utf-8")

    lines = [
        "# Stale Proposal Reaper Digest",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Total scanned: {len(records)}  ·  Pruned: {len(pruned)}  ·  Skipped (protected/error): {len(skipped)}",
        "",
        "## Pruned (parked)",
        "",
    ]
    if pruned:
        for r in pruned:
            lines.append(f"- **{r['title']}** (`{r['id']}`) — {r['source_kind']} · age {r['age']} · {r['reason']}")
    else:
        lines.append("_(none)_")
    lines += ["", "## Skipped (protected)", ""]
    if skipped:
        for r in skipped:
            lines.append(f"- **{r['title']}** (`{r['id']}`) — {r['source_kind']} · age {r['age']} · {r['reason']}")
    else:
        lines.append("_(none)_")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


def resolve_work_products_dir() -> Path:
    """Resolve the cron-workspace ``work_products`` dir for digest output."""
    env = (os.getenv("UA_WORKSPACES_DIR") or "").strip()
    if env:
        base = Path(env).expanduser()
    else:
        try:
            from universal_agent.artifacts import repo_root
            base = repo_root() / "AGENT_RUN_WORKSPACES"
        except Exception:  # noqa: BLE001 — fall back to a sibling of this file
            base = Path(__file__).resolve().parents[3] / "AGENT_RUN_WORKSPACES"
    return base / "cron_stale_proposal_reaper" / "work_products"


async def _run() -> dict[str, Any]:
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.infisical_loader import initialize_runtime_secrets

    initialize_runtime_secrets()
    db_path = os.getenv("UA_DB_PATH", "") or get_activity_db_path()
    conn = connect_runtime_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        max_age = _max_age_days()
        records = reap_stale_proposals(conn, max_age_days=max_age)
        md_path, json_path = write_digest(records, resolve_work_products_dir())
        summary = {
            "status": "ok",
            "max_age_days": max_age,
            "total": len(records),
            "pruned": sum(1 for r in records if r.get("disposition") == "pruned"),
            "skipped": sum(1 for r in records if r.get("disposition") == "skipped"),
            "digest_md": str(md_path),
            "digest_json": str(json_path),
        }
        logger.info("stale_proposal_reaper: %s", summary)
        return summary
    except Exception as exc:  # noqa: BLE001 — one-shot cron: surface the failure
        logger.error("stale_proposal_reaper failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


def main() -> dict[str, Any]:
    return asyncio.run(_run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Stale proposal reaper result: {main()}")
