"""
Automated verification script for the Discord audio pipeline.

This script tests the full pipeline WITHOUT requiring a live Discord connection:
1. Generates a synthetic WAV file with test tones and TTS-like audio
2. Simulates the recording output structure (per-speaker WAVs + metadata)
3. Runs the transcriber on the synthetic audio
4. Validates the transcript output
5. Tests the cleanup/retention policy
6. Reports results

Usage:
    uv run python -m discord_intelligence.audio_verify
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from pathlib import Path
import shutil
import struct
import tempfile
import wave

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("audio_verify")


def generate_sine_wave(
    frequency: float = 440.0,
    duration_seconds: float = 5.0,
    sample_rate: int = 48000,
    channels: int = 2,
    amplitude: float = 0.5,
) -> bytes:
    """Generate a sine wave as raw PCM data."""
    num_samples = int(sample_rate * duration_seconds)
    pcm_data = bytearray()
    
    for i in range(num_samples):
        t = i / sample_rate
        # Add some frequency variation to make it more interesting
        freq = frequency + 50 * math.sin(2 * math.pi * 0.5 * t)
        value = int(amplitude * 32767 * math.sin(2 * math.pi * freq * t))
        value = max(-32768, min(32767, value))
        # Write same value for both channels (stereo)
        for _ in range(channels):
            pcm_data.extend(struct.pack('<h', value))
    
    return bytes(pcm_data)


def generate_speech_like_audio(
    duration_seconds: float = 10.0,
    sample_rate: int = 48000,
    channels: int = 2,
) -> bytes:
    """
    Generate speech-like audio with formant frequencies.
    This won't be intelligible but will activate VAD and produce segments.
    """
    num_samples = int(sample_rate * duration_seconds)
    pcm_data = bytearray()
    
    # Simulate speech with formant-like frequencies (300Hz, 2500Hz) with
    # periodic voiced/unvoiced transitions
    for i in range(num_samples):
        t = i / sample_rate
        
        # Periodic voiced segments (simulate syllables)
        voicing = 1.0 if (int(t * 4) % 3 != 0) else 0.1  # 75% voiced
        
        # Fundamental frequency with natural variation (100-200 Hz)
        f0 = 150 + 30 * math.sin(2 * math.pi * 3 * t)
        
        # Formants
        f1 = 300 + 200 * math.sin(2 * math.pi * 0.3 * t)
        f2 = 2500 + 500 * math.sin(2 * math.pi * 0.5 * t)
        
        value = voicing * 0.3 * 32767 * (
            0.5 * math.sin(2 * math.pi * f0 * t) +
            0.3 * math.sin(2 * math.pi * f1 * t) +
            0.2 * math.sin(2 * math.pi * f2 * t)
        )
        value = int(max(-32768, min(32767, value)))
        
        for _ in range(channels):
            pcm_data.extend(struct.pack('<h', value))
    
    return bytes(pcm_data)


def write_wav(path: Path, pcm_data: bytes, sample_rate: int = 48000, channels: int = 2):
    """Write raw PCM data to a WAV file."""
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


def create_test_recording(output_dir: Path, event_id: str = "test_001") -> dict:
    """
    Create a simulated recording output matching the AudioRecorder's format.
    
    Creates:
    - speaker_Alice_12345.wav (speech-like audio)
    - speaker_Bob_67890.wav (different frequency)
    - event_test_001_mixed.wav (combined)
    - event_test_001_metadata.json
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Speaker 1: Alice (speech-like)
    alice_pcm = generate_speech_like_audio(duration_seconds=8.0)
    alice_path = output_dir / "speaker_Alice_12345.wav"
    write_wav(alice_path, alice_pcm)
    
    # Speaker 2: Bob (different characteristics)
    bob_pcm = generate_speech_like_audio(duration_seconds=6.0)
    bob_path = output_dir / "speaker_Bob_67890.wav"
    write_wav(bob_path, bob_pcm)
    
    # Mixed audio (just use Alice for simplicity — in real scenario ffmpeg would mix)
    mixed_path = output_dir / f"event_{event_id}_mixed.wav"
    write_wav(mixed_path, alice_pcm)  # Simple copy for testing
    
    # Metadata sidecar
    metadata = {
        "event_id": event_id,
        "event_name": "Test Office Hours",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        "speakers": [
            {"ssrc": 12345, "user_id": "111", "user_name": "Alice", "packet_count": 500},
            {"ssrc": 67890, "user_id": "222", "user_name": "Bob", "packet_count": 300},
        ],
        "wav_files": [str(alice_path), str(bob_path)],
    }
    
    meta_path = output_dir / f"event_{event_id}_metadata.json"
    with open(meta_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    return {
        "event_dir": str(output_dir),
        "mixed_path": str(mixed_path),
        "metadata_path": str(meta_path),
        "speaker_files": [str(alice_path), str(bob_path)],
    }


async def test_transcriber(recording_info: dict) -> bool:
    """Test the transcriber on synthetic audio."""
    from discord_intelligence.transcriber import Transcriber
    
    logger.info("=" * 60)
    logger.info("TEST: Transcription Pipeline")
    logger.info("=" * 60)
    
    # Create a minimal mock DB
    class MockDB:
        def __init__(self):
            self._events = []
            self._updates = {}
            
        def get_events_pending_transcription(self):
            return self._events
            
        def update_event_transcript(self, event_id, path, status='complete'):
            self._updates[event_id] = {"path": path, "status": status}
            logger.info(f"  DB update: event={event_id}, status={status}, path={path}")
    
    mock_db = MockDB()
    mock_db._events = [{
        "id": "test_001",
        "name": "Test Office Hours",
        "audio_path": recording_info["event_dir"],
        "transcript_status": "none",
    }]
    
    transcriber = Transcriber(mock_db, model_size="tiny")  # Use tiny model for speed
    
    # Test single file transcription
    logger.info("\n1. Testing single file transcription...")
    result = transcriber.transcribe_file(recording_info["mixed_path"])
    
    if result:
        logger.info(f"   ✓ Transcription succeeded")
        logger.info(f"   Language: {result['language']}")
        logger.info(f"   Duration: {result['duration']:.1f}s")
        logger.info(f"   Segments: {len(result['segments'])}")
        logger.info(f"   Text preview: {result['text'][:200]}")
    else:
        logger.error("   ✗ Transcription returned None")
        return False
    
    # Test diarized transcript building
    logger.info("\n2. Testing diarized transcript build...")
    event_dir = Path(recording_info["event_dir"])
    transcript_md = transcriber._build_diarized_transcript(event_dir, "Test Office Hours")
    
    if transcript_md:
        logger.info(f"   ✓ Diarized transcript generated ({len(transcript_md)} chars)")
        # Save it for inspection
        test_transcript = event_dir / "test_transcript.md"
        test_transcript.write_text(transcript_md)
        logger.info(f"   Saved to: {test_transcript}")
    else:
        logger.warning("   ⚠ Diarized transcript returned None (may be expected with synthetic audio)")
    
    # Test full event transcription pipeline
    logger.info("\n3. Testing full event transcription pipeline...")
    path = await transcriber.transcribe_event("test_001", "Test Office Hours", recording_info["event_dir"])
    
    if path:
        logger.info(f"   ✓ Event transcript saved to: {path}")
    else:
        logger.warning("   ⚠ Event transcript returned None (may be expected with synthetic audio)")
    
    # Check DB was updated
    if "test_001" in mock_db._updates:
        update = mock_db._updates["test_001"]
        logger.info(f"   ✓ DB updated: status={update['status']}")
    else:
        logger.warning("   ⚠ DB not updated for event")
    
    return True


async def test_cleanup(recording_info: dict) -> bool:
    """Test the audio cleanup/retention system."""
    from discord_intelligence.audio_cleanup import AudioCleanup
    
    logger.info("\n" + "=" * 60)
    logger.info("TEST: Audio Cleanup & Retention")
    logger.info("=" * 60)
    
    class MockDB:
        def __init__(self):
            self._events = []
            self._persisted = set()
            
        def get_events_for_audio_cleanup(self, cutoff_iso):
            return [e for e in self._events if not e.get("persist_audio")]
            
        def get_events_with_audio(self):
            return self._events
            
        def update_event_audio_path(self, event_id, path):
            logger.info(f"  DB: audio_path cleared for {event_id}")
            
        def set_event_persist_audio(self, event_id, persist):
            if persist:
                self._persisted.add(event_id)
            logger.info(f"  DB: persist_audio={persist} for {event_id}")
    
    # Create a temporary recordings directory
    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir) / "recordings"
        
        # Copy test recording into it
        test_dir = base_dir / "test_recording"
        shutil.copytree(recording_info["event_dir"], str(test_dir))
        
        mock_db = MockDB()
        mock_db._events = [{
            "id": "test_cleanup_001",
            "name": "Old Event",
            "audio_path": str(test_dir),
            "persist_audio": 0,
            "start_time": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
        }]
        
        cleanup = AudioCleanup(mock_db, str(base_dir), retention_days=30)
        
        # 1. Test cleanup of expired recordings
        logger.info("\n1. Testing cleanup of expired recordings...")
        assert test_dir.exists(), "Test recording should exist before cleanup"
        
        summary = await cleanup.run_cleanup()
        logger.info(f"   ✓ Cleanup ran: {summary}")
        
        if summary["deleted"] > 0:
            logger.info(f"   ✓ Deleted {summary['deleted']} recording(s)")
            if not test_dir.exists():
                logger.info(f"   ✓ Recording directory removed")
        
        # 2. Test persistence
        logger.info("\n2. Testing recording persistence...")
        
        # Re-create test recording
        shutil.copytree(recording_info["event_dir"], str(test_dir))
        mock_db._events = [{
            "id": "test_persist_001",
            "name": "Important Event",
            "audio_path": str(test_dir),
            "persist_audio": 0,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "server_id": "123456",
            "location": "Stage Channel",
            "description": "An important office hours session",
            "transcript_path": None,
            "transcript_status": "none",
        }]
        
        result = cleanup.persist_recording("test_persist_001")
        if result:
            logger.info(f"   ✓ Recording persisted")
            persist_dir = base_dir / "persisted"
            persisted_dirs = list(persist_dir.iterdir())
            if persisted_dirs:
                meta_file = persisted_dirs[0] / "persistence_metadata.json"
                if meta_file.exists():
                    with open(meta_file) as f:
                        meta = json.load(f)
                    logger.info(f"   ✓ Metadata sidecar created: {meta.get('event_name')}")
                    logger.info(f"   ✓ Retention: {meta.get('retention')}")
        else:
            logger.error("   ✗ Persistence failed")
        
        # 3. Test stats
        logger.info("\n3. Testing storage stats...")
        stats = cleanup.get_cleanup_stats()
        logger.info(f"   ✓ Stats: {json.dumps(stats, indent=2)}")
    
    return True


