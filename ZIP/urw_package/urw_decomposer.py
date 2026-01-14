"""
Universal Ralph Wrapper - Task Decomposer

Decomposes user requests into atomic tasks that:
1. Fit within a context window
2. Have clear completion criteria
3. Produce artifacts that can be handed off
4. Can be executed independently once dependencies are met

Unlike Ralph's static PRD.json, this decomposition is dynamic and
can be revised during execution based on learnings.
"""

import json
import uuid
from typing import List, Dict, Optional, Any, Callable
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from urw_state import Task, TaskStatus


# =============================================================================
# DECOMPOSITION TEMPLATES
# =============================================================================

# Common patterns for universal tasks. These provide structure without
# requiring LLM decomposition for known patterns.

DECOMPOSITION_TEMPLATES = {
    "research_report": {
        "description": "Research a topic and produce a comprehensive report",
        "keywords": ["research", "report", "investigate", "analyze", "study"],
        "tasks": [
            {
                "id_suffix": "scope",
                "title": "Define research scope and questions",
                "description": "Clarify the research questions, boundaries, and success criteria for the investigation.",
                "verification_type": "qualitative",
                "evaluation_rubric": "Are the research questions clear, specific, and answerable?",
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
                "evaluation_rubric": "Does the analysis identify clear patterns and provide actionable insights?",
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
        ]
    },
    
    "email_outreach": {
        "description": "Draft and send personalized email outreach",
        "keywords": ["email", "outreach", "contact", "reach out", "message"],
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
                "evaluation_rubric": "Is the template professional, clear, and personalizable?",
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
                "binary_checks": ["side_effect:emails_sent"],
            },
        ]
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
                "evaluation_rubric": "Does the analysis address all requested aspects?",
            },
            {
                "id_suffix": "output",
                "title": "Generate analysis output",
                "description": "Format the analysis results in the requested output format.",
                "depends_on": ["analyze"],
                "verification_type": "composite",
                "binary_checks": ["file_exists:analysis_output.md"],
            },
        ]
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
                "evaluation_rubric": "Does the output data meet all specified requirements?",
            },
        ]
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
                "evaluation_rubric": "Does the outline have a clear structure and cover all required topics?",
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
        ]
    },
}


# =============================================================================
# DECOMPOSER INTERFACE
# =============================================================================

class Decomposer(ABC):
    """Abstract base class for task decomposition strategies."""
    
    @abstractmethod
    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        """
        Decompose a user request into a list of atomic tasks.
        
        Args:
            request: The user's original request
            context: Optional context (prior learnings, constraints, etc.)
        
        Returns:
            List of Task objects representing the decomposed work
        """
        pass
    
    @abstractmethod
    def can_handle(self, request: str) -> bool:
        """Check if this decomposer can handle the given request."""
        pass


# =============================================================================
# TEMPLATE DECOMPOSER
# =============================================================================

