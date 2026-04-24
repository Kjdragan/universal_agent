#!/usr/bin/env python
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-cloud-texttospeech>=2.29.0", "certifi"]
# ///
"""Narrate a text file into an MP3 audiobook using Google Cloud Text-to-Speech API.

Uses the Cloud TTS API (not AI Studio) for:
- Native MP3 output (no ffmpeg needed)
- Separate prompt/text fields (8KB total budget)
- GA model names + gemini-3.1-flash-tts-preview access
- Service account auth via GOOGLE_APPLICATION_CREDENTIALS

See: docs/03_Operations/123_Gemini_TTS_Source_Of_Truth_2026-04-22.md
"""

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import ssl
import certifi


def get_ssl_context():
    """Create an SSL context using certifi's CA bundle for reliable verification."""
    return ssl.create_default_context(cafile=certifi.where())


# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_VOICE = "Aoede"
DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_LANGUAGE = "en-US"
GCP_PROJECT_ID = "gen-lang-client-0229532959"
SA_KEY_PATH = "/opt/universal_agent/.gcp-tts-sa-key.json"
INFISICAL_SA_KEY_NAME = "GCP_TTS_SERVICE_ACCOUNT_KEY"

# Chunk sizing: Cloud TTS allows 4000 bytes text + 4000 bytes prompt
MAX_TEXT_BYTES = 3800  # leave margin for markup tags


# ── Auth ────────────────────────────────────────────────────────────────────

