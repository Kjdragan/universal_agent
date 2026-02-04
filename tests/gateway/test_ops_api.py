import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_agent import gateway_server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setenv("UA_OPS_CONFIG_PATH", str(tmp_path / "ops_config.json"))
    monkeypatch.setenv("UA_APPROVALS_PATH", str(tmp_path / "approvals.json"))
    return TestClient(gateway_server.app)


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_ops_sessions_list_preview_compact_reset(client, tmp_path):
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    _write_lines(session_dir / "activity_journal.log", ["line1", "line2", "line3"])
    _write_lines(session_dir / "run.log", ["run1", "run2"])

    resp = client.get("/api/v1/ops/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["sessions"][0]["session_id"] == "session_test"

    preview = client.get("/api/v1/ops/sessions/session_test/preview?limit=2")
    assert preview.status_code == 200
    preview_data = preview.json()
    assert preview_data["lines"] == ["line2", "line3"]

    compact = client.post(
        "/api/v1/ops/sessions/session_test/compact",
        json={"max_lines": 1, "max_bytes": 100},
    )
    assert compact.status_code == 200
    assert (session_dir / "activity_journal.log").read_text().strip() == "line3"

    reset = client.post(
        "/api/v1/ops/sessions/session_test/reset",
        json={"clear_logs": True, "clear_memory": False, "clear_work_products": False},
    )
    assert reset.status_code == 200
    assert not (session_dir / "activity_journal.log").exists()
    assert not (session_dir / "run.log").exists()


def test_ops_logs_tail(client, tmp_path):
    session_dir = tmp_path / "session_logs"
    session_dir.mkdir()
    _write_lines(session_dir / "run.log", ["alpha", "beta", "gamma"])
    resp = client.get("/api/v1/ops/logs/tail?session_id=session_logs&limit=2")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["lines"] == ["beta", "gamma"]


def test_ops_skills_override(client, tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skill_path = skills_dir / "test-skill"
    skill_path.mkdir(parents=True)
    (skill_path / "SKILL.md").write_text(
        "---\nname: TestSkill\ndescription: Test skill\n---\nBody\n"
    )
    monkeypatch.setenv("UA_SKILLS_DIR", str(skills_dir))

    resp = client.get("/api/v1/ops/skills")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    assert skills[0]["name"] == "TestSkill"
    assert skills[0]["enabled"] is True

    update = client.patch("/api/v1/ops/skills/testskill", json={"enabled": False})
    assert update.status_code == 200

    resp = client.get("/api/v1/ops/skills")
    skill = resp.json()["skills"][0]
    assert skill["enabled"] is False


def test_ops_config_schema(client):
    resp = client.get("/api/v1/ops/config/schema")
    assert resp.status_code == 200
    schema = resp.json()["schema"]
    assert schema["type"] == "object"
    assert "skills" in schema["properties"]
    assert "channels" in schema["properties"]


def test_ops_channels_probe(client):
    probe = client.post("/api/v1/ops/channels/gateway/probe")
    assert probe.status_code == 200
    payload = probe.json()["probe"]
    assert payload["status"] == "ok"

    resp = client.get("/api/v1/ops/channels")
    assert resp.status_code == 200
    channels = resp.json()["channels"]
    gateway = next(item for item in channels if item["id"] == "gateway")
    assert gateway["probe"]["status"] == "ok"


def test_ops_approvals_create_update(client):
    create = client.post(
        "/api/v1/ops/approvals",
        json={"approval_id": "phase_2", "summary": "Test approval"},
    )
    assert create.status_code == 200
    approval = create.json()["approval"]
    assert approval["approval_id"] == "phase_2"
    assert approval["status"] == "pending"

    listing = client.get("/api/v1/ops/approvals")
    assert listing.status_code == 200
    approvals = listing.json()["approvals"]
    assert approvals and approvals[0]["approval_id"] == "phase_2"

    update = client.patch("/api/v1/ops/approvals/phase_2", json={"status": "approved"})
    assert update.status_code == 200
    updated = update.json()["approval"]
    assert updated["status"] == "approved"
