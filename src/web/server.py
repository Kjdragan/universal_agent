"""
Universal Agent Web Server
FastAPI backend with WebSocket for real-time agent interaction.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ============================================================================
# Configuration
# ============================================================================

BASE_DIR = Path(__file__).parent.parent.parent
STATIC_DIR = BASE_DIR / "static"
WORKSPACES_DIR = BASE_DIR / "AGENT_RUN_WORKSPACES"

# ============================================================================
# Models
# ============================================================================

class ChatMessage(BaseModel):
    role: str  # "user" or "agent"
    content: str
    timestamp: str
    tool_calls: Optional[list] = None

class SessionInfo(BaseModel):
    session_id: str
    timestamp: str
    workspace_path: str
    status: str

# ============================================================================
# Connection Manager for WebSocket
# ============================================================================

class ConnectionManager:
    """Manages WebSocket connections for real-time communication."""
    
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def send_message(self, websocket: WebSocket, message: dict):
        await websocket.send_json(message)
    
    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

# ============================================================================
# Agent Execution Bridge
# ============================================================================

class AgentBridge:
    """Bridge between web server and agent execution."""
    
    def __init__(self):
        self.current_session: Optional[str] = None
        self.is_processing = False
    
    async def execute_query(self, query: str, websocket: WebSocket) -> None:
        """Execute agent query and stream results to WebSocket."""
        import subprocess
        
        self.is_processing = True
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_session = f"session_{timestamp}"
        
        # Notify client that processing started
        await manager.send_message(websocket, {
            "type": "status",
            "status": "processing",
            "session_id": self.current_session
        })
        
        try:
            # Run agent as subprocess and capture output
            process = await asyncio.create_subprocess_exec(
                "uv", "run", "src/universal_agent/main.py",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(BASE_DIR)
            )
            
            # Send query to agent
            process.stdin.write(f"{query}\nquit\n".encode())
            await process.stdin.drain()
            process.stdin.close()
            
            # Stream output
            buffer = ""
            while True:
                chunk = await process.stdout.read(1024)
                if not chunk:
                    break
                
                text = chunk.decode('utf-8', errors='replace')
                buffer += text
                
                # Parse and send tool calls
                await self._parse_and_send_output(websocket, text, buffer)
            
            await process.wait()
            
            # Send completion
            await manager.send_message(websocket, {
                "type": "query_complete",
                "data": {
                    "session_id": self.current_session,
                    "workspace": str(WORKSPACES_DIR / self.current_session)
                }
            })
            
        except Exception as e:
            await manager.send_message(websocket, {
                "type": "error",
                "message": str(e)
            })
        finally:
            self.is_processing = False
    
    async def _parse_and_send_output(self, websocket: WebSocket, chunk: str, full_buffer: str):
        """Parse agent output and send structured messages."""
        import re
        
        # Detect tool calls (format: 🔧 [tool_name] +12.5s)
        tool_call_match = re.search(r'[🔧🏭]\s*(?:CODE EXECUTION\s*)?\[([^\]]+)\]\s*\+[\d.]+s', chunk)
        if tool_call_match:
            tool_name = tool_call_match.group(1)
            # Extract input if present
            input_match = re.search(r'Input:\s*(\{[^}]+\})', chunk, re.DOTALL)
            input_data = None
            if input_match:
                try:
                    input_data = input_match.group(1)[:500]  # Truncate
                except:
                    pass
            
            await manager.send_message(websocket, {
                "type": "tool_call",
                "data": {
                    "name": tool_name,
                    "input": input_data
                }
            })
        
        # Detect tool results (format: 📦 Tool Result (1234 bytes) +12.5s)
        result_match = re.search(r'📦 Tool Result \((\d+) bytes\)', chunk)
        if result_match:
            bytes_count = result_match.group(1)
            # Extract preview if present
            preview_match = re.search(r'Preview:\s*(.+?)(?:\n|$)', chunk)
            preview = preview_match.group(1)[:200] if preview_match else f"{bytes_count} bytes"
            
            await manager.send_message(websocket, {
                "type": "tool_result",
                "data": {
                    "content_preview": preview,
                    "is_error": False
                }
            })
        
        # Detect agent text output (not tool-related lines)
        if not tool_call_match and not result_match:
            # Filter out noise lines
            noise_patterns = [
                r'^[\s]*$',
                r'^\[\d+\]',
                r'^INFO:',
                r'^WARNING:',
                r'^✅',
                r'^={10,}',
                r'^🤖 Enter your request',
                r'^Query Classification',
                r'^Initializing Agent',
            ]
            
            lines = chunk.split('\n')
            text_lines = []
            for line in lines:
                is_noise = any(re.match(p, line) for p in noise_patterns)
                if not is_noise and line.strip():
                    text_lines.append(line)
            
            if text_lines:
                await manager.send_message(websocket, {
                    "type": "text",
                    "data": {
                        "text": '\n'.join(text_lines)
                    }
                })

agent_bridge = AgentBridge()

# ============================================================================
# FastAPI App
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    print("🚀 Universal Agent Web Server starting...")
    print(f"📁 Static files: {STATIC_DIR}")
    print(f"📂 Workspaces: {WORKSPACES_DIR}")
    yield
    print("👋 Server shutting down...")

app = FastAPI(
    title="Universal Agent",
    description="AGI-Era Neural Command Center",
    version="2.0.0",
    lifespan=lifespan
)

# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ============================================================================
# Routes
# ============================================================================

@app.get("/")
async def root():
    """Serve main UI."""
    ui_path = BASE_DIR / "universal_agent_ui.html"
    if ui_path.exists():
        return FileResponse(ui_path)
    return JSONResponse({"error": "UI file not found"}, status_code=404)


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0"
    }


@app.get("/api/sessions")
async def list_sessions():
    """List all agent sessions."""
    sessions = []
    
    if WORKSPACES_DIR.exists():
        for session_dir in sorted(WORKSPACES_DIR.iterdir(), reverse=True):
            if session_dir.is_dir() and session_dir.name.startswith("session_"):
                # Check for trace.json to determine status
                trace_file = session_dir / "trace.json"
                status = "complete" if trace_file.exists() else "incomplete"
                
                sessions.append({
                    "session_id": session_dir.name,
                    "timestamp": session_dir.name.replace("session_", ""),
                    "workspace_path": str(session_dir),
                    "status": status
                })
    
    return {"sessions": sessions[:20]}  # Return latest 20


@app.get("/api/files")
async def list_files(path: str = ""):
    """List files in workspace - for file browser."""
    # Get the most recent session
    sessions = []
    if WORKSPACES_DIR.exists():
        for session_dir in sorted(WORKSPACES_DIR.iterdir(), reverse=True):
            if session_dir.is_dir() and session_dir.name.startswith("session_"):
                sessions.append(session_dir)
                break
    
    if not sessions:
        return {"files": [], "path": "", "workspace": ""}
    
    current_session = sessions[0]
    base_path = current_session / path if path else current_session
    
    if not base_path.exists():
        return {"files": [], "path": path, "workspace": str(current_session)}
    
    files = []
    try:
        for item in sorted(base_path.iterdir()):
            file_info = {
                "name": item.name,
                "isDirectory": item.is_dir(),
                "path": str(item.relative_to(current_session))
            }
            if item.is_file():
                file_info["size"] = item.stat().st_size
            files.append(file_info)
    except Exception:
        pass
    
    return {
        "files": files,
        "path": path,
        "workspace": str(current_session)
    }


@app.get("/api/file/{file_path:path}")
async def get_workspace_file(file_path: str):
    """Get file content from the most recent workspace session."""
    # Get the most recent session
    sessions = []
    if WORKSPACES_DIR.exists():
        for session_dir in sorted(WORKSPACES_DIR.iterdir(), reverse=True):
            if session_dir.is_dir() and session_dir.name.startswith("session_"):
                sessions.append(session_dir)
                break
    
    if not sessions:
        raise HTTPException(status_code=404, detail="No session found")
    
    current_session = sessions[0]
    full_path = current_session / file_path
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    
    if full_path.is_dir():
        # Return directory listing as JSON
        files = []
        for item in sorted(full_path.iterdir()):
            file_info = {
                "name": item.name,
                "isDirectory": item.is_dir(),
                "path": str(item.relative_to(current_session))
            }
            if item.is_file():
                file_info["size"] = item.stat().st_size
            files.append(file_info)
        return {"files": files, "path": file_path}
    
    return FileResponse(full_path)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details including files."""
    session_path = WORKSPACES_DIR / session_id
    
    if not session_path.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    
    files = {
        "search_results": [],
        "work_products": []
    }
    
    # List search results
    search_dir = session_path / "search_results"
    if search_dir.exists():
        files["search_results"] = [f.name for f in search_dir.iterdir() if f.is_file()]
    
    # List work products
    products_dir = session_path / "work_products"
    if products_dir.exists():
        files["work_products"] = [f.name for f in products_dir.iterdir() if f.is_file()]
    
    # Get trace if exists
    trace_file = session_path / "trace.json"
    trace_summary = None
    if trace_file.exists():
        try:
            with open(trace_file) as f:
                trace = json.load(f)
                trace_summary = {
                    "total_messages": len(trace.get("messages", [])),
                    "tool_calls": len([m for m in trace.get("messages", []) if m.get("role") == "tool"])
                }
        except Exception:
            pass
    
    return {
        "session_id": session_id,
        "files": files,
        "trace_summary": trace_summary
    }


