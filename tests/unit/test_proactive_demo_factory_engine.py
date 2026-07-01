"""PR-2 guards: proactive tutorial_build → demo_factory engine routing + naming.

Component A: ``proactive_tutorial_builds._build_task_description`` appends the
DEMO ENGINE OVERRIDE block (the demo_factory ``build_demo.py`` driver, a FULL
land — no ``--build-only``) when ``UA_PROACTIVE_DEMO_ENGINE`` is on, and is
byte-for-byte the bespoke objective when it is off.

Component B: ``tutorial_demo_finalize`` renames ``demo-proactive-<slug>`` →
``demo-undemoable-<slug>`` on a conceptual land, keeps it on a demoable land,
and the demo_factory output dir the VP worker prepends as the first
workspace_candidate is computed from the SAME title-only slug the build-time
``--slug`` used.
"""

from __future__ import annotations

import json
from pathlib import Path

from universal_agent.services.proactive_tutorial_builds import _build_task_description
from universal_agent.services.tutorial_demo_finalize import (
    finalize_tutorial_build_demo,
    proactive_demo_slug,
)

# ── Component A: engine-routing override in the objective ────────────────────

def _desc(**over) -> str:
    kwargs = {
        "video_title": "Build an Agent with Google ADK & Gemini!",
        "video_url": "https://youtu.be/abc123",
        "channel_name": "DevChannel",
        "extraction_plan": {"language": "python"},
    }
    kwargs.update(over)
    return _build_task_description(**kwargs)


def test_override_present_when_flag_on(monkeypatch):
    monkeypatch.setenv("UA_PROACTIVE_DEMO_ENGINE", "1")
    desc = _desc()
    slug = proactive_demo_slug("Build an Agent with Google ADK & Gemini!")

    assert "DEMO ENGINE OVERRIDE: demo_factory" in desc
    assert "build_demo.py" in desc
    # The driver runs UNDER the demo_factory uv venv (google-genai is imported at
    # the eval stage; bare /usr/bin/python3 lacks it), NOT bare python3.
    assert (
        "uv run --project /home/ua/lrepos/demo_factory python "
        "/home/ua/lrepos/demo_factory/scripts/build_demo.py" in desc
    )
    assert "python3 /home/ua/lrepos/demo_factory" not in desc
    # full land — the driver is invoked WITHOUT --build-only
    assert "--build-only" not in desc
    # distinguishable naming: --slug proactive-<slug> + --demo-id proactive-<slug>
    assert f"--slug proactive-{slug}" in desc
    assert f"--demo-id proactive-{slug}" in desc
    # the seed URL rides along, and the deterministic on-disk dir is documented
    assert "--seed-url https://youtu.be/abc123" in desc
    assert f"/home/ua/lrepos/demo-proactive-{slug}" in desc
    # full-land flags from the design spec
    assert "--workspace-root /home/ua/lrepos" in desc
    assert "--endpoint-required any" in desc
    assert "--promote" in desc
    assert "--skill-tier library" in desc


def test_override_absent_when_flag_off(monkeypatch):
    monkeypatch.delenv("UA_PROACTIVE_DEMO_ENGINE", raising=False)
    monkeypatch.delenv("UA_DISABLE_PROACTIVE_DEMO_ENGINE", raising=False)
    desc = _desc()
    assert "DEMO ENGINE OVERRIDE" not in desc
    assert "build_demo.py" not in desc
    assert "--slug proactive-" not in desc
    # the bespoke objective is intact
    assert "Cody should build a runnable demo" in desc


def test_override_disable_flag_wins(monkeypatch):
    monkeypatch.setenv("UA_PROACTIVE_DEMO_ENGINE", "1")
    monkeypatch.setenv("UA_DISABLE_PROACTIVE_DEMO_ENGINE", "1")
    assert "DEMO ENGINE OVERRIDE" not in _desc()


def test_override_omits_seed_url_when_absent(monkeypatch):
    monkeypatch.setenv("UA_PROACTIVE_DEMO_ENGINE", "1")
    desc = _desc(video_url="")
    assert "DEMO ENGINE OVERRIDE" in desc
    assert "--seed-url" not in desc


def test_override_double_quotes_in_title_sanitized(monkeypatch):
    monkeypatch.setenv("UA_PROACTIVE_DEMO_ENGINE", "1")
    desc = _desc(video_title='He said "hello" to ADK')
    # the embedded --title / seed must not contain a raw double quote that would
    # break the shell command Cody runs
    override = desc.split("DEMO ENGINE OVERRIDE", 1)[1]
    title_line = next(ln for ln in override.splitlines() if "--title" in ln)
    # only the two wrapping quotes of --title "..."; the inner " were sanitized
    assert title_line.count('"') == 2, title_line


# ── Component B: finalize rename + naming ────────────────────────────────────

