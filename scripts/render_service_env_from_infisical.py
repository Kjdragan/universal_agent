#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from universal_agent.infisical_loader import initialize_runtime_secrets


def _parse_entry(raw: str) -> tuple[str, list[str]]:
    text = str(raw or "").strip()
    if "=" not in text:
        raise ValueError(f"Invalid --entry '{raw}'; expected OUT=SRC1,SRC2")
    out_key, sources_raw = text.split("=", 1)
    out_key = out_key.strip()
    sources = [part.strip() for part in sources_raw.split(",") if part.strip()]
    if not out_key or not sources:
        raise ValueError(f"Invalid --entry '{raw}'; expected OUT=SRC1,SRC2")
    return out_key, sources


def _render_lines(entries: list[tuple[str, list[str]]], allow_missing: bool) -> list[str]:
    lines: list[str] = []
    missing: list[str] = []
    for out_key, sources in entries:
        resolved = ""
        for src in sources:
            value = str(os.getenv(src) or "")
            if value:
                resolved = value
                break
        if not resolved and not allow_missing:
            missing.append(f"{out_key}<-{','.join(sources)}")
            continue
        lines.append(f"{out_key}={resolved}")
    if missing:
        raise RuntimeError(f"Missing required keys: {', '.join(missing)}")
    return lines


def _runtime_identity_entries() -> list[tuple[str, list[str]]]:
    return [
        ("INFISICAL_ENVIRONMENT", ["INFISICAL_ENVIRONMENT"]),
        ("UA_RUNTIME_STAGE", ["UA_RUNTIME_STAGE", "INFISICAL_ENVIRONMENT"]),
        ("FACTORY_ROLE", ["FACTORY_ROLE"]),
        ("UA_DEPLOYMENT_PROFILE", ["UA_DEPLOYMENT_PROFILE"]),
        ("UA_MACHINE_SLUG", ["UA_MACHINE_SLUG"]),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a service-specific env file from Infisical-backed runtime env.",
    )
    parser.add_argument("--output", required=True, help="Output env file path")
    parser.add_argument(
        "--entry",
        action="append",
        default=[],
        help="Mapping OUT=SRC1,SRC2. Uses first non-empty source value.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Optional deployment profile override for initialize_runtime_secrets",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Allow unresolved entries and write empty values instead of failing.",
    )
    parser.add_argument(
        "--include-runtime-identity",
        action="store_true",
        help="Also emit stage/bootstrap identity keys alongside requested entries.",
    )
    args = parser.parse_args()

    if not args.entry:
        raise SystemExit("At least one --entry is required")

    # Load Infisical values into process env (strict on VPS by profile policy).
    initialize_runtime_secrets(profile=args.profile, force_reload=True)

    entries = [_parse_entry(raw) for raw in args.entry]
    if args.include_runtime_identity:
        entries.extend(_runtime_identity_entries())
    lines = _render_lines(entries, allow_missing=bool(args.allow_missing))

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(output_path, 0o640)
    print(f"Wrote {len(lines)} entries to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
