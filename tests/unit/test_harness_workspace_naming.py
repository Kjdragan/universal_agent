from pathlib import Path

from universal_agent.urw.harness_helpers import toggle_session
from universal_agent.urw.harness_session import HarnessSessionManager


def test_harness_session_manager_uses_run_phase_workspace_names(tmp_path: Path):
    manager = HarnessSessionManager(tmp_path, harness_id="20260324_010203")
    harness_dir = manager.create_harness_dir()

    phase_path = manager.next_phase_session()

    assert phase_path == harness_dir / "run_phase_1"
    assert phase_path.exists()
    assert manager.get_prior_session_paths() == []


def test_toggle_session_creates_run_phase_workspace(tmp_path: Path):
    workspace = Path(toggle_session(tmp_path, 2))

    assert workspace == tmp_path / "run_phase_2"
    assert (workspace / "work_products" / "media").exists()
    assert (workspace / "downloads").exists()
    assert (workspace / "search_results").exists()
