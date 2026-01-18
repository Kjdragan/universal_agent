"""
URW Harness Session Manager

Manages harness directory structure and phase sessions for massive query processing.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class HarnessSessionManager:
    """Manages harness directory structure and phase sessions.
    
    Directory structure:
        AGENT_RUN_WORKSPACES/
        └── harness_YYYYMMDD_HHMMSS/
            ├── harness_state.json     # Phase tracking, status
            ├── macro_tasks.json       # Decomposed phases
            ├── session_phase_1/       # Phase 1 work products
            ├── session_phase_2/       # Phase 2 work products
            └── ...
    """

    def __init__(self, workspaces_root: Path, harness_id: Optional[str] = None):
        """
        Args:
            workspaces_root: Path to AGENT_RUN_WORKSPACES directory
            harness_id: Optional existing harness ID to resume
        """
        self.workspaces_root = Path(workspaces_root)
        
        if harness_id:
            self.harness_id = harness_id
            self.harness_dir = self.workspaces_root / f"harness_{harness_id}"
        else:
            self.harness_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.harness_dir = self.workspaces_root / f"harness_{self.harness_id}"
        
        self._state: Dict[str, Any] = {}
        self._current_phase: int = 0
        self._total_phases: int = 0
        self._phase_sessions: List[Path] = []

    def create_harness_dir(self) -> Path:
        """Create harness directory structure on first /harness command."""
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize state file
        self._state = {
            "harness_id": self.harness_id,
            "created_at": datetime.now().isoformat(),
            "status": "initialized",
            "current_phase": 0,
            "total_phases": 0,
            "phases": [],
        }
        self._save_state()
        
        return self.harness_dir

    def set_phases(self, phases: List[Dict[str, Any]]) -> None:
        """Set the phases from decomposition."""
        self._total_phases = len(phases)
        self._state["total_phases"] = self._total_phases
        self._state["phases"] = [
            {
                "phase_id": i + 1,
                "title": p.get("title", f"Phase {i + 1}"),
                "status": "pending",
                "session_path": None,
            }
            for i, p in enumerate(phases)
        ]
        self._save_state()

    def next_phase_session(self) -> Path:
        """Create new session directory for next phase."""
        self._current_phase += 1
        
        session_name = f"session_phase_{self._current_phase}"
        session_path = self.harness_dir / session_name
        session_path.mkdir(parents=True, exist_ok=True)
        
        self._phase_sessions.append(session_path)
        
        # Update state
        self._state["current_phase"] = self._current_phase
        if self._current_phase <= len(self._state.get("phases", [])):
            self._state["phases"][self._current_phase - 1]["status"] = "in_progress"
            self._state["phases"][self._current_phase - 1]["session_path"] = str(session_path)
        self._save_state()
        
        return session_path

    def get_prior_session_paths(self) -> List[str]:
        """Returns list of completed phase session paths for context injection."""
        return [str(p) for p in self._phase_sessions[:-1]]  # Exclude current

    def mark_phase_complete(self, phase_num: int, success: bool = True) -> None:
        """Mark a phase as complete."""
        if phase_num <= len(self._state.get("phases", [])):
            self._state["phases"][phase_num - 1]["status"] = "complete" if success else "failed"
            self._state["phases"][phase_num - 1]["completed_at"] = datetime.now().isoformat()
        self._save_state()

    def build_phase_prompt(
        self,
        phase_num: int,
        phase_title: str,
        phase_instructions: str,
        expected_artifacts: List[str],
    ) -> str:
        """Build phase prompt with minimal context + perspective."""
        prior_paths = self.get_prior_session_paths()
        prior_section = ""
        if prior_paths:
            prior_section = f"""**Prior work:** Sessions at paths:
{chr(10).join(f"- {p}" for p in prior_paths)}
(Consult ONLY if needed for continuity)

"""
        
        artifacts_section = "\n".join(f"- {a}" for a in expected_artifacts)
        
        return f"""# Phase {phase_num} of {self._total_phases}: {phase_title}

You are working through a larger multi-phase project. Your current phase is this one.
Complete this phase excellently so the system can continue to subsequent phases.

{prior_section}## Your Task
{phase_instructions}

## Expected Outputs
{artifacts_section}
"""

    def get_current_phase(self) -> int:
        """Get current phase number."""
        return self._current_phase

    def get_total_phases(self) -> int:
        """Get total number of phases."""
        return self._total_phases

    def is_complete(self) -> bool:
        """Check if all phases are complete."""
        phases = self._state.get("phases", [])
        return all(p.get("status") == "complete" for p in phases)

    def _save_state(self) -> None:
        """Save state to harness_state.json."""
        state_path = self.harness_dir / "harness_state.json"
        state_path.write_text(json.dumps(self._state, indent=2))

    def _load_state(self) -> None:
        """Load state from harness_state.json."""
        state_path = self.harness_dir / "harness_state.json"
        if state_path.exists():
            self._state = json.loads(state_path.read_text())
            self._current_phase = self._state.get("current_phase", 0)
            self._total_phases = self._state.get("total_phases", 0)

    @classmethod
    def resume(cls, workspaces_root: Path, harness_id: str) -> "HarnessSessionManager":
        """Resume an existing harness session."""
        manager = cls(workspaces_root, harness_id=harness_id)
        manager._load_state()
        return manager
