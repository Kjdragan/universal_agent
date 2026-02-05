# Composio Integration

## Overview
Reliable, authorized interaction with external services (SaaS) is handled via **Composio**. Unlike brittle web scraping or hard-coded API wrappers, Composio provides a managed authentication layer and a standardized "Tool" interface for the agent.

## Why Composio?
1.  **Auth Management**: It handles OAuth flows (e.g., "Login with GitHub") so the agent doesn't have to juggle raw tokens or handle 2FA challenges directly in the chat loop.
2.  **Schema Standardization**: It exposes external APIs (e.g., `github.issues.create`) as standard function-calling schemas that the LLM can understand and predict reliably.

## Configuration
To enable Composio tools:
1.  **API Key**: Ensure `COMPOSIO_API_KEY` is set in your `.env` file.
2.  **Connection**: The user must authorize the specific apps via the Composio dashboard or CLI.
    *   Example: `composio add github`
3.  **Agent Discovery**: On startup, the `ToolRegistry` queries Composio for active connections and dynamically adds those tools to the agent's definition.

## Supported Integrations
While Composio supports many apps, the Universal Agent is currently optimized for:
*   **GitHub**: Repository management, issue tracking, PR reviews.
*   **Slack/Discord**: Notifications and team communication.
*   **Google Workspace**: Calendar and Drive access for personal assistant tasks.

## Security Note
The Execution Engine treats Composio tools as "High Privilege".
*   Destructive actions (e.g., `delete_repo`) may require an additional confirmation step from the user, depending on the `SafeToAutoRun` configuration in the workflow.
