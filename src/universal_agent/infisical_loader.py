from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_agent.runtime_role import resolve_machine_slug, resolve_runtime_stage

logger = logging.getLogger(__name__)

_VALID_DEPLOYMENT_PROFILES = {"local_workstation", "standalone_node", "vps"}
_VALID_RUNTIME_STAGES = {"development", "staging", "production"}
_LEGACY_INFISICAL_ENV_ALIASES = {
    "dev": "development",
    "prod": "production",
    "staging-hq": "staging",
    "kevins-desktop-hq-dev": "development",
}
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_RESULT: SecretBootstrapResult | None = None


@dataclass(frozen=True)
class SecretBootstrapResult:
    ok: bool
    source: str
    strict_mode: bool
    loaded_count: int
    fallback_used: bool
    environment: str = ""
    runtime_stage: str = ""
    machine_slug: str = ""
    deployment_profile: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve_profile(profile: str | None) -> str:
    candidate = str(profile or os.getenv("UA_DEPLOYMENT_PROFILE") or "local_workstation").strip().lower()
    if candidate in _VALID_DEPLOYMENT_PROFILES:
        return candidate
    return "local_workstation"


def _normalize_infisical_environment(raw_environment: str | None) -> str:
    raw = str(raw_environment or "").strip()
    if not raw:
        return "development"
    lowered = raw.lower()
    return _LEGACY_INFISICAL_ENV_ALIASES.get(lowered, lowered)


def _resolve_runtime_stage_for_bootstrap(
    environment: str,
    *,
    profile: str,
) -> str:
    explicit = str(os.getenv("UA_RUNTIME_STAGE") or "").strip()
    if explicit:
        return resolve_runtime_stage(explicit) or ""
    if environment in _VALID_RUNTIME_STAGES:
        return environment
    if profile == "vps":
        return "production"
    return "development"


def _strict_mode_for_profile(profile: str) -> bool:
    default = profile in {"vps", "standalone_node"}
    return _env_flag("UA_INFISICAL_STRICT", default=default)


def _safe_error(exc: Exception) -> str:
    # Keep error output intentionally generic; never include secret values in logs.
    return f"{type(exc).__name__}"


def _inject_environment_values(values: dict[str, str], *, overwrite: bool = False) -> int:
    inserted = 0
    for key, value in values.items():
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        if not overwrite and clean_key in os.environ:
            continue
        os.environ[clean_key] = str(value or "")
        inserted += 1
    return inserted


def _load_local_dotenv() -> int:
    dotenv_path_raw = str(os.getenv("UA_DOTENV_PATH") or "").strip()
    if dotenv_path_raw:
        dotenv_path = Path(dotenv_path_raw).expanduser()
    else:
        # src/universal_agent/infisical_loader.py -> repo root
        dotenv_path = Path(__file__).resolve().parents[2] / ".env"
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return 0
    try:
        from dotenv import dotenv_values
    except Exception:
        logger.warning("python-dotenv is unavailable; skipping local dotenv fallback")
        return 0

    raw_values = dotenv_values(dotenv_path)
    normalized = {
        str(k): str(v)
        for k, v in raw_values.items()
        if k and v is not None
    }
    inserted = _inject_environment_values(normalized, overwrite=False)
    if inserted > 0:
        logger.info("Loaded %d env values from local dotenv fallback (%s)", inserted, str(dotenv_path))
    return inserted


def _bootstrap_infisical_env() -> None:
    try:
        from dotenv import load_dotenv
        dotenv_path_raw = str(os.getenv("UA_DOTENV_PATH") or "").strip()
        if dotenv_path_raw:
            dotenv_path = Path(dotenv_path_raw).expanduser()
        else:
            dotenv_path = Path(__file__).resolve().parents[2] / ".env"
        
        if dotenv_path.exists() and dotenv_path.is_file():
            load_dotenv(dotenv_path)
    except Exception:
        pass

