"""Parsing guards for the nightly golden-nuggets health report.

The report's whole value is that it keeps surfacing the three things the
2026-08-01/02 forensics could not settle from a finished run — the orphan leak
(CGPM leftover_procs), memory throttling (CGPM memory.events high), and the
fidelity gate that has rejected every build since 2026-07-18. If the parsing
silently drifts, the report goes quiet and those regressions become invisible
again, which is worse than having no report at all.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "nuggets_health_report.py"
_spec = importlib.util.spec_from_file_location("nuggets_health_report", _SRC)
assert _spec and _spec.loader
nhr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nhr)


# A faithful slice of a real run, including the systemd[1] lifecycle lines that
# only appear with journal privileges (the bug live-testing caught).
JOURNAL_OK = """\
2026-08-02T04:50:52+0000 srv systemd[1]: Starting universal-agent-proactive-demo-nuggets.service - judge...
2026-08-02T04:51:15+0000 srv python[1]: nuggets: building Alpha -> alpha
2026-08-02T05:20:00+0000 srv python[1]: nuggets: building Beta -> beta
2026-08-02T05:55:00+0000 srv python[1]: nuggets: build FAILED rc=3 (nonzero_exit) for beta
2026-08-02T06:30:00+0000 srv python[1]: nuggets swipe: cancelled 9 un-built pending candidate(s)
2026-08-02T06:30:02+0000 srv nuggets_cgroup_postmortem.sh[9]: CGPM result=success exit=exited/0 cgroup_exists=yes
2026-08-02T06:30:02+0000 srv nuggets_cgroup_postmortem.sh[9]: CGPM memory.peak=2310000000
2026-08-02T06:30:02+0000 srv nuggets_cgroup_postmortem.sh[9]: CGPM memory.events high 0
2026-08-02T06:30:02+0000 srv nuggets_cgroup_postmortem.sh[9]: CGPM memory.events oom_kill 0
2026-08-02T06:30:02+0000 srv nuggets_cgroup_postmortem.sh[9]: CGPM leftover_procs=0 total_in_cgroup=3
2026-08-02T06:30:03+0000 srv systemd[1]: universal-agent-proactive-demo-nuggets.service: Deactivated successfully.
2026-08-02T06:30:03+0000 srv systemd[1]: universal-agent-proactive-demo-nuggets.service: Consumed 25min 3.1s CPU time.
"""

JOURNAL_LEAK = JOURNAL_OK.replace(
    "CGPM leftover_procs=0 total_in_cgroup=3", "CGPM leftover_procs=2 total_in_cgroup=5"
).replace(
    "CGPM memory.events high 0", "CGPM memory.events high 41"
) + (
    "2026-08-02T06:30:02+0000 srv nuggets_cgroup_postmortem.sh[9]: CGPM leftover pid=555 comm=claude\n"
)


def test_parses_run_shape():
    r = nhr._parse_run(JOURNAL_OK)
    assert r["started"] == "2026-08-02T04:50:52"
    assert r["result"] == "success"
    assert r["builds_attempted"] == 2
    assert r["builds_rc3"] == 1
    assert r["builds_timeout"] == 0
    assert r["builds_ok"] == 1
    assert r["swept"] == 9
    assert r["cgpm"]["leftover_procs"] == "0"
    assert r["cgpm"]["memory.events high"] == 0
    assert r["cgpm"]["memory.peak"] == "2310000000"


def test_detects_the_orphan_leak_and_throttling():
    """The two regressions #1587/#1588 were shipped to fix must be caught."""
    r = nhr._parse_run(JOURNAL_LEAK)
    assert r["cgpm"]["leftover_procs"] == "2"
    assert r["cgpm"]["memory.events high"] == 41
    assert "claude(555)" in r["leftover"]


def test_build_timeout_is_counted_separately_from_rc3():
    j = JOURNAL_OK.replace(
        "nuggets: build FAILED rc=3 (nonzero_exit) for beta",
        "nuggets: build TIMEOUT for beta (timeout_killed): timed out after 3600 seconds",
    )
    r = nhr._parse_run(j)
    assert r["builds_timeout"] == 1
    assert r["builds_rc3"] == 0


def test_a_run_that_never_fired_is_visible():
    r = nhr._parse_run("")
    assert r["started"] is None
    assert r["builds_attempted"] == 0


