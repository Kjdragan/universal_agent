"""
Context Summarization and Checkpointing

This module provides deterministic context summarization for extending agent lifespan
and enabling comparison with Claude's auto-compaction.

Key features:
1. Capture current conversation state before compaction
2. Generate structured, deterministic summaries
3. Save checkpoints to JSON for later injection
4. PreCompact hook integration
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ContextCheckpoint:
    """Structured checkpoint of conversation context."""
    
    # Identification
    checkpoint_id: str
    session_id: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    trigger: str = "manual"  # "manual", "auto", "pre_compact", "phase_boundary"
    
    # Mission context
    original_request: Optional[str] = None
    current_objective: Optional[str] = None
    
    # Progress tracking
    completed_tasks: List[str] = field(default_factory=list)
    pending_tasks: List[str] = field(default_factory=list)
    current_task: Optional[str] = None
    overall_progress_pct: float = 0.0
    
    # Artifacts produced
    artifacts: List[Dict[str, str]] = field(default_factory=list)  # [{path, type, summary}]
    
    # Sub-agent results (critical to preserve)
    subagent_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # Key learnings and decisions
    learnings: List[str] = field(default_factory=list)
    key_decisions: List[str] = field(default_factory=list)
    failed_approaches: List[str] = field(default_factory=list)
    
    # Tool usage summary
    tools_used: List[str] = field(default_factory=list)
    tool_call_count: int = 0
    
    # Context metrics
    context_tokens_estimate: int = 0
    context_utilization_pct: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextCheckpoint":
        return cls(**data)
    
    def to_injection_prompt(self, max_length: int = 4000) -> str:
        """
        Format checkpoint as context injection for new session.
        
        This is a structured, deterministic summary that can be injected
        into a fresh agent context after compaction or phase transition.
        """
        sections = []
        
        # Header
        sections.append("## Context Summary (Deterministic Checkpoint)")
        sections.append(f"*Checkpoint: {self.checkpoint_id} | {self.timestamp}*\n")
        
        # Original request (critical to preserve intent)
        if self.original_request:
            sections.append("### Original Request")
            sections.append(f"> {self.original_request[:500]}...")
            sections.append("")
        
        # Current state
        sections.append("### Current State")
        sections.append(f"- **Progress**: {self.overall_progress_pct:.0f}% complete")
        if self.current_task:
            sections.append(f"- **Current Task**: {self.current_task}")
        if self.current_objective:
            sections.append(f"- **Objective**: {self.current_objective}")
        sections.append("")
        
        # Completed work
        if self.completed_tasks:
            sections.append("### Completed Tasks")
            for task in self.completed_tasks[-5:]:  # Last 5 only
                sections.append(f"- ✅ {task}")
            sections.append("")
        
        # Artifacts (most important for continuity)
        if self.artifacts:
            sections.append("### Produced Artifacts")
            for art in self.artifacts[-10:]:  # Last 10
                path = art.get("path", "unknown")
                summary = art.get("summary", "")[:100]
                sections.append(f"- `{path}`: {summary}")
            sections.append("")
        
        # Sub-agent results (critical - these are lost on compaction)
        if self.subagent_results:
            sections.append("### Sub-Agent Results (Preserved)")
            for result in self.subagent_results[-5:]:
                subagent_type = result.get("subagent_type", "unknown")
                summary = result.get("summary", "")[:200]
                sections.append(f"- **{subagent_type}**: {summary}")
            sections.append("")
        
        # Learnings
        if self.learnings:
            sections.append("### Key Learnings")
            for learning in self.learnings[-5:]:
                sections.append(f"- {learning}")
            sections.append("")
        
        # Failed approaches (prevent repetition)
        if self.failed_approaches:
            sections.append("### Failed Approaches (DO NOT REPEAT)")
            for failure in self.failed_approaches[-3:]:
                sections.append(f"- ❌ {failure}")
            sections.append("")
        
        # Pending work
        if self.pending_tasks:
            sections.append("### Remaining Tasks")
            for task in self.pending_tasks[:5]:  # Next 5
                sections.append(f"- ⏳ {task}")
            sections.append("")
        
        result = "\n".join(sections)
        
        # Truncate if too long
        if len(result) > max_length:
            result = result[:max_length - 50] + "\n\n*[Checkpoint truncated for context limits]*"
        
        return result


class ContextSummarizer:
    """
    Manages deterministic context summarization and checkpointing.
    
    This provides an alternative to Claude's opaque auto-compaction
    by generating structured, predictable summaries.
    """
    
    def __init__(self, workspace_path: Path):
        self.workspace_path = Path(workspace_path)
        self.checkpoints_dir = self.workspace_path / ".urw" / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self._current_checkpoint: Optional[ContextCheckpoint] = None
    
    def create_checkpoint(
        self,
        session_id: str,
        trigger: str = "manual",
        **kwargs
    ) -> ContextCheckpoint:
        """Create a new checkpoint with current context state."""
        checkpoint_id = f"ckpt_{int(time.time())}_{trigger}"
        
        checkpoint = ContextCheckpoint(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            trigger=trigger,
            **kwargs
        )
        
        self._current_checkpoint = checkpoint
        return checkpoint
    
    def save_checkpoint(self, checkpoint: ContextCheckpoint) -> Path:
        """Save checkpoint to JSON file."""
        filename = f"{checkpoint.checkpoint_id}.json"
        filepath = self.checkpoints_dir / filename
        
        with open(filepath, "w") as f:
            json.dump(checkpoint.to_dict(), f, indent=2)
        
        # Also save as "latest" for easy access
        latest_path = self.checkpoints_dir / "latest_checkpoint.json"
        with open(latest_path, "w") as f:
            json.dump(checkpoint.to_dict(), f, indent=2)
        
        return filepath
    
    def load_checkpoint(self, checkpoint_id: Optional[str] = None) -> Optional[ContextCheckpoint]:
        """Load checkpoint from JSON file."""
        if checkpoint_id:
            filepath = self.checkpoints_dir / f"{checkpoint_id}.json"
        else:
            filepath = self.checkpoints_dir / "latest_checkpoint.json"
        
        if not filepath.exists():
            return None
        
        with open(filepath) as f:
            data = json.load(f)
        
        return ContextCheckpoint.from_dict(data)
    
    def list_checkpoints(self) -> List[str]:
        """List all saved checkpoints."""
        return [
            f.stem for f in self.checkpoints_dir.glob("ckpt_*.json")
        ]
    
    def capture_from_state(
        self,
        state_manager: Any,  # URWStateManager
        trigger: str = "manual",
        session_id: str = "unknown"
    ) -> ContextCheckpoint:
        """
        Capture checkpoint from current URW state manager.
        
        This extracts all relevant information from the state manager
        to create a deterministic checkpoint.
        """
        # Get task status
        stats = state_manager.get_completion_stats()
        total = sum(stats.values())
        complete = stats.get("complete", 0)
        progress_pct = (complete / total * 100) if total > 0 else 0
        
        # Get tasks
        current_task = None
        if hasattr(state_manager, "current_task_id"):
            current_task_obj = state_manager.get_task(state_manager.current_task_id)
            if current_task_obj:
                current_task = current_task_obj.title
        
        completed_tasks = []
        pending_tasks = []
        for task in state_manager.get_all_tasks():
            if task.status.value == "complete":
                completed_tasks.append(task.title)
            elif task.status.value == "pending":
                pending_tasks.append(task.title)
        
        # Get artifacts
        artifacts = []
        for artifact in state_manager.get_all_artifacts():
            artifacts.append({
                "path": artifact.file_path or "unknown",
                "type": artifact.artifact_type.value if artifact.artifact_type else "file",
                "summary": artifact.content_preview or ""
            })
        
        # Get learnings from iterations
        learnings = []
        try:
            rows = state_manager.conn.execute(
                "SELECT learnings FROM iterations WHERE learnings IS NOT NULL ORDER BY iteration DESC LIMIT 10"
            ).fetchall()
            for row in rows:
                parsed = json.loads(row["learnings"])
                if parsed:
                    learnings.extend(parsed)
        except Exception:
            pass
        
        # Get failed approaches
        failed_approaches = []
        try:
            rows = state_manager.conn.execute(
                "SELECT approach_description FROM failed_approaches ORDER BY id DESC LIMIT 10"
            ).fetchall()
            for row in rows:
                failed_approaches.append(row["approach_description"])
        except Exception:
            pass
        
        # Get original request from mission
        original_request = None
        try:
            row = state_manager.conn.execute(
                "SELECT original_request FROM mission LIMIT 1"
            ).fetchone()
            if row:
                original_request = row["original_request"]
        except Exception:
            pass
        
        checkpoint = self.create_checkpoint(
            session_id=session_id,
            trigger=trigger,
            original_request=original_request,
            current_task=current_task,
            completed_tasks=completed_tasks,
            pending_tasks=pending_tasks,
            overall_progress_pct=progress_pct,
            artifacts=artifacts,
            learnings=learnings[:7],  # Limit
            failed_approaches=failed_approaches[:5],  # Limit
        )
        
        return checkpoint


# Pre-defined hook for use with Claude Agent SDK
async def pre_compact_checkpoint_hook(
    hook_input: Dict[str, Any],
    summarizer: Optional[ContextSummarizer] = None,
    state_manager: Any = None,
) -> Dict[str, Any]:
    """
    PreCompact hook that captures deterministic checkpoint before Claude compaction.
    
    This should be registered with the agent's hooks configuration.
    
    Usage:
        hooks = {
            "PreCompact": [HookMatcher(matcher="*", hooks=[pre_compact_checkpoint_hook])]
        }
    """
    session_id = hook_input.get("session_id", "unknown")
    trigger = hook_input.get("trigger", "auto")  # "auto" or "manual"
    
    checkpoint = None
    
    if summarizer and state_manager:
        # Capture full checkpoint from state
        checkpoint = summarizer.capture_from_state(
            state_manager=state_manager,
            trigger=f"pre_compact_{trigger}",
            session_id=session_id
        )
        summarizer.save_checkpoint(checkpoint)
        
        # Log checkpoint
        print(f"[PreCompact Hook] Saved checkpoint: {checkpoint.checkpoint_id}")
        print(f"[PreCompact Hook] Progress: {checkpoint.overall_progress_pct:.0f}%")
        print(f"[PreCompact Hook] Artifacts: {len(checkpoint.artifacts)}")
    
    # Return continue signal - let compaction proceed
    # Optionally inject a system message with our checkpoint summary
    result: Dict[str, Any] = {"continue_": True}
    
    if checkpoint:
        # Inject our deterministic summary as a system message
        # This gives Claude additional context before its own compaction
        result["systemMessage"] = checkpoint.to_injection_prompt(max_length=2000)
    
    return result