class TemplateDecomposer(Decomposer):
    """
    Decomposer that matches requests to predefined templates.
    Fast and deterministic for known patterns.
    """
    
    def __init__(self, templates: Optional[Dict] = None):
        self.templates = templates or DECOMPOSITION_TEMPLATES
    
    def can_handle(self, request: str) -> bool:
        """Check if request matches any template keywords."""
        request_lower = request.lower()
        for template in self.templates.values():
            for keyword in template.get('keywords', []):
                if keyword in request_lower:
                    return True
        return False
    
    def _match_template(self, request: str) -> Optional[str]:
        """Find the best matching template for a request."""
        request_lower = request.lower()
        best_match = None
        best_score = 0
        
        for name, template in self.templates.items():
            score = 0
            for keyword in template.get('keywords', []):
                if keyword in request_lower:
                    score += len(keyword)  # Longer matches score higher
            
            if score > best_score:
                best_score = score
                best_match = name
        
        return best_match
    
    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        """Decompose using matched template."""
        template_name = self._match_template(request)
        if not template_name:
            return []
        
        template = self.templates[template_name]
        plan_id = uuid.uuid4().hex[:8]
        tasks = []
        
        # Build task ID mapping for dependency resolution
        id_map = {}
        for task_def in template['tasks']:
            task_id = f"{plan_id}_{task_def['id_suffix']}"
            id_map[task_def['id_suffix']] = task_id
        
        for task_def in template['tasks']:
            task_id = id_map[task_def['id_suffix']]
            
            # Resolve dependency IDs
            depends_on = []
            for dep_suffix in task_def.get('depends_on', []):
                if dep_suffix in id_map:
                    depends_on.append(id_map[dep_suffix])
            
            task = Task(
                id=task_id,
                title=task_def['title'],
                description=self._contextualize_description(
                    task_def['description'], request
                ),
                status=TaskStatus.PENDING,
                depends_on=depends_on,
                verification_type=task_def.get('verification_type', 'composite'),
                binary_checks=task_def.get('binary_checks', []),
                constraints=task_def.get('constraints', []),
                evaluation_rubric=task_def.get('evaluation_rubric'),
                max_iterations=task_def.get('max_iterations', 10),
            )
            tasks.append(task)
        
        return tasks
    
    def _contextualize_description(self, description: str, request: str) -> str:
        """Add user request context to task description."""
        return f"{description}\n\n**Original Request:** {request}"


# =============================================================================
# LLM DECOMPOSER
# =============================================================================

class LLMDecomposer(Decomposer):
    """
    Decomposer that uses an LLM to generate task breakdown.
    More flexible than templates, handles novel requests.
    """
    
    def __init__(self, llm_client: Any, model: str = "claude-sonnet-4-20250514"):
        """
        Args:
            llm_client: Anthropic client or compatible interface
            model: Model to use for decomposition
        """
        self.llm_client = llm_client
        self.model = model
    
    def can_handle(self, request: str) -> bool:
        """LLM decomposer can handle any request."""
        return True
    
    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        """Use LLM to decompose the request into tasks."""
        
        prompt = self._build_decomposition_prompt(request, context)
        
        response = self.llm_client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Parse the response
        response_text = response.content[0].text
        return self._parse_decomposition_response(response_text, request)
    
    def _build_decomposition_prompt(self, request: str, 
                                    context: Optional[Dict] = None) -> str:
        """Build the prompt for LLM decomposition."""
        
        context_section = ""
        if context:
            if context.get('learnings'):
                context_section += "\n**Prior Learnings:**\n"
                context_section += "\n".join(f"- {l}" for l in context['learnings'])
            if context.get('constraints'):
                context_section += "\n**Constraints:**\n"
                context_section += "\n".join(f"- {c}" for c in context['constraints'])
        
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
    
    def _parse_decomposition_response(self, response: str, 
                                       original_request: str) -> List[Task]:
        """Parse LLM response into Task objects."""
        # Extract JSON from response (handle markdown code blocks)
        json_str = response.strip()
        if json_str.startswith("```"):
            # Remove markdown code block
            lines = json_str.split('\n')
            json_str = '\n'.join(lines[1:-1])
        
        try:
            task_dicts = json.loads(json_str)
        except json.JSONDecodeError:
            # If JSON parsing fails, return a single generic task
            return [Task(
                id=f"task_{uuid.uuid4().hex[:8]}",
                title="Execute user request",
                description=original_request,
                verification_type="qualitative",
                evaluation_rubric="Has the user's request been fulfilled?",
            )]
        
        tasks = []
        for td in task_dicts:
            task = Task(
                id=td.get('id', f"task_{uuid.uuid4().hex[:8]}"),
                title=td.get('title', 'Untitled task'),
                description=td.get('description', ''),
                depends_on=td.get('depends_on', []),
                verification_type=td.get('verification_type', 'composite'),
                binary_checks=td.get('binary_checks', []),
                constraints=td.get('constraints', []),
                evaluation_rubric=td.get('evaluation_rubric'),
                max_iterations=td.get('max_iterations', 10),
            )
            tasks.append(task)
        
        return tasks


