from __future__ import annotations

import logging

from universal_agent.sdk import runtime_info


def test_emit_sdk_runtime_banner_logs_versions_and_warning(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        runtime_info,
        "read_sdk_runtime_info",
        lambda: runtime_info.SdkRuntimeInfo(sdk_version="0.1.36", bundled_cli_version="2.1.42"),
    )
    info = runtime_info.emit_sdk_runtime_banner(required="0.1.48")
    assert info.sdk_version == "0.1.36"
    assert info.bundled_cli_version == "2.1.42"
    assert any("runtime versions" in rec.message for rec in caplog.records)
    assert any("below the required minimum" in rec.message for rec in caplog.records)


def test_sdk_version_is_at_least():
    assert runtime_info.sdk_version_is_at_least("0.1.48", current="0.1.48")
    assert runtime_info.sdk_version_is_at_least("0.1.48", current="0.1.49")
    assert not runtime_info.sdk_version_is_at_least("0.1.48", current="0.1.36")
