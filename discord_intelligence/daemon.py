import asyncio
import logging
from datetime import datetime
import discord
from discord.ext import tasks

from .config import init_secrets, get_discord_token, get_db_path, CONFIG
from .database import DiscordIntelligenceDB
from .signals import detect_signals
from .triage import run_triage_batch
from .integration.simone_alerts import send_simone_alert
from .integration.task_hub import create_task_hub_mission

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("discord_daemon")

class DiscordIntelligenceClient(discord.Client):
    def __init__(self, db: DiscordIntelligenceDB, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = db
        
    async def setup_hook(self) -> None:
        self.run_triage_jobs.start()
        logger.info("Started built-in triage loop task.")

    @tasks.loop(minutes=CONFIG.get("scheduling", {}).get("triage_interval_minutes", 60))
    async def run_triage_jobs(self):
        logger.info("Running periodic LLM triage batches...")
        # Get all channels we monitor
        channels = self.db.get_tier_channels("C") + self.db.get_tier_channels("B")
        for ch in channels:
            try:
                await run_triage_batch(self.db, ch["id"])
            except Exception as e:
                logger.error(f"Failed triage for {ch['name']}: {e}")
                
    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        # Sync visible servers and channels
        for guild in self.guilds:
            self.db.upsert_server(str(guild.id), guild.name)
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel):
                    self.db.upsert_channel(str(channel.id), str(guild.id), channel.name, channel.category.name if channel.category else None)
        logger.info("Synced servers and channels.")

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return
            
        # Basic check if it's in a guild
        if not message.guild:
            return

        channel_id_str = str(message.channel.id)
        server_id_str = str(message.guild.id)
        
        # Determine channel tier
        # In reality, you'd fetch from DB. For now, assume a fast in-memory map or we do a quick DB hit.
        with self.db._get_conn() as conn:
            cur = conn.execute("SELECT tier FROM channels WHERE id = ?", (channel_id_str,))
            row = cur.fetchone()
        tier = row['tier'] if row else 'C'

        self.db.store_message(
            msg_id=str(message.id),
            channel_id=channel_id_str,
            server_id=server_id_str,
            author_id=str(message.author.id),
            author_name=message.author.name,
            content=message.content,
            timestamp=message.created_at,
            is_bot=message.author.bot,
            reply_to_id=str(message.reference.message_id) if message.reference else None,
            has_attachments=bool(message.attachments)
        )

        signals = detect_signals(message.content, tier)
        for sig in signals:
            self.db.store_signal(str(message.id), sig["layer"], sig["rule_matched"], sig["severity"])
            
            # Use specific identifiers for text events to avoid collision with discord scheduled event IDs.
            if sig["rule_matched"] == "text_event_detected":
                self._upsert_event_from_text(message)

            # Immediately trigger UA workflow if high severity
            if sig["severity"] == "high":
                logger.info(f"HIGH SEVERITY SIGNAL: {sig['rule_matched']} on MSG {message.id}")
                if "release" in sig["rule_matched"]:
                    # Create task for CODIE
                    create_task_hub_mission(
                        title=f"New Release Detected: {message.guild.name}",
                        description=f"Message: {message.content}\n\nLink: {message.jump_url}",
                        tags=["release", "discord"]
                    )
                else:
                    # Alert Simone
                    # Dispatched softly via asyncio.create_task to not block discord event loop
                    asyncio.create_task(send_simone_alert(
                        subject=f"Discord Alert - {message.guild.name}",
                        message=f"Signal: {sig['rule_matched']}\n\n{message.content}\n\n{message.jump_url}",
                        is_urgent=True
                    ))

    def _upsert_event_from_text(self, message: discord.Message):
        self.db.upsert_scheduled_event(
            event_id=f"text_evt_{message.id}",
            server_id=str(message.guild.id),
            name="Textual Mention Event",
            description=message.content + "\n\n" + message.jump_url,
            start_time=message.created_at,
            end_time=None,
            location=message.channel.name,
            status="TEXT_DETECTED"
        )

    async def on_scheduled_event_create(self, event: discord.ScheduledEvent):
        self._upsert_event(event)

    async def on_scheduled_event_update(self, before: discord.ScheduledEvent, after: discord.ScheduledEvent):
        self._upsert_event(after)

    def _upsert_event(self, event: discord.ScheduledEvent):
        self.db.upsert_scheduled_event(
            event_id=str(event.id),
            server_id=str(event.guild.id),
            name=event.name,
            description=event.description,
            start_time=event.start_time,
            end_time=event.end_time,
            location=event.location,
            status=event.status.name
        )

def main():
    init_secrets()
    token = get_discord_token()
    db_path = get_db_path()
    
    db = DiscordIntelligenceDB(db_path)
    
    client = DiscordIntelligenceClient(db=db)
    client.run(token)

if __name__ == "__main__":
    main()