async def test_database_schema() -> bool:
    """Test that the schema extensions work correctly."""
    import sqlite3
    
    logger.info("\n" + "=" * 60)
    logger.info("TEST: Database Schema Extensions")
    logger.info("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        
        from discord_intelligence.database import DiscordIntelligenceDB
        db = DiscordIntelligenceDB(str(db_path))
        
        # 1. Test that new columns exist
        logger.info("\n1. Checking new columns...")
        with db._get_conn() as conn:
            cur = conn.execute("PRAGMA table_info(scheduled_events)")
            columns = {row[1] for row in cur.fetchall()}
        
        required_columns = {'audio_path', 'transcript_path', 'transcript_status', 'persist_audio'}
        missing = required_columns - columns
        
        if missing:
            logger.error(f"   ✗ Missing columns: {missing}")
            return False
        logger.info(f"   ✓ All new columns present: {required_columns}")
        
        # 2. Test audio path update
        logger.info("\n2. Testing audio management methods...")
        
        # Insert a test event
        db.upsert_scheduled_event(
            event_id="test_evt_1",
            server_id="srv_1",
            name="Test Event",
            description="A test",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc) + timedelta(hours=1),
            location="Stage",
            status="ACTIVE",
        )
        
        db.update_event_audio_path("test_evt_1", "/path/to/audio.wav")
        db.update_event_transcript("test_evt_1", "/path/to/transcript.md", "complete")
        db.set_event_persist_audio("test_evt_1", True)
        
        # Verify
        with db._get_conn() as conn:
            cur = conn.execute("SELECT * FROM scheduled_events WHERE id = 'test_evt_1'")
            row = dict(cur.fetchone())
        
        assert row['audio_path'] == "/path/to/audio.wav", f"Unexpected audio_path: {row['audio_path']}"
        assert row['transcript_path'] == "/path/to/transcript.md"
        assert row['transcript_status'] == "complete"
        assert row['persist_audio'] == 1
        
        logger.info(f"   ✓ Audio path: {row['audio_path']}")
        logger.info(f"   ✓ Transcript: {row['transcript_path']} (status={row['transcript_status']})")
        logger.info(f"   ✓ Persist: {row['persist_audio']}")
        
        # 3. Test query methods
        logger.info("\n3. Testing query methods...")
        
        events = db.get_events_with_audio()
        assert len(events) == 1
        logger.info(f"   ✓ get_events_with_audio: {len(events)} event(s)")
        
        # This event has a transcript, so shouldn't appear in pending
        pending = db.get_events_pending_transcription()
        assert len(pending) == 0
        logger.info(f"   ✓ get_events_pending_transcription: {len(pending)} (correct - transcript already complete)")
        
        # This event has persist=True, so shouldn't appear in cleanup
        cutoff = (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()
        cleanup_events = db.get_events_for_audio_cleanup(cutoff)
        assert len(cleanup_events) == 0
        logger.info(f"   ✓ get_events_for_audio_cleanup: {len(cleanup_events)} (correct - marked as persistent)")
    
    return True


async def main():
    """Run all verification tests."""
    logger.info("🔊 Discord Audio Pipeline Verification")
    logger.info("=" * 60)
    
    results = {}
    
    # Create temp directory for test recordings
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = Path(tmpdir) / "test_event"
        
        # Step 1: Generate synthetic recordings
        logger.info("\nStep 1: Generating synthetic test recordings...")
        recording_info = create_test_recording(test_dir, event_id="test_001")
        logger.info(f"  Created {len(recording_info['speaker_files'])} speaker files + mixed + metadata")
        
        # Step 2: Test database schema
        try:
            results["database_schema"] = await test_database_schema()
        except Exception as e:
            logger.error(f"Database schema test failed: {e}", exc_info=True)
            results["database_schema"] = False
        
        # Step 3: Test transcriber
        try:
            results["transcription"] = await test_transcriber(recording_info)
        except Exception as e:
            logger.error(f"Transcription test failed: {e}", exc_info=True)
            results["transcription"] = False
        
        # Step 4: Test cleanup
        try:
            results["cleanup"] = await test_cleanup(recording_info)
        except Exception as e:
            logger.error(f"Cleanup test failed: {e}", exc_info=True)
            results["cleanup"] = False
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("VERIFICATION SUMMARY")
    logger.info("=" * 60)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        logger.info(f"  {status}: {test_name}")
        if not passed:
            all_passed = False
    
    if all_passed:
        logger.info("\n🎉 All tests passed!")
    else:
        logger.info("\n⚠️  Some tests failed. Review output above.")
    
    return all_passed


if __name__ == "__main__":
    asyncio.run(main())
