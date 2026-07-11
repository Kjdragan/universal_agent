"""Regression tests: paper_to_podcast discovery is LOCAL-INDEX-FIRST.

The 2026-07-10 run (run_id 78c38721000a) died at step one when its single
live ``search_papers`` call hit arXiv's server-side per-IP HTTP 429. The fix
(services/arxiv_local_index.py) moves discovery to a local OAI-PMH-harvested
index, keeps live search only as a fallback, and adds a deterministic
cache-fallback last resort. These tests pin the wiring — the cron prompt and
the skill instructions — so a future edit cannot silently reintroduce
live-search-first discovery.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.gateway_server import _paper_to_podcast_command

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_PATH = _REPO_ROOT / ".claude" / "skills" / "paper-to-podcast-tf" / "SKILL.md"


class TestCronPromptWiring:
    def test_prompt_puts_local_index_before_live_search(self):
        prompt = _paper_to_podcast_command()
        index_pos = prompt.find("arxiv_local_index search")
        live_pos = prompt.find("mcp__arxiv-mcp-server__search_papers")
        assert index_pos != -1, "prompt must name the local index search CLI"
        assert live_pos != -1, "live search must remain as the fallback"
        assert index_pos < live_pos, "local index must come before live search"

    def test_prompt_names_cache_fallback_as_429_recovery(self):
        prompt = _paper_to_podcast_command()
        assert "arxiv_local_index cache-fallback" in prompt

    def test_prompt_covers_mcp_transport_death(self):
        # 2026-07-11: the arxiv-mcp-server stdio subprocess died with
        # "MCP error -32000: Connection closed" and the run ended after 63s
        # with no retry and no fallback. The prompt must name this failure
        # shape so the agent treats it like a 429 (retry once, then fall back)
        # instead of abandoning the run.
        prompt = _paper_to_podcast_command()
        assert "-32000" in prompt and "Connection closed" in prompt

    def test_prompt_still_forbids_raw_arxiv_clients(self):
        prompt = _paper_to_podcast_command()
        assert "curl/wget" in prompt
        assert "pip install" in prompt


class TestSkillWiring:
    def test_skill_discovery_is_local_index_first(self):
        text = _SKILL_PATH.read_text(encoding="utf-8")
        index_pos = text.find("arxiv_local_index search")
        live_pos = text.find("mcp__arxiv-mcp-server__search_papers")
        assert index_pos != -1
        assert live_pos != -1
        assert index_pos < live_pos

    def test_skill_keeps_offline_cache_fallback(self):
        text = _SKILL_PATH.read_text(encoding="utf-8")
        assert "cache-fallback" in text
        # The zero-papers fail-loud contract must survive the rewrite.
        assert "FAILURE.txt" in text
