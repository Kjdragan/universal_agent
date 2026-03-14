from __future__ import annotations

import argparse
import json

from universal_agent.notebooklm_runtime import run_auth_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="NotebookLM auth preflight helper")
    parser.add_argument("--workspace", required=True, help="Session workspace path")
    parser.add_argument("--timeout", type=float, default=45.0, help="Command timeout seconds")
    args = parser.parse_args()

    # Load Infisical secrets into environment BEFORE auth preflight.
    # This ensures NOTEBOOKLM_AUTH_COOKIE_HEADER and UA_NOTEBOOKLM_PROFILE
    # are available from Infisical when running on the VPS.
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets
        initialize_runtime_secrets()
    except Exception:
        pass  # Non-fatal: preflight will proceed with whatever env vars are set

    result = run_auth_preflight(args.workspace, timeout_seconds=args.timeout)
    payload = {
        "ok": result.ok,
        "profile": result.profile,
        "seeded": result.seeded,
        "refreshed": result.refreshed,
        "command_path": result.command_path,
        "checks_attempted": result.checks_attempted,
        "notes": list(result.notes),
        "errors": list(result.errors),
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
