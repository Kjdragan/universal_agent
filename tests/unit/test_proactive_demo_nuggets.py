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
    # ...and is terminal-COMPLETED: un-demoable is a verdict from a build that
    # SUCCEEDED, so the pipeline completed. The verdict lives in nugget_build.state.
    assert task_hub.get_item(conn, "tb-u")["status"] == task_hub.TASK_STATUS_COMPLETED


# ── already-built candidates are excluded from re-judging ─────────────────────
def test_landed_demo_dir_excludes_candidate(conn, tmp_path):
    """A LANDED demo (manifest.json present) is not rebuilt.

    This test previously created a bare directory with no manifest and asserted
    exclusion — which codified the 2026-08-02 defect rather than the intent. A
    failed build leaves exactly such a bare directory behind, so that assertion
    made "a build crashed" indistinguishable from "a demo landed". The intent is
    and always was "don't rebuild what already built"; the manifest is what makes
    that true. See _demo_dir_exists.
    """
    from universal_agent.services.tutorial_demo_finalize import proactive_demo_slug

    title = "Build a RAG agent with the ADK"
    slug = proactive_demo_slug(title)
    landed = tmp_path / "lrepos" / f"demo-proactive-{slug}"
    landed.mkdir(parents=True)
    (landed / "manifest.json").write_text('{"status": "passed"}', encoding="utf-8")
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


def test_failed_build_debris_does_not_exclude_candidate(conn, tmp_path):
    """The other half: a leftover dir with NO manifest is failed-build debris.

    The candidate must be considered again, and the debris must be quarantined
    out of the way first — build_demo.py hard-fails provision_demo on
    "workspace already exists", so eligibility without quarantine would just
    burn a build slot.
    """
    from universal_agent.services.tutorial_demo_finalize import proactive_demo_slug

    title = "Build a RAG agent with the ADK"
    slug = proactive_demo_slug(title)
    root = tmp_path / "lrepos"
    debris = root / f"demo-proactive-{slug}" / ".build"
    debris.mkdir(parents=True)
    (debris / "build_transcript.jsonl").write_text("evidence\n", encoding="utf-8")
    _seed_pending_candidate(conn, "tb-a", video_title=title)
    calls: list[list[str]] = []
    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "great")}),
        build_runner=_make_build_runner(record=calls),
        notifier=_noop_notifier,
    )
    assert result["candidates_considered"] == 1
    assert len(calls) == 1  # it actually rebuilt
    # The debris was moved aside (preserved, not deleted) so provision_demo can run.
    quarantined = list(root.glob(f"failed-proactive-{slug}.*"))
    assert len(quarantined) == 1
    assert (quarantined[0] / ".build" / "build_transcript.jsonl").read_text() == "evidence\n"


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


# ── build-subprocess process-group containment ────────────────────────────────
# Regression guard for the defect that produced BOTH nightly failures:
# a bare ``subprocess.run(argv, timeout=N)`` kills only the direct child, so the
# ``claude`` CLI grandchildren are ORPHANED. Orphans re-parent to PID 1 but stay
# in the systemd unit's cgroup, keep counting against MemoryMax, and outlive the
# main process (2026-07-27 oom-kill; 2026-08-01 result=timeout).
def _spawn_grandchild_script(marker_seconds: str) -> list[str]:
    """argv for: sh -> python -> `sleep <marker>` (the orphan-prone grandchild)."""
    inner = (
        "import subprocess,time;"
        f"subprocess.Popen(['sleep','{marker_seconds}']);"
        f"time.sleep({marker_seconds})"
    )
    return ["sh", "-c", f'python3 -c "{inner}"']


def _count_marker_procs(marker_seconds: str) -> int:
    out = subprocess.run(
        ["pgrep", "-x", "-f", f"sleep {marker_seconds}"],
        capture_output=True,
        text=True,
    ).stdout.split()
    return len(out)


