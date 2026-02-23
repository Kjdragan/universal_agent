#!/usr/bin/env python3
"""Adaptive quality loop for RSS category taxonomy."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.analytics import CATEGORY_STATE_KEY, canonicalize_category, ensure_taxonomy_state
from csi_ingester.analytics.emission import emit_and_track
from csi_ingester.config import load_config
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.store import source_state
from csi_ingester.store.sqlite import connect, ensure_schema

CORE_CATEGORIES = {"ai", "political", "war", "other_interest"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, raw = item.split("=", 1)
        val = raw.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key.strip()] = val
    return out


def _apply_env_defaults(path: Path) -> None:
    for key, val in _load_env_file(path).items():
        os.environ.setdefault(key, val)


def _retire_narrow_dynamic_category(state: dict[str, Any]) -> str:
    categories = state.get("categories")
    if not isinstance(categories, dict):
        return ""
    dynamic = []
    for slug, payload in categories.items():
        if not isinstance(payload, dict):
            continue
        if slug in CORE_CATEGORIES:
            continue
        if str(payload.get("kind") or "") != "dynamic":
            continue
        dynamic.append((slug, int(payload.get("count") or 0), str(payload.get("label") or slug)))
    if not dynamic:
        return ""
    dynamic.sort(key=lambda item: item[1])
    slug, count, label = dynamic[0]
    retired = state.get("retired_categories")
    if not isinstance(retired, list):
        retired = []
        state["retired_categories"] = retired
    retired.append({"slug": slug, "label": label, "count": count, "retired_at": _utc_now_iso()})
    del categories[slug]
    return slug


def _compute_metrics(conn: sqlite3.Connection, *, lookback_hours: int) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT category
        FROM rss_event_analysis
        WHERE analyzed_at >= datetime('now', ?)
        """,
        (f"-{max(1, int(lookback_hours))} hours",),
    ).fetchall()
    raw_counts: Counter[str] = Counter()
    canonical_counts: Counter[str] = Counter()
    uncategorized = 0

    for row in rows:
        raw = str(row["category"] or "").strip()
        normalized = canonicalize_category(raw or "other_interest")
        raw_counts[raw or "(empty)"] += 1
        canonical_counts[normalized] += 1
        if not raw or raw.lower() in {"unknown", "uncategorized", "uncategorised", "none", "null"}:
            uncategorized += 1

    total_items = len(rows)
    other_interest_items = int(canonical_counts.get("other_interest") or 0)
    other_ratio = float(other_interest_items / total_items) if total_items > 0 else 0.0
    return {
        "total_items": total_items,
        "other_interest_items": other_interest_items,
        "other_interest_ratio": other_ratio,
        "uncategorized_items": uncategorized,
        "by_category": dict(canonical_counts),
        "by_raw_category": dict(raw_counts),
    }


