import os
import sys
import asyncio
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()

async def register():
    # Try args first, then env vars
    token = sys.argv[1] if len(sys.argv) > 1 else os.getenv("TELEGRAM_BOT_TOKEN")
    url = sys.argv[2] if len(sys.argv) > 2 else os.getenv("WEBHOOK_URL")
    secret = sys.argv[3] if len(sys.argv) > 3 else os.getenv("WEBHOOK_SECRET")

    if not token or not url:
        print("Usage: python register_webhook.py [BOT_TOKEN] [WEBHOOK_URL] [SECRET]")
        print("OR set TELEGRAM_BOT_TOKEN and WEBHOOK_URL in .env")
        return
    
    print(f"Connecting with token: {token[:5]}...")
    bot = Bot(token)
    print(f"Setting webhook to: {url}")
    
    try:
        success = await bot.set_webhook(url=url, secret_token=secret)
        if success:
            print("✅ Webhook set successfully!")
        else:
            print("❌ Failed to set webhook.")
        
        info = await bot.get_webhook_info()
        print(f"Current Webhook Info:\nURL: {info.url}\nPending Updates: {info.pending_update_count}\nLast Error: {info.last_error_message}")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(register())
