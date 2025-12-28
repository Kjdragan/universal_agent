# Telegram Bot Integration for Claude Agent SDK Multi-Agent System

## Implementation Guide for AI Programmer

**Project Goal**: Create a Telegram bot interface that allows remote triggering of an existing Claude Agent SDK multi-agent system from a mobile phone, with async execution and activity logging.

**Critical Constraint**: This is an INTEGRATION LAYER. The existing multi-agent system code should NOT be rewritten. We are wrapping it with a new entry point.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    DOCKER CONTAINER                             │
│                                                                 │
│  ┌─────────────┐     ┌─────────────┐     ┌──────────────────┐  │
│  │ FastAPI     │────▶│ Task        │────▶│ EXISTING AGENT   │  │
│  │ Webhook     │     │ Manager     │     │ SYSTEM           │  │
│  │ Handler     │     │ (asyncio)   │     │ (unchanged)      │  │
│  └─────────────┘     └─────────────┘     └──────────────────┘  │
│        ▲                   │                     │              │
│        │                   ▼                     ▼              │
│  ┌─────────────┐     ┌─────────────┐     ┌──────────────────┐  │
│  │ Telegram    │     │ Result      │     │ File-Based       │  │
│  │ Signature   │     │ Callback    │     │ Coordination     │  │
│  │ Verify      │     │ Service     │     │ (/app/workspace) │  │
│  └─────────────┘     └─────────────┘     └──────────────────┘  │
│                            │                                    │
└────────────────────────────┼────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │ Telegram Bot    │
                    │ API             │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ User's Phone    │
                    └─────────────────┘
```

**Key Principle**: The `EXISTING AGENT SYSTEM` box is the current codebase. We create an adapter function that calls into it.

---

## Project Structure

Create this structure alongside (or wrapping) the existing project:

```
project-root/
├── bot/                          # NEW: Telegram bot integration
│   ├── __init__.py
│   ├── main.py                   # FastAPI app + webhook handlers
│   ├── config.py                 # Environment/secrets loading
│   ├── task_manager.py           # Async task queue management
│   ├── telegram_handlers.py      # Bot command handlers
│   ├── agent_adapter.py          # ADAPTER: Bridges bot → existing agent system
│   └── execution_logger.py       # Activity logging for visibility
│
├── agent_system/                 # EXISTING: Your multi-agent code (UNCHANGED)
│   ├── __init__.py
│   ├── orchestrator.py
│   ├── subagents/
│   └── ... (existing structure)
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── scripts/
│   ├── dev.sh                    # Local development launcher
│   ├── register_webhook.py       # Webhook registration helper
│   └── setup_secrets.sh          # First-time secret setup
│
├── secrets/                      # Git-ignored, local only
│   ├── anthropic_api_key.txt
│   ├── telegram_bot_token.txt
│   └── webhook_secret.txt
│
├── requirements.txt              # Add bot dependencies to existing
├── .env.example                  # Template for environment variables
├── .gitignore
└── README.md
```

---

## Integration Point: The Agent Adapter

This is the ONLY file that connects the bot to the existing agent system. It should call whatever entry point the existing system uses.

### bot/agent_adapter.py

```python
"""
AGENT ADAPTER
=============
This module bridges the Telegram bot interface to the existing multi-agent system.
Modify only this file to connect to your specific agent implementation.

The existing agent system should NOT be modified. This adapter wraps it.
"""

import asyncio
from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass
from datetime import datetime

from execution_logger import ExecutionLogger

# ---------------------------------------------------------------------
# IMPORT YOUR EXISTING AGENT SYSTEM HERE
# Adjust this import to match your actual project structure
# ---------------------------------------------------------------------
# Example imports (replace with actual):
# from agent_system.orchestrator import run_research_pipeline
# from agent_system.main import execute_agent_task
# ---------------------------------------------------------------------


@dataclass
class AgentResult:
    """Standardized result from agent execution."""
    success: bool
    content: str                          # Main text output
    artifacts: list = None                # List of generated files
    execution_log: bytes = None           # Downloadable log file
    error: Optional[str] = None
    duration_seconds: float = 0.0
    
    def __post_init__(self):
        self.artifacts = self.artifacts or []


@dataclass
class Artifact:
    """A file generated by the agent system."""
    filename: str
    content: bytes
    description: str
    mime_type: str = "text/plain"


