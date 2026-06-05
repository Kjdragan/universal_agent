import logging


def test_resolve_heartbeat_interval_honors_only_interval(monkeypatch, caplog):
    """``UA_HEARTBEAT_INTERVAL`` is canonical. The legacy ``UA_HEARTBEAT_EVERY``
    alias was retired (2026-06-05, S4 scheduling cleanup) and is now ignored —
    even when both are set there is no conflict to warn about."""
    import universal_agent.heartbeat_service as hb

    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("UA_HEARTBEAT_INTERVAL", "10m")
    monkeypatch.setenv("UA_HEARTBEAT_EVERY", "25m")

    resolved = hb._resolve_heartbeat_interval_env()

    assert resolved == "10m"
    assert "Conflicting heartbeat interval env vars detected" not in caplog.text


def test_resolve_heartbeat_interval_ignores_legacy_every(monkeypatch):
    """With only the retired ``UA_HEARTBEAT_EVERY`` set (``UA_HEARTBEAT_INTERVAL``
    unset), the resolver returns ``None`` — callers then fall back to
    ``DEFAULT_INTERVAL_SECONDS`` rather than honoring the deprecated alias."""
    import universal_agent.heartbeat_service as hb

    monkeypatch.delenv("UA_HEARTBEAT_INTERVAL", raising=False)
    monkeypatch.setenv("UA_HEARTBEAT_EVERY", "15m")

    assert hb._resolve_heartbeat_interval_env() is None


def test_resolve_min_interval_seconds_reads_current_env(monkeypatch):
    import universal_agent.heartbeat_service as hb

    monkeypatch.setenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", "600")
    assert hb._resolve_min_interval_seconds(default=1800) == 600

    monkeypatch.setenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", "900")
    assert hb._resolve_min_interval_seconds(default=1800) == 900
