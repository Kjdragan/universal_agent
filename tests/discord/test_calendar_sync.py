from discord_intelligence import calendar_sync


def test_calendar_payload_includes_discord_event_url():
    payload = calendar_sync.calendar_event_payload(
        {
            "id": "1492175827239174144",
            "server_id": "server-1",
            "name": "Office Hours",
            "description": "Bring questions",
            "start_time": "2026-04-14T13:00:00+00:00",
            "end_time": "2026-04-14T14:00:00+00:00",
            "channel_id": "channel-1",
            "server_name": "Example",
            "channel_name": "stage",
        }
    )

    assert "https://discord.com/events/server-1/1492175827239174144" in payload["description"]
    assert payload["location"] == "https://discord.com/events/server-1/1492175827239174144"
    assert payload["extendedProperties"]["private"]["source"] == "discord_structured_event"


def test_gws_command_prefix_uses_npx_fallback(monkeypatch):
    monkeypatch.delenv("UA_GWS_COMMAND", raising=False)
    monkeypatch.delenv("UA_GWS_BINARY_PATH", raising=False)
    monkeypatch.setenv("UA_GWS_ALLOW_NPX_FALLBACK", "1")
    monkeypatch.setattr(
        calendar_sync.shutil,
        "which",
        lambda binary: "/usr/bin/npx" if binary == "npx" else None,
    )

    assert calendar_sync.gws_command_prefix() == ["npx", "-y", "@googleworkspace/cli"]


def test_gws_command_prefix_prefers_explicit_command(monkeypatch):
    monkeypatch.setenv("UA_GWS_COMMAND", "npx -y @googleworkspace/cli")

    assert calendar_sync.gws_command_prefix() == ["npx", "-y", "@googleworkspace/cli"]


def test_calendar_insert_command_uses_events_insert_json(monkeypatch):
    monkeypatch.setenv("UA_GWS_COMMAND", "gws")
    monkeypatch.setenv("UA_DISCORD_CALENDAR_ID", "primary")
    payload = {"summary": "Office Hours"}

    cmd = calendar_sync.calendar_insert_command(payload)

    assert cmd[:4] == ["gws", "calendar", "events", "insert"]
    assert "--params" in cmd
    assert "--json" in cmd
    assert cmd[cmd.index("--json") + 1] == '{"summary": "Office Hours"}'


def test_gws_subprocess_env_removes_blank_credential_overrides(monkeypatch):
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE", "")
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER", "   ")
    monkeypatch.setenv("GOOGLE_WORKSPACE_CLI_TOKEN", "token-value")

    env = calendar_sync.gws_subprocess_env()

    assert "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE" not in env
    assert "GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER" not in env
    assert env["GOOGLE_WORKSPACE_CLI_TOKEN"] == "token-value"
