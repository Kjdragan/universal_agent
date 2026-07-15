"""Component D — end-of-day golden-nuggets demo judge.

The normal proactive flow builds up to 3 demos/day (PR-1's cap). This end-of-day
cron critically re-judges the day's REMAINING un-built ``tutorial_build``
candidates and builds 0-2 EXTRA "golden nuggets" directly via build_demo.py,
never exceeding the 5/day hard ceiling. These tests mock the LLM judge, the build
subprocess, and the built-email notifier — no LLM / subprocess / email runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from universal_agent import task_hub
from universal_agent.services import proactive_demo_nuggets as nuggets
from universal_agent.utils.day_boundary import chicago_day_start_iso


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _pin_flags(monkeypatch, tmp_path):
    # Pin the ceiling/cap so a polluted env can't shift the boundary.
    monkeypatch.setenv("UA_PROACTIVE_DEMO_DAILY_MAX", "5")
    monkeypatch.setenv("UA_PROACTIVE_DEMO_NUGGETS_MAX", "2")
    monkeypatch.delenv("UA_PROACTIVE_DEMO_NUGGETS_MIN_SCORE", raising=False)  # default 7.0
    monkeypatch.setenv("UA_PROACTIVE_DEMO_WORKSPACE_ROOT", str(tmp_path / "lrepos"))
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "ua_demos"))
    (tmp_path / "lrepos").mkdir(parents=True, exist_ok=True)
    yield


def _seed_pending_candidate(conn, task_id, *, video_title, video_id="", summary=""):
    """A pending-approval tutorial_build row (agent_ready=0) — exactly the shape
    ``list_pending_approval_builds`` returns (the candidate source)."""
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "tutorial_build",
            "title": f"Build private tutorial repo: {video_title}",
            "description": "pending-approval build",
            "status": task_hub.TASK_STATUS_OPEN,
            "agent_ready": False,
            "labels": ["pending-approval", "tutorial-build", "codie", "code"],
            "metadata": {
                "video_id": video_id or task_id,
                "video_title": video_title,
                "video_url": f"https://youtu.be/{video_id or task_id}",
                "channel_name": "Test Channel",
                "approval_state": "pending_approval",
                "extraction_plan": {"summary": summary},
            },
        },
    )


def _verdict_llm(mapping):
    """Build a fake ``call_llm(system, user)`` that returns judge JSON lines.

    ``mapping``: index -> (score, build, reason).
    """
    def _call(system, user):
        lines = []
        for idx, (score, build, reason) in mapping.items():
            lines.append(json.dumps({"index": idx, "score": score, "build": build, "reason": reason}))
        return "\n".join(lines)

    return _call


def _make_build_runner(*, status="ok", record=None):
    """Fake build_runner. ``status='ok'`` creates the landed demo-proactive dir
    (with a manifest whose ``status`` is ``un-demoable`` when status='undemoable')
    and returns rc 0; ``status='fail'`` returns rc 1."""
    def _run(argv):
        if record is not None:
            record.append(list(argv))
        # --slug proactive-<slug>
        slug = argv[argv.index("--slug") + 1].removeprefix("proactive-")
        root = Path(argv[argv.index("--workspace-root") + 1])
        if status == "fail":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
        demo_dir = root / f"demo-proactive-{slug}"
        demo_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "demo_id": f"proactive-{slug}",
            "status": "un-demoable" if status == "undemoable" else "passed",
            "acceptance_passed": status != "undemoable",
        }
        (demo_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (demo_dir / "README.md").write_text("## Run\nuv run python main.py\n", encoding="utf-8")
        (demo_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="built", stderr="")

    return _run


async def _noop_notifier(**kwargs):
    return {"emailed": True, "video_url": "", "exhibit_url": ""}


# ── budget math (pure) ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "built_today,expected",
    [(0, 2), (3, 2), (4, 1), (5, 0), (6, 0)],
)
def test_compute_budget(built_today, expected):
    assert nuggets._compute_budget(built_today, daily_max=5, nuggets_max=2) == expected


def test_compute_budget_nuggets_cap_binds_when_room_is_larger():
    # Plenty of daily room, but the per-run nuggets cap (2) is the binding limit.
    assert nuggets._compute_budget(0, daily_max=10, nuggets_max=2) == 2


# ── FIX 1: build_demo.py runs under the demo_factory uv venv, not bare python3 ─
def test_build_argv_runs_under_demo_factory_uv_venv(monkeypatch):
    monkeypatch.setenv("UA_UV_BIN", "/fake/uv")
    monkeypatch.setenv(
        "UA_PROACTIVE_DEMO_FACTORY_SCRIPT",
        "/home/ua/lrepos/demo_factory/scripts/build_demo.py",
    )
    argv = nuggets._build_argv(
        {"video_slug": "foo-bar", "video_title": "Foo Bar", "video_url": "https://x/y"},
        root=Path("/home/ua/lrepos"),
    )
    # uv run --project <demo_factory> python <driver>  (google-genai lives there).
    assert argv[:6] == [
        "/fake/uv", "run", "--project", "/home/ua/lrepos/demo_factory",
        "python", "/home/ua/lrepos/demo_factory/scripts/build_demo.py",
    ]
    assert "python3" not in argv  # never the bare interpreter
    # the land flags still ride after the prefix
    assert argv[argv.index("--slug") + 1] == "proactive-foo-bar"
    assert argv[argv.index("--workspace-root") + 1] == "/home/ua/lrepos"
    assert "--promote" in argv and "--skill-tier" in argv
    # operator decision 2026-07-02: proactive builds default to hybrid
    # (build on Anthropic-Max, verify/runtime on ZAI) and render the video
    assert argv[argv.index("--cody-mode") + 1] == "hybrid"
    assert "--video" in argv


# ── FIX 2: built_today counted over the America/Chicago day, disjoint sets ─────
def test_count_built_today_disjoint_over_chicago_day(conn):
    now = datetime.now(timezone.utc).isoformat()
    # A normal-flow build dispatched today (delegation marker).
    task_hub.upsert_item(conn, {
        "task_id": "tb-dispatched", "source_kind": "tutorial_build", "title": "d",
        "status": task_hub.TASK_STATUS_DELEGATED, "agent_ready": False,
        "metadata": {"delegation": {"delegated_at": now}},
    })
    # A nugget this cron built today (nugget_build marker, never dispatched).
    task_hub.upsert_item(conn, {
        "task_id": "tb-nugget", "source_kind": "tutorial_build", "title": "n",
        "status": task_hub.TASK_STATUS_OPEN, "agent_ready": False,
        "metadata": {"nugget_build": {"built_at": now, "state": "built"}},
    })
    # A nugget built long ago (before today's Chicago midnight) must NOT count.
    task_hub.upsert_item(conn, {
        "task_id": "tb-old-nugget", "source_kind": "tutorial_build", "title": "o",
        "status": task_hub.TASK_STATUS_OPEN, "agent_ready": False,
        "metadata": {"nugget_build": {"built_at": "2000-01-01T00:00:00+00:00"}},
    })
    dispatched, nuggets_today = nuggets._count_built_today(conn)
    assert dispatched == 1
    assert nuggets_today == 1  # the year-2000 nugget is excluded


def test_chicago_day_start_boundary():
    start = chicago_day_start_iso()
    assert start.endswith("+00:00")  # a lexicographically-comparable UTC ISO string
    now = datetime.now(timezone.utc).isoformat()
    assert start <= now  # today's local midnight is always in the past
    assert "2000-01-01T00:00:00+00:00" < start  # a year-2000 stamp precedes today


# ── judge selects <= budget ───────────────────────────────────────────────────
def test_judge_selects_at_most_budget(conn):
    # built_today=0 -> budget=2. Three candidates all clear the bar; only 2 build.
    _seed_pending_candidate(conn, "tb-a", video_title="Build a RAG agent with the ADK")
    _seed_pending_candidate(conn, "tb-b", video_title="Streaming tool use in the Agent SDK")
    _seed_pending_candidate(conn, "tb-c", video_title="Structured outputs with Gemini")
    calls: list[list[str]] = []
    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "specific + novel"),
                               1: (8.5, True, "buildable"),
                               2: (7.5, True, "ok")}),
        build_runner=_make_build_runner(record=calls),
        notifier=_noop_notifier,
    )
    assert result["budget"] == 2
    assert len(result["built"]) == 2  # never exceeds budget
    assert len(calls) == 2
    # The lowest-scored clearer is dropped as "over budget" (logged, not silent).
    assert len(result["dropped"]) == 1
    assert "over budget" in result["dropped"][0]["reason"]


def test_judge_below_threshold_builds_nothing(conn):
    # All candidates score below the 7.0 threshold or build=False -> 0 built.
    _seed_pending_candidate(conn, "tb-a", video_title="Vague hype about AI")
    _seed_pending_candidate(conn, "tb-b", video_title="A reaction to some news")
    calls: list[list[str]] = []
    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (3.0, False, "vague"), 1: (6.0, True, "below bar")}),
        build_runner=_make_build_runner(record=calls),
        notifier=_noop_notifier,
    )
    assert result["built"] == []
    assert calls == []
    assert len(result["dropped"]) == 2


# ── budget=0 when the day's ceiling is already met ────────────────────────────
def test_budget_zero_when_ceiling_reached(conn, monkeypatch):
    # 5 already dispatched today -> budget 0 -> nothing gathered/judged/built.
    monkeypatch.setattr(
        nuggets, "_count_built_today", lambda c: (5, 0)
    )
    _seed_pending_candidate(conn, "tb-a", video_title="Build a RAG agent")
    calls: list[list[str]] = []
    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "great")}),
        build_runner=_make_build_runner(record=calls),
        notifier=_noop_notifier,
    )
    assert result["budget"] == 0
    assert result["built"] == []
    assert calls == []


# ── dry-run builds nothing ────────────────────────────────────────────────────
def test_dry_run_builds_nothing(conn):
    _seed_pending_candidate(conn, "tb-a", video_title="Build a RAG agent with the ADK")
    calls: list[list[str]] = []
    result = nuggets.select_and_build_nuggets(
        dry_run=True,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "great")}),
        build_runner=_make_build_runner(record=calls),
        notifier=_noop_notifier,
    )
    assert result["dry_run"] is True
    assert calls == []  # build subprocess never invoked
    assert result["built"] == []
    assert len(result["selected"]) == 1  # but the pick is reported


# ── un-demoable rename is honored ─────────────────────────────────────────────
def test_undemoable_rename_is_honored(conn, tmp_path):
    from universal_agent.services.tutorial_demo_finalize import proactive_demo_slug

    title = "Conceptual talk with no runnable demo"
    slug = proactive_demo_slug(title)
    _seed_pending_candidate(conn, "tb-u", video_title=title)
    root = tmp_path / "lrepos"

    emails: list[dict] = []

    async def _capture_notifier(**kwargs):
        emails.append(kwargs)
        return {"emailed": True}

    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (8.0, True, "worth a shot")}),
        build_runner=_make_build_runner(status="undemoable"),
        notifier=_capture_notifier,
    )

    assert len(result["built"]) == 1
    built = result["built"][0]
    assert built["undemoable"] is True
    # The repo dir was renamed demo-proactive-* -> demo-undemoable-*.
    assert not (root / f"demo-proactive-{slug}").exists()
    assert (root / f"demo-undemoable-{slug}").is_dir()
    assert built["workspace_dir"] == str(root / f"demo-undemoable-{slug}")
    # Email fired against the renamed dir.
    assert emails and emails[0]["workspace_dir"] == str(root / f"demo-undemoable-{slug}")
    # The candidate is marked built (won't be re-judged on a same-day re-fire).
    marked = task_hub.get_item(conn, "tb-u")["metadata"]["nugget_build"]
    assert marked["state"] == "undemoable" and marked["undemoable"] is True


# ── already-built candidates are excluded from re-judging ─────────────────────
def test_existing_demo_dir_excludes_candidate(conn, tmp_path):
    from universal_agent.services.tutorial_demo_finalize import proactive_demo_slug

    title = "Build a RAG agent with the ADK"
    slug = proactive_demo_slug(title)
    (tmp_path / "lrepos" / f"demo-proactive-{slug}").mkdir(parents=True)
    _seed_pending_candidate(conn, "tb-a", video_title=title)
    calls: list[list[str]] = []
    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "great")}),
        build_runner=_make_build_runner(record=calls),
        notifier=_noop_notifier,
    )
    assert result["candidates_considered"] == 0
    assert calls == []


# ── new: judge chunking, zero-backlog swipe, tiebreak, default max ─────────────
def test_judge_chunks_large_pool_and_isolates_chunk_failures(monkeypatch):
    """The judge scores in bounded chunks (fix for the weak model choking on a
    huge single call -> ~600 fail-closed 0.0s); a failed chunk zeros ONLY its own
    candidates, not the whole night."""
    monkeypatch.setenv("UA_PROACTIVE_DEMO_NUGGETS_JUDGE_CHUNK", "3")
    candidates = [
        {"task_id": f"t{i}", "video_title": f"vid {i}", "channel_name": "c", "summary": ""}
        for i in range(7)
    ]
    calls = []

    def call_llm(system, user):
        n = user.count("### Candidate index=")
        calls.append(n)
        if len(calls) == 2:  # fail the middle chunk to prove isolation
            raise RuntimeError("boom")
        return "\n".join(
            json.dumps({"index": j, "score": 5.0, "build": False, "reason": "ok"})
            for j in range(n)
        )

    verdicts = nuggets._judge_candidates(candidates, call_llm=call_llm)
    assert len(verdicts) == 7
    assert calls == [3, 3, 1]  # 7 candidates chunked 3+3+1
    assert verdicts[0]["score"] == 5.0 and verdicts[2]["score"] == 5.0  # chunk 1 scored
    assert verdicts[3]["score"] == 0.0 and "judge_error" in verdicts[3]["reason"]  # chunk 2 isolated
    assert verdicts[6]["score"] == 5.0  # chunk 3 scored despite chunk 2 failing


def test_zero_backlog_swipe_cancels_unbuilt_keeps_built_and_gpu_approved(conn):
    from universal_agent.services.proactive_tutorial_builds import (
        sweep_unbuilt_pending_builds,
    )

    _seed_pending_candidate(conn, "tb-keep", video_title="the built one")
    _seed_pending_candidate(conn, "tb-drop1", video_title="unbuilt A")
    _seed_pending_candidate(conn, "tb-drop2", video_title="unbuilt B")
    task_hub.upsert_item(
        conn,
        {
            "task_id": "tb-gpu", "source_kind": "tutorial_build", "title": "gpu approved",
            "status": task_hub.TASK_STATUS_OPEN, "agent_ready": False,
            "labels": ["pending-approval", "tutorial-build"],
            "metadata": {"gpu_approval": {"state": "approved"}},
        },
    )
    result = sweep_unbuilt_pending_builds(conn, keep_task_ids={"tb-keep"})
    assert result["swept"] == 2 and result["preserved_approved"] == 1

    def status(tid):
        return task_hub.get_item(conn, tid).get("status")

    assert status("tb-keep") == task_hub.TASK_STATUS_OPEN          # just-built row untouched
    assert status("tb-drop1") == task_hub.TASK_STATUS_CANCELLED    # un-built swept
    assert status("tb-drop2") == task_hub.TASK_STATUS_CANCELLED
    assert status("tb-gpu") == task_hub.TASK_STATUS_OPEN           # operator-approved preserved
    swept_labels = [str(x).lower() for x in task_hub.get_item(conn, "tb-drop1").get("labels") or []]
    assert "swept-eod" in swept_labels


def test_run_zero_backlog_swipe_noop_on_dry_run(conn):
    _seed_pending_candidate(conn, "tb-x", video_title="x")
    res = nuggets.run_zero_backlog_swipe(built_summary={"built": []}, dry_run=True, conn=conn)
    assert res.get("swept") == 0 and "skipped" in res
    assert task_hub.get_item(conn, "tb-x").get("status") == task_hub.TASK_STATUS_OPEN


def test_run_zero_backlog_swipe_sweeps_and_keeps_built(conn, monkeypatch):
    monkeypatch.delenv("UA_DISABLE_PROACTIVE_DEMO_SWIPE", raising=False)
    _seed_pending_candidate(conn, "tb-a", video_title="a")
    _seed_pending_candidate(conn, "tb-b", video_title="b")
    res = nuggets.run_zero_backlog_swipe(
        built_summary={"built": [{"task_id": "tb-a"}]}, dry_run=False, conn=conn
    )
    assert res["swept"] == 1 and res["kept"] == 1
    assert task_hub.get_item(conn, "tb-a").get("status") == task_hub.TASK_STATUS_OPEN
    assert task_hub.get_item(conn, "tb-b").get("status") == task_hub.TASK_STATUS_CANCELLED


def test_selection_tiebreak_is_deterministic(conn, monkeypatch):
    """Two build:true candidates with an identical score must resolve to a
    deterministic pick (by task_id), never a coin-flip."""
    monkeypatch.setenv("UA_PROACTIVE_DEMO_NUGGETS_MAX", "1")
    _seed_pending_candidate(conn, "tb-zzz", video_title="Z capability")
    _seed_pending_candidate(conn, "tb-aaa", video_title="A capability")
    result = nuggets.select_and_build_nuggets(
        dry_run=True, conn=conn,
        call_llm=_verdict_llm({0: (8.0, True, "tie"), 1: (8.0, True, "tie")}),
    )
    assert [s["task_id"] for s in result["selected"]] == ["tb-aaa"]  # lower task_id wins the tie


def test_nuggets_max_default_is_three(monkeypatch):
    from universal_agent import feature_flags

    monkeypatch.delenv("UA_PROACTIVE_DEMO_NUGGETS_MAX", raising=False)
    assert feature_flags.proactive_demo_nuggets_max() == 3
