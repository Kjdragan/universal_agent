"""
Audio Recorder for Discord Stage/Voice Channels.

Hooks into discord.py-self's VoiceClient to capture incoming audio packets,
decrypt and decode them from Opus to PCM, and write per-speaker WAV files.
After the event ends, merges all speaker tracks into a single mixed WAV.

Decryption: discord.py-self only ships _encrypt_* methods (for sending audio).
We implement the corresponding _decrypt_* methods to handle received audio,
using the voice connection's negotiated mode and secret_key.

Usage:
    recorder = AudioRecorder(db, recordings_dir)
    await recorder.start_recording(client, voice_channel, event_id, event_name)
    # ... event runs ...
    await recorder.stop_recording(event_id)
"""

import asyncio
import logging
import struct
import wave
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass, field

import discord

try:
    import nacl.secret
    import nacl.utils
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

logger = logging.getLogger("discord_audio_recorder")

# Opus constants
OPUS_SAMPLE_RATE = 48000
OPUS_CHANNELS = 2
OPUS_FRAME_DURATION_MS = 20
OPUS_FRAME_SIZE = int(OPUS_SAMPLE_RATE * OPUS_FRAME_DURATION_MS / 1000)  # 960 samples

# RTP header is 12 bytes minimum
RTP_HEADER_SIZE = 12

# Silence frame for filling gaps: 5 bytes of Opus-encoded silence
OPUS_SILENCE = b'\xf8\xff\xfe'


@dataclass
class SpeakerTrack:
    """Tracks PCM data for a single speaker (identified by SSRC)."""
    ssrc: int
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    pcm_data: bytearray = field(default_factory=bytearray)
    packet_count: int = 0
    first_packet_time: Optional[float] = None
    last_packet_time: Optional[float] = None


class OpusDecoder:
    """Wraps the opus decoder from the discord library."""

    def __init__(self):
        self._decoders: Dict[int, object] = {}

    def decode(self, ssrc: int, data: bytes) -> Optional[bytes]:
        """Decode Opus packet to PCM for a given SSRC."""
        try:
            if ssrc not in self._decoders:
                decoder = discord.opus.Decoder()
                self._decoders[ssrc] = decoder

            decoder = self._decoders[ssrc]
            # discord.opus.Decoder.decode returns PCM bytes
            pcm = decoder.decode(data, OPUS_FRAME_SIZE)
            return pcm
        except Exception as e:
            logger.debug(f"Opus decode error for SSRC {ssrc}: {e}")
            return None

    def cleanup(self):
        self._decoders.clear()


