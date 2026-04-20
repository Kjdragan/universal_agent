"""
YouTube Transcript Guardrail & UV_CACHE_DIR Injection Tests
============================================================

Covers:
  1. _looks_like_youtube_transcript_intent() — positive / negative detection
  2. on_pre_tool_use_ledger YouTube guardrail:
       - blocks mcp__youtube__get_metadata before skill is invoked
       - unblocks after Skill(youtube-transcript-metadata) is recorded
       - does NOT fire for non-YouTube prompts
       - is exempted in VP worker lane
  3. on_pre_bash_inject_workspace_env UV_CACHE_DIR injection:
       - fires even when get_current_workspace() returns None
       - fires in normal (workspace available) path
       - skips injection when UV_CACHE_DIR= already in the command
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from universal_agent.hooks import (
    AgentHookSet,
    _looks_like_research_report_pipeline_intent,
    _looks_like_youtube_transcript_intent,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Intent detection helper
# ---------------------------------------------------------------------------


class TestYouTubeIntentDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "get the youtube transcript for https://youtu.be/abc123",
            "https://www.youtube.com/watch?v=abc123",
            "fetch the transcript of this video",
            "get captions for this video",
            "download subtitles from youtube",
            "summarise this youtube video",
            "https://youtu.be/SpReZZk_13w?si=uG8KhyjOMYGAXDU5",
        ],
    )
    def test_positive_cases(self, text):
        assert _looks_like_youtube_transcript_intent(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "find the best restaurants near me",
            "search for recent news on AI",
            "write a research report on climate change",
            "email me the report as a PDF",
            "run the heartbeat check",
            "",
            None,
        ],
    )
    def test_negative_cases(self, text):
        assert _looks_like_youtube_transcript_intent(text) is False

    def test_case_insensitive(self):
        assert _looks_like_youtube_transcript_intent("Get The TRANSCRIPT please") is True

    def test_youtu_be_short_url(self):
        assert _looks_like_youtube_transcript_intent("https://youtu.be/xyz") is True


class TestResearchIntentDetection:
    def test_media_only_request_does_not_trigger_research_delegate(self):
        text = "Get the YouTube transcript for https://youtu.be/SpReZZk_13w"
        assert _looks_like_research_report_pipeline_intent(text) is False

    def test_mixed_youtube_research_request_triggers_research_delegate(self):
        text = (
            "Get transcript from https://youtu.be/SpReZZk_13w, then research latest info from "
            "multiple sources, create a report, convert to PDF, and email it."
        )
        assert _looks_like_research_report_pipeline_intent(text) is True

    def test_delivery_only_creative_request_does_not_trigger_research_delegate(self):
        text = "Write a poem and then email it to me."
        assert _looks_like_research_report_pipeline_intent(text) is False

    def test_research_plus_delivery_request_triggers_research_delegate(self):
        text = "Search for latest AI safety updates and email the report as a PDF."
        assert _looks_like_research_report_pipeline_intent(text) is True


# ---------------------------------------------------------------------------
# 2. on_pre_tool_use_ledger YouTube guardrail
# ---------------------------------------------------------------------------


class TestYouTubeMcpGuardrail:
    def _make_hooks(self, workspace: str = "/opt/universal_agent") -> AgentHookSet:
        return AgentHookSet(run_id="unit-yt-guardrail", active_workspace=workspace)

    def _set_prompt(self, hooks: AgentHookSet, prompt: str) -> None:
        _run(hooks.on_user_prompt_skill_awareness({"prompt": prompt}))

    def _call_mcp_youtube(self, hooks: AgentHookSet, tool_name: str = "mcp__youtube__get_metadata") -> dict:
        return _run(
            hooks.on_pre_tool_use_ledger(
                {
                    "tool_name": tool_name,
                    "tool_input": {
                        "url": "https://youtu.be/SpReZZk_13w"
                    },
                },
                "tool-mcp-yt-1",
                {},
            )
        )

    def _call_skill_youtube(self, hooks: AgentHookSet) -> dict:
        return _run(
            hooks.on_pre_tool_use_ledger(
                {
                    "tool_name": "Skill",
                    "tool_input": {
                        "skill": "youtube-transcript-metadata",
                        "args": "https://youtu.be/SpReZZk_13w",
                    },
                },
                "tool-skill-yt-1",
                {},
            )
        )

    def _call_bash_youtube(self, hooks: AgentHookSet, command: str, *, parent_tool_use_id: str | None = None) -> dict:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        if parent_tool_use_id:
            payload["parent_tool_use_id"] = parent_tool_use_id
        return _run(
            hooks.on_pre_tool_use_ledger(
                payload,
                "tool-bash-yt-1",
                {},
            )
        )

    def _call_task(self, hooks: AgentHookSet, subagent_type: str) -> dict:
        return _run(
            hooks.on_pre_tool_use_ledger(
                {
                    "tool_name": "Task",
                    "tool_input": {
                        "subagent_type": subagent_type,
                        "description": "test delegation",
                        "prompt": "test",
                    },
                },
                "tool-task-1",
                {},
            )
        )

    def test_blocks_mcp_youtube_before_skill_is_invoked(self):
        hooks = self._make_hooks()
        self._set_prompt(hooks, "get the youtube transcript for https://youtu.be/SpReZZk_13w")

        result = self._call_mcp_youtube(hooks)

        assert result.get("decision") == "block"
        assert "youtube-transcript-metadata" in str(result.get("systemMessage", ""))

    def test_allows_mcp_youtube_after_skill_is_invoked(self):
        hooks = self._make_hooks()
        self._set_prompt(hooks, "get the youtube transcript for https://youtu.be/SpReZZk_13w")

        self._call_skill_youtube(hooks)
        result = self._call_mcp_youtube(hooks)

        assert result.get("decision") != "block"

    def test_blocks_other_mcp_youtube_tools_before_skill(self):
        hooks = self._make_hooks()
        self._set_prompt(hooks, "download subtitles from https://youtu.be/SpReZZk_13w")

        result = self._call_mcp_youtube(hooks, tool_name="mcp__youtube__download_subtitles")
        assert result.get("decision") == "block"
        assert "mcp__youtube__*" in str(result.get("systemMessage", ""))

    def test_allows_inline_bash_youtube_in_python_c_body(self):
        """_strip_heredoc_bodies strips python -c payloads before marker matching,
        so YouTube references inside a python -c body are no longer blocked."""
        hooks = self._make_hooks()
        self._set_prompt(hooks, "get transcript for https://youtu.be/SpReZZk_13w")

        result = self._call_bash_youtube(
            hooks,
            "uv run python3 -c \"from youtube_transcript_api import YouTubeTranscriptApi\"",
        )
        # python -c body is stripped — markers not in executable portion
        assert result.get("decision") != "block"

    def test_allows_inline_bash_youtube_fetch_after_skill(self):
        hooks = self._make_hooks()
        self._set_prompt(hooks, "get transcript for https://youtu.be/SpReZZk_13w")
        self._call_skill_youtube(hooks)

        result = self._call_bash_youtube(
            hooks,
            "uv run python3 -c \"from youtube_transcript_api import YouTubeTranscriptApi\"",
        )
        assert result.get("decision") != "block"

    def test_no_guardrail_for_non_youtube_prompt(self):
        hooks = self._make_hooks()
        self._set_prompt(hooks, "what is the weather today?")

        result = self._call_mcp_youtube(hooks)

        assert result.get("decision") != "block"

    def test_guardrail_resets_each_turn(self):
        hooks = self._make_hooks()

        self._set_prompt(hooks, "get the youtube transcript for https://youtu.be/abc")
        self._call_skill_youtube(hooks)
        assert self._call_mcp_youtube(hooks).get("decision") != "block"

        self._set_prompt(hooks, "get the youtube transcript for https://youtu.be/abc")
        result = self._call_mcp_youtube(hooks)
        assert result.get("decision") == "block"

    def test_guardrail_exempt_in_vp_worker_lane(self):
        vp_workspace = (
            "/opt/universal_agent/AGENT_RUN_WORKSPACES/vp_session/vp-mission-123"
        )
        hooks = self._make_hooks(workspace=vp_workspace)
        self._set_prompt(hooks, "get the youtube transcript for https://youtu.be/abc")

        result = self._call_mcp_youtube(hooks)

        assert result.get("decision") != "block"

    def test_allows_yt_dlp_in_python_c_subagent_context(self):
        """_strip_heredoc_bodies strips python -c payloads before marker matching,
        so yt_dlp references inside a python -c body are no longer blocked."""
        hooks = self._make_hooks()
        self._set_prompt(hooks, "get transcript for https://youtu.be/SpReZZk_13w")

        result = self._call_bash_youtube(
            hooks,
            "uv run python3 -c \"import yt_dlp\"",
            parent_tool_use_id="parent-yt-task-1",
        )
        assert result.get("decision") != "block"

    def test_mixed_prompt_requires_research_delegate_after_youtube_skill(self):
        hooks = self._make_hooks()
        self._set_prompt(
            hooks,
            (
                "Get transcript from https://youtu.be/SpReZZk_13w, then research latest info, "
                "create a report, and email PDF."
            ),
        )
        skill_result = self._call_skill_youtube(hooks)
        assert skill_result.get("decision") != "block"

        non_task_result = _run(
            hooks.on_pre_tool_use_ledger(
                {
                    "tool_name": "mcp__internal__read_text_file",
                    "tool_input": {"path": "work_products/foo.txt"},
                },
                "tool-read-1",
                {},
            )
        )
        assert non_task_result.get("decision") == "block"
        assert "research-specialist" in str(non_task_result.get("systemMessage", ""))

        wrong_task_result = self._call_task(hooks, "report-writer")
        assert wrong_task_result.get("decision") == "block"

        right_task_result = self._call_task(hooks, "research-specialist")
        assert right_task_result.get("decision") != "block"

    def test_mixed_prompt_allows_youtube_expert_before_research_delegate(self):
        hooks = self._make_hooks()
        self._set_prompt(
            hooks,
            (
                "Get transcript from https://youtu.be/SpReZZk_13w, then research latest info, "
                "create a report, and email PDF."
            ),
        )

        youtube_task_result = self._call_task(hooks, "youtube-expert")
        assert youtube_task_result.get("decision") != "block"

        report_task_result = self._call_task(hooks, "report-writer")
        assert report_task_result.get("decision") == "block"

        research_task_result = self._call_task(hooks, "research-specialist")
        assert research_task_result.get("decision") != "block"

    def test_flag_not_set_for_non_youtube_prompt(self):
        hooks = self._make_hooks()
        _run(hooks.on_user_prompt_skill_awareness({"prompt": "summarise this article"}))
        assert hooks._requires_youtube_skill_first is False

    def test_flag_set_for_youtube_prompt(self):
        hooks = self._make_hooks()
        _run(
            hooks.on_user_prompt_skill_awareness(
                {"prompt": "get transcript for https://youtu.be/abc"}
            )
        )
        assert hooks._requires_youtube_skill_first is True

    def test_skill_flag_clears_after_tracking(self):
        hooks = self._make_hooks()
        _run(
            hooks.on_user_prompt_skill_awareness(
                {"prompt": "get transcript for https://youtu.be/abc"}
            )
        )
        assert hooks._youtube_skill_seen_this_turn is False
        self._call_skill_youtube(hooks)
        assert hooks._youtube_skill_seen_this_turn is True


# ---------------------------------------------------------------------------
# 3. on_pre_bash_inject_workspace_env — UV_CACHE_DIR injection
# ---------------------------------------------------------------------------


class TestUvCacheDirInjection:
    def _bash_input(self, command: str) -> dict:
        return {"tool_name": "Bash", "tool_input": {"command": command}}

    def _run_bash_hook(
        self, hooks: AgentHookSet, command: str, workspace: str | None
    ) -> dict:
        with patch(
            "universal_agent.hooks.get_current_workspace", return_value=workspace
        ):
            return _run(
                hooks.on_pre_bash_inject_workspace_env(
                    self._bash_input(command), "tool-bash-1", {}
                )
            )

    def test_injects_uv_cache_dir_when_workspace_available(self):
        hooks = AgentHookSet(run_id="unit-bash-cache")
        result = self._run_bash_hook(
            hooks,
            "uv run scripts/my_script.py",
            "/opt/universal_agent",
        )
        injected_cmd = result.get("tool_input", {}).get("command", "")
        assert "UV_CACHE_DIR=/tmp/uv_cache" in injected_cmd
        assert "uv run scripts/my_script.py" in injected_cmd

    def test_injects_uv_cache_dir_when_workspace_unavailable(self):
        hooks = AgentHookSet(run_id="unit-bash-cache-noworkspace")
        result = self._run_bash_hook(
            hooks,
            "uv run scripts/my_script.py",
            None,
        )
        injected_cmd = (
            result.get("tool_input", {}).get("command", "")
            or result.get("command", "")
        )
        assert "UV_CACHE_DIR=/tmp/uv_cache" in injected_cmd
        assert "uv run scripts/my_script.py" in injected_cmd

    def test_skips_injection_when_already_set(self):
        hooks = AgentHookSet(run_id="unit-bash-cache-already-set")
        result = self._run_bash_hook(
            hooks,
            "UV_CACHE_DIR=/custom/path uv run scripts/my_script.py",
            "/opt/universal_agent",
        )
        injected_cmd = result.get("tool_input", {}).get("command", "")
        assert injected_cmd.count("UV_CACHE_DIR=") <= 1

    def test_no_injection_for_non_uv_command(self):
        hooks = AgentHookSet(run_id="unit-bash-cache-non-uv")
        result = self._run_bash_hook(
            hooks,
            "python scripts/my_script.py",
            "/opt/universal_agent",
        )
        injected_cmd = result.get("tool_input", {}).get("command", "")
        assert "UV_CACHE_DIR" not in injected_cmd

    def test_uv_cache_dir_comes_before_uv_run_in_command(self):
        hooks = AgentHookSet(run_id="unit-bash-cache-order")
        result = self._run_bash_hook(
            hooks,
            "uv run .claude/skills/youtube-transcript-metadata/scripts/fetch.py",
            "/opt/universal_agent",
        )
        injected_cmd = result.get("tool_input", {}).get("command", "")
        cache_pos = injected_cmd.find("UV_CACHE_DIR=")
        uv_run_pos = injected_cmd.find("uv run")
        assert cache_pos != -1
        assert uv_run_pos != -1
        assert cache_pos < uv_run_pos
