"""Unit tests for the Hacker News snapshot service.

All CLI invocations are mocked; the real `hackernews-pp-cli` binary is never
called.  Tests cover watchlist parsing, build_snapshot success / partial /
total failure paths, and write_snapshot ring pruning.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any

import pytest

from universal_agent.services import hackernews_snapshot_service as svc

# ─── _load_watchlist ───────────────────────────────────────────────────


def test_load_watchlist_returns_defaults_when_file_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing.yaml")
    topics = svc._load_watchlist()
    assert topics == svc.DEFAULT_TOPICS
    # caller mutating the result must not bleed into module state
    assert topics is not svc.DEFAULT_TOPICS


def test_load_watchlist_returns_parsed_list(monkeypatch, tmp_path: Path) -> None:
    p = tmp_path / "watch.yaml"
    p.write_text("topics:\n  - claude\n  - rust\n")
    monkeypatch.setattr(svc, "WATCHLIST_FILE", p)
    assert svc._load_watchlist() == ["claude", "rust"]


def test_load_watchlist_falls_back_on_oversize_list(
    monkeypatch, tmp_path: Path
) -> None:
    p = tmp_path / "watch.yaml"
    p.write_text("topics:\n" + "\n".join(f"  - t{i}" for i in range(10)))
    monkeypatch.setattr(svc, "WATCHLIST_FILE", p)
    assert svc._load_watchlist() == svc.DEFAULT_TOPICS


def test_load_watchlist_falls_back_on_malformed_yaml(
    monkeypatch, tmp_path: Path
) -> None:
    p = tmp_path / "watch.yaml"
    p.write_text("not: valid: yaml: at: all:\n  - [")
    monkeypatch.setattr(svc, "WATCHLIST_FILE", p)
    assert svc._load_watchlist() == svc.DEFAULT_TOPICS


def test_load_watchlist_falls_back_on_wrong_shape(
    monkeypatch, tmp_path: Path
) -> None:
    p = tmp_path / "watch.yaml"
    p.write_text("topics: not-a-list\n")
    monkeypatch.setattr(svc, "WATCHLIST_FILE", p)
    assert svc._load_watchlist() == svc.DEFAULT_TOPICS


# ─── _run_cli ──────────────────────────────────────────────────────────


def _make_completed(stdout: str = "{}", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["hackernews-pp-cli"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_run_cli_returns_parsed_json(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        return _make_completed(stdout='{"ok": true}')

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    result = svc._run_cli(["stories", "top"])
    assert result == {"ok": True}
    assert captured["cmd"][-2:] == ["--json", "--agent"]
    assert captured["env"]["HOME"] == "/opt/universal_agent/var/hackernews"
    assert captured["env"]["HACKERNEWS_NO_COLOR"] == "1"


def test_run_cli_returns_none_on_nonzero_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        svc.subprocess,
        "run",
        lambda *a, **k: _make_completed(stderr="boom", returncode=5),
    )
    assert svc._run_cli(["sync"]) is None


def test_run_cli_returns_none_on_timeout(monkeypatch) -> None:
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="hn", timeout=1)

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    assert svc._run_cli(["sync"]) is None


def test_run_cli_returns_none_on_missing_binary(monkeypatch) -> None:
    def fake_run(*a, **k):
        raise FileNotFoundError("no binary")

    monkeypatch.setattr(svc.subprocess, "run", fake_run)
    assert svc._run_cli(["sync"]) is None


def test_run_cli_returns_none_on_bad_json(monkeypatch) -> None:
    monkeypatch.setattr(
        svc.subprocess, "run", lambda *a, **k: _make_completed(stdout="not json")
    )
    assert svc._run_cli(["sync"]) is None


# ─── build_snapshot ────────────────────────────────────────────────────


@pytest.fixture
def all_ok(monkeypatch):
    def fake(args, timeout=60):
        if args[0] == "sync":
            return {"synced": True}
        if args == ["stories", "top", "--limit", "50"]:
            return [{"id": 1, "title": "t1"}]
        if args[:1] == ["since"]:
            return {"since": "x", "changes": []}
        if args[:1] == ["controversial"]:
            return [{"id": 2}]
        if args[:1] == ["pulse"]:
            return {"topic": args[1], "count": 5}
        if args[:2] == ["stories", "show"]:
            return [{"id": 10}]
        if args[:2] == ["stories", "ask"]:
            return [{"id": 11}]
        if args[:1] == ["hiring"]:
            return {"companies": []}
        return {}

    monkeypatch.setattr(svc, "_run_cli", fake)


def test_build_snapshot_success(all_ok, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing")
    snap = svc.build_snapshot()
    assert snap["meta"]["schema_version"] == 2
    assert snap["meta"]["errors"] == []
    assert isinstance(snap["meta"]["duration_seconds"], float)
    # Top-stories input was already a list of hydrated dicts; passes through unchanged.
    assert snap["top_stories"] == [{"id": 1, "title": "t1"}]
    assert set(snap["pulses"].keys()) == set(svc.DEFAULT_TOPICS)
    # Pulses are normalized into the frontend-shaped {count, avg_points, trend, pct_change} dict.
    assert snap["pulses"]["claude"] == {
        "topic": "claude",
        "count": 0,  # the fixture returns {"topic":..., "count": 5} but no "total_hits"
        "avg_points": 0,
        "trend": [],
        "pct_change": 0,
    }
    assert snap["meta"]["watchlist"] == svc.DEFAULT_TOPICS


def test_build_snapshot_raises_when_sync_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing")

    def fake(args, timeout=60):
        if args[0] == "sync":
            return None
        return {}

    monkeypatch.setattr(svc, "_run_cli", fake)
    with pytest.raises(RuntimeError, match="sync failed"):
        svc.build_snapshot()


def test_build_snapshot_partial_failure_marks_errors(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing")

    def fake(args, timeout=60):
        if args[0] == "sync":
            return {"ok": True}
        # Simulate hiring + one pulse failing.
        if args[:1] == ["hiring"]:
            return None
        if args[:2] == ["pulse", "claude"]:
            return None
        return {"ok": True}

    monkeypatch.setattr(svc, "_run_cli", fake)
    snap = svc.build_snapshot()
    assert "hiring" in snap["meta"]["errors"]
    assert "pulse_claude" in snap["meta"]["errors"]
    # other panels survived (post-normalization shape)
    assert snap["top_stories"] is not None
    assert snap["pulses"]["agent"]["topic"] == "agent"
    assert snap["pulses"]["agent"]["count"] == 0  # fake returns {"ok": True} → no total_hits
    assert snap["pulses"]["claude"] is None
    assert snap["hiring"] is None


def test_build_snapshot_raises_when_all_panels_fail(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing")

    def fake(args, timeout=60):
        if args[0] == "sync":
            return {"ok": True}
        return None

    monkeypatch.setattr(svc, "_run_cli", fake)
    with pytest.raises(RuntimeError, match="all panels failed"):
        svc.build_snapshot()


# ─── write_snapshot / read_latest ──────────────────────────────────────


def _stub_artifacts(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(svc, "resolve_artifacts_dir", lambda: tmp_path)
    return tmp_path


def test_write_snapshot_creates_latest_and_ring(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_artifacts(monkeypatch, tmp_path)
    snapshot = {
        "meta": {
            "generated_at": "2026-05-09T12:34:56+00:00",
            "schema_version": 1,
            "watchlist": [],
            "errors": [],
            "duration_seconds": 0.1,
        },
        "top_stories": [],
    }
    path = svc.write_snapshot(snapshot)
    assert path == tmp_path / "hackernews" / "latest.json"
    assert path.exists()
    ring = list((tmp_path / "hackernews" / "snapshots").glob("*.json"))
    assert len(ring) == 1
    payload = json.loads(path.read_text())
    assert payload["meta"]["schema_version"] == 1


def test_write_snapshot_prunes_ring_to_depth(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_artifacts(monkeypatch, tmp_path)
    monkeypatch.setattr(svc, "SNAPSHOT_RING_DEPTH", 3)

    # Pre-populate snapshots/ with 4 older entries that should be pruned.
    snaps = tmp_path / "hackernews" / "snapshots"
    snaps.mkdir(parents=True, exist_ok=True)
    for i in range(4):
        (snaps / f"2025010{i}T00000000+0000.json").write_text("{}")

    snapshot = {
        "meta": {
            "generated_at": "2026-12-31T23:59:59+00:00",
            "schema_version": 1,
            "watchlist": [],
            "errors": [],
            "duration_seconds": 0.0,
        }
    }
    svc.write_snapshot(snapshot)
    remaining = sorted(p.name for p in snaps.glob("*.json"))
    assert len(remaining) == 3
    # The newest (current) write must be retained.
    assert any("20261231T235959" in n for n in remaining)


def test_read_latest_returns_none_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_artifacts(monkeypatch, tmp_path)
    assert svc.read_latest() is None


def test_read_latest_returns_parsed_payload(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_artifacts(monkeypatch, tmp_path)
    root = tmp_path / "hackernews"
    root.mkdir(parents=True, exist_ok=True)
    (root / "latest.json").write_text(json.dumps({"meta": {"schema_version": 1}}))
    assert svc.read_latest() == {"meta": {"schema_version": 1}}


def test_read_latest_returns_none_on_corrupt(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_artifacts(monkeypatch, tmp_path)
    root = tmp_path / "hackernews"
    root.mkdir(parents=True, exist_ok=True)
    (root / "latest.json").write_text("{not valid json")
    assert svc.read_latest() is None


# ─── duration_seconds is wall-clock, not zero ──────────────────────────


def test_build_snapshot_records_duration(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing")

    counter = {"n": 0}

    def fake_run_cli(args, timeout=60):
        counter["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(svc, "_run_cli", fake_run_cli)
    real_monotonic = time.monotonic
    times = iter([0.0, 1.5])
    monkeypatch.setattr(svc.time, "monotonic", lambda: next(times, real_monotonic()))

    snap = svc.build_snapshot()
    assert snap["meta"]["duration_seconds"] == 1.5
    # sync + 6 pulses + 6 fixed panels (top, movers, controversial, show, ask, hiring) = 13
    assert counter["n"] == 13


# ─── _normalize_* helpers ──────────────────────────────────────────────


def test_normalize_top_like_passes_through_hydrated_dicts() -> None:
    raw = [{"id": 1, "title": "t"}, {"id": 2, "title": "u"}]
    assert svc._normalize_top_like(raw, limit=10) == raw


def test_normalize_top_like_handles_results_wrapper(monkeypatch) -> None:
    # IDs-only payload (the real CLI shape for `stories top|show|ask`).
    monkeypatch.setattr(
        svc,
        "_hydrate_stories",
        lambda ids, max_workers=8: [{"id": i, "title": f"t{i}"} for i in ids],
    )
    raw = {"meta": {"source": "live"}, "results": [10, 20, 30]}
    assert svc._normalize_top_like(raw, limit=2) == [
        {"id": 10, "title": "t10"},
        {"id": 20, "title": "t20"},
    ]


def test_normalize_top_like_passes_none_through() -> None:
    assert svc._normalize_top_like(None, limit=5) is None


def test_normalize_movers_emits_changes(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "_hydrate_stories",
        lambda ids, max_workers=8: [
            {"id": int(i), "title": f"#{i}", "score": 100} for i in ids
        ],
    )
    raw = {
        "previous_taken_at": "2026-05-09T15:00:00Z",
        "current_taken_at": "2026-05-09T15:30:00Z",
        "added": ["111"],
        "removed": ["222"],
        "moved": [{"id": "333", "from_rank": 5, "to_rank": 2}],
    }
    out = svc._normalize_movers(raw)
    assert out["since"] == "2026-05-09T15:00:00Z"
    statuses = [c["status"] for c in out["changes"]]
    assert statuses == ["new", "moved", "dropped"]
    moved = next(c for c in out["changes"] if c["status"] == "moved")
    assert moved["delta"] == 3  # climbed 3 ranks
    assert moved["rank"] == 2


def test_normalize_pulse_computes_avg_points() -> None:
    raw = {
        "topic": "claude",
        "total_hits": 232,
        "top_stories": [
            {"id": "1", "points": 500},
            {"id": "2", "points": 300},
            {"id": "3", "points": 100},
        ],
    }
    out = svc._normalize_pulse(raw, "claude")
    assert out == {
        "topic": "claude",
        "count": 232,
        "avg_points": 300,
        "trend": [],
        "pct_change": 0,
    }


def test_normalize_hiring_maps_top_companies() -> None:
    raw = {
        "top_companies": [
            {"name": "Apple", "count": 4},
            {"name": "MongoDB", "count": 4},
            {"name": "Foxglove", "count": 3},
            {"name": "X", "count": 2},
            {"name": "Y", "count": 2},
            {"name": "Z", "count": 1},  # capped to 5
        ]
    }
    out = svc._normalize_hiring(raw)
    assert out == {
        "companies": [
            {"name": "Apple", "months": 4},
            {"name": "MongoDB", "months": 4},
            {"name": "Foxglove", "months": 3},
            {"name": "X", "months": 2},
            {"name": "Y", "months": 2},
        ]
    }


def test_normalize_controversial_passes_list_through() -> None:
    raw = [{"id": "1", "title": "x", "score": 10, "descendants": 100}]
    assert svc._normalize_controversial(raw) == raw


# ─── P2.B2 — CSI emitter wiring ────────────────────────────────────────


def test_build_snapshot_calls_csi_emitter_with_normalized_snapshot(all_ok, monkeypatch, tmp_path: Path) -> None:
    """build_snapshot() must call emit_movers_signals at the end of the tick,
    passing the NORMALIZED snapshot (not the raw CLI output)."""
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing")
    captured: dict[str, Any] = {}

    def fake_emit(snapshot, **kwargs):
        captured["snapshot"] = snapshot
        captured["kwargs"] = kwargs
        return 1

    monkeypatch.setattr(svc, "emit_movers_signals", fake_emit)

    snap = svc.build_snapshot()

    assert "snapshot" in captured, "emit_movers_signals must be called"
    # The captured snapshot is the post-_normalize one — has the keys page.tsx expects
    captured_snap = captured["snapshot"]
    assert "movers" in captured_snap
    assert "controversial" in captured_snap
    # And it's the same one we returned from build_snapshot
    assert captured_snap is snap


def test_build_snapshot_succeeds_when_csi_emitter_raises(all_ok, monkeypatch, tmp_path: Path) -> None:
    """A crashing emitter must NEVER abort the snapshot — it's best-effort."""
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing")

    def angry_emit(snapshot, **kwargs):
        raise RuntimeError("CSI is on fire")

    monkeypatch.setattr(svc, "emit_movers_signals", angry_emit)

    # Must not raise
    snap = svc.build_snapshot()
    assert snap["meta"]["schema_version"] == 2  # we still got a snapshot back


def test_build_snapshot_does_not_call_emitter_when_all_panels_fail(
    monkeypatch, tmp_path: Path,
) -> None:
    """If we abort the tick (RuntimeError), emit must NOT have been called —
    we don't want to emit signals based on a non-existent snapshot."""
    monkeypatch.setattr(svc, "WATCHLIST_FILE", tmp_path / "missing")

    def fake(args, timeout=60):
        if args[0] == "sync":
            return {"ok": True}
        return None  # all panels fail

    monkeypatch.setattr(svc, "_run_cli", fake)

    called: dict[str, bool] = {"emitted": False}

    def fake_emit(snapshot, **kwargs):
        called["emitted"] = True
        return 0

    monkeypatch.setattr(svc, "emit_movers_signals", fake_emit)

    with pytest.raises(RuntimeError, match="all panels failed"):
        svc.build_snapshot()
    assert called["emitted"] is False
