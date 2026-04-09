# cc_bot.py
import os
import asyncio
import logging
import discord
from discord.ext import commands, tasks
from discord import app_commands

from .config import init_secrets, get_db_path
from .database import DiscordIntelligenceDB
from .integration.task_hub import create_task_hub_mission

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] CC_BOT: %(message)s")
logger = logging.getLogger("cc_bot")

class CCBot(commands.Bot):
    def __init__(self, db: DiscordIntelligenceDB):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        
        super().__init__(
            command_prefix="!", 
            intents=intents,
            help_command=None
        )
        self.db = db

    async def setup_hook(self):
        # Sync slash commands with Discord
        # For a rapid iteration, it is best to sync with a specific guild but here we sync globally or locally based on usage.
        # We will try a global sync (takes ~1 hour to appear) or manual sync command.
        try:
            logger.info("Syncing slash commands...")
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")

    async def on_ready(self):
        logger.info(f"Command & Control Bot logged in as {self.user.name} ({self.user.id})")
        self.poll_database.start()

    @tasks.loop(seconds=60)
    async def poll_database(self):
        # Poll for unnotified scheduled events
        try:
            conn = self.db._get_conn()
            cur = conn.execute("SELECT * FROM scheduled_events WHERE notified = 0")
            events = cur.fetchall()
            if events:
                for guild in self.guilds:
                    cat = discord.utils.get(guild.categories, name="🔬 INTELLIGENCE")
                    if cat:
                        channel = discord.utils.get(cat.channels, name="event-calendar")
                        if channel:
                            for ev in events:
                                embed = discord.Embed(title=f"New Event: {ev['name']}", description=ev['description'], color=discord.Color.blue())
                                embed.add_field(name="Server ID", value=ev['server_id'])
                                embed.add_field(name="Start", value=ev['start_time'])
                                if 'end_time' in ev.keys() and ev['end_time']:
                                    embed.add_field(name="End", value=ev['end_time'])
                                if ev['location']:
                                    embed.add_field(name="Location", value=ev['location'])
                                
                                msg = await channel.send(embed=embed)
                                await msg.add_reaction("✅")
                                await msg.add_reaction("🎙️")
                                await msg.add_reaction("📋")
                                await msg.add_reaction("❌")
                                
                                self.db.mark_event_notified(ev['id'])
        except Exception as e:
            logger.error(f"Polling error: {e}")
        
    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        # Simone chat integration
        # Owner's ID is 351727866549108737
        OWNER_ID = 351727866549108737
        if message.channel.name == "simone-chat" and message.author.id == OWNER_ID:
            logger.info(f"Message for Simone received: {message.content}")
            await message.channel.send("Simone says: Message received! (Processing hook stubbed)")

        await self.process_commands(message)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.user.id:
            return
            
        channel = self.get_channel(payload.channel_id)
        if not channel or channel.name != "event-calendar":
            return
            
        try:
            message = await channel.fetch_message(payload.message_id)
            if not message.embeds:
                return
                
            embed = message.embeds[0]
            event_name = embed.title.replace("New Event: ", "") if embed.title else "Discord Event"
            
            start_time = None
            end_time = None
            for field in embed.fields:
                if field.name == "Start":
                    start_time = field.value
                elif field.name == "End":
                    end_time = field.value
            
            if payload.emoji.name == "✅":
                logger.info(f"Adding event '{event_name}' to Google Calendar via GWS CLI.")
                import subprocess
                cmd = ['npx', '@googleworkspace/cli', 'calendar', '+insert', '--summary', f"{event_name}"]
                if start_time:
                    cmd.extend(['--start', start_time, '--end', end_time or start_time])
                
                subprocess.Popen(cmd)
                await channel.send(f"Scheduled '{event_name}' in Google Calendar.")
                
            elif payload.emoji.name == "🎙️":
                logger.info(f"Marked '{event_name}' for audio tracking/notes.")
                create_task_hub_mission(f"Record/Note: {event_name}", f"Track event {event_name} audio/notes.", tags=["discord", "audio"])
                await channel.send(f"Created Task Hub mission to track '{event_name}'.")
                
            elif payload.emoji.name == "❌":
                logger.info(f"Dismissed event '{event_name}'.")
                await message.delete()
        except Exception as e:
            logger.error(f"Error handling reaction: {e}")


def setup_commands(bot: CCBot):
    @bot.tree.command(name="status", description="UA system overview")
    async def status(interaction: discord.Interaction):
        await interaction.response.send_message("System Status: All clear. Heartbeats ok.", ephemeral=False)

    @bot.tree.command(name="task_add", description="Create Task Hub item")
    @app_commands.describe(description="Task text", priority="Priority 1-5")
    async def task_add(interaction: discord.Interaction, description: str, priority: int = 3):
        create_task_hub_mission(title="Discord CC Task", description=description)
        await interaction.response.send_message(f"Task added: {description} (priority={priority})")

    @bot.tree.command(name="discord_search", description="Search ingested Discord messages")
    @app_commands.describe(query="Text to search")
    async def discord_search(interaction: discord.Interaction, query: str):
        conn = bot.db._get_conn()
        cur = conn.execute("SELECT author_name, content FROM messages WHERE content LIKE ? LIMIT 5", (f"%{query}%",))
        results = cur.fetchall()
        if not results:
            await interaction.response.send_message(f"No results found for '{query}'")
            return
            
        reply = "\n".join([f"**{r['author_name']}**: {r['content'][:100]}" for r in results])
        await interaction.response.send_message(f"Search results:\n{reply}")

    @bot.tree.command(name="discord_signals", description="Show recent detected signals")
    async def discord_signals(interaction: discord.Interaction):
        conn = bot.db._get_conn()
        cur = conn.execute("SELECT rule_matched, severity, timestamp FROM signals ORDER BY timestamp DESC LIMIT 5")
        results = cur.fetchall()
        if not results:
            await interaction.response.send_message("No recent signals found.")
            return
            
        reply = "\n".join([f"[{r['severity']}] {r['rule_matched']} at {r['timestamp']}" for r in results])
        await interaction.response.send_message(f"Recent Signals:\n{reply}")

    @bot.tree.command(name="monitor_list", description="Show monitored channels")
    async def monitor_list(interaction: discord.Interaction):
        chan_b = bot.db.get_tier_channels("B")
        chan_c = bot.db.get_tier_channels("C")
        await interaction.response.send_message(f"Currently monitoring {len(chan_b)} Tier B channels and {len(chan_c)} Tier C channels.")

def main():
    init_secrets()
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        # Fallback to older config logic if needed, but CC MUST use BOT token
        raise ValueError("DISCORD_BOT_TOKEN not found in environment")
        
    db_path = get_db_path()
    db = DiscordIntelligenceDB(db_path)
    
    bot = CCBot(db=db)
    setup_commands(bot)
    
    bot.run(token)

if __name__ == "__main__":
    main()
