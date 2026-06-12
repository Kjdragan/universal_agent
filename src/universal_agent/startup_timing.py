"""Measure where the gateway lifespan spends its blocking pre-yield time.

C1 (measure-first) of the deploy-restart resilience ADR
(project_docs/06_platform/12_deploy_restart_resilience_adr.md): before any risky
reorder of `gateway_server.py::lifespan`, instrument the blocking startup
segments so a real deploy reveals the true cold-start cost and which segment
dominates. Pure timing/logging — no behavior change.
"""

from __future__ import annotations

import time


class StartupPhaseTimer:
    """Records wall-clock durations of named startup segments via `mark()`.

    Each `mark(name)` closes the segment since the previous mark (or since
    construction) and labels it. `summary()` reports total pre-yield time and
    the segments ranked slowest-first. The clock is injectable for tests.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._t0 = clock()
        self._last = self._t0
        self._marks: list[tuple[str, float, float]] = []

    def mark(self, name) -> float:
        now = self._clock()
        segment = now - self._last
        self._marks.append((str(name), segment, now - self._t0))
        self._last = now
        return segment

    def total_seconds(self) -> float:
        return self._clock() - self._t0

    def summary(self, top: int | None = None) -> str:
        total = self.total_seconds()
        ranked = sorted(self._marks, key=lambda m: m[1], reverse=True)
        if top is not None:
            ranked = ranked[:top]
        parts = ", ".join(f"{name}=+{seg:.2f}s" for name, seg, _ in ranked) or "(no marks)"
        return f"gateway lifespan pre-yield {total:.2f}s; slowest segments: {parts}"