async def run_agent(
    prompt: str,
    workspace_dir: str = "/app/workspace",
    progress_callback: Optional[Callable[[float, str], None]] = None,
    task_id: str = None
) -> AgentResult:
    """
    Main entry point called by the Telegram bot.
    
    This function should:
    1. Initialize logging
    2. Call your existing agent system
    3. Capture results and any generated files
    4. Return standardized AgentResult
    
    Args:
        prompt: The user's request/query
        workspace_dir: Directory for agent file operations (persisted via Docker volume)
        progress_callback: Optional callback(percent: float, status: str) for progress updates
        task_id: Unique identifier for this task
        
    Returns:
        AgentResult with content, artifacts, and execution log
    """
    start_time = datetime.utcnow()
    logger = ExecutionLogger(task_id=task_id)
    
    # Create task-specific workspace subdirectory
    task_workspace = Path(workspace_dir) / f"task_{task_id}"
    task_workspace.mkdir(parents=True, exist_ok=True)
    
    logger.info("Task started", prompt_preview=prompt[:100])
    
    try:
        # Report initial progress
        if progress_callback:
            await progress_callback(0.1, "Initializing agent system...")
        
        # -------------------------------------------------------------
        # CALL YOUR EXISTING AGENT SYSTEM HERE
        # Replace this section with your actual agent invocation
        # -------------------------------------------------------------
        
        # Example 1: If your system has an async entry point
        # result = await your_orchestrator.run(
        #     prompt=prompt,
        #     workspace=str(task_workspace),
        #     on_progress=progress_callback
        # )
        
        # Example 2: If your system is synchronous, run in executor
        # loop = asyncio.get_event_loop()
        # result = await loop.run_in_executor(
        #     None,
        #     your_sync_agent_function,
        #     prompt,
        #     str(task_workspace)
        # )
        
        # Example 3: If using Claude Agent SDK directly
        # from anthropic import Agent
        # agent = Agent(...)
        # result = await agent.run(prompt)
        
        # PLACEHOLDER - Replace with actual implementation:
        logger.info("Executing agent pipeline")
        if progress_callback:
            await progress_callback(0.3, "Agent processing...")
        
        # Simulated agent work - REPLACE THIS
        await asyncio.sleep(2)
        result_content = f"[PLACEHOLDER] Agent response to: {prompt}"
        
        if progress_callback:
            await progress_callback(0.9, "Finalizing results...")
        
        # -------------------------------------------------------------
        # END OF AGENT INVOCATION SECTION
        # -------------------------------------------------------------
        
        logger.info("Agent execution completed")
        
        # Collect any generated artifacts from workspace
        artifacts = collect_artifacts(task_workspace, logger)
        
        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info("Task completed", duration_seconds=duration)
        
        return AgentResult(
            success=True,
            content=str(result_content),
            artifacts=artifacts,
            execution_log=logger.generate_report(),
            duration_seconds=duration
        )
        
    except asyncio.TimeoutError:
        logger.error("Task timed out")
        return AgentResult(
            success=False,
            content="",
            error="Task timed out after maximum allowed duration",
            execution_log=logger.generate_report(),
            duration_seconds=(datetime.utcnow() - start_time).total_seconds()
        )
        
    except Exception as e:
        logger.error("Task failed", error=str(e), error_type=type(e).__name__)
        return AgentResult(
            success=False,
            content="",
            error=str(e),
            execution_log=logger.generate_report(),
            duration_seconds=(datetime.utcnow() - start_time).total_seconds()
        )


def collect_artifacts(workspace: Path, logger: ExecutionLogger) -> list[Artifact]:
    """
    Scan workspace directory for generated files to send back to user.
    
    Customize this based on what your agent system produces.
    """
    artifacts = []
    
    # Common output patterns - adjust based on your agent's behavior
    output_patterns = [
        ("*.md", "text/markdown", "Markdown document"),
        ("*.json", "application/json", "JSON data"),
        ("*.csv", "text/csv", "CSV data"),
        ("*.txt", "text/plain", "Text file"),
        ("*.pdf", "application/pdf", "PDF document"),
        ("*.html", "text/html", "HTML document"),
        ("report_*.md", "text/markdown", "Generated report"),
    ]
    
    for pattern, mime_type, description in output_patterns:
        for file_path in workspace.glob(pattern):
            if file_path.is_file() and file_path.stat().st_size < 50_000_000:  # 50MB limit
                try:
                    content = file_path.read_bytes()
                    artifacts.append(Artifact(
                        filename=file_path.name,
                        content=content,
                        description=description,
                        mime_type=mime_type
                    ))
                    logger.info("Artifact collected", filename=file_path.name)
                except Exception as e:
                    logger.error("Failed to collect artifact", filename=file_path.name, error=str(e))
    
    return artifacts


# Optional: Add specialized entry points for different agent modes
async def run_research_agent(prompt: str, **kwargs) -> AgentResult:
    """Specialized entry point for research tasks."""
    return await run_agent(f"[RESEARCH MODE] {prompt}", **kwargs)


async def run_code_agent(prompt: str, **kwargs) -> AgentResult:
    """Specialized entry point for coding tasks."""
    return await run_agent(f"[CODE MODE] {prompt}", **kwargs)
```

---

## Core Bot Implementation

### bot/config.py

```python
"""Configuration and secret loading."""

import os
from pathlib import Path
from typing import Set

def load_secret(name: str) -> str:
    """Load secret from Docker secrets or environment variable."""
    # Docker secrets location
    secret_file = Path(f"/run/secrets/{name}")
    if secret_file.exists():
        return secret_file.read_text().strip()
    
    # Fall back to environment variable
    env_name = name.upper()
    if env_name in os.environ:
        return os.environ[env_name]
    
    # Development: check local secrets directory
    local_secret = Path(f"./secrets/{name}.txt")
    if local_secret.exists():
        return local_secret.read_text().strip()
    
    raise ValueError(f"Secret '{name}' not found")


