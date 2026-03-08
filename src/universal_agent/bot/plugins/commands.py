
from typing import Callable, Awaitable
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from ..core.context import BotContext

import os
from pathlib import Path
from datetime import datetime, timezone

async def commands_middleware(ctx: BotContext, next_fn: Callable[[], Awaitable[None]]):
    """
    Handles /agent, /status, /continue, /new, /menu, /briefing, /delegate, callback queries, and generic text messages.
    """
    update = ctx.update
    
    # Handle Callback Queries (Inline button clicks)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = query.from_user.id
        task_manager = ctx.task_manager
        
        if data == "menu_status":
            if not task_manager:
                await query.edit_message_text("⚠️ Task Manager not available.")
                ctx.abort()
                return
            tasks = task_manager.get_user_tasks(user_id)
            mode = "🔗 CONTINUE" if task_manager.is_continuation_enabled(user_id) else "🆕 FRESH"
            if not tasks:
                await query.edit_message_text(f"📭 No recent tasks found.\n\nSession Mode: {mode}")
            else:
                status_msg = f"📋 **Task Status** (Last 5)\nSession Mode: {mode}\n\n"
                for task in tasks[:5]:
                    icon = "⏳" if task.status == "pending" else \
                           "🔄" if task.status == "running" else \
                           "✅" if task.status == "completed" else "❌"
                    prompt_snippet = task.prompt[:40] + "..." if len(task.prompt) > 40 else task.prompt
                    status_msg += f"{icon} `{task.id[:8]}`: {task.status.upper()}\n"
                    status_msg += f"   _{prompt_snippet}_\n\n"
                await query.edit_message_text(status_msg, parse_mode="Markdown")
            ctx.abort()
            return
            
        elif data == "menu_cancel":
            await query.edit_message_text("Usage: Send `/cancel <task_id>` to cancel a pending task.", parse_mode="Markdown")
            ctx.abort()
            return

        elif data == "menu_briefing":
            artifacts_dir = os.getenv("UA_ARTIFACTS_DIR", "").strip() or "/home/kjdragan/lrepos/universal_agent/artifacts"
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            briefing_path = Path(artifacts_dir) / "autonomous-briefings" / today / "DAILY_BRIEFING.md"
            
            if briefing_path.exists():
                with open(briefing_path, "r") as f:
                    content = f.read()
                if len(content) > 4000:
                    await query.message.reply_document(document=open(str(briefing_path), 'rb'), filename="DAILY_BRIEFING.md")
                    await query.edit_message_text("📄 Briefing sent as file (too long for message).")
                else:
                    await query.edit_message_text(f"📄 **Daily Briefing**\n\n{content}", parse_mode="Markdown")
            else:
                await query.edit_message_text("📭 No briefing found for today yet.")
            ctx.abort()
            return

        elif data == "menu_delegate":
            await query.edit_message_text("⚠️ To delegate a task, send: `/delegate <your objective>`", parse_mode="Markdown")
            ctx.abort()
            return

        elif data.startswith("vp_accept_") or data.startswith("vp_reject_"):
            # Placeholder for mission accept/reject flows
            action = "accepted" if data.startswith("vp_accept_") else "rejected"
            mission_id = data.split("_", 2)[2]
            # TODO: Integrate with VP Orchestration approval API
            await query.edit_message_text(f"Mission `{mission_id}` has been **{action}**.", parse_mode="Markdown")
            ctx.abort()
            return
            
        return await next_fn()

    msg = update.effective_message
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

    # /menu
    if text.lower().startswith("/menu"):
        keyboard = [
            [InlineKeyboardButton("📋 Status", callback_data="menu_status"),
             InlineKeyboardButton("🛑 Cancel Task", callback_data="menu_cancel")],
            [InlineKeyboardButton("📄 Briefing", callback_data="menu_briefing"),
             InlineKeyboardButton("🚀 Delegate", callback_data="menu_delegate")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.reply_text("Here are your quick actions:", reply_markup=reply_markup)
        ctx.abort()
        return

    # /briefing
    if text.lower().startswith("/briefing") or getattr(ctx.update.callback_query, "data", "") == "menu_briefing":
        artifacts_dir = os.getenv("UA_ARTIFACTS_DIR", "").strip() or "/home/kjdragan/lrepos/universal_agent/artifacts"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        briefing_path = Path(artifacts_dir) / "autonomous-briefings" / today / "DAILY_BRIEFING.md"
        
        reply_to = msg
        if getattr(ctx.update, "callback_query", None):
            await ctx.update.callback_query.answer()
            reply_to = ctx.update.callback_query.message
            
        if briefing_path.exists():
            with open(briefing_path, "r") as f:
                content = f.read()
            # Send Document if too long, or as text
            if len(content) > 4000:
                await reply_to.reply_document(document=open(briefing_path, 'rb'), filename="DAILY_BRIEFING.md")
            else:
                await reply_to.reply_text(f"📄 **Daily Briefing**\n\n{content}", parse_mode="Markdown")
        else:
            await reply_to.reply_text("📭 No briefing found for today yet.")
        ctx.abort()
        return

    # /delegate
    if text.lower().startswith("/delegate") or getattr(ctx.update.callback_query, "data", "") == "menu_delegate":
        prompt = ""
        if text.lower().startswith("/delegate"):
            prompt = text[9:].strip()
            
        if not prompt:
            reply_to = msg if msg else ctx.update.callback_query.message
            await reply_to.reply_text("⚠️ Use: `/delegate <objective>` to assign a VP mission manually.", parse_mode="Markdown")
            ctx.abort()
            return
            
        # Treat delegate exactly like an agent command but with specific intent prefix
        prompt_full = f"[DELEGATION REQUIRED]: {prompt}"
        try:
            task_id = await task_manager.add_task(user_id, prompt_full)
            reply_to = msg if msg else ctx.update.callback_query.message
            await reply_to.reply_text(f"🚀 Delegation task queued: `{task_id}`", parse_mode="Markdown")
        except Exception as e:
            pass # fallback to implicit handling
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

        try:
            task_id = await task_manager.add_task(user_id, prompt)
            await msg.reply_text("On it.")
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
