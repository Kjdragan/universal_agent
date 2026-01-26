# User Identity Handling & Divergence Report

## Overview

This document analyzes how User Identity (`user_id`) is handled across the three main entry points of the Universal Agent: **CLI**, **Gateway Server**, and **Agent Bridge**.

## Findings

There is a **divergence** in how `user_id` is resolved and defaulted across the codebase.

| Component | File | Resolution Logic | Fallback Default |
|-----------|------|------------------|------------------|
| **CLI** | `src/universal_agent/main.py` | Checks `COMPOSIO_USER_ID` or `DEFAULT_USER_ID` env vars. | `"unknown_user"` |
| **Gateway Server** | `src/universal_agent/gateway_server.py` | Pydantic model (`CreateSessionRequest`) default. **Does not check env vars.** | `"user_gateway"` |
| **Agent Bridge** | `src/universal_agent/api/agent_bridge.py` | Checks `COMPOSIO_USER_ID` or `DEFAULT_USER_ID` env vars. | `"user_ui"` |

## Impact

1.  **Inconsistent Session Metadata**: Runs triggered via CLI will be owned by `unknown_user` (if env not set), while runs via API/UI will be owned by `user_gateway` or `user_ui`.
2.  **Authentication/Tool Instability**: If `user_id` is used to look up Composio connections:
    - CLI might fail or use a different set of connections than the UI.
    - Gateway Server bypasses the environment variable check entirely if it relies solely on the Pydantic default.

## Recommendations

1.  **Unified Resolution Logic**:
    Extract the identity resolution logic into a single shared utility function (e.g., in `universal_agent.identity`) that all entry points use.

    ```python
    def resolve_user_id(requested_id: Optional[str] = None) -> str:
        if requested_id:
            return requested_id
        return (
            os.getenv("COMPOSIO_USER_ID") 
            or os.getenv("DEFAULT_USER_ID") 
            or "user_universal"  # Single authoritative default
        )
    ```

2.  **Update Gateway Server**:
    Modify `gateway_server.py` to use this resolver instead of a hardcoded string default in the Pydantic model (or use `None` as default and resolve inside the endpoint).

3.  **Update CLI & Bridge**:
    Refactor `main.py` and `agent_bridge.py` to use the shared resolver.