def load_allowed_users() -> Set[int]:
    """Load allowed Telegram user IDs from config."""
    try:
        users_str = os.getenv("ALLOWED_USER_IDS", "")
        if not users_str:
            return set()  # Empty = allow all (not recommended for production)
        return {int(uid.strip()) for uid in users_str.split(",") if uid.strip()}
    except ValueError:
        return set()


# Load configuration
ANTHROPIC_API_KEY = load_secret("anthropic_api_key")
TELEGRAM_BOT_TOKEN = load_secret("telegram_bot_token")
WEBHOOK_SECRET = load_secret("webhook_secret")

# Webhook URL - set via environment, updated by dev script for ngrok
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://localhost:8000")

# Access control
ALLOWED_USER_IDS: Set[int] = load_allowed_users()

# Task configuration
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT", "300"))  # 5 minutes default
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))

# Paths
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/app/workspace")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
```

### bot/execution_logger.py

```python
"""Execution logging for activity visibility."""

import io
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class LogEntry:
    timestamp: datetime
    level: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)


class ExecutionLogger:
    """Captures agent execution details for downloadable reports."""
    
    def __init__(self, task_id: str = None):
        self.task_id = task_id or "unknown"
        self.entries: List[LogEntry] = []
        self.start_time = datetime.utcnow()
    
    def _log(self, level: str, message: str, **context):
        self.entries.append(LogEntry(
            timestamp=datetime.utcnow(),
            level=level,
            message=message,
            context=context
        ))
        # Also print for container logs
        ctx_str = " ".join(f"{k}={v}" for k, v in context.items())
        print(f"[{self.task_id}] {level}: {message} {ctx_str}")
    
    def info(self, message: str, **context):
        self._log("INFO", message, **context)
    
    def warning(self, message: str, **context):
        self._log("WARN", message, **context)
    
    def error(self, message: str, **context):
        self._log("ERROR", message, **context)
    
    def debug(self, message: str, **context):
        self._log("DEBUG", message, **context)
    
    def generate_report(self) -> bytes:
        """Generate downloadable execution report as bytes."""
        end_time = datetime.utcnow()
        duration = (end_time - self.start_time).total_seconds()
        
        lines = [
            "=" * 70,
            f"EXECUTION REPORT - Task {self.task_id}",
            "=" * 70,
            f"Started:  {self.start_time.isoformat()}Z",
            f"Finished: {end_time.isoformat()}Z",
            f"Duration: {duration:.2f} seconds",
            f"Entries:  {len(self.entries)}",
            "",
            "-" * 70,
            "LOG ENTRIES",
            "-" * 70,
            ""
        ]
        
        for entry in self.entries:
            ts = entry.timestamp.strftime('%H:%M:%S.%f')[:-3]
            ctx = ""
            if entry.context:
                ctx = " | " + " ".join(f"{k}={v}" for k, v in entry.context.items())
            lines.append(f"[{ts}] {entry.level:5} {entry.message}{ctx}")
        
        lines.extend(["", "=" * 70, "END OF REPORT", "=" * 70])
        
        return "\n".join(lines).encode('utf-8')
