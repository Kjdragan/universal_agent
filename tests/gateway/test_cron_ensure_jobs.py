"""Tests for idempotent boot-time cron job registration helpers.

The user goal is robustness: every proactive job must survive deploys
and fresh state.  These tests pin the contract that `_ensure_*_cron_job`
helpers are idempotent — adding a job when missing, updating it when
present — and that they respect env-var overrides for cron expression,
timezone, and enable flag.

The Daily YouTube Digest is the first job migrated to this pattern;
later phases extend it to nightly_wiki, proactive_reports, etc.
"""

from types import SimpleNamespace

import pytest

from universal_agent import gateway_server


class _CronBootstrapStub:
    def __init__(self):
        self.jobs: list[SimpleNamespace] = []
        self.updated: list[tuple[str, dict]] = []

    def list_jobs(self):
        return list(self.jobs)

    def add_job(self, **kwargs):
        job = SimpleNamespace(
            job_id=f"cron_{kwargs.get('metadata', {}).get('system_job') or 'unknown'}_1",
            metadata=kwargs.get("metadata", {}),
            workspace_dir=kwargs.get("workspace_dir"),
            command=kwargs.get("command"),
            cron_expr=kwargs.get("cron_expr"),
            timezone=kwargs.get("timezone"),
            enabled=kwargs.get("enabled"),
            catch_up_on_restart=kwargs.get("catch_up_on_restart"),
            to_dict=lambda **_kw: {
                "job_id": f"cron_{kwargs.get('metadata', {}).get('system_job') or 'unknown'}_1",
                "cron_expr": kwargs.get("cron_expr"),
                "timezone": kwargs.get("timezone"),
                "enabled": kwargs.get("enabled"),
                "catch_up_on_restart": kwargs.get("catch_up_on_restart"),
                "metadata": kwargs.get("metadata", {}),
            },
        )
        self.jobs.append(job)
        return job

    def update_job(self, job_id: str, updates: dict):
        self.updated.append((job_id, updates))
        for job in self.jobs:
            if job.job_id == job_id:
                for key, value in updates.items():
                    setattr(job, key, value)
                return SimpleNamespace(
                    job_id=job_id,
                    metadata=updates.get("metadata", {}),
                    to_dict=lambda: {
                        "job_id": job_id,
                        "cron_expr": updates.get("cron_expr"),
                        "timezone": updates.get("timezone"),
                        "enabled": updates.get("enabled"),
                        "catch_up_on_restart": updates.get("catch_up_on_restart"),
                        "metadata": updates.get("metadata", {}),
                    },
                )
        raise KeyError(job_id)


# ─────────────────────────────────────────────────────────────────────────────
# Daily YouTube Digest
# ─────────────────────────────────────────────────────────────────────────────


def test_ensure_youtube_daily_digest_cron_job_creates_new(monkeypatch):
    """First boot with no existing job: helper must register the job
    via add_job, with catch_up_on_restart=True and the canonical
    `system_job` metadata key."""
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.delenv("UA_YOUTUBE_DAILY_DIGEST_ENABLED", raising=False)
    monkeypatch.delenv("UA_YOUTUBE_DAILY_DIGEST_CRON", raising=False)
    monkeypatch.delenv("UA_YOUTUBE_DAILY_DIGEST_TIMEZONE", raising=False)

    result = gateway_server._ensure_youtube_daily_digest_cron_job()

    assert result is not None, "helper must register the job and return its dict"
    assert len(cron_stub.jobs) == 1
    job = cron_stub.jobs[0]
    assert job.metadata["system_job"] == gateway_server.YOUTUBE_DAILY_DIGEST_JOB_KEY
    assert job.metadata.get("autonomous") is True
    assert job.catch_up_on_restart is True, (
        "Missed runs must be caught up on restart (the seed cron_jobs.json "
        "had this off, which silently dropped digests across deploys)."
    )
    assert job.cron_expr == gateway_server.YOUTUBE_DAILY_DIGEST_DEFAULT_CRON
    assert "youtube_daily_digest" in (job.command or "")


def test_ensure_youtube_daily_digest_cron_job_updates_existing(monkeypatch):
    """Subsequent boots: helper must call update_job on the existing
    record (matched by metadata.system_job), not stack duplicates."""
    cron_stub = _CronBootstrapStub()
    existing = SimpleNamespace(
        job_id="legacy_youtube_digest",
        metadata={"system_job": "youtube_daily_digest"},
        to_dict=lambda: {"job_id": "legacy_youtube_digest"},
    )
    cron_stub.jobs = [existing]
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv("UA_YOUTUBE_DAILY_DIGEST_ENABLED", "1")

    result = gateway_server._ensure_youtube_daily_digest_cron_job()

    assert result is not None
    assert len(cron_stub.updated) == 1
    job_id, updates = cron_stub.updated[0]
    assert job_id == "legacy_youtube_digest"
    assert updates["catch_up_on_restart"] is True
    assert updates["enabled"] is True


@pytest.mark.parametrize(
    "env_value,expected_skip",
    [
        ("0", True),
        ("false", True),
        ("no", True),
        ("off", True),
        ("1", False),
        ("true", False),
    ],
)
def test_ensure_youtube_daily_digest_cron_job_respects_disable_flag(
    monkeypatch, env_value, expected_skip
):
    """`UA_YOUTUBE_DAILY_DIGEST_ENABLED=0` must skip registration so a
    misbehaving job can be quickly disabled in prod by env override
    without code change."""
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv("UA_YOUTUBE_DAILY_DIGEST_ENABLED", env_value)

    result = gateway_server._ensure_youtube_daily_digest_cron_job()

    if expected_skip:
        assert result is None
        assert cron_stub.jobs == []
    else:
        assert result is not None
        assert len(cron_stub.jobs) == 1


def test_ensure_youtube_daily_digest_cron_job_respects_env_overrides(monkeypatch):
    """Custom cron expression and timezone must flow through unchanged."""
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv("UA_YOUTUBE_DAILY_DIGEST_ENABLED", "1")
    monkeypatch.setenv("UA_YOUTUBE_DAILY_DIGEST_CRON", "30 9 * * 1")
    monkeypatch.setenv("UA_YOUTUBE_DAILY_DIGEST_TIMEZONE", "America/New_York")

    gateway_server._ensure_youtube_daily_digest_cron_job()

    assert len(cron_stub.jobs) == 1
    job = cron_stub.jobs[0]
    assert job.cron_expr == "30 9 * * 1"
    assert job.timezone == "America/New_York"
