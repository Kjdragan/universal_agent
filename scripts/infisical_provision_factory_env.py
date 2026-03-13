#!/usr/bin/env python3
"""Legacy helper for machine-shaped Infisical environments.

This script predates the stage-based Infisical model. It is retained as an
admin/break-glass helper for historical machine-shaped environments, but new
deployments should use the canonical stage environments (`development`,
`staging`, `production`) with machine-local bootstrap identity instead.

Legacy usage examples::

    # Create "Kevin's Desktop" environment (LOCAL_WORKER)
    python scripts/infisical_provision_factory_env.py \
        --machine-name "Kevin's Desktop" \
        --machine-slug kevins-desktop \
        --factory-role LOCAL_WORKER

    # Dry-run to see what would be created
    python scripts/infisical_provision_factory_env.py \
        --machine-name "Kevin's Desktop" \
        --machine-slug kevins-desktop \
        --factory-role LOCAL_WORKER \
        --dry-run

    # Create from a different source environment
    python scripts/infisical_provision_factory_env.py \
        --machine-name "Kevin's Tablet" \
        --machine-slug kevins-tablet \
        --factory-role LOCAL_WORKER \
        --source-env dev

Prerequisites:
    - INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, INFISICAL_PROJECT_ID
      must be set (via .env or shell environment).
    - ``httpx`` must be installed (included in UA deps).

For the canonical stage-based workflow, prefer:
- `scripts/infisical_manage_stage_env.py`
- `scripts/bootstrap_local_hq_dev.sh`
- `scripts/bootstrap_local_worker_stage.sh`
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Override maps per factory role
# ---------------------------------------------------------------------------

_HEADQUARTERS_OVERRIDES: dict[str, str] = {
    "FACTORY_ROLE": "HEADQUARTERS",
    "UA_DEPLOYMENT_PROFILE": "vps",
    "UA_DELEGATION_REDIS_ENABLED": "1",
    "UA_VP_EXTERNAL_DISPATCH_ENABLED": "1",
    "UA_ENABLE_HEARTBEAT": "1",
    "UA_ENABLE_CRON": "1",
    "UA_INFISICAL_STRICT": "",
    "UA_INFISICAL_ALLOW_DOTENV_FALLBACK": "0",
    "ENABLE_VP_CODER": "true",
    "UA_ENABLE_GWS_CLI": "1",
    "UA_HOOKS_ENABLED": "1",
    "UA_SIGNALS_INGEST_ENABLED": "1",
    "UA_CAPABILITY_CSI_INGEST": "1",
    "UA_AGENTMAIL_ENABLED": "1",
    "UA_YT_PLAYLIST_WATCHER_ENABLED": "1",
    "UA_HOOKS_YOUTUBE_INGEST_MODE": "local_worker",
    "UA_HOOKS_YOUTUBE_INGEST_URLS": "http://127.0.0.1:8002/api/v1/youtube/ingest",
    "UA_YOUTUBE_INGEST_REQUIRE_PROXY": "1",
    "UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT": "/opt/universal_agent_data/tutorial_repos",
}

_LOCAL_WORKER_OVERRIDES: dict[str, str] = {
    "FACTORY_ROLE": "LOCAL_WORKER",
    "UA_DEPLOYMENT_PROFILE": "local_workstation",
    "UA_DELEGATION_REDIS_ENABLED": "1",
    # Keep local workers close to HQ operational behavior for dual-factory
    # coordination, while explicitly disabling duplicate CSI ingestion.
    "UA_VP_EXTERNAL_DISPATCH_ENABLED": "1",
    "UA_ENABLE_HEARTBEAT": "1",
    "UA_ENABLE_CRON": "1",
    "UA_INFISICAL_STRICT": "0",
    "UA_INFISICAL_ALLOW_DOTENV_FALLBACK": "1",
    "ENABLE_VP_CODER": "true",
    "UA_ENABLE_GWS_CLI": "1",
    "UA_HOOKS_ENABLED": "0",
    "UA_SIGNALS_INGEST_ENABLED": "0",
    "UA_CAPABILITY_CSI_INGEST": "0",
    "UA_AGENTMAIL_ENABLED": "1",
    "UA_YT_PLAYLIST_WATCHER_ENABLED": "0",
    "UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "0",
}

_STANDALONE_NODE_OVERRIDES: dict[str, str] = {
    "FACTORY_ROLE": "STANDALONE_NODE",
    "UA_DEPLOYMENT_PROFILE": "standalone_node",
    "UA_DELEGATION_REDIS_ENABLED": "0",
    "UA_VP_EXTERNAL_DISPATCH_ENABLED": "0",
    "UA_ENABLE_HEARTBEAT": "0",
    "UA_ENABLE_CRON": "0",
    "UA_INFISICAL_STRICT": "0",
    "UA_INFISICAL_ALLOW_DOTENV_FALLBACK": "1",
    "ENABLE_VP_CODER": "true",
    "UA_ENABLE_GWS_CLI": "0",
    "UA_HOOKS_ENABLED": "0",
    "UA_SIGNALS_INGEST_ENABLED": "0",
    "UA_AGENTMAIL_ENABLED": "0",
    "UA_YT_PLAYLIST_WATCHER_ENABLED": "0",
    "UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "0",
}

ROLE_OVERRIDES: dict[str, dict[str, str]] = {
    "HEADQUARTERS": _HEADQUARTERS_OVERRIDES,
    "LOCAL_WORKER": _LOCAL_WORKER_OVERRIDES,
    "STANDALONE_NODE": _STANDALONE_NODE_OVERRIDES,
}

# ---------------------------------------------------------------------------
# Infisical REST helpers
# ---------------------------------------------------------------------------

API_URL_DEFAULT = "https://app.infisical.com"


def _load_dotenv_into_environ() -> None:
    """Best-effort load of .env so Infisical creds are available."""
    repo_root = Path(__file__).resolve().parents[1]
    dotenv_path = repo_root / ".env"
    if not dotenv_path.exists():
        return
    try:
        from dotenv import dotenv_values
        for key, value in dotenv_values(dotenv_path).items():
            if key and value is not None and key not in os.environ:
                os.environ[key] = value
    except ImportError:
        # Manual fallback
        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _get_infisical_creds() -> tuple[str, str, str, str]:
    """Return (client_id, client_secret, project_id, api_url)."""
    client_id = os.environ.get("INFISICAL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("INFISICAL_CLIENT_SECRET", "").strip()
    project_id = os.environ.get("INFISICAL_PROJECT_ID", "").strip()
    api_url = (os.environ.get("INFISICAL_API_URL") or API_URL_DEFAULT).strip().rstrip("/")
    missing = []
    if not client_id:
        missing.append("INFISICAL_CLIENT_ID")
    if not client_secret:
        missing.append("INFISICAL_CLIENT_SECRET")
    if not project_id:
        missing.append("INFISICAL_PROJECT_ID")
    if missing:
        raise RuntimeError(f"Missing required Infisical credentials: {', '.join(missing)}")
    return client_id, client_secret, project_id, api_url


def _authenticate(api_url: str, client_id: str, client_secret: str) -> str:
    """Authenticate with Infisical universal auth and return access token."""
    import httpx
    resp = httpx.post(
        f"{api_url}/api/v1/auth/universal-auth/login",
        json={"clientId": client_id, "clientSecret": client_secret},
        timeout=20.0,
    )
    resp.raise_for_status()
    token = resp.json().get("accessToken", "").strip()
    if not token:
        raise RuntimeError("Infisical auth response missing accessToken")
    return token


def _list_environments(api_url: str, token: str, project_id: str) -> list[dict[str, Any]]:
    """List existing environments for the project via workspace endpoint."""
    import httpx
    resp = httpx.get(
        f"{api_url}/api/v1/workspace/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    data = resp.json()
    # Response shape: {"workspace": {"environments": [...]}}
    envs = data.get("workspace", {}).get("environments") or data.get("environments") or []
    return envs


def _create_environment(
    api_url: str,
    token: str,
    project_id: str,
    name: str,
    slug: str,
    position: int = 10,
) -> dict[str, Any]:
    """Create a new Infisical environment."""
    import httpx
    resp = httpx.post(
        f"{api_url}/api/v1/projects/{project_id}/environments",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"name": name, "slug": slug, "position": position},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_secrets(
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secret_path: str = "/",
) -> dict[str, str]:
    """Fetch all secrets from an environment."""
    import httpx
    resp = httpx.get(
        f"{api_url}/api/v3/secrets/raw",
        params={
            "workspaceId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "recursive": "true",
            "include_imports": "true",
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=20.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    items = payload.get("secrets", [])
    out: dict[str, str] = {}
    for item in items:
        key = str(item.get("secretKey") or "").strip()
        if not key:
            continue
        value = item.get("secretValue")
        if value is None:
            value = item.get("secret_value")
        out[key] = str(value or "")
    return out


def _bulk_create_secrets(
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secrets: dict[str, str],
    secret_path: str = "/",
) -> dict[str, Any]:
    """Bulk-create secrets into an environment via v4 batch API."""
    import httpx
    secret_entries = [
        {"secretKey": key, "secretValue": value}
        for key, value in sorted(secrets.items())
    ]
    resp = httpx.post(
        f"{api_url}/api/v4/secrets/batch",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "projectId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "secrets": secret_entries,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _bulk_update_secrets(
    api_url: str,
    token: str,
    project_id: str,
    environment: str,
    secrets: dict[str, str],
    secret_path: str = "/",
) -> dict[str, Any]:
    """Bulk-update secrets in an existing environment via v4 batch PATCH API."""
    import httpx
    secret_entries = [
        {"secretKey": key, "secretValue": value}
        for key, value in sorted(secrets.items())
    ]
    resp = httpx.patch(
        f"{api_url}/api/v4/secrets/batch",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "projectId": project_id,
            "environment": environment,
            "secretPath": secret_path,
            "secrets": secret_entries,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Main provisioning logic
# ---------------------------------------------------------------------------

def provision(
    *,
    machine_name: str,
    machine_slug: str,
    factory_role: str,
    deployment_profile: str | None = None,
    source_env: str = "dev",
    extra_overrides: dict[str, str] | None = None,
    dry_run: bool = False,
) -> None:
    """Provision a new Infisical environment for a factory machine."""
    role_upper = factory_role.upper().strip()
    if role_upper not in ROLE_OVERRIDES:
        raise ValueError(f"Unknown factory role: {factory_role}. Must be one of: {', '.join(ROLE_OVERRIDES)}")

    overrides = dict(ROLE_OVERRIDES[role_upper])
    if deployment_profile:
        overrides["UA_DEPLOYMENT_PROFILE"] = deployment_profile.strip().lower()
    # Set the target environment slug in the secrets themselves so the factory
    # knows which Infisical environment to load on startup.
    overrides["INFISICAL_ENVIRONMENT"] = machine_slug
    if extra_overrides:
        overrides.update(extra_overrides)

    _load_dotenv_into_environ()
    client_id, client_secret, project_id, api_url = _get_infisical_creds()

    print(f"Infisical API: {api_url}")
    print(f"Project ID:    {project_id}")
    print(f"Source env:    {source_env}")
    print(f"Target env:    {machine_slug} ({machine_name})")
    print(f"Factory role:  {role_upper}")
    if deployment_profile:
        print(f"Deploy profile:{deployment_profile.strip().lower()}")
    print(f"Overrides:     {len(overrides)} keys")
    print()

    if dry_run:
        print("=== DRY RUN — no changes will be made ===\n")

    # Step 1: Authenticate
    print("Authenticating with Infisical...")
    if dry_run:
        print("  [dry-run] Would authenticate with universal auth\n")
        token = "DRY_RUN_TOKEN"
    else:
        token = _authenticate(api_url, client_id, client_secret)
        print("  Authenticated successfully.\n")

    # Step 2: Check if target environment already exists
    env_exists = False
    if not dry_run:
        existing_envs = _list_environments(api_url, token, project_id)
        env_slugs = [e.get("slug", "") for e in existing_envs]
        env_exists = machine_slug in env_slugs
        if env_exists:
            print(f"Environment '{machine_slug}' already exists — skipping creation.\n")
        else:
            print(f"Existing environments: {env_slugs}")
    else:
        print("  [dry-run] Would check existing environments\n")

    # Step 3: Create environment if needed
    if not env_exists:
        print(f"Creating environment: name='{machine_name}' slug='{machine_slug}'")
        if dry_run:
            print("  [dry-run] Would POST /api/v1/projects/{projectId}/environments\n")
        else:
            result = _create_environment(api_url, token, project_id, machine_name, machine_slug)
            print(f"  Created: {json.dumps(result.get('environment', {}), indent=2)}\n")

    # Step 4: Fetch secrets from source environment
    print(f"Fetching secrets from source environment '{source_env}'...")
    if dry_run:
        print("  [dry-run] Would GET /api/v3/secrets/raw\n")
        source_secrets: dict[str, str] = {}
    else:
        source_secrets = _fetch_secrets(api_url, token, project_id, source_env)
        print(f"  Fetched {len(source_secrets)} secrets from '{source_env}'.\n")

    # Step 5: Apply overrides
    target_secrets = dict(source_secrets)
    applied_overrides: list[tuple[str, str, str]] = []
    for key, new_value in sorted(overrides.items()):
        old_value = target_secrets.get(key, "<not in source>")
        target_secrets[key] = new_value
        if old_value != new_value:
            applied_overrides.append((key, old_value, new_value))

    print(f"Overrides applied ({len(applied_overrides)} changed):")
    for key, old_val, new_val in applied_overrides:
        # Mask sensitive values
        if any(s in key.lower() for s in ("secret", "token", "password", "key", "api_key")):
            old_display = "***" if old_val and old_val != "<not in source>" else old_val
            new_display = "***" if new_val else "(empty)"
        else:
            old_display = old_val[:40] if old_val else "(empty)"
            new_display = new_val[:40] if new_val else "(empty)"
        print(f"  {key}: {old_display} → {new_display}")
    print()

    # Step 6: Bulk-create (or update) secrets into target environment
    if dry_run:
        print(f"[dry-run] Would bulk-create {len(target_secrets)} secrets into '{machine_slug}'")
        print("\nDry run complete. No changes were made.")
        return

    if env_exists:
        # Environment already exists — try to update existing secrets and create new ones
        print(f"Updating {len(target_secrets)} secrets in '{machine_slug}'...")
        try:
            # Try bulk update first for existing secrets
            existing_target = _fetch_secrets(api_url, token, project_id, machine_slug)
            to_update = {k: v for k, v in target_secrets.items() if k in existing_target}
            to_create = {k: v for k, v in target_secrets.items() if k not in existing_target}

            if to_update:
                _bulk_update_secrets(api_url, token, project_id, machine_slug, to_update)
                print(f"  Updated {len(to_update)} existing secrets.")
            if to_create:
                _bulk_create_secrets(api_url, token, project_id, machine_slug, to_create)
                print(f"  Created {len(to_create)} new secrets.")
        except Exception as exc:
            logger.warning("Bulk update failed (%s), falling back to full re-create", exc)
            _bulk_create_secrets(api_url, token, project_id, machine_slug, target_secrets)
            print(f"  Created {len(target_secrets)} secrets (full re-create).")
    else:
        print(f"Bulk-creating {len(target_secrets)} secrets into '{machine_slug}'...")
        _bulk_create_secrets(api_url, token, project_id, machine_slug, target_secrets)
        print(f"  Created {len(target_secrets)} secrets.")

    print(f"\n{'='*60}")
    print(f"Environment '{machine_slug}' ({machine_name}) provisioned successfully.")
    print(f"Factory role: {role_upper}")
    print(f"\nTo use this environment on the target machine, set:")
    print(f"  INFISICAL_ENVIRONMENT={machine_slug}")
    print(f"  (plus INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, INFISICAL_PROJECT_ID)")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision a new Infisical environment for a factory deployment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Kevin's Desktop (LOCAL_WORKER)
  %(prog)s --machine-name "Kevin's Desktop" --machine-slug kevins-desktop --factory-role LOCAL_WORKER

  # Kevin's Desktop HQ Dev (HEADQUARTERS + local_workstation)
  %(prog)s --machine-name "Kevin's Desktop HQ Dev" --machine-slug kevins-desktop-hq-dev --factory-role HEADQUARTERS --deployment-profile local_workstation

  # Kevin's Tablet (LOCAL_WORKER)
  %(prog)s --machine-name "Kevin's Tablet" --machine-slug kevins-tablet --factory-role LOCAL_WORKER

  # Dry run
  %(prog)s --machine-name "Kevin's Desktop" --machine-slug kevins-desktop --factory-role LOCAL_WORKER --dry-run
""",
    )
    parser.add_argument(
        "--machine-name",
        required=True,
        help="Human-readable machine name (e.g. \"Kevin's Desktop\")",
    )
    parser.add_argument(
        "--machine-slug",
        required=True,
        help="URL-safe slug for the environment (e.g. kevins-desktop)",
    )
    parser.add_argument(
        "--factory-role",
        required=True,
        choices=["HEADQUARTERS", "LOCAL_WORKER", "STANDALONE_NODE"],
        help="Factory role for this machine",
    )
    parser.add_argument(
        "--source-env",
        default="dev",
        help="Source environment to clone secrets from (default: dev)",
    )
    parser.add_argument(
        "--deployment-profile",
        choices=["local_workstation", "standalone_node", "vps"],
        default=None,
        help="Optional deployment profile override; defaults to the role-specific profile",
    )
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional overrides (can be specified multiple times)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making changes",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Parse extra overrides from CLI
    extra_overrides: dict[str, str] = {}
    for item in args.override:
        if "=" not in item:
            parser.error(f"--override must be KEY=VALUE, got: {item}")
        key, _, value = item.partition("=")
        extra_overrides[key.strip()] = value.strip()

    try:
        provision(
            machine_name=args.machine_name,
            machine_slug=args.machine_slug,
            factory_role=args.factory_role,
            deployment_profile=args.deployment_profile,
            source_env=args.source_env,
            extra_overrides=extra_overrides or None,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
