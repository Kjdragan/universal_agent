# Universal Agent UI - Modern Web Interface

AGI-era Neural Command Center with real-time chat, monitoring dashboard, and work product visualization.

## Features

- **Real-time Chat Interface**: Streaming agent responses with markdown support
- **Terminal-Style Monitoring**: Live tool call visualization with expandable details
- **Work Product Viewer**: Tabbed interface for viewing HTML reports and generated files
- **Session Management**: Browse and resume previous agent sessions
- **Activity Feed**: Real-time activity stream showing tool calls and work products
- **Metrics Dashboard**: Token usage, tool call count, duration tracking

## Architecture

### Backend (Python/FastAPI)
- **FastAPI Server**: `src/universal_agent/api/server.py`
- **WebSocket Events**: `src/universal_agent/api/events.py`
- **Agent Bridge**: `src/universal_agent/api/agent_bridge.py`
- **Agent Core**: `src/universal_agent/agent_core.py` (existing)

### Frontend (Next.js/React)
- **Framework**: Next.js 15 with App Router
- **UI Library**: Radix UI primitives
- **Styling**: Tailwind CSS with AGI-era dark theme
- **State**: Zustand for global state
- **Real-time**: WebSocket connection

## Getting Started

### Prerequisites
- Python 3.12+
- Node.js 18+
- COMPOSIO_API_KEY environment variable

### Backend Setup

1. **Navigate to project root**:
   ```bash
   cd /home/kjdragan/lrepos/universal_agent
   ```

2. **Ensure dependencies are installed** (using uv):
   ```bash
   uv sync
   ```

3. **Start the API server**:
   ```bash
   uv run python -m universal_agent.api.server
   ```

   The server will start on `http://localhost:8001`

### Frontend Setup

1. **Navigate to web-ui directory**:
   ```bash
   cd web-ui
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Start the development server**:
   ```bash
   npm run dev
   ```

   The UI will be available at `http://localhost:3000`

### Environment Variables

Create a `.env` file in the project root:

```bash
# Composio API Key (required)
COMPOSIO_API_KEY=your_key_here

# Optional: Logfire token for tracing
LOGFIRE_TOKEN=your_token_here

# Optional: API server port (default: 8001)
UA_API_PORT=8001

# Optional: API server host (default: 0.0.0.0)
UA_API_HOST=0.0.0.0
```

## Project Structure

```
universal_agent/
├── src/universal_agent/
│   ├── api/                    # NEW - Web API server
│   │   ├── server.py          # FastAPI server with WebSocket
│   │   ├── events.py          # WebSocket event protocol
│   │   └── agent_bridge.py    # Agent integration layer
│   ├── agent_core.py          # Core agent (existing)
│   └── urw/                   # URW orchestrator (existing)
└── web-ui/                     # NEW - Next.js frontend
    ├── app/
    │   ├── page.tsx           # Main dashboard
    │   ├── layout.tsx         # Root layout
    │   └── globals.css        # Global styles
    ├── components/
    │   ├── chat/              # Chat components
    │   ├── monitor/           # Monitoring components
    │   └── ui/                # Base UI components
    ├── lib/
    │   ├── store.ts           # Zustand store
    │   ├── websocket.ts       # WebSocket manager
    │   └── utils.ts           # Utility functions
    └── types/
        └── agent.ts           # TypeScript types
```

## WebSocket Event Protocol

### Server -> Client Events

- **connected**: Connection established with session info
- **text**: Streaming text response from agent
- **tool_call**: Tool being executed with params
- **tool_result**: Tool execution result
- **thinking**: Agent internal reasoning
- **status**: Status updates (processing, complete, error)
- **work_product**: HTML reports, files generated
- **query_complete**: Query execution finished
- **error**: Error occurred

### Client -> Server Events

- **query**: Send user query to agent
- **approval**: Submit approval for URW tasks
- **ping**: Keep-alive ping

## Development

### Backend Development

Run the API server with auto-reload:

```bash
uv run python -m universal_agent.api.server
```

API docs available at: `http://localhost:8001/docs`

### Frontend Development

Run the Next.js dev server:

```bash
cd web-ui
npm run dev
```

### Building for Production

Frontend:
```bash
cd web-ui
npm run build
npm start
```

Backend (use gunicorn/uvicorn in production):
```bash
uvicorn universal_agent.api.server:app --host 0.0.0.0 --port 8001
```

## Troubleshooting

### WebSocket Connection Issues

1. Check if backend is running on port 8001
2. Verify CORS settings in `server.py`
3. Check browser console for WebSocket errors

### Agent Not Responding

1. Verify COMPOSIO_API_KEY is set
2. Check backend logs for errors
3. Ensure MCP server is accessible

### Work Products Not Displaying

1. Check iframe permissions in browser
2. Verify work_products directory exists
3. Check file permissions

## License

MIT
