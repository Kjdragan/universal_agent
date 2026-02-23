#!/usr/bin/env python3
"""
Extract unique channel IDs from a YouTube playlist.

Creates a watchlist of creators from the playlist with:
- Channel ID
- Channel name
- Video count in playlist
- RSS feed URL for the channel

Output: channels_watchlist.json and channels_watchlist.csv
"""

import asyncio
import csv
import json
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, asdict
from xml.etree import ElementTree as ET
from typing import List, Dict, Set

ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

PLAYLIST_URL = "https://www.youtube.com/watch?v=tFaCR2a99hw&list=PLjL3liQSixtvyu1yGb6IOwPUPMKeS067E"


@dataclass
class ChannelInfo:
    channel_id: str
    channel_name: str
    video_count: int
    rss_feed_url: str

    def __hash__(self):
        return hash(self.channel_id)


def _safe_text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def extract_playlist_id(url: str) -> str:
    """Extract playlist ID from YouTube URL."""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    playlist_id = params.get('list', [''])[0]
    return playlist_id


def fetch_playlist_videos(playlist_id: str) -> List[Dict]:
    """
    Fetch all videos from a playlist using YouTube's Atom feed.

    Handles pagination automatically by following 'next' links.
    """
    videos = []
    next_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={urllib.parse.quote(playlist_id)}"

    print(f"Fetching videos from playlist: {playlist_id}")
    print(f"Starting URL: {next_url}")

    page_count = 0
    while next_url:
        page_count += 1
        print(f"  Fetching page {page_count}...")

        req = urllib.request.Request(
            next_url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; CSI-PlaylistExtractor/1.0)",
                "Accept": "application/atom+xml,application/xml,text/xml",
            }
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()

        root = ET.fromstring(raw)

        # Extract video entries from this page
        for entry in root.findall("a:entry", ATOM_NS):
            video_id = _safe_text(entry.find("yt:videoId", ATOM_NS))
            if not video_id:
                # Fallback from atom id form: "yt:video:<id>"
                atom_id = _safe_text(entry.find("a:id", ATOM_NS))
                if atom_id and ":" in atom_id:
                    video_id = atom_id.rsplit(":", 1)[-1]

            if not video_id:
                continue

            # Extract channel info
            author_name = _safe_text(entry.find("a:author/a:name", ATOM_NS))
            author_uri = _safe_text(entry.find("a:author/a:uri", ATOM_NS))

            channel_id = ""
            if "/channel/" in author_uri:
                channel_id = author_uri.rsplit("/channel/", 1)[-1].strip()
            elif "/user/" in author_uri:
                # For user-based channels, we'll need to handle differently
                username = author_uri.rsplit("/user/", 1)[-1].strip()
                # User channels don't have stable channel IDs in the feed
                # We'll mark them for special handling
                channel_id = f"user:{username}"

            video_title = _safe_text(entry.find("a:title", ATOM_NS))
            published = _safe_text(entry.find("a:published", ATOM_NS))

            videos.append({
                "video_id": video_id,
                "title": video_title,
                "channel_id": channel_id,
                "channel_name": author_name or "Unknown",
                "published": published,
            })

        # Find next page link
        next_link = root.find("a:link[@rel='next']", ATOM_NS)
        if next_link is not None:
            next_url = next_link.attrib.get("href", "")
            if not next_url:
                break
        else:
            break

    print(f"  Total videos found: {len(videos)}")
    return videos


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
        # Get RSS feed URL
        if channel_id.startswith("user:"):
            # User channels have a different RSS format
            username = channel_id.replace("user:", "")
            rss_url = f"https://www.youtube.com/feeds/videos.xml?user={username}"
        else:
            # Channel-based RSS
            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

        # Use the name from the first video (most consistent)
        channel_name = channel_video_list[0]["channel_name"]

        channels[channel_id] = ChannelInfo(
            channel_id=channel_id,
            channel_name=channel_name,
            video_count=len(channel_video_list),
            rss_feed_url=rss_url
        )

    return channels


def save_results(channels: Dict[str, ChannelInfo], output_prefix: str = "channels_watchlist"):
    """Save results to JSON and CSV files."""

    # Sort by video count (descending)
    sorted_channels = sorted(channels.values(), key=lambda c: c.video_count, reverse=True)

    # Save as JSON
    json_data = [
        {
            "channel_id": ch.channel_id,
            "channel_name": ch.channel_name,
            "video_count": ch.video_count,
            "rss_feed_url": ch.rss_feed_url,
            "youtube_url": f"https://www.youtube.com/channel/{ch.channel_id}" if not ch.channel_id.startswith("user:") else f"https://www.youtube.com/user/{ch.channel_id.replace('user:', '')}"
        }
        for ch in sorted_channels
    ]

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
            youtube_url = f"https://www.youtube.com/channel/{ch.channel_id}" if not ch.channel_id.startswith("user:") else f"https://www.youtube.com/user/{ch.channel_id.replace('user:', '')}"
            writer.writerow([
                ch.channel_id,
                ch.channel_name,
                ch.video_count,
                ch.rss_feed_url,
                youtube_url
            ])
    print(f"✅ Saved CSV: {csv_path}")

    # Print summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique channels: {len(channels)}")
    print(f"Total videos in playlist: {sum(ch.video_count for ch in channels.values())}")
    print(f"\nTop 10 channels by video count:")
    for i, ch in enumerate(sorted_channels[:10], 1):
        print(f"  {i:2}. {ch.channel_name} ({ch.channel_id})")
        print(f"      {ch.video_count} videos | {ch.rss_feed_url}")

    if len(sorted_channels) > 10:
        print(f"  ... and {len(sorted_channels) - 10} more channels")

    print(f"\n{'='*60}")
    print(f"For CSI Integration:")
    print(f"{'='*60}")
    print(f"Use the RSS feed URLs with your YouTube poller:")
    print(f"  scripts/youtube_playlist_poll_to_manual_hook.py")
    print(f"\nOr add individual channels to Composio triggers.")
    print(f"{'='*60}")


def main():
    print("="*60)
    print("YouTube Playlist Channel Extractor")
    print("="*60)
    print(f"Playlist URL: {PLAYLIST_URL}")

    # Extract playlist ID
    playlist_id = extract_playlist_id(PLAYLIST_URL)
    print(f"Playlist ID: {playlist_id}")

    # Fetch all videos from playlist
    videos = fetch_playlist_videos(playlist_id)

    if not videos:
        print("❌ No videos found in playlist")
        return

    # Extract unique channels
    channels = extract_channels(videos)

    # Save results
    save_results(channels)

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
