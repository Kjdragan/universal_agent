#!/usr/bin/env python3
"""AgentMail Triage Helper — CLI for the email triage agent.

Provides thread context, message details, and recent thread listing
using the AgentMail SDK. Called by the email-handler triage agent via
its Bash tool to enrich emails before producing triage briefs.

Usage:
    python scripts/agentmail_triage_helper.py thread-context <thread_id>
    python scripts/agentmail_triage_helper.py message-detail <message_id>
    python scripts/agentmail_triage_helper.py recent-threads [--limit N]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys


def _get_client():
    """Lazily create an async AgentMail client."""
    from agentmail import AsyncAgentMail

    api_key = os.environ.get("AGENTMAIL_API_KEY", "").strip()
    if not api_key:
        print(
            json.dumps({"error": "AGENTMAIL_API_KEY not set"}),
            file=sys.stderr,
        )
        sys.exit(1)
    return AsyncAgentMail(api_key=api_key)


def _inbox_id() -> str:
    return os.environ.get("UA_AGENTMAIL_INBOX_ADDRESS", "").strip()


async def thread_context(thread_id: str) -> dict:
    """Fetch thread messages and produce a context summary."""
    client = _get_client()
    inbox = _inbox_id()
    if not inbox:
        return {"error": "UA_AGENTMAIL_INBOX_ADDRESS not set"}

    try:
        # List messages in the inbox, filtering to this thread's messages
        messages_resp = await client.inboxes.messages.list(
            inbox_id=inbox,
        )
        all_msgs = getattr(messages_resp, "messages", []) or []

        # Filter to thread
        thread_msgs = [
            m for m in all_msgs
            if getattr(m, "thread_id", "") == thread_id
        ]

        context = {
            "thread_id": thread_id,
            "message_count": len(thread_msgs),
            "messages": [],
        }

        for msg in thread_msgs:
            context["messages"].append({
                "message_id": getattr(msg, "message_id", ""),
                "from": getattr(msg, "from_", ""),
                "subject": getattr(msg, "subject", ""),
                "text_preview": (getattr(msg, "text", "") or "")[:200],
                "created_at": str(getattr(msg, "created_at", "")),
                "labels": getattr(msg, "labels", []),
            })

        # Sort by creation time (oldest first)
        context["messages"].sort(key=lambda m: m.get("created_at", ""))

        return context
    except Exception as exc:
        return {"error": str(exc), "thread_id": thread_id}


async def message_detail(message_id: str) -> dict:
    """Fetch full message details."""
    client = _get_client()
    inbox = _inbox_id()
    if not inbox:
        return {"error": "UA_AGENTMAIL_INBOX_ADDRESS not set"}

    try:
        msg = await client.inboxes.messages.get(
            inbox_id=inbox,
            message_id=message_id,
        )
        return {
            "message_id": getattr(msg, "message_id", ""),
            "thread_id": getattr(msg, "thread_id", ""),
            "from": getattr(msg, "from_", ""),
            "to": getattr(msg, "to", ""),
            "subject": getattr(msg, "subject", ""),
            "text": getattr(msg, "text", ""),
            "html": (getattr(msg, "html", "") or "")[:500],
            "labels": getattr(msg, "labels", []),
            "attachments": [
                {
                    "filename": getattr(a, "filename", ""),
                    "content_type": getattr(a, "content_type", ""),
                    "size": getattr(a, "size", 0),
                }
                for a in (getattr(msg, "attachments", []) or [])
            ],
            "created_at": str(getattr(msg, "created_at", "")),
        }
    except Exception as exc:
        return {"error": str(exc), "message_id": message_id}


async def recent_threads(limit: int = 5) -> dict:
    """List recent threads with message counts."""
    client = _get_client()
    inbox = _inbox_id()
    if not inbox:
        return {"error": "UA_AGENTMAIL_INBOX_ADDRESS not set"}

    try:
        threads_resp = await client.inboxes.threads.list(inbox_id=inbox)
        all_threads = getattr(threads_resp, "threads", []) or []

        threads = []
        for t in all_threads[:limit]:
            threads.append({
                "thread_id": getattr(t, "thread_id", ""),
                "subject": getattr(t, "subject", ""),
                "message_count": getattr(t, "message_count", 0),
                "labels": getattr(t, "labels", []),
                "created_at": str(getattr(t, "created_at", "")),
            })

        return {"threads": threads, "total_available": len(all_threads)}
    except Exception as exc:
        return {"error": str(exc)}


def main():
    if len(sys.argv) < 2:
        print("Usage: agentmail_triage_helper.py <command> [args]")
        print("Commands: thread-context, message-detail, recent-threads")
        sys.exit(1)

    command = sys.argv[1]

    if command == "thread-context":
        if len(sys.argv) < 3:
            print("Usage: agentmail_triage_helper.py thread-context <thread_id>")
            sys.exit(1)
        result = asyncio.run(thread_context(sys.argv[2]))

    elif command == "message-detail":
        if len(sys.argv) < 3:
            print("Usage: agentmail_triage_helper.py message-detail <message_id>")
            sys.exit(1)
        result = asyncio.run(message_detail(sys.argv[2]))

    elif command == "recent-threads":
        limit = 5
        if "--limit" in sys.argv:
            idx = sys.argv.index("--limit")
            if idx + 1 < len(sys.argv):
                try:
                    limit = int(sys.argv[idx + 1])
                except ValueError:
                    pass
        result = asyncio.run(recent_threads(limit))

    else:
        print(f"Unknown command: {command}")
        print("Commands: thread-context, message-detail, recent-threads")
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
