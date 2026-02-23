
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
    user = ctx.update.effective_user
    # Channel posts/system updates may not have an effective user; skip command handling.
    if not user:
        await next_fn()
        return
    user_id = user.id
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

    # /cancel [task_id]
    if text.lower().startswith("/cancel"):
        parts = text.split(maxsplit=1)
        target_task_id = parts[1].strip() if len(parts) > 1 else None
        ok, detail = task_manager.cancel_task(user_id, task_id=target_task_id)
        if ok:
            await msg.reply_text(
                f"🛑 Canceled task: `{detail}`",
                parse_mode="Markdown",
            )
        else:
            await msg.reply_text(f"⚠️ Cancel failed: {detail}")
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

        try:
            task_id = await task_manager.add_task(user_id, prompt)
            await msg.reply_text(
                f"✅ Task Queued: `{task_id}`\n{mode_text}",
                parse_mode="Markdown"
            )
        except ValueError as e:
            text_error = str(e)
            if text_error.startswith("active_task:"):
                active_task_id = text_error.split(":", 1)[1]
                await msg.reply_text(
                    "⏳ You already have an active task.\n"
                    f"Active Task: `{active_task_id}`\n"
                    "Use `/status` to monitor completion before queuing another request.",
                    parse_mode="Markdown",
                )
            else:
                await msg.reply_text("⚠️ Could not queue task. Please try again.")
        ctx.abort()
        return

    await next_fn()
