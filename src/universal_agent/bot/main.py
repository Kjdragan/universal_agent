
import asyncio
import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, TypeHandler
from universal_agent import process_heartbeat
from universal_agent.runtime_bootstrap import bootstrap_runtime_environment
from .config import get_telegram_bot_token
from .core.runner import UpdateRunner
from .core.context import BotContext
from .core.middleware import MiddlewareChain
from .core.session import FileSessionStore
from .core.middleware_impl import logging_middleware, auth_middleware, SessionMiddleware
from .plugins.onboarding import onboarding_middleware
from .plugins.commands import commands_middleware
from .task_manager import TaskManager
from .agent_adapter import AgentAdapter
from .normalization.formatting import format_telegram_response


# Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def _send_with_retry(bot, chat_id, text, retries: int = 3, base_delay_s: float = 0.5):
    """Best-effort Telegram send with bounded retry for transient failures.

    Uses the shared :func:`telegram_send_async` utility which provides
    unified retry policy, rate-limit awareness, and structured logging.
    The ``bot`` parameter is accepted for API compatibility but not used;
    the shared utility sends directly via the Telegram HTTP API.
    """
    from universal_agent.services.telegram_send import telegram_send_async

    ok, err = await telegram_send_async(
        chat_id=chat_id,
        text=text,
        retries=retries,
        base_delay=base_delay_s,
    )
    if not ok:
        raise RuntimeError(f"telegram_send_failed: {err}")

async def run_bot():
    bootstrap_state = bootstrap_runtime_environment(profile=os.getenv("UA_DEPLOYMENT_PROFILE"))
    if not bootstrap_state.policy.enable_telegram_poll:
        logger.info(
            "Telegram polling disabled by runtime policy role=%s",
            bootstrap_state.policy.role,
        )
        return

    telegram_bot_token = get_telegram_bot_token()
    if not telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return

    process_heartbeat.start(
        path_env_var="UA_TELEGRAM_PROCESS_HEARTBEAT_FILE",
        legacy_path_env_var=None,
        interval_env_var="UA_TELEGRAM_PROCESS_HEARTBEAT_INTERVAL_SECONDS",
        default_path="/var/lib/universal-agent/heartbeat/telegram.heartbeat",
        default_interval=10,
        thread_name="telegram-process-heartbeat",
        log_label="Telegram process heartbeat",
    )

    # 1. Initialize Core Components
    session_store = FileSessionStore()
    middleware_chain = MiddlewareChain()
    
    # 2. Setup Middleware Pipeline
    middleware_chain.use(logging_middleware)
    middleware_chain.use(auth_middleware)
    middleware_chain.use(SessionMiddleware(session_store))
    middleware_chain.use(onboarding_middleware)
    middleware_chain.use(commands_middleware)

    # 3. Setup Agent Components
    agent_adapter = AgentAdapter()
    
    # Status Callback for TaskManager
    # We need a bot instance to send messages. We can use the app.bot later, 
    # or create a temporary one if needed, but better to use the app's bot.
    # However, 'app' isn't built yet.
    # We can define the callback to use a captured 'app' reference that is set later.
    
    app_ref = {"bot": None}
    
    async def task_status_callback(task):
        bot = app_ref["bot"]
        if not bot:
            return
            
        try:
            # Determine text
            text = f"📝 Task Update: {task.status.upper()}\n"
            if task.status == "completed":
                # Use formatter
                result = task.execution_summary if task.execution_summary else task.result
                text = format_telegram_response(result)
            elif task.status == "error":
                text = f"❌ Task Failed:\n{task.result}"
            elif task.status == "running":
                text = f"🚀 Task Started: {task.id}"
            
            # Send (assuming user_id is chat_id for DM)
            # In future, we might need a mapping if they are in a group 
            # (but task.user_id comes from update.effective_user.id)
            # If we want to support group replies, Task object should store chat_id too.
            # For now, simplistic approach: send to user_id.
            
            await _send_with_retry(bot, task.user_id, text)
            
        except Exception as e:
            logger.error(f"Failed to send status update: {e}")

    task_manager = TaskManager(status_callback=task_status_callback)

    # 4. Define Process Callback for Runner
    async def process_update_callback(update: Update, ptb_context: ContextTypes.DEFAULT_TYPE):
        # Create Context
        ctx = BotContext(
            update=update, 
            ptb_context=ptb_context,
            task_manager=task_manager
        )
        # Run Middleware Chain
        await middleware_chain.run(ctx)

    # 4. Initialize Runner
    runner = UpdateRunner(process_callback=process_update_callback)

    # 6. Setup PTB Application
    app = ApplicationBuilder().token(telegram_bot_token).build()
    app_ref["bot"] = app.bot

    # 6. Global Helper to feed the runner
    async def feed_runner(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await runner.enqueue(update, context)

    # Register a catch-all handler that feeds everything to our runner
    app.add_handler(TypeHandler(Update, feed_runner))

    logger.info("Bot starting... (New Architecture WITH Heartbeat)")
    
    # [Heartbeat] Inject callback for proactive messages
    async def send_message_callback(user_id: str, text: str):
        if not app_ref["bot"]:
            logger.warning("Attempted to send proactive message before bot ready")
            return
        try:
            # Assuming user_id is chat_id for DM
            await _send_with_retry(app_ref["bot"], user_id, text)
        except Exception as e:
            logger.error(f"Failed to deliver proactive message to {user_id}: {e}")

    agent_adapter.send_message_callback = send_message_callback

    # Initialize Agent Adapter & Task Worker
    await agent_adapter.initialize()
    task_worker = asyncio.create_task(task_manager.worker(agent_adapter))

    # 8. Run
    try:
        async with app:
            await app.start()
            await app.updater.start_polling()

            # Keep running until cancelled
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            finally:
                await app.updater.stop()
                await app.stop()
                await runner.stop()
                task_worker.cancel()
                await agent_adapter.shutdown()
    finally:
        process_heartbeat.stop()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
