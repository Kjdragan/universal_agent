"""
Universal Ralph Wrapper - Task Decomposer

Decomposes user requests into atomic tasks that:
1. Fit within a context window
2. Have clear completion criteria
3. Produce artifacts that can be handed off
4. Can be executed independently once dependencies are met
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod

from .state import Task, TaskStatus
from .evaluation_policy import TEMPLATE_EVALUATION_POLICIES


DECOMPOSITION_TEMPLATES = {
    "research_report": {
        "description": "Research a topic and produce a comprehensive report",
        "keywords": ["research", "report", "summary", "status report", "investigate", "analyze", "study"],
        "evaluation_policy": TEMPLATE_EVALUATION_POLICIES.get("research_report", {}),
        "tasks": [
            {
                "id_suffix": "scope",
                "title": "Define research scope and questions",
                "description": "Clarify the research questions, boundaries, and success criteria for the investigation.",
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:research_scope.md", "file_exists:handoff.json"],
                "constraints": [{"type": "min_length", "value": 500}],
                "evaluation_rubric": "Are the research questions clear, specific, and answerable?",
                "minimum_acceptable_score": 0.6,
            },
            {
                "id_suffix": "gather",
                "title": "Gather information from sources",
                "description": "Search for and collect relevant information from authoritative sources.",
                "depends_on": ["scope"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:research_notes.md", "file_exists:handoff.json"],
                "constraints": [{"type": "min_length", "value": 2000}],
            },
            {
                "id_suffix": "analyze",
                "title": "Analyze and synthesize findings",
                "description": "Analyze the gathered information, identify patterns, and synthesize key insights.",
                "depends_on": ["gather"],
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:analysis_notes.md", "file_exists:handoff.json"],
                "constraints": [{"type": "min_length", "value": 1200}],
                "evaluation_rubric": "Does the analysis identify clear patterns and provide actionable insights?",
                "minimum_acceptable_score": 0.6,
            },
            {
                "id_suffix": "report",
                "title": "Write final report",
                "description": "Write a comprehensive report with executive summary, findings, and recommendations.",
                "depends_on": ["analyze"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:final_report.md", "file_exists:handoff.json"],
                "constraints": [{"type": "min_length", "value": 3000}],
                "evaluation_rubric": "Is the report well-structured, comprehensive, and actionable?",
            },
        ],
    },
    "email_outreach": {
        "description": "Draft and send personalized email outreach",
        "keywords": ["email", "outreach", "contact", "reach out"],
        "evaluation_policy": TEMPLATE_EVALUATION_POLICIES.get("email_outreach", {}),
        "tasks": [
            {
                "id_suffix": "targets",
                "title": "Identify and research targets",
                "description": "Identify the recipients and gather relevant context about each.",
                "verification_type": "composite",
                "binary_checks": ["file_exists:targets.json", "file_exists:handoff.json"],
            },
            {
                "id_suffix": "template",
                "title": "Create email template",
                "description": "Draft an email template that can be personalized for each recipient.",
                "depends_on": ["targets"],
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:email_template.md", "file_exists:handoff.json"],
                "evaluation_rubric": "Is the template professional, clear, and personalizable?",
                "minimum_acceptable_score": 0.6,
            },
            {
                "id_suffix": "personalize",
                "title": "Personalize emails for each recipient",
                "description": "Create personalized versions of the email for each target recipient.",
                "depends_on": ["template"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:personalized_emails.json", "file_exists:handoff.json"],
            },
            {
                "id_suffix": "send",
                "title": "Send emails",
                "description": "Send the personalized emails to each recipient.",
                "depends_on": ["personalize"],
                "verification_type": "binary",
                "binary_checks": ["side_effect:email_sent", "file_exists:handoff.json"],
            },
        ],
    },
    "document_analysis": {
        "description": "Analyze one or more documents and extract insights",
        "keywords": ["analyze document", "review", "extract", "summarize document"],
        "evaluation_policy": TEMPLATE_EVALUATION_POLICIES.get("document_analysis", {}),
        "tasks": [
            {
                "id_suffix": "ingest",
                "title": "Ingest and parse documents",
                "description": "Read and parse the input documents into a workable format.",
                "verification_type": "composite",
                "binary_checks": ["file_exists:parsed_content.md", "file_exists:handoff.json"],
            },
            {
                "id_suffix": "analyze",
                "title": "Analyze document content",
                "description": "Analyze the parsed content according to the specified criteria.",
                "depends_on": ["ingest"],
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:analysis_notes.md", "file_exists:handoff.json"],
                "constraints": [{"type": "min_length", "value": 800}],
                "evaluation_rubric": "Does the analysis address all requested aspects?",
                "minimum_acceptable_score": 0.6,
            },
            {
                "id_suffix": "output",
                "title": "Generate analysis output",
                "description": "Format the analysis results in the requested output format.",
                "depends_on": ["analyze"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:analysis_output.md", "file_exists:handoff.json"],
            },
        ],
    },
    "data_processing": {
        "description": "Process, transform, or aggregate data",
        "keywords": ["process data", "transform", "aggregate", "clean data", "ETL"],
        "evaluation_policy": TEMPLATE_EVALUATION_POLICIES.get("data_processing", {}),
        "tasks": [
            {
                "id_suffix": "validate",
                "title": "Validate input data",
                "description": "Check input data for completeness, format, and quality issues.",
                "verification_type": "composite",
                "binary_checks": ["file_exists:validation_report.json", "file_exists:handoff.json"],
            },
            {
                "id_suffix": "transform",
                "title": "Transform and process data",
                "description": "Apply the required transformations to the validated data.",
                "depends_on": ["validate"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:processed_data.json", "file_exists:handoff.json"],
            },
            {
                "id_suffix": "verify",
                "title": "Verify output data quality",
                "description": "Verify the processed data meets quality and completeness requirements.",
                "depends_on": ["transform"],
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:quality_report.md", "file_exists:handoff.json"],
                "evaluation_rubric": "Does the output data meet all specified requirements?",
                "minimum_acceptable_score": 0.6,
            },
        ],
    },
    "content_creation": {
        "description": "Create written content (blog post, article, documentation)",
        "keywords": ["write", "create content", "draft", "blog", "article", "documentation"],
        "evaluation_policy": TEMPLATE_EVALUATION_POLICIES.get("content_creation", {}),
        "tasks": [
            {
                "id_suffix": "outline",
                "title": "Create content outline",
                "description": "Develop a structured outline for the content piece.",
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:outline.md", "file_exists:handoff.json"],
                "evaluation_rubric": "Does the outline have a clear structure and cover all required topics?",
                "minimum_acceptable_score": 0.6,
            },
            {
                "id_suffix": "draft",
                "title": "Write first draft",
                "description": "Write the first complete draft following the outline.",
                "depends_on": ["outline"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:draft.md", "file_exists:handoff.json"],
                "constraints": [{"type": "min_length", "value": 1500}],
            },
            {
                "id_suffix": "revise",
                "title": "Revise and polish",
                "description": "Review, revise, and polish the draft into a final version.",
                "depends_on": ["draft"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:final_content.md", "file_exists:handoff.json"],
                "evaluation_rubric": "Is the content well-written, engaging, and error-free?",
            },
        ],
    },
}


class Decomposer(ABC):
    """Abstract base class for task decomposition strategies."""

    @abstractmethod
    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        raise NotImplementedError

    @abstractmethod
    def can_handle(self, request: str) -> bool:
        raise NotImplementedError


class TemplateDecomposer(Decomposer):
    """Decomposer that matches requests to predefined templates."""

    def __init__(self, templates: Optional[Dict] = None):
        self.templates = templates or DECOMPOSITION_TEMPLATES

    def can_handle(self, request: str) -> bool:
        request_lower = request.lower()
        for template in self.templates.values():
            for keyword in template.get("keywords", []):
                if keyword in request_lower:
                    return True
        return False

    def _match_template(self, request: str) -> Optional[str]:
        request_lower = request.lower()
        best_match = None
        best_score = 0
        for name, template in self.templates.items():
            score = 0
            for keyword in template.get("keywords", []):
                if keyword in request_lower:
                    score += len(keyword)
            if score > best_score:
                best_score = score
                best_match = name
        return best_match

    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        template_name = self._match_template(request)
        if not template_name:
            return []
        template = self.templates[template_name]
        template_policy = template.get("evaluation_policy") or {}
        plan_id = uuid.uuid4().hex[:8]
        tasks: List[Task] = []

        id_map: Dict[str, str] = {}
        for task_def in template["tasks"]:
            task_id = f"{plan_id}_{task_def['id_suffix']}"
            id_map[task_def["id_suffix"]] = task_id

        for task_def in template["tasks"]:
            task_id = id_map[task_def["id_suffix"]]
            depends_on = [id_map[dep] for dep in task_def.get("depends_on", []) if dep in id_map]
            task_policy = {**template_policy, **task_def.get("evaluation_policy", {})}
            task = Task(
                id=task_id,
                title=task_def["title"],
                description=self._contextualize_description(task_def["description"], request),
                status=TaskStatus.PENDING,
                depends_on=depends_on,
                verification_type=task_def.get("verification_type", "composite"),
                binary_checks=task_def.get("binary_checks", []),
                constraints=task_def.get("constraints", []),
                evaluation_rubric=task_def.get("evaluation_rubric"),
                minimum_acceptable_score=task_def.get("minimum_acceptable_score", 0.7),
                evaluation_policy=task_policy or None,
                max_iterations=task_def.get("max_iterations", 10),
            )
            tasks.append(task)

        return tasks

    def _contextualize_description(self, description: str, request: str) -> str:
        return f"{description}\n\n**Original Request:** {request}"


class LLMDecomposer(Decomposer):
    """Decomposer that uses an LLM to generate task breakdown."""

    def __init__(self, llm_client: Any, model: str = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-5")):
        self.llm_client = llm_client
        self.model = model

    def can_handle(self, request: str) -> bool:
        return True

    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        prompt = self._build_decomposition_prompt(request, context)
        response = self.llm_client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = response.content[0].text
        return self._parse_decomposition_response(response_text, request)

    def _build_decomposition_prompt(self, request: str, context: Optional[Dict] = None) -> str:
        context_section = ""
        if context:
            if context.get("learnings"):
                context_section += "\n**Prior Learnings:**\n"
                context_section += "\n".join(f"- {l}" for l in context["learnings"])
            if context.get("constraints"):
                context_section += "\n**Constraints:**\n"
                context_section += "\n".join(f"- {c}" for c in context["constraints"])

        return f"""You are a task decomposition expert. Break down the following user request into atomic tasks that can be executed by an AI agent system.