@app.get("/api/sessions/{session_id}/files/{subdir}/{filename}")
async def get_file(session_id: str, subdir: str, filename: str):
    """Get file content from session."""
    file_path = WORKSPACES_DIR / session_id / subdir / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(file_path)


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for real-time chat with agent."""
    await manager.connect(websocket)
    
    try:
        # Send connection confirmation
        await manager.send_message(websocket, {
            "type": "connected",
            "message": "Connected to Universal Agent",
            "timestamp": datetime.now().isoformat()
        })
        
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            
            if data.get("type") == "query":
                query = data.get("content", "")
                
                if query.strip():
                    # Echo user message
                    await manager.send_message(websocket, {
                        "type": "user_message",
                        "content": query,
                        "timestamp": datetime.now().isoformat()
                    })
                    
                    # Execute agent query
                    await agent_bridge.execute_query(query, websocket)
            
            elif data.get("type") == "ping":
                await manager.send_message(websocket, {"type": "pong"})
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        manager.disconnect(websocket)
        print(f"WebSocket error: {e}")


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         UNIVERSAL AGENT — Neural Command Center              ║
║══════════════════════════════════════════════════════════════║
║  Server: http://{host}:{port}                                  
║  WebSocket: ws://{host}:{port}/ws/chat                         
╚══════════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(app, host=host, port=port)
