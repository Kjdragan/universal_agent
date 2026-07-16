"""Tests for the disk_usage_health invariant.

Background: Simone's 2026-05-20 morning digest flagged disk at 70% with a
"climbing" trend and recommended an invariant for proactive detection. P5
adds one. Same lightweight pattern as the P4 zai_inference_health probe:
no DB, no AI inference, single fast syscall + small pure-logic block.

Thresholds (operator-strict per pattern set in P4):
- WARN: any monitored mount above 75%
- CRITICAL: any monitored mount above 90%
- Severity is the worst-of across monitored mounts
"""

from __future__ import annotations

from collections import namedtuple
import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_registry_for_tests()
    from universal_agent.services.invariants import disk_usage_health
    importlib.reload(disk_usage_health)
    yield
    clear_registry_for_tests()


DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


def _gb(n: float) -> int:
    return int(n * 1024 * 1024 * 1024)


def _mock_disk_usage(mounts: dict[str, DiskUsage]):
    """Patch shutil.disk_usage to return controlled values per path."""
    def _impl(path):
        path_str = str(path)
        # Walk up looking for a known mount
        for mount, usage in mounts.items():
            if path_str.startswith(mount):
                return usage
        return mounts.get("/", DiskUsage(_gb(100), _gb(10), _gb(90)))
    return patch(
        "universal_agent.services.invariants.disk_usage_health.shutil.disk_usage",
        side_effect=_impl,
    )


def test_registers_on_import():
    ids = {inv.id for inv in pi.get_registered_invariants()}
    assert "disk_usage_health" in ids


def test_all_mounts_healthy_emits_nothing():
    """Every mount well below the warn threshold (75%) → no finding."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(200), _gb(40), _gb(160)),       # 20%
        "/opt": DiskUsage(_gb(100), _gb(30), _gb(70)),     # 30%
        "/var/lib": DiskUsage(_gb(100), _gb(50), _gb(50)), # 50%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert matches == []


def test_seventy_five_percent_does_not_fire():
    """At the exact threshold (75%) we don't fire — the gate is strictly above."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(75), _gb(25)),  # exactly 75%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert matches == []


def test_above_seventy_five_percent_fires_warn():
    """Disk above 75% → warn. Simone's 70%-and-climbing case crossing the gate."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(80), _gb(20)),  # 80%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    obs = matches[0].observed_value or {}
    pressured = obs.get("pressured_mounts") or []
    assert any(m.get("mount") == "/" and m.get("used_pct") >= 75 for m in pressured)


def test_above_ninety_percent_fires_critical():
    """Disk above 90% is catastrophic-class — critical severity."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(92), _gb(8)),  # 92%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"


def test_worst_mount_drives_severity():
    """If /opt is fine but / is critical, finding is critical. Worst-of."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(95), _gb(5)),     # 95% critical
        "/opt": DiskUsage(_gb(100), _gb(30), _gb(70)), # 30% healthy
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"


def test_multiple_pressured_mounts_listed_in_one_finding():
    """Both root and /opt above 75% → ONE finding listing both."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(82), _gb(18)),    # 82%
        "/opt": DiskUsage(_gb(100), _gb(78), _gb(22)), # 78%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    pressured = {m["mount"] for m in obs.get("pressured_mounts") or []}
    # At least the two pressured mounts present (others may be there if monitored too)
    assert "/" in pressured
    assert "/opt" in pressured


def test_disk_usage_oserror_silent():
    """If a mount path doesn't exist on the host (dev box without /var/lib),
    skip that mount gracefully — don't crash the watchdog."""
    def _raise(path):
        raise FileNotFoundError(path)
    with patch(
        "universal_agent.services.invariants.disk_usage_health.shutil.disk_usage",
        side_effect=_raise,
    ):
        findings = run_invariants({})
    # Probe handled the error; either no finding, or a finding with empty
    # pressured_mounts. Critically: no probe_error finding was emitted.
    probe_errors = [f for f in findings if "probe_error" in (f.metric_key or "")]
    assert probe_errors == []


def test_finding_includes_runbook_command():
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(85), _gb(15)),
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    runbook = matches[0].runbook_command or ""
    # Useful runbook mentions df + AGENT_RUN_WORKSPACES (most likely cleanup target)
    assert "df" in runbook or "AGENT_RUN_WORKSPACES" in runbook


