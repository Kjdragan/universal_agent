"""Operator-run A/B harness: which model tier does the convergence cluster-judge need?

The convergence "cluster-refine" stage (``proactive_convergence._refine_cluster_with_llm``)
is the dominant ZAI consumer and runs on the opus tier (``glm-5.1``) by default. This
harness measures whether a cheaper tier produces the *same convergence judgments* — so
the tier choice can be made on evidence instead of inference.

It mirrors ``youtube_digest_compare.py``: it drives the REAL production code path
(``_detect_clusters_sql`` for recall, ``_refine_cluster_with_llm`` for the LLM judge)
and only varies the model via the production override knob ``UA_CONVERGENCE_JUDGE_MODEL``
(restored after each sweep so it never bleeds into other state).

Method (fair comparison):
  1. Build the coarse SQL buckets ONCE — identical input for every model.
  2. For each model in --models, re-run the per-bucket LLM judge over those same buckets,
     SEQUENTIALLY with a small inter-call delay (so the A/B itself does not trip the very
     Fair-Usage-Policy 429 storm it is studying).
  3. Record per (bucket, model): confirmed?/thesis/signal_strength/latency/429-or-error.
  4. Emit a JSON + Markdown report: per-model summary, cross-model agreement, and a
     per-bucket divergence table.

Run on the VPS (it needs the activity DB + Infisical-injected ZAI creds), e.g.:

    UA_DEPLOYMENT_PROFILE=vps /opt/universal_agent/.venv/bin/python \
        -m universal_agent.scripts.convergence_model_ab \
        --models glm-4.5-air,glm-5-turbo,glm-5.1 --max-buckets 30

There is NO automated quality judge — the operator reads the divergence table. The
recommended decision rule: if the cheaper tier AGREES with glm-5.1 on (near-)all buckets,
adopt it; where they diverge, eyeball the theses to see which judgment is right.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import time
from typing import Any, Optional

DEFAULT_MODELS = "glm-4.5-air,glm-5-turbo,glm-5.1"
ENV_KNOB = "UA_CONVERGENCE_JUDGE_MODEL"


def _bucket_key(bucket: list[dict[str, Any]]) -> str:
    ids = sorted(str(s.get("video_id") or "").strip() for s in bucket)
    return hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:12]


def _bucket_label(bucket: list[dict[str, Any]]) -> str:
    from collections import Counter

    topics: Counter[str] = Counter()
    for s in bucket:
        for t in (s.get("primary_topics") or [])[:2]:
            if str(t).strip():
                topics[str(t).strip()] += 1
    top = topics.most_common(1)
    return top[0][0] if top else "(untagged)"


def _channels(bucket: list[dict[str, Any]]) -> int:
    return len({str(s.get("channel_id") or s.get("channel_name") or "").strip() for s in bucket})


def _classify_error(exc: Exception) -> str:
    s = str(exc).lower()
    if "1313" in s or "fair use" in s or "fair-use" in s:
        return "FUP_1313"
    if "429" in s or "too many requests" in s:
        return "rate_limited_429"
    if "timeout" in s or "timed out" in s:
        return "timeout"
    return "error"


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B the convergence cluster-judge across model tiers.")
    ap.add_argument("--models", default=DEFAULT_MODELS, help="comma-separated model ids to sweep")
    ap.add_argument("--window-hours", type=int, default=72, help="source window (production default 72)")
    ap.add_argument("--min-channels", type=int, default=2, help="min independent channels (production default 2)")
    ap.add_argument("--max-buckets", type=int, default=30, help="cap buckets to keep the A/B bounded")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds to sleep between LLM calls (self-throttle)")
    ap.add_argument("--out", default="", help="output dir (default: <artifacts>/proactive/convergence_model_ab)")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("no models given")
        return 2

    from universal_agent.infisical_loader import initialize_runtime_secrets

    initialize_runtime_secrets()

    from universal_agent.artifacts import resolve_artifacts_dir
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.services.proactive_convergence import (
        _detect_clusters_sql,
        _refine_cluster_with_llm,
    )

    started = datetime.now(timezone.utc)
    with connect_runtime_db(get_activity_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        buckets = _detect_clusters_sql(
            conn,
            source_window_hours=args.window_hours,
            min_channels=args.min_channels,
            include_secondary=True,
        )

    buckets = buckets[: max(1, args.max_buckets)]
    if not buckets:
        print(json.dumps({"ok": True, "buckets": 0, "note": "no buckets in window — nothing to compare"}))
        return 0

    bucket_meta = [
        {
            "key": _bucket_key(b),
            "label": _bucket_label(b),
            "size": len(b),
            "channels": _channels(b),
            "titles": [str(s.get("video_title") or "")[:80] for s in b][:6],
        }
        for b in buckets
    ]

    # results[model][bucket_key] = {...}
    results: dict[str, dict[str, dict[str, Any]]] = {m: {} for m in models}
    prior = os.environ.get(ENV_KNOB)
    print(f"Buckets: {len(buckets)} | models: {models} | window={args.window_hours}h min_channels={args.min_channels}")
    for model in models:
        os.environ[ENV_KNOB] = model
        confirmed = dropped = errors = rl = 0
        for b, meta in zip(buckets, bucket_meta):
            t0 = time.monotonic()
            rec: dict[str, Any] = {"confirmed": False, "thesis": "", "signal_strength": None, "error": ""}
            try:
                out: Optional[dict[str, Any]] = asyncio.run(
                    _refine_cluster_with_llm(b, min_channels=args.min_channels)
                )
                if out:
                    rec.update(
                        confirmed=True,
                        thesis=str(out.get("thesis") or "")[:240],
                        signal_strength=out.get("signal_strength"),
                        n_confirmed=len(out.get("signatures") or []),
                    )
                    confirmed += 1
                else:
                    dropped += 1
            except Exception as exc:  # noqa: BLE001
                kind = _classify_error(exc)
                rec["error"] = f"{kind}: {str(exc)[:120]}"
                errors += 1
                if kind in {"rate_limited_429", "FUP_1313"}:
                    rl += 1
            rec["latency_ms"] = round((time.monotonic() - t0) * 1000.0, 1)
            results[model][meta["key"]] = rec
            time.sleep(max(0.0, args.delay))
        lat = [r["latency_ms"] for r in results[model].values() if r.get("latency_ms")]
        print(
            f"  {model:14s} confirmed={confirmed:3d} dropped={dropped:3d} "
            f"errors={errors:3d} (429/FUP={rl}) mean_latency={statistics.mean(lat):.0f}ms"
            if lat else f"  {model}: no latencies"
        )
    if prior is None:
        os.environ.pop(ENV_KNOB, None)
    else:
        os.environ[ENV_KNOB] = prior

    # ---- summary + agreement ----
    summary = {}
    for m in models:
        recs = list(results[m].values())
        lat = [r["latency_ms"] for r in recs if r.get("latency_ms")]
        summary[m] = {
            "confirmed": sum(1 for r in recs if r["confirmed"]),
            "dropped": sum(1 for r in recs if not r["confirmed"] and not r["error"]),
            "errors": sum(1 for r in recs if r["error"]),
            "rate_limited": sum(1 for r in recs if r["error"].startswith(("rate_limited", "FUP"))),
            "mean_latency_ms": round(statistics.mean(lat), 1) if lat else None,
            "median_latency_ms": round(statistics.median(lat), 1) if lat else None,
        }

    baseline = models[-1]  # treat the last (glm-5.1) as the reference
    agree_confirm = agree_drop = split = 0
    per_bucket = []
    for meta in bucket_meta:
        k = meta["key"]
        verdicts = {m: results[m][k]["confirmed"] for m in models}
        n_confirm = sum(1 for v in verdicts.values() if v)
        if n_confirm == len(models):
            agree_confirm += 1
        elif n_confirm == 0:
            agree_drop += 1
        else:
            split += 1
        per_bucket.append(
            {
                **meta,
                "verdicts": {m: results[m][k] for m in models},
                "divergent": len(set(verdicts.values())) > 1,
            }
        )

    report = {
        "ok": True,
        "generated_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "models": models,
            "window_hours": args.window_hours,
            "min_channels": args.min_channels,
            "buckets": len(buckets),
            "delay_s": args.delay,
        },
        "summary": summary,
        "agreement_vs_each_other": {
            "all_confirm": agree_confirm,
            "all_drop": agree_drop,
            "split": split,
            "baseline_model": baseline,
        },
        "buckets": per_bucket,
    }

    out_dir = Path(args.out) if args.out else (resolve_artifacts_dir() / "proactive" / "convergence_model_ab")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"ab_{stamp}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    md_path = out_dir / f"ab_{stamp}.md"
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    print("\n" + _render_markdown(report))
    print(f"\nJSON: {json_path}\nMD:   {md_path}")
    return 0


def _render_markdown(r: dict[str, Any]) -> str:
    models = r["params"]["models"]
    out = []
    out.append(f"# Convergence cluster-judge — model A/B ({r['params']['buckets']} buckets)")
    out.append("")
    out.append(f"Window {r['params']['window_hours']}h · min_channels {r['params']['min_channels']} · "
               f"generated {r['generated_at']}")
    out.append("")
    out.append("## Per-model summary")
    out.append("")
    out.append("| Model | Confirmed | Dropped | Errors | 429/FUP | Mean ms | Median ms |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for m in models:
        s = r["summary"][m]
        out.append(f"| `{m}` | {s['confirmed']} | {s['dropped']} | {s['errors']} | "
                   f"{s['rate_limited']} | {s['mean_latency_ms']} | {s['median_latency_ms']} |")
    out.append("")
    ag = r["agreement_vs_each_other"]
    total = ag["all_confirm"] + ag["all_drop"] + ag["split"]
    pct = (100.0 * (ag["all_confirm"] + ag["all_drop"]) / total) if total else 0.0
    out.append("## Agreement across all models")
    out.append("")
    out.append(f"- **All agree (confirm or drop): {ag['all_confirm'] + ag['all_drop']}/{total} "
               f"= {pct:.0f}%**")
    out.append(f"- All confirm: {ag['all_confirm']} · All drop: {ag['all_drop']} · **Split (diverge): {ag['split']}**")
    out.append("")
    out.append("## Divergent buckets (where models disagree — read these)")
    out.append("")
    divergent = [b for b in r["buckets"] if b["divergent"]]
    if not divergent:
        out.append("_None — every model reached the same confirm/drop verdict on every bucket._")
    for b in divergent:
        out.append(f"### `{b['label']}` — {b['size']} videos / {b['channels']} channels  `{b['key']}`")
        for m in models:
            v = b["verdicts"][m]
            if v["confirmed"]:
                out.append(f"- `{m}` → **CONFIRM** (strength {v.get('signal_strength')}): {v.get('thesis')}")
            elif v["error"]:
                out.append(f"- `{m}` → ERROR {v['error']}")
            else:
                out.append(f"- `{m}` → drop")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
