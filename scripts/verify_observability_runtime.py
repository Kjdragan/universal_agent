#!/usr/bin/env python3
"""Fail fast when the runtime cannot load real Logfire tracing."""

from __future__ import annotations

import argparse
from importlib import import_module, metadata
import json
import sys
from typing import Any


def _entry_points_for_group(group: str) -> list[metadata.EntryPoint]:
    entry_points = metadata.entry_points()
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=group))
    return list(entry_points.get(group, ()))


def collect_observability_runtime_state() -> dict[str, Any]:
    failures: list[str] = []
    logfire_module: Any | None = None

    try:
        import_module("opentelemetry.context")
    except BaseException as exc:  # pragma: no cover - deploy-time probe
        failures.append(f"opentelemetry.context import failed: {type(exc).__name__}: {exc}")

    entry_points = _entry_points_for_group("opentelemetry_context")
    entry_point_names = sorted(entry_point.name for entry_point in entry_points)
    if "contextvars_context" not in entry_point_names:
        failures.append(
            "opentelemetry_context entry point 'contextvars_context' is missing"
        )

    try:
        logfire_module = import_module("logfire")
    except BaseException as exc:  # pragma: no cover - deploy-time probe
        failures.append(f"logfire import failed: {type(exc).__name__}: {exc}")
    else:
        if getattr(logfire_module, "__ua_stub__", False):
            stub_reason = str(getattr(logfire_module, "__ua_stub_error__", "")).strip()
            detail = f": {stub_reason}" if stub_reason else ""
            failures.append(f"logfire resolved to Universal Agent fail-open stub{detail}")

    return {
        "ok": not failures,
        "failures": failures,
        "opentelemetry_context_entry_points": entry_point_names,
        "logfire_module": getattr(logfire_module, "__file__", None),
        "logfire_stub": bool(getattr(logfire_module, "__ua_stub__", False)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that real OpenTelemetry + Logfire imports work in the target venv.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = collect_observability_runtime_state()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        print("OBSERVABILITY_RUNTIME_OK")
    else:
        for failure in payload["failures"]:
            print(f"OBSERVABILITY_RUNTIME_FAIL {failure}", file=sys.stderr)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
