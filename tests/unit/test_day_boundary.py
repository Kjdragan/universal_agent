"""Unit tests for ``universal_agent.utils.day_boundary``.

``chicago_day_start_iso`` is the single source of truth for the operator's
daily build-volume boundary. Three proactive demo ceilings (the normal-flow
build cap, the auto-route inflow ceiling, and the 23:50 America/Chicago
end-of-day golden-nuggets cap) count "today" against the same Houston-local
midnight. A UTC boundary would undercount the Chicago day (UTC has already
rolled over) and could let a ceiling be exceeded, so the boundary is
deliberately local. These tests pin that contract: the output is a
zero-offset UTC ISO string (lexicographically comparable against the
``datetime.now(timezone.utc).isoformat()`` stamps task_hub writes), always
falls on or before now, and resolves to the current Chicago calendar day at
00:00 local. A deterministic clock-patched conversion check pins the
``replace``/``astimezone`` chain exactly (including DST) so a regression in
the conversion math is caught here rather than in a mis-counted ceiling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from universal_agent.utils import day_boundary
from universal_agent.utils.day_boundary import chicago_day_start_iso

_CHICAGO = ZoneInfo("America/Chicago")


class TestChicagoDayStartIsoProperties:
    def test_returns_utc_iso_string_with_plus_zero_offset(self):
        result = chicago_day_start_iso()
        # task_hub stamps are datetime.now(timezone.utc).isoformat(), i.e. a
        # fixed-width "+00:00" string; the boundary must share that shape so
        # the lexicographic comparison the ceilings rely on is valid.
        assert result.endswith("+00:00")
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0

    def test_result_is_chicago_midnight_of_current_local_day(self):
        parsed = datetime.fromisoformat(chicago_day_start_iso()).astimezone(_CHICAGO)
        now_chicago = datetime.now(_CHICAGO)
        # Same Houston calendar day, exactly at local midnight.
        assert parsed.date() == now_chicago.date()
        assert (parsed.hour, parsed.minute, parsed.second, parsed.microsecond) == (
            0,
            0,
            0,
            0,
        )

    def test_result_is_at_or_before_now(self):
        # Today's local midnight has already happened, so the boundary is
        # always in the past relative to a row stamped "now".
        result = chicago_day_start_iso()
        now_iso = datetime.now(timezone.utc).isoformat()
        assert result <= now_iso

    def test_dst_offset_is_five_or_six_hours_west_of_utc(self):
        # Chicago is UTC-5 (CDT) or UTC-6 (CST); midnight Chicago therefore
        # lands at 05:00 or 06:00 UTC. Pins that ZoneInfo DST handling is in
        # play rather than a hardcoded offset.
        parsed = datetime.fromisoformat(chicago_day_start_iso())
        assert parsed.hour in {5, 6}

    def test_two_calls_share_the_same_chicago_calendar_day(self):
        # Two successive calls must not straddle a day boundary. A regression
        # that returned e.g. UTC midnight could flip the date near midnight
        # Houston. Asserting calendar-day equality (not string equality) keeps
        # the test stable at the midnight edge.
        a = datetime.fromisoformat(chicago_day_start_iso()).astimezone(_CHICAGO)
        b = datetime.fromisoformat(chicago_day_start_iso()).astimezone(_CHICAGO)
        assert a.date() == b.date()


class TestFixedClockConversion:
    """Patch ``datetime.now`` to a fixed instant to pin the exact output."""

    @staticmethod
    def _freeze(monkeypatch, fixed_local):
        class FakeDateTime(datetime):
            __slots__ = ()

            @classmethod
            def now(cls, tz=None):
                return fixed_local.astimezone(tz) if tz is not None else fixed_local

        monkeypatch.setattr(day_boundary, "datetime", FakeDateTime)

    def test_summer_instant_uses_cdt_offset(self, monkeypatch):
        # 2026-07-12 02:30 Chicago (CDT, UTC-5): local midnight is
        # 2026-07-12 00:00 -05:00 -> 2026-07-12T05:00:00+00:00.
        self._freeze(monkeypatch, datetime(2026, 7, 12, 2, 30, 0, tzinfo=_CHICAGO))
        assert chicago_day_start_iso() == "2026-07-12T05:00:00+00:00"

    def test_winter_instant_uses_cst_offset(self, monkeypatch):
        # 2026-01-15 09:00 Chicago (CST, UTC-6): local midnight is
        # 2026-01-15 00:00 -06:00 -> 2026-01-15T06:00:00+00:00.
        self._freeze(monkeypatch, datetime(2026, 1, 15, 9, 0, 0, tzinfo=_CHICAGO))
        assert chicago_day_start_iso() == "2026-01-15T06:00:00+00:00"
