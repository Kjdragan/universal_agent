#!/usr/bin/env python3
"""Seed CSI dedicated auth vars from shared UA auth vars in an env file.

This helper enables fast validation of CSI_LLM_AUTH_MODE=1 without changing
provider wiring. It copies shared keys into CSI_* equivalents.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MAPPINGS: list[tuple[str, str]] = [
    ("ANTHROPIC_API_KEY", "CSI_ANTHROPIC_API_KEY"),
    ("ANTHROPIC_AUTH_TOKEN", "CSI_ANTHROPIC_AUTH_TOKEN"),
    ("ANTHROPIC_BASE_URL", "CSI_ANTHROPIC_BASE_URL"),
    ("ZAI_API_KEY", "CSI_ZAI_API_KEY"),
    ("ZAI_BASE_URL", "CSI_ZAI_BASE_URL"),
]


def _parse_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    parsed: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip()
    return lines, parsed


def _upsert_line(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default="/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env",
        help="Target env file to modify in-place.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing CSI_* values. Default keeps non-empty CSI_* values unchanged.",
    )
    parser.add_argument(
        "--set-mode-1",
        action="store_true",
        help="Also set CSI_LLM_AUTH_MODE=1 after seeding.",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser()
    if not env_path.exists():
        raise SystemExit(f"Env file not found: {env_path}")

    lines, parsed = _parse_env(env_path)
    applied: list[str] = []
    skipped: list[str] = []

    for shared_key, dedicated_key in MAPPINGS:
        shared_val = str(parsed.get(shared_key, "")).strip()
        current_dedicated = str(parsed.get(dedicated_key, "")).strip()
        if not shared_val:
            skipped.append(f"{dedicated_key} (shared source {shared_key} missing)")
            continue
        if current_dedicated and not args.overwrite:
            skipped.append(f"{dedicated_key} (already set)")
            continue
        lines = _upsert_line(lines, dedicated_key, shared_val)
        parsed[dedicated_key] = shared_val
        applied.append(dedicated_key)

    if args.set_mode_1:
        lines = _upsert_line(lines, "CSI_LLM_AUTH_MODE", "1")
        applied.append("CSI_LLM_AUTH_MODE")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Updated env file: {env_path}")
    if applied:
        print("Applied:")
        for item in applied:
            print(f"- {item}")
    if skipped:
        print("Skipped:")
        for item in skipped:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
