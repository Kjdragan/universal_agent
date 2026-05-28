"""Regression tests for ``_register_system_cron_job`` enabled=False behavior.

Background
==========

Prior to this fix, ``_register_system_cron_job`` returned ``None`` early
when called with ``enabled=False``, regardless of whether a previously-
enabled row for the same ``system_job`` already existed in the cron DB.

That meant flipping a cron's enable env var from on → off via a code
change (e.g. PR #534 setting ``UA_INSIGHT_HOURLY_EMAIL_ENABLED`` default
to ``"0"``) did NOT propagate to the persisted row — it kept firing on
every gateway restart. Production evidence: ``hourly_insight_email``
fired at 2:03 PM CT on 2026-05-28 despite PR #534's flip four hours
earlier and sent an ``[ERROR] Autonomous Task Failed`` email.

Fixed behavior, asserted here:

* ``enabled=False`` + no existing row → return ``None``, do not call
  ``add_job``/``update_job`` (don't insert a disabled row).
* ``enabled=False`` + existing enabled row → call
  ``update_job(id, {"enabled": False})``, return the updated dict.
* ``enabled=False`` + existing already-disabled row → return ``None``,
  do not churn the row.
* ``enabled=True`` → unchanged upsert behavior.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _kwargs(**overrides):
    """Baseline kwargs for ``_register_system_cron_job``."""
    base = dict(
        system_job="ut_disable_test",
        default_cron="0 * * * *",
        default_timezone="UTC",
        command="!script universal_agent.scripts.noop",
        description="unit test cron",
        timeout_seconds=60,
        enabled=False,
    )
    base.update(overrides)
    return base


def test_disabled_no_existing_row_returns_none_without_db_calls() -> None:
    """enabled=False + no existing row → no-op return None, no add/update."""
    from universal_agent import gateway_server

    mock_service = MagicMock()
    mock_service.add_job = MagicMock()
    mock_service.update_job = MagicMock()

    with patch.object(gateway_server, "_cron_service", mock_service), patch.object(
        gateway_server, "_find_cron_job_by_system_job", return_value=None
    ) as find_mock:
        result = gateway_server._register_system_cron_job(**_kwargs(enabled=False))

    assert result is None
    find_mock.assert_called_once_with("ut_disable_test")
    mock_service.add_job.assert_not_called()
    mock_service.update_job.assert_not_called()


def test_disabled_existing_enabled_row_calls_update_job_with_enabled_false() -> None:
    """enabled=False + existing enabled row → update_job(id, {"enabled": False})."""
    from universal_agent import gateway_server

    existing = SimpleNamespace(job_id="job-abc", enabled=True)
    updated_dict = {"job_id": "job-abc", "enabled": False, "description": "x"}
    updated_obj = SimpleNamespace(
        job_id="job-abc",
        enabled=False,
        to_dict=MagicMock(return_value=updated_dict),
    )

    mock_service = MagicMock()
    mock_service.update_job = MagicMock(return_value=updated_obj)
    mock_service.add_job = MagicMock()

    with patch.object(gateway_server, "_cron_service", mock_service), patch.object(
        gateway_server, "_find_cron_job_by_system_job", return_value=existing
    ):
        result = gateway_server._register_system_cron_job(**_kwargs(enabled=False))

    mock_service.update_job.assert_called_once_with("job-abc", {"enabled": False})
    mock_service.add_job.assert_not_called()
    assert result == updated_dict


def test_disabled_existing_enabled_row_without_to_dict_returns_minimal_dict() -> None:
    """Defensive: update_job returning an object lacking to_dict still yields
    a meaningful dict — same fallback shape used by the enabled-path code."""
    from universal_agent import gateway_server

    existing = SimpleNamespace(job_id="job-xyz", enabled=True)
    # Object intentionally lacks to_dict
    updated_obj = SimpleNamespace(job_id="job-xyz", enabled=False)

    mock_service = MagicMock()
    mock_service.update_job = MagicMock(return_value=updated_obj)

    with patch.object(gateway_server, "_cron_service", mock_service), patch.object(
        gateway_server, "_find_cron_job_by_system_job", return_value=existing
    ):
        result = gateway_server._register_system_cron_job(**_kwargs(enabled=False))

    assert result == {"job_id": "job-xyz", "enabled": False}


def test_disabled_existing_already_disabled_row_is_noop() -> None:
    """enabled=False + existing already-disabled row → no-op, return None."""
    from universal_agent import gateway_server

    existing = SimpleNamespace(job_id="job-already-off", enabled=False)

    mock_service = MagicMock()
    mock_service.update_job = MagicMock()
    mock_service.add_job = MagicMock()

    with patch.object(gateway_server, "_cron_service", mock_service), patch.object(
        gateway_server, "_find_cron_job_by_system_job", return_value=existing
    ):
        result = gateway_server._register_system_cron_job(**_kwargs(enabled=False))

    assert result is None
    mock_service.update_job.assert_not_called()
    mock_service.add_job.assert_not_called()


def test_no_cron_service_returns_none_even_when_enabled_false() -> None:
    """Service unavailable short-circuit still takes precedence."""
    from universal_agent import gateway_server

    with patch.object(gateway_server, "_cron_service", None), patch.object(
        gateway_server, "_find_cron_job_by_system_job"
    ) as find_mock:
        result = gateway_server._register_system_cron_job(**_kwargs(enabled=False))

    assert result is None
    find_mock.assert_not_called()


def test_enabled_true_no_existing_row_calls_add_job() -> None:
    """Sanity: enabled=True path unchanged — new row goes through add_job."""
    from universal_agent import gateway_server

    added_dict = {"job_id": "new-job", "enabled": True}
    added_obj = SimpleNamespace(
        job_id="new-job",
        enabled=True,
        to_dict=MagicMock(return_value=added_dict),
    )

    mock_service = MagicMock()
    mock_service.add_job = MagicMock(return_value=added_obj)
    mock_service.update_job = MagicMock()

    with patch.object(gateway_server, "_cron_service", mock_service), patch.object(
        gateway_server, "_find_cron_job_by_system_job", return_value=None
    ):
        result = gateway_server._register_system_cron_job(**_kwargs(enabled=True))

    mock_service.add_job.assert_called_once()
    add_kwargs = mock_service.add_job.call_args.kwargs
    assert add_kwargs["enabled"] is True
    mock_service.update_job.assert_not_called()
    assert result == added_dict


def test_enabled_true_existing_row_calls_update_job_with_enabled_true() -> None:
    """Sanity: enabled=True + existing row → update_job upserts with enabled=True
    (preserving the original idempotent-upsert behavior, including re-enabling
    a row that had been disabled)."""
    from universal_agent import gateway_server

    existing = SimpleNamespace(job_id="job-existing", enabled=False)
    updated_dict = {"job_id": "job-existing", "enabled": True}
    updated_obj = SimpleNamespace(
        job_id="job-existing",
        enabled=True,
        to_dict=MagicMock(return_value=updated_dict),
    )

    mock_service = MagicMock()
    mock_service.update_job = MagicMock(return_value=updated_obj)
    mock_service.add_job = MagicMock()

    with patch.object(gateway_server, "_cron_service", mock_service), patch.object(
        gateway_server, "_find_cron_job_by_system_job", return_value=existing
    ):
        result = gateway_server._register_system_cron_job(**_kwargs(enabled=True))

    mock_service.update_job.assert_called_once()
    call_args = mock_service.update_job.call_args
    assert call_args.args[0] == "job-existing"
    assert call_args.args[1]["enabled"] is True
    mock_service.add_job.assert_not_called()
    assert result == updated_dict
