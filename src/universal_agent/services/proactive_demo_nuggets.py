"""End-of-day "golden-nuggets" demo judge (Component D of the proactive demo
engine migration — see project_docs/proactive_demo_engine_migration.md).

The normal proactive flow builds up to 3 demos/day (PR-1's cap in
``priority_dispatcher.dispatch_claimed``). Strong candidates that arrive LATER in
the day get priced out. This service — fired once at end of day by
``scripts/proactive_demo_nuggets_cron.py`` — critically re-judges the day's
REMAINING un-built ``tutorial_build`` candidates and builds 0-2 EXTRA "golden
nugget" demos (hard ceiling 5/day total), emailing each. Dark-factory: it builds
NONE if nothing clears the bar.

Design (approved): the cron BUILDS DIRECTLY via demo_factory's ``build_demo.py``
— exactly like the operator's ``/demo`` — instead of going through the agentic
VP-mission / ``priority_dispatcher`` path. The normal dispatch is agentic
(Simone -> vp_dispatch_mission) and cap-gated; invoking it from a cron would
fight PR-1's cap. So the budget is self-limited HERE and PR-1's cap is untouched.

Accounting is over the operator's **America/Chicago day**
(``utils.day_boundary.chicago_day_start_iso`` — the ONE boundary shared with
PR-1's cap and the inflow ceiling): the 5/day hard ceiling and ``built_today``
are computed against Chicago local midnight, so ``built_today +
nuggets_built_this_run <= daily_max`` always holds. The cron fires at 23:50
Chicago, where a UTC boundary would undercount the Chicago day and could exceed
5/day. See :func:`_count_built_today` for the double-count reasoning.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import Any, Callable, Optional

from universal_agent.feature_flags import (
    proactive_demo_daily_max,
    proactive_demo_factory_run_argv,
    proactive_demo_factory_script,
    proactive_demo_nuggets_max,
)
from universal_agent.services.worker_exit_classifier import classify_worker_exit

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE_ROOT = "/home/ua/lrepos"
_DEFAULT_MIN_SCORE = 7.0
# A full demo_factory land (Opus /goal build + verify + fidelity-eval) is slow;
# size the per-build subprocess cap generously. The systemd unit's
# TimeoutStartSec must exceed nuggets_max * this + slack.
_DEFAULT_BUILD_TIMEOUT_SECONDS = 3600


_JUDGE_SYSTEM_PROMPT = """You are the end-of-day GOLDEN-NUGGETS judge for a proactive demo factory. Earlier today the factory already built its normal quota of demos. You are now looking ONLY at the LEFTOVER candidates that did NOT get built. Your job is to find the RARE leftover genuinely worth spending a full autonomous build on, and to REJECT the rest.

Be highly critical and skeptical. MOST candidates should NOT clear the bar. A candidate clears the bar ONLY if ALL of these hold:
- It describes a SPECIFIC, concrete technical capability (a named SDK, API, feature, model, or technique) — not vague hype, news, opinion, or a talking-head take.
- A coding agent could actually BUILD a small runnable demo that exercises that capability end-to-end TODAY, against a real endpoint.
- The result would be a genuinely VALUABLE reference demo — novel enough to be worth the build, not a near-duplicate of an obvious existing capability.

Reject anything promotional, speculative, a pure news/reaction/opinion piece, too broad to scope into one demo, or trivially derivative.

For EACH candidate below, output ONE line of JSON and NOTHING else:
{"index": <int>, "score": <0-10, one decimal>, "build": <true|false>, "reason": "<1-2 sentences; the deciding factor>"}

