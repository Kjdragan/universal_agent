#!/usr/bin/env python3
"""Emit a signed synthetic CSI event to UA ingest for smoke validation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
DEV_ROOT = Path(__file__).resolve().parents[1]
if str(DEV_ROOT) not in sys.path:
    sys.path.insert(0, str(DEV_ROOT))

from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.emitter.ua_client import UAEmitter


def _build_event(*, source: str, playlist_id: str, video_id: str) -> CreatorSignalEvent:
    event_type = "video_added_to_playlist" if source == "youtube_playlist" else "channel_new_upload"
    return CreatorSignalEvent(
        event_id=f"evt-smoke-{uuid.uuid4().hex[:10]}",
        dedupe_key=f"youtube:video:{video_id}:{playlist_id}",
        source=source,
        event_type=event_type,
        occurred_at="2026-02-22T00:00:00Z",
        received_at="2026-02-22T00:00:01Z",
        subject={
            "platform": "youtube",
            "video_id": video_id,
            "playlist_id": playlist_id,
            "channel_id": "UC_TEST",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": "CSI Synthetic Smoke Event",
            "published_at": "2026-02-22T00:00:00Z",
        },
        routing={"pipeline": "youtube_tutorial_explainer", "priority": "urgent", "tags": ["youtube", "smoke"]},
        metadata={"source_adapter": "csi_smoke_script"},
    )


async def _run(args: argparse.Namespace) -> int:
    endpoint = (args.endpoint or os.getenv("CSI_UA_ENDPOINT") or "").strip()
    secret = (args.secret or os.getenv("CSI_UA_SHARED_SECRET") or "").strip()
    instance_id = (args.instance_id or os.getenv("CSI_INSTANCE_ID") or "csi-smoke").strip()
    if not endpoint:
        print("SMOKE_FAIL missing endpoint (set --endpoint or CSI_UA_ENDPOINT)")
        return 2
    if not secret:
        print("SMOKE_FAIL missing secret (set --secret or CSI_UA_SHARED_SECRET)")
        return 2

    emitter = UAEmitter(endpoint=endpoint, shared_secret=secret, instance_id=instance_id)
    event = _build_event(source=args.source, playlist_id=args.playlist_id, video_id=args.video_id)
    delivered, status_code, payload = await emitter.emit_with_retries([event], max_attempts=max(1, args.max_attempts))

    print(f"SMOKE_STATUS delivered={int(delivered)} http_status={status_code}")
    print("SMOKE_RESPONSE", json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    if not delivered:
        return 1
    if args.require_internal_dispatch and int(payload.get("internal_dispatches") or 0) < 1:
        print("SMOKE_FAIL missing internal_dispatches in response")
        return 1
    if int(payload.get("accepted") or 0) < 1:
        print("SMOKE_FAIL accepted count < 1")
        return 1
    print("SMOKE_OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit signed CSI smoke event to UA ingest endpoint.")
    parser.add_argument("--endpoint", default="", help="UA ingest endpoint URL")
    parser.add_argument("--secret", default="", help="Shared secret for CSI->UA auth")
    parser.add_argument("--instance-id", default="", help="CSI instance id")
    parser.add_argument(
        "--source",
        default="youtube_playlist",
        choices=["youtube_playlist", "youtube_channel_rss"],
        help="Synthetic source type",
    )
    parser.add_argument("--playlist-id", default="PL_CSI_SMOKE", help="Synthetic playlist id")
    parser.add_argument("--video-id", default="dQw4w9WgXcQ", help="Synthetic video id")
    parser.add_argument("--max-attempts", type=int, default=3, help="Retry attempts")
    parser.add_argument(
        "--require-internal-dispatch",
        action="store_true",
        help="Fail unless UA response includes internal_dispatches >= 1",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