**User Request:**
{request}
{context_section}

**Requirements for each task:**
1. **Atomic**: Each task should be completable in a single agent session (roughly 10-50 tool calls)
2. **Verifiable**: Each task should have clear completion criteria
3. **Independent**: Once dependencies are met, a task can be executed without additional context
4. **Artifact-producing**: Each task should produce a tangible output (file, data, action confirmation)
5. **Handoff-ready**: Each task must include a binary check for `file_exists:handoff.json`

**Output Format:**
Return a JSON array of task objects. Each task should have:
- `id`: Unique identifier (use format: task_001, task_002, etc.)
- `title`: Short descriptive title
- `description`: Detailed description of what needs to be done
- `depends_on`: Array of task IDs this task depends on (empty array if none)
- `verification_type`: One of "binary", "constraint", "qualitative", "composite"
- `binary_checks`: Array of binary checks like "file_exists:output.md" (must include "file_exists:handoff.json")
- `constraints`: Array of constraint objects like {{"type": "min_length", "value": 1000}} (optional)
- `evaluation_rubric`: Qualitative criteria for LLM evaluation (optional)
- `evaluation_policy`: Optional overrides for evaluation thresholds/requirements (e.g., {{"require_qualitative": false, "qualitative_min_score": 0.7}})
- `max_iterations`: Maximum attempts for this task (default: 10)

