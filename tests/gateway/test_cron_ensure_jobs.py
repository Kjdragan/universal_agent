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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Same idempotent-boot pattern for the other hard-coded jobs.
#   All four scripts (nightly_wiki_agent, briefings_agent,
#   proactive_report_agent, proactive_digest_agent) live in cron_jobs.json
#   today with no `_ensure_*` helper.  These tests pin the contract that
#   each helper auto-registers its job with `catch_up_on_restart=True`
#   and a unique `system_job` key.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ensure_attr,system_job,enable_env,expected_cron,expected_command_substr",
    [
        (
            "_ensure_nightly_wiki_cron_job",
            "nightly_wiki",
            "UA_NIGHTLY_WIKI_ENABLED",
            "15 3 * * *",
            "nightly_wiki_agent",
        ),
        (
            "_ensure_morning_briefing_cron_job",
            "morning_briefing",
            "UA_MORNING_BRIEFING_ENABLED",
            "30 6 * * *",
            "briefings_agent",
        ),
        (
            "_ensure_proactive_report_morning_cron_job",
            "proactive_report_morning",
            "UA_PROACTIVE_REPORTS_ENABLED",
            "0 7 * * *",
            "proactive_report_agent",
        ),
        (
            "_ensure_proactive_report_midday_cron_job",
            "proactive_report_midday",
            "UA_PROACTIVE_REPORTS_ENABLED",
            "0 12 * * *",
            "proactive_report_agent",
        ),
        (
            "_ensure_proactive_report_afternoon_cron_job",
            "proactive_report_afternoon",
            "UA_PROACTIVE_REPORTS_ENABLED",
            "0 16 * * *",
            "proactive_report_agent",
        ),
        (
            "_ensure_proactive_artifact_digest_cron_job",
            "proactive_artifact_digest",
            "UA_PROACTIVE_ARTIFACT_DIGEST_ENABLED",
            "0 8 * * *",
            "proactive_digest_agent",
        ),
    ],
)
def test_ensure_proactive_cron_jobs_create_with_catch_up_enabled(
    monkeypatch, ensure_attr, system_job, enable_env, expected_cron, expected_command_substr
):
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv(enable_env, "1")

    helper = getattr(gateway_server, ensure_attr)
    result = helper()

    assert result is not None, f"{ensure_attr} must register the job"
    assert len(cron_stub.jobs) == 1
    job = cron_stub.jobs[0]
    assert job.metadata["system_job"] == system_job
    assert job.metadata.get("autonomous") is True
    assert job.catch_up_on_restart is True, (
        f"{ensure_attr}: catch_up_on_restart must be True so a missed run "
        f"is backfilled on next gateway start (the seed file had this off)."
    )
    assert job.cron_expr == expected_cron
    assert expected_command_substr in (job.command or "")


@pytest.mark.parametrize(
    "ensure_attr,system_job",
    [
        ("_ensure_nightly_wiki_cron_job", "nightly_wiki"),
        ("_ensure_morning_briefing_cron_job", "morning_briefing"),
        ("_ensure_proactive_report_morning_cron_job", "proactive_report_morning"),
        ("_ensure_proactive_artifact_digest_cron_job", "proactive_artifact_digest"),
    ],
)
def test_ensure_proactive_cron_jobs_update_existing(monkeypatch, ensure_attr, system_job):
    """Subsequent boots must update the existing record (matched by
    metadata.system_job) rather than stack duplicates."""
    cron_stub = _CronBootstrapStub()
    existing = SimpleNamespace(
        job_id=f"legacy_{system_job}",
        metadata={"system_job": system_job},
        to_dict=lambda: {"job_id": f"legacy_{system_job}"},
    )
    cron_stub.jobs = [existing]
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)

    helper = getattr(gateway_server, ensure_attr)
    result = helper()

    assert result is not None
    assert len(cron_stub.updated) == 1
    job_id, updates = cron_stub.updated[0]
    assert job_id == f"legacy_{system_job}"
    assert updates["catch_up_on_restart"] is True


@pytest.mark.parametrize(
    "ensure_attr,enable_env",
    [
        ("_ensure_nightly_wiki_cron_job", "UA_NIGHTLY_WIKI_ENABLED"),
        ("_ensure_morning_briefing_cron_job", "UA_MORNING_BRIEFING_ENABLED"),
        ("_ensure_proactive_report_morning_cron_job", "UA_PROACTIVE_REPORTS_ENABLED"),
        ("_ensure_proactive_artifact_digest_cron_job", "UA_PROACTIVE_ARTIFACT_DIGEST_ENABLED"),
    ],
)
def test_ensure_proactive_cron_jobs_respect_disable_flag(monkeypatch, ensure_attr, enable_env):
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv(enable_env, "0")

    helper = getattr(gateway_server, ensure_attr)
    result = helper()

    assert result is None
    assert cron_stub.jobs == []


# ─────────────────────────────────────────────────────────────────────────────
# F2: required_secrets adoption.  Helpers whose backing scripts hard-fail
# on a missing env var must declare those keys in metadata so the Phase 5
# pre-flight check can surface them as a kind-upserted cron_run_failed
# notification before the run starts and exits ungracefully.
# ─────────────────────────────────────────────────────────────────────────────


def test_ensure_autonomous_daily_briefing_disabled_by_default(monkeypatch):
    """G2: autonomous_daily_briefing (7:00 AM) overlaps morning_briefing
    (6:30 AM) — both write to the same DAILY_BRIEFING.md path so the
    second one stomps the first.  Default is now OFF; opt-in via
    `UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED=1` if the operator explicitly
    wants the second pass."""
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.delenv("UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED", raising=False)

    result = gateway_server._ensure_autonomous_daily_briefing_job()

    assert result is None, (
        "When the env flag is unset, the helper must return None and "
        "register no job — protects against the briefing-artifact stomp "
        "with morning_briefing."
    )
    assert cron_stub.jobs == []


def test_ensure_autonomous_daily_briefing_explicit_opt_in_still_works(monkeypatch):
    """Operators who explicitly opt in (env=1) get the legacy
    autonomous_daily_briefing job back.  Confirms G2 only flips the
    default — explicit opt-in preserves the original behaviour."""
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv("UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED", "1")

    result = gateway_server._ensure_autonomous_daily_briefing_job()

    assert result is not None
    assert len(cron_stub.jobs) == 1


def test_ensure_morning_briefing_declares_ua_ops_token_required(monkeypatch):
    """`briefings_agent.py:23-26` does `sys.exit(1)` when UA_OPS_TOKEN
    is missing, with a generic stderr line.  Declaring the secret in
    metadata lets the cron pre-flight check fail the run with a
    structured `Missing required secrets: UA_OPS_TOKEN` message that
    surfaces as a dashboard notification — operator sees exactly what
    needs provisioning instead of `Script exited with 1`."""
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv("UA_MORNING_BRIEFING_ENABLED", "1")

    gateway_server._ensure_morning_briefing_cron_job()

    assert len(cron_stub.jobs) == 1
    job = cron_stub.jobs[0]
    required = job.metadata.get("required_secrets") or []
    assert "UA_OPS_TOKEN" in required, (
        f"morning_briefing helper must declare UA_OPS_TOKEN in required_secrets; "
        f"current metadata: {job.metadata!r}"
    )
