"""P4 guard: the Demo build contract — framework-per-video rule, the
functional-completeness acceptance bar, and ZAI inference wiring for
Claude-Agent-SDK demos — is pinned on both contract surfaces (string-pin
style, mirroring test_tutorial_teaching_doc_only.py for P3):

1. `.claude/skills/cody-implements-from-brief/SKILL.md` — the cody_demo_task
   / demo-mission contract Cody reads.
2. `proactive_tutorial_builds._build_task_description` — the BRIEF embedded
   in every `tutorial_build` Task Hub row Simone forwards to Cody verbatim.

Also pins the P4 housekeeping: vp/profiles.py no longer claims the coder
defaults to anthropic while the code says zai.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from universal_agent import task_hub
from universal_agent.services.proactive_tutorial_builds import (
    DEMO_BUILD_CONTRACT,
    queue_tutorial_build_task,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL = (
    _REPO_ROOT / ".claude" / "skills" / "cody-implements-from-brief" / "SKILL.md"
).read_text(encoding="utf-8")


# ── Surface 1: the skill markdown ───────────────────────────────────────────

def test_skill_md_contains_framework_selection_rule():
    assert "## Framework selection (per video)" in _SKILL
    assert "that native stack" in _SKILL
    assert "not a fallback" in _SKILL
    assert "Claude Agent SDK" in _SKILL
    assert "ONLY on explicit operator direction" in _SKILL
    assert "never blocks demo-worthiness" in _SKILL


def test_skill_md_contains_functional_completeness_acceptance():
    assert "functional completeness, not looks" in _SKILL
    assert "zero effort on design polish" in _SKILL
    assert "FULLY exercise the capability" in _SKILL
    assert "learning/reference library" in _SKILL


def test_skill_md_contains_zai_wiring_and_no_stale_scrub_default():
    assert "ANTHROPIC_BASE_URL" in _SKILL
    assert "ANTHROPIC_AUTH_TOKEN" in _SKILL
    assert "currently BROKEN" in _SKILL
    assert "NEVER hardcode" in _SKILL
    # Pre-P4 statements that contradict the ZAI-wired contract must be gone.
    assert "scrub_env=True,  # default" not in _SKILL
    assert "accidentally hit ZAI" not in _SKILL


# ── Surface 2: the tutorial_build BRIEF (Python-composed) ───────────────────

def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_tutorial_build_brief_embeds_demo_build_contract(tmp_path):
    with _connect(tmp_path / "activity.db") as conn:
        result = queue_tutorial_build_task(
            conn,
            video_id="p4contract",
            video_title="Build agents with Google ADK",
            video_url="https://youtube.test/watch?v=p4contract",
            channel_name="AI Builder",
            extraction_plan={"language": "python"},
        )
        desc = task_hub.get_item(conn, result["task"]["task_id"])["description"]

    # The full contract block rides in verbatim.
    assert DEMO_BUILD_CONTRACT.rstrip() in desc
    # Framework rule hooks.
    assert "THAT native stack" in desc
    assert "Claude Agent SDK" in desc
    assert "ONLY on explicit operator direction" in desc
    assert "never blocks demo-worthiness" in desc
    # Acceptance bar.
    assert "functional completeness" in desc.lower()
    # ZAI wiring — env var NAMES only.
    assert "ANTHROPIC_BASE_URL" in desc
    assert "ANTHROPIC_AUTH_TOKEN" in desc
    # P0-P3 invariants stay intact (same pins as test_proactive_tutorial_builds).
    assert "private by default" in desc.lower()
    assert "public publication is not allowed" in desc.lower()
    # Simone must forward the contract verbatim into the mission objective.
    assert "VERBATIM" in desc


def test_contract_contains_no_secret_values():
    assert "sk-ant-" not in DEMO_BUILD_CONTRACT
    assert "Bearer " not in DEMO_BUILD_CONTRACT


# ── P4 housekeeping: profiles.py comment matches the code ───────────────────

def test_profiles_comment_not_stale_and_coder_is_zai():
    from universal_agent.vp.profiles import get_vp_profile

    src = (
        _REPO_ROOT / "src" / "universal_agent" / "vp" / "profiles.py"
    ).read_text(encoding="utf-8")
    assert '(coder) defaults to "anthropic"' not in src
    coder = get_vp_profile("vp.coder.primary")
    assert coder is not None and coder.inference_mode == "zai"
