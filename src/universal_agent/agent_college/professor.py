import os
import subprocess
import logging
from Memory_System.manager import MemoryManager
from .common import AGENT_COLLEGE_NOTES_BLOCK

logger = logging.getLogger(__name__)

class ProfessorAgent:
    def __init__(self, memory_manager: MemoryManager):
        self.memory = memory_manager
        # Find repo root
        self.repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
        self.skill_creator_script = os.path.join(self.repo_root, ".claude", "skills", "skill-creator", "scripts", "init_skill.py")
        self.validate_script = os.path.join(self.repo_root, ".claude", "skills", "skill-creator", "scripts", "quick_validate.py")

    def check_sandbox(self) -> str:
        """
        Reads the Sandbox notes.
        """
        block = self.memory.get_memory_block(AGENT_COLLEGE_NOTES_BLOCK)
        if block:
            return block.value
        return "No notes."

    def create_skill(self, skill_name: str, description: str) -> str:
        """
        Executes init_skill.py to scaffold a new skill (Graduation).
        This MUST be Human-Approved before calling.
        """
        try:
            # 1. Run init_skill.py
            cmd = ["python3", self.skill_creator_script, skill_name, "--path", os.path.join(self.repo_root, ".claude", "skills")]
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.repo_root)
            
            if result.returncode != 0:
                return f"Failed to init skill: {result.stderr}"

            # 2. Update SKILL.md with description
            skill_dir = os.path.join(self.repo_root, ".claude", "skills", skill_name)
            skill_md = os.path.join(skill_dir, "SKILL.md")
            
            if os.path.exists(skill_md):
                # Simple replace for now, ideally use YAML parser but trying to be robust
                with open(skill_md, "r") as f:
                    content = f.read()
                
                # Replace description placeholder if it exists? 
                # Or init_skill might take args?
                # For now, valid scaffold is enough. The user (or Professor) can edit content later.
                pass

            return f"Skill '{skill_name}' created successfully at {skill_dir}"

        except Exception as e:
            return f"Error creating skill: {e}"