@pytest.mark.timeout(60)
def test_build_runner_timeout_reaps_the_whole_process_group(monkeypatch):
    """On timeout the build's grandchildren must NOT survive.

    Without ``start_new_session=True`` + ``os.killpg`` this test fails with one
    surviving ``sleep`` — exactly the orphaned ``claude`` tree seen in production.
    """
    import os
    import signal
    import time

    marker = "2913"  # unique duration doubles as the pgrep marker
    # NB: _build_timeout_seconds() floors env overrides at 60s, so stub it directly.
    monkeypatch.setattr(nuggets, "_build_timeout_seconds", lambda: 3)

    def _cleanup():
        subprocess.run(["pkill", "-9", "-x", "-f", f"sleep {marker}"], capture_output=True)
        time.sleep(0.3)

    _cleanup()
    assert _count_marker_procs(marker) == 0, "dirty precondition"
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            nuggets._default_build_runner(_spawn_grandchild_script(marker))
        time.sleep(1.0)
        survivors = _count_marker_procs(marker)
        assert survivors == 0, (
            f"{survivors} orphaned grandchild process(es) survived the build timeout; "
            "the build subprocess must run in its own process group and be killed with killpg"
        )
    finally:
        _cleanup()

    # The runner must also put the build in its OWN process group, so that
    # killpg targets the build tree and never the cron's own process group.
    proc = subprocess.Popen(["sleep", "5"], start_new_session=True)
    try:
        assert os.getpgid(proc.pid) != os.getpgid(os.getpid())
    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()


@pytest.mark.timeout(60)
def test_build_runner_returns_completedprocess_on_success():
    """The Popen rewrite must preserve the CompletedProcess contract callers use."""
    proc = nuggets._default_build_runner(["sh", "-c", "printf out; printf err 1>&2; exit 7"])
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.returncode == 7
    assert proc.stdout == "out"
    assert proc.stderr == "err"


# ── a failed build must surface its STDOUT, not just stderr ──────────────────
def test_build_failure_records_stdout_tail(conn):
    """build_demo.py writes its stage tokens to stdout, so a failure that logs
    only stderr throws away the one line that says WHY it degraded.

    Regression for 2026-08-02: a build failed rc=3 in 6m with an empty
    transcript. The cause -- a single flaked live-auth probe silently dry-ran
    the whole build, emitting ``GOAL_BUILD: DRYRUN ... auth=<label>`` on stdout
    -- was invisible in the journal and only recoverable by hand-inspecting the
    workspace.
    """
    _seed_pending_candidate(conn, "tb-a", video_title="Build a RAG agent with the ADK")

    def _failing_runner(argv):
        return subprocess.CompletedProcess(
            argv, 3,
            stdout="GOAL_BUILD: DRYRUN reason=auth_probe_failed auth=oauth-env\n",
            stderr="non-zero exit\n",
        )

    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "specific + novel")}),
        build_runner=_failing_runner,
        notifier=_noop_notifier,
    )

    assert result["built"] == []
    assert len(result["build_failures"]) == 1
    failure = result["build_failures"][0]
    assert failure["returncode"] == 3
    # stderr is still carried (unchanged contract) ...
    assert "non-zero exit" in failure["error"]
    # ... and the stdout diagnosis is no longer discarded.
    assert "GOAL_BUILD: DRYRUN" in failure["stdout_tail"]
    assert "auth=oauth-env" in failure["stdout_tail"]


def test_tail_helper_truncates_and_tolerates_none():
    assert nuggets._tail(None) == ""
    assert nuggets._tail("") == ""
    assert nuggets._tail("abcdef", limit=3) == "def"
    # The tail keeps the END of the stream — stage tokens are emitted late.
    assert nuggets._tail("x" * 900 + "TOKEN").endswith("TOKEN")
    assert len(nuggets._tail("x" * 2000)) == 800


# ── failed-build debris must not permanently disqualify a candidate ──────────
def test_demo_dir_without_manifest_does_not_count_as_built(tmp_path):
    """A dir with no manifest.json is FAILED-build debris, not a landed demo.

    Regression for 2026-08-02: seven candidates going back to 07-20 were stuck
    invisible this way, including the three highest-scored of that day's run
    (8.4 / 8.0 / 7.8). They never landed, never errored, and never came back
    through the deterministic-task_id re-ingest path.
    """
    root = tmp_path
    # Landed: manifest present -> already built, stays skipped.
    landed = root / "demo-proactive-landed"
    landed.mkdir()
    (landed / "manifest.json").write_text("{}", encoding="utf-8")
    assert nuggets._demo_dir_exists(root, "landed") is True

    # Debris: a build transcript but no manifest -> eligible again.
    debris = root / "demo-proactive-debris"
    (debris / ".build").mkdir(parents=True)
    (debris / ".build" / "build_transcript.jsonl").write_text("{}\n", encoding="utf-8")
    assert nuggets._demo_dir_exists(root, "debris") is False

    # Un-demoable is a real verdict from a SUCCESSFUL build -> stays skipped.
    (root / "demo-undemoable-judged").mkdir()
    assert nuggets._demo_dir_exists(root, "judged") is True

    # Nothing on disk at all -> eligible.
    assert nuggets._demo_dir_exists(root, "never-seen") is False


