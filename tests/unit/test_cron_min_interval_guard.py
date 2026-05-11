"""Guard against sub-minute cron intervals via the every_seconds path.

Motivation: 2026-05-11 a pair of test crons were created with every_seconds=2
("cron schedule" / "cron test" placeholder commands), ran every 2 seconds for
~10 hours, and generated a retry-storm of ~30 warning emails to the operator.
The cron service had no minimum-interval validation on the `every_seconds`
path (the alternative-to-cron_expr simple-interval mode). All 17 legitimate
production crons use cron_expr; nothing uses every_seconds. This guard
prevents the misuse without removing the API surface.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent.cron_service import MIN_CRON_INTERVAL_SECONDS, CronService


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)

    async def run_query(self, session, request, **_kwargs):
        return SimpleNamespace(response_text="")


def _service(tmp_path: Path) -> CronService:
    return CronService(_StubGateway(), tmp_path)


def test_add_job_rejects_two_second_interval(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="below the minimum interval"):
        service.add_job(
            user_id="cron",
            workspace_dir=str(tmp_path / "cron_test"),
            command="echo hi",
            every_raw="2s",
        )


def test_add_job_rejects_just_under_minimum(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="below the minimum interval"):
        service.add_job(
            user_id="cron",
            workspace_dir=str(tmp_path / "cron_test"),
            command="echo hi",
            every_raw=f"{MIN_CRON_INTERVAL_SECONDS - 1}s",
        )


def test_add_job_accepts_minimum_interval(tmp_path: Path) -> None:
    service = _service(tmp_path)
    job = service.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_ok"),
        command="echo hi",
        every_raw=f"{MIN_CRON_INTERVAL_SECONDS}s",
    )
    assert job.every_seconds == MIN_CRON_INTERVAL_SECONDS


def test_add_job_cron_expr_path_unaffected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    job = service.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_expr"),
        command="echo hi",
        cron_expr="*/5 * * * *",
    )
    assert job.cron_expr == "*/5 * * * *"
    assert job.every_seconds == 0


def test_update_job_rejects_sub_minimum_every_seconds(tmp_path: Path) -> None:
    service = _service(tmp_path)
    job = service.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_update"),
        command="echo hi",
        every_raw="5m",
    )
    with pytest.raises(ValueError, match="below the minimum interval"):
        service.update_job(job.job_id, {"every_seconds": 2})
    # job state must be unchanged after the rejected update
    assert service.get_job(job.job_id).every_seconds == 300
