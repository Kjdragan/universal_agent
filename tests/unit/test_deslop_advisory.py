"""Unit tests for the PURE helpers in scripts/deslop_advisory.py (no network).

Module import must not require `anthropic` — heavy imports are lazy inside the
script, so importing the module here loads only stdlib.
"""
import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "deslop_advisory.py"

_spec = importlib.util.spec_from_file_location("deslop_advisory", SCRIPT)
deslop_advisory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deslop_advisory)


def test_read_diff_reads_file(tmp_path):
    p = tmp_path / "pr.diff"
    p.write_text("diff --git a/x b/x\n+hello\n", encoding="utf-8")
    assert deslop_advisory._read_diff(str(p)) == "diff --git a/x b/x\n+hello\n"


def test_read_diff_missing_returns_empty(tmp_path):
    assert deslop_advisory._read_diff(str(tmp_path / "nope.diff")) == ""


def test_truncate_under_limit_passthrough():
    text = "short text"
    assert deslop_advisory._truncate(text, 1000) == text


def test_truncate_over_limit_clips_and_marks():
    text = "x" * 500
    out = deslop_advisory._truncate(text, 100)
    assert len(out.encode("utf-8")) <= 100 + len("\n\n...[diff truncated for length]...")
    assert out.startswith("x" * 100)
    assert "truncated" in out


def test_truncate_nonpositive_is_noop():
    text = "anything at all"
    assert deslop_advisory._truncate(text, 0) == text


def test_parse_llm_json_plain():
    raw = '{"suggestions": [{"file": "a.py", "severity": "low", "issue": "x", "fix": "y"}]}'
    parsed = deslop_advisory._parse_llm_json(raw)
    assert parsed["suggestions"][0]["file"] == "a.py"


def test_parse_llm_json_code_fence_json():
    raw = '```json\n{"suggestions": []}\n```'
    assert deslop_advisory._parse_llm_json(raw) == {"suggestions": []}


def test_parse_llm_json_code_fence_plain():
    raw = '```\n{"suggestions": [1]}\n```'
    assert deslop_advisory._parse_llm_json(raw) == {"suggestions": [1]}


def test_parse_llm_json_bad_json_returns_empty():
    assert deslop_advisory._parse_llm_json("not json at all") == {}


def test_parse_llm_json_empty_returns_empty():
    assert deslop_advisory._parse_llm_json("") == {}


def test_parse_llm_json_non_object_returns_empty():
    assert deslop_advisory._parse_llm_json("[1, 2, 3]") == {}


def test_build_comment_no_suggestions():
    out = deslop_advisory._build_comment([])
    assert "Deslop advisory (report-only)" in out
    assert "No slop found" in out


def test_build_comment_with_suggestions():
    suggestions = [
        {"file": "scripts/x.py", "severity": "high",
         "issue": "over-broad except swallows error", "fix": "remove the wrapper"},
        {"file": "scripts/y.py", "severity": "low",
         "issue": "redundant comment", "fix": "delete it"},
    ]
    out = deslop_advisory._build_comment(suggestions)
    assert "Deslop advisory (report-only)" in out
    assert "scripts/x.py" in out
    assert "over-broad except swallows error" in out
    assert "remove the wrapper" in out
    assert "scripts/y.py" in out
    assert "2 suggestion(s)" in out


def test_build_comment_tolerates_non_dict_entries():
    out = deslop_advisory._build_comment(["junk", {"file": "z.py", "severity": "medium",
                                                   "issue": "narration log", "fix": "drop it"}])
    assert "z.py" in out
    assert "narration log" in out


# --- marker presence -------------------------------------------------------

def test_build_comment_has_marker_at_top_no_suggestions():
    out = deslop_advisory._build_comment([])
    assert out.startswith(deslop_advisory.COMMENT_MARKER)
    assert "deslop-advisory" in out


def test_build_comment_has_marker_at_top_with_suggestions():
    out = deslop_advisory._build_comment(
        [{"file": "a.py", "severity": "low", "issue": "x", "fix": "y"}]
    )
    assert out.startswith(deslop_advisory.COMMENT_MARKER)


# --- meta sidecar: count + max_severity ------------------------------------

def test_build_meta_no_findings():
    meta = deslop_advisory._build_meta([])
    assert meta == {"count": 0, "max_severity": "none", "severities": []}


def test_build_meta_low_only():
    meta = deslop_advisory._build_meta(
        [{"file": "a.py", "severity": "low", "issue": "x"}]
    )
    assert meta["count"] == 1
    assert meta["max_severity"] == "low"
    assert meta["severities"] == ["low"]


def test_build_meta_medium_is_max_over_low():
    meta = deslop_advisory._build_meta([
        {"file": "a.py", "severity": "low", "issue": "x"},
        {"file": "b.py", "severity": "medium", "issue": "y"},
    ])
    assert meta["count"] == 2
    assert meta["max_severity"] == "medium"


def test_build_meta_high_is_max():
    meta = deslop_advisory._build_meta([
        {"file": "a.py", "severity": "low", "issue": "x"},
        {"file": "b.py", "severity": "high", "issue": "y"},
        {"file": "c.py", "severity": "medium", "issue": "z"},
    ])
    assert meta["count"] == 3
    assert meta["max_severity"] == "high"


def test_build_meta_case_insensitive_severity():
    meta = deslop_advisory._build_meta(
        [{"file": "a.py", "severity": "HIGH", "issue": "x"}]
    )
    assert meta["max_severity"] == "high"


def test_build_meta_unknown_severity_counts_but_is_none_max():
    meta = deslop_advisory._build_meta(
        [{"file": "a.py", "severity": "weird", "issue": "x"}]
    )
    assert meta["count"] == 1
    assert meta["max_severity"] == "none"


def test_build_meta_ignores_non_dict_entries():
    meta = deslop_advisory._build_meta(
        ["junk", {"file": "a.py", "severity": "medium", "issue": "x"}]
    )
    assert meta["count"] == 1
    assert meta["max_severity"] == "medium"


def test_write_meta_writes_json(tmp_path):
    out = tmp_path / "meta.json"
    deslop_advisory._write_meta(str(out), [
        {"file": "a.py", "severity": "high", "issue": "x"},
        {"file": "b.py", "severity": "low", "issue": "y"},
    ])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["count"] == 2
    assert data["max_severity"] == "high"
    assert data["severities"] == ["high", "low"]


def test_write_meta_empty(tmp_path):
    out = tmp_path / "meta.json"
    deslop_advisory._write_meta(str(out), [])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == {"count": 0, "max_severity": "none", "severities": []}
