from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / ".agents"
    / "skills"
    / "project-scaffolder"
    / "scripts"
    / "scaffold.py"
)


def load_scaffold_module():
    spec = importlib.util.spec_from_file_location("project_scaffold", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_infrastructure_templates_use_actual_project_dir(tmp_path: Path):
    scaffold = load_scaffold_module()
    project_dir = tmp_path / "sample-project"
    project_dir.mkdir()
    variables = {
        "PROJECT_NAME": "sample-project",
        "PROJECT_MODULE": "sample_project",
        "PROJECT_DESCRIPTION": "Sample project",
        "PROJECT_DIR": str(project_dir),
        "DNS_NAME": "sample-project",
        "BACKEND_PORT": "8010",
        "FRONTEND_PORT": "3010",
        "DB_PORT": "5433",
        "DOCS_PORT": "8100",
        "INFISICAL_PROJECT_ID": "project-id",
        "CREATED_DATE": "2026-04-29",
    }

    scaffold.render_and_write_templates(project_dir, variables)

    api_service = (project_dir / "_systemd" / "sample-project-api.service").read_text()
    db_service = (project_dir / "_systemd" / "sample-project-db.service").read_text()
    web_service = (project_dir / "_systemd" / "sample-project-web.service").read_text()
    docs_service = (
        project_dir / "_systemd" / "sample-project-docs.service"
    ).read_text()
    nginx_conf = (project_dir / "_nginx" / "sample-project.conf").read_text()
    deploy_yml = (project_dir / ".github" / "workflows" / "deploy.yml").read_text()

    assert f"WorkingDirectory={project_dir}/backend" in api_service
    assert f"WorkingDirectory={project_dir}" in db_service
    assert f"WorkingDirectory={project_dir}/frontend" in web_service
    assert f"WorkingDirectory={project_dir}" in docs_service
    assert f"root {project_dir}/frontend/dist;" in nginx_conf
    assert f"cd {project_dir}" in deploy_yml
    assert "/opt/sample-project" not in "\n".join(
        [api_service, db_service, web_service, docs_service, nginx_conf, deploy_yml]
    )


def test_skill_symlinks_use_agents_dir_with_legacy_fallback(
    tmp_path: Path, monkeypatch
):
    scaffold = load_scaffold_module()
    source_root = tmp_path / "ua" / ".agents" / "skills"
    legacy_root = tmp_path / "ua" / ".claude" / "skills"
    source_root.mkdir(parents=True)
    legacy_root.mkdir(parents=True)
    (source_root / "clean-code").mkdir()
    (legacy_root / "deep-research").mkdir()
    project_dir = tmp_path / "project"
    scaffold.create_directory_structure(project_dir, "sample_project")

    monkeypatch.setattr(scaffold, "CURATED_SKILLS", ["clean-code", "deep-research"])
    monkeypatch.setattr(scaffold, "UA_SKILL_SOURCE_DIRS", [source_root, legacy_root])

    scaffold.symlink_skills(project_dir)

    assert (project_dir / ".agents" / "skills" / "clean-code").is_symlink()
    assert (project_dir / ".agents" / "skills" / "deep-research").is_symlink()
    assert not (project_dir / ".claude" / "skills").exists()


def test_static_files_include_project_manifest_and_preflight(tmp_path: Path):
    scaffold = load_scaffold_module()
    project_dir = tmp_path / "sample-project"
    variables = {
        "PROJECT_NAME": "sample-project",
        "PROJECT_MODULE": "sample_project",
        "PROJECT_DESCRIPTION": "Sample project",
        "PROJECT_DIR": str(project_dir),
        "DNS_NAME": "sample-project",
        "BACKEND_PORT": "8010",
        "FRONTEND_PORT": "3010",
        "DB_PORT": "5433",
        "DOCS_PORT": "8100",
        "INFISICAL_PROJECT_ID": "project-id",
        "CREATED_DATE": "2026-04-29",
    }
    scaffold.create_directory_structure(project_dir, "sample_project")

    scaffold.write_static_files(project_dir, variables)

    manifest = json.loads((project_dir / "project.scaffold.json").read_text())
    preflight = project_dir / "scripts" / "preflight.sh"
    secrets_doc = (
        project_dir / "docs" / "deployment" / "secrets_and_environments.md"
    ).read_text()

    assert manifest["infisical"]["project_id"] == "project-id"
    assert manifest["ports"]["backend"] == 8010
    assert "infisical" in manifest["dependencies"]["host_clis"]
    assert preflight.exists()
    assert preflight.stat().st_mode & 0o111
    assert "check infisical infisical --version" in preflight.read_text()
    assert "Infisical project ID: `project-id`" in secrets_doc


def test_initialize_git_creates_main_branch_initial_commit(tmp_path: Path):
    scaffold = load_scaffold_module()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("# Project\n")

    scaffold.initialize_git(project_dir)

    branch = scaffold.subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=project_dir,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    commit = scaffold.subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=project_dir,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    assert branch == "main"
    assert commit == "chore: initial scaffold"
