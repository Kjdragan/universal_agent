"""
Harness Verification Module.

Responsibility:
- Verify task completion claims by the agent.
- Perform Binary Checks (Artifact existence).
- Perform Quality Checks (LLM-as-a-judge).
"""

import os
import json
import asyncio
from typing import Tuple, List, Dict, Any

# Assuming we can import the standard client/model interface
# adapting from main.py's usage
# For now, we'll design it to take a client or use a lightweight one.

class TaskVerifier:
    def __init__(self, client=None):
        self.client = client

    def verify_artifacts(self, task: Dict[str, Any], workspace_dir: str) -> Tuple[bool, str]:
        """
        Binary Check: Do the declared output artifacts exist?
        """
        artifacts = task.get("output_artifacts", [])
        if not artifacts:
            # If no artifacts are declared, we can't binary verify, so we pass
            # But we might want to warn if description implies artifacts.
            return True, "No artifacts declared to verify."

        missing = []
        for pattern in artifacts:
            # Simple check: if it looks like a glob, we might need globbing.
            # For now, assume explicit paths or simple names.
            # If the agent writes "report.pfd", we expect that file.
            
            # TODO: Support globbing if needed. For now, exact check.
            # Users might put "reports/*.md", we should handle that.
            import glob
            full_pattern = os.path.join(workspace_dir, pattern)
            matches = glob.glob(full_pattern)
            
            if not matches:
                missing.append(pattern)

        if missing:
            return False, f"Missing required artifacts: {', '.join(missing)}"
        
        return True, "All artifacts found."

    async def verify_quality(
        self, 
        task: Dict[str, Any], 
        workspace_dir: str, 
        context: str = ""
    ) -> Tuple[bool, str]:
        """
        LLM Judge: Does the work meet the success criteria?
        """
        if not self.client:
            return True, "No LLM client provided for quality check."

        criteria = task.get("success_criteria", "")
        if not criteria:
            return True, "No success_criteria defined."

        # Read artifacts to give context (limit size)
        artifacts_content = ""
        artifacts = task.get("output_artifacts", [])
        for pattern in artifacts:
            import glob
            full_pattern = os.path.join(workspace_dir, pattern)
            matches = glob.glob(full_pattern)
            for m in matches[:3]: # check first 3 matches
                try:
                    with open(m, "r", errors="ignore") as f:
                        content = f.read(2000) # Read first 2KB
                        artifacts_content += f"\n--- File: {os.path.basename(m)} ---\n{content}\n..."
                except Exception:
                    pass
        
        # specific prompt for the judge
        system_prompt = (
            "You are a strict QA Verifier for an automated agent.\n"
            "Your job is to check if a task was completed according to its Success Criteria.\n"
            "Analyze the artifacts and the user requirements.\n"
            "Output valid JSON only: {\"pass\": bool, \"reason\": \"short explanation\"}"
        )
        
        user_prompt = (
            f"TASK DESCRIPTION: {task.get('description')}\n"
            f"SUCCESS CRITERIA: {criteria}\n"
            f"ARTIFACTS PREVIEW:\n{artifacts_content}\n\n"
            f"Did the agent meet the criteria?"
        )

        try:
            # This relies on the client having a simple generation method
            # If client is the complex vertex client, we might need a different call path.
            # For now, we'll assume the caller passes a function or we create a fresh client.
            # As a fallback, we'll implement a simple one-off call if client is passed.
            
            # Using the 'client' object from main.py
            # client.models.generate_content(...)
            
            response = await self.client.generate_content(
                model="gemini-2.0-flash-exp", # Fast model for verification
                contents=user_prompt,
                config={"response_mime_type": "application/json"}
            )
            
            result_text = response.text
            result_json = json.loads(result_text)
            
            is_pass = result_json.get("pass", False)
            reason = result_json.get("reason", "No reason provided")
            
            return is_pass, reason

        except Exception as e:
            print(f"⚠️ Verification Error: {e}")
            # Fail safe: If judge fails, we warn but don't block? 
            # Or strict: Block. Let's be strict for now but safe on crash.
            return True, f"Verifier crashed ({e}), allowing to proceed with warning."

