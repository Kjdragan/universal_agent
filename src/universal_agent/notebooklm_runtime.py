from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Research import timeout (v0.5.0+: configurable, was hard 120s)
# ---------------------------------------------------------------------------

_DEFAULT_RESEARCH_IMPORT_TIMEOUT = 1200  # seconds


def notebooklm_research_import_timeout() -> int:
    """Return the configured research-import timeout in seconds.

    Reads ``UA_NOTEBOOKLM_RESEARCH_IMPORT_TIMEOUT`` from the environment,
    defaulting to 1200 s.  The NLM CLI accepts this via ``--timeout``.
    """
    raw = str(os.getenv("UA_NOTEBOOKLM_RESEARCH_IMPORT_TIMEOUT", "")).strip()
    if raw:
        try:
            return max(60, int(raw))
        except (ValueError, TypeError):
            pass
    return _DEFAULT_RESEARCH_IMPORT_TIMEOUT


# ---------------------------------------------------------------------------
# MCP error-hint parsing (v0.5.3+: all 27 MCP tools return hint fields)
# ---------------------------------------------------------------------------


def parse_nlm_error_hint(error_response: dict[str, Any] | str) -> str | None:
    """Extract the ``hint`` field from an NLM MCP/CLI JSON error response.

    v0.5.3 introduced structured error responses:
    ``{"status": "error", "error": "...", "hint": "..."}``

    Returns the hint string, or ``None`` if not present.
    """
    if isinstance(error_response, str):
        try:
            error_response = json.loads(error_response)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(error_response, dict):
        return None
    hint = error_response.get("hint")
    return str(hint).strip() if hint else None


def is_auth_hint(hint: str | None) -> bool:
    """Return True if *hint* suggests re-authentication is needed.

    Useful for agent self-correction: detect auth-related hints and
    trigger ``nlm login`` automatically.
    """
    if not hint:
        return False
    lowered = hint.lower()
    return any(
        marker in lowered
        for marker in ("nlm login", "authenticate", "auth", "expired", "re-auth")
    )


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return default


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}"


def notebooklm_profile() -> str:
    profile = (
        str(os.getenv("UA_NOTEBOOKLM_PROFILE") or "").strip()
        or str(os.getenv("NOTEBOOKLM_PROFILE") or "").strip()
        or "vps"
    )
    return profile


def notebooklm_cli_command() -> str:
    return str(os.getenv("UA_NOTEBOOKLM_CLI_COMMAND") or "").strip() or "nlm"


def notebooklm_mcp_command() -> str:
    return str(os.getenv("UA_NOTEBOOKLM_MCP_COMMAND") or "").strip() or "notebooklm-mcp"


def notebooklm_mcp_enabled() -> bool:
    return _env_flag("UA_ENABLE_NOTEBOOKLM_MCP", default=False)


def notebooklm_auth_seed_enabled(profile: str | None = None) -> bool:
    resolved = profile or notebooklm_profile()
    deployment_profile = str(os.getenv("UA_DEPLOYMENT_PROFILE") or "").strip().lower()

    # VPS/standalone defaults to enabled, local defaults to off unless explicitly set.
    # If deployment profile is missing, infer from NotebookLM profile (`vps` => enabled).
    if deployment_profile:
        profile_default = deployment_profile in {"vps", "standalone_node"}
    else:
        profile_default = str(resolved).strip().lower() == "vps"

    if resolved and str(resolved).strip().lower() != "vps":
        profile_default = False
    return _env_flag("UA_NOTEBOOKLM_AUTH_SEED_ENABLED", default=profile_default)


@dataclass(frozen=True)
class NotebookLMAuthPreflightResult:
    ok: bool
    profile: str
    seeded: bool
    refreshed: bool
    command_path: str
    checks_attempted: int
    errors: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)


