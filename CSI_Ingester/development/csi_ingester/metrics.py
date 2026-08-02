"""Very small in-memory metrics helpers for v1."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class MetricsRegistry:
    counters: Counter[str] = field(default_factory=Counter)
    gauges: dict[str, float] = field(default_factory=dict)

    def inc(self, name: str, value: int = 1) -> None:
        self.counters[name] += max(0, int(value))

    def set_gauge(self, name: str, value: float) -> None:
        """Set (overwrite) a point-in-time gauge value, e.g. a computed
        "seconds since X" reading. Unlike counters, gauges can go down."""
        self.gauges[name] = float(value)

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for key in sorted(self.counters):
            metric = key.replace(".", "_")
            lines.append(f"# TYPE {metric} counter")
            lines.append(f"{metric} {self.counters[key]}")
        for key in sorted(self.gauges):
            metric = key.replace(".", "_")
            lines.append(f"# TYPE {metric} gauge")
            lines.append(f"{metric} {self.gauges[key]}")
        return "\n".join(lines) + ("\n" if lines else "")

