
import logging
from typing import List, Protocol, Callable, Awaitable
from .context import BotContext

logger = logging.getLogger(__name__)

class Middleware(Protocol):
    async def __call__(self, ctx: BotContext, next_middleware: Callable[[], Awaitable[None]]) -> None:
        ...

MiddlewareHandler = Callable[[BotContext, Callable[[], Awaitable[None]]], Awaitable[None]]

class MiddlewareChain:
    """
    Executes a chain of middlewares.
    Like Koa/Express/Grammy: await next() yields control to the next middleware.
    """
    def __init__(self):
        self.middlewares: List[MiddlewareHandler] = []

    def use(self, middleware: MiddlewareHandler):
        self.middlewares.append(middleware)

    async def run(self, ctx: BotContext):
        """Execute the chain."""
        await self._execute_index(0, ctx)

    async def _execute_index(self, index: int, ctx: BotContext):
        if index >= len(self.middlewares):
            return
        
        if ctx.aborted:
            return

        middleware = self.middlewares[index]
        
        async def next_fn():
            await self._execute_index(index + 1, ctx)
            
        try:
            await middleware(ctx, next_fn)
        except Exception as e:
            logger.error(f"Middleware error at index {index}: {e}", exc_info=True)
            raise e
