"""
Artifact Publisher Tool.

Provides an internal MCP tool (`mcp__internal__publish_artifact`) for moving completed
session artifacts from `CURRENT_RUN_WORKSPACE/work_products/` into the persistent, 
long-term `UA_ARTIFACTS_DIR` (e.g. `<repo>/artifacts/`).
"""

import logging
import os
from pathlib import Path
import shutil
from typing import Any, Dict

from claude_agent_sdk import tool

from universal_agent.artifacts import build_artifact_run_dir, resolve_artifacts_dir

logger = logging.getLogger(__name__)

def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    import json
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}

def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}

@tool(
    name="publish_artifact",
    description=(
        "Promotes a file or directory from the temporary session workspace into long-term persistent storage. "
        "Use this only when a final deliverable is ready to be archived perpetually. "
        "Will copy the source file/dir to a timestamped folder in the main artifacts directory."
    ),
    input_schema={
        "source_path": str,
        "skill_or_topic": str,
        "description": str,
    }
)
async def mcp__internal__publish_artifact(args: Dict[str, Any]) -> Dict[str, Any]:
    """Publish a file or folder from the workspace to persistent artifacts storage."""
    source_path_str = str(args.get("source_path") or "").strip()
    skill_or_topic = str(args.get("skill_or_topic") or "").strip()
    description = str(args.get("description") or "").strip()

    if not source_path_str:
        return _err("'source_path' is required.")
    if not skill_or_topic:
        return _err("'skill_or_topic' is required to categorize the artifact.")
    
    source_path = Path(source_path_str).expanduser().resolve()
    if not source_path.exists():
        return _err(f"Source path does not exist: {source_path}")

    try:
        # Build the persistent destination path using durable logging logic.
        slug = source_path.stem
        # `artifacts_root` will resolve dynamically.
        artifact_run = build_artifact_run_dir(
            skill_name=skill_or_topic,
            slug=slug,
            artifacts_root=resolve_artifacts_dir()
        )
        
        dest_dir = artifact_run.run_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        dest_path = dest_dir / source_path.name
        
        if source_path.is_file():
            shutil.copy2(source_path, dest_path)
            
            # Write a small metadata file for context
            meta_path = dest_dir / "metadata.txt"
            with meta_path.open("w", encoding="utf-8") as f:
                f.write(f"Topic: {skill_or_topic}\n")
                f.write(f"Source: {source_path.name}\n")
                if description:
                    f.write(f"Description: {description}\n")
                    
            logger.info(f"Published artifact '{source_path.name}' to '{dest_path}'")
        elif source_path.is_dir():
            if dest_path.exists():
                shutil.rmtree(dest_path)
            shutil.copytree(source_path, dest_path)
            
            meta_path = dest_dir / "metadata.txt"
            with meta_path.open("w", encoding="utf-8") as f:
                f.write(f"Topic: {skill_or_topic}\n")
                f.write(f"Source Dir: {source_path.name}\n")
                if description:
                    f.write(f"Description: {description}\n")
                    
            logger.info(f"Published artifact directory '{source_path.name}' to '{dest_path}'")
            
        return _ok({
            "status": "success",
            "published_path": str(dest_path),
            "artifacts_root": str(resolve_artifacts_dir()),
            "message": "Artifact successfully published to persistent storage."
        })
        
    except Exception as e:
        logger.error(f"Failed to publish artifact: {e}", exc_info=True)
        return _err(str(e))
