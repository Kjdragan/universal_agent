"""Tests for priority_classifier -- deterministic email and task classification.

Covers classify_email_priority, classify_task_priority, and the
DispatchDecision / TaskPriority data structures.  No DB, no network, no
mocking required.
"""

from __future__ import annotations

import dataclasses

import pytest

from universal_agent.services.priority_classifier import (
    DispatchDecision,
    TaskPriority,
    classify_email_priority,
    classify_task_priority,
)


# -- Datastructure helpers ---------------------------------------------------

class TestTaskPriority:
    def test_enum_values(self) -> None:
        assert TaskPriority.P0_IMMEDIATE.value == "p0"
        assert TaskPriority.P1_SOON.value == "p1"
        assert TaskPriority.P2_SCHEDULED.value == "p2"
        assert TaskPriority.P3_BACKGROUND.value == "p3"

    def test_str_enum_identity(self) -> None:
        """TaskPriority is a str Enum -- comparing against the raw value works."""
        assert TaskPriority.P0_IMMEDIATE == "p0"
        assert TaskPriority.P3_BACKGROUND == "p3"


class TestDispatchDecision:
    def test_frozen(self) -> None:
        dd = DispatchDecision(
            priority=TaskPriority.P0_IMMEDIATE,
            strategy="immediate",
            max_wait_seconds=60,
            reason="test",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            dd.reason = "mutated"  # type: ignore[misc]

    def test_max_wait_seconds_is_positive(self) -> None:
        """Every mapped max_wait_seconds must be a positive int."""
        from universal_agent.services.priority_classifier import _STRATEGY_MAP

        for _prio, (strategy, max_wait) in _STRATEGY_MAP.items():
            assert isinstance(max_wait, int) and max_wait > 0


# -- classify_email_priority -------------------------------------------------

class TestClassifyEmailPriority:
    """Exercise every branch in classify_email_priority."""

    # --- Untrusted sender ---------------------------------------------------

    def test_untrusted_sender_is_p1(self) -> None:
        """Untrusted sender always routes to P1 (human security review)."""
        dd = classify_email_priority(
            sender_trusted=False,
            is_reply=False,
            subject="Hello",
            body_snippet="Just checking in",
        )
        assert dd.priority == TaskPriority.P1_SOON
        assert dd.reason == "untrusted_sender_security_review"

    def test_untrusted_sender_ignores_urgency_keywords(self) -> None:
        """Even urgent keywords from an untrusted sender stay at P1."""
        dd = classify_email_priority(
            sender_trusted=False,
            is_reply=False,
            subject="URGENT help needed",
            body_snippet="critical emergency",
        )
        assert dd.priority == TaskPriority.P1_SOON

    # --- Trusted sender defaults --------------------------------------------

    def test_trusted_sender_default_is_p1(self) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
        )
        assert dd.priority == TaskPriority.P1_SOON
        assert dd.reason == "operator_default"

    # --- Urgency keywords ---------------------------------------------------

    @pytest.mark.parametrize("keyword", ["urgent", "asap", "critical"])
    def test_urgency_keyword_in_subject(self, keyword: str) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
            subject=f"This is {keyword}",
        )
        assert dd.priority == TaskPriority.P0_IMMEDIATE
        assert dd.reason == "urgency_keyword_detected"

    @pytest.mark.parametrize("keyword", ["urgent", "asap", "critical"])
    def test_urgency_keyword_in_body(self, keyword: str) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
            body_snippet=f"Please handle this {keyword} thing",
        )
        assert dd.priority == TaskPriority.P0_IMMEDIATE

    # --- Deferral keywords --------------------------------------------------

    def test_deferral_overrides_urgency(self) -> None:
        """Deferral keywords take precedence over urgency keywords."""
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
            subject="urgent item",
            body_snippet="but no rush, handle later",
        )
        assert dd.priority == TaskPriority.P2_SCHEDULED
        assert dd.reason == "deferral_keyword_detected"

    def test_deferral_keyword_no_rush(self) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
            body_snippet="no rush on this",
        )
        assert dd.priority == TaskPriority.P2_SCHEDULED

    # --- Trusted reply to active thread -------------------------------------

    def test_trusted_reply_active_thread_p0(self) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=True,
            thread_message_count=3,
        )
        assert dd.priority == TaskPriority.P0_IMMEDIATE
        assert dd.reason == "operator_reply_to_active_thread"

    def test_trusted_reply_single_message_not_p0(self) -> None:
        """A reply to a single-message thread (count == 1) is NOT P0."""
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=True,
            thread_message_count=1,
        )
        assert dd.priority != TaskPriority.P0_IMMEDIATE

    # --- Classification-based routing ---------------------------------------

    def test_instruction_classification_p0(self) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
            classification="instruction",
        )
        assert dd.priority == TaskPriority.P0_IMMEDIATE
        assert dd.reason == "operator_instruction"

    def test_feedback_approval_p1(self) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
            classification="feedback_approval",
        )
        assert dd.priority == TaskPriority.P1_SOON
        assert "feedback_approval" in dd.reason

    def test_feedback_correction_p1(self) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
            classification="feedback_correction",
        )
        assert dd.priority == TaskPriority.P1_SOON
        assert "feedback_correction" in dd.reason

    def test_status_update_p2(self) -> None:
        dd = classify_email_priority(
            sender_trusted=True,
            is_reply=False,
            classification="status_update",
        )
        assert dd.priority == TaskPriority.P2_SCHEDULED
        assert dd.reason == "operator_status_update"


