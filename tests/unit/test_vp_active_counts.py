"""Tests for _vp_active_counts in todo_dispatch_service.

Validates that agent classification uses the canonical agent_id field
rather than brittle substring matching on title/task_id.
"""

from __future__ import annotations

from universal_agent.services.todo_dispatch_service import _vp_active_counts


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------


class TestVpActiveCountsEmpty:
    def test_none_returns_zeros(self):
        assert _vp_active_counts(None) == (0, 0)

    def test_empty_list_returns_zeros(self):
        assert _vp_active_counts([]) == (0, 0)


# ---------------------------------------------------------------------------
# Exact canonical agent_id matching
# ---------------------------------------------------------------------------


class TestVpActiveCountsCanonical:
    def test_coder_primary(self):
        assignments = [{"agent_id": "vp.coder.primary"}]
        assert _vp_active_counts(assignments) == (1, 0)

    def test_general_primary(self):
        assignments = [{"agent_id": "vp.general.primary"}]
        assert _vp_active_counts(assignments) == (0, 1)

    def test_simone_not_counted(self):
        assignments = [{"agent_id": "simone"}]
        assert _vp_active_counts(assignments) == (0, 0)

    def test_multiple_same_type(self):
        assignments = [
            {"agent_id": "vp.coder.primary"},
            {"agent_id": "vp.coder.primary"},
        ]
        assert _vp_active_counts(assignments) == (2, 0)


# ---------------------------------------------------------------------------
# Alias matching (substring of agent_id only)
# ---------------------------------------------------------------------------


class TestVpActiveCountsAliases:
    def test_codie_alias(self):
        assignments = [{"agent_id": "codie"}]
        assert _vp_active_counts(assignments) == (1, 0)

    def test_atlas_alias(self):
        assignments = [{"agent_id": "atlas"}]
        assert _vp_active_counts(assignments) == (0, 1)

    def test_coder_substring_in_agent_id(self):
        """agent_id containing 'coder' as a substring should match."""
        assignments = [{"agent_id": "some-coder-session"}]
        assert _vp_active_counts(assignments) == (1, 0)


# ---------------------------------------------------------------------------
# No false positives from title/task_id
# ---------------------------------------------------------------------------


class TestVpActiveCountsNoFalsePositives:
    def test_coder_in_title_not_matched(self):
        """The word 'coder' in a task title should NOT classify as coder."""
        assignments = [
            {
                "agent_id": "simone",
                "title": "Encoder Design Review",
                "task_id": "encoder-review-001",
            }
        ]
        assert _vp_active_counts(assignments) == (0, 0)

    def test_atlas_in_title_not_matched(self):
        """The word 'atlas' in a task title should NOT classify as general."""
        assignments = [
            {
                "agent_id": "simone",
                "title": "Atlassian Integration Setup",
                "task_id": "atlassian-001",
            }
        ]
        assert _vp_active_counts(assignments) == (0, 0)

    def test_codie_in_task_id_not_matched(self):
        """A task_id containing 'codie' should NOT classify as coder
        when agent_id is simone."""
        assignments = [
            {
                "agent_id": "simone",
                "task_id": "codie-feedback-review",
            }
        ]
        assert _vp_active_counts(assignments) == (0, 0)

    def test_coder_in_provider_session_id_not_matched(self):
        """provider_session_id is no longer part of the classification."""
        assignments = [
            {
                "agent_id": "simone",
                "provider_session_id": "coder-workspace-abc",
            }
        ]
        assert _vp_active_counts(assignments) == (0, 0)


# ---------------------------------------------------------------------------
# Mixed assignments
# ---------------------------------------------------------------------------


class TestVpActiveCountsMixed:
    def test_one_of_each(self):
        assignments = [
            {"agent_id": "vp.coder.primary"},
            {"agent_id": "vp.general.primary"},
        ]
        assert _vp_active_counts(assignments) == (1, 1)

    def test_all_three_agent_types(self):
        assignments = [
            {"agent_id": "simone"},
            {"agent_id": "vp.coder.primary"},
            {"agent_id": "vp.general.primary"},
        ]
        assert _vp_active_counts(assignments) == (1, 1)

    def test_unknown_agent_id_ignored(self):
        assignments = [
            {"agent_id": "unknown-agent"},
            {"agent_id": ""},
        ]
        assert _vp_active_counts(assignments) == (0, 0)

    def test_missing_agent_id_key_ignored(self):
        """Assignment dicts without agent_id should not crash."""
        assignments = [
            {"title": "Some task"},
            {},
        ]
        assert _vp_active_counts(assignments) == (0, 0)
