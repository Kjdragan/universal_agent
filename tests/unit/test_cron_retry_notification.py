"""Regression: a retry_queued cron run must NOT email an [ERROR] alert.

cron_service emits a failed-but-retryable run as a `cron_run_completed`
event carrying status="retry_queued". gateway_server._emit_cron_event used
to classify that in the `else` branch -> kind=autonomous_run_failed,
severity=error -> out-of-band email. That spammed an `[ERROR] Autonomous
Task Failed` for every self-healing transient (e.g. the claude_code_intel
X-API 402 cooldown, which fails attempt 1 then "succeeds" on the cooldown
short-circuit). The retry_queued branch downgrades it to info severity so
only TERMINAL failures (retries exhausted) email.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent import gateway_server
from universal_agent.cron_service import CronService


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_cron_service", None)


def _add_job(tmp_path: Path):
    gateway_server._cron_service = CronService(_StubGateway(), tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return gateway_server._cron_service.add_job(
        user_id="owner",
        workspace_dir=str(ws),
        command="!script universal_agent.scripts.claude_code_intel_run_report",
        every_raw="30m",
        enabled=True,
        # autonomous=True is what makes the failure branch emit the
        # error-severity `autonomous_run_failed` email (the operator's case).
        metadata={"autonomous": True, "system_job": "claude_code_intel_sync"},
    )


def test_retry_queued_completed_event_is_info_not_error(tmp_path: Path):
    job = _add_job(tmp_path)
    gateway_server._emit_cron_event(
        {
            "type": "cron_run_completed",
            "run": {
                "run_id": "run_retry_1",
                "job_id": job.job_id,
                "status": "retry_queued",
                "error": "Script exited with 1",
            },
        }
    )
    rows = [n for n in gateway_server._notifications if n.get("metadata", {}).get("job_id") == job.job_id]
    assert rows, "expected a notification for the retry_queued run"
    latest = rows[-1]
    assert latest["kind"] == "cron_run_retry_queued"
    assert latest["severity"] == "info"
    # The whole point: no error-severity / autonomous_run_failed alert.
    assert all(n["kind"] != "autonomous_run_failed" for n in rows)
    assert all(n["severity"] != "error" for n in rows)


def test_terminal_failure_still_errors(tmp_path: Path):
    """A genuinely failed run (retries exhausted) MUST still alert."""
    job = _add_job(tmp_path)
    gateway_server._emit_cron_event(
        {
            "type": "cron_run_completed",
            "run": {
                "run_id": "run_fail_1",
                "job_id": job.job_id,
                "status": "failed",
                "error": "boom",
            },
        }
    )
    rows = [n for n in gateway_server._notifications if n.get("metadata", {}).get("job_id") == job.job_id]
    assert rows, "expected a notification for the failed run"
    assert any(n["kind"] == "autonomous_run_failed" and n["severity"] == "error" for n in rows)
