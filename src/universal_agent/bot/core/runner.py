
import asyncio
import logging
from typing import Dict, Any, Callable, Awaitable
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

class UpdateRunner:
    """
    Manages sequential processing of updates per chat_id.
    Each chat gets its own queue and worker task to ensure strict ordering 
    of message processing (preventing race conditions).
    """
    def __init__(self, process_callback: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]):
        self.queues: Dict[int, asyncio.Queue] = {}
        self.workers: Dict[int, asyncio.Task] = {}
        self.process_callback = process_callback
        self._running = True

    async def enqueue(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add an update to the appropriate chat queue."""
        if not self._running:
            return

        chat_id = update.effective_chat.id if update.effective_chat else 0
        
        if chat_id not in self.queues:
            self.queues[chat_id] = asyncio.Queue()
            self.workers[chat_id] = asyncio.create_task(self._worker(chat_id))
            logger.debug(f"Started worker for chat {chat_id}")

        await self.queues[chat_id].put((update, context))

    async def _worker(self, chat_id: int):
        """Process updates for a specific chat sequentially."""
        queue = self.queues[chat_id]
        while self._running:
            try:
                # Wait for next update
                update, context = await queue.get()
                
                try:
                    await self.process_callback(update, context)
                except Exception as e:
                    logger.error(f"Error processing update for chat {chat_id}: {e}", exc_info=True)
                finally:
                    queue.task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker loop error for chat {chat_id}: {e}")

    async def stop(self):
        """Stop all workers."""
        self._running = False
        for task in self.workers.values():
            task.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
        self.workers.clear()
