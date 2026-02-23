#!/usr/bin/env python3
"""
Extract ALL channels from a YouTube playlist using YouTube Data API v3.

This version uses the YouTube Data API to fetch ALL videos from large playlists
(not limited to the ~15 videos that Atom feeds return).

Requirements: YOUTUBE_DATA_API_KEY in .env
"""

import asyncio
import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv
import httpx

# Load from UA project .env
env_path = Path(__file__).parent.parent / ".env"
if not env_path.exists():
    # Fallback to current directory
    env_path = Path(".env")
load_dotenv(env_path)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_DATA_API_KEY") or os.getenv("YOUTUBE_API_KEY")
PLAYLIST_ID = "PLjL3liQSixtvyu1yGb6IOwPUPMKeS067E"


@dataclass
class ChannelInfo:
    channel_id: str
    channel_name: str
    video_count: int
    rss_feed_url: str
    youtube_url: str


async def get_playlist_item_count(api_key: str, playlist_id: str) -> int:
    """Get the total number of videos in the playlist."""
    url = "https://www.googleapis.com/youtube/v3/playlists"
    params = {
        "part": "contentDetails",
        "id": playlist_id,
        "key": api_key,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("items"):
                return data["items"][0]["contentDetails"]["itemCount"]
        return 0


async def fetch_all_videos(api_key: str, playlist_id: str) -> List[Dict]:
    """
    Fetch ALL videos from a playlist using YouTube Data API v3.

    Handles pagination to get all videos, not just the first page.
    NOTE: The playlistItems API returns incorrect channel info (playlist owner instead of video owner).
    We fetch video IDs here, then get REAL channel info from the videos API.
    """
    videos = []
    next_page_token = None

    print(f"Fetching ALL videos from playlist: {playlist_id}")

    page = 0
    while True:
        page += 1
        print(f"  Fetching page {page}...", end="", flush=True)

        url = "https://www.googleapis.com/youtube/v3/playlistItems"
        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,  # Maximum per page
            "key": api_key,
        }

        if next_page_token:
            params["pageToken"] = next_page_token

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)

            if resp.status_code != 200:
                print(f"\n❌ Error fetching page {page}: {resp.status_code}")
                print(f"   Response: {resp.text[:200]}")
                break

            data = resp.json()

            # Extract video IDs from this page
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                content_details = item.get("contentDetails", {})

                video_id = content_details.get("videoId", "")
                if not video_id:
                    continue

                title = snippet.get("title", "")
                # NOTE: channelId from playlistItems is WRONG - it's the playlist owner
                # We'll get the real channel info from the videos API
                channel_id = snippet.get("channelId", "")
                channel_title = snippet.get("channelTitle", "")

                videos.append({
                    "video_id": video_id,
                    "title": title,
                    "temp_channel_id": channel_id,  # Will be replaced
                    "temp_channel_name": channel_title,  # Will be replaced
                })

            print(f" {len(data.get('items', []))} videos", end="", flush=True)

            # Check for next page
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

    print(f"\n  ✅ Total video IDs fetched: {len(videos)}")
    return videos


async def enrich_video_details(api_key: str, videos: List[Dict]) -> List[Dict]:
    """
    Fetch REAL video details to get the actual creator channel IDs.

    YouTube's playlistItems API returns the playlist owner's channel instead of
    the video creator's channel. We need to query the videos API to get the
    real channel information.
    """
    if not videos:
        return videos

    print(f"\n  Fetching REAL channel details for {len(videos)} videos...")

    # YouTube API allows up to 50 video IDs per request
    chunk_size = 50
    enriched_videos = []

    for i in range(0, len(videos), chunk_size):
        chunk = videos[i:i+chunk_size]
        video_ids = [v["video_id"] for v in chunk]

        print(f"    Batch {i//chunk_size + 1}/{(len(videos) + chunk_size - 1)//chunk_size}...",
              end="", flush=True)

        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            "part": "snippet",
            "id": ",".join(video_ids),
            "key": api_key,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)

            if resp.status_code != 200:
                print(f" Error: {resp.status_code}")
                # Keep original video data if enrichment fails
                enriched_videos.extend(chunk)
                continue

            data = resp.json()

            # Create a map of video_id -> snippet
            video_details = {
                item["id"]: item.get("snippet", {})
                for item in data.get("items", [])
            }

            # Enrich each video with real channel info
            for video in chunk:
                vid = video["video_id"]
                snippet = video_details.get(vid, {})

                if snippet:
                    enriched_videos.append({
                        "video_id": vid,
                        "title": snippet.get("title", video["title"]),
                        "channel_id": snippet.get("channelId", ""),
                        "channel_name": snippet.get("channelTitle", ""),
                    })
                else:
                    # Fallback to original if not found (private/deleted video)
                    enriched_videos.append({
                        "video_id": vid,
                        "title": video["title"],
                        "channel_id": video.get("temp_channel_id", ""),
                        "channel_name": video.get("temp_channel_name", "Unknown"),
                    })

        print(f" ✓", end="", flush=True)

    print(f"\n  ✅ Enriched {len(enriched_videos)} videos with real channel data")
    return enriched_videos