def _run_command(args: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _write_seed_file(workspace_dir: str, cookie_header: str) -> str:
    workspace = Path(workspace_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    fd, path = tempfile.mkstemp(prefix="notebooklm-seed-", suffix=".txt", dir=str(workspace))
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        os.write(fd, cookie_header.encode("utf-8"))
        os.write(fd, b"\n")
    finally:
        os.close(fd)
    return path


def run_auth_preflight(
    workspace_dir: str,
    *,
    timeout_seconds: float = 45.0,
) -> NotebookLMAuthPreflightResult:
    profile = notebooklm_profile()
    cli = notebooklm_cli_command()
    seed_enabled = notebooklm_auth_seed_enabled(profile)
    cookie_header = str(os.getenv("NOTEBOOKLM_AUTH_COOKIE_HEADER") or "").strip()

    errors: list[str] = []
    notes: list[str] = []
    checks_attempted = 0
    seeded = False
    refreshed = False

    cli_exists = bool(Path(cli).exists()) if os.path.sep in cli else bool(shutil.which(cli))
    if not cli_exists:
        notes.append("cli_missing")
        errors.append("auth_cli_missing:FileNotFoundError")
        if not seed_enabled:
            notes.append("seed_disabled")
        if not cookie_header:
            notes.append("seed_cookie_missing")
        return NotebookLMAuthPreflightResult(
            ok=False,
            profile=profile,
            seeded=False,
            refreshed=False,
            command_path=cli,
            checks_attempted=checks_attempted,
            notes=tuple(notes),
            errors=tuple(errors),
        )

    # v0.5.4+: prefer 'nlm auth status' — makes a real API call to
    # validate credentials, more reliable than the old 'login --check'.
    check_args = [cli, "auth", "status", "--profile", profile]
    try:
        checks_attempted += 1
        check_result = _run_command(check_args, timeout_seconds)
        if check_result.returncode == 0:
            return NotebookLMAuthPreflightResult(
                ok=True,
                profile=profile,
                seeded=False,
                refreshed=False,
                command_path=cli,
                checks_attempted=checks_attempted,
                notes=("auth_check_passed",),
            )
        notes.append("auth_check_failed_initial")
    except Exception as exc:
        errors.append(f"auth_check_initial:{_safe_error(exc)}")

    if seed_enabled and cookie_header:
        seed_file: str | None = None
        try:
            seed_file = _write_seed_file(workspace_dir, cookie_header)
            seed_args = [cli, "login", "--manual", "--file", seed_file, "--profile", profile]
            seed_result = _run_command(seed_args, timeout_seconds)
            if seed_result.returncode == 0:
                seeded = True
                refreshed = True
                notes.append("seed_applied")
            else:
                notes.append("seed_apply_failed")
                errors.append("auth_seed:nonzero_exit")
        except Exception as exc:
            errors.append(f"auth_seed:{_safe_error(exc)}")
        finally:
            if seed_file:
                try:
                    Path(seed_file).unlink(missing_ok=True)
                except Exception as exc:
                    errors.append(f"seed_cleanup:{_safe_error(exc)}")
    else:
        if not seed_enabled:
            notes.append("seed_disabled")
        if not cookie_header:
            notes.append("seed_cookie_missing")

    try:
        checks_attempted += 1
        check_result = _run_command(check_args, timeout_seconds)
        if check_result.returncode == 0:
            return NotebookLMAuthPreflightResult(
                ok=True,
                profile=profile,
                seeded=seeded,
                refreshed=refreshed,
                command_path=cli,
                checks_attempted=checks_attempted,
                notes=tuple(notes + ["auth_check_passed_final"]),
                errors=tuple(errors),
            )
        notes.append("auth_check_failed_final")
    except Exception as exc:
        errors.append(f"auth_check_final:{_safe_error(exc)}")

    return NotebookLMAuthPreflightResult(
        ok=False,
        profile=profile,
        seeded=seeded,
        refreshed=refreshed,
        command_path=cli,
        checks_attempted=checks_attempted,
        notes=tuple(notes),
        errors=tuple(errors),
    )


def build_notebooklm_mcp_server_config() -> dict[str, Any] | None:
    if not notebooklm_mcp_enabled():
        return None

    profile = notebooklm_profile()
    command = notebooklm_mcp_command()

    env_keys = [
        "NOTEBOOKLM_MCP_TRANSPORT",
        "NOTEBOOKLM_MCP_HOST",
        "NOTEBOOKLM_MCP_PORT",
        "NOTEBOOKLM_MCP_PATH",
        "NOTEBOOKLM_MCP_STATELESS",
        "NOTEBOOKLM_MCP_DEBUG",
        "NOTEBOOKLM_HL",
        "NOTEBOOKLM_QUERY_TIMEOUT",
        "NOTEBOOKLM_BL",
    ]

    env_payload: dict[str, str] = {}
    for key in env_keys:
        value = os.getenv(key)
        if value is not None and str(value).strip() != "":
            env_payload[key] = str(value)

    env_payload["NOTEBOOKLM_PROFILE"] = profile

    return {
        "type": "stdio",
        "command": command,
        "args": [],
        "env": env_payload,
    }


# ---------------------------------------------------------------------------
# nlm doctor diagnostic (v0.5.3+)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NLMDoctorResult:
    """Structured result from ``nlm doctor``."""
    ok: bool
    auth_ok: bool | None = None
    browser_ok: bool | None = None
    raw_output: str = ""
    errors: tuple[str, ...] = field(default_factory=tuple)


def run_nlm_doctor(
    *,
    timeout_seconds: float = 30.0,
) -> NLMDoctorResult:
    """Run ``nlm doctor`` and return structured diagnostics.

    Useful for healthcheck / heartbeat integration.  Falls back to
    text-parsing if ``--json`` output is unavailable.
    """
    cli = notebooklm_cli_command()
    cli_exists = bool(Path(cli).exists()) if os.path.sep in cli else bool(shutil.which(cli))
    if not cli_exists:
        return NLMDoctorResult(
            ok=False,
            errors=("nlm_cli_not_found",),
        )

    try:
        result = _run_command([cli, "doctor"], timeout_seconds)
        raw = (result.stdout or "") + (result.stderr or "")
        # Parse output for key indicators
        auth_ok = None
        browser_ok = None
        lowered = raw.lower()
        if "✓ authenticated" in lowered or "authenticated" in lowered:
            auth_ok = True
        elif "✗ auth" in lowered or "not authenticated" in lowered:
            auth_ok = False
        if "✓ browser" in lowered or "browser profile" in lowered:
            browser_ok = True
        elif "✗ browser" in lowered:
            browser_ok = False

        return NLMDoctorResult(
            ok=result.returncode == 0,
            auth_ok=auth_ok,
            browser_ok=browser_ok,
            raw_output=raw.strip(),
        )
    except subprocess.TimeoutExpired:
        return NLMDoctorResult(
            ok=False,
            errors=("nlm_doctor_timeout",),
        )
    except Exception as exc:
        return NLMDoctorResult(
            ok=False,
            errors=(f"nlm_doctor_error:{_safe_error(exc)}",),
        )
