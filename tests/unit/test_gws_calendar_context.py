from __future__ import annotations

import json
from types import SimpleNamespace

from universal_agent.services import gws_calendar_context


def test_today_calendar_context_returns_unavailable_without_gws(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _cmd: None)

    result = gws_calendar_context.today_calendar_context()

    assert result["ok"] is False
    assert result["reason"] == "gws_binary_not_found"
    assert result["events"] == []


def test_today_calendar_context_parses_gws_items(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _cmd: "/usr/bin/gws")

    def _run(cmd, capture_output, text, timeout):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "id": "evt-1",
                            "summary": "Planning review",
                            "start": {"dateTime": "2026-04-15T09:00:00-05:00"},
                            "htmlLink": "https://calendar.test/event",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", _run)

    result = gws_calendar_context.today_calendar_context()

    assert result["ok"] is True
    assert result["events"][0]["summary"] == "Planning review"
