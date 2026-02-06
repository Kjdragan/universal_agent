import os
from pathlib import Path
from typing import Optional
import logging
import sys
import re

logger = logging.getLogger(__name__)

def normalize_task_name(task_name: str) -> str:
    """Normalize task names to snake_case for consistent task directory naming."""
    if not task_name:
        return "default"
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", task_name)
    normalized = re.sub(r"_+", "_", normalized).strip("_").lower()
    return normalized or "default"

def resolve_best_task_match(requested_name: str, workspace_root: Optional[Path] = None) -> str:
    """
    Resolve a requested task name to an existing directory if a mismatch exists.
    
    Logic:
    1. If exact directory exists -> Return requested_name.
    2. If not, scan siblings in `tasks/`.
    3. If an existing directory ends with requested_name (e.g. `minnesota_protests_jan_2026` vs `jan27_2026`) -> Return existing.
    4. If requested_name is a substring of existing (or vice versa) -> Return existing name (Auto-Correct).
    5. Otherwise -> Return requested_name (Create new).
    
    Args:
        requested_name: The task name requested by the agent.
        workspace_root: Optional override for workspace root.
        
    Returns:
        The resolved task name (either original or corrected).
    """
    if not requested_name or requested_name == "default":
        return requested_name

    canonical_requested = normalize_task_name(requested_name)
    requested_name = canonical_requested

    # 1. Determine Workspace Root
    if not workspace_root:
        # Replicate _resolve_workspace logic from mcp_server.py to avoid circular imports
        # Priority: Env Var > Marker File > Heuristic
        env_workspace = os.getenv("CURRENT_SESSION_WORKSPACE")
        if env_workspace and os.path.exists(env_workspace):
            workspace_root = Path(env_workspace)
        else:
            # Try marker file
            # Assuming we are running from project root or src/
            # Try to find AGENT_RUN_WORKSPACES relative to CWD
            cwd = Path.cwd()
            marker_path = os.getenv("CURRENT_SESSION_WORKSPACE_FILE")
            if not marker_path:
                 # Check common locations
                 candidates = [
                     cwd / "AGENT_RUN_WORKSPACES" / ".current_session_workspace",
                     cwd.parent / "AGENT_RUN_WORKSPACES" / ".current_session_workspace",
                     Path("/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/.current_session_workspace")
                 ]
                 for c in candidates:
                     if c.exists():
                         marker_path = str(c)
                         break
            
            if marker_path and os.path.exists(marker_path):
                try:
                    workspace_root = Path(Path(marker_path).read_text().strip())
                except Exception:
                    pass
    
    if not workspace_root or not workspace_root.exists():
        # Fallback: Assume we are inside the workspace or CWD is root and we can't find tasks
        # If we can't find the workspace, we can't scan for duplicates. return original.
        return requested_name

    tasks_dir = workspace_root / "tasks"
    if not tasks_dir.exists():
        # No tasks yet, safe to create new
        return requested_name

    # 2. Exact Match Check
    target_path = tasks_dir / requested_name
    if target_path.exists():
        return requested_name

    # 3. Fuzzy/Substring Match Scan
    # We prioritize:
    # A. Existing directory contains strict requested name (e.g. req="jan_2026", exist="protests_jan_2026")
    # B. Requested name contains existing directory (e.g. req="protests_jan_2026_v2", exist="protests_jan_2026")
    # C. Common "token" overlap (e.g. "minnesota_protests" shared)
    
    # For the reported issue: `jan_2026` vs `jan27_2026`
    # Tokenizing is safer.
    
    def tokenize(name):
        return set(name.replace("_", " ").replace("-", " ").split())
    
    requested_tokens = tokenize(requested_name)
    best_candidate = None
    best_score = 0.0
    
    for existing_path in tasks_dir.iterdir():
        if not existing_path.is_dir():
            continue
        
        existing_name = existing_path.name
        existing_tokens = tokenize(existing_name)
        
        # Calculate Jaccard Similarity of tokens
        intersection = len(requested_tokens & existing_tokens)
        union = len(requested_tokens | existing_tokens)
        
        if union == 0: continue
        score = intersection / union
        
        # Boost score if one is a substring of the other
        if requested_name in existing_name or existing_name in requested_name:
            score += 0.5
            
        if score > best_score:
            best_score = score
            best_candidate = existing_name

    # Threshold: If score is high enough, auto-correct.
    # 0.5 means roughly half tokens match.
    # For `minnesota_protests_jan_2026` vs `minnesota_protests_jan27_2026`:
    # Tokens: {minnesota, protests, jan, 2026} vs {minnesota, protests, jan27, 2026}
    # Intersection: {minnesota, protests, 2026} (3)
    # Union: {minnesota, protests, jan, jan27, 2026} (5)
    # Score: 0.6. + Substring bonus? No.
    # 0.6 is > 0.5.
    
    if best_candidate and best_score > 0.5:
        msg = f"[Task Guardrail] Auto-corrected task name: '{requested_name}' -> '{best_candidate}' (Score: {best_score:.2f})"
        print(msg, file=sys.stderr)
        logger.info(msg)
        return best_candidate

    return requested_name
