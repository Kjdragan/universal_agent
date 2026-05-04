"""Pre-flight required-secrets check for cron jobs.

Phase 5: each `_ensure_*_cron_job` declares its required env-var
dependencies in `metadata.required_secrets`.  Before launching the job
process / LLM call, the cron service verifies all listed secrets are
present.  If any are missing, the run is failed immediately with a
structured error that flows through Phase 0's `cron_run_failed`
notification path — so the operator sees a kind-upserted dashboard
alert listing the missing keys, instead of the job firing-and-dying
with no visible cause.

This catches the "Infisical key not provisioned" silent failure mode
before 8 AM hits.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent.cron_service import CronJob, CronService


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)


def _make_job(*, system_job: str, required_secrets: list[str], workspace_dir: Path) -> CronJob:
    job = CronJob(
        job_id=f"test_{system_job}",
        user_id="cron_system",
        workspace_dir=str(workspace_dir),
        command="!script universal_agent.scripts.youtube_daily_digest",
        cron_expr="0 8 * * *",
        timezone="UTC",
        timeout_seconds=60,
        enabled=True,
        metadata={
            "system_job": system_job,
            "autonomous": True,
            "required_secrets": required_secrets,
        },
    )
    return job


@pytest.mark.asyncio
async def test_cron_run_fails_when_required_secret_missing(monkeypatch, tmp_path):
    """When a job declares `metadata.required_secrets` and one of the
    listed env vars is unset, the run must fail with status=error and
    emit a `cron_run_completed` event with `error` mentioning the
    missing keys."""
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.delenv("FAKE_REQUIRED_SECRET", raising=False)
    monkeypatch.delenv("ANOTHER_FAKE_SECRET", raising=False)
    # Skip workflow admission to keep the test focused on the new check.

    cron = CronService(_StubGateway(), tmp_path)
    job = _make_job(
        system_job="fake_proactive_job",
        required_secrets=["FAKE_REQUIRED_SECRET", "ANOTHER_FAKE_SECRET"],
        workspace_dir=tmp_path / "cron_fake",
    )
    cron.jobs[job.job_id] = job
    (tmp_path / "cron_fake").mkdir(parents=True, exist_ok=True)

    captured_events: list[dict] = []

    def _capture(event):
        captured_events.append(event)

    cron.event_sink = _capture

    record = await cron._run_job(
        job,
        scheduled_at=None,
        reason="unit_test",
        skip_workflow_admission=True,
    )

    assert record.status == "error", (
        f"Run should fail with missing-secret precheck; got status={record.status}, "
        f"error={record.error}"
    )
    assert "FAKE_REQUIRED_SECRET" in (record.error or "")
    assert "ANOTHER_FAKE_SECRET" in (record.error or "")

    completed = [e for e in captured_events if e.get("type") == "cron_run_completed"]
    assert len(completed) == 1
    assert completed[0]["run"]["status"] == "error"


@pytest.mark.asyncio
async def test_cron_run_proceeds_when_required_secrets_present(monkeypatch, tmp_path):
    """When all declared secrets are present, the precheck must pass
    (run proceeds to its normal path).  We mock the run path so the
    test doesn't actually subprocess-out."""
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("FAKE_REQUIRED_SECRET", "value-1")
    monkeypatch.setenv("ANOTHER_FAKE_SECRET", "value-2")
    monkeypatch.setenv("UA_CRON_MOCK_RESPONSE", "1")

    cron = CronService(_StubGateway(), tmp_path)
    job = _make_job(
        system_job="fake_proactive_job",
        required_secrets=["FAKE_REQUIRED_SECRET", "ANOTHER_FAKE_SECRET"],
        workspace_dir=tmp_path / "cron_fake",
    )
    cron.jobs[job.job_id] = job
    (tmp_path / "cron_fake").mkdir(parents=True, exist_ok=True)

    record = await cron._run_job(
        job,
        scheduled_at=None,
        reason="unit_test",
        skip_workflow_admission=True,
    )

    assert record.status == "success", (
        f"All secrets present — run should proceed; got status={record.status}, "
        f"error={record.error}"
    )


@pytest.mark.asyncio
async def test_cron_run_with_no_required_secrets_metadata_proceeds(monkeypatch, tmp_path):
    """A job that does NOT declare `required_secrets` must run normally —
    we cannot regress jobs that haven't opted in to the precheck yet."""
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_CRON_MOCK_RESPONSE", "1")

    cron = CronService(_StubGateway(), tmp_path)
    job = CronJob(
        job_id="test_no_metadata",
        user_id="cron_system",
        workspace_dir=str(tmp_path / "cron_no_meta"),
        command="!script universal_agent.scripts.youtube_daily_digest",
        cron_expr="0 8 * * *",
        timezone="UTC",
        timeout_seconds=60,
        enabled=True,
        metadata={"system_job": "no_secrets_declared"},
    )
    cron.jobs[job.job_id] = job
    (tmp_path / "cron_no_meta").mkdir(parents=True, exist_ok=True)

    record = await cron._run_job(
        job,
        scheduled_at=None,
        reason="unit_test",
        skip_workflow_admission=True,
    )

    assert record.status == "success"
