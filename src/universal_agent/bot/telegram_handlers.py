from telegram import Update
from telegram.ext import ContextTypes
from .config import ALLOWED_USER_IDS

async def check_auth(update: Update) -> bool:
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("⛔ Unauthorized access.")
        return False
    return True

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    await update.message.reply_text(
        "🤖 **Universal Agent Bot Online**\n\n"
        "Commands:\n"
        "/agent <prompt> - Run a task\n"
        "/status - Check running tasks\n"
        "/help - Show this message",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    await start_command(update, context)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    # context.bot_data should hold the TaskManager
    task_manager = context.bot_data.get("task_manager")
    if not task_manager:
        await update.message.reply_text("⚠️ Task Manager not ready.")
        return

    tasks = task_manager.get_user_tasks(update.effective_user.id)
    if not tasks:
        await update.message.reply_text("📭 No recent tasks found.")
        return

    msg = "📋 **Task Status** (Last 5)\n\n"
    # Show last 5 tasks
    for task in tasks[:5]:
        icon = "⏳" if task.status == "pending" else \
               "🔄" if task.status == "running" else \
               "✅" if task.status == "completed" else "❌"
        
        prompt_snippet = task.prompt[:40] + "..." if len(task.prompt) > 40 else task.prompt
        msg += f"{icon} `{task.id[:8]}`: {task.status.upper()}\n"
        msg += f"   _{prompt_snippet}_\n\n"
        
    await update.message.reply_text(msg, parse_mode="Markdown")

async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("⚠️ Please provide a prompt: `/agent Research AI trends`", parse_mode="Markdown")
        return

    task_manager = context.bot_data.get("task_manager")
    user_id = update.effective_user.id
    
    # Enqueue Task
    task_id = await task_manager.add_task(user_id, prompt)
    
    await update.message.reply_text(f"✅ Task Queued: `{task_id}`\n\nI will notify you when it starts and finishes.", parse_mode="Markdown")