```

### bot/task_manager.py

```python
"""Async task management for agent execution."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Callable, Any
from enum import Enum


class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class Task:
    task_id: str
    chat_id: int
    user_id: int
    prompt: str
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0
    progress_message: str = ""
    result: Any = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class TaskManager:
    """Manages async task execution with concurrency control."""
    
    def __init__(
        self,
        max_workers: int = 3,
        task_timeout: int = 300,
        agent_executor: Callable = None,
        result_callback: Callable = None
    ):
        self.queue: asyncio.Queue[Task] = asyncio.Queue(maxsize=50)
        self.tasks: Dict[str, Task] = {}
        self.workers: list[asyncio.Task] = []
        self.max_workers = max_workers
        self.task_timeout = task_timeout
        self.agent_executor = agent_executor
        self.result_callback = result_callback
        self._running = False
    
    async def start(self):
        """Start worker tasks."""
        self._running = True
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(f"worker-{i}"))
            self.workers.append(worker)
        print(f"TaskManager started with {self.max_workers} workers")
    
    async def stop(self):
        """Gracefully stop all workers."""
        self._running = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        print("TaskManager stopped")
    
    async def _worker(self, name: str):
        """Worker coroutine processing tasks from queue."""
        while self._running:
            try:
                task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                await self._process_task(task)
                self.queue.task_done()
            except asyncio.TimeoutError:
                continue  # Check _running flag
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Worker {name} error: {e}")
    
    async def _process_task(self, task: Task):
        """Process a single task."""
        task.status = TaskStatus.RUNNING
        
        async def progress_callback(percent: float, message: str):
            task.progress = percent
            task.progress_message = message
        
        try:
            result = await asyncio.wait_for(
                self.agent_executor(
                    prompt=task.prompt,
                    task_id=task.task_id,
                    progress_callback=progress_callback
                ),
                timeout=self.task_timeout
            )
            
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = datetime.utcnow()
            
        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMEOUT
            task.error = f"Task timed out after {self.task_timeout} seconds"
            task.completed_at = datetime.utcnow()
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.utcnow()
        
        # Notify completion
        if self.result_callback:
            try:
                await self.result_callback(task)
            except Exception as e:
                print(f"Result callback error for task {task.task_id}: {e}")
    
    async def submit(self, chat_id: int, user_id: int, prompt: str) -> str:
        """Submit a new task, returns task_id."""
        task_id = str(uuid.uuid4())[:8]
        task = Task(
            task_id=task_id,
            chat_id=chat_id,
            user_id=user_id,
            prompt=prompt
        )
        self.tasks[task_id] = task
        await self.queue.put(task)
        return task_id
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        return self.tasks.get(task_id)
    
    def get_user_tasks(self, user_id: int) -> list[Task]:
        """Get all tasks for a user."""
        return [t for t in self.tasks.values() if t.user_id == user_id]
```

### bot/telegram_handlers.py

```python
"""Telegram bot command handlers."""

from functools import wraps
from telegram import Update, InputFile
from telegram.ext import ContextTypes
from telegram.constants import ChatAction
import asyncio
import io

from config import ALLOWED_USER_IDS
from task_manager import TaskManager, TaskStatus


def restricted(func):
    """Decorator to restrict access to allowed users."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        
        # If ALLOWED_USER_IDS is empty, allow all (development mode)
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            await update.message.reply_text(
                f"🚫 Access denied.\n\n"
                f"Your User ID: `{user_id}`\n\n"
                f"Ask the administrator to add your ID to the allowed list.",
                parse_mode="Markdown"
            )
            return
        
        return await func(update, context, *args, **kwargs)
    return wrapper


