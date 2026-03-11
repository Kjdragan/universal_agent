from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "csi_threads_webhook_smoke.py"
    spec = importlib.util.spec_from_file_location("csi_threads_webhook_smoke", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load csi_threads_webhook_smoke module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_media_id_prefers_explicit():
    mod = _load_module()
    assert mod._resolve_media_id(explicit_media_id="abc123", fixed_media_id=False) == "abc123"


def test_resolve_media_id_uses_fixed_default_when_requested(monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("CSI_THREADS_WEBHOOK_CANARY_MEDIA_ID", "fixed-threads-id")
    assert mod._resolve_media_id(explicit_media_id="", fixed_media_id=True) == "fixed-threads-id"
