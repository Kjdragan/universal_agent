Implementing interactive planning mode in Claude Agent SDK
The Claude Agent SDK does not natively support a planning/interview mode despite having a plan permission type in its definitions—you'll need to build this functionality using custom MCP tools and conversation management. The good news: the SDK provides all the building blocks to replicate Claude Code's 4-phase planning workflow, including structured output, multi-turn conversations, and custom tool creation. claude
Native SDK support is limited but building blocks exist
The Claude Agent SDK's ClaudeAgentOptions dataclass includes a permission_mode parameter with four documented modes: default, acceptEdits, bypassPermissions, and notably plan. Claude DocsHexDocs However, plan mode is explicitly not supported in the current SDK according to official documentation—it exists only in the type definitions. Similarly, the built-in AskUserQuestion tool exists but fails silently in SDK mode because the subprocess has no attached TTY for terminal interaction. MediumGitHub
The permission modes that actually work follow this hierarchy:
ModeBehaviordefaultAll tools trigger the canUseTool callbackacceptEditsFile operations auto-approved; others require callbackbypassPermissionsAll tools run without permission promptsdontAskAuto-deny unless explicitly allowed
The SDK does provide robust structured output via JSON Schema through the output_format option—critical for generating typed plan objects: Claude Docs
pythonoptions = ClaudeAgentOptions(
    output_format={"type": "json_schema", "schema": Plan.model_json_schema()},
    allowed_tools=["Read", "Glob", "Grep"]
)
Claude Code's planning workflow operates through prompt engineering
Claude Code implements planning mode primarily through system prompt injection rather than tool-level restrictions. Armin Ronacher The workflow follows four phases that can be replicated in any SDK application:
Phase 1 (Initial Understanding): Read codebase and ask clarifying questions using AskUserQuestion tool. Focus on understanding the user's request and relevant code.
Phase 2 (Design): Generate implementation approach with comprehensive context from exploration—filenames, code paths, requirements, and constraints.
Phase 3 (Review): Verify plans align with user intentions. Use additional questions to clarify remaining ambiguities.
Phase 4 (Final Plan): Write the plan to a markdown file, including only the recommended approach with paths to critical files. Armin Ronacher
The key insight: Claude Code's plan mode doesn't actually restrict tool execution—it uses prompt reinforcement stating "you MUST NOT make any edits" Claude Docs combined with two special tools: EnterPlanMode (state transition) and ExitPlanMode (signals completion for user review). Claude Docs Plans are stored as markdown files in the .claude/ directory. Armin Ronacher
Build a custom terminal input tool for interviews
Since the native AskUserQuestion tool doesn't work in SDK environments, create a custom MCP tool that handles terminal interaction: Medium
pythonfrom claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeSDKClient, ClaudeAgentOptions
import asyncio

@tool("ask_user", "Ask the user a question and get their response", {
    "question": str,
    "category": str,
    "options": list  # Optional predefined choices
})
async def ask_user_tool(args):
    """Terminal-based user input tool."""
    question = args["question"]
    category = args.get("category", "general")
    options = args.get("options", [])
    
    print(f"\n📋 [{category.upper()}]")
    print(f"❓ {question}")
    
    if options:
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

# Create MCP server hosting the tool
interview_server = create_sdk_mcp_server(
    name="interview",
    version="1.0.0", 
    tools=[ask_user_tool]
)
Register this tool while disabling the native AskUserQuestion to force Claude to use your terminal-compatible version: GitHub
pythonoptions = ClaudeAgentOptions(
    mcp_servers={"interview": interview_server},
    allowed_tools=["mcp__interview__ask_user", "Read", "Glob"],
    disallowed_tools=["AskUserQuestion"],  # Block non-functional native tool
    system_prompt=PLANNING_SYSTEM_PROMPT
)
Design a multi-turn conversation loop with state management
Use ClaudeSDKClient instead of query() to maintain conversation context across interview rounds. GitHub +2 Combine with a state management class to track collected requirements:
pythonfrom dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict

class InterviewPhase(Enum):
    INTRODUCTION = "introduction"
    DISCOVERY = "discovery"
    REQUIREMENTS = "requirements"
    REVIEW = "review"
    COMPLETE = "complete"

@dataclass
class InterviewState:
    phase: InterviewPhase = InterviewPhase.INTRODUCTION
    collected_data: Dict[str, List[dict]] = field(default_factory=dict)
    questions_asked: List[str] = field(default_factory=list)
    
    def add_response(self, category: str, question: str, answer: str):
        self.questions_asked.append(question)
        if category not in self.collected_data:
            self.collected_data[category] = []
        self.collected_data[category].append({
            "question": question,
            "answer": answer
        })

# The interview loop
async def conduct_planning_interview(initial_task: str):
    state = InterviewState()
    
    async with ClaudeSDKClient(options=interview_options) as client:
        await client.query(f"The user wants to accomplish: {initial_task}\n\nBegin the planning interview.")
        
        async for message in client.receive_response():
            # Process messages, handle tool calls, extract final plan
            ...
Structure your plan schema with Pydantic for typed extraction
Define a comprehensive schema supporting atomic tasks grouped into phases:
pythonfrom pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID, uuid4
from datetime import datetime
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"

class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class AtomicTask(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., max_length=100)
    description: str = Field(default="")
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM)
    dependencies: List[UUID] = Field(default_factory=list)
    estimated_duration_minutes: Optional[int] = None
    context: dict = Field(default_factory=dict)
    output: Optional[dict] = None