def get_access_token() -> str:
    """Get an access token for Cloud TTS API using service account or ADC."""
    # 1. Try GOOGLE_APPLICATION_CREDENTIALS (service account key file)
    sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", SA_KEY_PATH)
    if os.path.exists(sa_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
        result = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        # Fallback: activate and use gcloud auth
        subprocess.run(
            ["gcloud", "auth", "activate-service-account",
             f"--key-file={sa_path}"],
            capture_output=True, text=True, timeout=15
        )
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

    # 2. Try gcloud user auth (fallback for local desktop)
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    raise RuntimeError(
        "No valid auth found. Set GOOGLE_APPLICATION_CREDENTIALS or run "
        "'gcloud auth application-default login'"
    )


# ── Text Processing ────────────────────────────────────────────────────────

def chunk_text(text: str, max_bytes: int = MAX_TEXT_BYTES) -> list[str]:
    """Split text into chunks respecting the 4000-byte TTS text limit.

    Splits at paragraph boundaries first, then sentence boundaries.
    Inserts [medium pause] markup between paragraphs for better pacing.
    """
    text_bytes = text.encode("utf-8")
    if len(text_bytes) <= max_bytes:
        return [text]

    chunks = []
    paragraphs = re.split(r'\n\s*\n', text)
    current = ""

    for para in paragraphs:
        para_bytes = para.encode("utf-8")
        test_text = f"{current}\n\n[medium pause]\n\n{para}" if current else para

        if current and len(test_text.encode("utf-8")) > max_bytes:
            chunks.append(current.strip())
            current = para
        else:
            current = test_text if current else para

    if current.strip():
        if len(current.encode("utf-8")) > max_bytes:
            # Split oversized chunk on sentences
            sentences = re.split(r'(?<=[.!?])\s+', current)
            sub = ""
            for s in sentences:
                candidate = f"{sub} {s}".strip() if sub else s
                if len(candidate.encode("utf-8")) > max_bytes:
                    if sub.strip():
                        chunks.append(sub.strip())
                    sub = s
                else:
                    sub = candidate
            if sub.strip():
                chunks.append(sub.strip())
        else:
            chunks.append(current.strip())

    return chunks


def resolve_source(source: str) -> str:
    """Resolve the input source to text content.

    Handles:
    - Local file paths (with desktop→VPS path mapping)
    - URLs (fetches content)
    - Raw text (returns as-is)
    """
    # URL detection
    if source.startswith(("http://", "https://")):
        print(f"Fetching URL via Jina Reader: {source}")
        # Use Jina Reader to extract clean markdown and bypass bot protection
        jina_url = f"https://r.jina.ai/{source}"
        req = urllib.request.Request(jina_url, headers={"User-Agent": "UA-TTS-Narrator/1.0"})
        with urllib.request.urlopen(req, timeout=60, context=get_ssl_context()) as resp:
            return resp.read().decode("utf-8", errors="replace")

    # File path resolution
    path = source
    if not os.path.exists(path):
        # Try desktop→VPS path mapping
        mappings = [
            ("/home/kjdragan/lrepos/universal_agent/", "/opt/universal_agent/"),
            ("/home/kjdragan/lrepos/universal_agent_repo/", "/opt/universal_agent/"),
        ]
        for local_prefix, vps_prefix in mappings:
            if path.startswith(local_prefix):
                vps_path = path.replace(local_prefix, vps_prefix)
                if os.path.exists(vps_path):
                    print(f"Path mapped: {path} → {vps_path}")
                    path = vps_path
                    break

    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read()

    # Check if it's raw text (no file extension, contains spaces)
    if " " in source and not source.endswith((".txt", ".md")):
        return source

    raise FileNotFoundError(f"Cannot find source: {source}")


# ── Cloud TTS API ──────────────────────────────────────────────────────────

def synthesize_chunk(
    access_token: str,
    text: str,
    prompt: str,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
) -> bytes:
    """Synthesize a single chunk using Cloud Text-to-Speech REST API.

    Returns MP3 bytes directly — no conversion needed.
    """
    payload = {
        "input": {
            "text": text,
            "prompt": prompt,
        },
        "voice": {
            "languageCode": language,
            "name": voice,
            "model_name": model,
        },
        "audioConfig": {
            "audioEncoding": "MP3",
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "x-goog-user-project": GCP_PROJECT_ID,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120, context=get_ssl_context()) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    audio_content = result.get("audioContent")
    if not audio_content:
        error = result.get("error", {})
        raise RuntimeError(
            f"TTS API error: {error.get('message', 'No audio in response')}"
        )

    return base64.b64decode(audio_content)


def narrate(
    text: str,
    output_path: str,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    prompt: str | None = None,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """Generate narration from text using Cloud Text-to-Speech API."""
    narrate_prompt = prompt or (
        "Read the following text aloud in a warm, engaging narration style. "
        "Use natural pacing with pauses between paragraphs. "
        "Sound like a skilled audiobook narrator telling a story."
    )

    print(f"Authenticating with Cloud TTS API...")
    access_token = get_access_token()

    chunks = chunk_text(text)
    total_bytes = len(text.encode("utf-8"))
    print(f"Narrating {len(text):,} chars ({total_bytes:,} bytes) in {len(chunks)} chunk(s)")
    print(f"Model: {model} | Voice: {voice}")

    mp3_parts = []
    for i, chunk in enumerate(chunks):
        chunk_bytes = len(chunk.encode("utf-8"))
        print(f"  Chunk {i+1}/{len(chunks)} ({chunk_bytes:,} bytes)...", end=" ", flush=True)
        mp3_data = synthesize_chunk(
            access_token, chunk, narrate_prompt, voice, model, language
        )
        mp3_parts.append(mp3_data)
        print(f"✓ {len(mp3_data):,} bytes MP3")

    # Concatenate MP3 chunks (MP3 frames are independently decodable)
    all_mp3 = b"".join(mp3_parts)

    with open(output_path, "wb") as f:
        f.write(all_mp3)

    size = os.path.getsize(output_path)
    print(f"\nOutput: {output_path} ({size:,} bytes)")
    return output_path


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Narrate text to audio using Google Cloud Text-to-Speech API"
    )
    parser.add_argument(
        "input",
        help="Input: file path, URL, or raw text"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output MP3 path (default: <input>_narrated.mp3)"
    )
    parser.add_argument(
        "-v", "--voice",
        default=DEFAULT_VOICE,
        help=f"Voice name (default: {DEFAULT_VOICE})"
    )
    parser.add_argument(
        "-m", "--model",
        default=DEFAULT_MODEL,
        help=f"TTS model (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Custom narration style prompt"
    )
    parser.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help=f"Language code (default: {DEFAULT_LANGUAGE})"
    )
    args = parser.parse_args()

    # Resolve source
    text = resolve_source(args.input)
    if not text.strip():
        print("ERROR: Input is empty", file=sys.stderr)
        sys.exit(1)

    # Determine output path
    if args.output:
        output = args.output
    elif os.path.exists(args.input):
        output = args.input.rsplit(".", 1)[0] + "_narrated.mp3"
    else:
        output = "narrated_output.mp3"

    narrate(text, output, args.voice, args.model, args.prompt, args.language)


if __name__ == "__main__":
    main()
