#!/usr/bin/env python3
"""Reclassify existing RSS analysis rows using adaptive taxonomy."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics.categories import (
    classify_and_update_category,
    normalize_existing_analysis_categories,
    reset_taxonomy_state,
)
from csi_ingester.store.sqlite import connect, ensure_schema


def _select_rows(conn, *, max_rows: int, since_hours: int) -> list[Any]:
    params: list[Any] = []
    where = ""
    if since_hours > 0:
        where = "WHERE analyzed_at >= datetime('now', ?)"
        params.append(f"-{int(since_hours)} hours")
    params.append(max(1, int(max_rows)))
    query = f"""
        SELECT
            id,
            event_id,
            channel_name,
            title,
            summary_text,
            category,
            analysis_json,
            analyzed_at
        FROM rss_event_analysis
        {where}
        ORDER BY id ASC
        LIMIT ?
    """
    return conn.execute(query, tuple(params)).fetchall()


def _parse_analysis(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _extract_themes(payload: dict[str, Any]) -> list[str]:
    themes = payload.get("themes")
    out: list[str] = []
    if isinstance(themes, list):
        out.extend(str(item).strip() for item in themes if str(item).strip())
    claude_block = payload.get("claude")
    if isinstance(claude_block, dict):
        claude_themes = claude_block.get("themes")
        if isinstance(claude_themes, list):
            out.extend(str(item).strip() for item in claude_themes if str(item).strip())
    dedup: list[str] = []
    seen: set[str] = set()
    for item in out:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return dedup[:24]


def main() -> int:
    parser = argparse.ArgumentParser(description="Reclassify existing RSS analysis rows with adaptive taxonomy.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument("--max-rows", type=int, default=1200)
    parser.add_argument("--since-hours", type=int, default=0, help="0 means all rows")
    parser.add_argument("--max-categories", type=int, default=10)
    parser.add_argument("--reset-taxonomy", action="store_true")
    args = parser.parse_args()

    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)
    normalize_existing_analysis_categories(conn)

    if args.reset_taxonomy:
        state = reset_taxonomy_state(conn, max_categories=max(4, int(args.max_categories)))
        print(f"RSS_RECLASSIFY_TAXONOMY_RESET=1 categories={len(state.get('categories', {}))}")

    rows = _select_rows(
        conn,
        max_rows=max(1, int(args.max_rows)),
        since_hours=max(0, int(args.since_hours)),
    )
    if not rows:
        print("RSS_RECLASSIFY_ROWS=0")
        conn.close()
        return 0

    changed = 0
    unchanged = 0
    counts: Counter[str] = Counter()

    for row in rows:
        row_id = int(row["id"])
        old_category = str(row["category"] or "").strip().lower()
        payload = _parse_analysis(str(row["analysis_json"] or "{}"))
        confidence = payload.get("confidence")
        try:
            conf_val = float(confidence)
        except Exception:
            conf_val = 0.5
        suggested = str(payload.get("suggested_category") or payload.get("category") or old_category).strip()
        themes = _extract_themes(payload)
        summary_text = str(row["summary_text"] or "")
        title = str(row["title"] or "")
        channel_name = str(row["channel_name"] or "")

        new_category, taxonomy_state = classify_and_update_category(
            conn,
            suggested_category=suggested,
            title=title,
            channel_name=channel_name,
            summary_text=summary_text,
            transcript_text="",
            themes=themes,
            confidence=conf_val,
            max_categories=max(4, int(args.max_categories)),
        )
        payload["category"] = new_category
        payload["taxonomy_categories"] = sorted(list((taxonomy_state.get("categories") or {}).keys()))
        payload["taxonomy_total"] = int(taxonomy_state.get("total_classified") or 0)

        conn.execute(
            "UPDATE rss_event_analysis SET category = ?, analysis_json = ? WHERE id = ?",
            (new_category, json.dumps(payload, separators=(",", ":"), sort_keys=True), row_id),
        )
        if new_category != old_category:
            changed += 1
        else:
            unchanged += 1
        counts[new_category] += 1

    conn.commit()
    conn.close()

    print(f"RSS_RECLASSIFY_ROWS={len(rows)}")
    print(f"RSS_RECLASSIFY_CHANGED={changed}")
    print(f"RSS_RECLASSIFY_UNCHANGED={unchanged}")
    for slug, count in sorted(counts.items()):
        metric = slug.upper().replace("-", "_")
        print(f"RSS_RECLASSIFY_CATEGORY_{metric}={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
