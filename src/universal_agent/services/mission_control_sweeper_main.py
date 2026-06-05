"""Standalone entrypoint for the Mission Control intelligence sweeper.

Phase B of the S5 scheduling-substrate migration (ADR
``project_docs/06_platform/08_scheduling_substrate_adr.md``, Decision 2).

The sweeper loop (``run_sweeper_loop``) used to run as an ``asyncio`` task inside
the gateway FastAPI lifespan, where it (a) shared the gateway event loop and
could starve / be starved by the heartbeat + in-process cron, and (b) died on
every deploy restart. This module runs the *unchanged* loop as its own
long-lived process (``universal-agent-mission-control-sweeper.service``) so it
is fully isolated from the gateway.

Responsibilities (kept deliberately tiny — this is a launcher, not new logic):

1. Bootstrap Infisical runtime secrets FIRST, before importing/constructing the
   sweeper, so the tier-1/tier-2 LLM lane has its ``ANTHROPIC_*`` / ZAI keys in
   ``os.environ``. Mirrors the pattern in ``api/server.py`` and
   ``vp/worker_main.py``. Skipping this is the #1 trap: the loop would run but
   every LLM card/readout would silently fail.
2. Drive ``run_sweeper_loop`` until SIGTERM/SIGINT (systemd stop), then exit
   cleanly (0) — the loop exits the moment its ``stop_event`` is set.

The cadence clock is durable: ``run_sweeper_loop`` reads/writes the
``__tier1_meta__`` / ``__tier2_meta__`` sentinel rows in
``mission_control_intelligence.db``, so restarting this process (e.g. on a code
deploy) does NOT reset the floor/ceiling timers — the win is *process
isolation*, not deploy-immunity. The DB paths resolve from the same
``AGENT_RUN_WORKSPACES`` root the gateway uses (the systemd unit shares the
gateway's ``WorkingDirectory`` and ``EnvironmentFile``), so the relocated loop
shares state with prior gateway runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

logger = logging.getLogger(__name__)


async def _run() -> None:
    # (i) Bootstrap runtime secrets from Infisical FIRST — before the sweeper
    # loop spins up the tier-1/tier-2 LLM passes that need ANTHROPIC_*/ZAI keys
    # in os.environ. Idempotent + try/except, same shape as api/server.py and
    # vp/worker_main.py.
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        initialize_runtime_secrets()
        logger.info("Infisical runtime secrets loaded for mission_control sweeper")
    except Exception as exc:  # noqa: BLE001 — never block startup on bootstrap
        logger.warning("Infisical secret bootstrap skipped: %s", exc)

    # (ii) The stop signal — set by SIGTERM/SIGINT for a graceful exit. Created
    # (and its handlers installed) before any blocking wait so even the
    # disabled-phase idle below stays interruptible by a systemd stop.
    stop_event = asyncio.Event()

    # (iii) Install signal handlers on the running loop so a clean shutdown
    # sets the event (no traceback, exit 0). Must run inside the loop, so it
    # uses get_running_loop() rather than firing before asyncio.run(). Signal
    # handling is best-effort (systemd will SIGKILL after its stop timeout
    # regardless), so a platform/thread that rejects it is non-fatal.
    loop = asyncio.get_running_loop()
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(_sig, stop_event.set)
        except (NotImplementedError, RuntimeError, ValueError):
            # Unavailable on some platforms (Windows) or off the main thread.
            logger.debug("add_signal_handler unsupported for %s", _sig)

    # (iv) Phase gate — mirror the old gateway-lifespan start-guard. When
    # UA_MC_PHASE_1_ENABLED is unset, tick() short-circuits on every pass, so
    # don't spin the loop at all; stay alive but idle until systemd stops us.
    # The flag is Infisical-injected, so this is read AFTER the bootstrap.
    from universal_agent.services.mission_control_db import is_phase_enabled

    if not is_phase_enabled(1):
        logger.info(
            "⏸️  Mission Control sweeper disabled (UA_MC_PHASE_1_ENABLED unset); "
            "idling until stop"
        )
        await stop_event.wait()
        return

    # (v) Imported AFTER secrets bootstrap + phase gate so any import-time env
    # reads see the loaded values.
    from universal_agent.services.mission_control_intelligence_sweeper import (
        run_sweeper_loop,
    )

    logger.info("🛰️  Mission Control sweeper standalone entrypoint starting")
    # (vi) Drive the loop until stop_event is set. run_sweeper_loop builds the
    # process-wide sweeper (config from env, interval default 60s) and offloads
    # the heavy sync tick() via asyncio.to_thread; logic is unchanged.
    await run_sweeper_loop(stop_event)
    logger.info("🛰️  Mission Control sweeper standalone entrypoint exiting cleanly")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("UA_MISSION_CONTROL_SWEEPER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
