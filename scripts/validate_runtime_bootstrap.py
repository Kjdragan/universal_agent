#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universal_agent.infisical_loader import initialize_runtime_secrets


def _identity_snapshot() -> dict[str, Any]:
    return {
        "infisical_environment": str(os.getenv("INFISICAL_ENVIRONMENT") or "").strip(),
        "runtime_stage": str(os.getenv("UA_RUNTIME_STAGE") or "").strip(),
        "factory_role": str(os.getenv("FACTORY_ROLE") or "").strip(),
        "deployment_profile": str(os.getenv("UA_DEPLOYMENT_PROFILE") or "").strip(),
        "machine_slug": str(os.getenv("UA_MACHINE_SLUG") or "").strip(),
    }


def _assert_equal(name: str, actual: str, expected: str) -> None:
    if expected and actual != expected:
        raise RuntimeError(f"{name} mismatch: expected {expected!r}, got {actual!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate runtime bootstrap identity and Infisical secret access.",
    )
    parser.add_argument("--profile", default=None)
    parser.add_argument("--require", action="append", default=[])
    parser.add_argument("--expect-environment", default="")
    parser.add_argument("--expect-runtime-stage", default="")
    parser.add_argument("--expect-factory-role", default="")
    parser.add_argument("--expect-deployment-profile", default="")
    parser.add_argument("--expect-machine-slug", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = initialize_runtime_secrets(profile=args.profile, force_reload=True)
    identity = _identity_snapshot()

    _assert_equal("INFISICAL_ENVIRONMENT", identity["infisical_environment"], args.expect_environment)
    _assert_equal("UA_RUNTIME_STAGE", identity["runtime_stage"], args.expect_runtime_stage)
    _assert_equal("FACTORY_ROLE", identity["factory_role"], args.expect_factory_role)
    _assert_equal("UA_DEPLOYMENT_PROFILE", identity["deployment_profile"], args.expect_deployment_profile)
    _assert_equal("UA_MACHINE_SLUG", identity["machine_slug"], args.expect_machine_slug)

    missing: list[str] = []
    for key in args.require:
        if not str(os.getenv(key) or "").strip():
            missing.append(key)
    if missing:
        raise RuntimeError("Missing required keys: " + ", ".join(sorted(missing)))

    payload = {
        "ok": True,
        "bootstrap": {
            "source": result.source,
            "loaded_count": result.loaded_count,
            "strict_mode": result.strict_mode,
            "fallback_used": result.fallback_used,
            "errors": list(result.errors),
        },
        "identity": identity,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
