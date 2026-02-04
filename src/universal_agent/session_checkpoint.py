"""
Session Checkpoint Generator

Generates consolidated session checkpoints on run completion for context 
continuity across fresh sessions. Used by Telegram bot and Web UI.

Checkpoint captures:
- Original request (intent preservation)
- Completed tasks (what was done)
- Artifacts produced (with paths and descriptions)
- Sub-agent results (critical - lost on context clear)
- Key decisions/learnings
- Failed approaches (prevent repetition)
- Tool stats
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SessionCheckpoint:
    """Consolidated session checkpoint for context continuity."""
    
    # Identification
    session_id: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    # Original intent
    original_request: str = ""
    
    # Completed work
    completed_tasks: List[str] = field(default_factory=list)
    
    # Artifacts with descriptions
    artifacts: List[Dict[str, str]] = field(default_factory=list)
    # Format: [{"path": "work_products/report.html", "description": "HTML report"}]
    
    # Sub-agent results (critical to preserve)
    subagent_results: List[Dict[str, Any]] = field(default_factory=list)
    # Format: [{"subagent_type": "research-specialist", "summary": "..."}]
    
    # Key decisions made during run
    key_decisions: List[str] = field(default_factory=list)
    
    # Failed approaches (to avoid repetition)
    failed_approaches: List[str] = field(default_factory=list)
    
    # Stats
    tool_call_count: int = 0
    execution_time_seconds: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionCheckpoint":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    def to_markdown(self, max_length: int = 4000) -> str:
        """
        Format checkpoint as markdown for injection into new session.
        """
        sections = []
        
        # Header
        sections.append("## Session Checkpoint")
        sections.append(f"*Session: {self.session_id} | {self.timestamp}*\n")
        
        # Original request
        if self.original_request:
            sections.append("### Original Request")
            request_preview = self.original_request[:500]
            if len(self.original_request) > 500:
                request_preview += "..."
            sections.append(f"> {request_preview}")
            sections.append("")
        
        # Completed tasks
        if self.completed_tasks:
            sections.append("### Completed Tasks")
            for task in self.completed_tasks[-10:]:  # Last 10
                sections.append(f"- ✅ {task}")
            sections.append("")
        
        # Artifacts produced
        if self.artifacts:
            sections.append("### Artifacts Produced")
            for art in self.artifacts[-10:]:  # Last 10
                path = art.get("path", "unknown")
                desc = art.get("description", "")[:100]
                sections.append(f"- `{path}`: {desc}")
            sections.append("")
        
        # Sub-agent results
        if self.subagent_results:
            sections.append("### Sub-Agent Results")
            for result in self.subagent_results[-5:]:  # Last 5
                agent_type = result.get("subagent_type", "unknown")
                summary = result.get("summary", "")[:200]
                sections.append(f"- **{agent_type}**: {summary}")
            sections.append("")
        
        # Key decisions
        if self.key_decisions:
            sections.append("### Key Decisions")
            for decision in self.key_decisions[-5:]:
                sections.append(f"- {decision}")
            sections.append("")
        
        # Failed approaches
        if self.failed_approaches:
            sections.append("### Failed Approaches (DO NOT REPEAT)")
            for failure in self.failed_approaches[-3:]:
                sections.append(f"- ❌ {failure}")
            sections.append("")
        
        # Stats
        if self.tool_call_count > 0 or self.execution_time_seconds > 0:
            sections.append("### Tool Stats")
            stats_parts = []
            if self.tool_call_count > 0:
                stats_parts.append(f"{self.tool_call_count} tool calls")
            if self.execution_time_seconds > 0:
                stats_parts.append(f"{self.execution_time_seconds:.1f}s execution")
            sections.append(f"- {' | '.join(stats_parts)}")
            sections.append("")
        
        result = "\n".join(sections)
        
        # Truncate if too long
        if len(result) > max_length:
            result = result[:max_length - 60] + "\n\n*[Checkpoint truncated for context limits]*"
        
        return result


class SessionCheckpointGenerator:
    """
    Generates session checkpoints from run results.
    
    Usage:
        generator = SessionCheckpointGenerator(workspace_path)
        checkpoint = generator.generate_from_result(session_id, result)
        generator.save(checkpoint)
        
        # Later, on new session:
        checkpoint = generator.load_latest()
        if checkpoint:
            inject_context(checkpoint.to_markdown())
    """
    
    CHECKPOINT_FILENAME = "session_checkpoint.json"
    CHECKPOINT_MARKDOWN_FILENAME = "session_checkpoint.md"
    
    def __init__(self, workspace_path: Path | str):
        self.workspace_path = Path(workspace_path)
    
    def generate_from_result(
        self,
        session_id: str,
        original_request: str,
        result: Any,
        trace_path: Optional[Path] = None,
    ) -> SessionCheckpoint:
        """
        Generate checkpoint from execution result.
        
        Args:
            session_id: The session identifier
            original_request: The user's original query
            result: The execution result object (TaskResult or similar)
            trace_path: Optional path to trace.json for additional context
        """
        checkpoint = SessionCheckpoint(
            session_id=session_id,
            original_request=original_request,
        )
        
        # Extract from result object
        if hasattr(result, "tool_calls"):
            checkpoint.tool_call_count = result.tool_calls
        if hasattr(result, "execution_time_seconds"):
            checkpoint.execution_time_seconds = result.execution_time_seconds
        
        # Scan workspace for artifacts
        checkpoint.artifacts = self._scan_artifacts()
        
        # Extract sub-agent results from subagent_outputs directory
        checkpoint.subagent_results = self._extract_subagent_results()
        
        # Extract completed tasks from trace if available
        if trace_path and trace_path.exists():
            checkpoint.completed_tasks = self._extract_tasks_from_trace(trace_path)
        else:
            # Try default trace location
            default_trace = self.workspace_path / "trace.json"
            if default_trace.exists():
                checkpoint.completed_tasks = self._extract_tasks_from_trace(default_trace)
        
        # Extract learnings/decisions from memory files if present
        checkpoint.key_decisions = self._extract_key_decisions()
        
        return checkpoint
    
    def _scan_artifacts(self) -> List[Dict[str, str]]:
        """Scan workspace for produced artifacts."""
        artifacts = []
        
        # Check work_products
        work_products = self.workspace_path / "work_products"
        if work_products.exists():
            for f in work_products.rglob("*"):
                if f.is_file() and not f.name.startswith("."):
                    rel_path = str(f.relative_to(self.workspace_path))
                    desc = self._infer_artifact_description(f)
                    artifacts.append({"path": rel_path, "description": desc})
        
        # Check tasks directory for outputs
        tasks_dir = self.workspace_path / "tasks"
        if tasks_dir.exists():
            for task_dir in tasks_dir.iterdir():
                if task_dir.is_dir():
                    for f in task_dir.iterdir():
                        if f.is_file() and not f.name.startswith("."):
                            rel_path = str(f.relative_to(self.workspace_path))
                            desc = self._infer_artifact_description(f)
                            artifacts.append({"path": rel_path, "description": desc})
        
        return artifacts
    
    def _infer_artifact_description(self, filepath: Path) -> str:
        """Infer description from filename and extension."""
        name = filepath.stem.replace("_", " ").replace("-", " ")
        ext = filepath.suffix.lower()
        
        ext_types = {
            ".html": "HTML document",
            ".pdf": "PDF document",
            ".md": "Markdown document",
            ".json": "JSON data",
            ".csv": "CSV data",
            ".png": "Image",
            ".jpg": "Image",
            ".mp4": "Video",
        }
        
        file_type = ext_types.get(ext, "file")
        return f"{name.title()} ({file_type})"
    
    def _extract_subagent_results(self) -> List[Dict[str, Any]]:
        """Extract sub-agent results from subagent_outputs directory."""
        results = []
        
        subagent_dir = self.workspace_path / "subagent_outputs"
        if not subagent_dir.exists():
            return results
        
        for task_dir in subagent_dir.iterdir():
            if not task_dir.is_dir():
                continue
            
            output_file = task_dir / "subagent_output.json"
            if output_file.exists():
                try:
                    data = json.loads(output_file.read_text())
                    
                    # Extract key info
                    subagent_type = data.get("tool_input", {}).get("subagent_type", "unknown")
                    
                    # Get summary from output
                    output = data.get("output", [])
                    summary = ""
                    if isinstance(output, list) and output:
                        first_block = output[0]
                        if isinstance(first_block, dict) and "text" in first_block:
                            # Take first 200 chars of output
                            summary = first_block["text"][:200]
                    
                    results.append({
                        "subagent_type": subagent_type,
                        "summary": summary,
                    })
                except Exception:
                    pass
        
        return results
    
    def _extract_tasks_from_trace(self, trace_path: Path) -> List[str]:
        """Extract completed tasks from trace.json."""
        tasks = []
        
        try:
            data = json.loads(trace_path.read_text())
            
            # Look at tool_results for Task tool completions
            tool_results = data.get("tool_results", [])
            for tr in tool_results:
                preview = tr.get("content_preview", "")
                
                # Look for success indicators
                if "✓" in preview or "Complete" in preview or "success" in preview.lower():
                    # Try to extract a task name from the preview
                    lines = preview.split("\n")
                    for line in lines[:3]:  # First 3 lines
                        if line.strip() and not line.startswith("["):
                            # Clean up the task name
                            task_name = line.strip()
                            if "##" in task_name:
                                task_name = task_name.replace("##", "").strip()
                            if task_name and len(task_name) < 100:
                                tasks.append(task_name)
                                break
        except Exception:
            pass
        
        # Deduplicate while preserving order
        seen = set()
        unique_tasks = []
        for t in tasks:
            if t not in seen:
                seen.add(t)
                unique_tasks.append(t)
        
        return unique_tasks
    
    def _extract_key_decisions(self) -> List[str]:
        """Extract key decisions from memory files if present."""
        decisions = []
        
        memory_dir = self.workspace_path / "memory"
        if memory_dir.exists():
            for md_file in memory_dir.glob("*.md"):
                try:
                    content = md_file.read_text()
                    # Look for decision-like statements
                    for line in content.split("\n"):
                        if any(kw in line.lower() for kw in ["decided", "chose", "using", "switched to"]):
                            clean_line = line.strip().lstrip("-").strip()
                            if clean_line and len(clean_line) < 150:
                                decisions.append(clean_line)
                except Exception:
                    pass
        
        return decisions[:5]  # Limit to 5
    
    def save(self, checkpoint: SessionCheckpoint) -> Path:
        """Save checkpoint to workspace."""
        # Save JSON
        json_path = self.workspace_path / self.CHECKPOINT_FILENAME
        json_path.write_text(json.dumps(checkpoint.to_dict(), indent=2))
        
        # Save Markdown (human-readable)
        md_path = self.workspace_path / self.CHECKPOINT_MARKDOWN_FILENAME
        md_path.write_text(checkpoint.to_markdown())
        
        return json_path
    
    def load_latest(self) -> Optional[SessionCheckpoint]:
        """Load the latest checkpoint from workspace."""
        json_path = self.workspace_path / self.CHECKPOINT_FILENAME
        
        if not json_path.exists():
            return None
        
        try:
            data = json.loads(json_path.read_text())
            return SessionCheckpoint.from_dict(data)
        except Exception:
            return None
