"""
Audio retention and cleanup for Discord voice recordings.

Policy:
- WAV files older than 30 days are auto-deleted UNLESS persist_audio=True
- Persisted files are moved to recordings/persisted/ with metadata sidecar
- Transcripts are always retained (they're small text files)

Usage:
    cleanup = AudioCleanup(db, recordings_dir)
    await cleanup.run_cleanup()  # called from daemon's scheduled loop
"""

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import shutil

logger = logging.getLogger("discord_audio_cleanup")

# Default retention period for non-persisted recordings
RETENTION_DAYS = 30


class AudioCleanup:
    """Manages retention policy for Discord voice recordings."""

    def __init__(self, db, recordings_base_dir: str, retention_days: int = RETENTION_DAYS):
        from .database import DiscordIntelligenceDB
        self.db: DiscordIntelligenceDB = db
        self.base_dir = Path(recordings_base_dir)
        self.persist_dir = self.base_dir / "persisted"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days

    async def run_cleanup(self) -> dict:
        """
        Execute the retention cleanup cycle.
        
        Returns a summary dict with counts of deleted/persisted recordings.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.retention_days)
        cutoff_iso = cutoff.isoformat()

        logger.info(
            f"Running audio cleanup (retention={self.retention_days}d, "
            f"cutoff={cutoff.strftime('%Y-%m-%d')})"
        )

        # Get events eligible for cleanup
        events = self.db.get_events_for_audio_cleanup(cutoff_iso)
        
        summary = {
            "scanned": len(events),
            "deleted": 0,
            "bytes_freed": 0,
            "errors": 0,
        }

        for event in events:
            audio_path = event.get("audio_path")
            if not audio_path:
                continue

            path = Path(audio_path)
            
            try:
                if path.is_dir():
                    # Delete entire event recording directory
                    dir_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                    
                    # Keep transcript files (move them to a safe location)
                    for transcript in path.glob("transcript_*.md"):
                        safe_dest = self.base_dir / "transcripts" / transcript.name
                        safe_dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(transcript), str(safe_dest))
                        logger.info(f"Preserved transcript: {safe_dest}")
                    
                    shutil.rmtree(str(path))
                    summary["bytes_freed"] += dir_size
                    logger.info(f"Deleted recording directory: {path} ({dir_size / 1024 / 1024:.1f} MB)")
                    
                elif path.is_file():
                    file_size = path.stat().st_size
                    path.unlink()
                    summary["bytes_freed"] += file_size
                    logger.info(f"Deleted recording file: {path} ({file_size / 1024 / 1024:.1f} MB)")
                else:
                    logger.debug(f"Audio path no longer exists: {path}")
                
                # Clear audio_path in DB (transcript_path remains)
                self.db.update_event_audio_path(event["id"], "")
                summary["deleted"] += 1

            except Exception as e:
                logger.error(f"Failed to clean up {audio_path}: {e}")
                summary["errors"] += 1

        logger.info(
            f"Cleanup complete: {summary['deleted']} deleted, "
            f"{summary['bytes_freed'] / 1024 / 1024:.1f} MB freed, "
            f"{summary['errors']} errors"
        )
        return summary

    def persist_recording(self, event_id: str):
        """
        Move a recording to the persistent directory with rich metadata.
        
        This exempts it from the 30-day auto-delete policy.
        """
        events = self.db.get_events_with_audio()
        event = next((e for e in events if e["id"] == event_id), None)
        
        if not event:
            logger.error(f"Event {event_id} not found or has no audio")
            return False

        audio_path = Path(event["audio_path"])
        if not audio_path.exists():
            logger.error(f"Audio path does not exist: {audio_path}")
            return False

        # Mark as persistent in DB
        self.db.set_event_persist_audio(event_id, True)

        # Build rich metadata sidecar for the persisted copy
        persist_meta = {
            "event_id": event["id"],
            "event_name": event.get("name", "Unknown"),
            "server_id": event.get("server_id", "Unknown"),
            "start_time": event.get("start_time"),
            "end_time": event.get("end_time"),
            "location": event.get("location"),
            "description": event.get("description", "")[:1000],
            "persisted_at": datetime.now(timezone.utc).isoformat(),
            "original_path": str(audio_path),
            "transcript_path": event.get("transcript_path"),
            "transcript_status": event.get("transcript_status"),
            "source": "discord_intelligence",
            "retention": "permanent",
        }

        # Create persisted directory with descriptive name
        safe_name = "".join(
            c for c in event.get("name", "unknown") if c.isalnum() or c in " _-"
        ).replace(' ', '_')
        date_str = ""
        if event.get("start_time"):
            try:
                dt = datetime.fromisoformat(event["start_time"].replace('Z', '+00:00'))
                date_str = dt.strftime("%Y%m%d_")
            except Exception:
                pass

        persist_dest = self.persist_dir / f"{date_str}{safe_name}_{event_id[:8]}"
        
        try:
            if audio_path.is_dir():
                shutil.copytree(str(audio_path), str(persist_dest), dirs_exist_ok=True)
            elif audio_path.is_file():
                persist_dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(audio_path), str(persist_dest / audio_path.name))
            
            # Write rich metadata sidecar
            meta_path = persist_dest / "persistence_metadata.json"
            with open(meta_path, 'w') as f:
                json.dump(persist_meta, f, indent=2)
            
            # Update DB to point to the persisted location
            self.db.update_event_audio_path(event_id, str(persist_dest))
            
            logger.info(f"Persisted recording for event '{event.get('name')}' to {persist_dest}")
            return True

        except Exception as e:
            logger.error(f"Failed to persist recording for event {event_id}: {e}")
            return False

    def get_cleanup_stats(self) -> dict:
        """Get statistics about current audio storage usage."""
        stats = {
            "total_recordings": 0,
            "total_size_mb": 0,
            "persisted_count": 0,
            "persisted_size_mb": 0,
            "ephemeral_count": 0,
            "ephemeral_size_mb": 0,
        }

        if self.base_dir.exists():
            for child in self.base_dir.iterdir():
                if child.is_dir() and child.name not in ("persisted", "transcripts"):
                    size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                    stats["total_recordings"] += 1
                    stats["total_size_mb"] += size / 1024 / 1024
                    stats["ephemeral_count"] += 1
                    stats["ephemeral_size_mb"] += size / 1024 / 1024

        if self.persist_dir.exists():
            for child in self.persist_dir.iterdir():
                if child.is_dir():
                    size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                    stats["persisted_count"] += 1
                    stats["persisted_size_mb"] += size / 1024 / 1024
                    stats["total_recordings"] += 1
                    stats["total_size_mb"] += size / 1024 / 1024

        # Round values
        for key in stats:
            if isinstance(stats[key], float):
                stats[key] = round(stats[key], 2)

        return stats
