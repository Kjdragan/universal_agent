import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler
import nest_asyncio
import os

from .config import TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, WEBHOOK_URL, PORT
from .task_manager import TaskManager
from .agent_adapter import AgentAdapter
from .telegram_handlers import start_command, help_command, status_command, agent_command

# Apply nest_asyncio to allow nested event loops (useful for some envs)
nest_asyncio.apply()

# Global Objects
ptb_app = None
agent_adapter = None
task_manager = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb_app, agent_adapter, task_manager
    
    # 1. Initialize Agent
    print("🚀 Starting Universal Agent Bot...")
    agent_adapter = AgentAdapter()
    await agent_adapter.initialize()
    
    # 2. Initialize Telegram Bot
    ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # 3. Setup Task Manager with Notification Callback
    async def notify_user(task):
        try:
            msg = f"Task Update: `{task.id[:8]}`\nStatus: {task.status.upper()}"
            if task.status == "completed":
                 res_preview = str(task.result)[:500] + ("..." if len(str(task.result)) > 500 else "")
                 msg += f"\n\n**Result Preview:**\n{res_preview}"
            elif task.status == "error":
                msg += f"\n\nError: {task.result}"

            await ptb_app.bot.send_message(chat_id=task.user_id, text=msg, parse_mode="Markdown")
            
            # Send Log File if completed or error
            if task.status in ["completed", "error"] and task.log_file and os.path.exists(task.log_file):
                try:
                     await ptb_app.bot.send_document(
                        chat_id=task.user_id,
                        document=open(task.log_file, 'rb'),
                        filename=f"task_{task.id[:8]}.log",
                        caption=f"📝 Execution Log for {task.id[:8]}"
                    )
                except Exception as e:
                    print(f"⚠️ Failed to send log: {e}")

        except Exception as e:
            print(f"⚠️ Notification failed: {e}")

    task_manager = TaskManager(status_callback=notify_user)
    
    # 4. Register Telegram Handlers
    ptb_app.add_handler(CommandHandler("start", start_command))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("status", status_command))
    ptb_app.add_handler(CommandHandler("agent", agent_command))
    
    # Store Task Manager in bot_data for handlers
    ptb_app.bot_data["task_manager"] = task_manager
    
    # Initialize Bot App
    await ptb_app.initialize()
    await ptb_app.start()
    
    # 5. Start Worker
    worker_task = asyncio.create_task(task_manager.worker(agent_adapter))
    
    print("✅ Bot is fully running!")
    yield
    
    # Shutdown
    print("🛑 Shutting down...")
    worker_task.cancel()
    await ptb_app.stop()
    await ptb_app.shutdown()


app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Handle incoming Telegram updates.
    """
    # Verify Secret Token (Recommended)
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_token != WEBHOOK_SECRET:
         return {"detail": "Unauthorized"}
    
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return {"status": "ok"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "tasks_active": task_manager.active_tasks if task_manager else 0}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("universal_agent.bot.main:app", host="0.0.0.0", port=PORT, reload=True)
