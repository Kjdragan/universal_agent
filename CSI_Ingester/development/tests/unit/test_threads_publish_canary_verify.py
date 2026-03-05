from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "csi_threads_publish_canary_verify.py"
    spec = importlib.util.spec_from_file_location("csi_threads_publish_canary_verify", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load csi_threads_publish_canary_verify module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_error_signature_extracts_threads_code_marker():
    mod = _load_module()
    detail = 'ThreadsAPIError:http_500:{"error":{"message":"x","code":10}}'
    sig = mod._error_signature(detail)
    assert '"code":10' in sig


def test_main_passes_with_dry_run_records(monkeypatch, tmp_path):
    mod = _load_module()
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "occurred_at_utc": "2026-03-05T20:00:00Z",
                "status": "dry_run",
                "operation": "create_container",
                "approval_ref": "phase2-dry-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "csi_threads_publish_canary_verify.py",
            "--audit-path",
            str(audit),
            "--lookback-hours",
            "72",
            "--min-records",
            "1",
            "--max-error-rate",
            "1.0",
            "--quiet",
        ],
    )
    assert mod.main() == 0


def test_main_can_require_live_ok(monkeypatch, tmp_path):
    mod = _load_module()
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "occurred_at_utc": "2026-03-05T20:00:00Z",
                "status": "dry_run",
                "operation": "create_container",
                "approval_ref": "phase2-dry-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "csi_threads_publish_canary_verify.py",
            "--audit-path",
            str(audit),
            "--lookback-hours",
            "72",
            "--min-records",
            "1",
            "--require-live-ok",
            "--quiet",
        ],
    )
    assert mod.main() == 1
