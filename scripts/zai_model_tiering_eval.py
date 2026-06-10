#!/usr/bin/env python3
"""Evaluate the impact of ZAI model-tier changes (the 2026-06-10 tiering work).

Two modes:

  before-after   Operational impact, from the httpx-hook events log
                 (AGENT_RUN_WORKSPACES/zai_inference_events.jsonl). Splits events
                 around a pivot time (the deploy/cutover) and compares the changed
                 caller's 429 rate, error rate, and latency before vs after. This
                 answers "did moving the high-volume classifiers to glm-4.5-air
                 relieve the 429 pressure without raising errors/latency?"

  agreement      Quality regression check, OFFLINE. Runs a sample of real inputs
                 through BOTH the cheap model (glm-4.5-air) and the flagship
                 (glm-5.1) and reports how often their verdicts agree. High
                 agreement = the downgrade is safe. Makes real ZAI calls, so keep
                 the sample small.

Why caller-level (not model-level): the observability hook attributes each call to
the innermost UA frame, which for both phase-1 sites is `llm_classifier.py`
(the tutorial-buildability judge and the convergence signature extractor both route
through `llm_classifier._call_llm`). So `llm_classifier.py` aggregate 429/latency is
the phase-1 signal.

Examples
--------
  # operational before/after around a deploy at 2026-06-10T02:30:00Z, +/- 12h
  python -m scripts.zai_model_tiering_eval before-after \
      --pivot 2026-06-10T02:30:00Z --window-hours 12

  # same, run on the VPS against the live events file
  python scripts/zai_model_tiering_eval.py before-after --pivot 1781056200

  # offline quality check: 20 recent tutorial-judge inputs, air vs flagship
  python -m scripts.zai_model_tiering_eval agreement --inputs /tmp/tutorial_inputs.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Iterable, Optional

# Event categories written by services/zai_observability.py
CAT_OK = "ok"
CAT_429 = "rate_limited_429"
CAT_5XX = "server_error_5xx"
CAT_4XX = "client_error_4xx"
CAT_FUP = "fup_signal"


# ── shared helpers ──────────────────────────────────────────────────────────

def _default_events_path() -> Path:
    env = os.getenv("UA_ZAI_EVENTS_PATH")
    if env:
        return Path(env)
    # Repo root is two levels up from scripts/.
    return Path(__file__).resolve().parents[1] / "AGENT_RUN_WORKSPACES" / "zai_inference_events.jsonl"


def _parse_pivot(raw: str) -> float:
    """Accept an epoch (int/float seconds) or an ISO-8601 timestamp."""
    raw = raw.strip()
    try:
        return float(raw)
    except ValueError:
        pass
    from datetime import datetime

    iso = raw.replace("Z", "+00:00")
    return datetime.fromisoformat(iso).timestamp()


def _iter_events(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        print(f"ERROR: events file not found: {path}", file=sys.stderr)
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(ev.get("ts"), (int, float)):
                yield ev


# ── before-after ────────────────────────────────────────────────────────────

def _window_stats(events: list[dict[str, Any]], lo: float, hi: float, caller_filter: Optional[str]) -> dict[str, Any]:
    n = c_ok = c_429 = c_5xx = c_4xx = c_fup = 0
    latencies: list[float] = []
    for ev in events:
        ts = ev["ts"]
        if ts < lo or ts >= hi:
            continue
        if caller_filter and caller_filter not in str(ev.get("caller") or ""):
            continue
        n += 1
        cat = ev.get("category")
        if cat == CAT_OK:
            c_ok += 1
        elif cat == CAT_429:
            c_429 += 1
        elif cat == CAT_5XX:
            c_5xx += 1
        elif cat == CAT_4XX:
            c_4xx += 1
        elif cat == CAT_FUP:
            c_fup += 1
        rt = ev.get("response_time_ms")
        if isinstance(rt, (int, float)):
            latencies.append(float(rt))
    span_h = max((hi - lo) / 3600.0, 1e-9)
    latencies.sort()

    def pct(p: float) -> Optional[float]:
        if not latencies:
            return None
        k = max(0, min(len(latencies) - 1, int(round(p * (len(latencies) - 1)))))
        return round(latencies[k], 1)

    return {
        "n": n,
        "ok": c_ok,
        "c429": c_429,
        "c5xx": c_5xx,
        "c4xx": c_4xx,
        "fup": c_fup,
        "rate429": round(c_429 / n, 4) if n else 0.0,
        "per_hour429": round(c_429 / span_h, 2),
        "span_hours": round(span_h, 2),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "mean_ms": round(statistics.fmean(latencies), 1) if latencies else None,
    }


def _fmt(v: Any) -> str:
    return "-" if v is None else str(v)


def _delta(before: Any, after: Any) -> str:
    if before is None or after is None:
        return "-"
    d = after - before
    arrow = "↓" if d < 0 else ("↑" if d > 0 else "=")
    return f"{arrow}{abs(round(d, 2))}"


def cmd_before_after(args: argparse.Namespace) -> int:
    path = Path(args.events_path) if args.events_path else _default_events_path()
    events = list(_iter_events(path))
    if not events:
        print(f"No usable events in {path}", file=sys.stderr)
        return 2

    pivot = _parse_pivot(args.pivot)
    now = time.time()
    win = args.window_hours * 3600.0
    before_lo, before_hi = pivot - win, pivot
    after_lo, after_hi = pivot, min(pivot + win, now)

    callers = [c.strip() for c in (args.callers or "").split(",") if c.strip()]
    groups = [("ALL ZAI", None)] + [(c, c) for c in callers]

    rows = ["n", "ok", "c429", "rate429", "per_hour429", "c5xx", "c4xx", "fup", "p50_ms", "p95_ms", "mean_ms", "span_hours"]
    out: dict[str, Any] = {"pivot": pivot, "events_path": str(path)}

    from datetime import datetime, timezone
    def iso(t: float) -> str:
        return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"\nZAI model-tiering before/after  —  pivot={iso(pivot)}")
    print(f"  BEFORE window: {iso(before_lo)} → {iso(before_hi)}")
    print(f"  AFTER  window: {iso(after_lo)} → {iso(after_hi)}")
    print(f"  events file:   {path}\n")

    for label, cf in groups:
        b = _window_stats(events, before_lo, before_hi, cf)
        a = _window_stats(events, after_lo, after_hi, cf)
        out[label] = {"before": b, "after": a}
        print(f"── {label} " + "─" * max(0, 50 - len(label)))
        print(f"  {'metric':<13}{'before':>12}{'after':>12}{'delta':>10}")
        for r in rows:
            print(f"  {r:<13}{_fmt(b[r]):>12}{_fmt(a[r]):>12}{_delta(b[r], a[r]):>10}")
        print()

    # Heuristic verdict on the primary changed caller (first caller filter, else ALL).
    primary = callers[0] if callers else "ALL ZAI"
    pb, pa = out[primary]["before"], out[primary]["after"]
    verdict = []
    if pb["per_hour429"] > 0 or pa["per_hour429"] > 0:
        verdict.append(f"429s/hour {pb['per_hour429']} → {pa['per_hour429']} ({_delta(pb['per_hour429'], pa['per_hour429'])})")
    if pb["p95_ms"] is not None and pa["p95_ms"] is not None:
        verdict.append(f"p95 latency {pb['p95_ms']}ms → {pa['p95_ms']}ms ({_delta(pb['p95_ms'], pa['p95_ms'])})")
    if pa["c5xx"] > pb["c5xx"] or pa["c4xx"] > pb["c4xx"]:
        verdict.append(f"WARNING: errors rose (5xx {pb['c5xx']}→{pa['c5xx']}, 4xx {pb['c4xx']}→{pa['c4xx']})")
    if pa["fup"] > 0:
        verdict.append(f"WARNING: {pa['fup']} FUP signal(s) after pivot")
    print(f"VERDICT [{primary}]: " + ("; ".join(verdict) if verdict else "no notable change"))

    if args.json:
        print("\n" + json.dumps(out, indent=2))
    return 0


# ── agreement (offline quality check) ───────────────────────────────────────

def cmd_agreement(args: argparse.Namespace) -> int:
    import asyncio

    inputs_path = Path(args.inputs)
    if not inputs_path.exists():
        print(
            "ERROR: --inputs file not found. Provide a JSON list of records, e.g.\n"
            '  [{"title": "...", "channel_name": "...", "summary_text": "..."}, ...]\n'
            "For the tutorial-buildability judge, sample recent rows from the CSI DB\n"
            "(rss_event_analysis: video_title, channel_name, summary).",
            file=sys.stderr,
        )
        return 2
    records = json.loads(inputs_path.read_text())
    if args.limit:
        records = records[: args.limit]
    if not records:
        print("No input records.", file=sys.stderr)
        return 2

    from universal_agent.services.llm_classifier import classify_tutorial_buildability

    async def judge(rec: dict[str, Any], model: str) -> Optional[bool]:
        res = await classify_tutorial_buildability(
            title=rec.get("title", ""),
            channel_name=rec.get("channel_name", ""),
            summary_text=rec.get("summary_text", ""),
            model=model,
        )
        # A fallback verdict means the model failed to answer — track separately.
        return None if res.get("method") != "llm" else bool(res.get("buildable"))

    async def run() -> dict[str, Any]:
        agree = disagree = air_fallback = flag_fallback = 0
        mismatches = []
        for i, rec in enumerate(records):
            air = await judge(rec, args.air_model)
            flag = await judge(rec, args.flagship_model)
            if air is None:
                air_fallback += 1
            if flag is None:
                flag_fallback += 1
            if air is None or flag is None:
                continue
            if air == flag:
                agree += 1
            else:
                disagree += 1
                mismatches.append({"i": i, "title": rec.get("title", "")[:80], "air": air, "flagship": flag})
        total = agree + disagree
        return {
            "compared": total,
            "agree": agree,
            "disagree": disagree,
            "agreement_pct": round(100.0 * agree / total, 1) if total else None,
            "air_fallbacks": air_fallback,
            "flagship_fallbacks": flag_fallback,
            "mismatches": mismatches,
        }

    result = asyncio.run(run())
    print(f"\nAgreement: air={args.air_model} vs flagship={args.flagship_model}")
    print(f"  compared:   {result['compared']}")
    print(f"  agreement:  {result['agreement_pct']}%  ({result['agree']} agree / {result['disagree']} disagree)")
    print(f"  fallbacks:  air={result['air_fallbacks']} flagship={result['flagship_fallbacks']}")
    if result["mismatches"]:
        print("  mismatches (air vs flagship):")
        for m in result["mismatches"]:
            print(f"    [{m['i']}] air={m['air']} flagship={m['flagship']}  {m['title']}")
    if args.json:
        print("\n" + json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    ba = sub.add_parser("before-after", help="operational 429/latency impact from the events log")
    ba.add_argument("--pivot", required=True, help="deploy/cutover time (epoch seconds or ISO-8601)")
    ba.add_argument("--window-hours", type=float, default=12.0, help="comparison window each side (default 12)")
    ba.add_argument("--events-path", default="", help="override events JSONL path")
    ba.add_argument("--callers", default="llm_classifier.py", help="comma-list of caller substrings to focus")
    ba.add_argument("--json", action="store_true", help="also print machine-readable JSON")
    ba.set_defaults(func=cmd_before_after)

    ag = sub.add_parser("agreement", help="offline air-vs-flagship verdict agreement on sampled inputs")
    ag.add_argument("--inputs", required=True, help="JSON list of {title, channel_name, summary_text} records")
    ag.add_argument("--air-model", default="glm-4.5-air", help="cheap model id (default glm-4.5-air)")
    ag.add_argument("--flagship-model", default="glm-5.1", help="flagship model id (default glm-5.1)")
    ag.add_argument("--limit", type=int, default=20, help="max records to compare (default 20)")
    ag.add_argument("--json", action="store_true", help="also print machine-readable JSON")
    ag.set_defaults(func=cmd_agreement)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