# =============================================================================
# HYBRID DECOMPOSER
# =============================================================================

class HybridDecomposer(Decomposer):
    """
    Combines template and LLM decomposition.
    Uses templates for known patterns (fast, deterministic),
    falls back to LLM for novel requests (flexible).
    """
    
    def __init__(self, llm_client: Any, 
                 templates: Optional[Dict] = None,
                 model: str = "claude-sonnet-4-20250514"):
        self.template_decomposer = TemplateDecomposer(templates)
        self.llm_decomposer = LLMDecomposer(llm_client, model)
    
    def can_handle(self, request: str) -> bool:
        """Hybrid can handle any request."""
        return True
    
    def decompose(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        """
        Try template first, fall back to LLM if no match.
        """
        if self.template_decomposer.can_handle(request):
            tasks = self.template_decomposer.decompose(request, context)
            if tasks:
                return tasks
        
        return self.llm_decomposer.decompose(request, context)


# =============================================================================
# PLAN MANAGER
# =============================================================================

class PlanManager:
    """
    Manages the task plan lifecycle:
    - Creation from decomposition
    - Dynamic revision based on learnings
    - Re-decomposition of failed tasks
    """
    
    def __init__(self, state_manager, decomposer: Decomposer):
        """
        Args:
            state_manager: URWStateManager instance
            decomposer: Decomposer to use for task breakdown
        """
        self.state_manager = state_manager
        self.decomposer = decomposer
    
    def create_plan(self, request: str, context: Optional[Dict] = None) -> List[Task]:
        """
        Create a new plan from a user request.
        
        Args:
            request: User's original request
            context: Optional context (prior learnings, etc.)
        
        Returns:
            List of created Task objects
        """
        # Store original request
        self.state_manager.set_original_request(request)
        
        # Decompose into tasks
        tasks = self.decomposer.decompose(request, context)
        
        # Store tasks in state manager
        self.state_manager.create_tasks_batch(tasks)
        
        # Create initial checkpoint
        self.state_manager.checkpointer.checkpoint(
            message=f"Plan created with {len(tasks)} tasks",
            iteration=0,
            metadata={"task_count": len(tasks), "request_preview": request[:100]}
        )
        
        return tasks
    
    def revise_plan(self, reason: str, 
                    failed_task_id: Optional[str] = None,
                    context: Optional[Dict] = None) -> List[Task]:
        """
        Revise the plan based on learnings or failures.
        
        This is called when:
        - A task fails repeatedly
        - New information changes the approach
        - Dependencies reveal additional work needed
        
        Args:
            reason: Why we're revising the plan
            failed_task_id: ID of task that triggered revision (if any)
            context: Additional context for re-decomposition
        
        Returns:
            List of new/modified Task objects
        """
        # Get current state
        original_request = self.state_manager.get_original_request()
        completed_tasks = self.state_manager.get_tasks_by_status(TaskStatus.COMPLETE)
        failed_approaches = self.state_manager.get_failed_approaches()
        
        # Build context for re-decomposition
        revision_context = context or {}
        revision_context['learnings'] = revision_context.get('learnings', [])
        revision_context['learnings'].append(f"Plan revision triggered: {reason}")
        
        # Add completed work as context
        if completed_tasks:
            revision_context['completed_work'] = [
                f"- {t.title}: Complete" for t in completed_tasks
            ]
        
        # Add failed approaches
        if failed_approaches:
            revision_context['failed_approaches'] = [
                f"- {f['approach']}: {f['why_failed']}" for f in failed_approaches
            ]
        
        # Re-decompose remaining work
        remaining_request = f"""
Original request: {original_request}

Work already completed:
{chr(10).join(revision_context.get('completed_work', ['None']))}

Approaches that failed:
{chr(10).join(revision_context.get('failed_approaches', ['None']))}

Revision reason: {reason}

Please create a revised plan for the remaining work, taking into account what has been learned.
"""
        
        new_tasks = self.decomposer.decompose(remaining_request, revision_context)
        
        # Mark old pending tasks as superseded (optional: could delete instead)
        # For now, just add the new tasks
        self.state_manager.create_tasks_batch(new_tasks)
        
        # Checkpoint the revision
        self.state_manager.checkpointer.checkpoint(
            message=f"Plan revised: {reason}",
            iteration=0,  # Will be updated by orchestrator
            metadata={"new_task_count": len(new_tasks), "reason": reason}
        )
        
        return new_tasks
    
    def decompose_failed_task(self, task: Task, 
                              failure_reason: str) -> List[Task]:
        """
        Break a failed task into smaller sub-tasks.
        
        Args:
            task: The task that failed
            failure_reason: Why it failed
        
        Returns:
            List of new sub-tasks
        """
        decomposition_request = f"""
The following task failed and needs to be broken into smaller steps:

Task: {task.title}
Description: {task.description}
Failure reason: {failure_reason}

Please break this into 2-4 smaller, more manageable tasks that together accomplish the original goal.
"""
        
        sub_tasks = self.decomposer.decompose(decomposition_request)
        
        # Set parent relationship
        for sub_task in sub_tasks:
            sub_task.parent_task_id = task.id
        
        # First sub-task inherits original dependencies
        if sub_tasks:
            sub_tasks[0].depends_on = task.depends_on.copy()
            
            # Chain subsequent sub-tasks
            for i in range(1, len(sub_tasks)):
                sub_tasks[i].depends_on = [sub_tasks[i-1].id]
        
        # Store sub-tasks
        self.state_manager.create_tasks_batch(sub_tasks)
        
        # Mark original task as superseded
        self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
        
        return sub_tasks
    
    def get_plan_summary(self) -> Dict:
        """Get a summary of the current plan."""
        stats = self.state_manager.get_completion_stats()
        tasks = self.state_manager.get_all_tasks()
        
        return {
            "original_request": self.state_manager.get_original_request(),
            "total_tasks": len(tasks),
            "status": stats,
            "is_complete": self.state_manager.is_plan_complete(),
            "next_task": self.state_manager.get_next_task(),
        }


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def estimate_task_complexity(task: Task) -> str:
    """
    Estimate task complexity for context budget planning.
    Returns: 'simple', 'moderate', or 'complex'
    """
    # Simple heuristics based on description length and verification requirements
    desc_length = len(task.description)
    has_qualitative = task.evaluation_rubric is not None
    num_constraints = len(task.constraints)
    num_checks = len(task.binary_checks)
    
    score = 0
    
    if desc_length > 500:
        score += 2
    elif desc_length > 200:
        score += 1
    
    if has_qualitative:
        score += 2
    
    score += num_constraints
    score += num_checks // 2
    
    if score >= 5:
        return 'complex'
    elif score >= 2:
        return 'moderate'
    else:
        return 'simple'


def validate_task_graph(tasks: List[Task]) -> List[str]:
    """
    Validate that the task graph is valid (no cycles, all deps exist).
    Returns list of error messages (empty if valid).
    """
    errors = []
    task_ids = {t.id for t in tasks}
    
    # Check all dependencies exist
    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id not in task_ids:
                errors.append(f"Task '{task.id}' depends on non-existent task '{dep_id}'")
    
    # Check for cycles using DFS
    visited = set()
    rec_stack = set()
    
    def has_cycle(task_id: str) -> bool:
        visited.add(task_id)
        rec_stack.add(task_id)
        
        task = next((t for t in tasks if t.id == task_id), None)
        if task:
            for dep_id in task.depends_on:
                if dep_id not in visited:
                    if has_cycle(dep_id):
                        return True
                elif dep_id in rec_stack:
                    return True
        
        rec_stack.remove(task_id)
        return False
    
    for task in tasks:
        if task.id not in visited:
            if has_cycle(task.id):
                errors.append(f"Cycle detected involving task '{task.id}'")
                break
    
    return errors