class Phase(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    order: int = Field(..., ge=0)
    tasks: List[AtomicTask] = Field(default_factory=list)
    status: TaskStatus = Field(default=TaskStatus.PENDING)

class Plan(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    phases: List[Phase] = Field(default_factory=list)
    version: str = "1.0.0"
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    session_id: Optional[str] = None
    global_context: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
Extract structured plans from Claude's response using JSON Schema output format: Claude Docs
pythonoptions = ClaudeAgentOptions(
    output_format={"type": "json_schema", "schema": Plan.model_json_schema()},
    system_prompt="After the interview, output a structured Plan object."
)

async for message in client.receive_response():
    if hasattr(message, 'structured_output') and message.structured_output:
        plan = Plan.model_validate(message.structured_output)
Persist plans to both JSON files and SQLite
For file persistence, leverage Pydantic's built-in serialization:
pythonfrom pathlib import Path

class PlanPersistence:
    def __init__(self, storage_dir: Path):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
    
    def save_plan(self, plan: Plan) -> Path:
        filepath = self.storage_dir / f"plan_{plan.id}.json"
        filepath.write_text(plan.model_dump_json(indent=2))
        return filepath
    
    def load_plan(self, filepath: Path) -> Plan:
        return Plan.model_validate_json(filepath.read_text())
For SQLite persistence supporting cross-session execution:
pythonimport sqlite3
import json

class SQLitePlanStore:
    def __init__(self, db_path: str = "plans.db"):
        self.db_path = db_path
        self._init_schema()
    
    def _init_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    data TEXT NOT NULL,  -- Full JSON
                    status TEXT DEFAULT 'pending',
                    session_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    phase_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    dependencies TEXT,  -- JSON array of task IDs
                    FOREIGN KEY (plan_id) REFERENCES plans(id)
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """)
    
    def save_plan(self, plan: Plan):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO plans (id, name, data, status, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (str(plan.id), plan.name, plan.model_dump_json(), 
                  plan.status.value, plan.session_id,
                  plan.created_at.isoformat(), plan.updated_at.isoformat()))
            
            # Also store individual tasks for efficient querying
            for phase in plan.phases:
                for task in phase.tasks:
                    conn.execute("""
                        INSERT OR REPLACE INTO tasks (id, plan_id, phase_id, name, status, dependencies)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (str(task.id), str(plan.id), str(phase.id), task.name,
                          task.status.value, json.dumps([str(d) for d in task.dependencies])))
    
    def get_pending_tasks(self, plan_id: str) -> List[AtomicTask]:
        """Get tasks ready for execution (all dependencies completed)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT t.* FROM tasks t
                WHERE t.plan_id = ? AND t.status = 'pending'
                AND NOT EXISTS (
                    SELECT 1 FROM tasks dep 
                    WHERE dep.id IN (SELECT value FROM json_each(t.dependencies))
                    AND dep.status != 'completed'
                )
            """, (plan_id,)).fetchall()
            # Convert rows to AtomicTask objects...
Complete implementation architecture
The full system combines these components:
pythonPLANNING_SYSTEM_PROMPT = """You are a planning agent conducting a requirements interview.

## Interview Process:
1. Ask the user about their massive task
2. Use ask_user tool to gather requirements across these categories:
   - Project Overview (goals, scope, success criteria)
   - Technical Requirements (constraints, integrations, stack)
   - User Stories (who benefits, key workflows)
   - Timeline (deadlines, milestones, phases)

3. Ask 2-3 questions per category, following up on interesting answers
4. After gathering requirements, generate a structured Plan

## Output:
When complete, output a Plan with:
- Atomic tasks grouped into logical phases
- Clear dependencies between tasks
- Estimated durations where possible
- Tasks should be small enough to complete in one session
"""

async def run_planning_interview():
    # 1. Get initial task from user
    massive_task = input("What massive task do you want to accomplish?\n> ")
    
    # 2. Create interview agent
    options = ClaudeAgentOptions(
        mcp_servers={"interview": interview_server},
        allowed_tools=["mcp__interview__ask_user", "Read", "Glob", "Grep"],
        disallowed_tools=["AskUserQuestion"],
        output_format={"type": "json_schema", "schema": Plan.model_json_schema()},
        system_prompt=PLANNING_SYSTEM_PROMPT,
        max_turns=30
    )
    
    # 3. Conduct interview
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"User's massive task: {massive_task}\n\nBegin the planning interview.")
        
        plan = None
        async for message in client.receive_response():
            if hasattr(message, 'structured_output') and message.structured_output:
                plan = Plan.model_validate(message.structured_output)
                break
    
    # 4. Persist the plan
    if plan:
        persistence = PlanPersistence(Path("./plans"))
        filepath = persistence.save_plan(plan)
        
        store = SQLitePlanStore("plans.db")
        store.save_plan(plan)
        
        print(f"\n✅ Plan saved: {filepath}")
        print(f"   Phases: {len(plan.phases)}")
        print(f"   Total tasks: {sum(len(p.tasks) for p in plan.phases)}")
        
        return plan

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_planning_interview())
Conclusion
Building an interactive planning mode in the Claude Agent SDK requires custom implementation since native support doesn't exist—but the SDK provides sufficient primitives. The key architectural decisions are: (1) create a custom MCP tool for terminal input Medium using asyncio.to_thread(input, ...), (2) use ClaudeSDKClient for multi-turn conversation context, Redreamality's Blog (3) leverage JSON Schema structured output for type-safe plan extraction, Claude Docs and (4) implement dual persistence (JSON for portability, SQLite for querying). GitHub Claude Code's 4-phase workflow (Understand → Design → Review → Plan) can be fully replicated through system prompt engineering rather than special SDK features. Armin Ronacher