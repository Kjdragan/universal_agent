# cc_bot.py
from datetime import datetime
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .calendar_sync import sync_event_to_calendar
from .config import get_db_path, init_secrets
from .database import DiscordIntelligenceDB
from .integration import gateway_client
from .integration.task_hub import (
    create_task_hub_mission,
    get_mission_status,
    get_task_hub_items,
)
from .views.review_queue import ReviewActionView, build_review_embed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] CC_BOT: %(message)s")
logger = logging.getLogger("cc_bot")
BASE_DIR = Path(__file__).resolve().parent
APP_ROOT = BASE_DIR.parent

AUTO_SYNC_CALENDAR_EVENTS = str(os.getenv("UA_DISCORD_AUTO_SYNC_CALENDAR_EVENTS", "1")).strip().lower() in {
    "1", "true", "yes", "on",
}
CALENDAR_SYNC_DAILY_LIMIT = max(1, int(os.getenv("UA_DISCORD_CALENDAR_SYNC_DAILY_LIMIT", "10") or 10))
CALENDAR_SYNC_RETRY_FAILED_AFTER_HOURS = max(
    1,
    int(os.getenv("UA_DISCORD_CALENDAR_SYNC_RETRY_FAILED_AFTER_HOURS", "6") or 6),
)

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
        # Register persistent views so buttons survive bot restarts.
        # An empty ReviewActionView() matches any custom_id starting with "review:".
        self.add_view(ReviewActionView())

        # Track which task_ids have been posted to #review-queue to avoid duplicates.
        self._posted_review_task_ids: set[str] = set()

        # Sync slash commands with Discord
        try:
            logger.info("Syncing slash commands...")
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")

    async def on_ready(self):
        logger.info(f"Command & Control Bot logged in as {self.user.name} ({self.user.id})")
        if not self.poll_database.is_running():
            self.poll_database.start()
        if not self.poll_signals_feed.is_running():
            self.poll_signals_feed.start()
            logger.info("Started poll_signals_feed loop")
        if not self.poll_insights_feed.is_running():
            self.poll_insights_feed.start()
            logger.info("Started poll_insights_feed loop")
        if not self.poll_briefings.is_running():
            self.poll_briefings.start()
            logger.info("Started poll_briefings loop")
        if not self.poll_knowledge_updates.is_running():
            self.poll_knowledge_updates.start()
            logger.info("Started poll_knowledge_updates loop")
        if not self.poll_event_digest.is_running():
            self.poll_event_digest.start()
            logger.info("Started poll_event_digest loop")
        if AUTO_SYNC_CALENDAR_EVENTS and not self.auto_sync_calendar_events.is_running():
            self.auto_sync_calendar_events.start()
            logger.info("Started auto_sync_calendar_events loop")
        if not self.poll_review_queue.is_running():
            self.poll_review_queue.start()
            logger.info("Started poll_review_queue loop")

    def _get_intel_channel(self, guild, channel_name: str):
        """Find a channel by name under the 🔬 INTELLIGENCE category."""
        cat = discord.utils.get(guild.categories, name="🔬 INTELLIGENCE")
        if cat:
            return discord.utils.get(cat.channels, name=channel_name)
        return None

    def _get_ops_channel(self, guild, channel_name: str):
        """Find a channel by name under the 📋 OPERATIONS category."""
        cat = discord.utils.get(guild.categories, name="📋 OPERATIONS")
        if cat:
            return discord.utils.get(cat.channels, name=channel_name)
        # Fallback: search all channels by name
        return discord.utils.get(guild.channels, name=channel_name)

    @tasks.loop(minutes=15)
    async def poll_event_digest(self):
        try:
            from discord_intelligence.event_digest import run_pipeline
            logger.info("Triggering background event digest pipeline...")
            await run_pipeline()
        except Exception as e:
            logger.error(f"Event digest loop error: {e}")

    @tasks.loop(seconds=90)
    async def poll_review_queue(self):
        """Poll Task Hub for tasks needing human review and post digest cards to #review-queue."""
        try:
            review_tasks = await gateway_client.get_review_tasks()
            if not review_tasks:
                return

            new_tasks = [
                t for t in review_tasks
                if str(t.get("task_id") or "") not in self._posted_review_task_ids
            ]
            if not new_tasks:
                return

            logger.info("Posting %d new review task(s) to #review-queue", len(new_tasks))
            for guild in self.guilds:
                channel = self._get_ops_channel(guild, "review-queue")
                if not channel:
                    logger.debug("No #review-queue channel found in guild %s", guild.name)
                    continue

                for task in new_tasks:
                    task_id = str(task.get("task_id") or "")
                    if not task_id:
                        continue
                    embed = build_review_embed(task)
                    view = ReviewActionView(task_id=task_id, task_data=task)
                    await channel.send(embed=embed, view=view)
                    self._posted_review_task_ids.add(task_id)

        except Exception as e:
            logger.error(f"Review queue polling error: {e}")

    @tasks.loop(minutes=15)
    async def auto_sync_calendar_events(self):
        try:
            remaining = CALENDAR_SYNC_DAILY_LIMIT - self.db.count_calendar_synced_today()
            if remaining <= 0:
                return
            candidates = self.db.get_calendar_sync_candidates(
                limit=remaining,
                retry_failed_after_hours=CALENDAR_SYNC_RETRY_FAILED_AFTER_HOURS,
            )
            if not candidates:
                return
            logger.info("Auto-syncing %d Discord structured event(s) to Google Calendar", len(candidates))
            for event in candidates:
                ok, detail = await sync_event_to_calendar(self.db, event)
                if ok:
                    logger.info("Synced Discord event to calendar: %s", event.get("name"))
                else:
                    logger.error("Failed syncing Discord event to calendar: %s detail=%s", event.get("name"), detail)

        except Exception as e:
            logger.error(f"Calendar auto-sync loop error: {e}")

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.user.id:
            return
            
        channel = self.get_channel(payload.channel_id)
        if not channel or channel.name != "event-calendar":
            return
            
        emoji = str(payload.emoji.name if payload.emoji.is_custom_emoji() else payload.emoji.name)
        if emoji not in ["✅", "🎙️", "📋", "❌"]:
            return
            
        try:
            msg = await channel.fetch_message(payload.message_id)
        except Exception as e:
            logger.error(f"Failed to fetch message for reaction: {e}")
            return
            
        if not msg.embeds:
            return
            
        embed = msg.embeds[0]
        title = embed.title
        if not title or not title.startswith("New Event: "):
            return
            
        event_name = title.replace("New Event: ", "")
        
        with self.db._get_conn() as conn:
            cur = conn.execute("SELECT * FROM scheduled_events WHERE name = ?", (event_name,))
            event = cur.fetchone()
            
        if not event:
            logger.error(f"Event '{event_name}' not found in database.")
            return

        if emoji == "❌":
            with self.db._get_conn() as conn:
                conn.execute("UPDATE scheduled_events SET status = 'declined' WHERE id = ?", (event["id"],))
                conn.commit()
            await channel.send(f"❌ Declined event: `{event_name}`")
            try:
                await msg.delete()
            except:
                pass
            return
            
        elif emoji == "🎙️":
            with self.db._get_conn() as conn:
                conn.execute("UPDATE scheduled_events SET persist_audio = 1 WHERE id = ?", (event["id"],))
                conn.commit()
            logger.info(f"Marked '{event_name}' for audio tracking/notes.")
            from discord_intelligence.integration.task_hub import (
                create_task_hub_mission,
            )
            create_task_hub_mission(title=f"Record/Note: {event_name}", description=f"Track event {event_name} audio/notes.", tags=["discord", "audio"])
            await channel.send(f"🎙️ Flagged event for audio recording and created Task Hub mission: `{event_name}`")
            return
            
        elif emoji == "📋":
            await channel.send(f"📋 Event `{event_name}` acknowledged.")
            return

        elif emoji == "✅":
            start_time = event["start_time"]
            end_time = event["end_time"] or start_time
            description = event["description"] or ""
            
            if event_name == "Textual Mention Event" and description:
                lines = [line.strip() for line in description.split('\n') if line.strip()]
                if lines:
                    candidate = lines[0]
                    if len(candidate) > 80:
                        candidate = candidate[:77] + "..."
                    event_name = candidate
                    
            # Embed link back to the Discord event notice
            description += f"\n\nDiscord Notice Link: {msg.jump_url}"
            
            logger.info(f"Adding event '{event_name}' to Google Calendar via GWS CLI.")
            event_payload = dict(event)
            event_payload["description"] = description
            event_payload["discord_event_url"] = event_payload.get("discord_event_url") or msg.jump_url
            ok, detail = await sync_event_to_calendar(self.db, event_payload)
            if ok:
                await channel.send(f"✅ Successfully synced `{event_name}` to Google Calendar.")
            else:
                logger.error("gws calendar error: %s", detail)
                await channel.send(f"⚠️ Failed to sync `{event_name}` to Calendar. Check logs.")
            return

    @tasks.loop(seconds=60)
    async def poll_database(self):
        # Poll for unnotified scheduled events
        try:
            with self.db._get_conn() as conn:
                cur = conn.execute("SELECT * FROM scheduled_events WHERE notified = 0")
                events = cur.fetchall()
            if events:
                for guild in self.guilds:
                    channel = self._get_intel_channel(guild, "event-calendar")
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

    @tasks.loop(seconds=90)
    async def poll_signals_feed(self):
        """Post unnotified signals (keyword matches, release detections) to #signals-feed."""
        try:
            signals = self.db.get_unnotified_signals(limit=10)
            if not signals:
                logger.debug("No unnotified signals to post.")
                return
            logger.info(f"Posting {len(signals)} unnotified signals to #signals-feed")
                
            for guild in self.guilds:
                signals_channel = self._get_intel_channel(guild, "signals-feed")
                if not signals_channel:
                    # Fallback to research-feed if signals-feed doesn't exist
                    signals_channel = self._get_intel_channel(guild, "research-feed")
                    
                release_channel = self._get_intel_channel(guild, "release-tracker")
                    
                for sig in signals:
                    is_release = "release" in sig['rule_matched']
                    channel = release_channel if is_release else signals_channel
                    
                    if not channel:
                        continue
                        
                    # Color code by severity
                    color = discord.Color.red() if sig['severity'] == 'high' else discord.Color.gold()
                    if is_release:
                        color = discord.Color.teal()
                    
                    # Truncate content for embed, respecting Discord limits while keeping most info
                    content_preview = (sig.get('content') or '')[:4000]
                    
                    embed = discord.Embed(
                        title=f"🚀 New Release Detected" if is_release else f"🔔 Signal: {sig['rule_matched']}",
                        description=content_preview,
                        color=color,
                        timestamp=datetime.fromisoformat(sig['created_at']) if sig.get('created_at') else None
                    )
                    if not is_release:
                        embed.add_field(name="Severity", value=sig['severity'].upper(), inline=True)
                    embed.add_field(name="Server", value=sig.get('server_name') or 'Unknown', inline=True)
                    embed.add_field(name="Channel", value=f"#{sig.get('channel_name') or 'unknown'}", inline=True)
                    embed.add_field(name="Author", value=sig.get('author_name') or 'Unknown', inline=True)
                    embed.set_footer(text=f"Signal ID: {sig['id']} | Action: CODIE task created" if is_release else f"Signal ID: {sig['id']}")
                    
                    await channel.send(embed=embed)
                    
                    # Route High Severity to #alerts
                    if sig['severity'] == 'high':
                        alerts_channel = discord.utils.get(self.guilds[0].channels, name="alerts")
                        if alerts_channel:
                            await alerts_channel.send(content="⚠️ **HIGH SEVERITY ALERT**", embed=embed)
                            
            self.db.mark_signals_notified([s['id'] for s in signals])
        except Exception as e:
            logger.error(f"Signal feed polling error: {e}")

    @tasks.loop(seconds=120)
    async def poll_insights_feed(self):
        """Post unnotified triage insights to #announcements-feed."""
        try:
            insights = self.db.get_unnotified_insights(limit=10)
            if not insights:
                logger.debug("No unnotified insights to post.")
                return
            logger.info(f"Posting {len(insights)} unnotified insights to #announcements-feed")
                
            for guild in self.guilds:
                channel = self._get_intel_channel(guild, "announcements-feed")
                if not channel:
                    continue
                    
                for ins in insights:
                    # Color by sentiment
                    if ins['sentiment'] == 'positive':
                        color = discord.Color.green()
                    elif ins['sentiment'] == 'negative':
                        color = discord.Color.red()
                    else:
                        color = discord.Color.greyple()
                    
                    # Urgency emoji
                    urgency_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(ins.get('urgency'), '⚪')
                    
                    embed = discord.Embed(
                        title=f"📊 Insight: {ins['topic']}",
                        description=ins['summary'][:4000],
                        color=color,
                        timestamp=datetime.fromisoformat(ins['created_at']) if ins.get('created_at') else None
                    )
                    embed.add_field(name="Urgency", value=f"{urgency_emoji} {ins.get('urgency', 'low').upper()}", inline=True)
                    embed.add_field(name="Sentiment", value=ins['sentiment'].capitalize(), inline=True)
                    embed.add_field(name="Confidence", value=f"{ins.get('confidence', 0):.0%}", inline=True)
                    if ins.get('server_name'):
                        embed.add_field(name="Source", value=f"{ins['server_name']} / #{ins.get('channel_name', '?')}", inline=True)
                    embed.set_footer(text=f"Insight ID: {ins['id']}")
                    
                    await channel.send(embed=embed)
                    
            self.db.mark_insights_notified([i['id'] for i in insights])
        except Exception as e:
            logger.error(f"Insight feed polling error: {e}")
            
    @tasks.loop(seconds=120)
    async def poll_knowledge_updates(self):
        """Post unnotified knowledge base updates to #knowledge-updates."""
        try:
            updates = self.db.get_unnotified_knowledge_updates(limit=5)
            if not updates:
                return
            logger.info(f"Posting {len(updates)} unnotified KB updates to #knowledge-updates")
            
            for guild in self.guilds:
                channel = self._get_intel_channel(guild, "knowledge-updates")
                if not channel:
                    continue
                    
                for up in updates:
                    embed = discord.Embed(
                        title=f"📚 KB Updated: {up['title']}",
                        description=up['summary'],
                        color=discord.Color.purple(),
                        timestamp=datetime.fromisoformat(up['created_at']) if up.get('created_at') else None
                    )
                    embed.add_field(name="File Path", value=f"`{up['file_path']}`", inline=False)
                    await channel.send(embed=embed)
                    
            self.db.mark_knowledge_updates_notified([u['id'] for u in updates])
        except Exception as e:
            logger.error(f"KB Updates polling error: {e}")
        
    @tasks.loop(minutes=30)
    async def poll_briefings(self):
        try:
            import glob
            import json
            import os
            briefings_dir = os.getenv("UA_DISCORD_BRIEFINGS_DIR", str(APP_ROOT / "kb" / "briefings"))
            cache_file = os.path.join(briefings_dir, ".posted_cache.json")
            
            if not os.path.exists(briefings_dir):
                return
                
            posted = []
            if os.path.exists(cache_file):
                with open(cache_file, "r") as f:
                    posted = json.load(f)
                    
            new_files = []
            for file in glob.glob(os.path.join(briefings_dir, "*.md")):
                fname = os.path.basename(file)
                if fname not in posted:
                    new_files.append(file)
                    posted.append(fname)
                    
            if not new_files:
                return
                
            if not self.guilds:
                return
                
            channel = discord.utils.get(self.guilds[0].channels, name="briefings")
            if not channel:
                return
                
            for file in new_files:
                with open(file, "r") as f:
                    content = f.read()
                
                # Split content into chunks to respect Discord limits (4000 for embed desc, or 2000 for message)
                chunks = [content[i:i+4000] for i in range(0, len(content), 4000)]
                for i, chunk in enumerate(chunks):
                    embed = discord.Embed(
                        title=f"📋 New Briefing: {os.path.basename(file)}" + (f" (Part {i+1}/{len(chunks)})" if len(chunks) > 1 else ""),
                        description=chunk,
                        color=discord.Color.blue()
                    )
                    await channel.send(embed=embed)
                    
            # save cache
            with open(cache_file, "w") as f:
                json.dump(posted, f)
                
        except Exception as e:
            logger.error(f"Briefings polling error: {e}")

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        # Simone chat integration
        # Owner's ID is 351727866549108737
        OWNER_ID = 351727866549108737
        if message.channel.name == "simone-chat" and message.author.id == OWNER_ID:
            logger.info(f"Message for Simone received: {message.content}")
            # Send message payload into Task Hub for Simone's main loop to intercept
            create_task_hub_mission(
                title=f"Chat message from Owner",
                description=message.content,
                tags=["simone-chat", "direct-prompt"]
            )
            await message.channel.send("✅ Inserted into processing queue! Simone will respond shortly.")

        await self.process_commands(message)