def test_observed_value_includes_gb_used_and_free():
    """Operator should be able to size the problem from the finding alone."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(200), _gb(160), _gb(40)),  # 80%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    pressured = obs.get("pressured_mounts") or []
    root = next((m for m in pressured if m["mount"] == "/"), None)
    assert root is not None
    assert root.get("used_gb") and root.get("free_gb") and root.get("total_gb")


def test_message_does_not_contains_stale_not_the_driver_literal():
    """2026-06-25 regression guard.

    The previous message + design_note claimed AGENT_RUN_WORKSPACES is
    "NOT the driver (~0.3G reapable)" and pointed at the uv cache as the
    sole driver. Under sustained VP-coder load that framing was stale
    (per-mission .venv bloat reached 30G / 19.6G regenerable on
    2026-06-25) and actively misled cycles into dismissing real bloat.

    Asserts:
      1. The probe's returned ``message`` field has no ``"NOT the driver"``.
      2. The ``design_note`` metadata has no ``"NOT the driver"``.
      3. The source file itself has no ``"NOT the driver"`` literal anywhere
         (defense against re-introduction in a docstring or comment).
    """
    # (1) Invoke the probe directly so we read the exact ``message`` string
    # returned (it is later surfaced as ``recommendation`` on HeartbeatFinding).
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(85), _gb(15)),  # 85% warn
    }):
        from universal_agent.services.invariants import disk_usage_health
        importlib.reload(disk_usage_health)
        result = disk_usage_health.disk_usage_health({})
    assert result is not None, "probe should fire at 85%"
    assert "NOT the driver" not in (result.get("message") or ""), (
        "probe 'message' must not contain the misleading 'NOT the driver' literal"
    )

    # (2) design_note inside the @invariant metadata.
    registered = [
        inv for inv in pi.get_registered_invariants()
        if inv.id == "disk_usage_health"
    ]
    assert registered, "disk_usage_health must be registered on import"
    metadata = registered[0].metadata or {}
    design_note = metadata.get("design_note") or ""
    assert "NOT the driver" not in design_note, (
        "design_note must not claim AGENT_RUN_WORKSPACES is 'NOT the driver'"
    )

    # (3) Source-file grep — strongest guarantee against re-introduction.
    import inspect
    src = inspect.getsource(disk_usage_health)
    assert "NOT the driver" not in src, (
        "no occurrence of 'NOT the driver' allowed anywhere in the module"
    )


def test_top_consumers_names_real_subdirs(tmp_path, monkeypatch):
    """The live probe returns the largest immediate subdirs by real byte size."""
    from universal_agent.services.invariants import disk_usage_health as duh

    root = tmp_path / "workspaces"
    root.mkdir()
    (root / "big").mkdir()
    (root / "big" / "blob.bin").write_bytes(b"\0" * (5 * 1024 * 1024))  # 5 MiB
    (root / "small").mkdir()
    (root / "small" / "tiny.txt").write_bytes(b"\0" * 1024)  # 1 KiB
    monkeypatch.setenv("UA_DISK_HEALTH_ROOTS", str(root))

    consumers = duh._top_consumers()
    assert consumers, "expected at least one consumer"
    paths = [c["path"] for c in consumers]
    assert str(root / "big") == paths[0]  # largest first
    assert consumers[0]["size_gb"] >= consumers[-1]["size_gb"]


def test_top_consumers_handles_missing_roots(monkeypatch):
    """A root that doesn't exist degrades to an empty result, never raises."""
    from universal_agent.services.invariants import disk_usage_health as duh

    monkeypatch.setenv(
        "UA_DISK_HEALTH_ROOTS",
        "/definitely/not/a/real/path/xyz,/another/missing/one",
    )
    assert duh._top_consumers() == []


def test_top_consumers_does_not_follow_symlink_loops(tmp_path, monkeypatch):
    """A self-referential symlink under a root must not hang or raise — the
    bounded scan skips symlinks and stays finite."""
    from universal_agent.services.invariants import disk_usage_health as duh

    root = tmp_path / "ws"
    root.mkdir()
    real = root / "real"
    real.mkdir()
    (real / "f.bin").write_bytes(b"\0" * 2048)
    # Self-loop symlink inside the measured subtree.
    (real / "loop").symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("UA_DISK_HEALTH_ROOTS", str(root))

    consumers = duh._top_consumers()  # must return, not hang
    assert any(c["path"] == str(real) for c in consumers)