class TelegramHandlers:
    """Handler methods for Telegram bot commands."""
    
    def __init__(self, task_manager: TaskManager):
        self.task_manager = task_manager
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        await update.message.reply_text(
            f"👋 Hello {user.first_name}!\n\n"
            f"I'm your Claude Agent assistant. I can run AI agent tasks for you.\n\n"
            f"**Commands:**\n"
            f"`/agent <prompt>` - Run an agent task\n"
            f"`/status` - Check your recent tasks\n"
            f"`/status <id>` - Check specific task\n"
            f"`/help` - Show detailed help\n"
            f"`/myid` - Show your Telegram user ID\n\n"
            f"**Example:**\n"
            f"`/agent Research the latest developments in quantum computing`",
            parse_mode="Markdown"
        )
    
    @restricted
    async def agent_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /agent command - triggers agent task."""
        # Extract prompt from command arguments
        prompt = " ".join(context.args) if context.args else ""
        
        if not prompt:
            await update.message.reply_text(
                "❓ Please provide a prompt.\n\n"
                "**Usage:** `/agent <your prompt here>`\n\n"
                "**Example:**\n"
                "`/agent Analyze the top 5 AI trends for 2025`",
                parse_mode="Markdown"
            )
            return
        
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Submit task
        task_id = await self.task_manager.submit(
            chat_id=chat_id,
            user_id=user_id,
            prompt=prompt
        )
        
        # Acknowledge
        await update.message.reply_text(
            f"✅ Task submitted!\n\n"
            f"**Task ID:** `{task_id}`\n"
            f"**Prompt:** _{prompt[:100]}{'...' if len(prompt) > 100 else ''}_\n\n"
            f"I'll notify you when it's complete. Use `/status {task_id}` to check progress.",
            parse_mode="Markdown"
        )
    
    @restricted
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        user_id = update.effective_user.id
        
        # Check for specific task ID
        if context.args:
            task_id = context.args[0]
            task = self.task_manager.get_task(task_id)
            
            if not task:
                await update.message.reply_text(f"❓ Task `{task_id}` not found.", parse_mode="Markdown")
                return
            
            status_emoji = {
                TaskStatus.QUEUED: "⏳",
                TaskStatus.RUNNING: "🔄",
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌",
                TaskStatus.TIMEOUT: "⏱️"
            }
            
            msg = (
                f"**Task {task.task_id}**\n\n"
                f"Status: {status_emoji.get(task.status, '❓')} {task.status.value}\n"
                f"Progress: {task.progress*100:.0f}%\n"
            )
            
            if task.progress_message:
                msg += f"Current: _{task.progress_message}_\n"
            
            if task.error:
                msg += f"\nError: `{task.error[:200]}`\n"
            
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            # Show recent tasks for user
            tasks = self.task_manager.get_user_tasks(user_id)
            recent = sorted(tasks, key=lambda t: t.created_at, reverse=True)[:5]
            
            if not recent:
                await update.message.reply_text("No recent tasks found.")
                return
            
            msg = "**Your Recent Tasks:**\n\n"
            for t in recent:
                status_emoji = {"queued": "⏳", "running": "🔄", "completed": "✅", "failed": "❌", "timeout": "⏱️"}
                emoji = status_emoji.get(t.status.value, "❓")
                msg += f"{emoji} `{t.task_id}` - {t.prompt[:30]}...\n"
            
            await update.message.reply_text(msg, parse_mode="Markdown")
    
    async def myid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /myid command - shows user's Telegram ID."""
        user = update.effective_user
        await update.message.reply_text(
            f"**Your Telegram Info:**\n\n"
            f"User ID: `{user.id}`\n"
            f"Username: @{user.username or 'not set'}\n"
            f"Name: {user.first_name} {user.last_name or ''}\n\n"
            f"_Share your User ID with the administrator to get access._",
            parse_mode="Markdown"
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        await update.message.reply_text(
            "**Claude Agent Bot Help**\n\n"
            "This bot runs AI agent tasks asynchronously. Submit a task and "
            "get notified when it completes.\n\n"
            "**Commands:**\n\n"
            "`/agent <prompt>`\n"
            "Submit a new agent task. The prompt can be any request.\n\n"
            "`/status`\n"
            "View your recent tasks.\n\n"
            "`/status <task_id>`\n"
            "Check the status of a specific task.\n\n"
            "`/myid`\n"
            "Display your Telegram user ID (for access setup).\n\n"
            "**Tips:**\n"
            "• Be specific in your prompts for better results\n"
            "• Long-running tasks will complete in background\n"
            "• You'll receive results as messages + file attachments\n",
            parse_mode="Markdown"
        )


def split_message(text: str, max_length: int = 3500) -> list[str]:
    """Split long text into chunks for Telegram's message limit."""
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        
        # Try to split at newline
        split_point = text.rfind('\n', 0, max_length)
        if split_point == -1 or split_point < max_length // 2:
            # Try space
            split_point = text.rfind(' ', 0, max_length)
        if split_point == -1:
            split_point = max_length
        
        chunks.append(text[:split_point])
        text = text[split_point:].lstrip()
    
    return chunks


async def send_task_result(bot, task):
    """Send task result back to user via Telegram."""
    chat_id = task.chat_id
    
    if task.status == TaskStatus.COMPLETED and task.result:
        result = task.result
        
        # Send main content
        if result.content:
            chunks = split_message(result.content, max_length=3500)
            for i, chunk in enumerate(chunks):
                prefix = f"**Results ({i+1}/{len(chunks)}):**\n\n" if len(chunks) > 1 else "**Results:**\n\n"
                await bot.send_message(
                    chat_id=chat_id,
                    text=prefix + chunk,
                    parse_mode="Markdown"
                )
        
        # Send artifacts as files
        if result.artifacts:
            for artifact in result.artifacts:
                await bot.send_document(
                    chat_id=chat_id,
                    document=InputFile(
                        io.BytesIO(artifact.content),
                        filename=artifact.filename
                    ),
                    caption=f"📄 {artifact.description}"
                )
        
        # Send execution log
        if result.execution_log:
            await bot.send_document(
                chat_id=chat_id,
                document=InputFile(
                    io.BytesIO(result.execution_log),
                    filename=f"execution_log_{task.task_id}.txt"
                ),
                caption=f"📋 Execution log for task {task.task_id}"
            )
        
        # Summary message
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ Task `{task.task_id}` completed in {result.duration_seconds:.1f}s",
            parse_mode="Markdown"
        )
    
    elif task.status == TaskStatus.FAILED:
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Task `{task.task_id}` failed:\n\n`{task.error[:500]}`",
            parse_mode="Markdown"
        )
    
    elif task.status == TaskStatus.TIMEOUT:
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏱️ Task `{task.task_id}` timed out.\n\nTry breaking it into smaller requests.",
            parse_mode="Markdown"
        )
```

### bot/main.py

```python
"""Main entry point - FastAPI app with Telegram webhook integration."""

import asyncio
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler

from config import (
    TELEGRAM_BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET,
    TASK_TIMEOUT_SECONDS, MAX_CONCURRENT_TASKS, WORKSPACE_DIR
)
from task_manager import TaskManager
from telegram_handlers import TelegramHandlers, send_task_result
from agent_adapter import run_agent


# Initialize Telegram application (webhook mode - no updater)
ptb_app = (
    Application.builder()
    .token(TELEGRAM_BOT_TOKEN)
    .updater(None)
    .build()
)

# Initialize task manager
task_manager = TaskManager(
    max_workers=MAX_CONCURRENT_TASKS,
    task_timeout=TASK_TIMEOUT_SECONDS,
    agent_executor=lambda prompt, task_id, progress_callback: run_agent(
        prompt=prompt,
        workspace_dir=WORKSPACE_DIR,
        progress_callback=progress_callback,
        task_id=task_id
    ),
    result_callback=lambda task: send_task_result(ptb_app.bot, task)
)

# Initialize handlers
handlers = TelegramHandlers(task_manager)

