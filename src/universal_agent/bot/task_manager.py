import asyncio
from datetime import datetime
from typing import Dict, Optional, Any, Set
import uuid

class Task:
    def __init__(self, user_id: int, prompt: str, continue_session: bool = False):
        self.id = str(uuid.uuid4())
        self.user_id = user_id
        self.prompt = prompt
        self.continue_session = continue_session  # Whether to continue previous session
        self.status = "pending"  # pending, running, completed, error
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.result: Any = None
        self.log_file: Optional[str] = None
        self.workspace: Optional[str] = None
        self.execution_summary: Optional[Any] = None  # Holds ExecutionResult object

class TaskManager:
    def __init__(self, status_callback=None):
        self.tasks: Dict[str, Task] = {}
        self.queue = asyncio.Queue()
        self.active_tasks = 0
        self.status_callback = status_callback
        from .config import MAX_CONCURRENT_TASKS
        self.max_concurrent = MAX_CONCURRENT_TASKS
        
        # Track which users have continuation mode enabled
        self.continuation_mode: Set[int] = set()

    def enable_continuation(self, user_id: int):
        """Enable continuation mode for a user. Next /agent will reuse session."""
        self.continuation_mode.add(user_id)
        
    def disable_continuation(self, user_id: int):
        """Disable continuation mode for a user."""
        self.continuation_mode.discard(user_id)
        
    def is_continuation_enabled(self, user_id: int) -> bool:
        """Check if user has continuation mode enabled."""
        return user_id in self.continuation_mode

    async def add_task(self, user_id: int, prompt: str) -> str:
        # Check if continuation mode is enabled for this user
        continue_session = self.is_continuation_enabled(user_id)
        
        task = Task(user_id, prompt, continue_session=continue_session)
        self.tasks[task.id] = task
        await self.queue.put(task.id)
        
        # After queuing, continuation mode stays ON until user does /new
        # (they can keep sending /agent to continue)
        
        return task.id

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)
    
    def get_user_tasks(self, user_id: int):
        # Return tasks sorted by creation time (newest first)
        tasks = [t for t in self.tasks.values() if t.user_id == user_id]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    async def worker(self, agent_adapter):
        """
        Continuous worker loop to process tasks.
        agent_adapter: Instance of AgentAdapter class
        """
        print("👷 Task Manager Worker Started")
        while True:
            task_id = await self.queue.get()
            
            # Simple concurrency control
            while self.active_tasks >= self.max_concurrent:
                await asyncio.sleep(1)
            
            self.active_tasks += 1
            task = self.tasks[task_id]
            
            # Update status
            task.status = "running"
            task.started_at = datetime.now()
            if self.status_callback:
                asyncio.create_task(self.status_callback(task))
            
            try:
                mode = "CONTINUE" if task.continue_session else "FRESH"
                print(f"🚀 Starting task {task_id} for user {task.user_id} [{mode}]")
                
                # Execute via adapter with continuation flag
                await agent_adapter.execute(task, continue_session=task.continue_session)
                
                if task.status == "running":
                    task.status = "completed"
                    
            except Exception as e:
                print(f"❌ Task {task_id} failed: {e}")
                task.status = "error"
                task.result = f"Exception: {str(e)}"
            finally:
                task.completed_at = datetime.now()
                self.active_tasks -= 1
                if self.status_callback:
                    asyncio.create_task(self.status_callback(task))
                self.queue.task_done()
