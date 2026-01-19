"""
URW Interview Module

Interactive planning interview to decompose massive requests into phases.
Based on interview.md design.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    tool,
    create_sdk_mcp_server,
    AssistantMessage,
    TextBlock,
)

from .plan_schema import Plan, Phase, AtomicTask, TaskStatus


# -----------------------------------------------------------------------------
# Interview State Management
# -----------------------------------------------------------------------------

class InterviewPhase(Enum):
    """Phases of the planning interview."""
    INTRODUCTION = "introduction"
    DISCOVERY = "discovery"
    REQUIREMENTS = "requirements"
    REVIEW = "review"
    COMPLETE = "complete"


@dataclass
class InterviewState:
    """Tracks interview progress and collected data."""
    phase: InterviewPhase = InterviewPhase.INTRODUCTION
    collected_data: Dict[str, List[dict]] = field(default_factory=dict)
    questions_asked: List[str] = field(default_factory=list)
    
    def add_response(self, category: str, question: str, answer: str) -> None:
        """Record a question-answer pair."""
        self.questions_asked.append(question)
        if category not in self.collected_data:
            self.collected_data[category] = []
        self.collected_data[category].append({
            "question": question,
            "answer": answer,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize state for persistence."""
        return {
            "phase": self.phase.value,
            "collected_data": self.collected_data,
            "questions_asked": self.questions_asked
        }


# -----------------------------------------------------------------------------
# Custom ask_user MCP Tool
# -----------------------------------------------------------------------------

@tool("ask_user", "Ask the user a question and get their response", {
    "question": str,
    "category": str,
    "options": list  # Optional predefined choices
})
async def ask_user_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Terminal-based user input tool.
    
    Replaces the native AskUserQuestion which doesn't work in SDK mode.
    Uses asyncio.to_thread to prevent blocking the event loop.
    """
    question = args["question"]
    category = args.get("category", "general")
    options = args.get("options", [])
    
    # Handle case where options is passed as comma-separated string
    if isinstance(options, str):
        options = [opt.strip() for opt in options.split(",") if opt.strip()]
    
    print(f"\n📋 [{category.upper()}]")
    print(f"❓ {question}")
    
    if options and len(options) > 0:
        for i, opt in enumerate(options, 1):
            print(f"   {i}. {opt}")
        print("   (or type your own answer)")
    
    # asyncio.to_thread prevents blocking the event loop
    answer = await asyncio.to_thread(input, "\n👤 Your answer: ")
    
    # Handle numeric selection
    if options and answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(options):
            answer = options[idx]
    
    return {"content": [{"type": "text", "text": f"User answered: {answer}"}]}


# Create MCP server hosting the interview tool
interview_server = create_sdk_mcp_server(
    name="interview",
    version="1.0.0",
    tools=[ask_user_tool]
)


# -----------------------------------------------------------------------------
# System Prompt for Planning Interview
# -----------------------------------------------------------------------------

PLANNING_SYSTEM_PROMPT = """You are a planning agent conducting a requirements interview for a massive task.

## Interview Process:
1. Start by understanding the user's massive task at a high level
2. Use the ask_user tool to gather requirements across these categories:
   - **Project Overview**: goals, scope, success criteria, constraints
   - **Technical Requirements**: dependencies, integrations, technology stack
   - **Deliverables**: expected outputs, formats, quality standards
   - **Timeline**: phases, milestones, dependencies between phases

3. Ask 2-3 focused questions per category, following up on important details
4. Once sufficient requirements are gathered, ask a final open-ended question: "Is there anything else you would like to add or clarify before I create the plan?"
5. After receiving the final answer, generate a structured Plan

## Question Guidelines:
- Be specific and actionable
- Don't ask open-ended questions that lead to rambling answers
- Offer options when appropriate to guide the user
- Summarize understanding periodically

