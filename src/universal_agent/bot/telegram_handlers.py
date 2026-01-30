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
        "**Commands:**\n"
        "`/agent <prompt>` - Run a task (fresh session)\n"
        "`/continue` - Enable multi-turn mode\n"
        "`/new` - Start fresh session\n"
        "`/status` - Check running tasks\n"
        "`/help` - Show this message\n\n"
        "**Session Modes:**\n"
        "• Default: Each `/agent` = fresh start\n"
        "• After `/continue`: Tasks share context",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    await start_command(update, context)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    task_manager = context.bot_data.get("task_manager")
    if not task_manager:
        await update.message.reply_text("⚠️ Task Manager not ready.")
        return

    user_id = update.effective_user.id
    tasks = task_manager.get_user_tasks(user_id)
    
    # Show continuation mode status
    mode = "🔗 CONTINUE" if task_manager.is_continuation_enabled(user_id) else "🆕 FRESH"
    
    if not tasks:
        await update.message.reply_text(f"📭 No recent tasks found.\n\nSession Mode: {mode}")
        return

    msg = f"📋 **Task Status** (Last 5)\nSession Mode: {mode}\n\n"
    for task in tasks[:5]:
        icon = "⏳" if task.status == "pending" else \
               "🔄" if task.status == "running" else \
               "✅" if task.status == "completed" else "❌"
        
        prompt_snippet = task.prompt[:40] + "..." if len(task.prompt) > 40 else task.prompt
        msg += f"{icon} `{task.id[:8]}`: {task.status.upper()}\n"
        msg += f"   _{prompt_snippet}_\n\n"
        
    await update.message.reply_text(msg, parse_mode="Markdown")

async def continue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable continuation mode - next /agent commands will reuse the session."""
    if not await check_auth(update): return
    
    task_manager = context.bot_data.get("task_manager")
    user_id = update.effective_user.id
    
    task_manager.enable_continuation(user_id)
    
    await update.message.reply_text(
        "🔗 **Continuation Mode Enabled**\n\n"
        "Your next `/agent` commands will continue in the same session.\n"
        "Use `/new` to start fresh.",
        parse_mode="Markdown"
    )

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable continuation mode and start fresh on next /agent."""
    if not await check_auth(update): return
    
    task_manager = context.bot_data.get("task_manager")
    user_id = update.effective_user.id
    
    task_manager.disable_continuation(user_id)
    
    await update.message.reply_text(
        "🆕 **Fresh Session Mode**\n\n"
        "Your next `/agent` will start a new session.\n"
        "Use `/continue` to enable multi-turn mode.",
        parse_mode="Markdown"
    )

async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("⚠️ Please provide a prompt: `/agent Research AI trends`", parse_mode="Markdown")
        return

async def _process_agent_request(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    """Helper to process an agent request from command or text."""
    task_manager = context.bot_data.get("task_manager")
    user_id = update.effective_user.id
    
    # Check if continuation mode and show in response
    is_continue = task_manager.is_continuation_enabled(user_id)
    mode_text = "🔗 Continuing session" if is_continue else "🆕 Fresh session"
    
    # Enqueue Task
    task_id = await task_manager.add_task(user_id, prompt)
    
    await update.message.reply_text(
        f"✅ Task Queued: `{task_id}`\n"
        f"{mode_text}\n\n"
        f"I will notify you when it starts and finishes.",
        parse_mode="Markdown"
    )

async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("⚠️ Please provide a prompt: `/agent Research AI trends`", parse_mode="Markdown")
        return
        
    await _process_agent_request(update, context, prompt)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages as agent commands."""
    if not await check_auth(update): return
    
    prompt = update.message.text
    if not prompt: return
    
    await _process_agent_request(update, context, prompt)
