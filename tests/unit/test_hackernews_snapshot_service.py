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
    assert captured["env"]["XDG_CONFIG_HOME"].endswith("/config")
    assert captured["env"]["XDG_DATA_HOME"].endswith("/data")


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
    assert snap["meta"]["schema_version"] == 1
    assert snap["meta"]["errors"] == []
    assert isinstance(snap["meta"]["duration_seconds"], float)
    assert snap["top_stories"] == [{"id": 1, "title": "t1"}]
    assert set(snap["pulses"].keys()) == set(svc.DEFAULT_TOPICS)
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
    # other panels survived
    assert snap["top_stories"] is not None
    assert snap["pulses"]["agent"] == {"ok": True}
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
