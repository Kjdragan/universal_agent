"""
Universal Agent API Server - FastAPI + WebSocket for the UI.

Serves the HTML UI and provides a WebSocket endpoint for real-time agent interaction.
"""

import asyncio
import json
import os
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, Response

from .agent_core import UniversalAgent, AgentEvent, EventType, configure_logfire

# Configure logfire on import
configure_logfire()

app = FastAPI(title="Universal Agent", version="0.1.0")

# Path to the UI HTML
UI_PATH = Path(__file__).parent.parent.parent / "universal_agent_ui.html"

# Track the current agent's workspace for file browsing
_current_workspace: Optional[str] = None

# Project root directory (where AGENT_RUN_WORKSPACES lives)
PROJECT_ROOT = Path(__file__).parent.parent.parent


def get_latest_workspace() -> Optional[str]:
    """Get the latest session workspace directory."""
    global _current_workspace
    if _current_workspace and os.path.exists(_current_workspace):
        return _current_workspace

    # Fallback: find the most recent session using absolute path
    workspaces_dir = PROJECT_ROOT / "AGENT_RUN_WORKSPACES"
    if not workspaces_dir.exists():
        return None

    sessions = sorted(workspaces_dir.iterdir(), reverse=True)
    if sessions:
        return str(sessions[0])
    return None


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    """Serve the main UI HTML page."""
    if UI_PATH.exists():
        return FileResponse(UI_PATH)
    return HTMLResponse("<h1>UI not found</h1>", status_code=404)


@app.get("/api/workspace")
async def get_workspace_info():
    """Get current workspace path."""
    workspace = get_latest_workspace()
    return {"workspace": workspace}


@app.get("/api/files")
async def list_files(path: str = ""):
    """List files in the workspace directory."""
    workspace = get_latest_workspace()
    if not workspace:
        return {"files": [], "error": "No workspace available"}

    # Resolve the path within workspace
    if path:
        target = Path(workspace) / path
    else:
        target = Path(workspace)

    # Security: ensure path is within workspace
    try:
        target = target.resolve()
        workspace_path = Path(workspace).resolve()
        if not str(target).startswith(str(workspace_path)):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target.exists():
        return {"files": [], "path": path}

    if target.is_file():
        return {"files": [], "path": path, "is_file": True}

    files = []
    for item in sorted(target.iterdir()):
        stat = item.stat()
        files.append(
            {
                "name": item.name,
                "path": str(item.relative_to(workspace_path)),
                "is_dir": item.is_dir(),
                "size": stat.st_size if item.is_file() else None,
                "children": len(list(item.iterdir())) if item.is_dir() else None,
            }
        )

    return {"files": files, "path": path, "workspace": workspace}


@app.get("/api/file/{file_path:path}")
async def read_file(file_path: str):
    """Read a file from the workspace."""
    workspace = get_latest_workspace()
    if not workspace:
        raise HTTPException(status_code=404, detail="No workspace available")

    target = (Path(workspace) / file_path).resolve()
    workspace_path = Path(workspace).resolve()

    # Security check
    if not str(target).startswith(str(workspace_path)):
        raise HTTPException(status_code=403, detail="Access denied")

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if target.is_dir():
        raise HTTPException(status_code=400, detail="Cannot read directory")

    # Detect content type
    mime_type, _ = mimetypes.guess_type(str(target))

    # For HTML files, return the content to render in iframe
    if target.suffix == ".html":
        return FileResponse(target, media_type="text/html")

    # For JSON, return as JSON
    if target.suffix == ".json":
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            return JSONResponse(data)
        except Exception as e:
            return Response(content=str(e), media_type="text/plain")

    # For text files, return content
    if target.suffix in [".txt", ".md", ".log", ".py", ".js", ".css"]:
        with open(target, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(content=content, media_type="text/plain")

    # For other files, serve as download
    return FileResponse(target)


@app.post("/api/save_summary")
async def save_summary(request: Request):
    """Save agent conversation summary as work product."""
    workspace = get_latest_workspace()
    if not workspace:
        return {"error": "No workspace available"}

    try:
        data = await request.json()
        summary = data.get("summary", "")
        if not summary or len(summary) < 100:
            return {"error": "Summary too short"}

        # Create work_products directory if needed
        work_products = Path(workspace) / "work_products"
        work_products.mkdir(exist_ok=True)

        # Generate filename with timestamp
        from datetime import datetime

        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"agent_summary_{timestamp}.md"
        filepath = work_products / filename

        # Write formatted summary
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# Agent Session Summary\n\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("---\n\n")
            f.write(summary)

        return {"path": str(filepath), "filename": filename}

    except Exception as e:
        return {"error": str(e)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time agent communication.

    Protocol:
    - Client sends: {"type": "query", "text": "..."}
    - Server sends: AgentEvent objects as JSON
    """
    global _current_workspace
    await websocket.accept()

    # Create agent instance for this connection
    agent = UniversalAgent()

    try:
        # Initialize and send session info
        await agent.initialize()
        _current_workspace = agent.workspace_dir  # Track for file browser
        session_info = agent.get_session_info()
        await websocket.send_json({"type": "session_info", "data": session_info})

        # Main message loop
        while True:
            # Wait for client message
            data = await websocket.receive_text()
            message = json.loads(data)

            if message.get("type") == "query":
                query = message.get("text", "")
                if not query:
                    continue

                # Stream agent events back to client
                async for event in agent.run_query(query):
                    await websocket.send_json(
                        {
                            "type": event.type.value,
                            "data": event.data,
                            "timestamp": event.timestamp,
                        }
                    )

                # Check for and send execution summary if available
                if _current_workspace:
                    summary_path = os.path.join(_current_workspace, "session_summary.txt")
                    if os.path.exists(summary_path):
                        try:
                            with open(summary_path, "r", encoding="utf-8") as f:
                                summary_content = f.read()
                            
                            if summary_content:
                                # Send as a distinct agent response event so it renders in the chat
                                await websocket.send_json(
                                    {
                                        "type": "agent_response",
                                        "data": {"text": summary_content},
                                        "timestamp": asyncio.get_event_loop().time(),
                                    }
                                )
                        except Exception as e:
                            print(f"⚠️ Failed to read session summary: {e}")

                # Signal query complete
                await websocket.send_json({"type": "query_complete", "data": {}})

            elif message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        print(f"WebSocket disconnected for workspace: {agent.workspace_dir}")
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": {"message": str(e)}})
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
