"""Weekly ZAI usage report — the standing follow-up to the 2026-08-08 audit.

Deterministic (NO LLM): federates every capture lane (in-process SDK, Cody
subprocess, CSI, httpx, demo-factory) for the last 7 days and the 7 days
before that, computes per-flow deltas, reads the weekly budget meter, and
emails the operator via ``simone_mail``. Scheduled by
``universal-agent-zai-usage-report.timer`` (weekly, Wed 09:00 CT — first
firing 2026-08-12, chosen by the operator as "four days after the audit,
once the ZAI coding plan is back"). The email also carries the standing
agenda: re-run ``/dragan:zai-usage-audit`` for the deep pass and advance the
truncation-strategy program (``project_docs/08_operations/09_truncation_strategy.md``).

Run manually: ``python -m universal_agent.scripts.zai_usage_weekly_report
[--dry-run] [--to addr]``.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

WINDOW = 7 * 86400


def _history_path() -> Path:
    """Durable trend ledger: one JSON row per report/audit run, appended forever.

    Lives in AGENT_RUN_WORKSPACES (outside git, survives deploys) so trends
    accumulate across weeks — the operator's mandate (2026-08-08): every
    snapshot must be SAVED, not just emailed, so "is the system actually
    reducing token use" is answerable from data, not memory. The
    zai-usage-audit skill reads and appends to the same file.
    """
    raw = os.getenv("UA_ZAI_USAGE_HISTORY_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "AGENT_RUN_WORKSPACES" / "zai_usage_history.jsonl"


def append_history_row(row: dict[str, Any]) -> None:
    """Append one trend row (fail-open — telemetry never breaks the report)."""
    try:
        p = _history_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def read_history(limit: int = 26) -> list[dict[str, Any]]:
    """Last `limit` trend rows, oldest first. Fail-open to []."""
    try:
        rows: list[dict[str, Any]] = []
        for line in _history_path().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows[-limit:]
    except Exception:  # noqa: BLE001
        return []


def _fmt(n: float) -> str:
    n = float(n or 0)
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k"
    return f"{n:.0f}"


def _window_view(now_ref: float, top_n: int = 15) -> dict[str, Any]:
    """One consolidated view over [now_ref - 7d, now_ref], all five lanes.
    Mirrors zai_weekly_budget.compute_week_to_date's fan-out, fail-soft."""
    from universal_agent.services import token_consolidation as tc, zai_status

    sources: list[dict[str, Any]] = []
    for reader in (
        tc.analyze_sink_token_usage,
        tc.analyze_cody_token_usage,
        tc.read_csi_token_usage,
        tc.read_demo_factory_token_usage,
    ):
        try:
            sources.append(reader(now_ref, WINDOW, top_n=top_n))
        except Exception:  # noqa: BLE001 — one lane never blocks the report
            pass
    try:
        httpx_src = zai_status.analyze_token_usage(now_ref, WINDOW, top_n=top_n)
        zai_status._make_cache_inclusive(httpx_src)
        httpx_src.setdefault("source", "httpx-zai")
        httpx_src.setdefault("available", True)
        sources.append(httpx_src)
    except Exception:  # noqa: BLE001
        pass
    view = tc.consolidate(sources, top_n=top_n)
    view["_sources"] = sources
    return view


def _meter_lines() -> list[str]:
    try:
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

        conn = connect_runtime_db(get_activity_db_path())
        try:
            rows = conn.execute(
                """SELECT datetime(week_anchor_epoch,'unixepoch'), observed_cap,
                          week_to_date_tokens, last_escalation_level, calibrated_from
                   FROM zai_weekly_budget_state ORDER BY week_anchor_epoch DESC LIMIT 2"""
            ).fetchall()
        finally:
            conn.close()
        out = ["Weekly budget meter:"]
        for anchor, cap, wtd, lvl, src in rows:
            out.append(
                f"  week {anchor}: {_fmt(wtd)} / cap {_fmt(cap)}"
                f" (escalation L{lvl}, cap from {src})"
            )
        return out
    except Exception as exc:  # noqa: BLE001
        return [f"Weekly budget meter: unreadable ({type(exc).__name__})"]


