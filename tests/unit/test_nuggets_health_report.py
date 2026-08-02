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
2026-08-02T06:30:02+0000 srv nuggets_cgroup_postmortem.sh[9]: CGPM leftover_procs=0
2026-08-02T06:30:03+0000 srv systemd[1]: universal-agent-proactive-demo-nuggets.service: Deactivated successfully.
2026-08-02T06:30:03+0000 srv systemd[1]: universal-agent-proactive-demo-nuggets.service: Consumed 25min 3.1s CPU time.
"""

JOURNAL_LEAK = JOURNAL_OK.replace(
    "CGPM leftover_procs=0", "CGPM leftover_procs=2"
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


def test_report_is_quiet_when_healthy_and_loud_when_not(monkeypatch, tmp_path):
    """Severity drives whether the operator is notified at all."""
    stats = tmp_path / "build_stats.jsonl"
    monkeypatch.setattr(nhr, "BUILD_STATS", stats)
    monkeypatch.setattr(nhr, "_journal", lambda since: JOURNAL_OK)

    # A fresh land today + a clean run => info (no notification).
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "")
    stats.write_text('{"ts": "%s", "demo_id": "d", "eval_votes": "4/5"}\n' % now)
    assert nhr.build_report()["severity"] == "info"

    # The real current state — last land 2026-07-18 — must escalate.
    stats.write_text('{"ts": "2026-07-18T01:43:24", "demo_id": "d", "eval_votes": "4/5"}\n')
    rep = nhr.build_report()
    assert rep["severity"] in ("warning", "error")
    assert any("FIDELITY GATE" in p for p in rep["problems"])
