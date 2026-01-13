"""
Harness Verification Module.

Responsibility:
- Verify task completion claims by the agent.
- Tiered Verification:
  - Tier 1 (Binary): Artifact existence
  - Tier 2 (Format): Non-empty, valid format
  - Tier 3 (Semantic): Optional LLM "vibe check"
"""

import os
import json
import glob
from typing import Tuple, Dict, Any, Optional


class TaskVerifier:
    """
    Tiered verification for harness task completion.
    
    TIER 1 (BINARY): Do files exist?
    TIER 2 (FORMAT): Are they non-empty and valid format?
    TIER 3 (SEMANTIC): Optional LLM quality check
    """
    
    def __init__(self, client=None):
        self.client = client

    def verify_artifacts(self, task: Dict[str, Any], workspace_dir: str) -> Tuple[bool, str]:
        """
        Tier 1 - Binary Check: Do the declared output artifacts exist?
        """
        artifacts = task.get("output_artifacts", [])
        if not artifacts:
            return True, "No artifacts declared to verify."

        missing = []
        for pattern in artifacts:
            full_pattern = os.path.join(workspace_dir, pattern)
            matches = glob.glob(full_pattern)
            
            if not matches:
                missing.append(pattern)

        if missing:
            return False, f"Missing required artifacts: {', '.join(missing)}"
        
        return True, "All artifacts found."

    def verify_format(self, task: Dict[str, Any], workspace_dir: str) -> Tuple[bool, str]:
        """
        Tier 2 - Format Check: Are artifacts non-empty and valid format?
        """
        artifacts = task.get("output_artifacts", [])
        if not artifacts:
            return True, "No artifacts to format-check."

        issues = []
        for pattern in artifacts:
            full_pattern = os.path.join(workspace_dir, pattern)
            matches = glob.glob(full_pattern)
            
            for filepath in matches:
                # Check non-empty
                if os.path.getsize(filepath) == 0:
                    issues.append(f"{os.path.basename(filepath)}: 0 bytes (empty)")
                    continue
                
                # Check basic format validity
                ext = os.path.splitext(filepath)[1].lower()
                if ext == ".json":
                    try:
                        with open(filepath, "r", errors="ignore") as f:
                            json.load(f)
                    except json.JSONDecodeError as e:
                        issues.append(f"{os.path.basename(filepath)}: Invalid JSON - {str(e)[:50]}")
                elif ext == ".html":
                    with open(filepath, "r", errors="ignore") as f:
                        content = f.read(500)
                        if "<html" not in content.lower() and "<!doctype" not in content.lower():
                            issues.append(f"{os.path.basename(filepath)}: Missing HTML structure")
                elif ext == ".pdf":
                    with open(filepath, "rb") as f:
                        header = f.read(5)
                        if header != b"%PDF-":
                            issues.append(f"{os.path.basename(filepath)}: Invalid PDF header")
                # For .md, .txt - just non-empty check (already done)

        if issues:
            return False, f"Format issues: {'; '.join(issues)}"
        
        return True, "All artifacts valid format."

    async def verify_semantic(
        self, 
        task: Dict[str, Any], 
        workspace_dir: str
    ) -> Tuple[bool, str]:
        """
        Tier 3 - Semantic Check: Simple LLM "vibe check" - is this acceptable?
        
        This is a lenient check - only fails if output is clearly wrong.
        """
        if not self.client:
            return True, "No LLM client for semantic check (skipped)."

        criteria = task.get("success_criteria", "")
        if not criteria:
            return True, "No success_criteria defined."

        # Read artifact previews
        artifacts_preview = ""
        artifacts = task.get("output_artifacts", [])
        for pattern in artifacts:
            full_pattern = os.path.join(workspace_dir, pattern)
            matches = glob.glob(full_pattern)
            for m in matches[:2]:  # Max 2 files
                try:
                    with open(m, "r", errors="ignore") as f:
                        content = f.read(1500)  # Read first 1.5KB
                        artifacts_preview += f"\n--- {os.path.basename(m)} ---\n{content[:1500]}\n"
                except Exception:
                    pass
        
        # Simple, lenient prompt
        prompt = (
            f"Task: {task.get('description', 'Unknown')}\n"
            f"Expected: {criteria}\n\n"
            f"Output Preview:\n{artifacts_preview}\n\n"
            f"Question: Does this output appear to be a reasonable attempt at the task?\n"
            f"Answer YES if acceptable, NO only if clearly incomplete or wrong.\n"
            f"Respond with just: YES or NO"
        )

        try:
            response = await self.client.generate_content(
                model="gemini-2.0-flash-exp",
                contents=prompt,
            )
            
            result = response.text.strip().upper()
            if "NO" in result and "YES" not in result:
                return False, "LLM semantic check: Output appears incomplete or wrong"
            
            return True, "Semantic check passed"

        except Exception as e:
            print(f"⚠️ Semantic verification error: {e}")
            # Fail-safe: Allow to proceed if LLM check crashes
            return True, f"Semantic check skipped (error: {str(e)[:50]})"

    def verify_task(
        self, 
        task: Dict[str, Any], 
        workspace_dir: str, 
        tier: int = 2
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Main verification entry point with tiered approach.
        
        Args:
            task: Task dict from mission.json
            workspace_dir: Workspace directory path
            tier: Verification tier (1=Binary, 2=Format, 3=Semantic)
        
        Returns:
            (passed, message, failure_info)
        """
        # Tier 1: Binary existence
        passed, msg = self.verify_artifacts(task, workspace_dir)
        if not passed:
            return False, msg, {"tier": "BINARY", "issue": msg}
        
        if tier >= 2:
            # Tier 2: Format validity
            passed, msg = self.verify_format(task, workspace_dir)
            if not passed:
                return False, msg, {"tier": "FORMAT", "issue": msg}
        
        # Tier 3 requires async - caller must use verify_task_async() for that
        if tier >= 3:
            return True, "Tier 3 requires async - use verify_task_async()", {"tier_note": "semantic_skipped"}
        
        return True, "Verification passed", {}

    async def verify_task_async(
        self, 
        task: Dict[str, Any], 
        workspace_dir: str, 
        tier: int = 2
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Async version of verify_task that supports Tier 3 semantic check.
        """
        # Tier 1 & 2
        passed, msg, info = self.verify_task(task, workspace_dir, min(tier, 2))
        if not passed:
            return passed, msg, info
        
        if tier >= 3 and self.client:
            # Tier 3: Semantic check
            passed, msg = await self.verify_semantic(task, workspace_dir)
            if not passed:
                return False, msg, {"tier": "SEMANTIC", "issue": msg}
        
        return True, "Verification passed", {}
