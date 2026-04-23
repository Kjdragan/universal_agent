---
name: gemini-tts-narrator-tf
description: Narrate text into a high-quality MP3 audiobook using Google Cloud Text-to-Speech API with Gemini TTS models. Accepts local file paths (desktop or VPS), URLs, or raw text. Produces native MP3 with warm narration style, smart paragraph chunking with pause markup, and multi-model support. Trigger on "narrate", "read aloud", "audiobook", "TTS this story", "convert to audio", "read this to me", or "narrator".
---

# Gemini TTS Narrator

## Goal
Convert text into a high-quality narrated MP3 audio file using Google Cloud Text-to-Speech API with Gemini TTS models.

## Success Criteria
- Input: local file path (.txt, .md), URL, or inline text
- Output: single MP3 file saved to `work_products/` or specified path
- Audio is clear, naturally paced, and covers the full input text
- Long texts are chunked with `[medium pause]` markup between paragraphs

## Context

> **Canonical reference**: `docs/03_Operations/123_Gemini_TTS_Source_Of_Truth_2026-04-22.md`

- **API**: Google Cloud Text-to-Speech API (`texttospeech.googleapis.com`)
- **NOT** AI Studio (`generativelanguage.googleapis.com`) — see doc 123 for why
- **Auth**: Service account key at `/opt/universal_agent/.gcp-tts-sa-key.json` (VPS) or gcloud ADC (desktop)
- **Default model**: `gemini-3.1-flash-tts-preview` (newest, best controllability, requires `global` endpoint)
- **Fallback models**: `gemini-2.5-flash-tts` (GA, fast), `gemini-2.5-pro-tts` (highest quality)
- **Voices**: Aoede (female, warm/narrative, default), Charon (male, deep), Kore, Leda, etc. (30 total)
- **Output**: Native MP3 — **no ffmpeg conversion needed**
- **Limits**: 4000 bytes text + 4000 bytes prompt per request (separate fields)
- **Chunking**: Texts >3800 bytes split at paragraph then sentence boundaries
- **GCP Project**: `gen-lang-client-0229532959`

## Execution Steps

1. **Read input**: Resolve the source — file path, URL, or raw text.
   - Desktop paths like `/home/kjdragan/lrepos/universal_agent/...` are automatically mapped to VPS paths `/opt/universal_agent/...`
2. **Validate**: Confirm the source exists and is readable. Block if missing.
3. **Run narrate_gemini.py**:
   ```bash
   GOOGLE_APPLICATION_CREDENTIALS=/opt/universal_agent/.gcp-tts-sa-key.json \
   uv run scripts/narrate_gemini.py <input> -o <output.mp3> -v Aoede
   ```
4. **Fallback**: If Cloud TTS fails, try with `gemini-2.5-flash-tts` model:
   ```bash
   uv run scripts/narrate_gemini.py <input> -o <output.mp3> -m gemini-2.5-flash-tts
   ```
5. **Deliver**: Email via agentmail or save to work_products as needed.

## Script Options (narrate_gemini.py)
```
narrate_gemini.py <input> [-o OUTPUT] [-v VOICE] [-m MODEL] [--prompt PROMPT] [--language LANG]
  input          File path, URL, or raw text
  -o, --output   Output MP3 path (default: <input>_narrated.mp3)
  -v, --voice    Voice name (default: Aoede)
  -m, --model    TTS model (default: gemini-3.1-flash-tts-preview)
  --prompt       Custom narration style prompt
  --language     Language code (default: en-US)
```

## Available Models
| Model | Type | Best For |
|-------|------|----------|
| `gemini-3.1-flash-tts-preview` | Preview | **Default.** Best controllability, multi-speaker |
| `gemini-2.5-flash-tts` | GA | Fast, cheap, reliable |
| `gemini-2.5-pro-tts` | GA | Highest quality for audiobooks |
| `gemini-2.5-flash-lite-preview-tts` | Preview | Ultra cost-efficient |

## Markup Tags (auto-inserted)
The script auto-inserts `[medium pause]` between paragraphs. For manual control:
- `[sigh]`, `[laughing]`, `[uhm]` — non-speech sounds
- `[whispering]`, `[shouting]`, `[sarcasm]` — style modifiers
- `[short pause]`, `[medium pause]`, `[long pause]` — pacing

## Constraints
- If the source file is missing, BLOCK the task immediately with the exact path needed.
- Each chunk must stay under ~3800 UTF-8 bytes (API limit is 4000 bytes per field).
- Output is native MP3 — no intermediate WAV or ffmpeg conversion.
- Auth requires service account key or gcloud ADC. **Do NOT use GEMINI_IMAGE_API_KEY.**

## Dependencies
- `google-cloud-texttospeech>=2.29.0` (via PEP 723 inline deps)
- `gcloud` CLI (for auth token generation)
- No ffmpeg required (native MP3 output)