# Register command handlers
ptb_app.add_handler(CommandHandler("start", handlers.start_command))
ptb_app.add_handler(CommandHandler("agent", handlers.agent_command))
ptb_app.add_handler(CommandHandler("status", handlers.status_command))
ptb_app.add_handler(CommandHandler("myid", handlers.myid_command))
ptb_app.add_handler(CommandHandler("help", handlers.help_command))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler for startup/shutdown."""
    # Startup
    print(f"Setting webhook to {WEBHOOK_URL}/telegram")
    await ptb_app.bot.set_webhook(
        url=f"{WEBHOOK_URL}/telegram",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=["message", "callback_query"]
    )
    
    async with ptb_app:
        await ptb_app.start()
        await task_manager.start()
        
        print("Bot started successfully!")
        yield
        
        # Shutdown
        await task_manager.stop()
        await ptb_app.stop()
    
    await ptb_app.bot.delete_webhook()
    print("Bot shut down")


app = FastAPI(lifespan=lifespan, title="Claude Agent Telegram Bot")


@app.post("/telegram")
async def telegram_webhook(request: Request) -> Response:
    """Handle incoming Telegram webhook updates."""
    # Verify secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != WEBHOOK_SECRET:
        return Response(status_code=HTTPStatus.FORBIDDEN)
    
    # Process update
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    
    return Response(status_code=HTTPStatus.OK)


@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestration."""
    return {
        "status": "healthy",
        "queued_tasks": task_manager.queue.qsize(),
        "total_tasks": len(task_manager.tasks)
    }


@app.get("/")
async def root():
    """Root endpoint with basic info."""
    return {
        "service": "Claude Agent Telegram Bot",
        "status": "running",
        "endpoints": {
            "webhook": "/telegram",
            "health": "/health"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

## Docker Configuration

### docker/Dockerfile

```dockerfile
# syntax=docker/dockerfile:1.4

# ===== BUILD STAGE =====
FROM python:3.11-slim AS builder

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc curl && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ===== PRODUCTION STAGE =====
FROM python:3.11-slim AS production

WORKDIR /app

# Install runtime dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1001 agent && \
    useradd --uid 1001 --gid agent --shell /bin/false agent

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create application directories
RUN mkdir -p /app/workspace /app/logs && \
    chown -R agent:agent /app

# Copy application code
COPY --chown=agent:agent . .

USER agent

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "bot.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker/docker-compose.yml

```yaml
version: "3.8"

services:
  agent-bot:
    build:
      context: ..
      dockerfile: docker/Dockerfile
      target: production
    container_name: claude-agent-telegram-bot
    restart: unless-stopped
    
    environment:
      - PYTHONUNBUFFERED=1
      - LOG_LEVEL=DEBUG
      - WEBHOOK_URL=${WEBHOOK_URL:-http://localhost:8000}
      - ALLOWED_USER_IDS=${ALLOWED_USER_IDS:-}
      - TASK_TIMEOUT=300
      - MAX_CONCURRENT_TASKS=3
      - WORKSPACE_DIR=/app/workspace
    
    # For development: load secrets from environment
    # For production: use Docker secrets (see docker-compose.prod.yml)
    env_file:
      - ../.env
    
    volumes:
      # Mount source for live development (remove for production)
      - ../bot:/app/bot
      - ../agent_system:/app/agent_system
      # Persistent storage
      - agent_workspace:/app/workspace
      - agent_logs:/app/logs
    
    ports:
      - "8000:8000"
    
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      start_period: 60s
      retries: 3
    
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  agent_workspace:
  agent_logs:
```

### docker/docker-compose.prod.yml (Production with secrets)

```yaml
version: "3.8"

services:
  agent-bot:
    build:
      context: ..
      dockerfile: docker/Dockerfile
      target: production
    container_name: claude-agent-telegram-bot
    restart: always
    
    environment:
      - PYTHONUNBUFFERED=1
      - LOG_LEVEL=INFO
      - WEBHOOK_URL=${WEBHOOK_URL}
      - ALLOWED_USER_IDS=${ALLOWED_USER_IDS}
      - TASK_TIMEOUT=300
      - MAX_CONCURRENT_TASKS=3
      - WORKSPACE_DIR=/app/workspace
    
    secrets:
      - anthropic_api_key
      - telegram_bot_token
      - webhook_secret
    
    volumes:
      - agent_workspace:/app/workspace
      - agent_logs:/app/logs
    
    ports:
      - "8000:8000"
    
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 2G

volumes:
  agent_workspace:
  agent_logs:

secrets:
  anthropic_api_key:
    file: ../secrets/anthropic_api_key.txt
  telegram_bot_token:
    file: ../secrets/telegram_bot_token.txt
  webhook_secret:
    file: ../secrets/webhook_secret.txt
```

---

## Development Scripts

### scripts/dev.sh

```bash
#!/bin/bash
# Development launcher: starts ngrok, registers webhook, runs container

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Claude Agent Telegram Bot - Development Mode ===${NC}"

# Check for required tools
command -v docker >/dev/null 2>&1 || { echo -e "${RED}Docker required but not installed.${NC}" >&2; exit 1; }
command -v ngrok >/dev/null 2>&1 || { echo -e "${RED}ngrok required but not installed.${NC}" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo -e "${RED}curl required but not installed.${NC}" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo -e "${RED}jq required but not installed.${NC}" >&2; exit 1; }

# Load environment
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
else
    echo -e "${RED}.env file not found. Copy .env.example to .env and fill in values.${NC}"
    exit 1
fi

# Verify required variables
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo -e "${RED}TELEGRAM_BOT_TOKEN not set in .env${NC}"
    exit 1
fi

# Kill existing ngrok if running
pkill -f "ngrok http" 2>/dev/null || true
sleep 1

# Start ngrok in background
echo -e "${YELLOW}Starting ngrok...${NC}"
ngrok http 8000 --log=stdout > /tmp/ngrok.log 2>&1 &
NGROK_PID=$!
sleep 3

# Get ngrok URL
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | jq -r '.tunnels[] | select(.proto == "https") | .public_url')

if [ -z "$NGROK_URL" ]; then
    echo -e "${RED}Failed to get ngrok URL. Check if ngrok is running properly.${NC}"
    cat /tmp/ngrok.log
    exit 1
fi

echo -e "${GREEN}ngrok URL: ${NGROK_URL}${NC}"

# Export for docker-compose
export WEBHOOK_URL=$NGROK_URL

# Register webhook with Telegram
echo -e "${YELLOW}Registering webhook with Telegram...${NC}"
WEBHOOK_RESPONSE=$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook?url=${NGROK_URL}/telegram&secret_token=${WEBHOOK_SECRET}")

if echo "$WEBHOOK_RESPONSE" | jq -e '.ok == true' > /dev/null; then
    echo -e "${GREEN}Webhook registered successfully!${NC}"
else
    echo -e "${RED}Failed to register webhook:${NC}"
    echo "$WEBHOOK_RESPONSE" | jq .
    exit 1
fi

# Cleanup function
cleanup() {
    echo -e "\n${YELLOW}Shutting down...${NC}"
    docker-compose -f docker/docker-compose.yml down
    kill $NGROK_PID 2>/dev/null || true
    # Optionally delete webhook
    curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook" > /dev/null
    echo -e "${GREEN}Cleanup complete${NC}"
}

trap cleanup EXIT

# Start containers
echo -e "${YELLOW}Starting Docker containers...${NC}"
docker-compose -f docker/docker-compose.yml up --build

# This line is reached when docker-compose exits
```

### scripts/register_webhook.py

```python
#!/usr/bin/env python3
"""Manually register/check Telegram webhook."""

import os
import sys
import requests
from pathlib import Path

def load_env():
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())

def main():
    load_env()
    
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)
    
    base_url = f"https://api.telegram.org/bot{token}"
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python register_webhook.py info          - Get current webhook info")
        print("  python register_webhook.py set <url>     - Set webhook URL")
        print("  python register_webhook.py delete        - Delete webhook")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "info":
        response = requests.get(f"{base_url}/getWebhookInfo")
        print(response.json())
    
    elif command == "set":
        if len(sys.argv) < 3:
            print("Error: URL required")
            sys.exit(1)
        
        url = sys.argv[2]
        secret = os.getenv("WEBHOOK_SECRET", "")
        
        params = {"url": f"{url}/telegram"}
        if secret:
            params["secret_token"] = secret
        
        response = requests.get(f"{base_url}/setWebhook", params=params)
        print(response.json())
    
    elif command == "delete":
        response = requests.get(f"{base_url}/deleteWebhook")
        print(response.json())
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

