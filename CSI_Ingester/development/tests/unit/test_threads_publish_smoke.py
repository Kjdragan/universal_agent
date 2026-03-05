from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "csi_threads_publish_smoke.py"
    spec = importlib.util.spec_from_file_location("csi_threads_publish_smoke", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load csi_threads_publish_smoke module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_permission_denial_detects_code_10_exact():
    mod = _load_module()
    body = '{"error":{"message":"Permission denied","type":"OAuthException","code":10}}'
    assert mod._is_permission_denial_error(body) is True


def test_permission_denial_does_not_match_code_100():
    mod = _load_module()
    body = '{"error":{"message":"The parameter text is required","type":"THApiException","code":100}}'
    assert mod._is_permission_denial_error(body) is False


def test_extract_threads_error_code_reads_numeric_code():
    mod = _load_module()
    body = '{"error":{"message":"The parameter text is required","type":"THApiException","code":100}}'
    assert mod._extract_threads_error_code(body) == 100


def test_extract_threads_error_code_returns_none_for_invalid_payload():
    mod = _load_module()
    assert mod._extract_threads_error_code("not-json") is None
