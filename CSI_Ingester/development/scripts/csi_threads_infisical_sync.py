#!/usr/bin/env python3
"""Upsert Threads secrets into Infisical from a JSON payload.

The write-back is routed through the project Infisical primitive
``universal_agent.infisical_loader.upsert_infisical_secret`` (universal-auth
machine identity; environment/path resolved from the runtime environment the
same way the read path ``_fetch_infisical_secrets`` resolves them). This is the
one true Infisical write path in UA — we deliberately do NOT use the raw
``infisical_client`` SDK here, which is not installed in the CSI venv and would
add a second, divergent auth path.

Expected payload shape:
{
  "THREADS_APP_ID": "...",
  "THREADS_APP_SECRET": "...",
  "THREADS_USER_ID": "...",
  "THREADS_ACCESS_TOKEN": "...",
  "THREADS_TOKEN_EXPIRES_AT": "..."
}

Connection settings (project id, environment, secret path, machine-identity
creds) are resolved internally by the loader from the process environment
(``INFISICAL_*`` vars). They are NOT command-line arguments here; the loader
already mirrors the resolution used by the refresh read path, so writes land at
the exact env/path the secrets were read from.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

# Make ``universal_agent`` importable when this script is run by the CSI venv's
# python without PYTHONPATH set. The systemd sync wrapper
# (``csi_threads_token_refresh_sync.sh``) only sets ``PYTHONPATH=<root>/src`` for
# its inline read block, NOT for this invocation, and ``universal_agent`` is not
# installed into the venv site-packages — so we add ``<repo>/src`` to sys.path
# defensively here. ``parents[3]`` is the repo root
# (scripts -> development -> CSI_Ingester -> <repo root>).
SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
_REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))


def _load_updates(*, raw_json: str, json_file: str, env_var: str) -> dict[str, str]:
    payload_raw = str(raw_json or "").strip()
    if not payload_raw and json_file:
        payload_raw = Path(json_file).expanduser().read_text(encoding="utf-8").strip()
    if not payload_raw:
        payload_raw = str(os.getenv(env_var) or "").strip()
    if not payload_raw:
        raise ValueError(
            "No update payload found. Provide --updates-json, --updates-file, "
            f"or set {env_var}."
        )

    try:
        parsed = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON payload: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Update payload must be a JSON object")

    updates: dict[str, str] = {}
    for key, value in parsed.items():
        name = str(key or "").strip()
        if not name:
            continue
        if value is None:
            continue
        updates[name] = str(value)
    if not updates:
        raise ValueError("Update payload is empty after normalization")
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--updates-json", default="", help="Raw JSON object of key/value secret updates")
    parser.add_argument("--updates-file", default="", help="Path to JSON file with key/value secret updates")
    parser.add_argument(
        "--updates-env-var",
        default="INFISICAL_SECRET_UPDATES_JSON",
        help="Env var name used when --updates-json/--updates-file are not provided",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        updates = _load_updates(
            raw_json=args.updates_json,
            json_file=args.updates_file,
            env_var=str(args.updates_env_var),
        )
    except Exception as exc:
        print(f"ERROR=invalid_update_payload:{type(exc).__name__}:{exc}")
        return 2

    if bool(args.dry_run):
        for name in updates:
            print(f"DRY_RUN UPSERT {name}")
        print("SYNC_UPSERTED=0")
        print(f"SYNC_TOTAL={len(updates)}")
        return 0

    try:
        from universal_agent.infisical_loader import upsert_infisical_secret
    except Exception as exc:
        print(f"ERROR=infisical_loader_import_failed:{type(exc).__name__}:{exc}")
        return 1

    upserted = 0
    failed: list[str] = []
    for name, value in updates.items():
        # upsert_infisical_secret is idempotent (PATCH-then-POST create-or-update)
        # and resolves env/path/creds from the environment. It returns False on a
        # REST failure OR when machine-identity creds are missing (in which case it
        # only updates os.environ) — both are sync failures from our perspective.
        if upsert_infisical_secret(name, value):
            print(f"UPSERTED {name}")
            upserted += 1
        else:
            print(f"FAILED {name}")
            failed.append(name)

    print(f"INFISICAL_ENVIRONMENT={os.getenv('INFISICAL_ENVIRONMENT', '')}")
    print(f"INFISICAL_SECRET_PATH={os.getenv('INFISICAL_SECRET_PATH', '/') or '/'}")
    print(f"SYNC_UPSERTED={upserted}")
    print(f"SYNC_TOTAL={len(updates)}")

    if failed:
        print("ERROR=infisical_sync_failed:upsert_failed:" + ",".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
