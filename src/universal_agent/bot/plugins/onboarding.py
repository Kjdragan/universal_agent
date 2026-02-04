
from typing import Callable, Awaitable
from ..core.context import BotContext

async def onboarding_middleware(ctx: BotContext, next_fn: Callable[[], Awaitable[None]]):
    """
    Handles /start and /help commands.
    """
    msg = ctx.update.effective_message
    if not msg or not msg.text:
        await next_fn()
        return

    text = msg.text.strip()
    
    if text == "/start" or text == "/help":
        await msg.reply_text(
            "🤖 **Universal Agent Bot (Refactored)**\n\n"
            "**Commands:**\n"
            "`/agent <prompt>` - Run a task\n"
            "`/status` - Check status\n"
            "`/help` - Show this message\n\n"
            "This bot is running on the new modular architecture.",
            parse_mode="Markdown"
        )
        ctx.abort() # Command handled, stop processing
        return

    await next_fn()
