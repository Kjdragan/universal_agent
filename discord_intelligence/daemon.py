import asyncio
import os
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
from .audio_recorder import AudioRecorder
from .transcriber import Transcriber
from .audio_cleanup import AudioCleanup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("discord_daemon")
AUTO_CREATE_RELEASE_TASKS = str(os.getenv("UA_DISCORD_AUTO_CREATE_RELEASE_TASKS", "0")).strip().lower() in {
    "1", "true", "yes", "on",
}
TEXT_EVENT_FALLBACK_ENABLED = str(os.getenv("UA_DISCORD_TEXT_EVENT_FALLBACK_ENABLED", "0")).strip().lower() in {
    "1", "true", "yes", "on",
}
TRIAGE_TIERS = {
    tier.strip().upper()
    for tier in os.getenv("UA_DISCORD_TRIAGE_TIERS", "A").split(",")
    if tier.strip()
}
TRIAGE_BATCH_LIMIT = max(1, int(os.getenv("UA_DISCORD_TRIAGE_BATCH_LIMIT", "50") or 50))
SEND_SIMONE_ALERTS = str(os.getenv("UA_DISCORD_SEND_SIMONE_ALERTS", "0")).strip().lower() in {
    "1", "true", "yes", "on",
}

# Base recordings directory (relative to discord_intelligence package)
import pathlib
RECORDINGS_DIR = str(pathlib.Path(__file__).resolve().parent / "recordings")