**Guidelines:**
- Aim for 3-7 tasks for most requests
- Put research/information gathering before synthesis/creation tasks
- If the task involves side effects (sending emails, API calls), make those separate tasks
- Be specific about what files or artifacts each task should produce

Return ONLY the JSON array, no additional text."""

    def _parse_decomposition_response(self, response: str, original_request: str) -> List[Task]:
        json_str = response.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            json_str = "\n".join(lines[1:-1])

        try:
            task_dicts = json.loads(json_str)
        except json.JSONDecodeError:
            return [
                Task(
                    id=f"task_{uuid.uuid4().hex[:8]}",
                    title="Execute user request",
                    description=original_request,
                    verification_type="qualitative",
                    evaluation_rubric="Has the user's request been fulfilled?",
                )
            ]

        tasks: List[Task] = []
        for td in task_dicts:
            verification_type = td.get("verification_type", "composite")
            minimum_score = td.get("minimum_acceptable_score")
            if minimum_score is None:
                minimum_score = 0.6 if verification_type == "qualitative" else 0.7
            task = Task(
                id=td.get("id", f"task_{uuid.uuid4().hex[:8]}"),
                title=td.get("title", "Untitled task"),
                description=td.get("description", ""),
                depends_on=td.get("depends_on", []),
                verification_type=verification_type,
                binary_checks=td.get("binary_checks", []),
                constraints=td.get("constraints", []),
                evaluation_rubric=td.get("evaluation_rubric"),
                minimum_acceptable_score=minimum_score,
                evaluation_policy=td.get("evaluation_policy"),
                max_iterations=td.get("max_iterations", 10),
            )
            tasks.append(task)

        return tasks


class HybridDecomposer(Decomposer):
    """Combines template and LLM decomposition."""

    def __init__(self, llm_client: Any, templates: Optional[Dict] = None, model: str = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-5")):
        self.template_decomposer = TemplateDecomposer(templates)
        self.llm_decomposer = LLMDecomposer(llm_client, model)

    def can_handle(self, request: str) -> bool:
        return True

    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        if self.template_decomposer.can_handle(request):
            tasks = self.template_decomposer.decompose(request, context)
            if tasks:
                return tasks
        return self.llm_decomposer.decompose(request, context)


class SubAgentDecomposer(Decomposer):
    """Decomposer that uses the task-decomposer sub-agent.
    
    This delegates decomposition to the task-decomposer sub-agent which
    has more nuanced understanding of available sub-agents and can create
    better phased plans.
    """

    def __init__(self, agent_adapter: Any, workspace_path: Any, fallback_decomposer: Optional[Decomposer] = None):
        """
        Args:
            agent_adapter: UniversalAgentAdapter with invoke_subagent() method
            workspace_path: Path to workspace for file operations
            fallback_decomposer: Optional fallback if sub-agent fails
        """
        from pathlib import Path
        self.agent_adapter = agent_adapter
        self.workspace_path = Path(workspace_path)
        self.fallback_decomposer = fallback_decomposer

    def can_handle(self, request: str) -> bool:
        return True

    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        """Decompose using sub-agent, with fallback to existing decomposer."""
        import asyncio
        try:
            # Try to run async decomposition
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run,
                        self._decompose_async(request, context)
                    )
                    return future.result()
            except RuntimeError:
                return asyncio.run(self._decompose_async(request, context))
        except Exception as e:
            print(f"[SubAgentDecomposer] Sub-agent failed: {e}, using fallback")
            if self.fallback_decomposer:
                return self.fallback_decomposer.decompose(request, context)
            # Create a single catch-all task
            return [
                Task(
                    id=f"task_{uuid.uuid4().hex[:8]}",
                    title="Execute user request",
                    description=request,
                    verification_type="qualitative",
                    evaluation_rubric="Has the user's request been fulfilled?",
                )
            ]

    async def _decompose_async(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        """Async decomposition using sub-agent."""
        prompt = self._build_decomposition_prompt(request, context)
        
        result = await self.agent_adapter.invoke_subagent(
            agent_type="task-decomposer",
            prompt=prompt,
            workspace_path=self.workspace_path,
        )
        
        # Check for macro_tasks.json
        macro_tasks = result.get("macro_tasks")
        if macro_tasks:
            return self._parse_macro_tasks(macro_tasks, request)
        
        # Fallback
        if self.fallback_decomposer:
            return self.fallback_decomposer.decompose(request, context)
        
        return [
            Task(
                id=f"task_{uuid.uuid4().hex[:8]}",
                title="Execute user request",
                description=request,
                verification_type="qualitative",
                evaluation_rubric="Has the user's request been fulfilled?",
            )
        ]

    def _build_decomposition_prompt(self, request: str, context: Optional[Dict] = None) -> str:
        """Build prompt for task-decomposer sub-agent."""
        context_section = ""
        if context:
            if context.get("learnings"):
                context_section += "\n**Prior Learnings:**\n"
                context_section += "\n".join(f"- {l}" for l in context["learnings"])
            if context.get("constraints"):
                context_section += "\n**Constraints:**\n"
                context_section += "\n".join(f"- {c}" for c in context["constraints"])
        
        return f"""# Decomposition Request