def test_quarantine_moves_debris_aside_and_preserves_the_transcript(tmp_path):
    """build_demo.py fails provision_demo on 'workspace already exists', so the
    debris must be moved before a rebuild — and never deleted, because the build
    transcript is the forensic record of the previous failure."""
    root = tmp_path
    debris = root / "demo-proactive-stuck"
    (debris / ".build").mkdir(parents=True)
    (debris / ".build" / "build_transcript.jsonl").write_text("evidence\n", encoding="utf-8")

    dest = nuggets._quarantine_failed_demo_dir(root, "stuck")

    assert dest is not None
    assert not debris.exists()                      # path is clear for the rebuild
    assert dest.is_dir()                            # debris preserved, not deleted
    assert (dest / ".build" / "build_transcript.jsonl").read_text() == "evidence\n"
    # Must not match the demo-proactive-* glob that feeds _land_history().
    assert not dest.name.startswith("demo-proactive-")
    assert list(root.glob("demo-proactive-*/eval_report.json")) == []


# ── the funnel must be able to SEE its own output ────────────────────────────
def test_built_row_transitions_to_completed_and_survives_the_swipe(conn, monkeypatch):
    """A row that produced a demo must land on the POSITIVE terminal status.

    Regression for 2026-08-02: the lane wired only the NEGATIVE terminal
    transition. A built row stayed ``open``, was spared for exactly one night by
    the swipe's keep-set, and was ``cancelled`` by the next night's swipe —
    byte-identical to a candidate the judge rejected. Production read
    "774 cancelled / 0 completed" while four demos had demonstrably shipped
    Jul 29 - Aug 1; a Jul 8 backup still had completed=127.
    """
    monkeypatch.delenv("UA_DISABLE_PROACTIVE_DEMO_SWIPE", raising=False)
    _seed_pending_candidate(conn, "tb-a", video_title="Build a RAG agent with the ADK")

    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "specific + novel")}),
        build_runner=_make_build_runner(),
        notifier=_noop_notifier,
    )
    assert len(result["built"]) == 1

    item = task_hub.get_item(conn, "tb-a")
    assert item["status"] == task_hub.TASK_STATUS_COMPLETED
    marked = item["metadata"]["nugget_build"]
    assert marked["state"] == "built"
    # The demo's identity travels with the terminal row (slug + id + path).
    assert marked["demo_id"] == f"proactive-{marked['video_slug']}"
    assert marked["workspace_dir"].endswith(f"demo-proactive-{marked['video_slug']}")

    # Terminal => invisible to the swipe's ``status = open`` filter, so the row
    # can never be re-swept into ``cancelled`` on a later night.
    swipe = nuggets.run_zero_backlog_swipe(built_summary=result, dry_run=False, conn=conn)
    assert swipe["swept"] == 0
    assert task_hub.get_item(conn, "tb-a")["status"] == task_hub.TASK_STATUS_COMPLETED


# ── a FAILED attempt is not a rejection — it must survive the night ──────────
def test_failed_build_attempt_is_kept_by_the_swipe(conn, monkeypatch):
    """The keep-set is ``built ∪ attempted_failed``, not ``built`` alone.

    On a total-failure night the judge's very best candidates are the ONLY rows
    that were tried, and every one of them failed — so a built-only keep-set
    guarantees they are swept. Rows the judge never selected must still sweep.
    """
    monkeypatch.delenv("UA_DISABLE_PROACTIVE_DEMO_SWIPE", raising=False)
    _seed_pending_candidate(conn, "tb-fail", video_title="Build a RAG agent with the ADK")

    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "specific + novel")}),
        build_runner=_make_build_runner(status="fail"),
        notifier=_noop_notifier,
    )
    assert result["built"] == []
    assert result["attempted_failed"] == ["tb-fail"]
    assert result["build_failures"][0]["task_id"] == "tb-fail"

    # A never-attempted pending row seeded after the build must still be swept.
    _seed_pending_candidate(conn, "tb-reject", video_title="Vague hype about AI")
    swipe = nuggets.run_zero_backlog_swipe(built_summary=result, dry_run=False, conn=conn)

    assert swipe["swept"] == 1
    assert task_hub.get_item(conn, "tb-fail")["status"] == task_hub.TASK_STATUS_OPEN
    assert task_hub.get_item(conn, "tb-reject")["status"] == task_hub.TASK_STATUS_CANCELLED