async def get_channel_details(api_key: str, channel_ids: List[str]) -> Dict[str, Dict]:
    """Get detailed information for multiple channels."""
    if not channel_ids:
        return {}

    # YouTube API allows up to 50 channels per request
    chunk_size = 50
    channels = {}

    for i in range(0, len(channel_ids), chunk_size):
        chunk = channel_ids[i:i+chunk_size]
        print(f"  Fetching channel details for {len(chunk)} channels...", end="", flush=True)

        url = "https://www.googleapis.com/youtube/v3/channels"
        params = {
            "part": "snippet",
            "id": ",".join(chunk),
            "key": api_key,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)

            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", []):
                    channel_id = item["id"]
                    snippet = item.get("snippet", {})
                    channels[channel_id] = {
                        "channel_id": channel_id,
                        "title": snippet.get("title", ""),
                        "description": snippet.get("description", "")[:500],  # Truncate for storage
                        "custom_url": snippet.get("customUrl", ""),
                    }
            else:
                print(f"  Error: {resp.status_code}")

        print(f" ✓", end="", flush=True)

    print()
    return channels


def extract_channels(videos: List[Dict]) -> Dict[str, ChannelInfo]:
    """Extract unique channel info from video list."""
    channel_videos: Dict[str, List[Dict]] = {}

    for video in videos:
        channel_id = video["channel_id"]
        if channel_id not in channel_videos:
            channel_videos[channel_id] = []
        channel_videos[channel_id].append(video)

    channels = {}
    for channel_id, channel_video_list in channel_videos.items():
        channels[channel_id] = ChannelInfo(
            channel_id=channel_id,
            channel_name=channel_video_list[0]["channel_name"],
            video_count=len(channel_video_list),
            rss_feed_url=f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            youtube_url=f"https://www.youtube.com/channel/{channel_id}"
        )

    return channels


def save_results(channels: Dict[str, ChannelInfo], videos: List[Dict], output_prefix: str = "channels_watchlist"):
    """Save results to JSON and CSV files."""

    # Sort by video count (descending)
    sorted_channels = sorted(channels.values(), key=lambda c: c.video_count, reverse=True)

    # Save as JSON
    json_data = {
        "extraction_date": "2026-02-22",
        "playlist_id": PLAYLIST_ID,
        "total_videos_found": len(videos),
        "unique_channels": len(channels),
        "channels": [
            {
                "channel_id": ch.channel_id,
                "channel_name": ch.channel_name,
                "video_count": ch.video_count,
                "rss_feed_url": ch.rss_feed_url,
                "youtube_url": ch.youtube_url
            }
            for ch in sorted_channels
        ]
    }

    json_path = f"{output_prefix}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Saved JSON: {json_path}")

    # Save as CSV
    csv_path = f"{output_prefix}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Channel ID", "Channel Name", "Video Count", "RSS Feed URL", "YouTube URL"])

        for ch in sorted_channels:
            writer.writerow([
                ch.channel_id,
                ch.channel_name,
                ch.video_count,
                ch.rss_feed_url,
                ch.youtube_url
            ])
    print(f"✅ Saved CSV: {csv_path}")

    # Save video list for reference
    videos_json_path = f"{output_prefix}_videos.json"
    with open(videos_json_path, "w", encoding="utf-8") as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)
    print(f"✅ Saved video list: {videos_json_path}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique channels: {len(channels)}")
    print(f"Total videos in playlist: {len(videos)}")
    print(f"\nTop 15 channels by video count:")
    for i, ch in enumerate(sorted_channels[:15], 1):
        print(f"  {i:2}. {ch.channel_name}")
        print(f"      Channel ID: {ch.channel_id}")
        print(f"      Videos: {ch.video_count}")
        print(f"      RSS: {ch.rss_feed_url[:60]}...")

    if len(sorted_channels) > 15:
        print(f"  ... and {len(sorted_channels) - 15} more channels")

    print(f"\n{'='*60}")
    print(f"For CSI Integration:")
    print(f"{'='*60}")
    print(f"Option 1 - Use RSS feeds (recommended):")
    print(f"  Each channel's RSS feed URL is in the JSON/CSV files")
    print(f"  Poll with: scripts/youtube_playlist_poll_to_manual_hook.py")
    print(f"\nOption 2 - Create Composio triggers:")
    print(f"  See Document 75 for Composio trigger setup")
    print(f"{'='*60}")


async def main():
    if not YOUTUBE_API_KEY or YOUTUBE_API_KEY == "YOUR_YOUTUBE_API_KEY_HERE":
        print("❌ YOUTUBE_DATA_API_KEY not configured in .env")
        print("   Please add your YouTube Data API key to .env:")
        print("   YOUTUBE_DATA_API_KEY=your_api_key_here")
        return

    print("="*60)
    print("YouTube Playlist Channel Extractor (Full API Version)")
    print("="*60)
    print(f"Playlist ID: {PLAYLIST_ID}")
    print(f"API Key: {YOUTUBE_API_KEY[:20]}...")

    # First, get the expected video count
    print("\nStep 1: Checking playlist size...")
    expected_count = await get_playlist_item_count(YOUTUBE_API_KEY, PLAYLIST_ID)
    print(f"  Expected videos in playlist: {expected_count}")

    # Fetch all videos (just IDs and titles initially)
    print("\nStep 2: Fetching video IDs from playlist...")
    videos = await fetch_all_videos(YOUTUBE_API_KEY, PLAYLIST_ID)

    if not videos:
        print("❌ No videos found in playlist")
        return

    # Enrich with REAL channel details (fix YouTube API bug)
    print("\nStep 2.5: Getting REAL creator channel IDs...")
    videos = await enrich_video_details(YOUTUBE_API_KEY, videos)

    # Extract unique channels
    print("\nStep 3: Extracting unique channels...")
    channels = extract_channels(videos)

    # Save results
    print("\nStep 4: Saving results...")
    save_results(channels, videos)

    print("\n✅ Done!")


if __name__ == "__main__":
    asyncio.run(main())
