"""Post-change monitor for the 2026-06-10 convergence tier/concurrency change.

After cluster-refine was switched to the sonnet tier (`glm-5-turbo`) and its
concurrency lowered 6 -> 2, we need to know — later the same day — whether:

  1. ZAI Fair-Usage 429/1313 (FUP) bursts have actually subsided, and
  2. the lower concurrency has NOT made convergence fall behind (a permanently
     growing backlog of unprocessed signatures).

This script reads the ZAI observability events log + the convergence tables,
splits the picture before vs after a `--change-at` timestamp, forms a verdict,
and EMAILS the operator. Designed to be fired once this evening by a transient
systemd timer; safe to run ad-hoc with `--no-email` to just print.

The throughput rule (operator's bar): as long as the newest convergence
candidate stays recent (the pipeline keeps clearing its buckets each hourly
run) and candidates keep flowing as signatures arrive, the reduced concurrency
is fine. A *growing* newest-candidate lag is the "falling behind" signal.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Optional

from universal_agent.utils.model_resolution import model_id_to_tier

DEFAULT_EMAIL = "kevinjdragan@gmail.com"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    # DB timestamps are stored naive (UTC). Make tz-aware so arithmetic with
    # _utcnow() (tz-aware) doesn't raise "offset-naive and offset-aware".
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _events_path() -> Path:
    env = os.getenv("UA_ZAI_EVENTS_PATH")
    if env:
        return Path(env)
    root = Path(os.getenv("AGENT_RUN_WORKSPACES", "/opt/universal_agent/AGENT_RUN_WORKSPACES"))
    return root / "zai_inference_events.jsonl"


def _analyze_429(change_at: datetime) -> dict[str, Any]:
    """Split the ZAI events log into MATCHED before/after windows around change_at.

    The ``after`` window is ``[change_at, now]``. The ``before`` window is the
    SAME duration immediately preceding ``change_at`` — a fair like-for-like
    comparison at comparable time-of-day load. (An earlier version compared
    ``after`` against ALL prior events, which spanned ~94h including idle
    overnight, making the post-change rate look worse than a matched baseline.)
    """
    path = _events_path()
    change_ts = change_at.timestamp()
    now_ts = _utcnow().timestamp()
    after_len = max(1.0, now_ts - change_ts)
    before_lo = change_ts - after_len
    buckets = {
        "before": {"total": 0, "r429": 0, "fup": 0, "fup_texted_429": 0, "ok": 0,
                   "min_ts": None, "max_ts": None,
                   "callers429": {}, "models_total": {}, "models429": {}},
        "after": {"total": 0, "r429": 0, "fup": 0, "fup_texted_429": 0, "ok": 0,
                  "min_ts": None, "max_ts": None,
                  "callers429": {}, "models_total": {}, "models429": {}},
    }
    if not path.exists():
        return {"available": False, "path": str(path), **buckets}
    for line in path.read_text(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        ts = e.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if ts >= change_ts:
            w = buckets["after"]
        elif ts >= before_lo:
            w = buckets["before"]
        else:
            continue  # outside the matched before-window
        w["total"] += 1
        w["min_ts"] = ts if w["min_ts"] is None else min(w["min_ts"], ts)
        w["max_ts"] = ts if w["max_ts"] is None else max(w["max_ts"], ts)
        # Per-model totals. Events predating this PR's deploy have no "model"
        # field -> they group under "unknown".
        model = str(e.get("model") or "unknown")
        w["models_total"][model] = w["models_total"].get(model, 0) + 1
        cat = e.get("category")
        if cat == "rate_limited_429":
            w["r429"] += 1
            c = str(e.get("caller") or "?")
            w["callers429"][c] = w["callers429"].get(c, 0) + 1
            w["models429"][model] = w["models429"].get(model, 0) + 1
            # 1313/Fair-Usage-texted 429s are the ordinary throttle gradient,
            # distinct from the cliff-only `fup_signal` category counted below.
            if e.get("fup_texted"):
                w["fup_texted_429"] += 1
        elif cat == "fup_signal":
            w["fup"] += 1
        elif cat == "ok":
            w["ok"] += 1
    for w in buckets.values():
        span_h = ((w["max_ts"] - w["min_ts"]) / 3600.0) if (w["min_ts"] and w["max_ts"]) else 0.0
        w["span_hours"] = round(span_h, 2)
        w["r429_per_hour"] = round(w["r429"] / span_h, 1) if span_h > 0 else (float(w["r429"]) if w["r429"] else 0.0)
        w["fup_per_hour"] = round(w["fup"] / span_h, 1) if span_h > 0 else (float(w["fup"]) if w["fup"] else 0.0)
        w["r429_pct"] = round(100.0 * w["r429"] / w["total"], 1) if w["total"] else 0.0
        # Collapse model -> limiter tier and roll up per-tier rates.
        w["tiers"] = _rollup_tiers(w["models_total"], w["models429"])

    # Coverage guard: the events file is line-trimmed (~10k lines) so the
    # `before` window can silently start LATER than before_lo — which would
    # understate the pre-change rejection rate and fake an improvement. Trip a
    # flag if the before bucket has no data, or its earliest event is more than
    # 5% of the window length after the requested before_lo.
    before = buckets["before"]
    window_len = after_len  # before window has the same nominal length
    tolerance = 0.05 * window_len
    before_min = before["min_ts"]
    before_truncated = before_min is None or (before_min - before_lo) > tolerance
    return {
        "available": True,
        "path": str(path),
        "before_truncated": before_truncated,
        "before_lo": before_lo,
        "before_min_ts": before_min,
        "window_seconds": round(window_len, 1),
        **buckets,
    }


def _rollup_tiers(models_total: dict[str, int], models429: dict[str, int]) -> dict[str, dict[str, int]]:
    """Collapse per-model counts into per-tier {total, r429} via model_id_to_tier.

    Events without a ``model`` field arrive here keyed ``"unknown"`` and stay
    in an explicit ``"unknown"`` tier bucket (NOT collapsed to a real tier) so
    the renderer can flag windows where un-tagged pre-deploy events dominate.
    """
    tiers: dict[str, dict[str, int]] = {}
    for model, n in models_total.items():
        tier = "unknown" if model == "unknown" else model_id_to_tier(model)
        bucket = tiers.setdefault(tier, {"total": 0, "r429": 0})
        bucket["total"] += n
    for model, n in models429.items():
        tier = "unknown" if model == "unknown" else model_id_to_tier(model)
        bucket = tiers.setdefault(tier, {"total": 0, "r429": 0})
        bucket["r429"] += n
    return tiers


def _analyze_throughput(change_at: datetime, conn: sqlite3.Connection) -> dict[str, Any]:
    """Is convergence keeping up? newest-candidate lag + signatures-in vs candidates-out."""
    now = _utcnow()
    change_iso = change_at.isoformat()

    def _scalar(sql: str, args: tuple = ()) -> Any:
        try:
            row = conn.execute(sql, args).fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    newest_cand = _scalar("SELECT MAX(created_at) FROM convergence_candidates")
    newest_sig = _scalar("SELECT MAX(ingested_at) FROM proactive_topic_signatures")
    cand_after = _scalar("SELECT COUNT(*) FROM convergence_candidates WHERE created_at >= ?", (change_iso,)) or 0
    sig_after = _scalar("SELECT COUNT(*) FROM proactive_topic_signatures WHERE ingested_at >= ?", (change_iso,)) or 0

    nc = _parse_iso(str(newest_cand or ""))
    ns = _parse_iso(str(newest_sig or ""))
    cand_lag_h = round((now - nc).total_seconds() / 3600.0, 2) if nc else None
    sig_lag_h = round((now - ns).total_seconds() / 3600.0, 2) if ns else None
    hours_since_change = max(0.01, (now - change_at).total_seconds() / 3600.0)

    # Keeping-up rule: the convergence cron runs hourly, so the newest candidate
    # should be < ~2h old if the pipeline is clearing buckets. A lag well beyond
    # that (and beyond the signature lag) means it is falling behind.
    keeping_up = cand_lag_h is not None and cand_lag_h <= 2.5
    return {
        "newest_candidate_at": newest_cand,
        "newest_signature_at": newest_sig,
        "candidate_lag_hours": cand_lag_h,
        "signature_lag_hours": sig_lag_h,
        "candidates_since_change": cand_after,
        "signatures_since_change": sig_after,
        "candidates_per_hour": round(cand_after / hours_since_change, 2),
        "signatures_per_hour": round(sig_after / hours_since_change, 2),
        "hours_since_change": round(hours_since_change, 2),
        "keeping_up": keeping_up,
    }


def _verdict(z: dict[str, Any], t: dict[str, Any]) -> tuple[str, str]:
    after = z.get("after", {}) if z.get("available") else {}
    before = z.get("before", {}) if z.get("available") else {}
    fup_after = after.get("fup", 0)
    # Use the volume-normalized REJECTION RATE (429 / total), not raw per-hour
    # counts — even matched windows can carry different absolute volume.
    pct_before = before.get("r429_pct", 0.0)
    pct_after = after.get("r429_pct", 0.0)

    # Coverage guard: if the before-window is truncated (the events file is
    # line-trimmed, so the baseline can start late and understate the
    # pre-change rate), we CANNOT claim improvement — the comparison is
    # unsound. Force the ACTION path with the caveat in the headline.
    if z.get("available") and z.get("before_truncated"):
        return "ACTION", (
            "⚠️ BEFORE-WINDOW TRUNCATED — baseline incomplete (events file "
            "line-trimmed); cannot claim 429 improvement. Re-run sooner after a "
            f"change or shorten the window. (after rejection {pct_after:.0f}%, "
            f"FUP={fup_after})"
        )

    fup_ok = fup_after == 0
    tput_ok = bool(t.get("keeping_up"))
    improved = pct_after < pct_before - 2.0  # rejection rate dropped meaningfully
    still_elevated = pct_after > 25.0

    if not tput_ok:
        return "ACTION", "⚠️ Convergence may be falling behind (throughput) after the concurrency drop"
    if not fup_ok:
        return "ACTION", "⚠️ FUP/1313 cliff signal present after the change — escalate to the limiter fix"
    if improved and still_elevated:
        return "ACTION", (
            f"🟡 Helped but not enough — 429 rejection {pct_before:.0f}%→{pct_after:.0f}%, "
            "FUP=0, throughput OK; 429s still elevated → limiter fix is the lever to go lower"
        )
    if improved:
        return "FYI", (
            f"✅ Convergence change looks good — 429 rejection {pct_before:.0f}%→{pct_after:.0f}%, "
            "FUP=0, throughput keeping up"
        )
    return "ACTION", (
        f"⚠️ No 429 improvement (rejection {pct_before:.0f}%→{pct_after:.0f}%), FUP=0 — "
        "escalate to the limiter fix"
    )


def _render_tier_lines(zbefore: dict[str, Any], zafter: dict[str, Any]) -> list[str]:
    """Per-tier rejection RATES with denominators, before -> after.

    One line per tier the data touches. Each side shows `r429/total (pct%)`.
    Tiers come from collapsing model ids via `model_id_to_tier`; `unknown`
    (events predating this PR's deploy) is rendered as its own tier so a
    window dominated by un-tagged events is visible rather than silently
    misattributed.
    """
    tiers_before = zbefore.get("tiers", {}) or {}
    tiers_after = zafter.get("tiers", {}) or {}
    all_tiers = set(tiers_before) | set(tiers_after)
    # Stable, meaningful order: real tiers by conservativeness, then unknown.
    order = ["opus", "sonnet", "mid", "haiku", "unknown"]
    ordered = [tt for tt in order if tt in all_tiers] + sorted(all_tiers - set(order))

    def _fmt(side: dict[str, int]) -> str:
        total = side.get("total", 0)
        r429 = side.get("r429", 0)
        pct = (100.0 * r429 / total) if total else 0.0
        return f"{r429}/{total} ({pct:.1f}%)"

    lines: list[str] = []
    for tier in ordered:
        b = tiers_before.get(tier, {"total": 0, "r429": 0})
        a = tiers_after.get(tier, {"total": 0, "r429": 0})
        lines.append(f"    tier {tier}: before {_fmt(b)} -> after {_fmt(a)}")
    return lines


def _unknown_dominates(window: dict[str, Any]) -> bool:
    """True if >=50% of this window's events lack a model tag (`unknown`)."""
    total = window.get("total", 0) or 0
    if total == 0:
        return False
    unk = (window.get("models_total", {}) or {}).get("unknown", 0)
    return unk >= 0.5 * total


def _render(change_at: datetime, z: dict[str, Any], t: dict[str, Any], headline: str) -> str:
    L = [headline, "", f"Change applied at: {change_at.isoformat()}  |  now: {_utcnow().isoformat()}", ""]
    # Coverage stamp — prominent, at the very top of the body.
    if z.get("available") and z.get("before_truncated"):
        L.append("!!! BEFORE-WINDOW TRUNCATED — DO NOT TRUST the before/after 429 comparison !!!")
        L.append("    The events file is line-trimmed (~10k lines); the baseline window starts")
        L.append("    later than requested, so the pre-change rejection rate is understated.")
        L.append("")
    L.append("── ZAI 429 / FUP (MATCHED before/after windows, same duration) ──")
    if not z.get("available"):
        L.append(f"  events log not found: {z.get('path')}")
    else:
        for w in ("before", "after"):
            d = z[w]
            fup_texted = d.get("fup_texted_429", 0)
            L.append(
                f"  {w:6s}: span {d['span_hours']}h | 429={d['r429']} ({d['r429_per_hour']}/h, {d['r429_pct']}%) "
                f"| FUP-cliff={d['fup']} ({d['fup_per_hour']}/h) | 1313-throttle-429={fup_texted} "
                f"| total={d['total']}"
            )
            if _unknown_dominates(d):
                L.append(
                    f"         ⚠️ {w} window is dominated by un-tagged (pre-deploy) events — "
                    "per-tier split below is unreliable for this window."
                )
        L.append("  per-tier 429 rejection rate (model collapsed to limiter tier):")
        L.extend(_render_tier_lines(z["before"], z["after"]))
        top = sorted(z["after"]["callers429"].items(), key=lambda kv: -kv[1])[:4]
        if top:
            L.append("  after 429s by caller: " + ", ".join(f"{c.split('/')[-1]}×{n}" for c, n in top))
    L.append("")
    L.append("── Convergence throughput (falling behind?) ──")
    L.append(f"  newest candidate: {t['newest_candidate_at']}  (lag {t['candidate_lag_hours']}h)")
    L.append(f"  newest signature: {t['newest_signature_at']}  (lag {t['signature_lag_hours']}h)")
    L.append(
        f"  since change ({t['hours_since_change']}h): "
        f"signatures in={t['signatures_since_change']} ({t['signatures_per_hour']}/h), "
        f"candidates out={t['candidates_since_change']} ({t['candidates_per_hour']}/h)"
    )
    L.append(f"  keeping up: {'YES' if t['keeping_up'] else 'NO — newest candidate is stale; backlog may be growing'}")
    L.append("")
    L.append("Config now: cluster-refine = glm-5-turbo (sonnet), UA_CONVERGENCE_LLM_CONCURRENCY = 2.")
    L.append("If 429/FUP persist here, the durable fix is routing _call_llm through the ZAIRateLimiter")
    L.append("(needs the loop-resilient limiter) — see project_docs/06_platform/10_zai_rate_limiter.md.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Post-change convergence 429/throughput monitor → email.")
    ap.add_argument("--email", default=DEFAULT_EMAIL)
    ap.add_argument("--change-at", default="", help="ISO timestamp the tier/concurrency change went live")
    ap.add_argument("--no-email", action="store_true", help="print only, do not send")
    args = ap.parse_args()

    from universal_agent.infisical_loader import initialize_runtime_secrets

    initialize_runtime_secrets()

    change_at = _parse_iso(args.change_at) or _utcnow().replace(hour=18, minute=45, second=0, microsecond=0)

    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

    z = _analyze_429(change_at)
    with connect_runtime_db(get_activity_db_path()) as conn:
        t = _analyze_throughput(change_at, conn)

    action, headline = _verdict(z, t)
    body = _render(change_at, z, t, headline)
    print(body)

    if args.no_email:
        return 0

    async def _send() -> dict[str, Any]:
        from universal_agent.services.agentmail_service import AgentMailService

        mail = AgentMailService()
        # REQUIRED: startup() wires the client/inbox; without it send_email
        # raises "AgentMail service is not enabled or initialized" (the working
        # report crons all call this first). force_send bypasses draft/approval.
        await mail.startup()
        if not getattr(mail, "_started", False):
            raise RuntimeError("AgentMail failed to start (startup() did not set _started)")
        return await mail.send_email(
            to=args.email,
            subject="Convergence tier/concurrency change — post-change 429 + throughput check",
            text=body,
            html="<pre style='font-family:ui-monospace,Menlo,monospace;font-size:13px;line-height:1.5'>"
            + body.replace("&", "&amp;").replace("<", "&lt;") + "</pre>",
            force_send=True,
            require_approval=False,
            action=action,
            kind="DIGEST",
            source="convergence_change_monitor",
        )

    try:
        result = asyncio.run(_send())
        print(f"\n[email] sent to {args.email}: message_id={result.get('message_id') or result}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[email] FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