class VoicePacketDecryptor:
    """
    Decrypts incoming voice packets using the same encryption mode as the VoiceClient.
    
    discord.py-self negotiates one of these modes with Discord's voice server:
    - aead_xchacha20_poly1305_rtpsize (newest, preferred)
    - xsalsa20_poly1305_lite
    - xsalsa20_poly1305_suffix  
    - xsalsa20_poly1305
    
    We implement the decrypt counterpart for each.
    """

    def __init__(self, secret_key: bytes, mode: str):
        self.secret_key = secret_key
        self.mode = mode
        self._decrypt_fn = getattr(self, f'_decrypt_{mode}', None)
        
        if self._decrypt_fn is None:
            raise ValueError(f"Unsupported voice encryption mode: {mode}")
        
        logger.info(f"VoicePacketDecryptor initialized with mode: {mode}")

    def decrypt(self, data: bytes) -> Optional[tuple]:
        """
        Decrypt a raw voice UDP packet.
        
        Returns:
            (ssrc, opus_data) tuple, or None if decryption fails.
        """
        if len(data) < RTP_HEADER_SIZE:
            return None

        try:
            return self._decrypt_fn(data)
        except Exception as e:
            logger.debug(f"Decryption failed ({self.mode}): {e}")
            return None

    def _parse_rtp_header(self, data: bytes) -> tuple:
        """
        Parse RTP header fields.
        
        Returns:
            (header_bytes, ssrc, header_size) or raises on invalid data.
        """
        first_byte = data[0]
        has_extension = bool(first_byte & 0x10)
        cc = first_byte & 0x0F
        
        header_size = RTP_HEADER_SIZE + (4 * cc)
        
        if len(data) < header_size:
            raise ValueError("Packet too short for RTP header")
        
        ssrc = struct.unpack_from('>I', data, 8)[0]
        header = data[:header_size]
        
        return header, ssrc, header_size, has_extension

    def _strip_rtp_extension(self, data: bytes, offset: int) -> int:
        """Advance past an RTP header extension if present."""
        if len(data) < offset + 4:
            raise ValueError("Packet too short for RTP extension")
        ext_length = struct.unpack_from('>H', data, offset + 2)[0]
        return offset + 4 + (ext_length * 4)

    # ── xsalsa20_poly1305 ──────────────────────────────────────────────
    # Encrypt: header + box.encrypt(data, nonce=header[:12] padded to 24).ciphertext
    # Decrypt: use header[:12] as nonce (padded), decrypt ciphertext after header
    
    def _decrypt_xsalsa20_poly1305(self, data: bytes) -> Optional[tuple]:
        header, ssrc, header_size, _ = self._parse_rtp_header(data)
        
        encrypted_data = data[header_size:]
        if len(encrypted_data) < nacl.secret.SecretBox.MACBYTES:
            return None
        
        box = nacl.secret.SecretBox(self.secret_key)
        nonce = bytearray(24)
        nonce[:12] = header[:12]  # First 12 bytes of RTP header as nonce
        
        decrypted = box.decrypt(bytes(encrypted_data), bytes(nonce))
        return (ssrc, decrypted)

    # ── xsalsa20_poly1305_suffix ────────────────────────────────────────
    # Encrypt: header + ciphertext + nonce (24 bytes appended)
    # Decrypt: last 24 bytes are the nonce
    
    def _decrypt_xsalsa20_poly1305_suffix(self, data: bytes) -> Optional[tuple]:
        header, ssrc, header_size, _ = self._parse_rtp_header(data)
        
        nonce_size = nacl.secret.SecretBox.NONCE_SIZE  # 24 bytes
        if len(data) < header_size + nacl.secret.SecretBox.MACBYTES + nonce_size:
            return None
        
        # Nonce is the last 24 bytes
        nonce = data[-nonce_size:]
        encrypted_data = data[header_size:-nonce_size]
        
        box = nacl.secret.SecretBox(self.secret_key)
        decrypted = box.decrypt(bytes(encrypted_data), bytes(nonce))
        return (ssrc, decrypted)

    # ── xsalsa20_poly1305_lite ──────────────────────────────────────────
    # Encrypt: header + ciphertext + nonce[:4] (4-byte incrementing counter)
    # Decrypt: last 4 bytes are the truncated nonce (pad to 24)
    
    def _decrypt_xsalsa20_poly1305_lite(self, data: bytes) -> Optional[tuple]:
        header, ssrc, header_size, _ = self._parse_rtp_header(data)
        
        if len(data) < header_size + nacl.secret.SecretBox.MACBYTES + 4:
            return None
        
        # Nonce is the last 4 bytes, padded to 24
        nonce = bytearray(24)
        nonce[:4] = data[-4:]
        encrypted_data = data[header_size:-4]
        
        box = nacl.secret.SecretBox(self.secret_key)
        decrypted = box.decrypt(bytes(encrypted_data), bytes(nonce))
        return (ssrc, decrypted)

    # ── aead_xchacha20_poly1305_rtpsize ─────────────────────────────────
    # Encrypt: header + aead.encrypt(data, aad=header, nonce[:4] counter).ciphertext + nonce[:4]
    # Decrypt: last 4 bytes are truncated nonce; header is AAD
    
    def _decrypt_aead_xchacha20_poly1305_rtpsize(self, data: bytes) -> Optional[tuple]:
        header, ssrc, header_size, has_extension = self._parse_rtp_header(data)
        
        if len(data) < header_size + 4 + 16:  # 4 nonce + 16 tag minimum
            return None
        
        # Nonce is the last 4 bytes, padded to 24
        nonce = bytearray(24)
        nonce[:4] = data[-4:]
        
        # Encrypted payload sits between header and the 4-byte nonce suffix
        encrypted_data = data[header_size:-4]
        
        try:
            box = nacl.secret.Aead(self.secret_key)
            decrypted = box.decrypt(bytes(encrypted_data), bytes(header), bytes(nonce))
            
            # For this mode, the decrypted data may include an RTP header extension
            # that was part of the encrypted payload. We need to skip past it.
            offset = 0
            if has_extension and len(decrypted) >= 4:
                ext_length = struct.unpack_from('>H', decrypted, 2)[0]
                offset = 4 + (ext_length * 4)
            
            return (ssrc, decrypted[offset:])
        except Exception as e:
            logger.debug(f"AEAD decrypt failed: {e}")
            return None


