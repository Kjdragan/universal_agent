import asyncio
from datetime import datetime
from typing import Dict, Optional, Any
import uuid

class Task:
    def __init__(self, user_id: int, prompt: str):
        self.id = str(uuid.uuid4())
        self.user_id = user_id
        self.prompt = prompt
        self.status = "pending"  # pending, running, completed, error
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.result: Any = None
        self.log_file: Optional[str] = None
        self.workspace: Optional[str] = None

class TaskManager:
    def __init__(self, status_callback=None):
        self.tasks: Dict[str, Task] = {}
        self.queue = asyncio.Queue()
        self.active_tasks = 0
        self.status_callback = status_callback
        from .config import MAX_CONCURRENT_TASKS
        self.max_concurrent = MAX_CONCURRENT_TASKS

    async def add_task(self, user_id: int, prompt: str) -> str:
        task = Task(user_id, prompt)
        self.tasks[task.id] = task
        await self.queue.put(task.id)
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
                print(f"🚀 Starting task {task_id} for user {task.user_id}")
                # Execute via adapter
                # agent_adapter.execute MUST be awaitable and return result info
                await agent_adapter.execute(task)
                
                # Status is updated by adapter usually? Or here?
                # If adapter returns, we assume success unless it raised exception.
                # However, adapter might update `task.result`.
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
