"""Tests for the PR B gateway routes: brief viewer + feedback + digest pause."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_agent import gateway_server
from universal_agent.services.cron_artifact_notifier import (
    sign_digest_pause_token,
    sign_feedback_token,
)
from universal_agent.services.proactive_artifacts import upsert_artifact


def _disable_lifespan(monkeypatch):
    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    monkeypatch.setattr(
        gateway_server.app.router, "lifespan_context", _test_lifespan
    )


@pytest.fixture
def gateway_env(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "UA_ACTIVITY_DB_PATH", str((tmp_path / "activity.db").resolve())
    )
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret-for-feedback")
    monkeypatch.setenv(
        "UA_RECENT_BRIEFS_INDEX_PATH",
        str((tmp_path / "recent_briefs_index.md").resolve()),
    )
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    _disable_lifespan(monkeypatch)
    return tmp_path


def _create_artifact(tmp_path: Path, *, with_html: bool = False) -> str:
    conn = gateway_server._activity_connect()
    try:
        gateway_server._ensure_activity_schema(conn)
        artifact_path = ""
        if with_html:
            html_path = tmp_path / "intel" / "brief.html"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(
                "<h2>Convergence detected</h2><p>Three channels agree.</p>",
                encoding="utf-8",
            )
            artifact_path = str(html_path)
        artifact = upsert_artifact(
            conn,
            artifact_type="intel_brief",
            source_kind="convergence_candidate",
            source_ref="cand_test01",
            title="Test convergence brief",
            summary="Brief test summary.",
            artifact_path=artifact_path,
            metadata={"thesis": "A test thesis.", "candidate_id": "cand_test01"},
        )
        return artifact["artifact_id"]
    finally:
        conn.close()


class TestFeedbackEndpoint:
    def test_valid_up_vote(self, gateway_env):
        artifact_id = _create_artifact(gateway_env)
        token = sign_feedback_token(artifact_id, "up")
        with TestClient(gateway_server.app) as client:
            r = client.get(
                f"/api/v1/briefs/{artifact_id}/feedback",
                params={"v": "up", "t": token},
            )
            assert r.status_code == 200
            assert "Thanks" in r.text or "recorded" in r.text

            # Verify the artifact was updated with feedback.
            conn = gateway_server._activity_connect()
            try:
                from universal_agent.services.proactive_artifacts import get_artifact
                fetched = get_artifact(conn, artifact_id)
                assert fetched is not None
                feedback = fetched.get("feedback") or {}
                assert feedback.get("last_score") == 5
            finally:
                conn.close()

    def test_valid_down_vote(self, gateway_env):
        artifact_id = _create_artifact(gateway_env)
        token = sign_feedback_token(artifact_id, "down")
        with TestClient(gateway_server.app) as client:
            r = client.get(
                f"/api/v1/briefs/{artifact_id}/feedback",
                params={"v": "down", "t": token},
            )
            assert r.status_code == 200

            conn = gateway_server._activity_connect()
            try:
                from universal_agent.services.proactive_artifacts import get_artifact
                fetched = get_artifact(conn, artifact_id)
                feedback = fetched.get("feedback") or {}
                assert feedback.get("last_score") == 1
            finally:
                conn.close()

    def test_invalid_hmac_returns_401(self, gateway_env):
        artifact_id = _create_artifact(gateway_env)
        with TestClient(gateway_server.app) as client:
            r = client.get(
                f"/api/v1/briefs/{artifact_id}/feedback",
                params={"v": "up", "t": "deadbeefcafebabe"},
            )
            assert r.status_code == 401
            assert "expired" in r.text.lower() or "invalid" in r.text.lower()

    def test_missing_vote_returns_401(self, gateway_env):
        artifact_id = _create_artifact(gateway_env)
        with TestClient(gateway_server.app) as client:
            r = client.get(
                f"/api/v1/briefs/{artifact_id}/feedback",
                params={"t": "anything"},
            )
            assert r.status_code == 401

    def test_missing_artifact_returns_404(self, gateway_env):
        # Make sure schema exists so the verify path can reach the lookup.
        conn = gateway_server._activity_connect()
        try:
            gateway_server._ensure_activity_schema(conn)
        finally:
            conn.close()
        artifact_id = "pa_doesnotexist01"
        token = sign_feedback_token(artifact_id, "up")
        with TestClient(gateway_server.app) as client:
            r = client.get(
                f"/api/v1/briefs/{artifact_id}/feedback",
                params={"v": "up", "t": token},
            )
            assert r.status_code == 404


class TestBriefViewer:
    def test_with_html_file(self, gateway_env):
        artifact_id = _create_artifact(gateway_env, with_html=True)
        with TestClient(gateway_server.app) as client:
            r = client.get(f"/briefs/{artifact_id}")
            assert r.status_code == 200
            assert "Convergence detected" in r.text

    def test_without_html_file_falls_back(self, gateway_env):
        artifact_id = _create_artifact(gateway_env, with_html=False)
        with TestClient(gateway_server.app) as client:
            r = client.get(f"/briefs/{artifact_id}")
            assert r.status_code == 200
            assert "Brief test summary" in r.text
            assert "summary from the database" in r.text.lower()

    def test_missing_artifact_returns_404(self, gateway_env):
        # Ensure schema exists.
        conn = gateway_server._activity_connect()
        try:
            gateway_server._ensure_activity_schema(conn)
        finally:
            conn.close()
        with TestClient(gateway_server.app) as client:
            r = client.get("/briefs/pa_nope00000000aa")
            assert r.status_code == 404


class TestDigestPause:
    def test_valid_pause(self, gateway_env):
        token = sign_digest_pause_token(24)
        with TestClient(gateway_server.app) as client:
            r = client.get(
                "/api/v1/digest/pause", params={"hours": 24, "t": token}
            )
            assert r.status_code == 200
            assert "paused" in r.text.lower()

        # Verify DB state.
        conn = gateway_server._activity_connect()
        try:
            row = conn.execute(
                "SELECT paused_until FROM digest_state WHERE id = 1"
            ).fetchone()
            assert row is not None
            assert row["paused_until"]
        finally:
            conn.close()

    def test_invalid_hmac_returns_401(self, gateway_env):
        with TestClient(gateway_server.app) as client:
            r = client.get(
                "/api/v1/digest/pause",
                params={"hours": 24, "t": "deadbeefcafebabe"},
            )
            assert r.status_code == 401

    def test_hours_clamped_at_168(self, gateway_env):
        # Sign with 168 so the verify succeeds after clamp.
        token = sign_digest_pause_token(168)
        with TestClient(gateway_server.app) as client:
            r = client.get(
                "/api/v1/digest/pause",
                params={"hours": 9999, "t": token},
            )
            # Hours clamped to 168 → token signed for 168 matches.
            assert r.status_code == 200
            assert "168h" in r.text or "paused" in r.text.lower()