def build_report(now: float | None = None) -> str:
    now = now or time.time()
    cur = _window_view(now)
    prev = _window_view(now - WINDOW)
    cur_tot = int(cur["totals"].get("total_tokens") or 0)
    prev_tot = int(prev["totals"].get("total_tokens") or 0)
    delta = cur_tot - prev_tot
    pct = (100.0 * delta / prev_tot) if prev_tot else 0.0

    prev_by_caller = {p["caller"]: p for p in prev.get("processes", [])}
    lines = [
        "ZAI usage — weekly report (deterministic; all five capture lanes, cache-inclusive)",
        "",
        f"Last 7 days:  {_fmt(cur_tot)} tokens",
        f"Prior 7 days: {_fmt(prev_tot)} tokens   (delta {'+' if delta >= 0 else ''}{_fmt(abs(delta)) if delta >= 0 else '-' + _fmt(abs(delta))}, {pct:+.0f}%)",
        "",
        "Top flows (this week vs prior):",
    ]
    for p in cur.get("processes", [])[:12]:
        prior = int(prev_by_caller.get(p["caller"], {}).get("total_tokens") or 0)
        lines.append(
            f"  {p['caller'][:44]:<44} {_fmt(p['total_tokens']):>8}"
            f"  (prior {_fmt(prior)}, {p['requests']} calls)"
        )
    history = read_history()
    weekly_rows = [r for r in history if r.get("kind") == "weekly_report"][-8:]
    if weekly_rows:
        lines.append("")
        lines.append("Trend (stored history, cache-inclusive per 7d window):")
        prior_t = None
        for r in weekly_rows:
            t = int(r.get("total_tokens") or 0)
            d = f" ({'+' if t >= (prior_t or t) else '-'}{_fmt(abs(t - prior_t))})" if prior_t else ""
            lines.append(f"  {time.strftime('%Y-%m-%d', time.gmtime(float(r.get('ts') or 0)))}: {_fmt(t)}{d}")
            prior_t = t
        lines.append(f"  this run: {_fmt(cur_tot)}")

    lines.append("")
    lines.extend(_meter_lines())
    lines.append("")
    lines.append("Lane health:")
    for s in cur.get("_sources", []):
        lines.append(
            f"  {s.get('source', '?'):<16} available={s.get('available', '?')}"
            f" events={s.get('token_events_seen', 0)}"
        )
    lines += [
        "",
        "Standing agenda (operator, 2026-08-08):",
        "  1. Deep pass: run /dragan:zai-usage-audit in a session — measures the",
        "     effect of the heartbeat sonnet-tier + 2h-ideation changes and the",
        "     new demo-factory lane against this deterministic snapshot.",
        "  2. Truncation-strategy program: advance",
        "     project_docs/08_operations/09_truncation_strategy.md —",
        "     classify truncation sites (noise-cut vs context-amputation) and move",
        "     high-value flows to structured-output contracts. Blind truncation",
        "     degrades assessment quality; delta-gating and schemas are the fix.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", default="kevinjdragan@gmail.com")
    ap.add_argument("--dry-run", action="store_true", help="print only, no email")
    a = ap.parse_args()

    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        initialize_runtime_secrets()
    except Exception:  # noqa: BLE001 — report still prints without secrets
        pass

    now = time.time()
    text = build_report(now)
    print(text)
    if a.dry_run:
        return 0

    # Persist this run's aggregates BEFORE emailing, so the trend survives a
    # failed send. Recomputed compactly from the same windows.
    try:
        cur = _window_view(now)
        row: dict[str, Any] = {
            "ts": now,
            "kind": "weekly_report",
            "window_days": 7,
            "total_tokens": int(cur["totals"].get("total_tokens") or 0),
            "fresh_tokens": int(cur["totals"].get("input_tokens") or 0)
            + int(cur["totals"].get("output_tokens") or 0),
            "flows": [
                {
                    "caller": p["caller"],
                    "total_tokens": p["total_tokens"],
                    "requests": p["requests"],
                }
                for p in cur.get("processes", [])[:12]
            ],
            "lanes": {
                s.get("source", "?"): s.get("token_events_seen", 0)
                for s in cur.get("_sources", [])
            },
        }
        append_history_row(row)
    except Exception:  # noqa: BLE001
        pass
    from universal_agent.simone_mail import send_simone_email

    res = send_simone_email(
        to=a.to,
        subject="ZAI usage — weekly report (audit follow-up)",
        text=text,
        source="zai_usage_weekly_report",
    )
    print(f"email: {res.get('status', 'unknown')}")
    return 0 if res.get("status") not in ("failed",) else 1


if __name__ == "__main__":
    raise SystemExit(main())
