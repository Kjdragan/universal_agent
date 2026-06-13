"""Unit tests for the deploy-coalescing decision (Phase B of the deploy-restart
resilience ADR, project_docs/06_platform/12_deploy_restart_resilience_adr.md).

The decision is pure and data-driven so the risky logic is tested here rather
than embedded in deploy.yml YAML. The script is loaded by path (importlib) so it
stays self-contained — deploy.yml fetches and runs it standalone on the GHA
runner, where the universal_agent package is not installed.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy" / "deploy_coalesce.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("deploy_coalesce", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
should_skip_redundant_deploy = _mod.should_skip_redundant_deploy


def _run_cli(stdin_text: str, my_run_id: str):
    """Invoke the script exactly as deploy.yml does: runs JSON on stdin,
    --my-run-id arg, decision on stdout. Returns (returncode, stdout)."""
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--my-run-id", str(my_run_id)],
        input=stdin_text,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


def test_newer_active_run_causes_skip():
    """A strictly-newer Deploy run that is still queued/in-progress will ship a
    superset of origin/main HEAD, so this (older) run is redundant -> skip."""
    runs = [
        {"databaseId": 100, "status": "in_progress", "event": "push"},
        {"databaseId": 101, "status": "queued", "event": "push"},
    ]
    skip, reason = should_skip_redundant_deploy(runs, my_run_id=100)
    assert skip is True
    assert "101" in reason


def test_newer_completed_run_does_not_skip():
    """Only ACTIVE newer runs supersede. A newer run that already completed will
    not run again, so this run must still proceed (else HEAD might never ship).
    With concurrency serialization a higher-id run executes after us, so a
    higher-id 'completed' is an off-nominal/edge timing case — fail safe = proceed.
    """
    runs = [
        {"databaseId": 100, "status": "in_progress", "event": "push"},
        {"databaseId": 101, "status": "completed", "event": "push"},
    ]
    skip, reason = should_skip_redundant_deploy(runs, my_run_id=100)
    assert skip is False


def test_latest_run_never_skips_itself():
    """Safety invariant: the newest run (highest id) has no newer run, so it
    ALWAYS proceeds — guaranteeing the latest origin/main HEAD deploys even when
    older runs in the burst are coalesced away."""
    runs = [
        {"databaseId": 98, "status": "completed", "event": "push"},
        {"databaseId": 99, "status": "queued", "event": "push"},
        {"databaseId": 100, "status": "in_progress", "event": "push"},
    ]
    skip, _ = should_skip_redundant_deploy(runs, my_run_id=100)
    assert skip is False


def test_own_run_is_not_counted_as_newer():
    """My own run appearing in the list (status in_progress) must not be treated
    as a superseding newer run."""
    runs = [{"databaseId": 100, "status": "in_progress", "event": "push"}]
    skip, _ = should_skip_redundant_deploy(runs, my_run_id=100)
    assert skip is False


def test_empty_runs_proceeds():
    """Fail-safe: no run data => never skip."""
    skip, _ = should_skip_redundant_deploy([], my_run_id=100)
    assert skip is False


def test_cli_emits_skip_true_for_newer_active_run():
    runs = [
        {"databaseId": 100, "status": "in_progress"},
        {"databaseId": 101, "status": "queued"},
    ]
    rc, out = _run_cli(json.dumps(runs), my_run_id=100)
    assert rc == 0
    assert "skip=true" in out


def test_cli_emits_skip_false_when_no_newer_run():
    runs = [{"databaseId": 100, "status": "in_progress"}]
    rc, out = _run_cli(json.dumps(runs), my_run_id=100)
    assert rc == 0
    assert "skip=false" in out


def test_cli_fails_safe_on_malformed_stdin():
    """Malformed/empty input must never skip a deploy — emit skip=false, exit 0."""
    rc, out = _run_cli("this is not json", my_run_id=100)
    assert rc == 0
    assert "skip=false" in out
