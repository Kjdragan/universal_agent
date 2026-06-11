from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import threading
from typing import Any

from universal_agent.runtime_role import resolve_machine_slug, resolve_runtime_stage

logger = logging.getLogger(__name__)

_VALID_DEPLOYMENT_PROFILES = {"local_workstation", "standalone_node", "vps"}
_VALID_RUNTIME_STAGES = {"development", "staging", "local", "production"}
_LEGACY_INFISICAL_ENV_ALIASES = {
    "dev": "development",
    "prod": "production",
    "staging-hq": "staging",
    "kevins-desktop-hq-dev": "development",
}
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_RESULT: SecretBootstrapResult | None = None

# Keys that represent the runtime's bootstrap identity. When already set in
# the process environment (typically by systemd Environment= directives or
# the bootstrap .env), we preserve the pre-set value even if Infisical
# returns a different one. Everything else is treated as application
# configuration where Infisical is authoritative.
#
# Why this exists: previously _inject_environment_values defaulted to
# overwrite=False, which meant ANY key already in os.environ was silently
# skipped — including new Infisical-managed feature flags. On the VPS,
# systemd + bootstrap .env + module-import side effects pre-populate ~37
# keys before bootstrap runs, so any of those that overlap with Infisical
# secrets stayed at their bootstrap values forever. Operator-flippable
# disables like UA_ATLAS_DIRECT_DISPATCH_ENABLED were unreachable from
# Infisical. The fix is to make Infisical authoritative by default, with
# this small carve-out for true identity keys that must not be moved by
# remote config.
_BOOTSTRAP_IDENTITY_KEYS: frozenset[str] = frozenset({
    "INFISICAL_CLIENT_ID",
    "INFISICAL_CLIENT_SECRET",
    "INFISICAL_PROJECT_ID",
    "INFISICAL_API_URL",
    "INFISICAL_ENVIRONMENT",
    "INFISICAL_SECRET_PATH",
    "UA_RUNTIME_STAGE",
    "UA_MACHINE_SLUG",
    "UA_DEPLOYMENT_PROFILE",
    "FACTORY_ROLE",
    "UA_INFISICAL_ENABLED",
    "UA_INFISICAL_STRICT",
    "UA_INFISICAL_ALLOW_DOTENV_FALLBACK",
    "UA_DOTENV_PATH",
})


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


def _on_production_vps() -> bool:
    """True when this process is running on the production VPS.

    The deploy target `/opt/universal_agent` exists ONLY on the VPS (the deploy
    pipeline checks the repo out there). It is never present on Kevin's desktop
    (the repo lives at `/home/kjdragan/lrepos/universal_agent`) or in CI, and the
    SSHFS bridge mounts the desktop tree onto the VPS, not the reverse — so it is a
    reliable, env-independent "am I on the prod box?" signal.
    """
    try:
        return Path("/opt/universal_agent").is_dir()
    except OSError:
        return False


def _normalize_infisical_environment(raw_environment: str | None) -> str:
    raw = str(raw_environment or "").strip()
    if not raw:
        # The VPS is production-only (see CLAUDE.md). When INFISICAL_ENVIRONMENT is
        # unset there, the right default is "production", NOT "development" — the
        # services set it explicitly via systemd, so only manual/standalone runs
        # (cron-style scripts, an operator's ad-hoc `python ...`) hit this path, and
        # silently loading DEV secrets on the prod box is a recurring footgun: toggles
        # like UA_AGENTMAIL_ENABLED default off in dev, so things fail confusingly
        # (a digest "sends" but AgentMail is disabled) instead of working. Default to
        # production on the prod host; everywhere else stays "development". An explicit
        # INFISICAL_ENVIRONMENT=development still wins (handled below).
        if _on_production_vps():
            logger.warning(
                "INFISICAL_ENVIRONMENT is unset on the production VPS — defaulting to "
                "'production' (not 'development'). Set INFISICAL_ENVIRONMENT explicitly "
                "to override (e.g. =development to intentionally use dev secrets here)."
            )
            return "production"
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