def test_pressured_finding_embeds_live_top_consumers(tmp_path, monkeypatch):
    """End-to-end: when a mount is pressured, the finding's message + observed
    value name the live-scanned top consumers with fresh GB — proving the
    narrative is measured at evaluation time, not a hardcoded assumption."""
    root = tmp_path / "AGENT_RUN_WORKSPACES"
    root.mkdir()
    (root / "vp_coder_primary_external").mkdir()
    (root / "vp_coder_primary_external" / "hog.bin").write_bytes(
        b"\0" * (7 * 1024 * 1024)
    )
    monkeypatch.setenv("UA_DISK_HEALTH_ROOTS", str(root))

    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(88), _gb(12)),  # 88% pressured
    }):
        from universal_agent.services.invariants import disk_usage_health
        importlib.reload(disk_usage_health)
        # Scans run on a background thread; measure synchronously so the
        # assertion is deterministic rather than racing the refresh.
        disk_usage_health._refresh_top_consumers()
        result = disk_usage_health.disk_usage_health({})
    assert result is not None
    obs = result.get("observed_value") or {}
    top = obs.get("top_consumers") or []
    assert any("vp_coder_primary_external" in c["path"] for c in top)
    assert "vp_coder_primary_external" in (result.get("message") or "")


def test_probe_never_scans_inline(tmp_path, monkeypatch):
    """The probe is awaited on the gateway event loop by ops_proactive_health
    (`async def`), so it must never run the scan inline — it serves the cached
    result and kicks the refresh onto a background thread. Regression for the
    pre-2026-07-15 probe, which called _top_consumers() synchronously and could
    block the loop for the whole scan budget."""
    monkeypatch.setenv("UA_DISK_HEALTH_ROOTS", str(tmp_path))
    from universal_agent.services.invariants import disk_usage_health
    importlib.reload(disk_usage_health)

    scanned: list[str] = []
    monkeypatch.setattr(
        disk_usage_health, "_top_consumers", lambda *a, **k: scanned.append("scan") or []
    )
    monkeypatch.setattr(disk_usage_health.threading, "Thread", lambda **k: _NoopThread())

    with _mock_disk_usage({"/": DiskUsage(_gb(100), _gb(91), _gb(9))}):
        result = disk_usage_health.disk_usage_health({})

    assert result is not None  # finding still emitted
    assert scanned == [], "probe must not scan on the caller's thread"


class _NoopThread:
    def start(self) -> None:
        return None


def test_finding_message_handles_absent_roots_gracefully(tmp_path, monkeypatch):
    """When every consumer root is absent, the finding still emits and the
    message says the scan found nothing (no crash, no stale claim)."""
    monkeypatch.setenv("UA_DISK_HEALTH_ROOTS", "/no/such/root/here")
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(91), _gb(9)),  # 91% critical
    }):
        from universal_agent.services.invariants import disk_usage_health
        importlib.reload(disk_usage_health)
        disk_usage_health._refresh_top_consumers()  # scan runs, finds nothing
        result = disk_usage_health.disk_usage_health({})
    assert result is not None
    assert (result.get("observed_value") or {}).get("top_consumers") == []
    assert "Live top-consumer scan found nothing" in (result.get("message") or "")


def test_default_roots_cover_the_parents_not_a_leaf_allowlist(monkeypatch):
    """Regression for 2026-07-15: the default roots were four hand-picked leaf
    dirs (~17G of a 170G-used disk), so the alert reported a 0.04G dir as the
    top consumer while 18G sat in /home/ua/.cache and 16G in .worktrees —
    neither reachable from any configured root. The defaults must be the
    PARENTS those live under, so a consumer nobody enumerated still gets found.
    """
    monkeypatch.delenv("UA_DISK_HEALTH_ROOTS", raising=False)
    from universal_agent.services.invariants import disk_usage_health
    importlib.reload(disk_usage_health)

    roots = disk_usage_health._consumer_roots()

    # The dirs that actually held the space in the incident must be reachable.
    for missed in ("/home/ua/.cache", "/opt/universal_agent/.worktrees"):
        assert any(
            missed.startswith(r.rstrip("/") + "/") for r in roots
        ), f"{missed} is unreachable from default roots {roots}"


def test_scan_reports_children_not_the_root_aggregate(tmp_path, monkeypatch):
    """`du --max-depth=1` also prints the root's own total. Reporting it would
    double-count the root against its own children in the largest-first list."""
    (tmp_path / "child").mkdir()
    (tmp_path / "child" / "big.bin").write_bytes(b"\0" * (4 * 1024 * 1024))
    monkeypatch.setenv("UA_DISK_HEALTH_ROOTS", str(tmp_path))

    from universal_agent.services.invariants import disk_usage_health
    importlib.reload(disk_usage_health)
    consumers = disk_usage_health._top_consumers()

    paths = [c["path"] for c in consumers]
    assert any("child" in p for p in paths)
    assert str(tmp_path) not in paths, "root aggregate must not be reported"
