# User and Account Management

## Overview
The Universal Agent uses **Composio** to manage authentication and integrations (GitHub, Linear, Notion, etc.). While Composio supports multiple user entities, this project is architected to use a **Single Consolidated Identity** to ensure the agent always has access to the full suite of configured tools.

## core Identity
The agent effectively runs as a specific "Master User" who holds all the OAuth tokens.

- **Entity ID**: `pg-test-86524ebc-9b1e-4f08-bd20-b77dd71c2df9`
- **Role**: Primary Admin / Agent Identity
- **Configured In**: `src/universal_agent/main.py`

> [!IMPORTANT]
> ANY new integration (e.g., adding Outlook, Slack) MUST be authenticated under this specific Entity ID. If you create a new user (e.g., `user_456`), the agent running as `pg-test...` will NOT see those tools.

## How to Add New Tools
To add a new tool so the agent can use it:

1.  **Use the CLI**: using the `composio` CLI is the easiest way.
2.  **Specify the User**: You must associate the connection with the master ID.
    ```bash
    composio add <tool_name> -e "pg-test-86524ebc-9b1e-4f08-bd20-b77dd71c2df9"
    ```
3.  **Verify**: Restart the agent (`uv run src/universal_agent/main.py`). The new tool should appear in the "Active Composio Apps" startup log.

## Troubleshooting Logic
If the agent says "Tool X is not available" or you see different tools than expected:

1.  **Check Discovery Log**: Look at the agent startup output:
    ```
    ✅ Discovered Active Composio Apps: ['github', 'linear', ...]
    ```
    If your tool isn't there, it's not connected to the agent's user entity.

2.  **Audit Connections**:
    We use `src/universal_agent/utils/composio_discovery.py` to inspect available toolkits. The internal logic filters for `is_active` connections associated with the current session's user.

3.  **"Unknown" Users**:
    If you authenticating via the dashboard without specifying a user, Composio might assign a default/random UUID. You can find this UUID by inspecting the connection object in the dashboard or via API audit scripts, and then you must re-authenticate correctly under the Master ID.

## Automated Tool Onboarding
We have provided a helper script to simplify connecting key tools to the Master User identity.

**Script**: `onboard_tools.py`

**Usage**:
```bash
uv run onboard_tools.py
```

**Functionality**:
1. Checks connection status for Gmail, Google Calendar, GitHub, and Slack for the configured `PRIMARY_USER_ID`.
2. If any tool is missing, it automatically generates a **Connect Link**.
3. You simply click the link, authorize in the browser, and the tool is legally connected to the agent's identity.

Run this script whenever you need to add these core integrations or checking connection health.