class DiscordIntelligenceClient(discord.Client):
    def __init__(self, db: DiscordIntelligenceDB, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = db
        
        # Audio subsystem
        self.audio_recorder = AudioRecorder(db, RECORDINGS_DIR)
        self.transcriber = Transcriber(db)
        self.audio_cleanup = AudioCleanup(db, RECORDINGS_DIR)
        
    async def setup_hook(self) -> None:
        self.run_triage_jobs.start()
        self.run_audio_maintenance.start()
        self.poll_guild_events.start()
        logger.info("Started built-in triage, audio maintenance, and event polling loop tasks.")

    @tasks.loop(minutes=CONFIG.get("scheduling", {}).get("triage_interval_minutes", 60))
    async def run_triage_jobs(self):
        logger.info("Running periodic LLM triage batches...")
        channels = []
        for tier in sorted(TRIAGE_TIERS):
            channels.extend(self.db.get_tier_channels(tier))
        for ch in channels:
            try:
                await run_triage_batch(self.db, ch["id"], limit=TRIAGE_BATCH_LIMIT)
            except Exception as e:
                logger.error(f"Failed triage for {ch['name']}: {e}")

    @tasks.loop(hours=6)
    async def run_audio_maintenance(self):
        """Periodic audio maintenance: transcribe pending events, run cleanup."""
        logger.info("Running audio maintenance cycle...")
        
        # 1. Transcribe any events with audio but no transcript
        try:
            count = await self.transcriber.transcribe_pending_events()
            if count:
                logger.info(f"Transcribed {count} event(s) in maintenance cycle.")
        except Exception as e:
            logger.error(f"Transcription maintenance failed: {e}")
        
        # 2. Run retention cleanup (30-day auto-delete)
        try:
            summary = await self.audio_cleanup.run_cleanup()
            if summary["deleted"]:
                logger.info(f"Audio cleanup: deleted {summary['deleted']} recording(s), freed {summary['bytes_freed'] / 1024 / 1024:.1f} MB")
        except Exception as e:
            logger.error(f"Audio cleanup failed: {e}")

    @run_audio_maintenance.before_loop
    async def before_audio_maintenance(self):
        await self.wait_until_ready()

    # ═══════════════════════════════════════════════════════════════════
    #  STRUCTURED EVENT DISCOVERY — replaces regex-based event parsing
    # ═══════════════════════════════════════════════════════════════════

    @tasks.loop(minutes=CONFIG.get("scheduling", {}).get("event_poll_interval_minutes", 30))
    async def poll_guild_events(self):
        """
        Periodically poll all guilds for scheduled events using the structured API.
        
        This replaces the fragile regex-based event detection from text messages.
        The Discord API returns typed JSON objects with entity_type, status,
        channel_id, start_time, etc. — zero false positives or negatives.
        
        Catches events that were created while we were offline or missed
        the gateway event for.
        """
        logger.info("Polling guilds for scheduled events...")
        total_events = 0
        stage_events = 0
        active_events = 0

        for guild in self.guilds:
            try:
                events = await guild.fetch_scheduled_events()
                for event in events:
                    total_events += 1
                    self._upsert_event(event)
                    
                    # Track stage events specifically
                    entity_type = self._get_entity_type(event)
                    if entity_type == "stage_instance":
                        stage_events += 1
                    
                    status = self._get_status_name(event)
                    
                    # If we find an ACTIVE event we're not recording, start recording
                    if status == "active":
                        active_events += 1
                        event_id = str(event.id)
                        if not self.audio_recorder.is_recording(event_id):
                            if entity_type in ("stage_instance", "voice"):
                                logger.info(
                                    f"Poll discovered active {entity_type} event: "
                                    f"'{event.name}' in {guild.name} — triggering recording"
                                )
                                await self._handle_event_started(event)
                
            except discord.HTTPException as e:
                logger.warning(f"Failed to fetch events for {guild.name}: {e}")
            except Exception as e:
                logger.error(f"Error polling events for {guild.name}: {e}")
            
            # Rate-limit courtesy: don't hammer the API across many guilds
            await asyncio.sleep(1)

        logger.info(
            f"Event poll complete: {total_events} total, "
            f"{stage_events} stage, {active_events} active across {len(self.guilds)} guilds"
        )

    @poll_guild_events.before_loop
    async def before_poll_guild_events(self):
        await self.wait_until_ready()

    # ═══════════════════════════════════════════════════════════════════
    #  GATEWAY EVENT HANDLERS — real-time push notifications
    # ═══════════════════════════════════════════════════════════════════

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
            
            # Text-event detection is now a SUPPLEMENTARY signal.
            # Primary event discovery comes from the structured API (poll + gateway).
            # We still store text-derived events as a fallback for announcements that
            # reference events without creating a Discord Scheduled Event.
            if sig["rule_matched"] == "text_event_detected" and TEXT_EVENT_FALLBACK_ENABLED:
                self._upsert_event_from_text(message)

            # Immediately trigger UA workflow if high severity
            if sig["severity"] == "high":
                logger.info(f"HIGH SEVERITY SIGNAL: {sig['rule_matched']} on MSG {message.id}")
                if "release" in sig["rule_matched"]:
                    if AUTO_CREATE_RELEASE_TASKS:
                        create_task_hub_mission(
                            title=f"New Release Detected: {message.guild.name}",
                            description=f"Message: {message.content}\n\nLink: {message.jump_url}",
                            tags=["release", "discord"],
                            source_kind="discord_intelligence",
                            metadata={
                                "server_name": message.guild.name,
                                "channel_id": str(message.channel.id),
                                "message_id": str(message.id),
                                "jump_url": message.jump_url,
                                "rule_matched": sig["rule_matched"],
                                "auto_created_from_passive_signal": True,
                            },
                        )
                    else:
                        logger.info(
                            "Passive Discord release signal stored without Task Hub mission "
                            "(set UA_DISCORD_AUTO_CREATE_RELEASE_TASKS=1 to enable): guild=%s message=%s",
                            message.guild.name,
                            message.id,
                        )
                else:
                    if SEND_SIMONE_ALERTS:
                        asyncio.create_task(send_simone_alert(
                            subject=f"Discord Alert - {message.guild.name}",
                            message=f"Signal: {sig['rule_matched']}\n\n{message.content}\n\n{message.jump_url}",
                            is_urgent=True
                        ))
                    else:
                        logger.info(
                            "Passive Discord high-severity signal stored without Simone alert "
                            "(set UA_DISCORD_SEND_SIMONE_ALERTS=1 to enable): rule=%s guild=%s message=%s",
                            sig["rule_matched"],
                            message.guild.name,
                            message.id,
                        )

    def _upsert_event_from_text(self, message: discord.Message):
        """
        Fallback: create an event record from a text message that mentions an event.
        
        This is the SUPPLEMENTARY path — kept for messages that reference events
        without a corresponding Discord Scheduled Event (e.g., "join our AMA at 3pm").
        The primary path is the structured API via poll_guild_events and gateway events.
        """
        self.db.upsert_scheduled_event(
            event_id=f"text_evt_{message.id}",
            server_id=str(message.guild.id),
            name="Textual Mention Event",
            description=message.content + "\n\n" + message.jump_url,
            start_time=message.created_at,
            end_time=None,
            location=message.channel.name,
            status="TEXT_DETECTED",
            entity_type="text_mention",  # Not a real Discord entity type — marks text source
        )

    # ── Scheduled Event Gateway Handlers (real-time, structured) ─────

    async def on_scheduled_event_create(self, event: discord.ScheduledEvent):
        """Fires when a new scheduled event is created in any guild we're in."""
        self._upsert_event(event)
        entity_type = self._get_entity_type(event)
        logger.info(
            f"📅 New scheduled event: '{event.name}' ({entity_type}) "
            f"in {event.guild.name}, starts {event.start_time}"
        )

    async def on_scheduled_event_update(self, before: discord.ScheduledEvent, after: discord.ScheduledEvent):
        """Fires on any scheduled event change — most importantly, status transitions."""
        self._upsert_event(after)
        
        # ── Detect status transitions using structured enum comparison ───
        before_status = self._get_status_name(before)
        after_status = self._get_status_name(after)
        
        if before_status != after_status:
            entity_type = self._get_entity_type(after)
            logger.info(
                f"Event '{after.name}' ({entity_type}) status: {before_status} → {after_status}"
            )
            
            # SCHEDULED → ACTIVE: event just went live
            if after_status == "active":
                if entity_type in ("stage_instance", "voice"):
                    await self._handle_event_started(after)
                else:
                    logger.info(f"External event went active (no audio to capture): '{after.name}'")
            
            # ACTIVE → COMPLETED/CANCELLED: event ended
            elif after_status in ("completed", "canceled", "cancelled"):
                await self._handle_event_ended(after)

    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent):
        """Fires when a scheduled event is deleted/canceled."""
        entity_type = self._get_entity_type(event)
        logger.info(f"📅 Event deleted: '{event.name}' ({entity_type}) in {event.guild.name}")
        # If we were recording this event, stop
        await self._handle_event_ended(event)

    # ── Stage Instance Gateway Handlers (for impromptu stages) ───────

    async def on_stage_instance_create(self, stage_instance: discord.StageInstance):
        """
        Fires when ANY stage goes live — even without a scheduled event.
        
        This catches impromptu stages that someone starts spontaneously
        without creating a Discord Scheduled Event first. Between this
        handler and on_scheduled_event_update, we have 100% coverage of
        both planned and spontaneous stages.
        """
        guild = stage_instance.guild
        channel = stage_instance.channel
        topic = stage_instance.topic or "Untitled Stage"
        
        logger.info(
            f"🎤 Impromptu stage detected: '{topic}' "
            f"in {guild.name} / #{channel.name}"
        )
        
        # Check if this stage has an associated scheduled event
        # If it does, the on_scheduled_event_update handler will handle recording
        if stage_instance.scheduled_event_id:
            logger.info(
                f"  Stage has scheduled event ID {stage_instance.scheduled_event_id} — "
                f"deferring to scheduled event handler"
            )
            return
        
        # No scheduled event → this is a spontaneous stage
        # Create a synthetic event record and start recording
        event_id = f"stage_{stage_instance.id}"
        
        self.db.upsert_scheduled_event(
            event_id=event_id,
            server_id=str(guild.id),
            name=topic,
            description=f"Impromptu stage in #{channel.name}",
            start_time=datetime.utcnow(),
            end_time=None,
            location=channel.name,
            status="active",
            entity_type="stage_instance",
            channel_id=str(channel.id),
        )
        
        # Start recording
        logger.info(f"Starting recording for impromptu stage '{topic}'")
        success = await self.audio_recorder.start_recording(
            client=self,
            channel=channel,
            event_id=event_id,
            event_name=topic,
        )
        
        if success:
            logger.info(f"🎙️ Recording started for impromptu stage '{topic}'")
        else:
            logger.warning(f"Failed to start recording for impromptu stage '{topic}'")

    async def on_stage_instance_delete(self, stage_instance: discord.StageInstance):
        """Stage ended — if we have a recording for it, stop."""
        event_id = f"stage_{stage_instance.id}"
        topic = stage_instance.topic or "Untitled Stage"
        
        if self.audio_recorder.is_recording(event_id):
            logger.info(f"🎤 Impromptu stage ended: '{topic}' — stopping recording")
            audio_path = await self.audio_recorder.stop_recording(event_id)
            
            if audio_path:
                logger.info(f"🎙️ Recording complete: {audio_path}")
                asyncio.create_task(
                    self._transcribe_and_notify(event_id, topic, audio_path)
                )

    # ═══════════════════════════════════════════════════════════════════
    #  AUDIO RECORDING LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    async def _handle_event_started(self, event: discord.ScheduledEvent):
        """When a scheduled event goes ACTIVE, join its voice/stage channel and record."""
        event_id = str(event.id)
        
        if self.audio_recorder.is_recording(event_id):
            return
        
        # Check entity_type — only record stage/voice events, not external
        entity_type = self._get_entity_type(event)
        if entity_type not in ("stage_instance", "voice"):
            logger.info(f"Skipping non-audio event '{event.name}' (type: {entity_type})")
            return
        
        # Resolve the voice/stage channel using the structured channel_id
        channel = None
        
        # Primary: use the event's channel reference (typed, reliable)
        if hasattr(event, 'channel') and event.channel:
            channel = event.channel
        elif hasattr(event, 'channel_id') and event.channel_id:
            channel = self.get_channel(event.channel_id)
        
        # Fallback: search by location name (for events with metadata instead of channel)
        if channel is None:
            location = getattr(event, 'location', None)
            entity_meta = getattr(event, 'entity_metadata', None)
            search_name = str(location or entity_meta or "")
            if search_name:
                for guild in self.guilds:
                    if str(guild.id) == str(event.guild.id):
                        for ch in guild.voice_channels + guild.stage_channels:
                            if ch.name.lower() == search_name.lower():
                                channel = ch
                                break
                        break
        
        if channel is None:
            logger.warning(
                f"Cannot find voice/stage channel for event '{event.name}' "
                f"(ID: {event_id}, type: {entity_type}). Audio recording skipped."
            )
            return
        
        logger.info(
            f"Event '{event.name}' ({entity_type}) started → "
            f"joining {channel.name} to record audio"
        )
        
        success = await self.audio_recorder.start_recording(
            client=self,
            channel=channel,
            event_id=event_id,
            event_name=event.name,
        )
        
        if success:
            logger.info(f"🎙️ Recording started for event '{event.name}'")
        else:
            logger.error(f"Failed to start recording for event '{event.name}'")

    async def _handle_event_ended(self, event: discord.ScheduledEvent):
        """When a scheduled event ends, stop recording and trigger transcription."""
        event_id = str(event.id)
        
        if not self.audio_recorder.is_recording(event_id):
            return
        
        logger.info(f"Event '{event.name}' ended → stopping recording")
        
        audio_path = await self.audio_recorder.stop_recording(event_id)
        
        if audio_path:
            logger.info(f"🎙️ Recording complete: {audio_path}")
            
            # Trigger transcription immediately (non-blocking)
            asyncio.create_task(
                self._transcribe_and_notify(event_id, event.name, audio_path)
            )
        else:
            logger.warning(f"No audio captured for event '{event.name}'")

    async def _transcribe_and_notify(self, event_id: str, event_name: str, audio_path: str):
        """Background task: transcribe audio and optionally notify."""
        try:
            transcript_path = await self.transcriber.transcribe_event(
                event_id=event_id,
                event_name=event_name,
                audio_path=audio_path,
            )
            
            if transcript_path:
                logger.info(f"📝 Transcript ready for '{event_name}': {transcript_path}")
                
                # Optionally trigger digest generation from transcript
                # This feeds into the existing event_digest pipeline
        except Exception as e:
            logger.error(f"Background transcription failed for event {event_id}: {e}")

    # ═══════════════════════════════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def _upsert_event(self, event: discord.ScheduledEvent):
        """Store/update a scheduled event with full structured data from the API."""
        entity_type = self._get_entity_type(event)
        creator_name = None
        if hasattr(event, 'creator') and event.creator:
            creator_name = getattr(event.creator, 'name', str(event.creator))
        
        channel_id = str(event.channel_id) if hasattr(event, 'channel_id') and event.channel_id else None
        user_count = getattr(event, 'user_count', 0) or 0
        
        self.db.upsert_scheduled_event(
            event_id=str(event.id),
            server_id=str(event.guild.id),
            name=event.name,
            description=event.description,
            start_time=event.start_time,
            end_time=event.end_time,
            location=event.location,
            status=self._get_status_name(event),
            entity_type=entity_type,
            channel_id=channel_id,
            creator_name=creator_name,
            user_count=user_count,
        )

    @staticmethod
    def _get_entity_type(event: discord.ScheduledEvent) -> str:
        """Extract entity type as a clean string from a ScheduledEvent."""
        if hasattr(event, 'entity_type') and event.entity_type:
            et = event.entity_type
            if hasattr(et, 'name'):
                return et.name  # 'stage_instance', 'voice', or 'external'
            return str(et)
        return "unknown"

    @staticmethod
    def _get_status_name(event: discord.ScheduledEvent) -> str:
        """Extract status as a clean lowercase string from a ScheduledEvent."""
        if hasattr(event, 'status') and event.status:
            s = event.status
            if hasattr(s, 'name'):
                return s.name.lower()  # 'scheduled', 'active', 'completed', 'canceled'
            return str(s).lower()
        return "unknown"


def main():
    init_secrets()
    token = get_discord_token()
    db_path = get_db_path()
    
    db = DiscordIntelligenceDB(db_path)
    
    client = DiscordIntelligenceClient(db=db)
    client.run(token)

if __name__ == "__main__":
    main()
