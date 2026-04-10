"""
Transcription engine for Discord voice recordings.

Uses faster-whisper (CTranslate2 backend) for offline speech-to-text,
producing per-speaker diarized transcripts in Markdown format.

Usage:
    transcriber = Transcriber(db, model_size="base")
    await transcriber.transcribe_pending_events()
"""

import asyncio
import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("discord_transcriber")

# Default whisper model — balance speed/accuracy for server events
DEFAULT_MODEL_SIZE = "base"
# Larger model for higher accuracy (slower): "small", "medium", "large-v3"


class Transcriber:
    """Transcribes WAV recordings to text using faster-whisper."""

    def __init__(self, db, model_size: str = DEFAULT_MODEL_SIZE, compute_type: str = "int8"):
        from .database import DiscordIntelligenceDB
        self.db: DiscordIntelligenceDB = db
        self.model_size = model_size
        self.compute_type = compute_type
        self._model = None
        self._model_lock = asyncio.Lock()

    def _get_model(self):
        """Lazy-load the Whisper model (heavy one-time cost)."""
        if self._model is None:
            from faster_whisper import WhisperModel
            logger.info(f"Loading Whisper model '{self.model_size}' (compute_type={self.compute_type})...")
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type=self.compute_type,
            )
            logger.info("Whisper model loaded successfully.")
        return self._model

    def transcribe_file(self, wav_path: str, language: str = "en") -> Optional[dict]:
        """
        Transcribe a single WAV file.
        
        Returns:
            dict with keys: text, segments, language, duration
        """
        if not Path(wav_path).exists():
            logger.error(f"WAV file not found: {wav_path}")
            return None

        try:
            model = self._get_model()
            segments, info = model.transcribe(
                wav_path,
                language=language,
                beam_size=5,
                vad_filter=True,  # Filter out silence
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=200,
                ),
            )

            # Collect all segments
            result_segments = []
            full_text_parts = []
            
            for segment in segments:
                seg_data = {
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": segment.text.strip(),
                }
                result_segments.append(seg_data)
                full_text_parts.append(segment.text.strip())

            result = {
                "text": " ".join(full_text_parts),
                "segments": result_segments,
                "language": info.language if info else language,
                "duration": info.duration if info else 0,
            }

            logger.info(
                f"Transcribed {wav_path}: {len(result_segments)} segments, "
                f"{result['duration']:.1f}s, language={result['language']}"
            )
            return result

        except Exception as e:
            logger.error(f"Transcription failed for {wav_path}: {e}")
            return None

    def _build_diarized_transcript(self, event_dir: Path, event_name: str) -> Optional[str]:
        """
        Build a diarized transcript from per-speaker WAV files.
        
        Reads the metadata sidecar to identify speakers, transcribes each
        speaker's track, and interleaves by timestamp.
        """
        # Find metadata file
        meta_files = list(event_dir.glob("*_metadata.json"))
        if not meta_files:
            logger.warning(f"No metadata file found in {event_dir}")
            return None

        with open(meta_files[0]) as f:
            metadata = json.load(f)

        # Find all speaker WAV files
        speaker_wavs = sorted(event_dir.glob("speaker_*.wav"))
        if not speaker_wavs:
            # Try the mixed file directly
            mixed_files = list(event_dir.glob("*_mixed.wav"))
            if mixed_files:
                speaker_wavs = mixed_files

        if not speaker_wavs:
            logger.warning(f"No WAV files found in {event_dir}")
            return None

        # Build speaker name lookup from metadata
        ssrc_to_name = {}
        for speaker in metadata.get("speakers", []):
            ssrc_to_name[str(speaker["ssrc"])] = speaker.get("user_name", f"Speaker_{speaker['ssrc']}")

        # Transcribe each speaker's track
        all_segments = []
        
        for wav_path in speaker_wavs:
            # Extract SSRC from filename (speaker_<name>_<ssrc>.wav)
            stem = wav_path.stem
            parts = stem.split("_")
            ssrc = parts[-1] if parts else "unknown"
            speaker_name = ssrc_to_name.get(ssrc, f"Speaker ({ssrc})")

            # Check if this is a mixed file
            if "mixed" in stem:
                speaker_name = "Mixed Audio"

            logger.info(f"Transcribing {wav_path.name} (speaker: {speaker_name})...")
            result = self.transcribe_file(str(wav_path))
            
            if result and result["segments"]:
                for seg in result["segments"]:
                    seg["speaker"] = speaker_name
                    all_segments.append(seg)

        if not all_segments:
            logger.warning(f"No transcribable audio found in {event_dir}")
            return None

        # Sort all segments by start time
        all_segments.sort(key=lambda s: s["start"])

        # Build markdown transcript
        lines = [
            f"# Transcript: {event_name}",
            f"",
            f"**Date:** {metadata.get('started_at', 'Unknown')}",
            f"**Duration:** {self._format_duration(all_segments)}",
            f"**Speakers:** {', '.join(set(s['speaker'] for s in all_segments))}",
            f"",
            "---",
            "",
        ]

        current_speaker = None
        for seg in all_segments:
            timestamp = self._format_time(seg["start"])
            
            if seg["speaker"] != current_speaker:
                current_speaker = seg["speaker"]
                lines.append(f"")
                lines.append(f"### {current_speaker}")
                lines.append(f"")

            lines.append(f"**[{timestamp}]** {seg['text']}")

        lines.append("")
        lines.append("---")
        lines.append(f"*Transcribed at {datetime.now(timezone.utc).isoformat()}*")

        return "\n".join(lines)

    async def transcribe_event(self, event_id: str, event_name: str, audio_path: str) -> Optional[str]:
        """
        Transcribe audio for a specific event.
        
        Returns the path to the generated transcript file, or None.
        """
        audio_dir = Path(audio_path)
        
        # If audio_path points to a specific file, use its parent directory
        if audio_dir.is_file():
            audio_dir = audio_dir.parent

        if not audio_dir.exists():
            logger.error(f"Audio directory not found: {audio_dir}")
            self.db.update_event_transcript(event_id, "", status="error")
            return None

        self.db.update_event_transcript(event_id, "", status="processing")

        try:
            # Run transcription in a thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            transcript_md = await loop.run_in_executor(
                None,
                self._build_diarized_transcript,
                audio_dir,
                event_name,
            )

            if not transcript_md:
                self.db.update_event_transcript(event_id, "", status="empty")
                return None

            # Save transcript
            transcript_path = audio_dir / f"transcript_{event_id[:8]}.md"
            transcript_path.write_text(transcript_md, encoding="utf-8")
            logger.info(f"Saved transcript: {transcript_path}")

            # Also save to KB briefings
            kb_path = Path("/home/kjdragan/lrepos/universal_agent/kb/briefings")
            kb_path.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c for c in event_name if c.isalnum() or c in " _-").replace(' ', '_')
            briefing_file = kb_path / f"AudioTranscript_{safe_name}.md"
            briefing_file.write_text(transcript_md, encoding="utf-8")
            logger.info(f"Pushed transcript to KB Briefings: {briefing_file}")

            # Update DB
            self.db.update_event_transcript(event_id, str(transcript_path), status="complete")

            return str(transcript_path)

        except Exception as e:
            logger.error(f"Transcription pipeline failed for event {event_id}: {e}")
            self.db.update_event_transcript(event_id, "", status="error")
            return None

    async def transcribe_pending_events(self) -> int:
        """
        Process all events that have audio but no transcript.
        
        Returns count of events transcribed.
        """
        pending = self.db.get_events_pending_transcription()
        if not pending:
            logger.info("No events pending transcription.")
            return 0

        logger.info(f"Found {len(pending)} events pending transcription.")
        count = 0

        for event in pending:
            audio_path = event.get("audio_path")
            if not audio_path:
                continue

            result = await self.transcribe_event(
                event_id=event["id"],
                event_name=event.get("name", "Unknown Event"),
                audio_path=audio_path,
            )
            if result:
                count += 1

        logger.info(f"Transcription complete: {count}/{len(pending)} events processed.")
        return count

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds as HH:MM:SS."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def _format_duration(segments: list) -> str:
        """Calculate total duration from segments."""
        if not segments:
            return "0:00"
        max_end = max(s.get("end", 0) for s in segments)
        return Transcriber._format_time(max_end)


if __name__ == "__main__":
    # Standalone test mode
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m discord_intelligence.transcriber <wav_file_or_event_dir>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    
    path = Path(sys.argv[1])
    
    # Simple file transcription (no DB needed)
    t = Transcriber.__new__(Transcriber)
    t.model_size = DEFAULT_MODEL_SIZE
    t.compute_type = "int8"
    t._model = None
    
    if path.is_file() and path.suffix == ".wav":
        result = t.transcribe_file(str(path))
        if result:
            print(f"\n{'='*60}")
            print(f"Language: {result['language']}")
            print(f"Duration: {result['duration']:.1f}s")
            print(f"Segments: {len(result['segments'])}")
            print(f"{'='*60}")
            print(result['text'])
    else:
        print(f"Not a valid WAV file: {path}")
