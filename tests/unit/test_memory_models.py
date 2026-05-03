"""Unit tests for universal_agent.memory.memory_models."""

from __future__ import annotations

import uuid

from universal_agent.memory.memory_models import MemoryEntry


class TestMemoryEntryCreation:
    def test_defaults_populated(self) -> None:
        entry = MemoryEntry(content="hello")
        assert entry.content == "hello"
        assert entry.source == "unknown"
        assert entry.session_id is None
        assert entry.summary is None
        assert entry.tags == []
        assert uuid.UUID(entry.entry_id)
        assert "T" in entry.timestamp

    def test_all_fields_set(self) -> None:
        entry = MemoryEntry(
            content="body",
            entry_id="fixed-id",
            timestamp="2026-01-01T00:00:00+00:00",
            source="test",
            session_id="sess-1",
            tags=["a", "b"],
            summary="short",
        )
        assert entry.entry_id == "fixed-id"
        assert entry.tags == ["a", "b"]
        assert entry.summary == "short"


class TestMemoryEntryToDict:
    def test_roundtrip_keys(self) -> None:
        entry = MemoryEntry(
            content="body",
            entry_id="id-1",
            source="test",
            tags=["x"],
        )
        d = entry.to_dict()
        assert d["content"] == "body"
        assert d["entry_id"] == "id-1"
        assert d["source"] == "test"
        assert d["tags"] == ["x"]
        assert d["session_id"] is None
        assert d["summary"] is None

    def test_tags_is_copy(self) -> None:
        entry = MemoryEntry(content="c", tags=["original"])
        d = entry.to_dict()
        d["tags"].append("extra")
        assert entry.tags == ["original"]


class TestMemoryEntryFromDict:
    def test_roundtrip_to_dict_from_dict(self) -> None:
        original = MemoryEntry(
            content="hello world",
            source="unit-test",
            session_id="s-123",
            tags=["roundtrip"],
            summary="brief",
        )
        restored = MemoryEntry.from_dict(original.to_dict())
        assert restored.content == original.content
        assert restored.entry_id == original.entry_id
        assert restored.source == original.source
        assert restored.session_id == original.session_id
        assert restored.tags == original.tags
        assert restored.summary == original.summary

    def test_missing_fields_get_defaults(self) -> None:
        entry = MemoryEntry.from_dict({"content": "minimal"})
        assert entry.content == "minimal"
        assert uuid.UUID(entry.entry_id)
        assert entry.source == "unknown"
        assert entry.session_id is None
        assert entry.tags == []
        assert entry.summary is None

    def test_empty_dict_uses_defaults(self) -> None:
        entry = MemoryEntry.from_dict({})
        assert entry.content == ""

    def test_none_tags_become_empty_list(self) -> None:
        entry = MemoryEntry.from_dict({"content": "c", "tags": None})
        assert entry.tags == []

    def test_none_entry_id_generates_uuid(self) -> None:
        entry = MemoryEntry.from_dict({"content": "c", "entry_id": None})
        assert uuid.UUID(entry.entry_id)

    def test_preserves_existing_id(self) -> None:
        entry = MemoryEntry.from_dict({"content": "c", "entry_id": "kept"})
        assert entry.entry_id == "kept"
