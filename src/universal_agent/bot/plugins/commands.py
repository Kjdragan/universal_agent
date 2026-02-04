
from typing import Callable, Awaitable
from ..core.context import BotContext

async def commands_middleware(ctx: BotContext, next_fn: Callable[[], Awaitable[None]]):
    """
    Handles /agent, /status, /continue, /new and generic text messages.
    """
    msg = ctx.update.effective_message
    if not msg or not msg.text:
        await next_fn()
        return

    text = msg.text.strip()
    user_id = ctx.update.effective_user.id
    task_manager = ctx.task_manager
    
    if not task_manager:
        await msg.reply_text("⚠️ Task Manager not available.")
        return

    # /status
    if text.lower().startswith("/status"):
        tasks = task_manager.get_user_tasks(user_id)
        mode = "🔗 CONTINUE" if task_manager.is_continuation_enabled(user_id) else "🆕 FRESH"
        
        if not tasks:
            await msg.reply_text(f"📭 No recent tasks found.\n\nSession Mode: {mode}")
            ctx.abort()
            return
            
        status_msg = f"📋 **Task Status** (Last 5)\nSession Mode: {mode}\n\n"
        for task in tasks[:5]:
            icon = "⏳" if task.status == "pending" else \
                   "🔄" if task.status == "running" else \
                   "✅" if task.status == "completed" else "❌"
            
            prompt_snippet = task.prompt[:40] + "..." if len(task.prompt) > 40 else task.prompt
            status_msg += f"{icon} `{task.id[:8]}`: {task.status.upper()}\n"
            status_msg += f"   _{prompt_snippet}_\n\n"
            
        await msg.reply_text(status_msg, parse_mode="Markdown")
        ctx.abort()
        return

    # /continue
    if text.lower().startswith("/continue"):
        task_manager.enable_continuation(user_id)
        await msg.reply_text(
            "🔗 **Continuation Mode Enabled**\n"
            "Next `/agent` commands will reuse session.",
            parse_mode="Markdown"
        )
        ctx.abort()
        return

    # /new
    if text.lower().startswith("/new"):
        task_manager.disable_continuation(user_id)
        await msg.reply_text(
            "🆕 **Fresh Session Mode**\n"
            "Next `/agent` will start a new session.",
            parse_mode="Markdown"
        )
        ctx.abort()
        return

    # /agent or implicit
    prompt = None
    if text.lower().startswith("/agent"):
        prompt = text[6:].strip()
        if not prompt:
            await msg.reply_text("⚠️ Use: `/agent <prompt>`")
            ctx.abort()
            return
    elif not text.startswith("/"):
        # Implicit agent command for non-command text
        prompt = text
    
    if prompt:
        is_continue = task_manager.is_continuation_enabled(user_id)
        mode_text = "🔗 Continuing session" if is_continue else "🆕 Fresh session"
        
        task_id = await task_manager.add_task(user_id, prompt)
        
        await msg.reply_text(
            f"✅ Task Queued: `{task_id}`\n{mode_text}",
            parse_mode="Markdown"
        )
        ctx.abort()
        return

    await next_fn()
