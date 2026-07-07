"""Regression tests for the paper_to_podcast_daily topic-rotation fix.

Background (2026-07-07 Atlas investigation, mission
vp-mission-46cfdf9fde1d91e3234fd0f6): the podcast topic was frozen on
"Diffusion" for 2026-07-05/06/07 even though the cron fired daily. Root
cause: the topic is derived from datetime.now().timetuple().tm_yday and
was baked into the stored cron command string at ensure-time
(_ensure_paper_to_podcast_cron_job runs at gateway boot), so a long-lived
gateway process kept replaying the SAME topic until the next deploy. The
fix re-renders the command at dispatch time
(cron_service._resolve_dispatch_command) so tm_yday resolves to the actual
run date.
"""

from __future__ import annotations

import datetime as _dt_module
from types import SimpleNamespace
from unittest.mock import patch

from universal_agent import cron_service, gateway_server


def _make_job(command: str) -> SimpleNamespace:
    return SimpleNamespace(
        job_id="cron_paper_to_podcast",
        command=command,
        metadata={"system_job": "paper_to_podcast_daily"},
    )


def _force_yday(yday: int):
    """Patch datetime.datetime.now() so timetuple().tm_yday == yday.

    _paper_to_podcast_command does `from datetime import datetime as _dt`
    then `_dt.now().timetuple().tm_yday`, so patching the real
    datetime.datetime.now classmethod is what reaches it.
    """
    real_dt_cls = _dt_module.datetime

    class _FakeDateTime(real_dt_cls):
        @classmethod
        def now(cls, tz=None):
            base = _dt_module.date(2024, 1, 1)
            return _dt_module.datetime.combine(
                _dt_module.date.fromordinal(base.toordinal() + (yday - 1)),
                _dt_module.time(2, 0, 0),
            )

    return patch.object(_dt_module, "datetime", _FakeDateTime)


def test_dispatch_command_rerenders_for_paper_to_podcast_daily(monkeypatch):
    """For the paper_to_podcast_daily system job, _resolve_dispatch_command
    must call _paper_to_podcast_command() fresh (so tm_yday resolves to the
    actual run date) rather than replaying the frozen stored command."""
    calls = []

    def fake_render() -> str:
        calls.append(True)
        return "RENDERED-AT-DISPATCH-TIME"

    monkeypatch.setattr(gateway_server, "_paper_to_podcast_command", fake_render)
    job = _make_job("FROZEN-AT-ENSURE-TIME")
    out = cron_service._resolve_dispatch_command(job, job.command)
    assert calls, "must re-render via _paper_to_podcast_command at dispatch time"
    assert out == "RENDERED-AT-DISPATCH-TIME"


def test_dispatch_command_passthrough_for_other_jobs():
    """Non-paper_to_podcast jobs must replay their stored command verbatim."""
    job = SimpleNamespace(
        job_id="some_other_cron",
        command="do the thing",
        metadata={"system_job": "youtube_daily_digest"},
    )
    out = cron_service._resolve_dispatch_command(job, job.command)
    assert out == "do the thing"


def test_dispatch_command_passthrough_when_no_system_job():
    """A job with no system_job metadata must replay verbatim (defensive)."""
    job = SimpleNamespace(job_id="ad_hoc", command="hello", metadata={})
    assert cron_service._resolve_dispatch_command(job, job.command) == "hello"
    job2 = SimpleNamespace(job_id="ad_hoc", command="hello", metadata=None)
    assert cron_service._resolve_dispatch_command(job2, job2.command) == "hello"


def test_command_rotates_topic_with_yday():
    """_paper_to_podcast_command() must embed the topic for the CURRENT
    yday, and two different ydays that map to different topic indices must
    produce different commands."""
    topics = list(gateway_server.PAPER_TO_PODCAST_TOPICS)
    assert len(topics) >= 2

    n = len(topics)
    yd_a = next(y for y in range(1, 367) if y % n == 0)
    yd_b = next(
        y
        for y in range(1, 367)
        if y % n != yd_a % n and topics[y % n] != topics[yd_a % n]
    )
    assert yd_a != yd_b

    with _force_yday(yd_a):
        cmd_a = gateway_server._paper_to_podcast_command()
    with _force_yday(yd_b):
        cmd_b = gateway_server._paper_to_podcast_command()

    topic_a = topics[yd_a % n]
    topic_b = topics[yd_b % n]
    assert topic_a != topic_b
    assert topic_a in cmd_a
    assert topic_b in cmd_b
    assert cmd_a != cmd_b


def test_two_runs_on_different_ydays_produce_different_topics():
    """The load-bearing regression assertion: a single registered
    paper_to_podcast job (with one frozen stored command) must dispatch
    DIFFERENT commands on two different ydays, because
    _resolve_dispatch_command re-renders against the real clock at dispatch
    time. This is the exact failure that kept the topic stuck on "Diffusion"
    across 2026-07-05/06/07."""
    topics = list(gateway_server.PAPER_TO_PODCAST_TOPICS)
    n = len(topics)
    assert n >= 2

    yd_a = next(y for y in range(1, 367) if y % n == 0)
    yd_b = next(y for y in range(1, 367) if y % n != yd_a % n)

    frozen_command = "FROZEN-AT-ENSURE-TIME"
    job = _make_job(frozen_command)

    with _force_yday(yd_a):
        cmd_a = cron_service._resolve_dispatch_command(job, frozen_command)
    with _force_yday(yd_b):
        cmd_b = cron_service._resolve_dispatch_command(job, frozen_command)

    assert cmd_a != cmd_b, (
        "same registered job must dispatch different commands on different ydays"
    )
    assert cmd_a != frozen_command, "must not replay the frozen stored command"
    assert cmd_b != frozen_command, "must not replay the frozen stored command"
    assert topics[yd_a % n] in cmd_a
    assert topics[yd_b % n] in cmd_b


def test_resolve_dispatch_command_falls_back_on_render_error(monkeypatch):
    """If the re-render raises, the helper must NEVER block dispatch -- it
    falls back to the stored command. A cron dispatching a stale topic is
    strictly better than a cron not dispatching at all."""
    def boom() -> str:
        raise RuntimeError("render exploded")

    monkeypatch.setattr(gateway_server, "_paper_to_podcast_command", boom)
    job = _make_job("FALLBACK-ME")
    out = cron_service._resolve_dispatch_command(job, job.command)
    assert out == "FALLBACK-ME"
