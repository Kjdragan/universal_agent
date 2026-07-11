"""Unit tests for utils/time_utils.py — shared now_iso / parse_iso helpers."""

from datetime import datetime, timezone

from universal_agent.utils.time_utils import now_iso, parse_iso


class TestNowIso:
    def test_returns_aware_utc_isoformat_string(self):
        result = now_iso()
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0

    def test_parseable_round_trip(self):
        result = now_iso()
        parsed = datetime.fromisoformat(result)
        assert isinstance(parsed, datetime)


class TestParseIso:
    def test_z_suffix(self):
        parsed = parse_iso("2026-07-11T12:00:00Z")
        assert parsed == datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)

    def test_plus_offset(self):
        parsed = parse_iso("2026-07-11T12:00:00+00:00")
        assert parsed == datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)

    def test_microseconds(self):
        parsed = parse_iso("2026-07-11T12:00:00.123456+00:00")
        assert parsed == datetime(2026, 7, 11, 12, 0, 0, 123456, tzinfo=timezone.utc)

    def test_naive_string(self):
        parsed = parse_iso("2026-07-11T12:00:00")
        assert parsed == datetime(2026, 7, 11, 12, 0, 0)
        assert parsed.tzinfo is None

    def test_none_returns_none(self):
        assert parse_iso(None) is None

    def test_empty_string_returns_none(self):
        assert parse_iso("") is None

    def test_garbage_returns_none(self):
        assert parse_iso("not-a-timestamp") is None

    def test_datetime_input_via_str_coercion(self):
        dt = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
        parsed = parse_iso(dt)
        assert parsed == dt


class TestMigratedAliasesShareIdentity:
    """A handful of migrated call sites should bind to the exact same
    function objects, not local re-definitions with matching behavior.
    """

    def test_task_hub_now_iso_is_shared(self):
        from universal_agent.task_hub import _now_iso

        assert _now_iso is now_iso

    def test_memory_models_now_iso_is_shared(self):
        from universal_agent.memory.memory_models import _now_iso

        assert _now_iso is now_iso

    def test_simone_chat_tasks_now_iso_and_parse_iso_are_shared(self):
        from universal_agent.services.simone_chat_tasks import _now_iso, _parse_iso

        assert _now_iso is now_iso
        assert _parse_iso is parse_iso

    def test_wiki_core_now_iso_is_shared(self):
        from universal_agent.wiki.core import _now_iso

        assert _now_iso is now_iso
