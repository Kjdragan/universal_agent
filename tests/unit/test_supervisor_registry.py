"""Unit tests for supervisors/registry.py."""
from __future__ import annotations

from universal_agent.supervisors.registry import (
    find_supervisor,
    supervisor_registry,
)


class TestSupervisorRegistry:
    def test_returns_list_of_dicts(self):
        result = supervisor_registry()
        assert isinstance(result, list)
        assert len(result) >= 2
        for row in result:
            assert isinstance(row, dict)
            assert "id" in row

    def test_returns_copies(self):
        first = supervisor_registry()
        second = supervisor_registry()
        assert first is not second
        assert first[0] is not second[0]

    def test_contains_factory_supervisor(self):
        ids = [row["id"] for row in supervisor_registry()]
        assert "factory-supervisor" in ids

    def test_contains_csi_supervisor(self):
        ids = [row["id"] for row in supervisor_registry()]
        assert "csi-supervisor" in ids


class TestFindSupervisor:
    def test_find_factory_supervisor(self):
        result = find_supervisor("factory-supervisor")
        assert result is not None
        assert result["id"] == "factory-supervisor"

    def test_find_csi_supervisor(self):
        result = find_supervisor("csi-supervisor")
        assert result is not None
        assert result["id"] == "csi-supervisor"

    def test_case_insensitive(self):
        result = find_supervisor("FACTORY-SUPERVISOR")
        assert result is not None
        assert result["id"] == "factory-supervisor"

    def test_whitespace_trimmed(self):
        result = find_supervisor("  factory-supervisor  ")
        assert result is not None
        assert result["id"] == "factory-supervisor"

    def test_unknown_returns_none(self):
        assert find_supervisor("nonexistent") is None

    def test_empty_returns_none(self):
        assert find_supervisor("") is None

    def test_none_returns_none(self):
        assert find_supervisor(None) is None

    def test_returns_copy(self):
        first = find_supervisor("factory-supervisor")
        second = find_supervisor("factory-supervisor")
        assert first is not second