def _fetch_infisical_secrets() -> dict[str, str]:
    _bootstrap_infisical_env()
    client_id = str(os.getenv("INFISICAL_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("INFISICAL_CLIENT_SECRET") or "").strip()
    project_id = str(os.getenv("INFISICAL_PROJECT_ID") or "").strip()
    environment = _normalize_infisical_environment(os.getenv("INFISICAL_ENVIRONMENT"))
    os.environ["INFISICAL_ENVIRONMENT"] = environment
    secret_path = str(os.getenv("INFISICAL_SECRET_PATH") or "/").strip() or "/"
    api_url = str(os.getenv("INFISICAL_API_URL") or "https://app.infisical.com").strip() or "https://app.infisical.com"
    api_url = api_url.rstrip("/")

    missing = [
        name for name, value in (
            ("INFISICAL_CLIENT_ID", client_id),
            ("INFISICAL_CLIENT_SECRET", client_secret),
            ("INFISICAL_PROJECT_ID", project_id),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required Infisical settings: {', '.join(missing)}")

    try:
        from infisical_client import (
            AuthenticationOptions,
            ClientSettings,
            InfisicalClient,
            ListSecretsOptions,
            UniversalAuthMethod,
        )
    except Exception as exc:
        logger.warning("Infisical SDK unavailable; falling back to REST bootstrap (%s)", _safe_error(exc))
        return _fetch_infisical_secrets_via_rest(
            api_url=api_url,
            client_id=client_id,
            client_secret=client_secret,
            project_id=project_id,
            environment=environment,
            secret_path=secret_path,
        )

    try:
        client = InfisicalClient(
            ClientSettings(
                auth=AuthenticationOptions(
                    universal_auth=UniversalAuthMethod(
                        client_id=client_id,
                        client_secret=client_secret,
                    )
                )
            )
        )
        secrets = client.listSecrets(
            options=ListSecretsOptions(
                environment=environment,
                project_id=project_id,
                path=secret_path,
            )
        )
        out: dict[str, str] = {}
        for item in secrets:
            key = str(getattr(item, "secret_key", "")).strip()
            if not key:
                continue
            out[key] = str(getattr(item, "secret_value", "") or "")
        return out
    except Exception as exc:
        logger.warning("Infisical SDK fetch failed; falling back to REST bootstrap (%s)", _safe_error(exc))
        return _fetch_infisical_secrets_via_rest(
            api_url=api_url,
            client_id=client_id,
            client_secret=client_secret,
            project_id=project_id,
            environment=environment,
            secret_path=secret_path,
        )


def _upsert_infisical_secret_via_rest(
    *,
    api_url: str,
    client_id: str,
    client_secret: str,
    project_id: str,
    environment: str,
    secret_path: str,
    key: str,
    value: str,
) -> None:
    import httpx

    with httpx.Client(timeout=20.0) as client:
        auth_resp = client.post(
            f"{api_url}/api/v1/auth/universal-auth/login",
            json={
                "clientId": client_id,
                "clientSecret": client_secret,
            },
        )
        auth_resp.raise_for_status()
        auth_payload = auth_resp.json() if auth_resp.headers.get("content-type", "").startswith("application/json") else {}
        access_token = str((auth_payload or {}).get("accessToken") or "").strip()
        if not access_token:
            raise RuntimeError("Infisical REST auth response missing access token")

        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Try updating first
        update_resp = client.patch(
            f"{api_url}/api/v3/secrets/raw/{key}",
            json={
                "workspaceId": project_id,
                "environment": environment,
                "secretPath": secret_path,
                "secretValue": value,
                "type": "shared",
            },
            headers=headers,
        )
        
        if update_resp.status_code == 404 or (update_resp.status_code >= 400 and "not found" in update_resp.text.lower()):
            # Fallback to create
            create_resp = client.post(
                f"{api_url}/api/v3/secrets/raw/{key}",
                json={
                    "workspaceId": project_id,
                    "environment": environment,
                    "secretPath": secret_path,
                    "secretValue": value,
                    "type": "shared",
                },
                headers=headers,
            )
            create_resp.raise_for_status()
        else:
            update_resp.raise_for_status()


def upsert_infisical_secret(key: str, value: str) -> bool:
    """
    Update or create a secret in Infisical and update the local os.environ.
    Returns True on success.
    """
    _bootstrap_infisical_env()
    client_id = str(os.getenv("INFISICAL_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("INFISICAL_CLIENT_SECRET") or "").strip()
    project_id = str(os.getenv("INFISICAL_PROJECT_ID") or "").strip()
    environment = _normalize_infisical_environment(os.getenv("INFISICAL_ENVIRONMENT"))
    secret_path = str(os.getenv("INFISICAL_SECRET_PATH") or "/").strip() or "/"
    api_url = str(os.getenv("INFISICAL_API_URL") or "https://app.infisical.com").strip() or "https://app.infisical.com"
    api_url = api_url.rstrip("/")

    if not (client_id and client_secret and project_id):
        # We can't write to Infisical, but we can update the local environment and .env
        logger.warning(f"Upserting {key} to local os.environ only (missing Infisical credentials)")
        os.environ[key] = value
        return False

    try:
        from infisical_client import (
            AuthenticationOptions,
            ClientSettings,
            InfisicalClient,
            UniversalAuthMethod,
        )
        # Import the structs for creation/updating
        from infisical_client.models.options import UpdateSecretOptions, CreateSecretOptions

        client = InfisicalClient(
            ClientSettings(
                auth=AuthenticationOptions(
                    universal_auth=UniversalAuthMethod(
                        client_id=client_id,
                        client_secret=client_secret,
                    )
                )
            )
        )
        
        try:
            client.updateSecret(
                options=UpdateSecretOptions(
                    environment=environment,
                    project_id=project_id,
                    path=secret_path,
                    secret_name=key,
                    secret_value=value,
                    type="shared",
                )
            )
        except Exception as exc:
            if "not found" in str(exc).lower() or "404" in str(exc):
                client.createSecret(
                    options=CreateSecretOptions(
                        environment=environment,
                        project_id=project_id,
                        path=secret_path,
                        secret_name=key,
                        secret_value=value,
                        type="shared",
                    )
                )
            else:
                raise
                
    except Exception as exc:
        logger.warning("Infisical SDK upsert failed; falling back to REST bootstrap (%s)", _safe_error(exc))
        try:
            _upsert_infisical_secret_via_rest(
                api_url=api_url,
                client_id=client_id,
                client_secret=client_secret,
                project_id=project_id,
                environment=environment,
                secret_path=secret_path,
                key=key,
                value=value,
            )
        except Exception as rest_exc:
            logger.error("Infisical REST upsert failed for %s: %s", key, _safe_error(rest_exc))
            return False

    # Update current process environment so changes reflect immediately without restart
    os.environ[key] = value
    logger.info("Successfully upserted secret %s to Infisical (env=%s)", key, environment)
    return True

def _fetch_infisical_secrets_via_rest(
    *,
    api_url: str,
    client_id: str,
    client_secret: str,
    project_id: str,
    environment: str,
    secret_path: str,
) -> dict[str, str]:
    import httpx

    with httpx.Client(timeout=20.0) as client:
        auth_resp = client.post(
            f"{api_url}/api/v1/auth/universal-auth/login",
            json={
                "clientId": client_id,
                "clientSecret": client_secret,
            },
        )
        auth_resp.raise_for_status()
        auth_payload = auth_resp.json() if auth_resp.headers.get("content-type", "").startswith("application/json") else {}
        access_token = str((auth_payload or {}).get("accessToken") or "").strip()
        if not access_token:
            raise RuntimeError("Infisical REST auth response missing access token")

        secrets_resp = client.get(
            f"{api_url}/api/v3/secrets/raw",
            params={
                "workspaceId": project_id,
                "environment": environment,
                "secretPath": secret_path,
                "recursive": "true",
                "include_imports": "true",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        secrets_resp.raise_for_status()
        payload: Any = (
            secrets_resp.json()
            if secrets_resp.headers.get("content-type", "").startswith("application/json")
            else {}
        )

    items = payload.get("secrets") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("Infisical REST secrets response missing secrets list")

    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("secretKey") or item.get("secret_key") or "").strip()
        if not key:
            continue
        value = item.get("secretValue")
        if value is None:
            value = item.get("secret_value")
        out[key] = str(value or "")
    return out


def initialize_runtime_secrets(profile: str | None = None, *, force_reload: bool = False) -> SecretBootstrapResult:
    """
    Initialize runtime secrets with Infisical-first strategy.

    Behavior:
    - strict mode (default on VPS/standalone): fail closed if Infisical cannot load.
    - local mode: allow optional dotenv fallback and existing env-only startup.
    """
    global _BOOTSTRAP_RESULT

    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAP_RESULT is not None and not force_reload:
            return _BOOTSTRAP_RESULT

        resolved_profile = _resolve_profile(profile)
        strict_mode = _strict_mode_for_profile(resolved_profile)
        normalized_environment = _normalize_infisical_environment(os.getenv("INFISICAL_ENVIRONMENT"))
        os.environ["INFISICAL_ENVIRONMENT"] = normalized_environment
        runtime_stage = _resolve_runtime_stage_for_bootstrap(
            normalized_environment,
            profile=resolved_profile,
        )
        os.environ["UA_RUNTIME_STAGE"] = runtime_stage
        machine_slug = resolve_machine_slug()
        os.environ["UA_MACHINE_SLUG"] = machine_slug
        infisical_enabled = _env_flag("UA_INFISICAL_ENABLED", default=True)
        allow_dotenv_fallback = _env_flag(
            "UA_INFISICAL_ALLOW_DOTENV_FALLBACK",
            default=(resolved_profile == "local_workstation"),
        )

        errors: list[str] = []
        loaded_count = 0
        source = "environment"
        fallback_used = False

        if infisical_enabled:
            try:
                secret_values = _fetch_infisical_secrets()
                loaded_count = _inject_environment_values(secret_values, overwrite=False)
                source = "infisical"
                normalized_environment = _normalize_infisical_environment(os.getenv("INFISICAL_ENVIRONMENT"))
                os.environ["INFISICAL_ENVIRONMENT"] = normalized_environment
                runtime_stage = _resolve_runtime_stage_for_bootstrap(
                    normalized_environment,
                    profile=resolved_profile,
                )
                os.environ["UA_RUNTIME_STAGE"] = runtime_stage
                machine_slug = resolve_machine_slug()
                os.environ["UA_MACHINE_SLUG"] = machine_slug
                logger.info(
                    "Infisical runtime secret bootstrap succeeded: profile=%s env=%s stage=%s machine=%s loaded=%d",
                    resolved_profile,
                    normalized_environment,
                    runtime_stage,
                    machine_slug,
                    loaded_count,
                )
            except Exception as exc:
                err = _safe_error(exc)
                errors.append(err)
                logger.warning(
                    "Infisical runtime secret bootstrap failed: profile=%s reason=%s",
                    resolved_profile,
                    err,
                )
        else:
            logger.info("Infisical bootstrap disabled by UA_INFISICAL_ENABLED=0")

        if source != "infisical":
            if strict_mode:
                failure = SecretBootstrapResult(
                    ok=False,
                    source="none",
                    strict_mode=True,
                    loaded_count=0,
                    fallback_used=False,
                    environment=normalized_environment,
                    runtime_stage=runtime_stage,
                    machine_slug=machine_slug,
                    deployment_profile=resolved_profile,
                    errors=tuple(errors or ["InfisicalBootstrapUnavailable"]),
                )
                _BOOTSTRAP_RESULT = failure
                raise RuntimeError(
                    "Infisical bootstrap is required in strict mode but could not be completed"
                )

            if allow_dotenv_fallback:
                loaded_count = _load_local_dotenv()
                if loaded_count > 0:
                    source = "dotenv"
            fallback_used = bool(errors)

        result = SecretBootstrapResult(
            ok=True,
            source=source,
            strict_mode=strict_mode,
            loaded_count=max(0, int(loaded_count)),
            fallback_used=fallback_used,
            environment=normalized_environment,
            runtime_stage=runtime_stage,
            machine_slug=machine_slug,
            deployment_profile=resolved_profile,
            errors=tuple(errors),
        )
        _BOOTSTRAP_RESULT = result
        return result