def _save_snapshot(
    conn: sqlite3.Connection,
    *,
    observed_at: str,
    metrics: dict[str, Any],
    dynamic_categories: int,
    new_category_min_topic_hits: int,
    action: str,
    notes: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO category_quality_snapshots (
            observed_at,
            total_items,
            other_interest_items,
            other_interest_ratio,
            dynamic_categories,
            uncategorized_items,
            new_category_min_topic_hits,
            action,
            notes_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observed_at,
            int(metrics.get("total_items") or 0),
            int(metrics.get("other_interest_items") or 0),
            float(metrics.get("other_interest_ratio") or 0.0),
            int(dynamic_categories),
            int(metrics.get("uncategorized_items") or 0),
            int(new_category_min_topic_hits),
            action,
            json.dumps(notes, separators=(",", ":"), sort_keys=True),
        ),
    )
    conn.commit()


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run adaptive category quality loop.")
    parser.add_argument("--db-path", default="/opt/universal_agent/CSI_Ingester/development/var/csi.db")
    parser.add_argument(
        "--state-path",
        default="/opt/universal_agent/CSI_Ingester/development/var/category_quality_loop_state.json",
    )
    parser.add_argument("--lookback-hours", type=int, default=72)
    parser.add_argument("--min-items", type=int, default=30)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--env-file", default="/opt/universal_agent/.env")
    parser.add_argument(
        "--csi-env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
    )
    args = parser.parse_args()

    _apply_env_defaults(Path(args.csi_env_file).expanduser())
    _apply_env_defaults(Path(args.env_file).expanduser())

    conn = connect(Path(args.db_path).expanduser())
    ensure_schema(conn)
    config = load_config()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    hour_key = now.strftime("%Y-%m-%dT%H:00:00Z")
    state_path = Path(args.state_path).expanduser()
    state_cursor = _load_state(state_path)
    if not args.force and state_cursor.get("last_hour_key") == hour_key:
        print(f"CATEGORY_QUALITY_SKIPPED hour={hour_key}")
        conn.close()
        return 0

    taxonomy_state = ensure_taxonomy_state(conn)
    categories = taxonomy_state.get("categories")
    if not isinstance(categories, dict):
        categories = {}
    dynamic_count = len([slug for slug in categories if slug not in CORE_CATEGORIES])
    max_categories = max(4, int(taxonomy_state.get("max_categories") or 10))
    threshold = max(5, int(taxonomy_state.get("new_category_min_topic_hits") or 8))

    metrics = _compute_metrics(conn, lookback_hours=max(1, int(args.lookback_hours)))
    total_items = int(metrics.get("total_items") or 0)
    other_ratio = float(metrics.get("other_interest_ratio") or 0.0)
    uncategorized_items = int(metrics.get("uncategorized_items") or 0)

    action = "no_change"
    notes: dict[str, Any] = {
        "reason": "default",
        "max_categories": max_categories,
        "dynamic_categories_before": dynamic_count,
    }
    state_changed = False

    if total_items >= max(1, int(args.min_items)):
        max_dynamic = max(0, max_categories - len(CORE_CATEGORIES))
        if other_ratio > 0.55 and dynamic_count < max_dynamic and threshold > 5:
            taxonomy_state["new_category_min_topic_hits"] = threshold - 1
            action = "lower_new_category_threshold"
            notes["reason"] = "other_interest_ratio_high_capacity_available"
            state_changed = True
        elif dynamic_count >= max_dynamic and other_ratio > 0.45:
            retired_slug = _retire_narrow_dynamic_category(taxonomy_state)
            if retired_slug:
                action = "retire_narrow_dynamic_category"
                notes["reason"] = "taxonomy_at_capacity_and_other_interest_high"
                notes["retired_slug"] = retired_slug
                state_changed = True
        elif other_ratio < 0.18 and uncategorized_items == 0 and threshold < 20:
            taxonomy_state["new_category_min_topic_hits"] = threshold + 1
            action = "raise_new_category_threshold"
            notes["reason"] = "taxonomy_stable_reduce_churn"
            state_changed = True
    else:
        action = "no_change_low_volume"
        notes["reason"] = "insufficient_items"

    if state_changed:
        taxonomy_state["updated_at"] = _utc_now_iso()
        source_state.set_state(conn, CATEGORY_STATE_KEY, taxonomy_state)
        threshold = int(taxonomy_state.get("new_category_min_topic_hits") or threshold)
        categories = taxonomy_state.get("categories") if isinstance(taxonomy_state, dict) else {}
        if not isinstance(categories, dict):
            categories = {}
        dynamic_count = len([slug for slug in categories if slug not in CORE_CATEGORIES])

    observed_at = _utc_now_iso()
    _save_snapshot(
        conn,
        observed_at=observed_at,
        metrics=metrics,
        dynamic_categories=dynamic_count,
        new_category_min_topic_hits=threshold,
        action=action,
        notes=notes,
    )

    event = CreatorSignalEvent(
        event_id=f"csi:category_quality:{config.instance_id}:{hour_key}:{uuid.uuid4().hex[:8]}",
        dedupe_key=f"csi:category_quality:{config.instance_id}:{hour_key}",
        source="csi_analytics",
        event_type="category_quality_report",
        occurred_at=observed_at,
        received_at=observed_at,
        subject={
            "report_type": "category_quality_loop",
            "hour_key": hour_key,
            "action": action,
            "metrics": metrics,
            "taxonomy": {
                "max_categories": max_categories,
                "dynamic_categories": dynamic_count,
                "new_category_min_topic_hits": threshold,
            },
            "notes": notes,
        },
        routing={"pipeline": "csi_category_quality", "priority": "standard", "tags": ["csi", "taxonomy", "quality"]},
        metadata={"source_adapter": "csi_category_quality_loop_v1"},
    )
    delivered, status_code, _payload = emit_and_track(conn, config=config, event=event, retry_count=3)

    _save_state(
        state_path,
        {
            "last_hour_key": hour_key,
            "last_action": action,
            "last_delivered": bool(delivered),
            "last_status_code": int(status_code),
            "updated_at": observed_at,
        },
    )

    conn.close()
    print(f"CATEGORY_QUALITY_ITEMS={total_items}")
    print(f"CATEGORY_QUALITY_ACTION={action}")
    print(f"CATEGORY_QUALITY_OTHER_RATIO={other_ratio:.4f}")
    print(f"CATEGORY_QUALITY_DYNAMIC={dynamic_count}")
    print(f"CATEGORY_QUALITY_THRESHOLD={threshold}")
    print(f"CATEGORY_QUALITY_EMIT_STATUS={status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
