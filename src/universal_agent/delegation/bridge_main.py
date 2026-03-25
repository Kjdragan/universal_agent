"""Run the Redis→VP SQLite bridge as a standalone process.

Usage::

    python -m universal_agent.delegation.bridge_main
    python -m universal_agent.delegation.bridge_main --once
    python -m universal_agent.delegation.bridge_main --poll-seconds 3

The bridge:
  1. Connects to the Redis delegation bus
  2. Connects to the local VP SQLite runtime DB
  3. Starts two concurrent tasks:
     - Inbound bridge: Redis missions → VP SQLite ``vp_missions``
     - Outbound bridge: finalized VP missions → Redis results stream
  4. The existing ``VpWorkerLoop`` (running separately) picks up and
     executes the queued missions.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import signal
import socket
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

from universal_agent.delegation.redis_bus import (
    MISSION_CONSUMER_GROUP,
    MISSION_DLQ_STREAM,
    MISSION_STREAM,
    RedisMissionBus,
)
from universal_agent.delegation.heartbeat import FactoryHeartbeat, HeartbeatConfig
from universal_agent.delegation.redis_vp_bridge import BridgeConfig, RedisVpBridge
from universal_agent.delegation.redis_vp_result_bridge import RedisVpResultBridge
from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path, get_sqlite_busy_timeout_ms
from universal_agent.durable.migrations import ensure_schema
from universal_agent.runtime_role import resolve_machine_slug

logger = logging.getLogger(__name__)


def _redis_url_from_env() -> str:
    explicit = str(os.getenv("UA_REDIS_URL") or "").strip()
    if explicit:
        return explicit
    host = str(os.getenv("UA_REDIS_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(str(os.getenv("UA_REDIS_PORT") or "6379").strip() or 6379)
    password = str(os.getenv("REDIS_PASSWORD") or "").strip()
    db = int(str(os.getenv("UA_REDIS_DB") or "0").strip() or 0)
    if password:
        encoded = urllib.parse.quote(password, safe="")
        return f"redis://:{encoded}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


def _factory_id() -> str:
    return (
        str(os.getenv("UA_FACTORY_ID") or "").strip()
        or str(os.getenv("UA_MACHINE_SLUG") or "").strip()
        or str(os.getenv("INFISICAL_MACHINE_IDENTITY_NAME") or "").strip()
        or resolve_machine_slug()
    )


async def _run(*, once: bool, poll_seconds: float) -> int:
    # --- Redis bus ---
    stream_name = str(os.getenv("UA_DELEGATION_STREAM_NAME") or MISSION_STREAM).strip() or MISSION_STREAM
    consumer_group = str(os.getenv("UA_DELEGATION_CONSUMER_GROUP") or MISSION_CONSUMER_GROUP).strip() or MISSION_CONSUMER_GROUP
    dlq_stream = str(os.getenv("UA_DELEGATION_DLQ_STREAM") or MISSION_DLQ_STREAM).strip() or MISSION_DLQ_STREAM

    redis_url = _redis_url_from_env()
    bus = RedisMissionBus.from_url(
        redis_url,
        stream_name=stream_name,
        consumer_group=consumer_group,
        dlq_stream=dlq_stream,
    )
    bus.ensure_group()
    logger.info("Redis bus connected stream=%s group=%s", stream_name, consumer_group)

    # --- SQLite runtime DB ---
    db_path = get_runtime_db_path()
    conn = connect_runtime_db(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={get_sqlite_busy_timeout_ms()}")
    ensure_schema(conn)
    logger.info("Runtime DB connected: %s", db_path)

    # --- Bridge config ---
    factory_id = _factory_id()
    config = BridgeConfig(
        poll_seconds=poll_seconds,
        consumer_name=re.sub(r"[^A-Za-z0-9:_-]+", "-", f"bridge_{factory_id}"),
    )

    # --- Start bridges + heartbeat ---
    inbound = RedisVpBridge(bus, conn, config)
    outbound = RedisVpResultBridge(bus, conn, poll_seconds=poll_seconds)
    heartbeat_config = HeartbeatConfig.from_env()
    heartbeat = FactoryHeartbeat(heartbeat_config, paused_callback=lambda: inbound.paused)

    # Graceful shutdown on signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: _shutdown(inbound, outbound, heartbeat))

    if once:
        inserted = await inbound.run(once=True)
        outbound._tick()
        await heartbeat.send()
        logger.info("Bridge --once complete. inserted=%d", inserted)
        return inserted

    inbound_task = asyncio.create_task(inbound.run(), name="redis_vp_bridge_inbound")
    outbound_task = asyncio.create_task(outbound.run(), name="redis_vp_bridge_outbound")
    heartbeat_task = asyncio.create_task(heartbeat.run(), name="factory_heartbeat")

    done, pending = await asyncio.wait(
        {inbound_task, outbound_task, heartbeat_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    # If one exits, stop the others
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    conn.close()

    # If a system:update_factory mission triggered restart, exit cleanly
    # so systemd restarts the bridge with the new code.
    if inbound.restart_requested:
        logger.info("Bridge exiting for restart (system:update_factory)")
        return 0

    return 0


def _shutdown(
    inbound: RedisVpBridge,
    outbound: RedisVpResultBridge,
    heartbeat: FactoryHeartbeat,
) -> None:
    logger.info("Bridge shutdown requested")
    inbound.stop()
    outbound.stop()
    heartbeat.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Redis→VP SQLite bridge for cross-machine mission delegation.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch then exit (for scripted testing).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.getenv("UA_BRIDGE_POLL_SECONDS", "5") or 5),
        help="Polling interval in seconds (default: 5).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Load secrets from Infisical before anything reads env vars
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets
        result = initialize_runtime_secrets()
        logger.info("Secrets loaded: ok=%s source=%s", result.ok, result.source)
    except Exception as exc:
        logger.warning("Infisical init skipped: %s", exc)

    try:
        return asyncio.run(
            _run(once=args.once, poll_seconds=args.poll_seconds)
        )
    except KeyboardInterrupt:
        logger.info("Bridge interrupted.")
        return 0
    except Exception as exc:
        logger.exception("Bridge fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
