"""Unit tests for utils/json_utils.py — 5-layer JSON extraction."""

from pydantic import BaseModel
import pytest

from universal_agent.utils.json_utils import extract_json_payload

# ── Pydantic model fixtures ──────────────────────────────────────────────


class SampleModel(BaseModel):
    name: str
    count: int


# ── Layer 0/1: standard JSON ─────────────────────────────────────────────


class TestStandardParse:
    def test_valid_dict(self):
        result = extract_json_payload('{"a": 1}')
        assert result == {"a": 1}

    def test_valid_list(self):
        result = extract_json_payload("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            extract_json_payload("")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            extract_json_payload(None)

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            extract_json_payload(42)


# ── Layer 0: Python literal normalization ─────────────────────────────────


class TestPythonLiteralNormalization:
    def test_true_false_none(self):
        result = extract_json_payload('{"ok": True, "fail": False, "val": None}')
        assert result == {"ok": True, "fail": False, "val": None}

    def test_mixed_python_literals(self):
        result = extract_json_payload('{"a": True, "b": None, "c": False}')
        assert result["a"] is True
        assert result["b"] is None
        assert result["c"] is False


# ── Layer 2: json_repair recovery ────────────────────────────────────────


class TestJsonRepair:
    def test_trailing_comma(self):
        result = extract_json_payload('{"a": 1,}')
        assert result == {"a": 1}

    def test_unquoted_keys(self):
        result = extract_json_payload('{a: 1, b: "hello"}')
        assert result == {"a": 1, "b": "hello"}


# ── Layer 3: regex extraction from surrounding text ──────────────────────


class TestRegexExtraction:
    def test_json_wrapped_in_markdown(self):
        text = 'Here is the result:\n```json\n{"key": "value"}\n```\nDone.'
        result = extract_json_payload(text)
        assert result == {"key": "value"}

    def test_json_in_conversational_text(self):
        text = 'The answer is {"status": "ok", "data": [1, 2]} as discussed.'
        result = extract_json_payload(text)
        assert result["status"] == "ok"

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="Failed to extract"):
            extract_json_payload("This is just plain text with no JSON at all.")


# ── Layer 5: Pydantic validation ─────────────────────────────────────────


class TestPydanticValidation:
    def test_valid_model(self):
        result = extract_json_payload('{"name": "test", "count": 5}', model=SampleModel)
        assert isinstance(result, SampleModel)
        assert result.name == "test"
        assert result.count == 5

    def test_model_validation_fails_returns_dict(self):
        """When require_model=False (default), return raw dict on validation failure."""
        result = extract_json_payload('{"name": "test"}', model=SampleModel)
        assert isinstance(result, dict)
        assert result == {"name": "test"}

    def test_require_model_raises_on_bad_schema(self):
        with pytest.raises(ValueError, match="did not match required schema"):
            extract_json_payload('{"name": "test"}', model=SampleModel, require_model=True)

    def test_no_model_returns_dict(self):
        result = extract_json_payload('{"anything": "goes"}')
        assert isinstance(result, dict)