## User Request
{request}
{context_section}

## Workspace
{self.workspace_path}

## Instructions
Create `macro_tasks.json` with phases and tasks for this request.

**Composio-Anchored Decomposition:** Prefer Composio tools for deterministic atomic actions (search, email, calendar, code execution). Use subagents for multi-step workflows. Use local MCP tools for processing. Define handoff points between local phases and the Composio backbone.

**Todoist boundary policy:**
- Keep complex engineering/research execution on the normal decomposition + specialist pipeline.
- Use Todoist only for reminders, lightweight personal todos, and brainstorm backlog capture/progression.
- For Todoist intents, prefer internal `mcp__internal__todoist_*` tools before Composio Todoist connector flow.
- Do NOT rewrite multi-step implementation tasks into Todoist bookkeeping phases.

**Available Sub-Agents:** research-specialist, report-writer, image-expert, video-creation-expert, video-remotion-expert, mermaid-expert, claude-bowser-agent, playwright-bowser-agent, bowser-qa-agent, browserbase, slack-expert, youtube-expert (legacy alias: youtube-explainer-expert), system-configuration-agent, data-analyst, action-coordinator, code-writer.

**Available Composio Toolkits:** composio_search, gmail, googlecalendar, slack, codeinterpreter, googledrive, googlesheets, googledocs, github, notion, discord, youtube, airtable, hubspot, linear, browserbase, filetool, sqltool.