@pytest.mark.parametrize(
    "votes,expected",
    [("5/5", True), ("4/5", True), ("2/3", True), ("1/3", False), ("0/3", False), ("0/2", False), ("1/2", False)],
)
def test_strict_majority_matches_land_demo_semantics(votes, expected):
    """`land_demo.py` requires a strict majority — a tie does NOT pass."""
    import re

    m = re.match(r"(\d+)\s*/\s*(\d+)", votes)
    got, total = int(m.group(1)), int(m.group(2))
    assert (total > 0 and got * 2 > total) is expected


def _write_eval(root, name: str, status: str, mtime: float, votes: str = "3/3") -> None:
    """Lay down a demo workspace with an eval_report.json, like a real build does."""
    import os

    d = root / f"demo-{name}"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "eval_report.json"
    f.write_text(
        json.dumps(
            {
                "verdict": {"demo_id": name, "status": status, "votes": votes,
                            "gating_pass": 3 if status == "PASS" else 0, "gating_total": 3},
                "_vote": {"samples_requested": 3, "samples_ok": 3,
                          "passed": 3 if status == "PASS" else 0, "gate": status},
            }
        )
    )
    os.utime(f, (mtime, mtime))


def test_land_history_reads_eval_reports_not_build_stats(monkeypatch, tmp_path):
    """Regression guard: build_stats.jsonl is an INCOMPLETE ledger.

    Reading it produced a two-week false alarm (its newest pass was 2026-07-18)
    while the eval reports showed passes through 07-30. The metric must come from
    the per-demo eval_report.json.
    """
    import time

    monkeypatch.setattr(nhr, "DEMO_GLOB_ROOT", tmp_path)
    now = time.time()
    _write_eval(tmp_path, "proactive-old-fail", "FAIL", now - 20 * 86400)
    _write_eval(tmp_path, "proactive-recent-pass", "PASS", now - 3600)
    _write_eval(tmp_path, "proactive-newest-fail", "FAIL", now - 60)

    land = nhr._land_history()
    assert land["last_land"] is not None
    assert land["days_since"] == 0            # the pass an hour ago, not the 20-day-old row
    assert len(land["recent"]) == 3
    assert land["recent"][-1]["pass"] is False


def test_report_is_quiet_when_healthy_and_loud_when_not(monkeypatch, tmp_path):
    """Severity drives whether the operator is notified at all."""
    import time

    monkeypatch.setattr(nhr, "DEMO_GLOB_ROOT", tmp_path)
    monkeypatch.setattr(nhr, "_journal", lambda since: JOURNAL_OK)

    # A fresh land today + a clean run => info (no notification).
    _write_eval(tmp_path, "proactive-fresh", "PASS", time.time() - 600)
    assert nhr.build_report()["severity"] == "info"

    # Nothing landed for weeks — the 2026-07-31 onward state — must escalate.
    import shutil

    shutil.rmtree(tmp_path / "demo-proactive-fresh")
    _write_eval(tmp_path, "proactive-stale", "PASS", time.time() - 20 * 86400)
    _write_eval(tmp_path, "proactive-since-then", "FAIL", time.time() - 3600)
    rep = nhr.build_report()
    assert rep["severity"] in ("warning", "error")
    assert any("FIDELITY GATE" in p for p in rep["problems"])


def test_leftover_procs_parses_alongside_total_in_cgroup():
    """The post-mortem emits `leftover_procs=N total_in_cgroup=M` on one line.

    `total_in_cgroup` is the raw cgroup.procs count and INCLUDES the ExecStopPost
    script itself, so it is never 0 and must not be what the report gates on. Only
    `leftover_procs` (self-tree excluded) means something outlived the run.
    """
    r = nhr._parse_run(JOURNAL_OK)
    assert r["cgpm"]["leftover_procs"] == "0"      # must not swallow "total_in_cgroup"
    r2 = nhr._parse_run(JOURNAL_LEAK)
    assert r2["cgpm"]["leftover_procs"] == "2"


# ── zero-lands / failure-mode / scope / reclaim-counter regression guards ──────
# These four cover the actual 2026-08-02 blind spot: three nights in a row all
# builds timed out or errored, zero demos landed, and the report still said
# "healthy" -- because nothing keyed severity directly on build outcomes, the
# rc=3-only match missed other failure shapes, a stray manually-built demo-*
# workspace could fake a recent land, and a routine cgroup counter escalated to
# error every night for the wrong reason.

