"""Heartbeat daemon model right-sizing (ZAI usage audit, 2026-08-08).

The heartbeat daemon session runs its persistent SDK client on the sonnet
tier by default (knob UA_HEARTBEAT_MODEL_TIER); every other session keeps
the opus daemon default. These tests cover the pure helpers the gateway
threads through EngineConfig -> setup_session.
"""

from universal_agent.services.daemon_sessions import (
    heartbeat_model_tier_default,
    is_heartbeat_daemon_session,
)
from universal_agent.gateway import _model_tier_default_for_session


class TestIsHeartbeatDaemonSession:
    def test_heartbeat_id_shape(self):
        assert is_heartbeat_daemon_session("daemon_simone_heartbeat")

    def test_other_agent_heartbeat_id_shape(self):
        assert is_heartbeat_daemon_session("daemon_atlas_heartbeat")

    def test_todo_daemon_is_not_heartbeat(self):
        assert not is_heartbeat_daemon_session("daemon_simone_todo")

    def test_interactive_session_is_not_heartbeat(self):
        assert not is_heartbeat_daemon_session("session_20260808_abc123")

    def test_metadata_role_wins_over_id_shape(self):
        # Metadata is authoritative when present, in both directions.
        assert is_heartbeat_daemon_session(
            "some_custom_id", {"daemon_role": "heartbeat"}
        )
        assert not is_heartbeat_daemon_session(
            "daemon_simone_heartbeat", {"daemon_role": "todo"}
        )

    def test_empty_inputs(self):
        assert not is_heartbeat_daemon_session("")
        assert not is_heartbeat_daemon_session("", {})


class TestHeartbeatModelTierDefault:
    def test_default_is_sonnet(self, monkeypatch):
        monkeypatch.delenv("UA_HEARTBEAT_MODEL_TIER", raising=False)
        assert heartbeat_model_tier_default() == "sonnet"

    def test_env_override_opus(self, monkeypatch):
        monkeypatch.setenv("UA_HEARTBEAT_MODEL_TIER", "opus")
        assert heartbeat_model_tier_default() == "opus"

    def test_invalid_value_falls_back_to_sonnet(self, monkeypatch):
        monkeypatch.setenv("UA_HEARTBEAT_MODEL_TIER", "glm-5.2")
        assert heartbeat_model_tier_default() == "sonnet"


class TestGatewayModelTierForSession:
    def test_heartbeat_session_gets_sonnet(self, monkeypatch):
        monkeypatch.delenv("UA_HEARTBEAT_MODEL_TIER", raising=False)
        assert (
            _model_tier_default_for_session("daemon_simone_heartbeat", None)
            == "sonnet"
        )

    def test_todo_daemon_keeps_default(self):
        assert _model_tier_default_for_session("daemon_simone_todo", None) is None

    def test_interactive_session_keeps_default(self):
        assert (
            _model_tier_default_for_session("session_20260808_abc123", {}) is None
        )
