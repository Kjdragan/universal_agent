from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_VALID_DEPLOYMENT_PROFILES = {"local_workstation", "standalone_node", "vps"}
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAP_RESULT: SecretBootstrapResult | None = None


@dataclass(frozen=True)
class SecretBootstrapResult:
    ok: bool
    source: str
    strict_mode: bool
    loaded_count: int
    fallback_used: bool
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


def _fetch_infisical_secrets() -> dict[str, str]:
    client_id = str(os.getenv("INFISICAL_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("INFISICAL_CLIENT_SECRET") or "").strip()
    project_id = str(os.getenv("INFISICAL_PROJECT_ID") or "").strip()
    environment = str(os.getenv("INFISICAL_ENVIRONMENT") or "dev").strip() or "dev"
    secret_path = str(os.getenv("INFISICAL_SECRET_PATH") or "/").strip() or "/"

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

    from infisical_client import (
        AuthenticationOptions,
        ClientSettings,
        InfisicalClient,
        ListSecretsOptions,
        UniversalAuthMethod,
    )

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
                logger.info(
                    "Infisical runtime secret bootstrap succeeded: profile=%s loaded=%d",
                    resolved_profile,
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
            errors=tuple(errors),
        )
        _BOOTSTRAP_RESULT = result
        return result
