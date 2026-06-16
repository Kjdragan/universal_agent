"""Regression tests for the paper_to_podcast_daily turn-budget + poll-window fix.

Background (RCA ``RCA_paper_to_podcast.md``, 2026-06-16): the cron reliably
reached the audio studio-create step then ended mid-poll on slow ``deep_dive``
audio days, orphaning the ``.m4a`` download. Two coupled defects:

  * The audio-poll instruction was vague, so the agent's own poll loop
    (~19.5 min) exited while audio was still ``in_progress``.
  * The per-job agentic-loop turn cap was the engine default
    (``EngineConfig.max_iterations`` = 20), which the full pipeline
    (search + ingest + notebook + 3x studio_create + poll + download + email)
    exhausts before the download turn.

The fix (this PR) makes the poll instruction explicit AND gives the job a
larger, job-scoped turn budget via a generic ``max_turns`` request-metadata
override honored by ``gateway._resolve_max_turns_override``.
"""

from __future__ import annotations

from types import SimpleNamespace

from universal_agent import gateway_server
from universal_agent.execution_engine import EngineConfig
from universal_agent.gateway import (
    GatewayRequest,
    InProcessGateway,
    _resolve_max_turns_override,
)

# ── Fix 1: the command body must carry an explicit, long audio-poll window ──

def test_command_includes_long_audio_poll_window():
    """The poll instruction must (a) name a 40-minute ceiling, (b) forbid
    ending the run while audio is still in_progress, and (c) require a re-poll
    if a background poll ends incomplete. The old one-liner ("Poll ... until
    the audio is completed") left the agent free to cap the poll under 30 min
    and terminate on slow-audio days."""
    body = gateway_server._paper_to_podcast_command()
    assert "UP TO 40 MINUTES" in body, "command must name the 40-minute ceiling"
    assert "DO NOT cap the poll under 30 minutes" in body
    assert "DO NOT end the run while audio is still `in_progress`" in body, (
        "command must forbid terminating mid-poll"
    )
    assert "start another poll loop rather than terminating" in body, (
        "command must require a re-poll when a background poll ends incomplete"
    )
    assert "sleep 30" in body


def test_command_still_requires_real_m4a_download():
    """Guard against the fix accidentally dropping the 'real .m4a' requirement
    (the headline deliverable). Fabricated audio / a text transcript substitute
    is a hard failure, not a success."""
    body = gateway_server._paper_to_podcast_command()
    assert "real .m4a" in body


# ── Fix 2a: the request-metadata → max_iterations resolver ──────────────────

def test_resolve_max_turns_override_valid_values():
    assert _resolve_max_turns_override({"max_turns": 30}) == 30
    assert _resolve_max_turns_override({"max_turns": "28"}) == 28  # str coerced
    assert _resolve_max_turns_override({"max_turns": 1}) == 1


def test_resolve_max_turns_override_invalid_or_absent_returns_none():
    """Absent / non-positive / garbage must return None so the engine default
    (20) is preserved — never crash a run on a bad override."""
    assert _resolve_max_turns_override({}) is None
    assert _resolve_max_turns_override({"max_turns": None}) is None
    assert _resolve_max_turns_override({"max_turns": 0}) is None
    assert _resolve_max_turns_override({"max_turns": -5}) is None
    assert _resolve_max_turns_override({"max_turns": "not-a-number"}) is None


# ── Fix 2b: the job-scoped UA_PAPER_TO_PODCAST_MAX_TURNS resolver ───────────

def test_paper_to_podcast_max_turns_default_is_above_engine_default(monkeypatch):
    """Default (30) must exceed the engine default (20) — that's the whole
    point of the override; the pipeline needs more than 20 turns on slow days."""
    monkeypatch.delenv("UA_PAPER_TO_PODCAST_MAX_TURNS", raising=False)
    assert gateway_server._paper_to_podcast_max_turns() == 30
    assert gateway_server._paper_to_podcast_max_turns() > 20


def test_paper_to_podcast_max_turns_honors_env(monkeypatch):
    monkeypatch.setenv("UA_PAPER_TO_PODCAST_MAX_TURNS", "40")
    assert gateway_server._paper_to_podcast_max_turns() == 40


