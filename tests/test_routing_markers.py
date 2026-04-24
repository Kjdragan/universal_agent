"""Tests for centralized routing_markers module."""

import pytest

from universal_agent.services.routing_markers import (
    CODE_WORKFLOW_RE,
    RESEARCH_WORKFLOW_RE,
    CSI_CODE_RE,
    CSI_RESEARCH_RE,
    CSI_WRITER_RE,
    CSI_AGENT_HINTS_RE,
    CSI_HUMAN_HINTS_RE,
)


class TestCodeWorkflowMarkers:
    """Word-boundary matching should reduce false positives."""

    def test_fix_matches(self):
        assert CODE_WORKFLOW_RE.search("please fix the bug")

    def test_fix_no_false_positive_on_suffix(self):
        # "suffix" contains "fix" as substring — should NOT match with word boundaries
        assert not CODE_WORKFLOW_RE.search("the suffix is wrong")

    def test_python_matches(self):
        assert CODE_WORKFLOW_RE.search("update the python code")

    def test_repository_matches(self):
        assert CODE_WORKFLOW_RE.search("push to the repository")

    def test_code_change_matches(self):
        assert CODE_WORKFLOW_RE.search("make a code change")

    def test_no_match_random(self):
        assert not CODE_WORKFLOW_RE.search("the weather is nice today")


class TestResearchWorkflowMarkers:
    def test_research_matches(self):
        assert RESEARCH_WORKFLOW_RE.search("do some research on AI")

    def test_report_no_false_positive_airport(self):
        # "airport" should NOT match "report" with word boundaries
        assert not RESEARCH_WORKFLOW_RE.search("going to the airport")

    def test_report_matches(self):
        assert RESEARCH_WORKFLOW_RE.search("generate a report")

    def test_analysis_matches(self):
        assert RESEARCH_WORKFLOW_RE.search("perform analysis of the data")

    def test_pdf_matches(self):
        assert RESEARCH_WORKFLOW_RE.search("save as pdf")


class TestCSISubtaskRole:
    def test_code_role(self):
        assert CSI_CODE_RE.search("install the new package")

    def test_code_role_fix(self):
        assert CSI_CODE_RE.search("fix the signature validation")

    def test_research_role(self):
        assert CSI_RESEARCH_RE.search("analyze the logs")

    def test_writer_role(self):
        assert CSI_WRITER_RE.search("write a summary")

    def test_no_false_positive_env_on_eleven(self):
        # "eleven" contains "env" — should NOT match with word boundaries
        assert not CSI_CODE_RE.search("there are eleven items")


class TestCSIHintMatching:
    def test_agent_hint_automation(self):
        assert CSI_AGENT_HINTS_RE.search("add cron automation")

    def test_human_hint_manual(self):
        assert CSI_HUMAN_HINTS_RE.search("requires manual approval")

    def test_agent_hint_no_false_positive(self):
        # "environment" should not match "env" with word boundaries
        # Actually "environment" does NOT have \benv\b since env is at start
        # \b matches at word boundary — "env" in "environment" would match
        # because \benv\b matches "env" as a whole word, not as substring.
        # "environment" starts with "env" but \b needs boundary AFTER "env" too
        assert not CSI_AGENT_HINTS_RE.search("check the environment variable")

    def test_human_hint_kevin(self):
        assert CSI_HUMAN_HINTS_RE.search("ask kevin for approval")

    def test_human_hint_sign_off(self):
        assert CSI_HUMAN_HINTS_RE.search("needs sign-off from stakeholder")
