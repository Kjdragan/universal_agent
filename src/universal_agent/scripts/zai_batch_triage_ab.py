"""Operator-run A/B harness: BATCHED vs PER-ITEM for the P2/P3 ZAI batching gates.

The two high-precision batching features (P2 convergence triage, P3 tutorial
buildability) ship DEFAULT-OFF behind ``UA_INTEL_TRIAGE_BATCH_SIZE`` /
``UA_TUTORIAL_BUILDABILITY_BATCH_SIZE`` (both =1 = legacy per-item). The operator's
quality bar (memory ``feedback_measure_before_change``) is: do NOT flip the default
until a batched-vs-per-item A/B on REAL data shows (a) the call/token reduction AND
(b) that the batched decisions AGREE with the per-item decisions. This harness is
that gate.

It drives the REAL production code (``triage_candidate`` and
``_run_batched_triage`` for P2; ``classify_tutorial_buildability`` and
``classify_tutorial_buildability_batched`` for P3) over the SAME live inputs and
measures, per arm:
  - call count (per-item = N calls; batched = ceil(N/batch_size));
  - input size sent (sum of len(system)+len(user) — a deterministic token proxy;
    the per-item arm repeats the ~12K-char recent_briefs_index PER candidate, which
    the batched arm sends ONCE per chunk — that repetition is the dominant cost);
  - 429 / Fair-Usage errors (the whole point: fewer calls ⇒ fewer FUP rejections);
  - wall-clock latency;
  - VERDICT AGREEMENT: for each item, does the batched verdict == the per-item
    verdict? (ship/skip/defer/retry for triage; buildable bool for buildability.)

It is READ-ONLY on the production DBs (it never writes a convergence_candidates row
or a tutorial_build_judge cache row — it only reads inputs and makes LLM calls), and
it self-throttles (``--delay``) so the A/B does not itself trip the FUP storm it
studies.

Run on the VPS (needs the activity/csi DBs + Infisical-injected ZAI creds):

    UA_DEPLOYMENT_PROFILE=vps /opt/universal_agent/.venv/bin/python \
        -m universal_agent.scripts.zai_batch_triage_ab \
        --target both --limit 24 --batch-size 20 --delay 0.6

Decision rule: adopt the batched default only when agreement is (near-)100% AND the
call/token reduction is material. Where verdicts diverge, read the divergence table
and eyeball which decision is right.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Optional


class _Counter:
    """Accumulates call count / input-char proxy / error counts for one arm."""

    def __init__(self) -> None:
        self.calls = 0
        self.input_chars = 0
        self.errors = 0
        self.fup = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_chars": self.input_chars,
            "est_input_tokens": round(self.input_chars / 4),
            "errors": self.errors,
            "fup_or_429": self.fup,
        }


def _make_counting_call_llm(orig, counter: _Counter, is_fup):
    async def wrapped(*, system: str, user: str, max_tokens: int, **overrides):
        counter.calls += 1
        counter.input_chars += len(system or "") + len(user or "")
        try:
            return await orig(system=system, user=user, max_tokens=max_tokens, **overrides)
        except Exception as exc:  # noqa: BLE001
            counter.errors += 1
            if is_fup(str(exc)):
                counter.fup += 1
            raise

    return wrapped


# ── P2: convergence triage ─────────────────────────────────────────────────


def _load_triage_specs(conn: sqlite3.Connection, *, limit: int) -> list[dict[str, Any]]:
    """Reconstruct un-finalized candidate specs from live convergence_candidates."""
    rows = conn.execute(
        """
        SELECT candidate_id, signatures_json, metadata_json
        FROM convergence_candidates
        WHERE TRIM(verdict) = ''
        ORDER BY detected_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    specs: list[dict[str, Any]] = []
    for r in rows:
        try:
            signatures = json.loads(r["signatures_json"] or "[]")
        except (TypeError, ValueError):
            signatures = []
        if not signatures:
            continue
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        specs.append(
            {
                "candidate_id": r["candidate_id"],
                "signatures": signatures,
                "thesis": str(meta.get("thesis") or ""),
                "value": str(meta.get("value") or ""),
                "candidate_kind": str(meta.get("candidate_kind") or "convergence"),
            }
        )
    return specs