def test_zero_lands_is_an_error_even_with_a_recent_land(monkeypatch, tmp_path):
    """attempted>0 and ok==0 must be severity=error on its own.

    This is the actual bug: three consecutive all-timeout nights rendered
    healthy because the only other build-outcome check (builds_timeout) only
    ever downgraded to "warning" without a prior error, and the fidelity-gate
    staleness check can stay quiet for days after a real land. Zero lands
    tonight has to be loud regardless of what happened earlier in the week.
    """
    import time

    _write_eval(tmp_path, "proactive-yesterday", "PASS", time.time() - 3600)  # a fresh land

    # Make BOTH attempted builds fail this run (one timeout, one rc=3) so
    # builds_ok lands at exactly 0 even though a land happened recently.
    journal = JOURNAL_OK.replace(
        "2026-08-02T04:51:15+0000 srv python[1]: nuggets: building Alpha -> alpha\n",
        "2026-08-02T04:51:15+0000 srv python[1]: nuggets: building Alpha -> alpha\n"
        "2026-08-02T04:55:00+0000 srv python[1]: nuggets: build TIMEOUT for alpha "
        "(timeout_killed): timed out after 3600 seconds\n",
    )
    r = nhr._parse_run(journal)
    assert r["builds_attempted"] == 2
    assert r["builds_ok"] == 0

    monkeypatch.setattr(nhr, "DEMO_GLOB_ROOT", tmp_path)
    monkeypatch.setattr(nhr, "_journal", lambda since: journal)
    rep = nhr.build_report()
    assert rep["severity"] == "error"
    assert any("0 landed this run" in p for p in rep["problems"])


def test_rc_failures_and_subprocess_raise_both_count_and_appear_in_histogram():
    j = JOURNAL_OK.replace(
        "nuggets: build FAILED rc=3 (nonzero_exit) for beta",
        "nuggets: build FAILED rc=1 (nonzero_exit) for beta",
    ) + (
        "2026-08-02T06:40:00+0000 srv python[1]: nuggets: build subprocess raised for "
        "gamma (worker_signaled): RuntimeError('boom')\n"
    )
    r = nhr._parse_run(j)
    assert r["rc_histogram"] == {"1": 1}
    assert r["builds_rc3"] == 0            # rc=1 must not be miscounted as rc=3
    assert r["builds_raised"] == 1
    # attempted=2, one rc=1 failure + one raise -> both are failures even though
    # only 2 "building" lines fired; builds_ok reflects the survivors correctly.
    assert r["builds_ok"] == max(0, r["builds_attempted"] - 1 - 1 - r["builds_timeout"])


def test_memory_events_max_alone_does_not_escalate_to_error(monkeypatch, tmp_path):
    """cgroup v2's `memory.events max` increments on every reclaim at the
    MemoryMax ceiling -- expected steady state under a memory limit, not a
    fault. It must still be visible (reported), just never gate severity.
    """
    j = JOURNAL_OK.replace(
        "CGPM memory.events oom_kill 0",
        "CGPM memory.events oom_kill 0\n"
        "2026-08-02T06:30:02+0000 srv nuggets_cgroup_postmortem.sh[9]: CGPM memory.events max 2706",
    )
    r = nhr._parse_run(j)
    assert r["cgpm"]["memory.events max"] == 2706

    monkeypatch.setattr(nhr, "DEMO_GLOB_ROOT", tmp_path)
    monkeypatch.setattr(nhr, "_journal", lambda since: j)
    rep = nhr.build_report()
    assert rep["severity"] != "error"
    assert any("memory.events max=2706" in p for p in rep["problems"])  # still reported


def test_land_history_ignores_non_proactive_demo_dirs(monkeypatch, tmp_path):
    """A manually-built `demo-<slug>` workspace must not fake a recent land for
    this nightly-job's history -- only `demo-proactive-*` counts.
    """
    import time

    monkeypatch.setattr(nhr, "DEMO_GLOB_ROOT", tmp_path)
    _write_eval(tmp_path, "proactive-real-one", "FAIL", time.time() - 20 * 86400)
    # A manual build, NOT produced by this cron job: dir name has no
    # "proactive-" prefix after "demo-".
    manual = tmp_path / "demo-manually-built-thing"
    manual.mkdir()
    (manual / "eval_report.json").write_text(
        json.dumps(
            {
                "verdict": {"demo_id": "manually-built-thing", "status": "PASS", "votes": "3/3",
                            "gating_pass": 3, "gating_total": 3},
                "_vote": {"samples_requested": 3, "samples_ok": 3, "passed": 3, "gate": "PASS"},
            }
        )
    )
    import os

    now = time.time()
    os.utime(manual / "eval_report.json", (now - 60, now - 60))

    land = nhr._land_history()
    assert land["last_land"] is None          # the manual land must not count
    assert land["days_since"] is None
    assert all("manually-built-thing" not in r["demo"] for r in land["recent"])
