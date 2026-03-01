"""Packet 17: tests for CSI follow-up contract v2."""

import time
from datetime import datetime, timezone

import pytest

from universal_agent.csi_followup_contract import (
    FollowUpRequest,
    FollowUpResponse,
    build_followup_request,
    build_followup_response,
    validate_followup_request,
    check_followup_timeout,
    check_followup_budget,
    MAX_FOLLOWUP_BUDGET,
    DEFAULT_FOLLOWUP_BUDGET,
    DEFAULT_TIMEOUT_SECONDS,
)


class TestFollowUpRequest:
    def test_defaults(self):
        req = FollowUpRequest(topic_key="rss_trend:ai")
        assert req.correlation_id.startswith("fu_")
        assert req.budget_remaining == DEFAULT_FOLLOWUP_BUDGET
        assert req.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert req.deadline_utc

    def test_budget_clamped_to_max(self):
        req = FollowUpRequest(topic_key="t", budget_remaining=100, budget_total=100)
        assert req.budget_remaining == MAX_FOLLOWUP_BUDGET
        assert req.budget_total == MAX_FOLLOWUP_BUDGET

    def test_to_dict(self):
        req = FollowUpRequest(topic_key="t")
        d = req.to_dict()
        assert isinstance(d, dict)
        assert d["topic_key"] == "t"
        assert "correlation_id" in d
        assert "deadline_utc" in d

    def test_parent_correlation_id(self):
        req = FollowUpRequest(topic_key="t", parent_correlation_id="fu_parent123")
        assert req.parent_correlation_id == "fu_parent123"


class TestFollowUpResponse:
    def test_defaults(self):
        resp = FollowUpResponse(correlation_id="fu_abc", topic_key="t", outcome="completed")
        assert resp.correlation_id == "fu_abc"
        assert resp.outcome == "completed"
        assert resp.completed_at

    def test_to_dict(self):
        resp = FollowUpResponse(
            correlation_id="fu_abc",
            topic_key="t",
            outcome="timeout",
            elapsed_seconds=3600.0,
        )
        d = resp.to_dict()
        assert d["outcome"] == "timeout"
        assert d["elapsed_seconds"] == 3600.0


class TestValidateRequest:
    def test_valid_minimal(self):
        ok, err = validate_followup_request({"topic_key": "rss_trend:ai"})
        assert ok
        assert err == ""

    def test_missing_topic_key(self):
        ok, err = validate_followup_request({})
        assert not ok
        assert "topic_key" in err

    def test_invalid_request_type(self):
        ok, err = validate_followup_request({
            "topic_key": "t",
            "request_type": "invalid_type",
        })
        assert not ok
        assert "request_type" in err

    def test_valid_request_types(self):
        for rt in ["targeted_followup", "full_reanalysis", "source_expansion"]:
            ok, err = validate_followup_request({"topic_key": "t", "request_type": rt})
            assert ok, f"Expected valid for {rt}: {err}"

    def test_budget_out_of_range(self):
        ok, err = validate_followup_request({"topic_key": "t", "budget_remaining": -1})
        assert not ok
        assert "budget_remaining" in err

        ok, err = validate_followup_request({"topic_key": "t", "budget_remaining": 999})
        assert not ok

    def test_timeout_out_of_range(self):
        ok, err = validate_followup_request({"topic_key": "t", "timeout_seconds": 10})
        assert not ok
        assert "timeout_seconds" in err

    def test_not_a_dict(self):
        ok, err = validate_followup_request("not_a_dict")
        assert not ok


class TestBuildFollowupRequest:
    def test_basic(self):
        req = build_followup_request(
            topic_key="rss_trend:ai",
            reason="Low confidence, need more sources",
        )
        assert req.topic_key == "rss_trend:ai"
        assert req.reason == "Low confidence, need more sources"
        assert req.correlation_id.startswith("fu_")
        assert req.budget_remaining <= MAX_FOLLOWUP_BUDGET

    def test_budget_clamped(self):
        req = build_followup_request(
            topic_key="t",
            reason="r",
            budget_remaining=50,
        )
        assert req.budget_remaining == MAX_FOLLOWUP_BUDGET

    def test_timeout_clamped(self):
        req = build_followup_request(
            topic_key="t",
            reason="r",
            timeout_seconds=10,
        )
        assert req.timeout_seconds == 60  # minimum


class TestBuildFollowupResponse:
    def test_completed(self):
        resp = build_followup_response(
            correlation_id="fu_abc",
            topic_key="rss_trend:ai",
            outcome="completed",
            quality_score=0.85,
            quality_grade="A",
            budget_consumed=1,
            budget_remaining=2,
        )
        assert resp.outcome == "completed"
        assert resp.quality_score == 0.85
        assert resp.budget_consumed == 1

    def test_timeout(self):
        resp = build_followup_response(
            correlation_id="fu_abc",
            topic_key="t",
            outcome="timeout",
            elapsed_seconds=3600.0,
            error_detail="Exceeded 1h deadline",
        )
        assert resp.outcome == "timeout"
        assert resp.error_detail == "Exceeded 1h deadline"


class TestTimeoutAndBudget:
    def test_not_timed_out(self):
        req = build_followup_request(
            topic_key="t",
            reason="r",
            timeout_seconds=3600,
        )
        assert not check_followup_timeout(req)

    def test_timed_out(self):
        req = FollowUpRequest(topic_key="t")
        past = datetime.fromtimestamp(time.time() - 10, tz=timezone.utc).isoformat()
        req.deadline_utc = past
        assert check_followup_timeout(req)

    def test_budget_not_exhausted(self):
        req = FollowUpRequest(topic_key="t", budget_remaining=2)
        assert not check_followup_budget(req)

    def test_budget_exhausted(self):
        req = FollowUpRequest(topic_key="t", budget_remaining=0)
        assert check_followup_budget(req)


class TestCorrelationIdChain:
    """Contract: every follow-up request/response pair shares a correlation_id."""
    def test_request_response_correlation(self):
        req = build_followup_request(topic_key="t", reason="r")
        resp = build_followup_response(
            correlation_id=req.correlation_id,
            topic_key=req.topic_key,
            outcome="completed",
            budget_consumed=1,
            budget_remaining=req.budget_remaining - 1,
        )
        assert resp.correlation_id == req.correlation_id
        assert resp.topic_key == req.topic_key

    def test_chained_followups_share_parent(self):
        req1 = build_followup_request(topic_key="t", reason="initial")
        req2 = build_followup_request(
            topic_key="t",
            reason="refinement",
            parent_correlation_id=req1.correlation_id,
        )
        assert req2.parent_correlation_id == req1.correlation_id
        assert req2.correlation_id != req1.correlation_id