@pytest.mark.parametrize("mode", ["timeout", "crash", "nonzero"])
def test_every_build_failure_path_records_attempted_failed(conn, mode):
    """All three failure exits (timeout kill / subprocess crash / nonzero rc)
    must feed the keep-set — a candidate lost on any one of them is unretryable
    once its source video ages out of the intel window."""
    _seed_pending_candidate(conn, "tb-x", video_title="Build a RAG agent with the ADK")

    def _runner(argv):
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, 1, output="stdout tail", stderr="")
        if mode == "crash":
            raise RuntimeError("exec failed")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "specific + novel")}),
        build_runner=_runner,
        notifier=_noop_notifier,
    )
    assert result["built"] == []
    assert result["attempted_failed"] == ["tb-x"]


def test_swipe_still_runs_when_nothing_was_built_or_attempted(conn, monkeypatch):
    """A night where NOTHING clears the judge's 7.0 bar legitimately builds
    nothing. The swipe must NOT be gated on a non-empty build set — that pool
    still has to return to ~zero."""
    monkeypatch.delenv("UA_DISABLE_PROACTIVE_DEMO_SWIPE", raising=False)
    _seed_pending_candidate(conn, "tb-a", video_title="a")
    _seed_pending_candidate(conn, "tb-b", video_title="b")

    res = nuggets.run_zero_backlog_swipe(
        built_summary={"built": [], "attempted_failed": []}, dry_run=False, conn=conn
    )
    assert res["swept"] == 2 and res["kept"] == 0
    assert task_hub.get_item(conn, "tb-a")["status"] == task_hub.TASK_STATUS_CANCELLED
    assert task_hub.get_item(conn, "tb-b")["status"] == task_hub.TASK_STATUS_CANCELLED


def test_quarantine_never_touches_a_landed_demo(tmp_path):
    """A landed demo has a manifest; quarantining it would destroy a real build."""
    root = tmp_path
    landed = root / "demo-proactive-landed"
    landed.mkdir()
    (landed / "manifest.json").write_text("{}", encoding="utf-8")

    assert nuggets._quarantine_failed_demo_dir(root, "landed") is None
    assert landed.is_dir()
    # Nothing on disk at all is also a no-op.
    assert nuggets._quarantine_failed_demo_dir(root, "absent") is None


# ── a build that LANDED and then blew the cap is a success, not a failure ────
def test_timeout_after_landing_registers_the_demo(conn, tmp_path):
    """build_demo.py keeps working after the demo lands -- promote, then Stage D,
    which carries its own 900s budget that nothing reconciles against the outer
    3600s cap. A build that lands with under 15 minutes left is structurally
    guaranteed to be killed however well it went.

    Regression for 2026-08-03: google-okf-hermes landed at 06:34:50 with verify
    PASS, DEMO_EVAL PASS gating=8/8 votes=3/3 and manifest status=demoed, then was
    killed at 06:47:51 mid-post-land. It was recorded as a build failure and never
    registered, and builds_ok reported 1 on a night that landed 2.
    """
    from universal_agent.services.tutorial_demo_finalize import proactive_demo_slug

    title = "Build a RAG agent with the ADK"
    slug = proactive_demo_slug(title)
    root = tmp_path / "lrepos"

    def _lands_then_times_out(argv):
        # The build does its work -- the landed demo is on disk ...
        demo_dir = root / f"demo-proactive-{slug}"
        demo_dir.mkdir(parents=True, exist_ok=True)
        (demo_dir / "manifest.json").write_text(
            json.dumps({"demo_id": f"proactive-{slug}", "status": "demoed",
                        "acceptance_passed": True}), encoding="utf-8")
        (demo_dir / "README.md").write_text("## Run\nuv run python main.py\n", encoding="utf-8")
        (demo_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        # ... and THEN the outer cap fires during post-land promote / Stage D.
        raise subprocess.TimeoutExpired(
            argv, 3600, output="[ok   ] land_demo: PASS\n", stderr="")

    _seed_pending_candidate(conn, "tb-a", video_title=title)
    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "great")}),
        build_runner=_lands_then_times_out,
        notifier=_noop_notifier,
    )

    # The demo is real and gated -- it counts as built, not failed.
    assert len(result["built"]) == 1
    assert result["build_failures"] == []
    assert result["attempted_failed"] == []
    # ... but the truncated post-land work stays visible.
    assert len(result["landed_after_timeout"]) == 1
    assert result["landed_after_timeout"][0]["demo_id"] == f"proactive-{slug}"


