
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
            "Hi, I'm Simone \u2014 your Universal Agent.\n\n"
            "Just type naturally and I'll get to work. "
            "No special commands needed.\n\n"
            "A few shortcuts if you want them:\n"
            "/status \u2014 check what's running\n"
            "/continue \u2014 resume previous session\n"
            "/new \u2014 start fresh session\n"
            "/cancel \u2014 cancel current task",
        )
        ctx.abort() # Command handled, stop processing
        return

    await next_fn()
