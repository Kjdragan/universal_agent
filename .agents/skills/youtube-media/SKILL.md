---
name: youtube-media
description: Download raw audio or video binary payloads natively from YouTube, explicitly bypassing all residential proxies, via the PoT provider on the VPS. Use when asked to download, fetch, or grab youtube audio/video streams to local files.
---

# YouTube Media Downloader

This skill enables you (Simone) to fetch raw YouTube audio/video payloads natively to the VPS, safely bypassing the residential proxy mechanisms to avoid massive gigabyte billing charges.

**CRITICAL RULES:**
1. **NEVER use this skill for transcripts, playlists, or metadata scraping.** Those operations must continue to flow through the standard residential proxy workflows via `youtube_ingest.py` API endpoints.
2. **USE THIS ONLY for media binary downloads** (e.g. "grab the audio for this YT video", "download this youtube video to a mp4").
3. **DO NOT pass proxies to this script.** It natively prevents environment variables from loading proxy tunnels, ensuring the heavy multi-megabyte payload rides the free VPS datacenter IP via the *Proof-of-Origin* token (PoT) bypass.

## Usage

You can invoke the `fetch_youtube_media.py` script provided with this skill via `uv run`:

```bash
uv run .agents/skills/youtube-media/scripts/fetch_youtube_media.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --format audio --out-dir /tmp
```

### Options
- `URL`: The valid YouTube URL.
- `--format`: Either `audio` (default, downloads best .m4a) or `video` (downloads best .mp4 combined stream).
- `--out-dir`: The directory to place the downloaded file. Defaults to your current working directory.

### Example Walkthrough
**User:** "Simone, please download the audio from the YouTube video https://youtube.com/watch?v=example and stick it in /tmp for me to review."
**Simone Action:** 
`uv run .agents/skills/youtube-media/scripts/fetch_youtube_media.py "https://youtube.com/watch?v=example" --format audio --out-dir /tmp`

The script will handle wiping any local proxy-related environment variables securely before invoking the `yt-dlp` download subprocess. Output path of the successful file download will be printed.