Scoring: reserve 8+ for a clearly buildable, specific, novel capability; the default should be 2-5. Set "build": true ONLY for a candidate you would stake a full build on. Output ONLY the JSON lines, one per candidate, no preamble, no wrapping array.
"""


# ── budget math (pure, unit-testable) ─────────────────────────────────────────
def _compute_budget(built_today: int, *, daily_max: int, nuggets_max: int) -> int:
    """``min(nuggets_max, max(0, daily_max - built_today))`` — the 5/day ceiling
    always wins over the per-run nuggets cap."""
    return min(max(0, int(nuggets_max)), max(0, int(daily_max) - int(built_today)))


def _workspace_root() -> Path:
    return Path(
        (os.getenv("UA_PROACTIVE_DEMO_WORKSPACE_ROOT") or "").strip()
        or _DEFAULT_WORKSPACE_ROOT
    )


def _min_score() -> float:
    raw = (os.getenv("UA_PROACTIVE_DEMO_NUGGETS_MIN_SCORE") or "").strip()
    if not raw:
        return _DEFAULT_MIN_SCORE
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MIN_SCORE


def _build_timeout_seconds() -> int:
    raw = (os.getenv("UA_PROACTIVE_DEMO_NUGGETS_BUILD_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_BUILD_TIMEOUT_SECONDS
    try:
        return max(60, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_BUILD_TIMEOUT_SECONDS


# ── built-today accounting ────────────────────────────────────────────────────
def _count_built_today(conn) -> tuple[int, int]:
    """Return ``(dispatched_today, nuggets_today)`` over the America/Chicago day.

    ``dispatched_today`` reuses PR-1's authoritative counter
    (``priority_dispatcher._count_dispatched_tutorial_builds_today``, which now
    counts over the SHARED ``chicago_day_start_iso`` boundary): normal-flow
    tutorial_build builds carry ``metadata.delegation.delegated_at``.
    ``nuggets_today`` counts tutorial_build rows this cron already built today
    (``metadata.nugget_build.built_at``) over the same Chicago boundary — those
    are built DIRECTLY via build_demo.py and NEVER dispatched, so they carry no
    ``delegated_at`` and are disjoint from ``dispatched_today``. Summing them is
    therefore not a double-count in the normal case. The only overlap is the rare
    event where an operator later approves + dispatches a row this cron already
    nugget-built; that row would then be counted in BOTH sets, over-counting by 1
    — which only shrinks the budget (the SAFE direction; the ceiling can never be
    exceeded).
    """
    from universal_agent.services.priority_dispatcher import (
        _count_dispatched_tutorial_builds_today,
    )
    from universal_agent.utils.day_boundary import chicago_day_start_iso

    dispatched = int(_count_dispatched_tutorial_builds_today(conn))

    day_start = chicago_day_start_iso()
    nuggets = 0
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM task_hub_items
            WHERE source_kind = 'tutorial_build'
              AND json_extract(metadata_json, '$.nugget_build.built_at') >= ?
            """,
            (day_start,),
        ).fetchone()
        nuggets = int(row[0] or 0) if row else 0
    except Exception:  # noqa: BLE001 — counting must never break the cron
        # Fallback: scan in Python (older SQLite without JSON1).
        try:
            for r in conn.execute(
                "SELECT metadata_json FROM task_hub_items WHERE source_kind = 'tutorial_build'"
            ).fetchall():
                try:
                    meta = json.loads(
                        (r["metadata_json"] if hasattr(r, "keys") else r[0]) or "{}"
                    )
                except Exception:
                    continue
                built_at = str(((meta.get("nugget_build") or {}).get("built_at")) or "")
                if built_at >= day_start:
                    nuggets += 1
        except Exception:  # noqa: BLE001
            nuggets = 0
    return dispatched, nuggets


# ── candidate gathering ───────────────────────────────────────────────────────
def _demo_dir_exists(root: Path, video_slug: str) -> bool:
    """True if this candidate was already built (a landed repo dir exists)."""
    return (root / f"demo-proactive-{video_slug}").is_dir() or (
        root / f"demo-undemoable-{video_slug}"
    ).is_dir()


