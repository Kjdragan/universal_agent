# Webhook Service Implementation

**Date:** 2026-02-10
**Status:** Implemented

## Overview

This document details the implementation of the `HooksService` in the Universal Agent gateway. This service enables the agent to receive external webhook events (e.g., from GitHub, Linear, Stripe) and autonomously trigger agent actions in response.

This implementation is inspired by `clawdbot`'s webhook system but adapted for the Universal Agent's Python-based `gateway_server`.

## Architecture

The system consists of the following components:

1. **`HooksService`**: A new service class in `universal_agent.hooks_service`.
    * Loads configuration.
    * Authenticates requests.
    * Matches requests to configured mappings.
    * Executes optional Python-based "Transforms".
    * Dispatches actions to the `InProcessGateway`.

2. **`GatewayServer` Integration**:
    * The service is initialized in `gateway_server.py`'s `lifespan`.
    * A new endpoint `POST /api/v1/hooks/{subpath:path}` routes requests to the service.

3. **Configuration**:
    * Managed via `ops_config` (schema updated).
    * Supports `UA_HOOKS_ENABLED` and `UA_HOOKS_TOKEN` environment variables.

## Configuration Guide

Webhooks are configured in `ops_config.json` under the `hooks` key.

```json
{
  "hooks": {
    "enabled": true,
    "token": "secret-token",
    "base_path": "/api/v1/hooks",
    "max_body_bytes": 1048576,
    "transforms_dir": "transforms",
    "mappings": [
      {
        "id": "github-pr",
        "match": {
          "path": "github/pr",
          "source": "github",
          "headers": {
            "x-github-event": "pull_request"
          }
        },
        "action": "agent",
        "message_template": "New PR {{ payload.pull_request.number }}: {{ payload.pull_request.title }}",
        "name": "GitHubBot",
        "session_key": "gh-pr-{{ payload.pull_request.id }}",
        "transform": {
            "module": "github_verifier.py",
            "export": "verify_and_transform"
        }
      }
    ]
  }
}
```

### Transforms

Transforms are Python scripts that allow for complex logic, such as signature verification or payload restructuring.

**Example Transform (`github_verifier.py`):**

```python
import hmac
import hashlib

def verify_and_transform(ctx):
    # Verify HMAC signature here...
    if not valid:
        return None  # Block the request
    
    # Return overrides for the action
    return {
        "message": f"PR {ctx['payload']['number']} opened by {ctx['payload']['user']['login']}"
    }
```

## Security

1. **Authentication**: All webhook requests must ideally include the `Authorization: Bearer <token>` header matching the configured token.
    * *Note*: The service supports open webhooks if the token is not enforced, but it is highly recommended to use the token or a Transform for verification.
2. **Validation**: Transforms allow for provider-specific validation (e.g., HMAC signatures) to ensure request integrity.
3. **Isolation**: Transforms run in the main process but are loaded dynamically. Standard code review practices apply to transform scripts.

## Verification

The implementation has been verified via:

1. **Unit Tests**: `tests/test_hooks_service.py` covers configuration loading, request matching, template rendering, and action dispatch.
2. **Manual Testing**: Verified using `curl` to simulate webhook events and observing agent execution.

## Next Steps

1. **Create Standard Transforms**: Develop a library of standard transforms for common services (GitHub, Linear, Slack).
2. **Dashboard Integration**: Add a UI in the dashboard to view and manage webhook configurations.
3. **Observability**: Add specific metrics for webhook throughput and failure rates.
4. **Composio Trigger Ingress**: Implement Composio webhook signature verification, central subscription routing, and YouTube automation fallback paths per `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/16_Composio_Trigger_Ingress_And_Youtube_Automation_Plan_2026-02-10.md`.