## Output:
When requirements gathering is complete, you MUST output the final Plan using the provided JSON schema/tool.
DO NOT output the plan as Markdown text.
The Plan must include:
- Atomic tasks grouped into logical phases
- Each phase should be completable in one agent session (single context window)
- Clear dependencies between tasks where applicable
- Phases ordered by execution sequence
- Each phase has a descriptive prompt that can be fed to the multi-agent system

## Phase Design Principle:
- **Phase = New Context Window**: Only create multiple phases if the task is massive and requires clearing memory between steps.
- **Single Phase Default**: For most workflows (e.g., Research -> Write -> Email), put ALL steps as Atomic Tasks into a **SINGLE PHASE**.
- **Atomic Task**: A specific action (search, write file, calculate) that the agent performs.
- **Use Case**: Providing "subatomic" details (specific constraints, attachment requirements, known fields) in the `use_case` field is highly encouraged to guide the tool router.

## Output Schema:
{
  "name": "Plan Name",
  "phases": [
    {
      "name": "Main Execution Phase",
      "order": 1,
      "tasks": [
        {
          "name": "Task Name",
          "description": "High level description",
          "use_case": "Detailed subatomic instructions (e.g. 'Search for X with time filter Y')",
          "success_criteria": ["Criteria 1", "Criteria 2"]
        }
      ]
    }
  ]
}
"""


# -----------------------------------------------------------------------------
# Interview Conductor
# -----------------------------------------------------------------------------

class InterviewConductor:
    """Conducts the planning interview and produces a structured Plan."""
    
    def __init__(self, harness_dir: Path, harness_id: str):
        self.harness_dir = Path(harness_dir)
        self.harness_id = harness_id
        self.state = InterviewState()
    
    def _get_interview_options(self) -> ClaudeAgentOptions:
        """Build options for the interview agent."""
        return ClaudeAgentOptions(
            mcp_servers={"interview": interview_server},
            allowed_tools=[
                "mcp__interview__ask_user",
                "Read",
                "Glob",
                "Grep"
            ],
            disallowed_tools=["AskUserQuestion"],  # Block non-functional native tool
            output_format={
                "type": "json_schema",
                "schema": Plan.model_json_schema()
            },
            system_prompt=PLANNING_SYSTEM_PROMPT,
            max_turns=30
        )
    
    def _normalize_plan_data(self, data: Any) -> Any:
        """Recursively normalize JSON keys to match Plan schema."""
        if isinstance(data, dict):
            new_data = {}
            key_map = {
                "planTitle": "name", "planName": "name", "title": "name",
                "planDescription": "description", "desc": "description",
                "phaseId": "id", "phaseName": "name", "phaseDescription": "description",
                "taskId": "id", "taskName": "name", "taskDescription": "description",
                "estimatedDurationMinutes": "estimated_duration_minutes",
                "duration": "estimated_duration_minutes",
                "estimatedDuration": "estimated_duration_minutes",
                "successCriteria": "success_criteria", "criteria": "success_criteria",
                "acceptanceCriteria": "success_criteria", "requirements": "success_criteria",
                "useCase": "use_case", "intent": "use_case",
                "phaseNumber": "order"
            }
            for k, v in data.items():
                new_key = key_map.get(k, k)
                
                # Type conversion for duration
                if new_key == "estimated_duration_minutes" and isinstance(v, str):
                    import re
                    match = re.search(r'\d+', v)
                    if match:
                        v = int(match.group())
                    else:
                        v = None
                        
                new_data[new_key] = self._normalize_plan_data(v)
            return new_data
        elif isinstance(data, list):
            return [self._normalize_plan_data(item) for item in data]
        return data

    def _extract_plan_from_text(self, text: str) -> Optional[Plan]:
        """Attempt to extract and parse JSON Plan from text with normalization."""
        try:
            # Look for JSON code block
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if not match:
                # Look for just JSON object start/end
                match = re.search(r"(\{.*\})", text, re.DOTALL)
            
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                data = self._normalize_plan_data(data)
                
                # Heuristic: Inject default Plan name if missing
                if not data.get("name"):
                    data["name"] = "Generated Plan"
                
                # Heuristic: Collapse "Task-like Phases" into a single Phase
                phases = data.get("phases", [])
                if phases and isinstance(phases, list):
                    # Check if first phase has NO tasks (implying the phases ARE the tasks)
                    first_phase = phases[0]
                    if not first_phase.get("tasks"):
                        print("⚠️  Detecting Task-like Phases. Collapsing into single Execution Phase...")
                        new_tasks = []
                        for i, p in enumerate(phases):
                            # Map Phase fields to AtomicTask fields
                            task = {
                                "id": p.get("id", str(i+1)),
                                "name": p.get("name", f"Task {i+1}"),
                                "description": p.get("description", p.get("name", "")),
                                # Important: User wants 'prompt' details as 'use_use' (subatomic details)
                                "use_case": p.get("prompt") or p.get("description") or "",
                                "success_criteria": p.get("success_criteria", []),
                                "estimated_duration_minutes": p.get("estimated_duration_minutes"),
                                "status": "pending"
                            }
                            new_tasks.append(task)
                        
                        # Replace phases list with single consolidated phase
                        data["phases"] = [{
                            "name": "Execution Phase",
                            "description": "Auto-consolidated phase from task list",
                            "order": 0,
                            "tasks": new_tasks,
                            "status": "pending"
                        }]
                
                # Force 0-indexed sequential order for all phases
                if "phases" in data and isinstance(data["phases"], list):
                    for i, p in enumerate(data["phases"]):
                        p["order"] = i
                
                return Plan.model_validate(data)
        except Exception as e:
            print(f"⚠️ JSON extraction failed: {e}")
        return None

    async def conduct_interview(self, massive_request: str) -> Optional[Plan]:
        """
        Conduct the planning interview and return a structured Plan.
        
        Args:
            massive_request: The user's original massive request
            
        Returns:
            Plan object with phases and atomic tasks, or None if failed
        """
        print("\n" + "=" * 60)
        print("🎯 HARNESS PLANNING INTERVIEW")
        print("=" * 60)
        print(f"\nMassive request: {massive_request[:200]}...")
        print("\nThe planning agent will ask you questions to understand your requirements.\n")
        
        options = self._get_interview_options()
        plan = None
        
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(
                    f"User's massive task:\n\n{massive_request}\n\n"
                    "Begin the planning interview. Ask clarifying questions to understand "
                    "the requirements, then produce a structured Plan."
                )
                
                async for message in client.receive_response():
                    # Check for structured output (the Plan)
                    if hasattr(message, 'structured_output') and message.structured_output:
                        try:
                            plan = Plan.model_validate(message.structured_output)
                            plan.massive_request = massive_request
                            plan.harness_id = self.harness_id
                            break
                        except Exception as e:
                            print(f"⚠️ Failed to parse plan: {e}")
                    
                    # Print assistant messages for visibility
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                print(f"\n🤖 {block.text}")
                                
                                # Fallback: parse plan from markdown text
                                if not plan:
                                    extracted = self._extract_plan_from_text(block.text)
                                    if extracted:
                                        plan = extracted
                                        plan.massive_request = massive_request
                                        plan.harness_id = self.harness_id
                                        break
            
            if plan:
                # Save interview log
                self._save_interview_log(massive_request)
                print("\n" + "=" * 60)
                print(f"✅ Plan created: {plan.name}")
                print(f"   Phases: {len(plan.phases)}")
                print(f"   Total tasks: {plan.total_tasks()}")
                print("=" * 60 + "\n")
            else:
                print("\n⚠️ Interview completed but no plan was generated.")
                
        except Exception as e:
            print(f"\n❌ Interview failed: {e}")
            import traceback
            traceback.print_exc()
        
        return plan
    
    async def generate_plan_from_transcript(self, massive_request: str, transcript: List[Dict[str, str]]) -> Optional[Plan]:
        """
        Generate a plan from an existing interview transcript (skipping interactive mode).
        """
        print("\n" + "=" * 60)
        print("🎯 HARNESS PLANNING - FROM TEMPLATE")
        print("=" * 60)
        print(f"\nMassive request: {massive_request[:200]}...")
        print(f"Using transcript with {len(transcript)} Q&A pairs.")
        
        # Build transcript string
        transcript_text = ""
        for item in transcript:
            transcript_text += f"Q: {item['question']}\nA: {item['answer']}\n\n"
            
        options = self._get_interview_options()
        plan = None
        
        try:
            async with ClaudeSDKClient(options=options) as client:
                query = (
                    f"User's massive task:\n\n{massive_request}\n\n"
                    f"Interview Transcript:\n{transcript_text}\n\n"
                    "Based on the requirements gathered in the interview above, "
                    "produce a structured Plan with Phased Atomic Tasks.\n\n"
                    "CRITICAL INSTRUCTION:\n"
                    "1. You MUST output the result as a strict JSON object matching the Plan schema.\n"
                    "2. DO NOT include any conversational text, markdown headers, or explanations.\n"
                    "3. Start your response with `{` and end with `}`.\n"
                    "4. If you cannot use the structured output tool, output the raw JSON string inside a ```json code block."
                )
                
                await client.query(query)
                
                async for message in client.receive_response():
                    # Check for structured output (the Plan)
                    if hasattr(message, 'structured_output') and message.structured_output:
                        try:
                            plan = Plan.model_validate(message.structured_output)
                            plan.massive_request = massive_request
                            plan.harness_id = self.harness_id
                            break
                        except Exception as e:
                            print(f"⚠️ Failed to parse plan: {e}")
                    
                    # Print assistant messages for visibility
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                print(f"\n🤖 {block.text}")
                                
                                # Fallback: parse plan from markdown text
                                if not plan:
                                    extracted = self._extract_plan_from_text(block.text)
                                    if extracted:
                                        plan = extracted
                                        plan.massive_request = massive_request
                                        plan.harness_id = self.harness_id
                                        break
            
            if plan:
                print("\n" + "=" * 60)
                print(f"✅ Plan created from template: {plan.name}")
                print(f"   Phases: {len(plan.phases)}")
                print(f"   Total tasks: {plan.total_tasks()}")
                print("=" * 60 + "\n")
            else:
                print("\n⚠️ Template processing completed but no plan was generated.")
                
        except Exception as e:
            print(f"\n❌ Template generation failed: {e}")
            import traceback
            traceback.print_exc()
        
        return plan

    def _save_interview_log(self, massive_request: str) -> None:
        """Save interview data to harness directory."""
        log_path = self.harness_dir / "interview_log.json"
        log_data = {
            "massive_request": massive_request,
            "interview_state": self.state.to_dict(),
            "completed_at": datetime.utcnow().isoformat()
        }
        log_path.write_text(json.dumps(log_data, indent=2))


# -----------------------------------------------------------------------------
# Convenience Function
# -----------------------------------------------------------------------------

async def run_planning_interview(
    harness_dir: Path,
    harness_id: str,
    massive_request: str
) -> Optional[Plan]:
    """
    Run a planning interview for a massive request.
    
    Args:
        harness_dir: Directory for harness files
        harness_id: Unique harness run ID
        massive_request: The user's massive request
        
    Returns:
        Structured Plan object, or None if interview failed
    """
    conductor = InterviewConductor(harness_dir, harness_id)
    return await conductor.conduct_interview(massive_request)


async def run_planning_from_template(
    harness_dir: Path,
    harness_id: str,
    template_path: Path
) -> Optional[Plan]:
    """
    Generate a plan using a saved interview template (skips Q&A).
    """
    if not template_path.exists():
        print(f"❌ Template file not found: {template_path}")
        return None
        
    try:
        data = json.loads(template_path.read_text())
        massive_request = data.get("massive_request", "")
        transcript = data.get("questions_and_answers", [])
        
        conductor = InterviewConductor(harness_dir, harness_id)
        return await conductor.generate_plan_from_transcript(massive_request, transcript)
    except Exception as e:
        print(f"❌ Failed to load template: {e}")
        return None