def test_timeout_without_landing_is_still_a_failure(conn, tmp_path):
    """The inverse must not regress: a cap kill with nothing on disk is a real
    failure and must stay in build_failures + attempted_failed (so the swipe
    preserves it for retry)."""
    title = "Build a RAG agent with the ADK"

    def _times_out_with_nothing(argv):
        raise subprocess.TimeoutExpired(argv, 3600, output="", stderr="")

    _seed_pending_candidate(conn, "tb-a", video_title=title)
    result = nuggets.select_and_build_nuggets(
        dry_run=False,
        conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "great")}),
        build_runner=_times_out_with_nothing,
        notifier=_noop_notifier,
    )

    assert result["built"] == []
    assert result["landed_after_timeout"] == []
    assert len(result["build_failures"]) == 1
    assert result["build_failures"][0]["worker_exit"]
    # Preserved for retry by the swipe -- exactly one task id, the one we seeded.
    assert result["attempted_failed"] == [result["build_failures"][0]["task_id"]]


# ── the build's wall budget must reach build_demo, and stay in lockstep ──────
@pytest.mark.timeout(60)
def test_build_runner_passes_wall_budget_matching_the_cap(monkeypatch):
    """demo_factory budgets its post-land stages (promote, Stage D) against
    DEMO_FACTORY_WALL_BUDGET_S. If that value drifts from the cap this runner
    actually enforces, Stage D can again be entitled to more time than exists --
    the 2026-08-03 kill (landed at 2819s of 3600s, Stage D wanted 900s, 781s left).

    Derived from _build_timeout_seconds() rather than pinned in the unit file, so
    tuning UA_PROACTIVE_DEMO_NUGGETS_BUILD_TIMEOUT_SECONDS moves both together.
    """
    monkeypatch.setenv("UA_PROACTIVE_DEMO_NUGGETS_BUILD_TIMEOUT_SECONDS", "1234")
    proc = nuggets._default_build_runner(
        ["sh", "-c", 'printf "%s" "$DEMO_FACTORY_WALL_BUDGET_S"']
    )
    assert proc.returncode == 0
    assert proc.stdout == "1234"
    assert proc.stdout == str(nuggets._build_timeout_seconds())


@pytest.mark.timeout(60)
def test_build_runner_still_inherits_the_rest_of_the_environment(monkeypatch):
    """Passing env= must not scrub the environment build_demo needs (PATH, the
    Anthropic/ZAI credentials the judge and land use). first-party child -- full
    inherit is correct here, plus the one added var."""
    monkeypatch.setenv("UA_SENTINEL_FOR_TEST", "kept")
    proc = nuggets._default_build_runner(
        ["sh", "-c", 'printf "%s|%s" "$UA_SENTINEL_FOR_TEST" "${PATH:+haspath}"']
    )
    assert proc.stdout == "kept|haspath"


# ── failure classification: harness-fault retry + goal-DEFER requeue ──────────
def test_harness_fault_retries_once_via_into_and_registers(conn):
    """A judge HARNESS_FAULT (land exit 4) records nothing — but the finished
    build is on disk, so the cron resumes it ONCE via --into the same night
    instead of throwing the whole build away (demo_factory #256 companion)."""
    _seed_pending_candidate(conn, "t-hf", video_title="Harness Fault Video")
    calls = []

    def _runner(argv):
        calls.append(list(argv))
        slug = argv[argv.index("--slug") + 1].removeprefix("proactive-")
        root = Path(argv[argv.index("--workspace-root") + 1])
        demo_dir = root / f"demo-proactive-{slug}"
        demo_dir.mkdir(parents=True, exist_ok=True)
        if len(calls) == 1:
            # Built fine, judge machinery broke: NO manifest was recorded.
            return subprocess.CompletedProcess(
                argv, 3,
                stdout=("  warn  land_demo        HARNESS FAULT/exit=4 "
                        "(not the demo's fault; nothing recorded): x\n"
                        "BUILD_DEMO: FAIL demo_id=d exit=3\n"),
                stderr="",
            )
        (demo_dir / "manifest.json").write_text(
            json.dumps({"demo_id": f"proactive-{slug}", "status": "passed"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="land_demo: PASS", stderr="")

    summary = nuggets.select_and_build_nuggets(
        dry_run=False, conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "specific + novel")}),
        build_runner=_runner, notifier=_noop_notifier,
    )
    assert len(calls) == 2
    into_i = calls[1].index("--into")
    assert calls[1][into_i + 1].endswith("demo-proactive-harness-fault-video")
    assert [b["task_id"] for b in summary["built"]] == ["t-hf"]
    assert summary["build_failures"] == []
    assert summary["attempted_failed"] == []