def setup_commands(bot: CCBot):
    @bot.tree.command(name="status", description="UA system overview")
    async def status(interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            approvals = await gateway_client.get_approvals_highlight()
            queue_data = await gateway_client.get_dispatch_queue(limit=5)

            pending_count = approvals.get("pending_count", 0)
            queue_items = queue_data.get("items", [])
            queue_total = queue_data.get("total", len(queue_items))

            color = discord.Color.green()
            if pending_count > 3:
                color = discord.Color.orange()
            if pending_count > 10:
                color = discord.Color.red()

            embed = discord.Embed(
                title="\U0001f3d7\ufe0f UA System Status",
                color=color,
                timestamp=datetime.utcnow(),
            )
            embed.add_field(
                name="Pending Reviews",
                value=f"{pending_count} task{'s' if pending_count != 1 else ''} awaiting approval",
                inline=True,
            )
            embed.add_field(
                name="Dispatch Queue",
                value=f"{queue_total} task{'s' if queue_total != 1 else ''} queued",
                inline=True,
            )
            embed.set_footer(text="Universal Agent")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"/status command error: {e}")
            await interaction.followup.send(f"Failed to fetch status: {e}")

    @bot.tree.command(name="queue", description="Show tasks pending human review")
    @app_commands.describe(limit="Max items to show")
    async def queue(interaction: discord.Interaction, limit: int = 10):
        await interaction.response.defer()
        try:
            review_tasks = await gateway_client.get_review_tasks()
            if not review_tasks:
                await interaction.followup.send("No tasks pending review.")
                return
            review_tasks = review_tasks[:limit]
            lines = []
            for t in review_tasks:
                tid = str(t.get("task_id") or "?")[:12]
                title = str(t.get("title") or "Untitled")[:60]
                status = str(t.get("status") or "?")
                lines.append(f"`{tid}` [{status}] {title}")
            reply = "\n".join(lines)
            embed = discord.Embed(
                title=f"\U0001f4cb Pending Review ({len(review_tasks)})",
                description=reply,
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"/queue command error: {e}")
            await interaction.followup.send(f"Failed to fetch review queue: {e}")

    @bot.tree.command(name="task_add", description="Create Task Hub item")
    @app_commands.describe(description="Task text", priority="Priority 1-5")
    async def task_add(interaction: discord.Interaction, description: str, priority: int = 3):
        task_id = create_task_hub_mission(title="Discord CC Task", description=description)
        await interaction.response.send_message(f"Task added: {description} (priority={priority})")
        
        # Post to mission thread 
        if priority >= 3:
            ch = discord.utils.get(interaction.guild.channels, name="mission-status")
            if ch and hasattr(ch, "create_thread"):
                try:
                    msg = await ch.send(f"Mission launched: **{description[:30]}** (Task ID: `{task_id}`)")
                    await msg.create_thread(name=f"Mission: {task_id[:6]}")
                except Exception as e:
                    logger.error(f"Failed to create thread: {e}")

    @bot.tree.command(name="task_list", description="Show task queue")
    @app_commands.describe(status="Status to filter by", limit="Max items to return")
    async def task_list(interaction: discord.Interaction, status: str = None, limit: int = 10):
        tasks = get_task_hub_items(status, limit)
        if not tasks:
            await interaction.response.send_message("No tasks found.")
            return
        
        reply = "\n".join([f"`{t['task_id'][:8]}` [{t['status']}] P{t['priority']}: {t['title'][:80]}" for t in tasks])
        await interaction.response.send_message(f"Task Queue:\n{reply}")

    @bot.tree.command(name="mission_list", description="List all missions with status")
    async def mission_list(interaction: discord.Interaction):
        # Assuming missions are tasks with 'project_key' = 'mission' or just all high priority tasks
        # For simplicity, we just fetch mostly active tasks
        tasks = get_task_hub_items(status="in_progress", limit=10)
        if not tasks:
            await interaction.response.send_message("No active missions found.")
            return
        
        reply = "\n".join([f"`{t['task_id'][:8]}` [{t['status']}]: {t['title'][:80]}" for t in tasks])
        await interaction.response.send_message(f"Active Missions:\n{reply}")

    @bot.tree.command(name="mission_status", description="Active mission details")
    @app_commands.describe(task_id="Task ID")
    async def mission_status(interaction: discord.Interaction, task_id: str):
        task = get_mission_status(task_id)
        if not task:
            await interaction.response.send_message(f"Mission {task_id} not found.")
            return
        
        lines = [
            f"**Title**: {task['title']}",
            f"**Status**: {task['status']}",
            f"**Priority**: {task['priority']}",
            f"**Due**: {task.get('due_at') or 'N/A'}",
            f"**Last Updated**: {task['updated_at']}"
        ]
        await interaction.response.send_message("\n".join(lines))

    @bot.tree.command(name="research", description="Commission ATLAS research mission")
    @app_commands.describe(topic="Topic to research")
    async def research(interaction: discord.Interaction, topic: str):
        task_id = create_task_hub_mission(
            title=f"Research Request: {topic}",
            description=f"Initial research requested via Discord Command against topic: {topic}",
            tags=["research", "ATLAS"]
        )
        if not task_id:
            await interaction.response.send_message(f"❌ Failed to request research on '{topic}'.")
            return
        
        # Post to mission thread 
        ch = discord.utils.get(interaction.guild.channels, name="mission-status")
        if ch and hasattr(ch, "create_thread"):
            try:
                msg = await ch.send(f"Mission launched: **Research {topic}**")
                await msg.create_thread(name=f"Mission: {topic[:50]}")
            except Exception as e:
                logger.error(f"Failed to create thread: {e}")
                
        await interaction.response.send_message(f"✅ Commissioned ATLAS research on: '{topic}'. Task ID: `{task_id}`")

    @bot.tree.command(name="briefing", description="Trigger or retrieve briefing")
    @app_commands.describe(param="now, morning, or weekly")
    async def briefing(interaction: discord.Interaction, param: str = "morning"):
        import glob
        import os
        
        briefings_dir = "/home/kjdragan/lrepos/universal_agent/kb/briefings"
        if not os.path.exists(briefings_dir):
            await interaction.response.send_message("Briefings directory not found.")
            return
            
        md_files = glob.glob(os.path.join(briefings_dir, "*.md"))
        if not md_files:
            await interaction.response.send_message("No briefings available.")
            return
            
        # Get newest file
        latest_file = max(md_files, key=os.path.getmtime)
        with open(latest_file, "r") as f:
            content = f.read()
            
        # Truncate content to Discord embed single message limit if necessary
        content = content[:1900] + "..." if len(content) > 1900 else content
        await interaction.response.send_message(f"**Latest Briefing ({os.path.basename(latest_file)})**:\n{content}")

    @bot.tree.command(name="wiki_query", description="Query the LLM Wiki")
    @app_commands.describe(question="Question to ask")
    async def wiki_query(interaction: discord.Interaction, question: str):
        # We search knowledge_updates table in db for matching text
        with bot.db._get_conn() as conn:
            cur = conn.execute("SELECT title, summary FROM knowledge_updates WHERE summary LIKE ? OR title LIKE ? LIMIT 3", (f"%{question}%", f"%{question}%"))
            results = cur.fetchall()
            
        if not results:
            await interaction.response.send_message(f"No Wiki entries found matching '{question}'.")
            return
            
        reply = "\n\n".join([f"**{r['title']}**\n{r['summary'][:400]}..." for r in results])
        await interaction.response.send_message(f"Wiki Results for '{question}':\n{reply}")

    @bot.tree.command(name="discord_search", description="Search ingested Discord messages")
    @app_commands.describe(query="Text to search")
    async def discord_search(interaction: discord.Interaction, query: str):
        with bot.db._get_conn() as conn:
            cur = conn.execute("SELECT author_name, content FROM messages WHERE content LIKE ? LIMIT 5", (f"%{query}%",))
            results = cur.fetchall()
        if not results:
            await interaction.response.send_message(f"No results found for '{query}'")
            return
            
        # Total length of message cannot exceed 2000 characters, so limit individual results to 350 chars
        reply = "\n".join([f"**{r['author_name']}**: {r['content'][:350]}" for r in results])
        await interaction.response.send_message(f"Search results:\n{reply}")

    @bot.tree.command(name="discord_signals", description="Show recent detected signals")
    async def discord_signals(interaction: discord.Interaction):
        with bot.db._get_conn() as conn:
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

    @bot.tree.command(name="discord_insights", description="Show top unread insights")
    @app_commands.describe(limit="Number of insights")
    async def discord_insights(interaction: discord.Interaction, limit: int = 3):
        with bot.db._get_conn() as conn:
            cur = conn.execute("SELECT server_name, channel_name, category, summary, extract_links FROM insights ORDER BY timestamp DESC LIMIT ?", (limit,))
            results = cur.fetchall()
            
        if not results:
            await interaction.response.send_message("No recent insights found.")
            return
            
        embeds = []
        for r in results:
            emb = discord.Embed(title=f"{r['category']} Insight", description=r['summary'][:2000], color=0x00FF00)
            emb.add_field(name="Source", value=f"{r['server_name']} / {r['channel_name']}")
            if r['extract_links'] and r['extract_links'].strip() != "None":
                emb.add_field(name="Links", value=r['extract_links'][:1000], inline=False)
            embeds.append(emb)
            
        await interaction.response.send_message("Top Insights:", embeds=embeds)

    @bot.tree.command(name="wiki_add", description="Add wiki entry")
    @app_commands.describe(title="Entry title", content="Entry content")
    async def wiki_add(interaction: discord.Interaction, title: str, content: str):
        # Insert into knowledge_updates db
        import uuid
        with bot.db._get_conn() as conn:
            conn.execute("INSERT INTO knowledge_updates (id, title, summary, file_path) VALUES (?, ?, ?, ?)",
                         (f"man_{uuid.uuid4().hex[:8]}", title, content, "manual_entry"))
            conn.commit()
            
        await interaction.response.send_message(f"✅ Added '{title}' to the Wiki.")

    @bot.tree.command(name="setup_webhooks", description="Create artifact routing webhooks")
    async def setup_webhooks(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Must be executed in a Server.")
            return

        out = []
        for chan_name in ["reports", "code-artifacts"]:
            channel = discord.utils.get(interaction.guild.channels, name=chan_name)
            if not channel:
                out.append(f"Channel `#{chan_name}` not found.")
                continue
                
            webhooks = await channel.webhooks()
            hook = discord.utils.get(webhooks, name="UA-Artifact-Router")
            if not hook:
                try:
                    hook = await channel.create_webhook(name="UA-Artifact-Router")
                    out.append(f"Created webhook for `#{chan_name}`")
                except Exception as e:
                    out.append(f"Error creating webhook on `#{chan_name}`: {e}")
            else:
                out.append(f"Webhook already exists for `#{chan_name}`")
                
            if hook:
                # Obscure the token for safety in output, but save to db or print locally if required.
                logger.info(f"WEBHOOK URL FOR {chan_name}: {hook.url}")
                out.append(f"URL logged securely for {chan_name}.")
                
        await interaction.response.send_message("\n".join(out))

    @bot.tree.command(name="config_triage_frequency", description="Adjust Layer 3 frequency")
    @app_commands.describe(hours="Frequency in hours")
    async def config_triage_frequency(interaction: discord.Interaction, hours: int):
        # We don't have a dynamic config DB for this yet. In a complete build, this updates a `config` table in sqlite or env.
        await interaction.response.send_message(f"⚙️ Triage frequency updated to {hours} hours (Stubbed confirmation).")

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
