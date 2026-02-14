import sys
from pathlib import Path


def _import_build_tool():
    repo_root = Path(__file__).resolve().parents[2]
    skill_scripts = repo_root / ".claude" / "skills" / "grok-x-trends" / "scripts"
    sys.path.insert(0, str(skill_scripts))
    from lib.xai_x_search import _build_x_search_tool  # type: ignore

    return _build_x_search_tool


def test_build_tool_includes_dates_and_media_flags():
    build = _import_build_tool()
    tool = build(
        from_date="2026-02-12",
        to_date="2026-02-13",
        enable_image_understanding=True,
        enable_video_understanding=True,
    )
    assert tool["type"] == "x_search"
    assert tool["from_date"] == "2026-02-12"
    assert tool["to_date"] == "2026-02-13"
    assert tool["enable_image_understanding"] is True
    assert tool["enable_video_understanding"] is True


def test_build_tool_allows_or_excludes_handles_but_not_both():
    build = _import_build_tool()
    tool = build(from_date="2026-02-12", to_date="2026-02-13", allowed_x_handles=["@a", "b"])
    assert tool["allowed_x_handles"] == ["a", "b"]
    assert "excluded_x_handles" not in tool

    tool2 = build(from_date="2026-02-12", to_date="2026-02-13", excluded_x_handles=["@x"])
    assert tool2["excluded_x_handles"] == ["x"]
    assert "allowed_x_handles" not in tool2

    try:
        build(from_date="2026-02-12", to_date="2026-02-13", allowed_x_handles=["a"], excluded_x_handles=["b"])
        assert False, "expected ValueError"
    except ValueError:
        pass

