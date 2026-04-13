# cc_setup.py
import os
import asyncio
import discord
from universal_agent.infisical_loader import initialize_runtime_secrets

async def setup_channels():
    # Only loads valid Infisical secrets if script running standalone
    if not os.environ.get("DISCORD_BOT_TOKEN"):
        initialize_runtime_secrets(force_reload=False)

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("Required DISCORD_BOT_TOKEN not found.")
        return

    # Owner Server Name or ID
    # Currently setting via simple logic - will pick up the first server the bot is in
    
    intents = discord.Intents.default()
    intents.guilds = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user} for channel setup")
        for guild in client.guilds:
            print(f"Found guild: {guild.name}")
            
            async def create_category_and_channels(cat_name, channels):
                category = discord.utils.get(guild.categories, name=cat_name)
                if not category:
                    print(f"Creating category {cat_name}...")
                    category = await guild.create_category(cat_name)
                
                for ch in channels:
                    if not discord.utils.get(category.channels, name=ch):
                        print(f"Creating channel #{ch} under {cat_name}...")
                        await guild.create_text_channel(ch, category=category)
            
            await create_category_and_channels("📋 OPERATIONS", ["simone-chat", "mission-status", "alerts", "task-queue", "review-queue"])
            await create_category_and_channels("🔬 INTELLIGENCE", ["research-feed", "announcements-feed", "event-calendar", "signals-feed", "release-tracker", "knowledge-updates"])
            await create_category_and_channels("📦 ARTIFACTS", ["briefings", "reports", "code-artifacts"])
            await create_category_and_channels("⚙️ SYSTEM", ["bot-logs", "config"])
            
            print("Setup completed for this guild!")
            
        await client.close()

    await client.start(token)

if __name__ == "__main__":
    asyncio.run(setup_channels())
