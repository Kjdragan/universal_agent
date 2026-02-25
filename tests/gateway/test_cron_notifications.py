import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

from universal_agent import gateway_server
from universal_agent.cron_service import CronService
from universal_agent.gateway import GatewaySession


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)


def test_emit_cron_event_adds_completion_notification(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_cron_service", CronService(_StubGateway(), tmp_path))

    cron_ws = tmp_path / "cron_notification_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="notification demo",
        every_raw="30m",
        enabled=True,
    )

    before_count = len(gateway_server._notifications)
    gateway_server._emit_cron_event(
        {
            "type": "cron_run_completed",
            "run": {
                "run_id": "run_ntf_1",
                "job_id": job.job_id,
                "status": "success",
                "scheduled_at": time.time(),
                "started_at": time.time(),
                "finished_at": time.time(),
                "error": None,
            },
        }
    )

    assert len(gateway_server._notifications) == before_count + 1
    latest = gateway_server._notifications[-1]
    assert latest["kind"] == "cron_run_success"
    assert latest["title"] == "Chron Run Succeeded"
    assert latest["metadata"]["job_id"] == job.job_id
    assert latest["metadata"]["status"] == "success"


def test_emit_cron_event_labels_autonomous_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_cron_service", CronService(_StubGateway(), tmp_path))

    cron_ws = tmp_path / "cron_autonomous_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="cron_system",
        workspace_dir=str(cron_ws),
        command="autonomous demo",
        every_raw="30m",
        enabled=True,
        metadata={"autonomous": True, "system_job": "demo"},
    )

    gateway_server._emit_cron_event(
        {
            "type": "cron_run_completed",
            "run": {
                "run_id": "run_auto_1",
                "job_id": job.job_id,
                "status": "success",
                "scheduled_at": time.time(),
                "started_at": time.time(),
                "finished_at": time.time(),
                "error": None,
            },
        }
    )

    latest = gateway_server._notifications[-1]
    assert latest["kind"] == "autonomous_run_completed"
    assert latest["title"] == "Autonomous Task Completed"
    assert latest["metadata"]["autonomous"] is True


def test_emit_cron_event_daily_briefing_includes_report_links(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(gateway_server, "_cron_service", CronService(_StubGateway(), tmp_path))

    # Seed prior autonomous activity so the deterministic briefing has content.
    gateway_server._add_notification(
        kind="autonomous_run_completed",
        title="Autonomous Task Completed",
        message="autonomous demo completed",
        severity="info",
        metadata={
            "source": "cron",
            "autonomous": True,
            "job_id": "seed_job",
            "run_id": "seed_run",
        },
    )

    cron_ws = tmp_path / "cron_daily_briefing_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="cron_system",
        workspace_dir=str(cron_ws),
        command="daily briefing job",
        cron_expr="0 7 * * *",
        timezone="UTC",
        enabled=True,
        metadata={
            "autonomous": True,
            "system_job": gateway_server.AUTONOMOUS_DAILY_BRIEFING_JOB_KEY,
        },
    )

    gateway_server._emit_cron_event(
        {
            "type": "cron_run_completed",
            "run": {
                "run_id": "run_daily_brief_1",
                "job_id": job.job_id,
                "status": "success",
                "scheduled_at": time.time(),
                "started_at": time.time(),
                "finished_at": time.time(),
                "error": None,
            },
        }
    )

    latest = gateway_server._notifications[-1]
    assert latest["kind"] == "autonomous_daily_briefing_ready"
    assert latest["title"] == "Daily Autonomous Briefing Ready"
    assert latest["metadata"]["report_api_url"].startswith("/api/artifacts/files/autonomous-briefings/")
    assert latest["metadata"]["report_relative_path"].endswith("/DAILY_BRIEFING.md")


def test_emit_heartbeat_event_records_workspace_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(gateway_server, "_sessions", {})

    workspace_root = gateway_server.WORKSPACES_DIR
    workspace_root.mkdir(parents=True, exist_ok=True)
    session_ws = workspace_root / "session_abc"
    session_ws.mkdir(parents=True, exist_ok=True)
    output_file = session_ws / "work_products" / "summary.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# output\n", encoding="utf-8")

    session = GatewaySession(
        session_id="session_abc",
        user_id="tester",
        workspace_dir=str(session_ws),
        metadata={},
    )
    gateway_server._sessions[session.session_id] = session

    gateway_server._emit_heartbeat_event(
        {
            "type": "heartbeat_completed",
            "session_id": session.session_id,
            "timestamp": time.time(),
            "ok_only": False,
            "suppressed_reason": None,
            "sent": True,
            "artifacts": {
                "writes": [],
                "work_products": [str(output_file)],
                "bash_commands": [],
            },
        }
    )

    latest = gateway_server._notifications[-1]
    assert latest["kind"] == "autonomous_heartbeat_completed"
    links = latest["metadata"]["heartbeat_artifacts"]
    assert isinstance(links, list) and links
    assert links[0]["scope"] == "workspaces"
    assert links[0]["relative_path"].endswith("session_abc/work_products/summary.md")
    assert "scope=workspaces" in links[0]["storage_href"]


def test_generate_daily_briefing_includes_non_cron_artifacts_section(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")

    gateway_server._add_notification(
        kind="autonomous_heartbeat_completed",
        title="Autonomous Heartbeat Activity Completed",
        message="heartbeat completed independent work",
        severity="info",
        metadata={
            "source": "heartbeat",
            "heartbeat_artifacts": [
                {
                    "scope": "workspaces",
                    "relative_path": "session_abc/work_products/summary.md",
                    "storage_href": "/storage?tab=explorer&scope=workspaces&path=session_abc%2Fwork_products%2Fsummary.md",
                    "api_url": "",
                }
            ],
        },
    )

    payload = gateway_server._generate_autonomous_daily_briefing_artifact(now_ts=time.time())
    markdown_path = gateway_server.ARTIFACTS_DIR / payload["markdown"]["relative_path"]
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Non-Cron Autonomous Artifact Outputs" in markdown
    assert "scope=workspaces" in markdown
    assert payload["counts"]["non_cron_artifacts"] == 1


def test_storage_explorer_href_normalizes_file_path_to_parent_for_preview():
    file_rel = "youtube-tutorial-learning/2026-02-25/abc123/README.md"
    href = gateway_server._storage_explorer_href(scope="artifacts", path=file_rel, preview=file_rel)
    parsed = urllib.parse.urlparse(href)
    params = urllib.parse.parse_qs(parsed.query)

    assert params["tab"] == ["explorer"]
    assert params["scope"] == ["artifacts"]
    assert params["root_source"] == ["local"]
    assert params["path"] == ["youtube-tutorial-learning/2026-02-25/abc123"]
    assert params["preview"] == [file_rel]


def test_storage_explorer_href_supports_root_file_preview_without_path():
    href = gateway_server._storage_explorer_href(scope="workspaces", path="", preview="run.log")
    parsed = urllib.parse.urlparse(href)
    params = urllib.parse.parse_qs(parsed.query)

    assert params["tab"] == ["explorer"]
    assert params["scope"] == ["workspaces"]
    assert params["root_source"] == ["local"]
    assert "path" not in params
    assert params["preview"] == ["run.log"]
