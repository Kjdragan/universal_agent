"""
Memory tool implementation for safe file retrieval.
"""

import os
from pathlib import Path

def ua_memory_get(path: str, line_start: int = 1, num_lines: int = 100) -> str:
    """
    Read content from the agent's memory files.
    
    This tool allows safe access to 'MEMORY.md' and files within the 'memory/'
    subdirectory of the current workspace. It prevents access to any other
    files on the system.
    
    Args:
        path: Relative path to the file (e.g., 'MEMORY.md', 'memory/project_notes.md').
        line_start: Line number to start reading from (1-based index).
        num_lines: Number of lines to read.
        
    Returns:
        The content of the file or an error message.
    """
    # 1. Resolve workspace root
    # In the tool execution context (unlike the agent setup), we might not have 'self.workspace_dir'
    # readily available if this is a standalone function. However, the agent execution environment
    # typically sets current working directory to the workspace or provides it via env.
    # We will assume CWD is the workspace root or relies on an env var 'AGENT_WORKSPACE_DIR'.
    # Fallback to CWD if env is not set.
    
    workspace_dir = os.environ.get("AGENT_WORKSPACE_DIR", os.getcwd())
    root = Path(workspace_dir).resolve()
    
    # 2. Resolve target path
    # Prevent absolute paths that escape root immediately if possible, 
    # but .resolve() handles '..' sanitization best.
    try:
        target_path = (root / path).resolve()
    except Exception as e:
         return f"Error resolving path: {e}"

    # 3. Security Check: Must be within root and follow rules
    # Rule 1: Must be inside the workspace root
    if not str(target_path).startswith(str(root)):
        return f"Access Denied: Path '{path}' is outside the active workspace."
    
    # Rule 2: Allowed paths are ONLY 'MEMORY.md' (in valid root) OR inside 'memory/' dir
    rel_path = target_path.relative_to(root)
    is_memory_md = str(rel_path) == "MEMORY.md"
    is_in_memory_dir = str(rel_path).startswith("memory" + os.sep)
    
    if not (is_memory_md or is_in_memory_dir):
        return f"Access Denied: You may only read 'MEMORY.md' or files in the 'memory/' directory. Requested: {path}"
        
    # 4. Read File
    if not target_path.exists():
         return f"File not found: {path}"
         
    if not target_path.is_file():
         return f"Not a file: {path}"

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        total_lines = len(lines)
        if line_start < 1:
            line_start = 1
            
        start_idx = line_start - 1
        end_idx = start_idx + num_lines
        
        selected_lines = lines[start_idx:end_idx]
        content = "".join(selected_lines)
        
        return content
    except Exception as e:
        return f"Error reading file: {e}"
