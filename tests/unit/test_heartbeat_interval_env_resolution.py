import logging


def test_resolve_heartbeat_interval_prefers_interval_and_warns_on_conflict(monkeypatch, caplog):
    import universal_agent.heartbeat_service as hb

    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("UA_HEARTBEAT_INTERVAL", "10m")
    monkeypatch.setenv("UA_HEARTBEAT_EVERY", "25m")

    resolved = hb._resolve_heartbeat_interval_env(
        prefer_interval=True,
        warn_on_conflict=True,
    )

    assert resolved == "10m"
    assert "Conflicting heartbeat interval env vars detected" in caplog.text
    assert "UA_HEARTBEAT_INTERVAL" in caplog.text


def test_resolve_heartbeat_interval_falls_back_to_legacy_every(monkeypatch):
    import universal_agent.heartbeat_service as hb

    monkeypatch.delenv("UA_HEARTBEAT_INTERVAL", raising=False)
    monkeypatch.setenv("UA_HEARTBEAT_EVERY", "15m")

    resolved = hb._resolve_heartbeat_interval_env(
        prefer_interval=True,
        warn_on_conflict=True,
    )
    assert resolved == "15m"


def test_resolve_min_interval_seconds_reads_current_env(monkeypatch):
    import universal_agent.heartbeat_service as hb

    monkeypatch.setenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", "600")
    assert hb._resolve_min_interval_seconds(default=1800) == 600

    monkeypatch.setenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", "900")
    assert hb._resolve_min_interval_seconds(default=1800) == 900
