from universal_agent import prompt_assets


def test_live_capabilities_snapshot_includes_bowser_policy(monkeypatch):
    monkeypatch.setattr(
        prompt_assets,
        "_discover_agent_profiles",
        lambda _root: [
            {
                "name": "playwright-bowser-agent",
                "description": "Parallel browser automation agent.",
            },
            {
                "name": "research-specialist",
                "description": "Research pipeline agent.",
            },
        ],
    )
    monkeypatch.setattr(
        prompt_assets,
        "discover_skills",
        lambda _skills_dir=None: [
            {
                "name": "playwright-bowser",
                "description": "Headless browser automation skill.",
                "path": "/tmp/.claude/skills/playwright-bowser/SKILL.md",
            }
        ],
    )

    snapshot = prompt_assets.build_live_capabilities_snapshot("/tmp/project")

    assert "Bowser-first" in snapshot
    assert "playwright-bowser-agent" in snapshot
    assert "Task(subagent_type='playwright-bowser-agent'" in snapshot
    assert "playwright-bowser" in snapshot


def test_live_capabilities_snapshot_handles_empty_discovery(monkeypatch):
    monkeypatch.setattr(prompt_assets, "_discover_agent_profiles", lambda _root: [])
    monkeypatch.setattr(prompt_assets, "discover_skills", lambda _skills_dir=None: [])

    snapshot = prompt_assets.build_live_capabilities_snapshot("/tmp/project")

    assert "No specialist agents discovered." in snapshot
    assert "No skills discovered." in snapshot