### scripts/setup_secrets.sh

```bash
#!/bin/bash
# First-time secret setup helper

set -e

SECRETS_DIR="./secrets"
ENV_FILE=".env"
ENV_EXAMPLE=".env.example"

echo "=== Secret Setup Helper ==="

# Create secrets directory
mkdir -p "$SECRETS_DIR"
echo "Created $SECRETS_DIR directory"

# Create .env from example if it doesn't exist
if [ ! -f "$ENV_FILE" ] && [ -f "$ENV_EXAMPLE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "Created $ENV_FILE from $ENV_EXAMPLE"
fi

# Prompt for secrets
echo ""
echo "Enter your API keys (or press Enter to skip):"
echo ""

read -p "Anthropic API Key: " ANTHROPIC_KEY
if [ -n "$ANTHROPIC_KEY" ]; then
    echo "$ANTHROPIC_KEY" > "$SECRETS_DIR/anthropic_api_key.txt"
    echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY" >> "$ENV_FILE"
    echo "  ✓ Saved Anthropic API key"
fi

read -p "Telegram Bot Token (from @BotFather): " TELEGRAM_TOKEN
if [ -n "$TELEGRAM_TOKEN" ]; then
    echo "$TELEGRAM_TOKEN" > "$SECRETS_DIR/telegram_bot_token.txt"
    echo "TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN" >> "$ENV_FILE"
    echo "  ✓ Saved Telegram bot token"
fi

# Generate random webhook secret
WEBHOOK_SECRET=$(openssl rand -hex 32)
echo "$WEBHOOK_SECRET" > "$SECRETS_DIR/webhook_secret.txt"
echo "WEBHOOK_SECRET=$WEBHOOK_SECRET" >> "$ENV_FILE"
echo "  ✓ Generated webhook secret"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Secrets saved to: $SECRETS_DIR/"
echo "Environment file: $ENV_FILE"
echo ""
echo "Next steps:"
echo "1. Review and edit $ENV_FILE if needed"
echo "2. Add your Telegram user ID to ALLOWED_USER_IDS"
echo "3. Run: ./scripts/dev.sh"
```

