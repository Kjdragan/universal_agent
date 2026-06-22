"""The ClaudeDevs/bcherny X-intel poller is paused by operator decision
(X API out of credits, HTTP 402). Guard the durable-pause behavior:

- Default OFF: _claude_code_intel_cron_enabled() is False with no env set.
- Paused registration DURABLY disables an existing live row (closes the
  "disable-on-flip leaves the row firing" landmine) rather than bare-returning.
- Resume (env=1) re-registers the job enabled.
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
    monkeypatch.setattr(gateway_server, "_cron_service", CronService(_StubGateway(), tmp_path))


def test_default_is_paused(monkeypatch):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_CRON_ENABLED", raising=False)
    assert gateway_server._claude_code_intel_cron_enabled() is False


def test_paused_disables_existing_live_row(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("UA_CLAUDE_CODE_INTEL_CRON_ENABLED", raising=False)
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    # Simulate a previously-registered, still-enabled live row.
    gateway_server._cron_service.add_job(
        user_id="system",
        workspace_dir=str(ws),
        command="!script universal_agent.scripts.claude_code_intel_run_report",
        cron_expr="0 8,16,22 * * *",
        timezone="America/Chicago",
        enabled=True,
        metadata={"system_job": "claude_code_intel_sync"},
    )
    # Re-key the job under the canonical job_id the ensure-fn manages.
    job = gateway_server._cron_service.jobs.pop(
        next(iter(gateway_server._cron_service.jobs))
    )
    job.job_id = "claude_code_intel_sync"
    gateway_server._cron_service.jobs["claude_code_intel_sync"] = job
    assert gateway_server._cron_service.get_job("claude_code_intel_sync").enabled is True

    gateway_server._ensure_claude_code_intel_cron_job()

    refreshed = gateway_server._cron_service.get_job("claude_code_intel_sync")
    assert refreshed is not None, "pause keeps the row (disabled), does not delete it"
    assert refreshed.enabled is False, "paused ensure must durably disable the live row"


def test_resume_registers_enabled(monkeypatch):
    monkeypatch.setenv("UA_CLAUDE_CODE_INTEL_CRON_ENABLED", "1")
    assert gateway_server._claude_code_intel_cron_enabled() is True
    gateway_server._ensure_claude_code_intel_cron_job()
    job = gateway_server._cron_service.get_job("claude_code_intel_sync")
    assert job is not None and job.enabled is True