# -- classify_task_priority --------------------------------------------------

class TestClassifyTaskPriority:
    """Exercise every branch in classify_task_priority."""

    # --- must_complete ------------------------------------------------------

    def test_must_complete_flag(self) -> None:
        dd = classify_task_priority(must_complete=True)
        assert dd.priority == TaskPriority.P0_IMMEDIATE
        assert dd.reason == "must_complete_flag"

    def test_must_complete_label(self) -> None:
        dd = classify_task_priority(labels=["must-complete"])
        assert dd.priority == TaskPriority.P0_IMMEDIATE

    # --- email-derived tasks ------------------------------------------------

    def test_email_source_kind(self) -> None:
        dd = classify_task_priority(source_kind="email")
        assert dd.priority == TaskPriority.P1_SOON
        assert dd.reason == "email_derived_task"

    def test_email_task_label(self) -> None:
        dd = classify_task_priority(labels=["email-task"])
        assert dd.priority == TaskPriority.P1_SOON

    # --- due dates ----------------------------------------------------------

    def test_due_today(self) -> None:
        dd = classify_task_priority(is_due_today=True)
        assert dd.priority == TaskPriority.P1_SOON
        assert dd.reason == "due_today"

    def test_has_due_date(self) -> None:
        dd = classify_task_priority(has_due_date=True)
        assert dd.priority == TaskPriority.P2_SCHEDULED
        assert dd.reason == "has_due_date"

    # --- background labels --------------------------------------------------

    def test_brainstorm_label(self) -> None:
        dd = classify_task_priority(labels=["brainstorm"])
        assert dd.priority == TaskPriority.P3_BACKGROUND

    def test_heartbeat_candidate_label(self) -> None:
        dd = classify_task_priority(labels=["heartbeat-candidate"])
        assert dd.priority == TaskPriority.P3_BACKGROUND

    # --- default ------------------------------------------------------------

    def test_default_is_p2(self) -> None:
        dd = classify_task_priority()
        assert dd.priority == TaskPriority.P2_SCHEDULED
        assert dd.reason == "default"

    # --- precedence ---------------------------------------------------------

    def test_must_complete_takes_precedence_over_email(self) -> None:
        dd = classify_task_priority(must_complete=True, source_kind="email")
        assert dd.priority == TaskPriority.P0_IMMEDIATE

    def test_due_today_takes_precedence_over_has_due_date(self) -> None:
        dd = classify_task_priority(is_due_today=True, has_due_date=True)
        assert dd.priority == TaskPriority.P1_SOON
        assert dd.reason == "due_today"

    # --- edge cases ---------------------------------------------------------

    def test_none_labels_handled(self) -> None:
        dd = classify_task_priority(labels=None)
        assert dd.priority == TaskPriority.P2_SCHEDULED

    def test_empty_labels_handled(self) -> None:
        dd = classify_task_priority(labels=[])
        assert dd.priority == TaskPriority.P2_SCHEDULED