def _gather_candidates(conn, *, root: Path) -> list[dict[str, Any]]:
    """The day's REMAINING un-built tutorial_build candidates.

    Source: ``proactive_tutorial_builds.list_pending_approval_builds`` — the
    pending-approval rows (``source_kind='tutorial_build'``, ``status=open``,
    ``agent_ready=0``, ``pending-approval`` label) that the normal flow queued
    but never dispatched (priced out by PR-1's 3/day cap). Excludes any already
    built by this cron (``metadata.nugget_build``) or whose landed repo dir
    already exists on disk.
    """
    from universal_agent import task_hub
    from universal_agent.services.proactive_tutorial_builds import (
        list_pending_approval_builds,
    )
    from universal_agent.services.tutorial_demo_finalize import proactive_demo_slug

    pending = list_pending_approval_builds(conn, limit=200)
    out: list[dict[str, Any]] = []
    for row in pending:
        task_id = str(row.get("task_id") or "").strip()
        video_title = str(row.get("video_title") or row.get("title") or "").strip()
        if not task_id or not video_title:
            continue
        video_slug = proactive_demo_slug(video_title)
        if _demo_dir_exists(root, video_slug):
            continue
        # Skip anything this cron already nugget-built (idempotent re-fire).
        item = task_hub.get_item(conn, task_id)
        meta = item.get("metadata") if isinstance(item, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        if isinstance(meta.get("nugget_build"), dict):
            continue
        plan = meta.get("extraction_plan") if isinstance(meta.get("extraction_plan"), dict) else {}
        out.append(
            {
                "task_id": task_id,
                "video_id": str(row.get("video_id") or ""),
                "video_title": video_title,
                "video_url": str(row.get("video_url") or ""),
                "channel_name": str(row.get("channel_name") or ""),
                "video_slug": video_slug,
                "summary": str(plan.get("summary") or "")[:800],
            }
        )
    return out


# ── LLM critical-eye judge ────────────────────────────────────────────────────
def _build_judge_user_message(candidates: list[dict[str, Any]]) -> str:
    lines = ["Score the following leftover candidates:", ""]
    for idx, cand in enumerate(candidates):
        summary = " ".join(str(cand.get("summary") or "").split())
        lines.extend(
            [
                f"### Candidate index={idx}",
                f"title: {cand.get('video_title') or ''}",
                f"channel: {cand.get('channel_name') or '(unknown)'}",
                f"summary: {summary or '(none)'}",
                "",
            ]
        )
    return "\n".join(lines)


def _parse_judge_lines(raw: str, n: int) -> dict[int, dict[str, Any]]:
    """Parse one ``{"index", "score", "build", "reason"}`` JSON object per line."""
    out: dict[int, dict[str, Any]] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("nuggets judge: skipping malformed line: %s", line[:120])
            continue
        if not isinstance(obj, dict) or "index" not in obj:
            continue
        try:
            idx = int(obj.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < n):
            continue
        try:
            score = float(obj.get("score"))
        except (TypeError, ValueError):
            score = 0.0
        out[idx] = {
            "score": max(0.0, min(10.0, round(score, 1))),
            "build": bool(obj.get("build")),
            "reason": str(obj.get("reason") or "").strip()[:500],
        }
    return out


def _default_call_llm(system: str, user: str) -> str:
    """Reuse the demo-triage LLM client + model config (resolved from env, never
    hardcoded here) — the same client ``csi_demo_triage_ranker`` uses."""
    from universal_agent.services.csi_demo_triage_ranker import _call_llm

    return _call_llm(system=system, user=user)


def _judge_candidates(
    candidates: list[dict[str, Any]],
    *,
    call_llm: Optional[Callable[[str, str], str]] = None,
) -> list[dict[str, Any]]:
    """Return per-candidate verdicts aligned 1:1 with ``candidates``. A candidate
    with no returned verdict fails closed (score 0, build False)."""
    n = len(candidates)
    verdicts: list[dict[str, Any]] = [
        {"score": 0.0, "build": False, "reason": "no verdict returned"} for _ in range(n)
    ]
    if n == 0:
        return verdicts
    call = call_llm or _default_call_llm
    # Eureka bias: append demo_factory's capability shelf so the golden-nuggets
    # judge prefers landmark leftovers over me-toos. Fail-safe — an empty block
    # leaves the system prompt byte-identical to before.
    from universal_agent.services.demo_shelf_context import capability_shelf_block

    system = _JUDGE_SYSTEM_PROMPT + capability_shelf_block()
    try:
        raw = call(system, _build_judge_user_message(candidates))
    except Exception as exc:  # noqa: BLE001 — a judge failure drops everything, never builds
        logger.warning("nuggets judge: LLM call failed: %s", exc)
        for v in verdicts:
            v["reason"] = f"judge_error: {type(exc).__name__}"
        return verdicts
    for idx, parsed in _parse_judge_lines(raw, n).items():
        verdicts[idx] = parsed
    return verdicts


# ── build + register + email ──────────────────────────────────────────────────
def _sanitize_one_line(text: str) -> str:
    """Collapse to one clean line (defense-in-depth; argv already avoids the
    shell). Mirrors proactive_tutorial_builds._sanitize_one_line's intent."""
    one_line = " ".join(str(text or "").split()).replace('"', "'")
    for ch in ("$", "`", "\\"):
        one_line = one_line.replace(ch, "")
    return one_line[:200]


def _build_argv(cand: dict[str, Any], *, root: Path) -> list[str]:
    """The build_demo.py argv, mirroring
    proactive_tutorial_builds._demo_factory_override_block (a FULL land).

    Runs build_demo.py UNDER the demo_factory uv venv via
    ``proactive_demo_factory_run_argv`` (``uv run --project <demo_factory>
    python <driver>``) — NOT bare python3: the eval stage imports google-genai,
    which the VPS bare interpreter lacks.
    """
    driver = proactive_demo_factory_script()
    video_slug = cand["video_slug"]
    demo_id = f"proactive-{video_slug}"
    seed = _sanitize_one_line(cand.get("video_title") or "") or "this tutorial"
    title = _sanitize_one_line(cand.get("video_title") or "") or demo_id
    argv = proactive_demo_factory_run_argv(driver) + [
        f"Build a runnable demo of the capability from this tutorial: {seed}",
        "--demo-id",
        demo_id,
        "--slug",
        f"proactive-{video_slug}",
        "--title",
        title,
        "--workspace-root",
        str(root),
    ]
    seed_url = str(cand.get("video_url") or "").strip()
    if seed_url:
        argv += ["--seed-url", seed_url]
    argv += [
        "--endpoint-required", "any", "--promote", "--skill-tier", "library",
        "--cody-mode", "hybrid", "--video",
    ]
    return argv


def _default_build_runner(argv: list[str]) -> subprocess.CompletedProcess:
    """Run build_demo.py as a subprocess (the injection seam tests replace)."""
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_build_timeout_seconds(),
    )