def _ab_triage(conn, specs, *, batch_size, delay, is_fup) -> dict[str, Any]:
    from universal_agent.services import proactive_convergence as pc
    import universal_agent.services.llm_classifier as llm

    idx_text = pc._triage_index_text(conn)

    # PER-ITEM arm (the legacy path). triage_candidate uses pc._call_llm.
    per_counter = _Counter()
    pc_orig = pc._call_llm
    pc._call_llm = _make_counting_call_llm(pc_orig, per_counter, is_fup)
    per_verdicts: dict[str, str] = {}
    t0 = time.monotonic()
    try:
        for s in specs:
            try:
                v = pc.triage_candidate(
                    conn,
                    candidate_kind=s["candidate_kind"],
                    thesis=s["thesis"],
                    value=s["value"],
                    signatures=s["signatures"],
                )
                per_verdicts[s["candidate_id"]] = str(v.get("kind") or "retry")
            except Exception as exc:  # noqa: BLE001
                per_verdicts[s["candidate_id"]] = f"error:{str(exc)[:60]}"
            time.sleep(max(0.0, delay))
    finally:
        pc._call_llm = pc_orig
    per_latency = round((time.monotonic() - t0) * 1000.0, 1)

    # BATCHED arm. _run_batched_triage routes through batched_judge → llm._call_llm.
    bat_counter = _Counter()
    llm_orig = llm._call_llm
    llm._call_llm = _make_counting_call_llm(llm_orig, bat_counter, is_fup)
    prior = os.environ.get("UA_INTEL_TRIAGE_BATCH_SIZE")
    os.environ["UA_INTEL_TRIAGE_BATCH_SIZE"] = str(batch_size)
    t1 = time.monotonic()
    try:
        overrides = pc._run_batched_triage(conn, specs, idx_text=idx_text)
    finally:
        llm._call_llm = llm_orig
        if prior is None:
            os.environ.pop("UA_INTEL_TRIAGE_BATCH_SIZE", None)
        else:
            os.environ["UA_INTEL_TRIAGE_BATCH_SIZE"] = prior
    bat_latency = round((time.monotonic() - t1) * 1000.0, 1)
    bat_verdicts = {cid: str((v or {}).get("kind") or "retry") for cid, v in overrides.items()}

    return _compare(
        kind="triage",
        specs=specs,
        per_verdicts=per_verdicts,
        bat_verdicts=bat_verdicts,
        per_counter=per_counter,
        bat_counter=bat_counter,
        per_latency=per_latency,
        bat_latency=bat_latency,
        batch_size=batch_size,
    )


# ── P3: tutorial buildability ──────────────────────────────────────────────