class ActiveRecording:
    """Manages a single active recording session."""

    def __init__(self, event_id: str, event_name: str, output_dir: Path):
        self.event_id = event_id
        self.event_name = event_name
        self.output_dir = output_dir
        self.speakers: Dict[int, SpeakerTrack] = {}  # ssrc -> SpeakerTrack
        self.ssrc_to_user: Dict[int, tuple] = {}  # ssrc -> (user_id, user_name)
        self.decoder = OpusDecoder()
        self.decryptor: Optional[VoicePacketDecryptor] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.started_at = datetime.now(timezone.utc)
        self._stop_event = asyncio.Event()
        self._recording = False

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def initialize_decryptor(self, voice_client: discord.VoiceClient):
        """
        Initialize the decryptor using the VoiceClient's negotiated secret_key and mode.
        
        Must be called AFTER the voice handshake completes (secret_key is available).
        """
        if not HAS_NACL:
            logger.error("PyNaCl not available — cannot decrypt voice packets")
            return
        
        try:
            secret_key = bytes(voice_client.secret_key)
            mode = voice_client.mode
            self.decryptor = VoicePacketDecryptor(secret_key, mode)
            logger.info(f"Decryptor ready: mode={mode}, key_len={len(secret_key)}")
        except Exception as e:
            logger.error(f"Failed to initialize decryptor: {e}")
            self.decryptor = None

    def on_voice_packet(self, data: bytes):
        """
        Callback registered with SocketReader via add_socket_listener.
        
        Receives raw encrypted UDP voice data. Decrypts, extracts SSRC,
        decodes Opus→PCM, and stores per-speaker.
        """
        if not self._recording:
            return

        if self.decryptor is None:
            return

        # Decrypt the packet
        result = self.decryptor.decrypt(data)
        if result is None:
            return
        
        ssrc, opus_data = result
        
        if not opus_data or len(opus_data) < 3:
            return
        
        now = asyncio.get_event_loop().time()
        
        # Initialize speaker track if new SSRC
        if ssrc not in self.speakers:
            user_info = self.ssrc_to_user.get(ssrc, (None, None))
            self.speakers[ssrc] = SpeakerTrack(
                ssrc=ssrc,
                user_id=user_info[0],
                user_name=user_info[1],
                first_packet_time=now
            )
            logger.info(f"New speaker detected: SSRC={ssrc} user={user_info[1] or 'unknown'}")
        
        track = self.speakers[ssrc]
        track.last_packet_time = now
        track.packet_count += 1
        
        # Decode Opus to PCM
        pcm = self.decoder.decode(ssrc, opus_data)
        if pcm:
            track.pcm_data.extend(pcm)

    def register_ssrc_user(self, ssrc: int, user_id: str, user_name: str):
        """Map an SSRC to a Discord user (from SPEAKING events)."""
        self.ssrc_to_user[ssrc] = (user_id, user_name)
        if ssrc in self.speakers:
            self.speakers[ssrc].user_id = user_id
            self.speakers[ssrc].user_name = user_name

    def save_recordings(self) -> Optional[str]:
        """Save all speaker tracks as WAV files and create a mixed output."""
        if not self.speakers:
            logger.warning(f"No audio data captured for event {self.event_id}")
            return None

        saved_files = []
        
        for ssrc, track in self.speakers.items():
            if not track.pcm_data:
                continue
                
            speaker_name = track.user_name or f"unknown_{ssrc}"
            safe_name = "".join(c for c in speaker_name if c.isalnum() or c in "_-")
            wav_path = self.output_dir / f"speaker_{safe_name}_{ssrc}.wav"
            
            try:
                with wave.open(str(wav_path), 'wb') as wf:
                    wf.setnchannels(OPUS_CHANNELS)
                    wf.setsampwidth(2)  # 16-bit PCM
                    wf.setframerate(OPUS_SAMPLE_RATE)
                    wf.writeframes(bytes(track.pcm_data))
                saved_files.append(str(wav_path))
                duration_sec = len(track.pcm_data) / (OPUS_SAMPLE_RATE * OPUS_CHANNELS * 2)
                logger.info(
                    f"Saved speaker track: {wav_path} "
                    f"({track.packet_count} packets, {duration_sec:.1f}s)"
                )
            except Exception as e:
                logger.error(f"Failed to save speaker track for {speaker_name}: {e}")

        # Create mixed WAV from all speakers
        mixed_path = self.output_dir / f"event_{self.event_id}_mixed.wav"
        if saved_files:
            try:
                self._mix_wav_files(saved_files, str(mixed_path))
                logger.info(f"Created mixed recording: {mixed_path}")
            except Exception as e:
                logger.error(f"Failed to create mixed recording: {e}")
                # Fall back to first speaker file
                if saved_files:
                    mixed_path = Path(saved_files[0])

        # Write metadata sidecar
        self._write_metadata_sidecar(saved_files)
        
        self.decoder.cleanup()
        return str(mixed_path) if mixed_path.exists() else None

    def _mix_wav_files(self, wav_paths: list, output_path: str):
        """Mix multiple WAV files into a single output using ffmpeg."""
        import subprocess
        
        if len(wav_paths) == 1:
            # Just copy
            import shutil
            shutil.copy2(wav_paths[0], output_path)
            return

        # Use ffmpeg amix filter 
        inputs = []
        for p in wav_paths:
            inputs.extend(['-i', p])
        
        cmd = ['ffmpeg', '-y'] + inputs + [
            '-filter_complex', f'amix=inputs={len(wav_paths)}:duration=longest',
            '-ac', '2',
            '-ar', str(OPUS_SAMPLE_RATE),
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg mix failed: {result.stderr[:500]}")

    def _write_metadata_sidecar(self, wav_paths: list):
        """Write a JSON sidecar with recording metadata."""
        meta = {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "started_at": self.started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "encryption_mode": self.decryptor.mode if self.decryptor else "unknown",
            "speakers": [],
            "wav_files": wav_paths,
        }
        
        for ssrc, track in self.speakers.items():
            duration = len(track.pcm_data) / (OPUS_SAMPLE_RATE * OPUS_CHANNELS * 2) if track.pcm_data else 0
            meta["speakers"].append({
                "ssrc": ssrc,
                "user_id": track.user_id,
                "user_name": track.user_name,
                "packet_count": track.packet_count,
                "duration_seconds": round(duration, 2),
            })
        
        meta_path = self.output_dir / f"event_{self.event_id}_metadata.json"
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2)
        logger.info(f"Saved metadata: {meta_path}")


