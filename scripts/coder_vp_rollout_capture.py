from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RolloutAssessment:
    decision: str
    reasons: list[str]


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_dotenv_token(text: str, key: str = "UA_OPS_TOKEN") -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        lhs, rhs = line.split("=", 1)
        if lhs.strip() != key:
            continue
        value = rhs.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value.strip()
    return ""


def _resolve_ops_token(cli_token: str) -> str:
    cli_value = (cli_token or "").strip()
    if cli_value:
        return cli_value

    env_value = (os.getenv("UA_OPS_TOKEN") or "").strip()
    if env_value:
        return env_value

    dotenv_path = Path(".env")
    if dotenv_path.exists() and dotenv_path.is_file():
        try:
            return _extract_dotenv_token(dotenv_path.read_text(encoding="utf-8"), key="UA_OPS_TOKEN")
        except Exception:
            return ""
    return ""


def _fetch_snapshot_http(
    gateway_url: str,
    vp_id: str,
    mission_limit: int,
    event_limit: int,
    ops_token: str,
) -> dict[str, Any]:
    base = gateway_url.rstrip("/")
    params = urlencode(
        {
            "vp_id": vp_id,
            "mission_limit": mission_limit,
            "event_limit": event_limit,
        }
    )
    url = f"{base}/api/v1/ops/metrics/coder-vp?{params}"
    headers: dict[str, str] = {"accept": "application/json"}
    if ops_token:
        headers["x-ua-ops-token"] = ops_token
        headers["authorization"] = f"Bearer {ops_token}"

    req = Request(url=url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=30) as resp:  # nosec B310 - operator-targeted local endpoint fetch
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            if body.strip():
                detail = f" Body: {body.strip()}"
        except Exception:
            detail = ""
        raise RuntimeError(
            f"HTTP request failed ({exc.code}) for {url}.{detail}"
            + (" Provide --ops-token or set UA_OPS_TOKEN for protected endpoints." if exc.code in {401, 403} else "")
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach gateway endpoint {url}: {exc.reason}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected response payload from ops endpoint")
    return payload


def _fetch_snapshot_direct(
    vp_id: str,
    mission_limit: int,
    event_limit: int,
) -> dict[str, Any]:
    from universal_agent import gateway_server

    snapshot = gateway_server._vp_metrics_snapshot(
        vp_id=vp_id,
        mission_limit=mission_limit,
        event_limit=event_limit,
    )
    if not isinstance(snapshot, dict):
        raise RuntimeError("Unexpected response payload from direct snapshot")
    return snapshot


def _assess(snapshot: dict[str, Any]) -> RolloutAssessment:
    return _assess_with_profile(snapshot)


def _assess_with_profile(
    snapshot: dict[str, Any],
    *,
    assessment_profile: str = "rollout",
    min_missions: int = 1,
    max_fallback_watch: float = 0.05,
    max_fallback_critical: float = 0.20,
    max_p95_watch_seconds: float = 45.0,
    max_p95_critical_seconds: float = 90.0,
) -> RolloutAssessment:
    fallback = snapshot.get("fallback") if isinstance(snapshot.get("fallback"), dict) else {}
    latency = snapshot.get("latency_seconds") if isinstance(snapshot.get("latency_seconds"), dict) else {}

    missions_considered = int(fallback.get("missions_considered") or 0)
    fallback_rate = _as_float(fallback.get("rate"))
    p95 = _as_float(latency.get("p95_seconds"))

    reasons: list[str] = []
    if missions_considered < max(1, int(min_missions)):
        reasons.append("no mission traffic in observation window")
        if assessment_profile == "sustained":
            return RolloutAssessment(decision="SUSTAINED_MONITOR_NO_TRAFFIC", reasons=reasons)
        return RolloutAssessment(decision="HOLD_SHADOW", reasons=reasons)

    if fallback_rate is None:
        reasons.append("fallback rate unavailable")
        if assessment_profile == "sustained":
            return RolloutAssessment(decision="SUSTAINED_WATCH", reasons=reasons)
        return RolloutAssessment(decision="HOLD_SHADOW", reasons=reasons)

    if fallback_rate >= max_fallback_critical:
        reasons.append(f"fallback rate high ({fallback_rate:.3f} >= {max_fallback_critical:.3f})")
        if assessment_profile == "sustained":
            return RolloutAssessment(decision="SUSTAINED_FORCE_FALLBACK", reasons=reasons)
        return RolloutAssessment(decision="HOLD_SHADOW", reasons=reasons)

    if fallback_rate >= max_fallback_watch:
        reasons.append(f"fallback rate elevated ({fallback_rate:.3f} >= {max_fallback_watch:.3f})")
        if assessment_profile == "sustained":
            return RolloutAssessment(decision="SUSTAINED_WATCH", reasons=reasons)
        return RolloutAssessment(decision="HOLD_SHADOW", reasons=reasons)

    if p95 is None:
        reasons.append("p95 latency unavailable")
        if assessment_profile == "sustained":
            return RolloutAssessment(decision="SUSTAINED_WATCH", reasons=reasons)
        return RolloutAssessment(decision="HOLD_SHADOW", reasons=reasons)

    if assessment_profile == "sustained":
        if p95 >= max_p95_critical_seconds:
            reasons.append(f"p95 latency high ({p95:.3f}s >= {max_p95_critical_seconds:.3f}s)")
            return RolloutAssessment(decision="SUSTAINED_FORCE_FALLBACK", reasons=reasons)
        if p95 >= max_p95_watch_seconds:
            reasons.append(f"p95 latency elevated ({p95:.3f}s >= {max_p95_watch_seconds:.3f}s)")
            return RolloutAssessment(decision="SUSTAINED_WATCH", reasons=reasons)
        reasons.append(f"fallback rate healthy ({fallback_rate:.3f})")
        reasons.append(f"p95 latency healthy ({p95:.3f}s)")
        return RolloutAssessment(decision="SUSTAINED_DEFAULT_ON_HEALTHY", reasons=reasons)

    reasons.append(f"fallback rate healthy ({fallback_rate:.3f})")
    reasons.append(f"p95 latency observed ({p95:.3f}s)")
    return RolloutAssessment(decision="READY_FOR_LIMITED_COHORT_PILOT", reasons=reasons)


def _format_row(
    snapshot: dict[str, Any],
    window_label: str,
    scope: str,
    ref: str,
    assessment: RolloutAssessment,
) -> str:
    fallback = snapshot.get("fallback") if isinstance(snapshot.get("fallback"), dict) else {}
    latency = snapshot.get("latency_seconds") if isinstance(snapshot.get("latency_seconds"), dict) else {}
    mission_counts = snapshot.get("mission_counts") if isinstance(snapshot.get("mission_counts"), dict) else {}
    event_counts = snapshot.get("event_counts") if isinstance(snapshot.get("event_counts"), dict) else {}

    generated_at = str(snapshot.get("generated_at") or datetime.now(timezone.utc).isoformat())
    if generated_at.endswith("+00:00"):
        generated_at = generated_at[:-6] + "Z"

    fallback_rate = _as_float(fallback.get("rate"))
    fallback_text = f"{fallback_rate:.3f}" if fallback_rate is not None else "n/a"

    p95 = _as_float(latency.get("p95_seconds"))
    p95_text = f"{p95:.3f}s" if p95 is not None else "n/a"

    notes = (
        f"missions_considered={int(fallback.get('missions_considered') or 0)}, "
        f"missions_with_fallback={int(fallback.get('missions_with_fallback') or 0)}, "
        f"mission_counts={json.dumps(mission_counts, separators=(',', ':'))}, "
        f"event_counts={json.dumps(event_counts, separators=(',', ':'))}; "
        f"assessment={'; '.join(assessment.reasons)}"
    )

    return (
        f"| {generated_at} | {window_label} | {scope} | `{ref}` | {fallback_text} | {p95_text} "
        f"| {assessment.decision} | {notes} |"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture CODIE (CODER VP) metrics and emit a markdown evidence row."
    )
    parser.add_argument("--vp-id", default="vp.coder.primary")
    parser.add_argument("--mission-limit", type=int, default=100)
    parser.add_argument("--event-limit", type=int, default=500)
    parser.add_argument("--window-label", default="Shadow window")
    parser.add_argument("--scope", default="vp.coder.primary")
    parser.add_argument(
        "--ref",
        default='_vp_metrics_snapshot(vp_id="vp.coder.primary", mission_limit=100, event_limit=500)',
        help="Reference string to include in evidence table row.",
    )
    parser.add_argument(
        "--mode",
        choices=("direct", "http"),
        default="direct",
        help="direct uses gateway_server internals in-process; http queries running gateway endpoint.",
    )
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8002")
    parser.add_argument("--ops-token", default="", help="Optional ops token; falls back to UA_OPS_TOKEN env or local .env")
    parser.add_argument(
        "--assessment-profile",
        choices=("rollout", "sustained"),
        default="rollout",
        help="rollout: promotion gate decisions, sustained: low-cost default-on health monitoring",
    )
    parser.add_argument("--min-missions", type=int, default=1)
    parser.add_argument("--max-fallback-watch", type=float, default=0.05)
    parser.add_argument("--max-fallback-critical", type=float, default=0.20)
    parser.add_argument("--max-p95-watch-seconds", type=float, default=45.0)
    parser.add_argument("--max-p95-critical-seconds", type=float, default=90.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ops_token = _resolve_ops_token(str(args.ops_token or ""))

    try:
        if args.mode == "http":
            snapshot = _fetch_snapshot_http(
                gateway_url=args.gateway_url,
                vp_id=args.vp_id,
                mission_limit=max(1, min(int(args.mission_limit), 500)),
                event_limit=max(1, min(int(args.event_limit), 1000)),
                ops_token=ops_token,
            )
        else:
            snapshot = _fetch_snapshot_direct(
                vp_id=args.vp_id,
                mission_limit=max(1, min(int(args.mission_limit), 500)),
                event_limit=max(1, min(int(args.event_limit), 1000)),
            )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    assessment = _assess_with_profile(
        snapshot,
        assessment_profile=str(args.assessment_profile),
        min_missions=int(args.min_missions),
        max_fallback_watch=float(args.max_fallback_watch),
        max_fallback_critical=float(args.max_fallback_critical),
        max_p95_watch_seconds=float(args.max_p95_watch_seconds),
        max_p95_critical_seconds=float(args.max_p95_critical_seconds),
    )
    row = _format_row(
        snapshot=snapshot,
        window_label=str(args.window_label),
        scope=str(args.scope),
        ref=str(args.ref),
        assessment=assessment,
    )

    print(json.dumps({
        "vp_id": snapshot.get("vp_id"),
        "generated_at": snapshot.get("generated_at"),
        "fallback": snapshot.get("fallback"),
        "latency_seconds": snapshot.get("latency_seconds"),
        "mission_counts": snapshot.get("mission_counts"),
        "event_counts": snapshot.get("event_counts"),
        "assessment": {
            "decision": assessment.decision,
            "reasons": assessment.reasons,
        },
    }, indent=2, sort_keys=True))
    print()
    print("Markdown row:")
    print(row)


if __name__ == "__main__":
    main()
