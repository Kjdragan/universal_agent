"""Guards against tutorial-build misroutes (e.g. news clips reaching CODIE).

Real-world failure that motivated these tests: a Kanal13 geopolitical news
clip "Israeli strikes hit southern Lebanon..." was auto-routed into the
tutorial-build lane because substring `"api"` matched inside an unrelated
word, and there was no category/channel/extraction-plan gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.services import proactive_tutorial_builds as ptb
from universal_agent.services.proactive_tutorial_builds import (
    _looks_build_oriented,
    record_channel_denylist_entry,
)


@pytest.fixture(autouse=True)
def _isolated_denylist(tmp_path, monkeypatch):
    """Point the denylist file at a tmp path for the test, restore after."""
    denylist = tmp_path / "tutorial_build_denylist.yaml"
    monkeypatch.setattr(ptb, "_DENYLIST_FILE", denylist)
    yield denylist


def _kanal13_subject() -> dict:
    return {
        "title": "Israeli strikes hit southern Lebanon areas despite ceasefire extension",
        "description": "Kanal13 news report on the latest geopolitical developments.",
        "channel_name": "Kanal13",
        "video_id": "uFWp5Dv8ggU",
        "url": "https://www.youtube.com/watch?v=uFWp5Dv8ggU",
    }


def _empty_analysis() -> dict:
    return {
        "language": "unknown",
        "dependencies": [],
        "implementation_steps": [],
    }


def test_news_category_blocks_route():
    """Hard block when CSI category indicates non-code content."""
    assert not _looks_build_oriented(
        subject=_kanal13_subject(),
        analysis={"language": "python", "dependencies": ["fastapi"], "implementation_steps": [{"step_number": 1}]},
        category="news",
        summary="Geopolitical news clip.",
    )


def test_empty_extraction_plan_blocks_route():
    """If CSI produced nothing to build, do not route — regardless of keywords."""
    subject = {
        "title": "Build a Python API tutorial",  # has positive tokens
        "description": "",
        "channel_name": "Some Channel",
    }
    assert not _looks_build_oriented(
        subject=subject,
        analysis=_empty_analysis(),
        category="education",
        summary="",
    )


def test_kanal13_substring_false_positive_no_longer_routes():
    """The original bug: substring 'api' inside 'rapid' / 'Apia' tripped the gate."""
    subject = {
        "title": "Israeli strikes hit southern Lebanon areas despite ceasefire extension",
        "description": "Rapid escalation, capital cities affected.",
        "channel_name": "Kanal13",
    }
    assert not _looks_build_oriented(
        subject=subject,
        analysis=_empty_analysis(),
        category="",  # even with no category signal, must not route
        summary="",
    )


def test_channel_denylist_blocks_route(_isolated_denylist):
    """Once a channel is denylisted, no subsequent video routes — even codey ones."""
    assert record_channel_denylist_entry("Kanal13", reason="not_code_tutorial")
    subject = {
        "title": "Build an MCP server in Python",  # would otherwise pass
        "description": "Walkthrough of an SDK tutorial.",
        "channel_name": "Kanal13",
    }
    analysis = {
        "language": "python",
        "dependencies": ["mcp"],
        "implementation_steps": [{"step_number": 1, "description": "scaffold"}],
    }
    assert not _looks_build_oriented(
        subject=subject,
        analysis=analysis,
        category="education",
        summary="",
    )


def test_record_denylist_is_idempotent(_isolated_denylist):
    assert record_channel_denylist_entry("ExampleChannel")
    assert record_channel_denylist_entry("examplechannel")  # case-insensitive dedup
    contents = Path(_isolated_denylist).read_text(encoding="utf-8")
    assert contents.lower().count("examplechannel") == 1


def test_negative_token_in_title_blocks_route():
    subject = {
        "title": "Drama reaction to the new Python API",
        "description": "",
        "channel_name": "Some Channel",
    }
    analysis = {
        "language": "python",
        "dependencies": ["x"],
        "implementation_steps": [{"step_number": 1}],
    }
    assert not _looks_build_oriented(
        subject=subject,
        analysis=analysis,
        category="entertainment",
        summary="",
    )


def test_positive_signal_must_be_in_title():
    """Description-only positive tokens are too weak — must appear in title."""
    subject = {
        "title": "My day in the city",
        "description": "Also I mentioned Python and Docker briefly.",
        "channel_name": "Vlogger",
    }
    analysis = {
        "language": "python",
        "dependencies": ["x"],
        "implementation_steps": [{"step_number": 1}],
    }
    assert not _looks_build_oriented(
        subject=subject,
        analysis=analysis,
        category="vlog",  # also category-blocked, but this asserts the title rule
        summary="",
    )


def test_happy_path_still_routes():
    """A genuine coding tutorial with a real plan and clean signals still routes."""
    subject = {
        "title": "Build an MCP server in Python — full tutorial",
        "description": "Step-by-step walkthrough.",
        "channel_name": "AI Builder",
    }
    analysis = {
        "language": "python",
        "dependencies": ["mcp", "fastapi"],
        "implementation_steps": [{"step_number": 1, "description": "scaffold"}],
    }
    assert _looks_build_oriented(
        subject=subject,
        analysis=analysis,
        category="education",
        summary="Builds an MCP server end-to-end.",
    )
