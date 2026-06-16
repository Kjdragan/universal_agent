"""Optional Infisical secret bootstrap for CSI.

Mirrors the UA pattern in ``infisical_loader.py``: fetch secrets from
Infisical over the REST API (universal-auth machine identity), inject into
``os.environ`` so that ``CSIConfig`` reads them transparently via
``os.getenv()``. We use REST (``httpx``) rather than the ``infisical_client``
SDK on purpose — the SDK was removed from the project on 2026-05-12 (no cp313
wheel, won't build clean on 3.13) and ``httpx`` covers the full surface we use.

When disabled (default) or when Infisical credentials are absent, this
module is a no-op and CSI falls back to its existing env-file behaviour.

Env vars:
    CSI_INFISICAL_ENABLED       — 1 to enable (default 0)
    INFISICAL_CLIENT_ID         — machine identity client ID
    INFISICAL_CLIENT_SECRET     — machine identity client secret
    INFISICAL_PROJECT_ID        — Infisical project ID
    INFISICAL_ENVIRONMENT       — environment slug (default: csi)
    INFISICAL_SECRET_PATH       — secret path (default: /)
    INFISICAL_API_URL           — API base (default: https://app.infisical.com)
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CSIBootstrapResult:
    ok: bool
    source: str          # "infisical", "environment", "disabled"
    loaded_count: int
    error: str = ""


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _allowlist_keys() -> set[str] | None:
    """Return the ``CSI_INFISICAL_KEYS`` allowlist, or ``None`` for inject-all mode.

    When set (comma-separated), only the named keys are injected from the vault.
    This is the guard against the #820/#824 + 2026-06-16 regression: the vault
    contains proxy-routing vars (``CSI_RSS_PROXY_URL``, ``HTTP_PROXY``, ...) that,
    if injected into ``os.environ``, route the youtube RSS httpx client through a
    proxy that rejects the embedded creds with ``407 NO_USER``. Proxy routing is
    an explicit operator opt-in via the systemd env file only — never the vault.
    """
    raw = (os.getenv("CSI_INFISICAL_KEYS") or "").strip()
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


def _inject(values: dict[str, str], allowlist: set[str] | None = None) -> int:
    count = 0
    for k, v in values.items():
        k = k.strip()
        if not k:
            continue
        if allowlist is not None and k not in allowlist:
            continue
        if k not in os.environ:
            os.environ[k] = str(v or "")
            count += 1
    return count


def _fetch_via_rest(
    api_url: str,
    client_id: str,
    client_secret: str,
    project_id: str,
    environment: str,
    secret_path: str,
) -> dict[str, str]:
    """Fetch secrets from Infisical REST API (no SDK dependency)."""
    import httpx

    with httpx.Client(timeout=20.0) as client:
        auth_resp = client.post(
            f"{api_url}/api/v1/auth/universal-auth/login",
            json={"clientId": client_id, "clientSecret": client_secret},
        )
        auth_resp.raise_for_status()
        token = (auth_resp.json() or {}).get("accessToken", "")
        if not token:
            raise RuntimeError("Infisical REST auth: missing accessToken")

        secrets_resp = client.get(
            f"{api_url}/api/v3/secrets/raw",
            params={
                "workspaceId": project_id,
                "environment": environment,
                "secretPath": secret_path,
                "recursive": "true",
                "include_imports": "true",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        secrets_resp.raise_for_status()
        payload: Any = secrets_resp.json() or {}

    items = payload.get("secrets") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("Infisical REST: missing secrets list in response")

    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (item.get("secretKey") or item.get("secret_key") or "").strip()
        if not key:
            continue
        val = item.get("secretValue")
        if val is None:
            val = item.get("secret_value")
        out[key] = str(val or "")
    return out


def _fetch_secrets() -> dict[str, str]:
    """Fetch secrets from Infisical over the REST API (universal-auth)."""
    client_id = os.getenv("INFISICAL_CLIENT_ID", "").strip()
    client_secret = os.getenv("INFISICAL_CLIENT_SECRET", "").strip()
    project_id = os.getenv("INFISICAL_PROJECT_ID", "").strip()
    environment = os.getenv("INFISICAL_ENVIRONMENT", "csi").strip() or "csi"
    secret_path = os.getenv("INFISICAL_SECRET_PATH", "/").strip() or "/"
    api_url = (os.getenv("INFISICAL_API_URL", "https://app.infisical.com").strip()
               or "https://app.infisical.com").rstrip("/")

    missing = [n for n, v in [
        ("INFISICAL_CLIENT_ID", client_id),
        ("INFISICAL_CLIENT_SECRET", client_secret),
        ("INFISICAL_PROJECT_ID", project_id),
    ] if not v]
    if missing:
        raise RuntimeError(f"Missing Infisical credentials: {', '.join(missing)}")

    return _fetch_via_rest(
        api_url=api_url,
        client_id=client_id,
        client_secret=client_secret,
        project_id=project_id,
        environment=environment,
        secret_path=secret_path,
    )


def bootstrap_csi_secrets() -> CSIBootstrapResult:
    """Run Infisical bootstrap for CSI if enabled.

    Call this **before** ``load_config()`` so that secrets are available
    in ``os.environ`` when ``CSIConfig`` properties resolve them.
    """
    if not _env_flag("CSI_INFISICAL_ENABLED", default=False):
        return CSIBootstrapResult(ok=True, source="disabled", loaded_count=0)

    try:
        values = _fetch_secrets()
        allowlist = _allowlist_keys()
        count = _inject(values, allowlist=allowlist)
        if allowlist is not None:
            logger.info(
                "CSI Infisical bootstrap loaded %d secrets (allowlist: %d keys, vault fetched %d)",
                count, len(allowlist), len(values),
            )
        else:
            logger.info("CSI Infisical bootstrap loaded %d secrets", count)
        return CSIBootstrapResult(ok=True, source="infisical", loaded_count=count)
    except Exception as exc:
        logger.warning("CSI Infisical bootstrap failed: %s", exc)
        return CSIBootstrapResult(
            ok=False, source="environment", loaded_count=0,
            error=f"{type(exc).__name__}: {exc}",
        )