def _load_buildability_items(
    csi_conn: sqlite3.Connection, activity_conn: sqlite3.Connection, *, limit: int
) -> list[dict[str, Any]]:
    from universal_agent.services import proactive_tutorial_builds as ptb

    rows = csi_conn.execute(
        """
        SELECT e.event_id, e.subject_json, a.category, a.summary_text, a.analysis_json
        FROM events e
        LEFT JOIN rss_event_analysis a ON a.event_id = e.event_id
        WHERE e.source = 'youtube_channel_rss'
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (max(1, int(limit) * 6),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        subject = ptb._json_loads_obj(row["subject_json"])
        analysis = ptb._json_loads_obj(row["analysis_json"])
        summary = str(row["summary_text"] or "")
        if not summary.strip():
            continue
        if not ptb._looks_build_oriented(
            subject=subject, analysis=analysis, category=str(row["category"] or ""), summary=summary
        ):
            continue
        vid = str(subject.get("video_id") or row["event_id"] or "").strip()
        if not vid or vid in seen:
            continue
        # Only A/B the UNCACHED videos (cached ones never reach the LLM in either arm).
        if ptb._get_cached_judge_verdict(activity_conn, vid) is not None:
            continue
        seen.add(vid)
        items.append(
            {
                "video_id": vid,
                "title": str(subject.get("title") or subject.get("media_title") or vid),
                "channel_name": str(subject.get("channel_name") or subject.get("author_name") or ""),
                "summary_text": summary,
            }
        )
        if len(items) >= limit:
            break
    return items


def _ab_buildability(items, *, batch_size, delay, is_fup) -> dict[str, Any]:
    import universal_agent.services.llm_classifier as llm

    # PER-ITEM arm (legacy classify_tutorial_buildability, one asyncio.run each).
    per_counter = _Counter()
    orig = llm._call_llm
    llm._call_llm = _make_counting_call_llm(orig, per_counter, is_fup)
    per_verdicts: dict[str, str] = {}
    t0 = time.monotonic()
    try:
        for it in items:
            try:
                v = asyncio.run(
                    llm.classify_tutorial_buildability(
                        title=it["title"], channel_name=it["channel_name"], summary_text=it["summary_text"]
                    )
                )
                per_verdicts[it["video_id"]] = "buildable" if v.get("buildable") else "no"
            except Exception as exc:  # noqa: BLE001
                per_verdicts[it["video_id"]] = f"error:{str(exc)[:60]}"
            time.sleep(max(0.0, delay))
    finally:
        llm._call_llm = orig
    per_latency = round((time.monotonic() - t0) * 1000.0, 1)

    # BATCHED arm.
    bat_counter = _Counter()
    llm._call_llm = _make_counting_call_llm(orig, bat_counter, is_fup)
    t1 = time.monotonic()
    try:
        out = asyncio.run(llm.classify_tutorial_buildability_batched(items, batch_size=batch_size))
    finally:
        llm._call_llm = orig
    bat_latency = round((time.monotonic() - t1) * 1000.0, 1)
    bat_verdicts = {
        vid: ("buildable" if (v or {}).get("buildable") else ("fallback" if (v or {}).get("method") == "fallback" else "no"))
        for vid, v in out.items()
    }

    return _compare(
        kind="buildability",
        specs=[{"candidate_id": it["video_id"], "thesis": it["title"]} for it in items],
        per_verdicts=per_verdicts,
        bat_verdicts=bat_verdicts,
        per_counter=per_counter,
        bat_counter=bat_counter,
        per_latency=per_latency,
        bat_latency=bat_latency,
        batch_size=batch_size,
    )


# ── shared comparison + reporting ──────────────────────────────────────────


def _compare(*, kind, specs, per_verdicts, bat_verdicts, per_counter, bat_counter, per_latency, bat_latency, batch_size) -> dict[str, Any]:
    ids = [s["candidate_id"] for s in specs]
    agree = disagree = 0
    divergences = []
    for s in specs:
        cid = s["candidate_id"]
        pv = per_verdicts.get(cid, "missing")
        bv = bat_verdicts.get(cid, "missing")
        if pv == bv:
            agree += 1
        else:
            disagree += 1
            divergences.append({"id": cid, "label": str(s.get("thesis") or "")[:80], "per_item": pv, "batched": bv})
    total = len(ids)
    return {
        "kind": kind,
        "n": total,
        "batch_size": batch_size,
        "per_item": {**per_counter.as_dict(), "latency_ms": per_latency},
        "batched": {**bat_counter.as_dict(), "latency_ms": bat_latency},
        "call_reduction": f"{per_counter.calls} → {bat_counter.calls}",
        "input_token_reduction_pct": (
            round(100.0 * (per_counter.input_chars - bat_counter.input_chars) / per_counter.input_chars, 1)
            if per_counter.input_chars else 0.0
        ),
        "agreement": {
            "agree": agree,
            "disagree": disagree,
            "pct": round(100.0 * agree / total, 1) if total else 0.0,
        },
        "divergences": divergences,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    out = ["# ZAI batching A/B — batched vs per-item", "", f"generated {report['generated_at']}", ""]
    for r in report["results"]:
        out.append(f"## {r['kind']} — {r['n']} items @ batch_size={r['batch_size']}")
        out.append("")
        out.append("| Arm | Calls | Est input tokens | 429/FUP | Latency ms |")
        out.append("|---|---:|---:|---:|---:|")
        for arm in ("per_item", "batched"):
            a = r[arm]
            out.append(f"| {arm} | {a['calls']} | {a['est_input_tokens']} | {a['fup_or_429']} | {a['latency_ms']} |")
        out.append("")
        out.append(f"- **Call reduction:** {r['call_reduction']}  ·  **Input-token reduction:** {r['input_token_reduction_pct']}%")
        ag = r["agreement"]
        out.append(f"- **Verdict agreement: {ag['agree']}/{r['n']} = {ag['pct']}%**  (disagree: {ag['disagree']})")
        out.append("")
        if r["divergences"]:
            out.append("### Divergences (read these — which decision is right?)")
            out.append("")
            out.append("| id | label | per-item | batched |")
            out.append("|---|---|---|---|")
            for d in r["divergences"]:
                out.append(f"| `{d['id']}` | {d['label']} | `{d['per_item']}` | `{d['batched']}` |")
            out.append("")
        else:
            out.append("_No divergences — batched matched per-item on every item._")
            out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B batched vs per-item for the P2/P3 ZAI batching gates.")
    ap.add_argument("--target", choices=["triage", "buildability", "both"], default="both")
    ap.add_argument("--limit", type=int, default=20, help="max items per target (keep bounded)")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--delay", type=float, default=0.6, help="inter-call sleep on the per-item arm (self-throttle)")
    ap.add_argument("--out", default="", help="output dir (default <artifacts>/proactive/zai_batch_ab)")
    args = ap.parse_args()

    from universal_agent.infisical_loader import initialize_runtime_secrets

    initialize_runtime_secrets()

    from universal_agent.artifacts import resolve_artifacts_dir
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.rate_limiter import _is_fup_error as is_fup

    started = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []

    with connect_runtime_db(get_activity_db_path()) as conn:
        conn.row_factory = sqlite3.Row

        if args.target in ("triage", "both"):
            specs = _load_triage_specs(conn, limit=args.limit)
            if specs:
                print(f"[triage] {len(specs)} un-finalized candidates")
                results.append(_ab_triage(conn, specs, batch_size=args.batch_size, delay=args.delay, is_fup=is_fup))
            else:
                print("[triage] no un-finalized candidates in the DB — skipping")

        if args.target in ("buildability", "both"):
            csi_path = os.environ.get("UA_CSI_DB_PATH", "/opt/universal_agent/AGENT_RUN_WORKSPACES/csi.db")
            if Path(csi_path).exists():
                with sqlite3.connect(csi_path) as csi_conn:
                    csi_conn.row_factory = sqlite3.Row
                    items = _load_buildability_items(csi_conn, conn, limit=args.limit)
                if items:
                    print(f"[buildability] {len(items)} uncached build-oriented videos")
                    results.append(_ab_buildability(items, batch_size=args.batch_size, delay=args.delay, is_fup=is_fup))
                else:
                    print("[buildability] no uncached build-oriented videos — skipping")
            else:
                print(f"[buildability] csi.db not found at {csi_path} — skipping")

    report = {
        "ok": True,
        "generated_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "params": {"limit": args.limit, "batch_size": args.batch_size, "delay_s": args.delay},
        "results": results,
    }

    out_dir = Path(args.out) if args.out else (resolve_artifacts_dir() / "proactive" / "zai_batch_ab")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"ab_{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    md = _render_markdown(report)
    (out_dir / f"ab_{stamp}.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"\nJSON: {out_dir / f'ab_{stamp}.json'}\nMD:   {out_dir / f'ab_{stamp}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
