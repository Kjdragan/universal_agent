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
    assert "External VP Control Plane" in snapshot
    assert "vp_dispatch_mission" in snapshot
    assert "playwright-bowser-agent" in snapshot
    assert "Task(subagent_type='playwright-bowser-agent'" in snapshot
    assert "playwright-bowser" in snapshot


def test_live_capabilities_snapshot_handles_empty_discovery(monkeypatch):
    monkeypatch.setattr(prompt_assets, "_discover_agent_profiles", lambda _root: [])
    monkeypatch.setattr(prompt_assets, "discover_skills", lambda _skills_dir=None: [])

    snapshot = prompt_assets.build_live_capabilities_snapshot("/tmp/project")

    assert "No specialist agents discovered." in snapshot
    assert "No skills discovered." in snapshot


def test_discover_skills_follows_symlinked_skill_dirs(tmp_path):
    source_skill = tmp_path / "source_skills" / "vp-orchestration"
    source_skill.mkdir(parents=True)
    (source_skill / "SKILL.md").write_text(
        "---\nname: vp-orchestration\ndescription: VP tool-first skill.\n---\n",
        encoding="utf-8",
    )

    linked_root = tmp_path / "linked_skills"
    linked_root.mkdir(parents=True)
    (linked_root / "vp-orchestration").symlink_to(source_skill, target_is_directory=True)

    skills = prompt_assets.discover_skills(str(linked_root))
    names = {item.get("name") for item in skills}
    assert "vp-orchestration" in names


def test_load_capabilities_registry_uses_last_good_when_live_fails(tmp_path, monkeypatch):
    project = tmp_path / "project"
    assets_dir = project / "src" / "universal_agent" / "prompt_assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "capabilities.last_good.md").write_text("LAST GOOD\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    def _raise_live(_root):
        raise RuntimeError("live snapshot failed")

    monkeypatch.setattr(prompt_assets, "build_live_capabilities_snapshot", _raise_live)
    content, source = prompt_assets.load_capabilities_registry(
        str(project),
        workspace_dir=str(workspace),
    )
    assert source == "last_good"
    assert content == "LAST GOOD"


def test_load_capabilities_registry_persists_live_snapshot(tmp_path, monkeypatch):
    project = tmp_path / "project"
    assets_dir = project / "src" / "universal_agent" / "prompt_assets"
    assets_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    monkeypatch.setattr(
        prompt_assets, "build_live_capabilities_snapshot", lambda _root: "LIVE SNAPSHOT"
    )
    content, source = prompt_assets.load_capabilities_registry(
        str(project),
        workspace_dir=str(workspace),
    )
    assert source == "live"
    assert content == "LIVE SNAPSHOT"
    assert (workspace / "capabilities.md").read_text(encoding="utf-8").strip() == "LIVE SNAPSHOT"
    assert (
        (assets_dir / "capabilities.last_good.md").read_text(encoding="utf-8").strip()
        == "LIVE SNAPSHOT"
    )


def test_build_live_capabilities_snapshot_parses_agent_frontmatter(tmp_path):
    project = tmp_path / "project"
    agents_dir = project / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "vp-general.md").write_text(
        "---\nname: vp-general\ndescription: Handles general VP delegation.\n---\n",
        encoding="utf-8",
    )

    snapshot = prompt_assets.build_live_capabilities_snapshot(str(project))
    assert "**vp-general**" in snapshot
    assert "Handles general VP delegation." in snapshot
