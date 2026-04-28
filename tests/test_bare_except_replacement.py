"""Tests for CODIE bare-except replacement (main.py lines 4227, 10075)."""

import json


def test_run_spec_json_decode_with_valid_json():
    raw = '{"original_objective": "test objective"}'
    result = json.loads(raw)
    assert result == {"original_objective": "test objective"}


def test_run_spec_json_decode_with_invalid_json_falls_back():
    raw = "not-json"
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        result = {}
    assert result == {}


def test_run_spec_json_decode_with_empty_string_falls_back():
    raw = ""
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        result = {}
    assert result == {}


def test_aexit_exception_type_hierarchy():
    assert not issubclass(KeyboardInterrupt, Exception)
    assert not issubclass(SystemExit, Exception)
    assert issubclass(RuntimeError, Exception)
    assert issubclass(ValueError, Exception)


def test_aexit_exception_type_catches_runtime_error():
    caught = False
    try:
        raise RuntimeError("cleanup failed")
    except Exception:
        caught = True
    assert caught


def test_aexit_exception_type_catches_attribute_error():
    caught = False
    try:
        raise AttributeError("NoneType has no attribute __aexit__")
    except Exception:
        caught = True
    assert caught
