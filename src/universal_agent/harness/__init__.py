"""
Harness V2 Module for Universal Agent.

Provides long-running task management with:
- Mission Manifest tracking (mission.json)
- Planning Phase with Interview Tool
- Approval Gate before execution
"""

from .interview_tool import ask_user_questions, present_plan_summary

__all__ = ["ask_user_questions", "present_plan_summary"]