def _register_and_email(
    conn,
    cand: dict[str, Any],
    *,
    root: Path,
    notifier: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Register the landed demo (symlink + manifest key-alias + undemoable
    rename) via ``tutorial_demo_finalize.finalize_tutorial_build_demo``, then send
    the built email. Returns a per-build summary dict."""
    from universal_agent.services.demo_built_notifier import notify_demo_built
    from universal_agent.services.tutorial_demo_finalize import (
        finalize_tutorial_build_demo,
    )

    video_slug = cand["video_slug"]
    demo_id = f"proactive-{video_slug}"
    built_dir = root / f"demo-proactive-{video_slug}"

    # finalize handles: demo-proactive -> demo-undemoable rename (reading the
    # landed manifest status), /opt/ua_demos symlink registration, and the
    # manifest ts->timestamp / marker_verified key-alias for dashboard fidelity.
    finalize = finalize_tutorial_build_demo(
        task_id=cand["task_id"],
        task_meta={
            "video_id": cand.get("video_id"),
            "video_title": cand.get("video_title"),
            "video_url": cand.get("video_url"),
        },
        mission={},
        mission_id=demo_id,
        workspace_candidates=[str(built_dir)],
    )
    workspace_dir = str(finalize.get("workspace_dir") or built_dir)
    undemoable = bool(finalize.get("undemoable"))

    notify = notifier or notify_demo_built
    emailed = False
    try:
        email_result = asyncio.run(
            notify(
                demo_id=demo_id,
                title=cand.get("video_title") or demo_id,
                capability=cand.get("video_title") or "",
                workspace_dir=workspace_dir,
                build_engine="demo_factory",
                review_required=False,
            )
        )
        emailed = bool((email_result or {}).get("emailed"))
    except Exception:  # noqa: BLE001 — email best-effort, never fails the build
        logger.warning("nuggets: built email failed for %s", demo_id, exc_info=True)

    # Mark the candidate built so a same-day re-fire (and the daily count) sees it.
    try:
        from universal_agent import task_hub

        task_hub.upsert_item(
            conn,
            {
                "task_id": cand["task_id"],
                "metadata": {
                    "nugget_build": {
                        "state": "undemoable" if undemoable else "built",
                        "demo_id": demo_id,
                        "workspace_dir": workspace_dir,
                        "undemoable": undemoable,
                        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                },
            },
        )
        conn.commit()
    except Exception:  # noqa: BLE001
        logger.warning("nuggets: nugget_build mark failed for %s", demo_id, exc_info=True)

    return {
        "task_id": cand["task_id"],
        "demo_id": demo_id,
        "video_title": cand.get("video_title"),
        "workspace_dir": workspace_dir,
        "undemoable": undemoable,
        "emailed": emailed,
    }


# ── entrypoint ────────────────────────────────────────────────────────────────
def select_and_build_nuggets(
    *,
    dry_run: bool,
    conn: sqlite3.Connection | None = None,
    call_llm: Optional[Callable[[str, str], str]] = None,
    build_runner: Optional[Callable[[list[str]], subprocess.CompletedProcess]] = None,
    notifier: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """Judge the day's remaining un-built candidates and build 0-``budget``
    golden nuggets. ``dry_run`` builds NOTHING (reports the judge's picks).

    Injection seams (all default to the real implementations): ``conn`` (runtime
    activity DB), ``call_llm`` (the critical judge), ``build_runner`` (build_demo
    subprocess), ``notifier`` (built-email coroutine fn) — the unit tests replace
    these so no LLM/subprocess/email runs.
    """
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    daily_max = proactive_demo_daily_max()
    nuggets_max = proactive_demo_nuggets_max()
    min_score = _min_score()
    root = _workspace_root()

    summary: dict[str, Any] = {
        "ok": True,
        "dry_run": bool(dry_run),
        "started_at": started_at,
        "finished_at": "",
        "daily_max": daily_max,
        "nuggets_max": nuggets_max,
        "min_score": min_score,
        "dispatched_today": 0,
        "nuggets_today": 0,
        "built_today": 0,
        "budget": 0,
        "candidates_considered": 0,
        "selected": [],
        "built": [],
        "dropped": [],
        "build_failures": [],
        "error": None,
    }

    own_conn = conn is None
    if conn is None:
        from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

        conn = connect_runtime_db(get_activity_db_path())

    try:
        dispatched_today, nuggets_today = _count_built_today(conn)
        built_today = dispatched_today + nuggets_today
        budget = _compute_budget(built_today, daily_max=daily_max, nuggets_max=nuggets_max)
        summary.update(
            {
                "dispatched_today": dispatched_today,
                "nuggets_today": nuggets_today,
                "built_today": built_today,
                "budget": budget,
            }
        )
        if budget <= 0:
            logger.info(
                "nuggets: budget=0 (built_today=%d, daily_max=%d) — nothing to build",
                built_today, daily_max,
            )
            summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return summary

        candidates = _gather_candidates(conn, root=root)
        summary["candidates_considered"] = len(candidates)
        if not candidates:
            logger.info("nuggets: no remaining un-built candidates")
            summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return summary

        verdicts = _judge_candidates(candidates, call_llm=call_llm)

        # Rank the bar-clearers by score desc; everything else is a logged drop.
        scored = []
        for cand, v in zip(candidates, verdicts):
            entry = {
                "task_id": cand["task_id"],
                "video_title": cand["video_title"],
                "video_slug": cand["video_slug"],
                "video_id": cand.get("video_id"),
                "video_url": cand.get("video_url"),
                "channel_name": cand.get("channel_name"),
                "score": v["score"],
                "build": v["build"],
                "reason": v["reason"],
            }
            scored.append(entry)
        scored.sort(key=lambda e: e["score"], reverse=True)

        selected: list[dict[str, Any]] = []
        for entry in scored:
            clears = bool(entry["build"]) and float(entry["score"]) >= min_score
            if clears and len(selected) < budget:
                selected.append(entry)
            else:
                # LOG every dropped candidate with the judge's reason (no silent
                # truncation) — includes bar-clearers beyond budget.
                drop_reason = entry["reason"]
                if clears and len(selected) >= budget:
                    drop_reason = f"over budget ({budget}); {drop_reason}"
                logger.info(
                    "nuggets: DROPPED %s score=%.1f build=%s: %s",
                    entry["video_title"], entry["score"], entry["build"], drop_reason,
                )
                summary["dropped"].append(
                    {
                        "task_id": entry["task_id"],
                        "video_title": entry["video_title"],
                        "score": entry["score"],
                        "build": entry["build"],
                        "reason": drop_reason,
                    }
                )

        summary["selected"] = [
            {"task_id": e["task_id"], "video_title": e["video_title"],
             "score": e["score"], "reason": e["reason"], "demo_id": f"proactive-{e['video_slug']}"}
            for e in selected
        ]

        if dry_run:
            for e in selected:
                logger.info(
                    "nuggets[dry-run]: would build %s (score=%.1f)",
                    e["video_title"], e["score"],
                )
            summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return summary

        run_build = build_runner or _default_build_runner
        built_this_run = 0
        for e in selected:
            # Hard ceiling belt-and-suspenders: never exceed daily_max.
            if built_today + built_this_run >= daily_max:
                break
            argv = _build_argv(e, root=root)
            logger.info("nuggets: building %s -> %s", e["video_title"], e["video_slug"])
            # Task Hub Observability Protocol: classify the build subprocess's
            # exit (timeout / signal / rc) into an outcome bucket for the log +
            # failure record, so a hung or crashed golden-nuggets build is visible.
            try:
                proc = run_build(argv)
            except subprocess.TimeoutExpired as exc:
                worker_exit = classify_worker_exit(return_code=None, was_timeout_killed=True)
                logger.warning(
                    "nuggets: build TIMEOUT for %s (%s): %s",
                    e["video_slug"], worker_exit.outcome, exc,
                )
                summary["build_failures"].append(
                    {"task_id": e["task_id"], "demo_id": f"proactive-{e['video_slug']}",
                     "returncode": None, "worker_exit": worker_exit.outcome,
                     "error": f"timeout after {_build_timeout_seconds()}s"}
                )
                continue
            except Exception as exc:  # noqa: BLE001 — a build crash drops that one, continue
                worker_exit = classify_worker_exit(return_code=None, was_signaled=True)
                logger.warning(
                    "nuggets: build subprocess raised for %s (%s): %s",
                    e["video_slug"], worker_exit.outcome, exc,
                )
                summary["build_failures"].append(
                    {"task_id": e["task_id"], "demo_id": f"proactive-{e['video_slug']}",
                     "returncode": None, "worker_exit": worker_exit.outcome,
                     "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            rc = int(getattr(proc, "returncode", 1) or 0)
            worker_exit = classify_worker_exit(return_code=rc)
            if rc != 0:
                tail = str(getattr(proc, "stderr", "") or "")[-800:]
                logger.warning(
                    "nuggets: build FAILED rc=%d (%s) for %s: %s",
                    rc, worker_exit.outcome, e["video_slug"], tail,
                )
                summary["build_failures"].append(
                    {"task_id": e["task_id"], "demo_id": f"proactive-{e['video_slug']}",
                     "returncode": rc, "worker_exit": worker_exit.outcome, "error": tail}
                )
                continue
            built = _register_and_email(conn, e, root=root, notifier=notifier)
            summary["built"].append(built)
            built_this_run += 1

        summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return summary
    except Exception as exc:  # noqa: BLE001 — top-level guard; the cron must exit cleanly
        logger.exception("nuggets: top-level failure")
        summary["ok"] = False
        summary["error"] = f"{type(exc).__name__}: {exc}"
        summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return summary
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
