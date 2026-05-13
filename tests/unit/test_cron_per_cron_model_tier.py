"""Per-cron model_tier + max_attempts metadata resolution.

Covers the two helpers added to ``CronService``:

- ``_force_complex_for_job`` — translates ``metadata.model_tier`` into
  the ``force_complex`` flag passed to ``GatewayRequest`` for LLM-prompt
  crons. Default behavior must match the pre-change hardcoded
  ``force_complex=True`` so any cron that hasn't opted in keeps running
  on Opus tier.

- ``_max_attempts_for_job`` — reads ``metadata.max_attempts`` for the
  workflow admission / retry-queue path. Default 3 matches the
  pre-change hardcoded value; a value of 1 disables retries entirely.

These tests exercise the helpers directly without going through the
full ``_run_job`` orchestration (which requires gateway / admission /
session mocking — out of scope here, in line with the pattern in
``test_cron_llm_path_f_observability.py``).
"""

from __future__ import annotations

import pytest

from universal_agent.cron_service import CronJob, CronService


def _make_job(metadata: dict | None = None) -> CronJob:
    return CronJob(
        job_id="test-job",
        user_id="cron",
        workspace_dir="/tmp/test-workspace",
        command="!script universal_agent.scripts.example",
        metadata=metadata or {},
    )


# ── _force_complex_for_job ──────────────────────────────────────────────────


class TestForceComplexForJob:
    def test_no_metadata_returns_true(self) -> None:
        """Pre-change default: missing metadata.model_tier → force_complex=True (Opus)."""
        job = _make_job(metadata={})
        assert CronService._force_complex_for_job(job) is True

    def test_empty_string_returns_true(self) -> None:
        job = _make_job(metadata={"model_tier": ""})
        assert CronService._force_complex_for_job(job) is True

    def test_explicit_high_returns_true(self) -> None:
        job = _make_job(metadata={"model_tier": "high"})
        assert CronService._force_complex_for_job(job) is True

    def test_opus_returns_true(self) -> None:
        job = _make_job(metadata={"model_tier": "opus"})
        assert CronService._force_complex_for_job(job) is True

    def test_low_returns_false(self) -> None:
        job = _make_job(metadata={"model_tier": "low"})
        assert CronService._force_complex_for_job(job) is False

    def test_sonnet_returns_false(self) -> None:
        job = _make_job(metadata={"model_tier": "sonnet"})
        assert CronService._force_complex_for_job(job) is False

    def test_haiku_returns_false(self) -> None:
        """Haiku tier requests Sonnet-or-lower via force_complex=False.

        True Haiku-tier routing (forcing claude-haiku-4-5 explicitly)
        would need an additional mechanism (model_override field or
        prompt directive). This change just opens the door — Haiku-tier
        crons no longer pay Opus prices.
        """
        job = _make_job(metadata={"model_tier": "haiku"})
        assert CronService._force_complex_for_job(job) is False

    def test_case_insensitive(self) -> None:
        for variant in ("LOW", "Low", "  low  ", "HAIKU"):
            job = _make_job(metadata={"model_tier": variant})
            assert CronService._force_complex_for_job(job) is False, variant

    def test_unknown_tier_falls_back_to_true(self) -> None:
        """A typo in metadata.model_tier must not silently downgrade.

        Defaulting unknown values to ``force_complex=True`` is the
        safer choice: a misspelled "opass" should keep running on
        Opus, not get silently routed to Sonnet.
        """
        job = _make_job(metadata={"model_tier": "opass"})
        assert CronService._force_complex_for_job(job) is True

    def test_none_metadata_attribute_returns_true(self) -> None:
        """Some code paths construct CronJob with metadata=None despite the dataclass default.

        The helper must not blow up on ``None``.
        """
        job = _make_job(metadata={})
        job.metadata = None  # type: ignore[assignment]
        assert CronService._force_complex_for_job(job) is True


# ── _max_attempts_for_job ────────────────────────────────────────────────────


class TestMaxAttemptsForJob:
    def test_no_metadata_returns_3(self) -> None:
        """Pre-change default: 3 attempts."""
        job = _make_job(metadata={})
        assert CronService._max_attempts_for_job(job) == 3

    def test_explicit_3(self) -> None:
        job = _make_job(metadata={"max_attempts": 3})
        assert CronService._max_attempts_for_job(job) == 3

    def test_explicit_1_disables_retries(self) -> None:
        """max_attempts=1 means the first dispatch is the only one.

        Used to silence the retry storm for crons whose failures are
        not recoverable by retry (e.g., hackernews_snapshot when the
        ``hackernews`` CLI is flaky on a 60s timeout).
        """
        job = _make_job(metadata={"max_attempts": 1})
        assert CronService._max_attempts_for_job(job) == 1

    def test_explicit_5(self) -> None:
        job = _make_job(metadata={"max_attempts": 5})
        assert CronService._max_attempts_for_job(job) == 5

    def test_string_coerced(self) -> None:
        """API callers often pass integer-valued strings — coerce them."""
        job = _make_job(metadata={"max_attempts": "2"})
        assert CronService._max_attempts_for_job(job) == 2

    def test_zero_clamped_to_one(self) -> None:
        """max_attempts=0 is nonsensical (no dispatch at all). Clamp to 1."""
        job = _make_job(metadata={"max_attempts": 0})
        assert CronService._max_attempts_for_job(job) == 1

    def test_negative_clamped_to_one(self) -> None:
        job = _make_job(metadata={"max_attempts": -7})
        assert CronService._max_attempts_for_job(job) == 1

    def test_garbage_falls_back_to_default(self) -> None:
        for value in ("abc", None, [3], {}, 2.5):
            job = _make_job(metadata={"max_attempts": value})
            assert CronService._max_attempts_for_job(job) == 3, value


# ── Integration sanity: dataclass default metadata is empty dict ─────────────


def test_cronjob_dataclass_default_metadata_works() -> None:
    """If someone forgets to pass metadata, both helpers must still work.

    Smoke test that the dataclass's default-factory `dict` lets the
    helpers see an empty mapping rather than crash on AttributeError.
    """
    job = CronJob(
        job_id="smoke",
        user_id="cron",
        workspace_dir="/tmp",
        command="!script foo",
    )
    assert CronService._force_complex_for_job(job) is True
    assert CronService._max_attempts_for_job(job) == 3
