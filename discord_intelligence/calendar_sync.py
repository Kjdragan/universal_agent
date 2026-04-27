import asyncio
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil

from .database import DiscordIntelligenceDB


def _truthy_env(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def calendar_event_id(discord_event_id: str) -> str:
    # Google Calendar event ids allow lowercase letters a-v and digits; keep deterministic for dedupe.
    cleaned = re.sub(r"[^a-v0-9]", "", f"discord{discord_event_id}".lower())
    return cleaned[:512] or f"discord{abs(hash(discord_event_id))}"


def discord_event_url(event: dict) -> str:
    if event.get("discord_event_url"):
        return str(event["discord_event_url"])
    server_id = str(event.get("server_id") or "").strip()
    event_id = str(event.get("id") or "").strip()
    if server_id and event_id and not event_id.startswith("text_evt_"):
        return f"https://discord.com/events/{server_id}/{event_id}"
    return ""


def calendar_event_payload(event: dict) -> dict:
    event_id = calendar_event_id(str(event.get("id") or ""))
    discord_url = discord_event_url(event)
    description_parts = [
        str(event.get("description") or "").strip(),
        "",
        f"Discord event: {discord_url}" if discord_url else "",
        f"Discord server: {event.get('server_name') or event.get('server_id') or 'unknown'}",
        f"Discord channel/location: {event.get('channel_name') or event.get('location') or event.get('channel_id') or 'unknown'}",
        f"Discord event id: {event.get('id')}",
        "Source: discord_structured_event",
    ]
    payload = {
        "id": event_id,
        "summary": str(event.get("name") or "Discord Event").strip() or "Discord Event",
        "description": "\n".join(part for part in description_parts if part is not None).strip(),
        "start": {"dateTime": event["start_time"]},
        "end": {"dateTime": event.get("end_time") or event["start_time"]},
        "extendedProperties": {
            "private": {
                "source": "discord_structured_event",
                "discord_event_id": str(event.get("id") or ""),
                "discord_server_id": str(event.get("server_id") or ""),
                "discord_channel_id": str(event.get("channel_id") or ""),
            },
        },
    }
    if event.get("location"):
        payload["location"] = event["location"]
    elif discord_url:
        payload["location"] = discord_url
    return payload


def gws_command_prefix() -> list[str]:
    raw_command = os.getenv("UA_GWS_COMMAND", "").strip()
    if raw_command:
        return shlex.split(raw_command)

    configured_binary = os.getenv("UA_GWS_BINARY_PATH", "gws").strip() or "gws"
    if shutil.which(configured_binary):
        return [configured_binary]

    allow_npx = _truthy_env("UA_GWS_ALLOW_NPX_FALLBACK", "1")
    package_name = os.getenv("UA_GWS_NPX_PACKAGE", "@googleworkspace/cli").strip() or "@googleworkspace/cli"
    if configured_binary == "gws" and allow_npx and shutil.which("npx"):
        return ["npx", "-y", package_name]

    return [configured_binary]


def calendar_insert_command(payload: dict) -> list[str]:
    calendar_id = os.getenv("UA_DISCORD_CALENDAR_ID", "primary").strip() or "primary"
    return [
        *gws_command_prefix(),
        "calendar",
        "events",
        "insert",
        "--params",
        json.dumps({"calendarId": calendar_id}),
        "--json",
        json.dumps(payload),
    ]


def _materialize_binary_secret(env: dict, env_key: str, target_path: Path) -> bool:
    """Decode a base64-encoded Infisical secret and write it as a binary file.

    Returns True if the file was written/already up-to-date, False if nothing to do.
    """
    b64_value = env.get(env_key, "").strip()
    if not b64_value:
        return False
    raw_bytes = base64.b64decode(b64_value)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and target_path.read_bytes() == raw_bytes:
        return True  # already current
    target_path.write_bytes(raw_bytes)
    target_path.chmod(0o600)
    return True


def gws_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)

    # --- Materialize full encrypted gws credential suite from Infisical ---
    # The gws CLI stores credentials as AES-256-GCM encrypted files.  On a
    # headless VPS the OS keyring is unavailable, so we use the "file" backend
    # which reads the encryption key from ~/.config/gws/.encryption_key.
    # Four secrets are stored in Infisical as base64-encoded blobs:
    #   GWS_CREDENTIALS_ENC_B64    -> ~/.config/gws/credentials.enc
    #   GWS_TOKEN_CACHE_B64        -> ~/.config/gws/token_cache.json
    #   GWS_ENCRYPTION_KEY_B64     -> ~/.config/gws/.encryption_key
    #   GWS_CLIENT_SECRET_JSON_B64 -> ~/.config/gws/client_secret.json
    gws_dir = Path(env.get("UA_GWS_CONFIG_DIR", "~/.config/gws")).expanduser()
    _infisical_files = {
        "GWS_CREDENTIALS_ENC_B64": gws_dir / "credentials.enc",
        "GWS_TOKEN_CACHE_B64": gws_dir / "token_cache.json",
        "GWS_ENCRYPTION_KEY_B64": gws_dir / ".encryption_key",
        "GWS_CLIENT_SECRET_JSON_B64": gws_dir / "client_secret.json",
    }
    materialized_any = False
    for secret_key, target_path in _infisical_files.items():
        if _materialize_binary_secret(env, secret_key, target_path):
            materialized_any = True
        env.pop(secret_key, None)  # never leak raw blobs to subprocess

    # If we materialized encrypted credentials on a headless box, tell gws
    # to use the file-based keyring backend (reads .encryption_key on disk).
    if materialized_any and "GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND" not in env:
        env["GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND"] = "file"

    # --- Legacy plain-JSON credential path (kept for backwards compat) ---
    credentials_json = env.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_JSON", "").strip()
    credentials_b64 = env.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_JSON_B64", "").strip()
    if not env.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", "").strip() and (credentials_json or credentials_b64):
        if credentials_b64:
            credentials_json = base64.b64decode(credentials_b64).decode("utf-8")
        credentials_path = Path(
            env.get("UA_GWS_MATERIALIZED_CREDENTIALS_FILE", "~/.config/gws/credentials.from-infisical.json")
        ).expanduser()
        credentials_path.parent.mkdir(parents=True, exist_ok=True)
        if not credentials_path.exists() or credentials_path.read_text(encoding="utf-8") != credentials_json:
            credentials_path.write_text(credentials_json, encoding="utf-8")
            credentials_path.chmod(0o600)
        env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = str(credentials_path)
    env.pop("GOOGLE_WORKSPACE_CLI_CREDENTIALS_JSON", None)
    env.pop("GOOGLE_WORKSPACE_CLI_CREDENTIALS_JSON_B64", None)
    for key in (
        "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE",
        "GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER",
        "GOOGLE_WORKSPACE_CLI_TOKEN",
    ):
        if not env.get(key, "").strip():
            env.pop(key, None)
    return env


async def sync_event_to_calendar(db: DiscordIntelligenceDB, event: dict) -> tuple[bool, str]:
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        return False, "missing_event_id"
    payload = calendar_event_payload(event)
    cmd = calendar_insert_command(payload)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=gws_subprocess_env(),
        )
        stdout, stderr = await proc.communicate()
    except Exception as exc:
        db.mark_event_calendar_failed(event_id, str(exc))
        return False, str(exc)
    if proc.returncode == 0:
        db.mark_event_calendar_synced(
            event_id,
            str(payload["id"]),
            datetime.now(timezone.utc).isoformat(),
        )
        return True, stdout.decode(errors="ignore")[:500]
    error = stderr.decode(errors="ignore") or stdout.decode(errors="ignore")
    # Duplicate inserts may be reported as failure by the CLI even though the target event exists.
    if "already exists" in error.lower() or "duplicate" in error.lower() or "409" in error:
        db.mark_event_calendar_synced(
            event_id,
            str(payload["id"]),
            datetime.now(timezone.utc).isoformat(),
        )
        return True, "already_exists"
    db.mark_event_calendar_failed(event_id, error)
    return False, error[:500]
