import json
from pathlib import Path
import sys


def _import_skill_lib():
    # Import the skill lib from the repo without requiring it to be an installed package.
    repo_root = Path(__file__).resolve().parents[2]
    skill_scripts = repo_root / ".claude" / "skills" / "grok-x-trends" / "scripts"
    sys.path.insert(0, str(skill_scripts))
    from lib.xai_x_search import parse_trends_response  # type: ignore

    return parse_trends_response


def test_parse_trends_response_prefers_full_json_object():
    parse_trends_response = _import_skill_lib()
    fixture = Path("tests/fixtures/grok_x_trends/response_json_object_ok.json").read_text(encoding="utf-8")
    resp = json.loads(fixture)

    parsed = parse_trends_response(resp)
    assert len(parsed["themes"]) == 1
    assert len(parsed["posts"]) == 2
    assert parsed["posts"][0]["url"].startswith("https://x.com/")


def test_parse_trends_response_fallback_embedded_json_and_cleans_invalid_date():
    parse_trends_response = _import_skill_lib()
    fixture = Path("tests/fixtures/grok_x_trends/response_text_with_json_embedded.json").read_text(encoding="utf-8")
    resp = json.loads(fixture)

    parsed = parse_trends_response(resp)
    assert parsed["themes"] == []
    assert len(parsed["posts"]) == 1
    # invalid date should be normalized to null/None
    assert parsed["posts"][0]["date"] is None

