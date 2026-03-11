"""
Promptfoo custom provider for Universal Agent Gateway.

This is a lightweight, standalone script that communicates with the UA Gateway
via HTTP (session creation) and WebSocket (prompt execution).
It does NOT import the main universal_agent package, so it can run outside the
heavy .venv if needed — only `requests` and `websocket-client` are required.

Promptfoo calls  call_api(prompt, options, context)  for each adversarial prompt.
"""

import json
import os
import sys
import time

try:
    import requests
except ImportError:
    # Fallback: try urllib if requests not available
    requests = None

try:
    import websocket  # websocket-client package
except ImportError:
    websocket = None


GATEWAY_URL = os.getenv("UA_GATEWAY_URL", "http://localhost:8002")
GATEWAY_TOKEN = os.getenv(
    "UA_INTERNAL_API_TOKEN",
    os.getenv("UA_OPS_TOKEN", ""),
)
TIMEOUT_SECONDS = int(os.getenv("PROMPTFOO_TIMEOUT", "120"))


def _headers():
    h = {"Content-Type": "application/json"}
    if GATEWAY_TOKEN:
        h["Authorization"] = f"Bearer {GATEWAY_TOKEN}"
    return h


def _create_session():
    """Create a fresh isolated session via REST API."""
    url = f"{GATEWAY_URL}/api/v1/sessions"
    payload = {"user_id": "promptfoo_redteamer"}

    if requests:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()
    else:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers=_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())


def _run_prompt_via_ws(session_id, prompt):
    """Connect via WebSocket, send the prompt, and collect the full text response."""
    if websocket is None:
        raise RuntimeError(
            "websocket-client is required. Install with: pip install websocket-client"
        )

    ws_base = GATEWAY_URL.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{ws_base}/api/v1/sessions/{session_id}/stream"

    header_list = []
    if GATEWAY_TOKEN:
        header_list.append(f"Authorization: Bearer {GATEWAY_TOKEN}")

    ws = websocket.create_connection(ws_url, header=header_list, timeout=TIMEOUT_SECONDS)

    try:
        # 1. Wait for the "connected" handshake
        connected_raw = ws.recv()
        connected = json.loads(connected_raw)
        if connected.get("type") != "connected":
            raise RuntimeError(f"Expected 'connected', got: {connected}")

        # 2. Send the execute message with the adversarial prompt
        execute_msg = json.dumps({
            "type": "execute",
            "data": {
                "user_input": prompt,
                "force_complex": False,
                "metadata": {"source": "promptfoo_redteam"},
            },
        })
        ws.send(execute_msg)

        # 3. Collect response events
        response_text = ""
        tool_calls = 0
        start = time.time()

        while True:
            if time.time() - start > TIMEOUT_SECONDS:
                break
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                break

            data = json.loads(raw)
            event_type = data.get("type", "")

            if event_type == "query_complete":
                break
            elif event_type == "error":
                err = data.get("data", {})
                err_msg = err.get("message") or err.get("error") or "Unknown error"
                raise RuntimeError(f"Gateway error: {err_msg}")
            elif event_type == "text":
                chunk = data.get("data", {})
                if isinstance(chunk, dict):
                    response_text += chunk.get("text", "")
                elif isinstance(chunk, str):
                    response_text += chunk
            elif event_type == "tool_start":
                tool_calls += 1

        return response_text.strip(), tool_calls

    finally:
        ws.close()


def call_api(prompt, options, context):
    """
    Promptfoo provider entry point.
    Returns { "output": str } on success, or { "error": str } on failure.
    """
    try:
        # Create an isolated session
        session_data = _create_session()
        session_id = session_data.get("session_id")
        if not session_id:
            return {"error": f"No session_id in response: {session_data}"}

        # Send prompt and collect response
        response_text, tool_calls = _run_prompt_via_ws(session_id, prompt)

        return {
            "output": response_text or "(no response)",
            "metadata": {
                "session_id": session_id,
                "tool_calls": tool_calls,
            },
        }

    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