# Env vars whose VALUE is a single-line filesystem path (TLS CA bundles / cert
# files). Infisical stores values verbatim and the injector below does NOT strip
# them (unlike the .env reader `_claude_launcher.py::_source_env_file`, which
# does `.strip()`). So a path value pasted into Infisical with a stray trailing
# newline reaches os.environ malformed — e.g. a `CURL_CA_BUNDLE` ending in "\n"
# makes curl treat the newline-suffixed path as an unreadable cert file and fail
# EVERY HTTPS request (exit 77 / HTTP 000). Observed live 2026-06-11 in both the
# development and production Infisical envs. These keys are ALWAYS paths, so
# trimming surrounding whitespace is safe and a no-op when the value is clean.
#
# We deliberately do NOT strip every secret value here: multi-line secrets (PEM
# private keys, JSON service-account blobs) legitimately contain trailing
# newlines and must survive byte-for-byte. The fix is therefore a targeted
# allowlist of unambiguous path vars, not a blanket strip.
_PATH_VALUE_KEYS: frozenset[str] = frozenset(
    {"CURL_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
)
_PATH_VALUE_SUFFIXES: tuple[str, ...] = ("_CA_BUNDLE", "_CERT_FILE", "_CERT_PATH")


def _normalize_secret_value(key: str, value: str) -> str:
    """Trim surrounding whitespace from known single-line path env vars only.

    Targeted backstop (NOT a blanket strip) for the failure mode where an
    Infisical-stored cert-path value carries a stray trailing newline and breaks
    curl/OpenSSL. Only keys that are unambiguously filesystem paths
    (``_PATH_VALUE_KEYS`` / ``_PATH_VALUE_SUFFIXES``) are trimmed; every other
    secret value passes through unchanged so multi-line secrets are preserved.
    """
    if key in _PATH_VALUE_KEYS or key.endswith(_PATH_VALUE_SUFFIXES):
        return value.strip()
    return value


def _inject_environment_values(
    values: dict[str, str],
    *,
    overwrite: bool = False,
    exclude_prefixes: tuple[str, ...] = (),
    preserve_keys: frozenset[str] = frozenset(),
) -> int:
    inserted = 0
    for key, value in values.items():
        clean_key = str(key or "").strip()
        if not clean_key:
            continue
        if exclude_prefixes and any(clean_key.startswith(p) for p in exclude_prefixes):
            continue
        # Bootstrap identity keys win over remote config when pre-set,
        # even if overwrite=True for everything else.
        if clean_key in preserve_keys and clean_key in os.environ:
            continue
        if not overwrite and clean_key in os.environ:
            continue
        os.environ[clean_key] = _normalize_secret_value(clean_key, str(value or ""))
        inserted += 1

        # Alias for zai_vision tool compatibility
        if clean_key == "ZAI_API_KEY":
            if overwrite or "Z_AI_API_KEY" not in os.environ:
                os.environ["Z_AI_API_KEY"] = str(value or "")
                inserted += 1

    return inserted


def _load_local_dotenv(exclude_prefixes: tuple[str, ...] = ()) -> int:
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
    inserted = _inject_environment_values(
        normalized,
        overwrite=False,
        exclude_prefixes=exclude_prefixes,
    )
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


def initialize_runtime_secrets(
    profile: str | None = None,
    *,
    force_reload: bool = False,
    exclude_prefixes: tuple[str, ...] = (),
) -> SecretBootstrapResult:
    """
    Initialize runtime secrets with Infisical-first strategy.

    Behavior:
    - strict mode (default on VPS/standalone): fail closed if Infisical cannot load.
    - local mode: allow optional dotenv fallback and existing env-only startup.

    Parameters:
    - exclude_prefixes: tuple of key prefixes to skip when injecting secrets onto
      ``os.environ``. Used by the interactive `claude` launcher to prevent
      ``ANTHROPIC_*`` runtime keys (e.g. ``ANTHROPIC_API_KEY``) from polluting
      the env and overriding the Anthropic Max OAuth path. UA Python services
      that need those keys call this without ``exclude_prefixes``.
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
                # Infisical is single source of truth for application config.
                # We pass overwrite=True so values from the vault win over any
                # pre-existing os.environ entries (systemd Environment=, .env
                # bootstrap, module-import side effects). Bootstrap identity
                # keys are exempted via preserve_keys so the machine's
                # role/stage/slug can never be moved by remote config.
                loaded_count = _inject_environment_values(
                    secret_values,
                    overwrite=True,
                    exclude_prefixes=exclude_prefixes,
                    preserve_keys=_BOOTSTRAP_IDENTITY_KEYS,
                )
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
                loaded_count = _load_local_dotenv(exclude_prefixes=exclude_prefixes)
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

        # P7 (2026-05-21): install universal ZAI HTTP observability hooks.
        # Monkey-patches httpx.Client/AsyncClient __init__ so every outbound
        # request to api.z.ai gets captured into a rolling JSONL — closes
        # the P4 instrumentation gap where 8+ files bypassed ZAIRateLimiter.
        # Best-effort: failure here must NOT break runtime bootstrap.
        try:
            from universal_agent.services.zai_observability import (
                install_zai_observability,
            )
            install_zai_observability()
        except Exception:  # noqa: BLE001 — never break bootstrap over observability
            pass

        return result