def _write_landed_demo(root: Path, slug: str, *, status: str, acc: bool) -> Path:
    d = root / f"demo-proactive-{slug}"
    d.mkdir(parents=True)
    (d / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (d / "README.md").write_text("## Run\nuv run python main.py\n", encoding="utf-8")
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "demo_id": f"proactive-{slug}",
                "feature": "F",
                "status": status,
                "acceptance_passed": acc,
                "endpoint_hit": "zai",
                "ts": "2026-06-30T10:00:00",
                "exhibit_url": "https://exhibit",
            }
        ),
        encoding="utf-8",
    )
    return d


def _finalize(ws: Path, root: Path) -> dict:
    return finalize_tutorial_build_demo(
        task_id="tutorial-build:pr2",
        task_meta={"video_title": "Cap", "video_id": "vid"},
        mission={"payload_json": "{}", "started_at": ""},
        mission_id="vp-mission-pr2abcd",
        workspace_candidates=[
            str(root / "demo-proactive-cap"),
            str(root / "demo-undemoable-cap"),
            str(ws),
        ],
    )


def test_finalize_keeps_proactive_dir_when_demoable(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    root = tmp_path / "lrepos"
    d = _write_landed_demo(root, "cap", status="demoed", acc=True)

    result = _finalize(d, root)
    assert result["ok"] is True
    assert result.get("undemoable") is None
    assert result["workspace_dir"].endswith("demo-proactive-cap")
    assert d.exists()
    # key-alias for the gateway walker
    mani = json.loads((d / "manifest.json").read_text())
    assert mani["timestamp"] == "2026-06-30T10:00:00"
    assert mani["marker_verified"] is True


def test_finalize_renames_to_undemoable_when_conceptual(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    root = tmp_path / "lrepos"
    d = _write_landed_demo(root, "cap", status="un-demoable", acc=False)

    result = _finalize(d, root)
    assert result["ok"] is True
    assert result.get("undemoable") is True
    assert result["workspace_dir"].endswith("demo-undemoable-cap")
    assert not d.exists(), "the demo-proactive dir must be renamed away"
    renamed = root / "demo-undemoable-cap"
    assert renamed.is_dir()
    mani = json.loads((renamed / "manifest.json").read_text())
    assert mani["marker_verified"] is False


def test_finalize_undemoable_rename_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    root = tmp_path / "lrepos"
    d = _write_landed_demo(root, "cap", status="un-demoable", acc=False)
    _finalize(d, root)  # first run renames

    # second run: the demo-proactive candidate no longer exists; the
    # demo-undemoable candidate resolves and is NOT re-renamed.
    result = finalize_tutorial_build_demo(
        task_id="tutorial-build:pr2",
        task_meta={"video_title": "Cap", "video_id": "vid"},
        mission={"payload_json": "{}", "started_at": ""},
        mission_id="vp-mission-pr2abcd",
        workspace_candidates=[
            str(root / "demo-proactive-cap"),
            str(root / "demo-undemoable-cap"),
        ],
    )
    assert result["ok"] is True
    assert result["workspace_dir"].endswith("demo-undemoable-cap")
    assert (root / "demo-undemoable-cap").is_dir()


def test_finalize_leaves_bespoke_workspace_untouched(tmp_path, monkeypatch):
    """A bespoke mission workspace (no landed status manifest) is never renamed."""
    monkeypatch.setenv("UA_DEMOS_ROOT", str(tmp_path / "demos_root"))
    ws = tmp_path / "mission-workspace-xyz"
    ws.mkdir()
    (ws / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (ws / "README.md").write_text("## Run\nuv run python main.py\n", encoding="utf-8")

    result = finalize_tutorial_build_demo(
        task_id="tutorial-build:pr2",
        task_meta={"video_title": "Cap", "video_id": "vid"},
        mission={"payload_json": "{}", "started_at": ""},
        mission_id="vp-mission-pr2abcd",
        workspace_candidates=[str(ws)],
    )
    assert result["ok"] is True
    assert result.get("undemoable") is None
    assert ws.exists()
    assert result["workspace_dir"] == str(ws)


def test_worker_candidate_dir_matches_build_slug():
    """The VP worker prepends /home/ua/lrepos/demo-proactive-<slug> as the first
    workspace_candidate, computed from the SAME title-only slug the build-time
    --slug used — so finalize resolves the repo the driver actually created."""
    title = "Build an Agent with Google ADK & Gemini!"
    slug = proactive_demo_slug(title)
    # build side (the objective embeds the on-disk dir)
    import os

    os.environ["UA_PROACTIVE_DEMO_ENGINE"] = "1"
    try:
        desc = _build_task_description(
            video_title=title, video_url="", channel_name="", extraction_plan={}
        )
    finally:
        os.environ.pop("UA_PROACTIVE_DEMO_ENGINE", None)
    build_dir = f"/home/ua/lrepos/demo-proactive-{slug}"
    assert build_dir in desc
    # finalize side (worker_loop computes this exact string)
    worker_candidate = f"/home/ua/lrepos/demo-proactive-{proactive_demo_slug(title)}"
    assert worker_candidate == build_dir