class AudioRecorder:
    """
    High-level recorder that manages voice connections and audio capture.
    
    Approach:
    1. Connect to the voice/stage channel via VoiceClient
    2. For Stage Channels: request audience mode (suppress=True)
    3. Register a SocketReader callback via add_socket_listener
    4. Decrypt incoming packets using VoiceClient's secret_key and mode
    5. Decode Opus → PCM and store per-speaker
    6. On stop, save WAV files and create a mixed output
    """

    def __init__(self, db, recordings_base_dir: str):
        from .database import DiscordIntelligenceDB
        self.db: DiscordIntelligenceDB = db
        self.base_dir = Path(recordings_base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        # Persisted recordings directory
        self.persist_dir = self.base_dir / "persisted"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        
        self._active_recordings: Dict[str, ActiveRecording] = {}
        self._recording_tasks: Dict[str, asyncio.Task] = {}

    async def start_recording(
        self,
        client: discord.Client,
        channel: discord.abc.Connectable,
        event_id: str,
        event_name: str,
        max_duration_hours: float = 4.0,
    ) -> bool:
        """
        Join a voice/stage channel and begin recording.
        
        Returns True if recording started successfully.
        """
        if event_id in self._active_recordings:
            logger.warning(f"Already recording event {event_id}")
            return False

        if not HAS_NACL:
            logger.error("PyNaCl is required for voice recording but is not installed")
            return False

        # Create event-specific output directory
        safe_name = "".join(c for c in event_name if c.isalnum() or c in " _-").replace(' ', '_')
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_dir = self.base_dir / f"{timestamp}_{safe_name}_{event_id[:8]}"
        
        recording = ActiveRecording(event_id, event_name, output_dir)

        try:
            # ── Item 1 Fix: Stage Channel audience mode ──────────────────
            # Connect to the voice channel
            is_stage = isinstance(channel, discord.StageChannel)
            
            voice_client = await channel.connect(
                self_deaf=True,   # Self-deafen to signal we're just listening
                self_mute=True,   # Self-mute to ensure we never transmit
            )
            recording.voice_client = voice_client
            
            # For Stage Channels, we MUST suppress ourselves as audience.
            # Without this, we'd appear as a speaker or get kicked.
            if is_stage and hasattr(channel, 'guild') and channel.guild:
                try:
                    me = channel.guild.me
                    if me:
                        await me.edit(suppress=True)
                        logger.info(f"Joined Stage Channel '{channel.name}' as audience (suppressed)")
                    else:
                        logger.warning("Could not find guild.me to set suppress=True")
                except discord.HTTPException as e:
                    logger.warning(f"Failed to set suppress=True: {e}")
                except Exception as e:
                    logger.warning(f"Unexpected error setting suppress: {e}")
            else:
                logger.info(f"Joined Voice Channel '{channel.name}'")

            # ── Item 2 Fix: Wait for secret_key, then init decryptor ─────
            # The secret_key is set during the voice handshake. We need to
            # wait until it's available before we can decrypt packets.
            try:
                await asyncio.wait_for(
                    self._wait_for_secret_key(voice_client),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.error(f"Timed out waiting for voice secret_key (event {event_id})")
                await voice_client.disconnect()
                return False
            
            # Initialize the decryptor with the negotiated key and mode
            recording.initialize_decryptor(voice_client)
            if recording.decryptor is None:
                logger.error(f"Decryptor initialization failed for event {event_id}")
                await voice_client.disconnect()
                return False
            
            recording._recording = True
            
            # ── Register socket listener using the public API ────────────
            if hasattr(voice_client, '_connection') and hasattr(voice_client._connection, 'add_socket_listener'):
                voice_client._connection.add_socket_listener(recording.on_voice_packet)
                logger.info(f"Registered voice packet listener for event {event_id}")
            else:
                logger.error(
                    f"Cannot access add_socket_listener on VoiceConnectionState. "
                    f"discord.py-self API may have changed. Recording will not capture audio."
                )
                await voice_client.disconnect()
                return False

            self._active_recordings[event_id] = recording
            
            # Update DB
            self.db.update_event_audio_path(event_id, str(output_dir))
            
            # Set max-duration auto-stop
            async def auto_stop():
                await asyncio.sleep(max_duration_hours * 3600)
                logger.info(f"Auto-stopping recording for event {event_id} (max duration reached)")
                await self.stop_recording(event_id)
            
            self._recording_tasks[event_id] = asyncio.create_task(auto_stop())
            
            logger.info(f"🎙️ Started recording event '{event_name}' (ID: {event_id}) in {output_dir}")
            return True

        except Exception as e:
            logger.error(f"Failed to start recording for event {event_id}: {e}", exc_info=True)
            return False

    async def _wait_for_secret_key(self, voice_client: discord.VoiceClient):
        """Poll until the VoiceClient has its secret_key from the handshake."""
        while True:
            try:
                key = voice_client.secret_key
                if key is not None and key is not discord.utils.MISSING:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.1)

    async def stop_recording(self, event_id: str) -> Optional[str]:
        """
        Stop recording, disconnect from voice, save audio files.
        
        Returns path to the mixed WAV file, or None if no audio was captured.
        """
        if event_id not in self._active_recordings:
            logger.warning(f"No active recording for event {event_id}")
            return None
        
        recording = self._active_recordings.pop(event_id)
        recording._recording = False
        
        # Cancel auto-stop task
        task = self._recording_tasks.pop(event_id, None)
        if task and not task.done():
            task.cancel()
        
        # Unregister callback and disconnect
        if recording.voice_client:
            try:
                if hasattr(recording.voice_client, '_connection') and hasattr(recording.voice_client._connection, 'remove_socket_listener'):
                    recording.voice_client._connection.remove_socket_listener(recording.on_voice_packet)
            except Exception:
                pass
            
            try:
                await recording.voice_client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting voice client: {e}")
        
        # Save audio to WAV files
        mixed_path = recording.save_recordings()
        
        if mixed_path:
            self.db.update_event_audio_path(event_id, mixed_path)
            logger.info(f"🎙️ Recording complete for event {event_id}: {mixed_path}")
        else:
            logger.warning(f"No audio captured for event {event_id}")
        
        return mixed_path

    @property
    def active_recordings(self) -> list:
        """List of currently active recording event IDs."""
        return list(self._active_recordings.keys())

    def is_recording(self, event_id: str) -> bool:
        return event_id in self._active_recordings
