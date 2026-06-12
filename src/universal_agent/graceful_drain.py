"""Bounded in-flight drain for graceful shutdown (deploy-restart resilience C2).

A small, dependency-free helper that lets a caller *wait* for an in-flight
``asyncio`` unit of work to finish within a bounded budget instead of cancelling
it immediately. It is the mechanism behind Phase C2 of the deploy-restart
resilience ADR (``project_docs/06_platform/12_deploy_restart_resilience_adr.md``):
on gateway shutdown we want an in-flight Simone heartbeat iteration to *complete*
(or checkpoint) rather than be SIGTERM-cancelled mid-flight (harm **H2**).

Design contract (deliberately narrow so it is trivially testable and reusable):

* It **awaits** the in-flight awaitable up to ``timeout`` seconds.
* On timeout it does **NOT** cancel the work — it returns ``TIMED_OUT`` and lets
  the *caller* decide. This keeps the helper side-effect-light and lets the
  heartbeat reuse its existing outer-task cancel path for the fallback.
* If there is nothing in flight (``None`` or already-done future) it returns
  immediately.
* An exception raised by the in-flight work counts as ``DRAINED`` (the work
  finished — its own ``finally`` already ran); the exception is swallowed here
  because shutdown drain is not the place to surface iteration errors.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import time
from typing import Awaitable, Optional


class DrainResult(str, Enum):
    """Outcome of a bounded drain attempt."""

    NOTHING_IN_FLIGHT = "nothing_in_flight"
    DRAINED = "drained"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class DrainOutcome:
    result: DrainResult
    waited_seconds: float


async def drain_inflight(
    inflight: Optional[Awaitable],
    *,
    timeout: float,
) -> DrainOutcome:
    """Await ``inflight`` up to ``timeout`` seconds without cancelling it on timeout.

    Returns a :class:`DrainOutcome` describing whether there was anything to
    drain, whether it finished in time, or whether the budget elapsed first.
    """
    if inflight is None:
        return DrainOutcome(DrainResult.NOTHING_IN_FLIGHT, 0.0)

    fut = asyncio.ensure_future(inflight)
    if fut.done():
        # Retrieve any exception so it isn't reported as "never retrieved".
        _swallow(fut)
        return DrainOutcome(DrainResult.NOTHING_IN_FLIGHT, 0.0)

    started = time.monotonic()
    try:
        # shield() so a wait_for timeout cancels only the wrapper, never the
        # underlying work — the caller owns the decision to cancel.
        await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
    except asyncio.TimeoutError:
        return DrainOutcome(DrainResult.TIMED_OUT, time.monotonic() - started)
    except Exception:
        # The work finished by raising — that's still "drained" for shutdown.
        pass
    return DrainOutcome(DrainResult.DRAINED, time.monotonic() - started)


def _swallow(fut: "asyncio.Future") -> None:
    try:
        fut.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        pass
