#!/usr/bin/env python3
"""Verify Threads Phase-2 publish canary health from JSONL audit history."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _error_signature(detail: str) -> str:
    text = str(detail or "").strip()
    if not text:
        return "unknown"
    # Keep this narrow so reports remain compact.
    for marker in ('"code":', " code:", "code="):
        idx = text.find(marker)
        if idx >= 0:
            tail = text[idx : idx + 24]
            return tail
    return text[:80]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-path",
        default=os.getenv("CSI_THREADS_PUBLISH_AUDIT_PATH", "/var/lib/universal-agent/csi/threads_publishing_audit.jsonl"),
    )
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--min-records", type=int, default=1)
    parser.add_argument("--max-error-rate", type=float, default=0.5)
    parser.add_argument("--require-live-ok", action="store_true")
    parser.add_argument("--write-json", default="")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    start = _utc_now() - timedelta(hours=max(1, int(args.lookback_hours)))
    rows = _read_jsonl(Path(args.audit_path).expanduser())

    in_window: list[dict[str, Any]] = []
    for row in rows:
        occurred = _parse_iso(row.get("occurred_at_utc"))
        if occurred is None or occurred < start:
            continue
        in_window.append(row)

    by_status: Counter[str] = Counter()
    by_operation: Counter[str] = Counter()
    error_signatures: Counter[str] = Counter()
    approval_refs: set[str] = set()
    latest_seen: datetime | None = None

    for row in in_window:
        status = str(row.get("status") or "unknown").strip().lower() or "unknown"
        operation = str(row.get("operation") or "unknown").strip().lower() or "unknown"
        by_status[status] += 1
        by_operation[operation] += 1
        approval = str(row.get("approval_ref") or "").strip()
        if approval:
            approval_refs.add(approval)
        occurred = _parse_iso(row.get("occurred_at_utc"))
        if occurred is not None and (latest_seen is None or occurred > latest_seen):
            latest_seen = occurred
        if status == "error":
            error_signatures[_error_signature(str(row.get("detail") or ""))] += 1

    total = int(len(in_window))
    ok_count = int(by_status.get("ok", 0))
    dry_count = int(by_status.get("dry_run", 0))
    error_count = int(by_status.get("error", 0))
    error_rate = float(error_count / total) if total > 0 else 0.0

    failures: list[str] = []
    warnings: list[str] = []

    if total < max(0, int(args.min_records)):
        failures.append("insufficient_audit_records")
    if bool(args.require_live_ok) and ok_count <= 0:
        failures.append("no_live_ok_records")
    if total > 0 and error_rate > max(0.0, float(args.max_error_rate)):
        failures.append("error_rate_exceeds_max")
    if ok_count <= 0 and dry_count > 0:
        warnings.append("dry_run_only_window")
    if error_count > 0:
        warnings.append("live_errors_present")

    payload = {
        "verified_at_utc": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "audit_path": str(Path(args.audit_path).expanduser()),
        "lookback_hours": int(max(1, int(args.lookback_hours))),
        "counts": {
            "total": total,
            "ok": ok_count,
            "dry_run": dry_count,
            "error": error_count,
        },
        "error_rate": round(error_rate, 4),
        "operations": dict(by_operation),
        "unique_approval_refs": int(len(approval_refs)),
        "latest_audit_utc": latest_seen.strftime("%Y-%m-%dT%H:%M:%SZ") if latest_seen else "",
        "top_error_signatures": [
            {"signature": sig, "count": int(count)}
            for sig, count in error_signatures.most_common(5)
        ],
        "warnings": warnings,
        "failures": failures,
        "ok_status": len(failures) == 0,
    }

    if not bool(args.quiet):
        print(f"THREADS_PUBLISH_CANARY_OK={1 if payload['ok_status'] else 0}")
        print(f"THREADS_PUBLISH_CANARY_FAILURES={len(failures)}")
        print(f"THREADS_PUBLISH_CANARY_WARNINGS={len(warnings)}")
        print("THREADS_PUBLISH_CANARY_COUNTS=" + json.dumps(payload["counts"], sort_keys=True))
        print(f"THREADS_PUBLISH_CANARY_ERROR_RATE={payload['error_rate']}")
        if failures:
            print("THREADS_PUBLISH_CANARY_FAILURE_LIST=" + ",".join(failures))
        if warnings:
            print("THREADS_PUBLISH_CANARY_WARNING_LIST=" + ",".join(warnings))

    if str(args.write_json or "").strip():
        out_path = Path(str(args.write_json)).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        if not bool(args.quiet):
            print(f"THREADS_PUBLISH_CANARY_JSON={out_path}")

    return 0 if payload["ok_status"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