def test_paper_to_podcast_max_turns_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("UA_PAPER_TO_PODCAST_MAX_TURNS", "forty")
    assert gateway_server._paper_to_podcast_max_turns() == 30


def test_paper_to_podcast_max_turns_non_positive_falls_back(monkeypatch):
    """A non-positive override would disable the loop entirely — fall back
    rather than let a misconfigured env wedge the cron."""
    monkeypatch.setenv("UA_PAPER_TO_PODCAST_MAX_TURNS", "0")
    assert gateway_server._paper_to_podcast_max_turns() == 30


# ── Fix 2c: the override actually reaches adapter.config.max_iterations ─────

class _AdapterStub:
    """Minimal stand-in for ProcessTurnAdapter: _apply_request_config only
    touches adapter.config (force_complex, extra_disallowed_tools,
    max_iterations, and a few __dict__ bookkeeping keys)."""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config


def _apply(request_metadata: dict) -> EngineConfig:
    config = EngineConfig(workspace_dir="/tmp/ws")  # default max_iterations == 20
    adapter = _AdapterStub(config)
    request = GatewayRequest(user_input="x", metadata=request_metadata)
    # _apply_request_config never reads `self`, so a bare object() suffices.
    InProcessGateway._apply_request_config(object(), adapter, request)
    return adapter.config


def test_apply_request_config_honors_max_turns_override():
    """The load-bearing assertion: a max_turns override in request metadata
    must set adapter.config.max_iterations — which is what process_turn loops
    against as the hard agentic turn cap."""
    config = _apply({"max_turns": 30})
    assert config.max_iterations == 30


def test_apply_request_config_preserves_default_without_override():
    """No override → engine default (20) untouched. Guards against a regression
    that bumped every session's turn cap."""
    config = _apply({})
    assert config.max_iterations == 20


def test_apply_request_config_ignores_invalid_override():
    """A garbage override must NOT corrupt max_iterations — fall back to default."""
    config = _apply({"max_turns": "garbage"})
    assert config.max_iterations == 20


# ── Fix 2d: the cron job's metadata carries the override (end-to-end stamp) ─

class _CronStub:
    def __init__(self) -> None:
        self.jobs: list[SimpleNamespace] = []

    def list_jobs(self) -> list[SimpleNamespace]:
        return list(self.jobs)

    def add_job(self, **kw) -> SimpleNamespace:
        job = SimpleNamespace(
            job_id=f"new_{len(self.jobs)}", to_dict=lambda: {"job_id": "new"}, **kw
        )
        self.jobs.append(job)
        return job

    def update_job(self, job_id: str, updates: dict) -> SimpleNamespace:
        return SimpleNamespace(job_id=job_id, to_dict=lambda: {"job_id": job_id, **updates})


def test_ensure_paper_to_podcast_job_stamps_max_turns_metadata(monkeypatch):
    """When the job is registered fresh (no existing row), its metadata must
    carry a max_turns >= the engine default — this is what cron_service._run_job
    plumbs into request_metadata and what _apply_request_config honors."""
    stub = _CronStub()
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.setenv("UA_PAPER_TO_PODCAST_ENABLED", "1")
    monkeypatch.delenv("UA_PAPER_TO_PODCAST_MAX_TURNS", raising=False)

    result = gateway_server._ensure_paper_to_podcast_cron_job()
    assert result is not None
    assert len(stub.jobs) == 1
    metadata = stub.jobs[0].metadata
    assert "max_turns" in metadata, "job metadata must carry the turn-budget override"
    assert int(metadata["max_turns"]) >= 20
    assert int(metadata["max_turns"]) == 30  # default


def test_ensure_paper_to_podcast_job_env_flows_into_metadata(monkeypatch):
    """An operator UA_PAPER_TO_PODCAST_MAX_TURNS override must reach the job's
    metadata without a code change (so tuning doesn't need a redeploy)."""
    stub = _CronStub()
    monkeypatch.setattr(gateway_server, "_cron_service", stub)
    monkeypatch.setenv("UA_PAPER_TO_PODCAST_ENABLED", "1")
    monkeypatch.setenv("UA_PAPER_TO_PODCAST_MAX_TURNS", "42")

    gateway_server._ensure_paper_to_podcast_cron_job()
    assert int(stub.jobs[0].metadata["max_turns"]) == 42