---

## Configuration Files

### .env.example

```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
WEBHOOK_SECRET=randomly_generated_secret_string

# Anthropic API
ANTHROPIC_API_KEY=sk-ant-your-api-key

# Access Control (comma-separated Telegram user IDs)
# Leave empty to allow all users (not recommended)
ALLOWED_USER_IDS=123456789,987654321

# Task Configuration
TASK_TIMEOUT=300
MAX_CONCURRENT_TASKS=3

# Logging
LOG_LEVEL=DEBUG

# Webhook URL (set automatically by dev.sh, manually for production)
WEBHOOK_URL=https://your-app.fly.dev
```

### requirements.txt

```
# Add these to your existing requirements.txt

# Telegram Bot
python-telegram-bot[webhooks]>=20.0

# Web Framework
fastapi>=0.100.0
uvicorn[standard]>=0.23.0

# HTTP Client (for webhook registration)
httpx>=0.24.0

# Already likely present for Claude Agent SDK:
# anthropic
# asyncio (stdlib)
```

### .gitignore additions

```
# Secrets - NEVER commit these
secrets/
.env

# ngrok
ngrok.log
/tmp/ngrok.log

# Docker volumes (if local)
workspace/
logs/

# Python
__pycache__/
*.pyc
.venv/
venv/
```

---

## Implementation Checklist

For the AI Programmer to track progress:

### Phase 1: Project Setup
- [ ] Create directory structure as specified
- [ ] Add dependencies to requirements.txt
- [ ] Create .env.example and .gitignore
- [ ] Set up secrets directory

### Phase 2: Core Bot Implementation
- [ ] Implement config.py with secret loading
- [ ] Implement execution_logger.py
- [ ] Implement task_manager.py
- [ ] Implement telegram_handlers.py
- [ ] Implement main.py with FastAPI integration

### Phase 3: Agent Adapter
- [ ] Create agent_adapter.py skeleton
- [ ] Identify existing agent system entry point
- [ ] Implement run_agent() to call existing system
- [ ] Test adapter in isolation (without bot)

### Phase 4: Docker Setup
- [ ] Create Dockerfile with multi-stage build
- [ ] Create docker-compose.yml for development
- [ ] Create docker-compose.prod.yml for production
- [ ] Test container builds successfully

### Phase 5: Development Scripts
- [ ] Create dev.sh with ngrok integration
- [ ] Create register_webhook.py helper
- [ ] Create setup_secrets.sh for onboarding
- [ ] Test full development workflow

### Phase 6: End-to-End Testing
- [ ] Create Telegram bot via @BotFather
- [ ] Run dev.sh and verify ngrok tunnel
- [ ] Test /start command from phone
- [ ] Test /myid command
- [ ] Test /agent command with simple prompt
- [ ] Verify results return to Telegram
- [ ] Test /status command
- [ ] Test file attachments (execution log)

### Phase 7: Polish
- [ ] Add error handling for edge cases
- [ ] Improve message formatting
- [ ] Add progress updates during long tasks
- [ ] Test with real multi-agent workloads

---

## Troubleshooting Guide

### Common Issues

**"Webhook was not set"**
- Check that TELEGRAM_BOT_TOKEN is correct
- Verify ngrok is running: `curl http://localhost:4040/api/tunnels`
- Manually check webhook: `python scripts/register_webhook.py info`

**"Access denied" when messaging bot**
- Get your user ID with /myid (works without auth)
- Add ID to ALLOWED_USER_IDS in .env
- Restart container

**Tasks never complete**
- Check container logs: `docker-compose logs -f`
- Verify agent_adapter.py correctly calls your agent system
- Check for exceptions in the agent execution

**Results don't arrive in Telegram**
- Verify the result callback is firing (add logging)
- Check Telegram API rate limits (unlikely for personal use)
- Ensure bot has permission to send messages in the chat

**Container keeps restarting**
- Check logs for startup errors
- Verify all secrets are present
- Check health endpoint: `curl http://localhost:8000/health`

---

## Summary

This guide provides everything needed to integrate a Telegram bot with an existing Claude Agent SDK multi-agent system. The key points:

1. **No rewrite required** - The existing agent system is wrapped, not modified
2. **Single integration point** - Only agent_adapter.py touches your existing code
3. **Async execution** - Handles Telegram's timeout requirements gracefully
4. **Development-first** - Docker + ngrok provides fast iteration
5. **Production-ready** - Same container deploys to cloud platforms

The AI Programmer should start with the adapter (Phase 3) to ensure the existing agent system can be called, then build out the bot infrastructure around it.