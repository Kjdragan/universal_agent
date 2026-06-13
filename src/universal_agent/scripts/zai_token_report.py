#!/usr/bin/env python3
"""ZAI per-process token-use report — terminal snapshot of "where did our ZAI
tokens go in the last N hours".

PURE PYTHON, NO LLM. Reads the httpx observability JSONL
(``AGENT_RUN_WORKSPACES/zai_inference_events.jsonl``) and aggregates per-process
token burn + churn signals via ``zai_status.analyze_token_usage`` — the SAME
engine the ZAI-Control dashboard token panel uses. The events file retains
~6 days, so any ``--hours`` up to ~144 is answerable with no durable store.

Token counts only appear for calls captured AFTER the capture upgrade
(``zai_observability`` token-usage extraction). For an older window the report
degrades gracefully to request counts (it tells you when no token data was
present via the "token events" line).

Usage (from the repo root)::

    PYTHONPATH=src uv run python -m universal_agent.scripts.zai_token_report --hours 24
    PYTHONPATH=src uv run python -m universal_agent.scripts.zai_token_report --hours 6 --top 15
    PYTHONPATH=src uv run python -m universal_agent.scripts.zai_token_report --json    # machine-readable

On the VPS the events file is at
``/opt/universal_agent/AGENT_RUN_WORKSPACES/zai_inference_events.jsonl`` and is
picked up automatically; pass ``--events-path`` to analyze a copy elsewhere.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import time
from typing import Any


def _fmt_tokens(n: int) -> str:
    """Compact human token count: 1234 -> '1.2k', 3400000 -> '3.4M'."""
    try:
        n = int(n)
    except Exception:  # noqa: BLE001
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _short(caller: str) -> str:
    """Trim the long universal_agent/ prefix for terminal display."""
    return (
        caller.replace("universal_agent/services/", "")
        .replace("universal_agent/", "")
    )


def _houston(ts: float) -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.fromtimestamp(ts, ZoneInfo("America/Chicago")).strftime(
            "%Y-%m-%d %H:%M %Z"
        )
    except Exception:  # noqa: BLE001
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")


def _render_text(report: dict[str, Any], hours: float, show_stages: bool) -> str:
    lines: list[str] = []
    now = report.get("generated_at", time.time())
    t = report.get("totals", {})
    seen = report.get("token_events_seen", 0)
    lines.append("")
    lines.append(f"  ZAI token use — last {hours:g}h  (as of {_houston(now)})")
    lines.append(
        f"  window total: {t.get('requests', 0)} calls · "
        f"{_fmt_tokens(t.get('input_tokens', 0))} in / "
        f"{_fmt_tokens(t.get('output_tokens', 0))} out / "
        f"{_fmt_tokens(t.get('total_tokens', 0))} total · "
        f"{t.get('r429', 0)} × 429 · "
        f"retry-waste {_fmt_tokens(t.get('retry_input_tokens', 0))} in · "
        f"dormant {_fmt_tokens(t.get('dormant_tokens', 0))}"
    )
    if seen == 0:
        lines.append(
            "  NOTE: 0 events carried token data in this window — showing "
            "request counts only (capture upgrade not yet live for this range)."
        )
    lines.append("")
    header = (
        f"  {'process':<40} {'calls':>6} {'rej%':>5} "
        f"{'in':>8} {'out':>8} {'total':>8} {'retryWaste':>11} {'rMult':>6} {'dormant':>8}"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for p in report.get("processes", []):
        rmult = p.get("retry_multiplier")
        rmult_s = "—" if rmult is None else f"{rmult:g}"
        lines.append(
            f"  {_short(p['caller'])[:40]:<40} "
            f"{p['requests']:>6} {p['reject_pct']:>5} "
            f"{_fmt_tokens(p['input_tokens']):>8} {_fmt_tokens(p['output_tokens']):>8} "
            f"{_fmt_tokens(p['total_tokens']):>8} "
            f"{_fmt_tokens(p['retry_input_tokens']):>11} {rmult_s:>6} "
            f"{_fmt_tokens(p['dormant_tokens']):>8}"
        )
        if show_stages:
            for s in p.get("stages", []):
                fn = s["caller_fn"].split("::", 1)[-1] if "::" in s["caller_fn"] else "(file-level)"
                lines.append(
                    f"      └ {fn[:46]:<46} "
                    f"{s['requests']:>5} calls  "
                    f"{_fmt_tokens(s['input_tokens'])} in / {_fmt_tokens(s['output_tokens'])} out"
                )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zai_token_report",
        description="Per-process ZAI token-use snapshot from the observability JSONL.",
    )
    parser.add_argument("--hours", type=float, default=24.0,
                        help="window size in hours (default 24; events retained ~144h)")
    parser.add_argument("--top", type=int, default=25,
                        help="number of top processes to show (default 25)")
    parser.add_argument("--stages", action="store_true",
                        help="also show the per-stage (file::function) breakdown under each process")
    parser.add_argument("--json", action="store_true",
                        help="emit the raw aggregation as JSON instead of a table")
    parser.add_argument("--events-path", default=None,
                        help="override the events JSONL path (else UA_ZAI_EVENTS_PATH / repo default)")
    args = parser.parse_args(argv)

    if args.events_path:
        os.environ["UA_ZAI_EVENTS_PATH"] = args.events_path

    from universal_agent.services.zai_status import analyze_token_usage

    now = time.time()
    report = analyze_token_usage(now, int(args.hours * 3600), top_n=args.top)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_render_text(report, args.hours, show_stages=args.stages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
