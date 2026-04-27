
import logging
from typing import Awaitable, Callable

from ..config import get_allowed_user_ids
from .context import BotContext
from .session import SessionStore

logger = logging.getLogger(__name__)

async def logging_middleware(ctx: BotContext, next_fn: Callable[[], Awaitable[None]]):
    user = ctx.update.effective_user
    chat = ctx.update.effective_chat
    logger.info(f"Update {ctx.update.update_id} | User: {user.id if user else 'N/A'} | Chat: {chat.id if chat else 'N/A'}")
    await next_fn()

async def auth_middleware(ctx: BotContext, next_fn: Callable[[], Awaitable[None]]):
    user_id = ctx.update.effective_user.id if ctx.update.effective_user else 0
    allowed_user_ids = set(get_allowed_user_ids())
    if allowed_user_ids and user_id not in allowed_user_ids:
        logger.warning(f"Unauthorized access attempt from {user_id}")
        if ctx.update.effective_message:
            await ctx.update.effective_message.reply_text("⛔ Unauthorized access.")
        ctx.abort()
        return
    await next_fn()

class SessionMiddleware:
    def __init__(self, store: SessionStore):
        self.store = store

    async def __call__(self, ctx: BotContext, next_fn: Callable[[], Awaitable[None]]):
        if not ctx.update.effective_chat:
            await next_fn()
            return

        chat_id = ctx.update.effective_chat.id
        session_id = await self.store.get_session(chat_id)
        ctx.session_id = session_id
        
        await next_fn()