def test_goal_defer_requeues_seed_once_then_terminal(conn):
    """A goal_build DEFER is a budget artifact, not a fidelity verdict: night 1
    sets the manifest-bearing workspace aside and stamps the task for ONE more
    night; a second DEFER is at the cap and files terminally (workspace kept)."""
    from universal_agent.services.tutorial_demo_finalize import proactive_demo_slug

    _seed_pending_candidate(conn, "t-defer", video_title="Kitchen Sink Workflow")
    slug = proactive_demo_slug("Kitchen Sink Workflow")
    root = nuggets._workspace_root()

    def _defer_runner(argv):
        r = Path(argv[argv.index("--workspace-root") + 1])
        demo_dir = r / f"demo-proactive-{slug}"
        demo_dir.mkdir(parents=True, exist_ok=True)
        # land recorded un-demoable beneath the DEFER — the manifest that would
        # otherwise delist the candidate forever.
        (demo_dir / "manifest.json").write_text(
            json.dumps({"status": "un-demoable"}), encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            argv, 3,
            stdout=("  warn  goal_build       DEFER model=claude-opus-5\n"
                    "  warn  land_demo        un-demoable/exit=1: x\n"
                    "BUILD_DEMO: FAIL exit=3\n"),
            stderr="",
        )

    llm = _verdict_llm({0: (9.0, True, "focused enough")})
    s1 = nuggets.select_and_build_nuggets(
        dry_run=False, conn=conn, call_llm=llm,
        build_runner=_defer_runner, notifier=_noop_notifier,
    )
    assert s1["defer_requeued"] == ["t-defer"]
    assert s1["attempted_failed"] == ["t-defer"]
    assert s1["build_failures"][0]["failure_kind"] == "goal_defer"
    assert s1["build_failures"][0]["requeued"] is True
    assert not (root / f"demo-proactive-{slug}").exists()
    assert len(list(root.glob(f"failed-proactive-{slug}.defer1.*"))) == 1
    meta = task_hub.get_item(conn, "t-defer")["metadata"]
    assert meta["nugget_defer"]["count"] == 1

    # Night 2: the seed is re-gathered (workspace moved aside, no nugget_build,
    # still open) and DEFERs again — at the cap, so it files terminally and the
    # manifest-bearing workspace stays to delist it from future nights.
    s2 = nuggets.select_and_build_nuggets(
        dry_run=False, conn=conn, call_llm=llm,
        build_runner=_defer_runner, notifier=_noop_notifier,
    )
    assert s2["defer_requeued"] == []
    assert s2["attempted_failed"] == ["t-defer"]
    assert (root / f"demo-proactive-{slug}").is_dir()


def test_plain_failure_is_not_retried_or_requeued(conn):
    """rc!=0 with neither marker keeps the exact pre-classification behavior."""
    _seed_pending_candidate(conn, "t-plain", video_title="Plain Failure Video")
    calls = []

    def _runner(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    summary = nuggets.select_and_build_nuggets(
        dry_run=False, conn=conn,
        call_llm=_verdict_llm({0: (9.0, True, "specific + novel")}),
        build_runner=_runner, notifier=_noop_notifier,
    )
    assert len(calls) == 1
    assert summary["defer_requeued"] == []
    assert summary["build_failures"][0]["failure_kind"] == "other"
    assert summary["attempted_failed"] == ["t-plain"]


def test_judge_prompt_screens_kitchen_sink_scope():
    """The demo-ability criterion must stay in the judge prompt — a sprawling
    multi-service tutorial burned a full nightly build on 2026-08-07."""
    prompt = nuggets._JUDGE_SYSTEM_PROMPT
    assert "kitchen-sink" in prompt
    assert "ONE small, self-contained demo" in prompt
