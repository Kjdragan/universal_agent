"""Tests for system mission handlers (Phase 3d)."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_agent.delegation.system_handlers import (
    SystemMissionResult,
    dispatch_system_mission,
    handle_pause_factory,
    handle_resume_factory,
    handle_update_factory,
    is_system_mission,
)


class TestIsSystemMission:
    def test_known_system_mission(self):
        assert is_system_mission("system:update_factory") is True

    def test_pause_resume_are_system_missions(self):
        assert is_system_mission("system:pause_factory") is True
        assert is_system_mission("system:resume_factory") is True

    def test_regular_mission(self):
        assert is_system_mission("coding_task") is False
        assert is_system_mission("general_task") is False
        assert is_system_mission("") is False

    def test_unknown_system_prefix(self):
        assert is_system_mission("system:unknown") is False


class TestDispatchSystemMission:
    def test_dispatch_known_kind(self, tmp_path):
        # Create a fake update script that succeeds
        script = tmp_path / "scripts" / "update_factory.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/bash\necho '[update] Factory updated to abc1234'\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        result = dispatch_system_mission(
            "system:update_factory",
            {"branch": "main"},
            factory_dir=str(tmp_path),
        )
        assert result.status == "SUCCESS"
        assert result.restart_requested is True
        assert "abc1234" in result.result.get("updated_to", "")

    def test_dispatch_unknown_kind(self):
        result = dispatch_system_mission("system:nonexistent", {})
        assert result.status == "FAILED"
        assert "Unknown system mission kind" in result.error


class TestHandleUpdateFactory:
    def test_script_not_found(self, tmp_path):
        result = handle_update_factory(
            {"branch": "main"},
            factory_dir=str(tmp_path),
        )
        assert result.status == "FAILED"
        assert "not found" in result.error

    def test_script_success(self, tmp_path):
        script = tmp_path / "scripts" / "update_factory.sh"
        script.parent.mkdir(parents=True)
        script.write_text(
            "#!/bin/bash\n"
            "echo '[update] Fetching origin...'\n"
            "echo '[update] Factory updated to def5678'\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        result = handle_update_factory(
            {"branch": "main"},
            factory_dir=str(tmp_path),
        )
        assert result.status == "SUCCESS"
        assert result.restart_requested is True
        assert "def5678" in result.result.get("updated_to", "")
        assert result.result.get("branch") == "main"
        assert result.result.get("restart_scheduled") is True

    def test_script_failure(self, tmp_path):
        script = tmp_path / "scripts" / "update_factory.sh"
        script.parent.mkdir(parents=True)
        script.write_text(
            "#!/bin/bash\n"
            "echo 'error: merge conflict' >&2\n"
            "exit 1\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        result = handle_update_factory(
            {"branch": "main"},
            factory_dir=str(tmp_path),
        )
        assert result.status == "FAILED"
        assert result.restart_requested is False
        assert "exit 1" in result.error
        assert result.result.get("exit_code") == 1

    def test_script_timeout(self, tmp_path):
        script = tmp_path / "scripts" / "update_factory.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/bash\nsleep 999\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        with patch("universal_agent.delegation.system_handlers._DEFAULT_TIMEOUT", 1):
            result = handle_update_factory(
                {"branch": "main"},
                factory_dir=str(tmp_path),
            )
        assert result.status == "FAILED"
        assert "timed out" in result.error

    def test_branch_from_context(self, tmp_path):
        script = tmp_path / "scripts" / "update_factory.sh"
        script.parent.mkdir(parents=True)
        # Script that echoes the branch argument
        script.write_text(
            "#!/bin/bash\n"
            "echo \"[update] Branch: $2\"\n"
            "echo '[update] Factory updated to abc1234'\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        result = handle_update_factory(
            {"branch": "dev-feature"},
            factory_dir=str(tmp_path),
        )
        assert result.status == "SUCCESS"
        assert result.result.get("branch") == "dev-feature"

    def test_factory_dir_from_env(self, tmp_path):
        script = tmp_path / "scripts" / "update_factory.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/bash\necho '[update] Factory updated to env1234'\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        with patch.dict(os.environ, {"UA_FACTORY_DIR": str(tmp_path)}):
            result = handle_update_factory({"branch": "main"})
        assert result.status == "SUCCESS"


class TestHandlePauseFactory:
    def test_pause_returns_success(self):
        result = handle_pause_factory({})
        assert result.status == "SUCCESS"
        assert result.pause_requested is True
        assert result.resume_requested is False
        assert result.restart_requested is False
        assert result.result["action"] == "pause"

    def test_resume_returns_success(self):
        result = handle_resume_factory({})
        assert result.status == "SUCCESS"
        assert result.resume_requested is True
        assert result.pause_requested is False
        assert result.restart_requested is False
        assert result.result["action"] == "resume"

    def test_dispatch_pause(self):
        result = dispatch_system_mission("system:pause_factory", {})
        assert result.status == "SUCCESS"
        assert result.pause_requested is True

    def test_dispatch_resume(self):
        result = dispatch_system_mission("system:resume_factory", {})
        assert result.status == "SUCCESS"
        assert result.resume_requested is True


class TestSystemMissionResult:
    def test_default_values(self):
        r = SystemMissionResult(status="SUCCESS", result={"key": "val"})
        assert r.error == ""
        assert r.restart_requested is False
        assert r.pause_requested is False
        assert r.resume_requested is False

    def test_with_restart(self):
        r = SystemMissionResult(
            status="SUCCESS",
            result={},
            restart_requested=True,
        )
        assert r.restart_requested is True

    def test_with_pause(self):
        r = SystemMissionResult(status="SUCCESS", result={}, pause_requested=True)
        assert r.pause_requested is True
        assert r.resume_requested is False

    def test_with_resume(self):
        r = SystemMissionResult(status="SUCCESS", result={}, resume_requested=True)
        assert r.resume_requested is True
        assert r.pause_requested is False
