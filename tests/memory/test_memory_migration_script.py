from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path


def _load_migration_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "memory_hard_cut_migrate.py"
    spec = importlib.util.spec_from_file_location("memory_hard_cut_migrate", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load memory_hard_cut_migrate module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed_session(session_root: Path, *, transcript: str, memory_note: str) -> None:
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "transcript.md").write_text(transcript, encoding="utf-8")
    (session_root / "MEMORY.md").write_text(memory_note, encoding="utf-8")
    mem_dir = session_root / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "notes.md").write_text(memory_note, encoding="utf-8")


def test_hard_cut_migration_dry_run_and_idempotent_real_run(tmp_path: Path, monkeypatch):
    module = _load_migration_module()
    monkeypatch.setenv("UA_MEMORY_ENABLED", "1")
    monkeypatch.delenv("UA_DISABLE_MEMORY", raising=False)

    workspaces_root = tmp_path / "AGENT_RUN_WORKSPACES"
    shared_root = tmp_path / "Memory_System" / "ua_shared_workspace"
    archive_dir = tmp_path / "archives"
    report_dry = tmp_path / "tmp" / "report_dry.json"
    report_real = tmp_path / "tmp" / "report_real.json"
    report_real_2 = tmp_path / "tmp" / "report_real_2.json"

    _seed_session(
        workspaces_root / "session_alpha",
        transcript="user: remember alpha strategy\nassistant: noted",
        memory_note="# Agent Memory\n\nAlpha strategic context.",
    )

    dry_args = Namespace(
        workspaces_root=str(workspaces_root),
        shared_root=str(shared_root),
        archive_dir=str(archive_dir),
        report_json=str(report_dry),
        dry_run=True,
        delete_legacy=False,
    )
    dry_report = module.run(dry_args)
    assert dry_report["dry_run"] is True
    assert dry_report["stats"]["scanned_session_roots"] == 1
    assert dry_report["stats"]["scanned_memory_files"] >= 2
    assert dry_report["stats"]["inserted_long_term"] >= 1
    assert dry_report["stats"]["indexed_sessions"] == 1
    assert report_dry.exists()

    real_args = Namespace(
        workspaces_root=str(workspaces_root),
        shared_root=str(shared_root),
        archive_dir=str(archive_dir),
        report_json=str(report_real),
        dry_run=False,
        delete_legacy=False,
    )
    first_real = module.run(real_args)
    assert first_real["dry_run"] is False
    assert first_real["stats"]["inserted_long_term"] >= 1
    assert first_real["stats"]["indexed_sessions"] >= 1
    assert report_real.exists()

    second_args = Namespace(
        workspaces_root=str(workspaces_root),
        shared_root=str(shared_root),
        archive_dir=str(archive_dir),
        report_json=str(report_real_2),
        dry_run=False,
        delete_legacy=False,
    )
    second_real = module.run(second_args)
    assert second_real["stats"]["inserted_long_term"] == 0
