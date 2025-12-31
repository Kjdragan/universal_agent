import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler
from telegram.request import HTTPXRequest
import os

from .config import TELEGRAM_BOT_TOKEN, WEBHOOK_SECRET, WEBHOOK_URL, PORT
from .task_manager import TaskManager
from .agent_adapter import AgentAdapter
from .telegram_handlers import start_command, help_command, status_command, agent_command, continue_command, new_command
from .telegram_formatter import format_telegram_response

# nest_asyncio removed to avoid conflict with uvicorn loop_factory in Python 3.13


# Global Objects
ptb_app = None
agent_adapter = None
task_manager = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb_app, agent_adapter, task_manager
    
    # 1. Initialize Agent
    print("🚀 Starting Universal Agent Bot...")
    
    # DEBUG: Check Network/DNS
    import socket
    try:
        ip = socket.gethostbyname("api.telegram.org")
        print(f"📡 DNS RESOLVED: api.telegram.org -> {ip}")
    except Exception as e:
        print(f"❌ DNS FAILED: {e}")
    
    # DEBUG: Print critical environment variables
    print("=" * 60)
    print("🔍 STARTUP DEBUG INFO")
    print("=" * 60)
    print(f"   WEBHOOK_URL: {WEBHOOK_URL}")
    print(f"   WEBHOOK_SECRET: {'***SET***' if WEBHOOK_SECRET else 'NOT SET'}")
    print(f"   TELEGRAM_BOT_TOKEN: {'***SET***' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
    print(f"   PORT: {PORT}")
    print("=" * 60)

    agent_adapter = AgentAdapter()
    await agent_adapter.initialize()
    
    # 2. Initialize Telegram Bot with increased timeouts
    # Custom request with longer timeouts to avoid TimedOut errors
    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        http_version="1.1",  # Force HTTP/1.1 to avoid HTTP/2 hangs
    )
    # Note: HTTPXRequest in PTB doesn't expose trust_env directly in constructor in older versions, 
    # but let's check if we can pass it via connection_pool_kwargs or similar if needed.
    # Actually, PTB 20+ passes arbitrary kwargs to httpx.AsyncClient? No, it uses restricted args.
    # Let's double check PTB docs / code if needed. 
    # For now, we rely on the fact that if we don't set proxy_url, it defaults to None.
    # But trust_env=False is safer.
    
    # PTB's HTTPXRequest wrapper is specific.
    # Let's try to inject it if possible, otherwise we skip for now to avoid AttributeError.
    # Instead, let's verify if `connect_timeout` is actually being respected.
    
    ptb_app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .get_updates_request(request)  # Also for receiving updates
        .build()
    )
    
    # 3. Setup Task Manager with Notification Callback (with retry)
    async def notify_user(task):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if task.status == "completed":
                    # Use rich formatter for completed tasks
                    # Prefer execution_summary (ExecutionResult object) if available
                    result_to_format = task.execution_summary if task.execution_summary else task.result
                    formatted_msg = format_telegram_response(result_to_format)
                    # We might want to prepend "✅ Task Completed" or similar? 
                    # Actually formatted_msg includes stats, so maybe just send it.
                    # Use a short header
                    msg = f"✅ *Task Completed*\n\n{formatted_msg}"
                    
                elif task.status == "error":
                    msg = f"❌ *Task Failed*\n\nError: {task.result}"
                else:
                    # Updates for running/pending
                    msg = f"Task Update: `{task.id[:8]}`\nStatus: {task.status.upper()}"

                try:
                    await ptb_app.bot.send_message(chat_id=task.user_id, text=msg, parse_mode="MarkdownV2")
                except Exception as e_md:
                    print(f"⚠️ MarkdownV2 failed, trying plain text: {e_md}")
                    # Fallback to plain text if markdown fails
                    # We might want to strip markdown chars or just send raw
                    await ptb_app.bot.send_message(chat_id=task.user_id, text=msg, parse_mode=None)
                
                # Send Log File if completed or error
                if task.status in ["completed", "error"] and task.log_file and os.path.exists(task.log_file):
                    try:
                         await ptb_app.bot.send_document(
                            chat_id=task.user_id,
                            document=open(task.log_file, 'rb'),
                            filename=f"task_{task.id[:8]}.log",
                            caption=f"📝 Execution Log for {task.id[:8]}"
                        )
                    except Exception as e:
                        print(f"⚠️ Failed to send log: {e}")
                
                # Success - break retry loop
                break

            except Exception as e:
                print(f"⚠️ Notification attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)  # Wait before retry
                else:
                    print(f"❌ All notification attempts failed for task {task.id[:8]}")

    task_manager = TaskManager(status_callback=notify_user)
    
    # 4. Register Telegram Handlers
    ptb_app.add_handler(CommandHandler("start", start_command))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("status", status_command))
    ptb_app.add_handler(CommandHandler("agent", agent_command))
    ptb_app.add_handler(CommandHandler("continue", continue_command))
    ptb_app.add_handler(CommandHandler("new", new_command))
    
    # Store Task Manager in bot_data for handlers
    ptb_app.bot_data["task_manager"] = task_manager
    
    # Initialize Bot App - ALWAYS do this first (doesn't need network)
    print("🔄 Initializing PTB Application...")
    try:
        await ptb_app.initialize()
        print("✅ PTB Application initialized")
    except Exception as e:
        print(f"❌ CRITICAL: Failed to initialize PTB: {e}")
        # If we can't even initialize, something is very wrong
        raise e
    
    # Try to register webhook (this is what might timeout due to network)
    # We do this with retries, but if it fails, we continue anyway
    webhook_registered = False
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            print(f"🔄 Webhook registration attempt {attempt + 1}/{max_retries}...")
            if WEBHOOK_URL:
                print(f"🌍 Registering webhook: {WEBHOOK_URL}")
                await ptb_app.bot.set_webhook(url=WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
                webhook_registered = True
                print("✅ Webhook registered successfully!")
                break
            else:
                # Polling mode - requires delete_webhook which also needs network
                print("📡 Polling Mode - deleting any existing webhook...")
                await ptb_app.bot.delete_webhook()
                await ptb_app.updater.start_polling()
                webhook_registered = True
                break
        except Exception as e:
            print(f"⚠️ Webhook registration attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                print("⏳ Retrying in 5 seconds...")
                await asyncio.sleep(5)
    
    if not webhook_registered:
        print("=" * 60)
        print("⚠️ WEBHOOK REGISTRATION FAILED - RUNNING IN DEGRADED MODE")
        print("=" * 60)
        print("")
        print("📋 MANUAL FIX: Register webhook from your local machine:")
        print(f"   curl 'https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url={WEBHOOK_URL}&secret_token={WEBHOOK_SECRET}'")
        print("")
        print("ℹ️  Bot is still running and will accept webhooks if already registered")
        print("=" * 60)
    
    # Start the bot - this should always work since we initialized above
    try:
        await ptb_app.start()
        print("✅ Bot is running!" + (" (degraded - webhook not registered)" if not webhook_registered else ""))
    except Exception as e:
        print(f"❌ Failed to start bot: {e}")
        raise e

    # 5. Start Worker
    worker_task = asyncio.create_task(task_manager.worker(agent_adapter))
    
    yield
    
    # Shutdown
    if agent_adapter:
        print("🛑 Shutting down Agent Adapter...")
        await agent_adapter.shutdown()
        
    print("👋 Bot shutting down...")
    worker_task.cancel()
    await ptb_app.stop()
    await ptb_app.shutdown()


app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Handle incoming Telegram updates.
    """
    # Verify Secret Token (Recommended)
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_token != WEBHOOK_SECRET:
         return {"detail": "Unauthorized"}
    
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return {"status": "ok"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "tasks_active": task_manager.active_tasks if task_manager else 0}

@app.get("/")
async def root():
    """Root endpoint for basic health check"""
    return {"status": "Universal Agent Bot Running", "version": "1.0.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("universal_agent.bot.main:app", host="0.0.0.0", port=PORT, reload=True)
