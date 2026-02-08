#!/usr/bin/env python3
"""Scheduling Runtime V2 soak checker.

Runs a repeated set of API checks and writes a JSON report with latencies,
status codes, and pass/fail summary.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class CheckSpec:
    name: str
    path: str
    query: dict[str, str] | None = None
    method: str = "GET"
    expects_json: bool = True


CHECKS: list[CheckSpec] = [
    CheckSpec(name="health", path="/api/v1/health"),
    CheckSpec(name="scheduling_runtime_metrics", path="/api/v1/ops/metrics/scheduling-runtime"),
    CheckSpec(name="session_continuity_metrics", path="/api/v1/ops/metrics/session-continuity"),
    CheckSpec(
        name="calendar_events",
        path="/api/v1/ops/calendar/events",
        query={"source": "all", "view": "week"},
    ),
    CheckSpec(
        name="scheduling_replay",
        path="/api/v1/ops/scheduling/events",
        query={"since_seq": "0", "limit": "50"},
    ),
    CheckSpec(
        name="scheduling_stream_once",
        path="/api/v1/ops/scheduling/stream",
        query={"since_seq": "0", "once": "1"},
        expects_json=False,
    ),
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_url(base_url: str, spec: CheckSpec) -> str:
    base = base_url.rstrip("/")
    path = spec.path if spec.path.startswith("/") else f"/{spec.path}"
    if not spec.query:
        return f"{base}{path}"
    return f"{base}{path}?{urlencode(spec.query)}"


def _http_request(url: str, timeout_seconds: float, ops_token: str | None) -> tuple[int, float, str]:
    headers: dict[str, str] = {}
    if ops_token:
        headers["X-UA-OPS-TOKEN"] = ops_token
    req = Request(url=url, headers=headers, method="GET")
    started = time.perf_counter()
    with urlopen(req, timeout=timeout_seconds) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        latency_ms = (time.perf_counter() - started) * 1000.0
        return int(resp.status), latency_ms, body


def _run_check(base_url: str, spec: CheckSpec, timeout_seconds: float, ops_token: str | None) -> dict[str, Any]:
    url = _build_url(base_url, spec)
    started_at = _iso_now()
    try:
        status_code, latency_ms, body = _http_request(url, timeout_seconds, ops_token)
        parsed: Any = None
        if spec.expects_json:
            try:
                parsed = json.loads(body or "{}")
            except Exception:
                parsed = {"_parse_error": True, "raw_preview": body[:300]}
            if isinstance(parsed, dict):
                parsed = _compact_payload(spec.name, parsed)
        return {
            "name": spec.name,
            "url": url,
            "started_at": started_at,
            "status_code": status_code,
            "latency_ms": round(latency_ms, 3),
            "ok": 200 <= status_code < 300,
            "payload_preview": parsed if parsed is not None else body[:300],
        }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        return {
            "name": spec.name,
            "url": url,
            "started_at": started_at,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "latency_ms": None,
            "ok": False,
            "error": f"http_error:{exc}",
            "payload_preview": body[:300],
        }
    except URLError as exc:
        return {
            "name": spec.name,
            "url": url,
            "started_at": started_at,
            "status_code": 0,
            "latency_ms": None,
            "ok": False,
            "error": f"url_error:{exc}",
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "name": spec.name,
            "url": url,
            "started_at": started_at,
            "status_code": 0,
            "latency_ms": None,
            "ok": False,
            "error": f"unexpected:{exc}",
        }


def _compact_payload(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if name == "calendar_events":
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        always_running = payload.get("always_running") if isinstance(payload.get("always_running"), list) else []
        stasis_queue = payload.get("stasis_queue") if isinstance(payload.get("stasis_queue"), list) else []
        return {
            "keys": sorted(list(payload.keys()))[:25],
            "events_count": len(events),
            "always_running_count": len(always_running),
            "stasis_count": len(stasis_queue),
            "timezone": payload.get("timezone"),
            "view": payload.get("view"),
        }
    if name == "scheduling_runtime_metrics":
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        counters = metrics.get("counters") if isinstance(metrics.get("counters"), dict) else {}
        projection_state = metrics.get("projection_state") if isinstance(metrics.get("projection_state"), dict) else {}
        return {
            "keys": sorted(list(payload.keys()))[:25],
            "projection_state": {
                "enabled": projection_state.get("enabled"),
                "seeded": projection_state.get("seeded"),
                "version": projection_state.get("version"),
                "last_event_seq": projection_state.get("last_event_seq"),
            },
            "push_counters": {
                "push_replay_requests": counters.get("push_replay_requests"),
                "push_stream_connects": counters.get("push_stream_connects"),
                "push_stream_disconnects": counters.get("push_stream_disconnects"),
                "push_stream_event_payloads": counters.get("push_stream_event_payloads"),
                "push_stream_keepalives": counters.get("push_stream_keepalives"),
            },
        }
    if name == "scheduling_replay":
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        return {
            "keys": sorted(list(payload.keys()))[:25],
            "events_count": len(events),
            "event_bus_seq": payload.get("event_bus_seq"),
            "projection_version": payload.get("projection_version"),
            "projection_last_event_seq": payload.get("projection_last_event_seq"),
        }
    if name == "session_continuity_metrics":
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        return {
            "keys": sorted(list(payload.keys()))[:25],
            "continuity_status": metrics.get("continuity_status"),
            "resume_success_rate": metrics.get("resume_success_rate"),
            "attach_success_rate": metrics.get("attach_success_rate"),
            "alerts_count": len(metrics.get("alerts") or []),
        }
    return {
        "keys": sorted(list(payload.keys()))[:25],
    }


def run_soak(
    *,
    base_url: str,
    duration_seconds: int,
    interval_seconds: int,
    timeout_seconds: float,
    ops_token: str | None,
) -> dict[str, Any]:
    started_at = _iso_now()
    end_ts = time.time() + max(1, int(duration_seconds))
    checks_per_cycle: list[dict[str, Any]] = []
    cycle = 0
    while time.time() < end_ts:
        cycle_started = time.time()
        cycle_results: list[dict[str, Any]] = []
        for spec in CHECKS:
            cycle_results.append(
                _run_check(
                    base_url=base_url,
                    spec=spec,
                    timeout_seconds=timeout_seconds,
                    ops_token=ops_token,
                )
            )
        checks_per_cycle.append(
            {
                "cycle": cycle,
                "at": _iso_now(),
                "results": cycle_results,
            }
        )
        cycle += 1
        elapsed = time.time() - cycle_started
        sleep_for = max(0.0, float(interval_seconds) - elapsed)
        if sleep_for > 0 and time.time() < end_ts:
            time.sleep(sleep_for)

    ended_at = _iso_now()
    flat = [result for cycle_data in checks_per_cycle for result in cycle_data["results"]]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in flat:
        by_name.setdefault(str(item.get("name")), []).append(item)

    endpoint_summary: dict[str, Any] = {}
    for name, rows in by_name.items():
        latencies = [float(r["latency_ms"]) for r in rows if isinstance(r.get("latency_ms"), (int, float))]
        ok_count = sum(1 for r in rows if bool(r.get("ok")))
        endpoint_summary[name] = {
            "samples": len(rows),
            "ok": ok_count,
            "fail": len(rows) - ok_count,
            "latency_ms_avg": round(sum(latencies) / len(latencies), 3) if latencies else None,
            "latency_ms_max": round(max(latencies), 3) if latencies else None,
        }

    total_checks = len(flat)
    total_ok = sum(1 for item in flat if bool(item.get("ok")))
    return {
        "started_at": started_at,
        "ended_at": ended_at,
        "base_url": base_url,
        "duration_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "timeout_seconds": timeout_seconds,
        "checks_per_cycle": checks_per_cycle,
        "summary": {
            "cycles": len(checks_per_cycle),
            "total_checks": total_checks,
            "total_ok": total_ok,
            "total_fail": total_checks - total_ok,
            "all_checks_ok": total_ok == total_checks and total_checks > 0,
            "by_endpoint": endpoint_summary,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Scheduling Runtime V2 soak checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--duration-seconds", type=int, default=24 * 3600)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--ops-token", default=None)
    parser.add_argument(
        "--out-json",
        default="OFFICIAL_PROJECT_DOCUMENTATION/03_Run_Reviews/scheduling_v2_soak_latest.json",
    )
    args = parser.parse_args()

    report = run_soak(
        base_url=args.base_url,
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        timeout_seconds=args.timeout_seconds,
        ops_token=args.ops_token,
    )
    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = report.get("summary", {})
    print(
        json.dumps(
            {
                "out_json": str(out_path),
                "cycles": summary.get("cycles"),
                "total_checks": summary.get("total_checks"),
                "total_fail": summary.get("total_fail"),
                "all_checks_ok": summary.get("all_checks_ok"),
            },
            indent=2,
        )
    )
    return 0 if summary.get("all_checks_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