**Browser lane policy (mandatory):**
- Use Bowser lanes first for browser execution:
  - `claude-bowser-agent` for authenticated/real-session Chrome work
  - `playwright-bowser-agent` for isolated/repeatable/parallel runs
  - `bowser-qa-agent` for structured UI validation with screenshot evidence
- Use `browserbase` only when Bowser is unavailable or cloud-browser behavior is explicitly required.

**X (Twitter) trends:** Prefer the internal tool `mcp__internal__x_trends_posts` (xAI `x_search` evidence fetch). Fallback: `grok-x-trends` skill. Do NOT use a Composio toolkit. Preferred pattern: fetch evidence posts only, then infer themes/summarize using the primary model.

**Weather:** Use the `openweather` skill for current + forecast for any location.

**Think beyond research-and-report.** Consider: Does this need computation? Media creation? Real-world actions? Browser operations? Monitoring? Code/engineering? Knowledge capture?
For non-trivial requests, evaluate at least 4 capability domains before selecting a plan.

**CRITICAL: Phase Dependencies & Consolidation**
1. **Parallel Execution:** Tasks in the same phase run in parallel. Put independent work (research, asset creation) in early phases.
2. **Consolidation/Reporting:** If the request implies a final report or summary, you MUST create a final phase containing ONLY the consolidation task.
3. **Strict Dependencies:** The consolidation task MUST depend on ALL previous task IDs (e.g., [`research_task`, `media_task`]). Do NOT allow the report to be generated before inputs are ready.
4. **Verification:** Add a specific task to verify all artifacts exist before the final report is generated.
"""

    def _parse_macro_tasks(self, macro_tasks: Dict[str, Any], original_request: str) -> List[Task]:
        """Parse macro_tasks.json into Task objects."""
        tasks: List[Task] = []
        plan_id = uuid.uuid4().hex[:8]
        
        phases = macro_tasks.get("phases", [])
        for phase in phases:
            phase_id = phase.get("phase_id", 1)
            phase_tasks = phase.get("tasks", [])
            
            for task_def in phase_tasks:
                task_id = task_def.get("task_id", f"{plan_id}_p{phase_id}")
                
                # Parse success criteria into binary checks and constraints
                binary_checks = []
                constraints = []
                for criterion in task_def.get("success_criteria", []):
                    if "file" in criterion.lower() or "exists" in criterion.lower():
                        # Extract file path if mentioned
                        binary_checks.append(f"contains:{criterion[:50]}")
                    else:
                        constraints.append({"type": "contains", "value": criterion[:50]})
                
                # Add expected artifacts as binary checks
                for artifact in task_def.get("expected_artifacts", []):
                    binary_checks.append(f"file_exists:{artifact}")
                
                task = Task(
                    id=task_id,
                    title=task_def.get("title", "Untitled task"),
                    description=f"{task_def.get('description', '')}\n\n**Original Request:** {original_request}",
                    status=TaskStatus.PENDING,
                    verification_type="composite",
                    binary_checks=binary_checks or ["file_exists:handoff.json"],
                    constraints=constraints,
                    evaluation_rubric=task_def.get("description"),
                    minimum_acceptable_score=0.7,
                )
                tasks.append(task)
        
        return tasks if tasks else [
            Task(
                id=f"task_{uuid.uuid4().hex[:8]}",
                title="Execute user request",
                description=original_request,
                verification_type="qualitative",
                evaluation_rubric="Has the user's request been fulfilled?",
            )
        ]


@dataclass
class PlanManager:
    """Manages the task plan lifecycle."""

    state_manager: Any
    decomposer: Decomposer

    def _ensure_unique_ids(self, tasks: List[Task]) -> List[Task]:
        existing_ids = {task.id for task in self.state_manager.get_all_tasks()}
        assigned_ids = set(existing_ids)
        remap: Dict[str, str] = {}

        for task in tasks:
            candidate = task.id
            if candidate in assigned_ids:
                new_id = f"{candidate}_{uuid.uuid4().hex[:6]}"
                if candidate not in remap:
                    remap[candidate] = new_id
                task.id = new_id
            assigned_ids.add(task.id)

        if remap:
            for task in tasks:
                task.depends_on = [remap.get(dep, dep) for dep in task.depends_on]
                if task.parent_task_id in remap:
                    task.parent_task_id = remap[task.parent_task_id]

        return tasks

    def create_plan(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        self.state_manager.set_original_request(request)
        superseded = self.state_manager.mark_active_tasks_failed("New plan created")
        tasks = self.decomposer.decompose(request, context)
        tasks = self._ensure_unique_ids(tasks)
        self.state_manager.create_tasks_batch(tasks)
        self.state_manager.checkpointer.checkpoint(
            message=f"Plan created with {len(tasks)} tasks",
            iteration=0,
            metadata={"task_count": len(tasks), "superseded_tasks": superseded},
        )
        return tasks

    def revise_plan(self, reason: str, context: Optional[Dict] = None) -> List[Task]:
        self.state_manager.checkpointer.create_branch(f"replan_{uuid.uuid4().hex[:6]}")
        superseded = self.state_manager.mark_active_tasks_failed(reason)
        tasks = self.decomposer.decompose(reason, context)
        tasks = self._ensure_unique_ids(tasks)
        self.state_manager.create_tasks_batch(tasks)
        self.state_manager.checkpointer.checkpoint(
            message=f"Plan revised: {reason}",
            iteration=0,
            metadata={"reason": reason, "superseded_tasks": superseded},
        )
        return tasks

    def decompose_failed_task(self, task: Task, reason: str) -> List[Task]:
        context = {
            "constraints": [f"Failed because: {reason}"],
            "learnings": [],
        }
        new_tasks = self.decomposer.decompose(task.description, context)
        for t in new_tasks:
            t.parent_task_id = task.id
        new_tasks = self._ensure_unique_ids(new_tasks)
        self.state_manager.create_tasks_batch(new_tasks)
        return new_tasks


def estimate_task_complexity(request: str) -> int:
    length_score = min(len(request) // 200, 10)
    keyword_score = sum(
        1
        for k in ["research", "report", "analysis", "multi-step"]
        if k in request.lower()
    )
    return length_score + keyword_score


def validate_task_graph(tasks: List[Task]) -> bool:
    task_ids = {t.id for t in tasks}
    for task in tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                return False
    return True
