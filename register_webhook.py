import sys
import asyncio
from telegram import Bot

async def register():
    if len(sys.argv) < 3:
        print("Usage: python register_webhook.py <BOT_TOKEN> <WEBHOOK_URL> [SECRET]")
        return

    token = sys.argv[1]
    url = sys.argv[2]
    secret = sys.argv[3] if len(sys.argv) > 3 else None
    
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
