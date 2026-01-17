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
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod

from .state import Task, TaskStatus


DECOMPOSITION_TEMPLATES = {
    "research_report": {
        "description": "Research a topic and produce a comprehensive report",
        "keywords": ["research", "report", "summary", "status report", "investigate", "analyze", "study"],
        "tasks": [
            {
                "id_suffix": "scope",
                "title": "Define research scope and questions",
                "description": "Clarify the research questions, boundaries, and success criteria for the investigation.",
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:research_scope.md"],
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
                "binary_checks": ["file_exists:research_notes.md"],
                "constraints": [{"type": "min_length", "value": 2000}],
            },
            {
                "id_suffix": "analyze",
                "title": "Analyze and synthesize findings",
                "description": "Analyze the gathered information, identify patterns, and synthesize key insights.",
                "depends_on": ["gather"],
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:analysis_notes.md"],
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
                "binary_checks": ["file_exists:final_report.md"],
                "constraints": [{"type": "min_length", "value": 3000}],
                "evaluation_rubric": "Is the report well-structured, comprehensive, and actionable?",
            },
        ],
    },
    "email_outreach": {
        "description": "Draft and send personalized email outreach",
        "keywords": ["email", "outreach", "contact", "reach out"],
        "tasks": [
            {
                "id_suffix": "targets",
                "title": "Identify and research targets",
                "description": "Identify the recipients and gather relevant context about each.",
                "verification_type": "composite",
                "binary_checks": ["file_exists:targets.json"],
            },
            {
                "id_suffix": "template",
                "title": "Create email template",
                "description": "Draft an email template that can be personalized for each recipient.",
                "depends_on": ["targets"],
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:email_template.md"],
                "evaluation_rubric": "Is the template professional, clear, and personalizable?",
                "minimum_acceptable_score": 0.6,
            },
            {
                "id_suffix": "personalize",
                "title": "Personalize emails for each recipient",
                "description": "Create personalized versions of the email for each target recipient.",
                "depends_on": ["template"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:personalized_emails.json"],
            },
            {
                "id_suffix": "send",
                "title": "Send emails",
                "description": "Send the personalized emails to each recipient.",
                "depends_on": ["personalize"],
                "verification_type": "binary",
                "binary_checks": ["side_effect:email_sent"],
            },
        ],
    },
    "document_analysis": {
        "description": "Analyze one or more documents and extract insights",
        "keywords": ["analyze document", "review", "extract", "summarize document"],
        "tasks": [
            {
                "id_suffix": "ingest",
                "title": "Ingest and parse documents",
                "description": "Read and parse the input documents into a workable format.",
                "verification_type": "composite",
                "binary_checks": ["file_exists:parsed_content.md"],
            },
            {
                "id_suffix": "analyze",
                "title": "Analyze document content",
                "description": "Analyze the parsed content according to the specified criteria.",
                "depends_on": ["ingest"],
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:analysis_notes.md"],
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
                "binary_checks": ["file_exists:analysis_output.md"],
            },
        ],
    },
    "data_processing": {
        "description": "Process, transform, or aggregate data",
        "keywords": ["process data", "transform", "aggregate", "clean data", "ETL"],
        "tasks": [
            {
                "id_suffix": "validate",
                "title": "Validate input data",
                "description": "Check input data for completeness, format, and quality issues.",
                "verification_type": "composite",
                "binary_checks": ["file_exists:validation_report.json"],
            },
            {
                "id_suffix": "transform",
                "title": "Transform and process data",
                "description": "Apply the required transformations to the validated data.",
                "depends_on": ["validate"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:processed_data.json"],
            },
            {
                "id_suffix": "verify",
                "title": "Verify output data quality",
                "description": "Verify the processed data meets quality and completeness requirements.",
                "depends_on": ["transform"],
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:quality_report.md"],
                "evaluation_rubric": "Does the output data meet all specified requirements?",
                "minimum_acceptable_score": 0.6,
            },
        ],
    },
    "content_creation": {
        "description": "Create written content (blog post, article, documentation)",
        "keywords": ["write", "create content", "draft", "blog", "article", "documentation"],
        "tasks": [
            {
                "id_suffix": "outline",
                "title": "Create content outline",
                "description": "Develop a structured outline for the content piece.",
                "verification_type": "qualitative",
                "binary_checks": ["file_exists:outline.md"],
                "evaluation_rubric": "Does the outline have a clear structure and cover all required topics?",
                "minimum_acceptable_score": 0.6,
            },
            {
                "id_suffix": "draft",
                "title": "Write first draft",
                "description": "Write the first complete draft following the outline.",
                "depends_on": ["outline"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:draft.md"],
                "constraints": [{"type": "min_length", "value": 1500}],
            },
            {
                "id_suffix": "revise",
                "title": "Revise and polish",
                "description": "Review, revise, and polish the draft into a final version.",
                "depends_on": ["draft"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:final_content.md"],
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
        plan_id = uuid.uuid4().hex[:8]
        tasks: List[Task] = []

        id_map: Dict[str, str] = {}
        for task_def in template["tasks"]:
            task_id = f"{plan_id}_{task_def['id_suffix']}"
            id_map[task_def["id_suffix"]] = task_id

        for task_def in template["tasks"]:
            task_id = id_map[task_def["id_suffix"]]
            depends_on = [id_map[dep] for dep in task_def.get("depends_on", []) if dep in id_map]
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
                max_iterations=task_def.get("max_iterations", 10),
            )
            tasks.append(task)

        return tasks

    def _contextualize_description(self, description: str, request: str) -> str:
        return f"{description}\n\n**Original Request:** {request}"


class LLMDecomposer(Decomposer):
    """Decomposer that uses an LLM to generate task breakdown."""

    def __init__(self, llm_client: Any, model: str = "claude-sonnet-4-20250514"):
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

**Output Format:**
Return a JSON array of task objects. Each task should have:
- `id`: Unique identifier (use format: task_001, task_002, etc.)
- `title`: Short descriptive title
- `description`: Detailed description of what needs to be done
- `depends_on`: Array of task IDs this task depends on (empty array if none)
- `verification_type`: One of "binary", "constraint", "qualitative", "composite"
- `binary_checks`: Array of binary checks like "file_exists:output.md" (optional)
- `constraints`: Array of constraint objects like {{"type": "min_length", "value": 1000}} (optional)
- `evaluation_rubric`: Qualitative criteria for LLM evaluation (optional)
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
                max_iterations=td.get("max_iterations", 10),
            )
            tasks.append(task)

        return tasks


class HybridDecomposer(Decomposer):
    """Combines template and LLM decomposition."""

    def __init__(self, llm_client: Any, templates: Optional[Dict] = None, model: str = "claude-sonnet-4-20250514"):
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
