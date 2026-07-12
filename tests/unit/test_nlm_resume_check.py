"""Unit tests for the deterministic .nlm_resume.json verdict."""
import json
import time

from universal_agent.services.nlm_resume_check import MAX_AGE_SECONDS, verdict


def _write(tmp_path, payload) -> None:
    (tmp_path / ".nlm_resume.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )


def test_missing_checkpoint_is_fresh(tmp_path):
    assert verdict(tmp_path).startswith("FRESH: no .nlm_resume.json")


def test_corrupt_checkpoint_is_fresh(tmp_path):
    _write(tmp_path, "{not json")
    assert "unreadable" in verdict(tmp_path)


def test_done_checkpoint_is_fresh(tmp_path):
    _write(tmp_path, {"notebook_id": "nb1", "topic": "t", "run_started_at": time.time(), "status": "done"})
    assert "status=done" in verdict(tmp_path)


def test_stale_checkpoint_is_fresh(tmp_path):
    now = time.time()
    _write(tmp_path, {"notebook_id": "nb1", "topic": "t", "run_started_at": now - MAX_AGE_SECONDS - 60, "status": "polling"})
    assert "stale" in verdict(tmp_path, now=now)


def test_inflight_checkpoint_resumes_with_notebook_and_topic(tmp_path):
    now = time.time()
    _write(tmp_path, {"notebook_id": "nb42", "topic": "federated learning", "run_started_at": now - 3600, "status": "polling"})
    out = verdict(tmp_path, now=now)
    assert out.startswith("RESUME: adopt notebook nb42")
    assert "federated learning" in out and "polling" in out
