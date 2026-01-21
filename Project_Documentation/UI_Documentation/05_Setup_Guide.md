# Universal Agent UI - Setup Guide

## Prerequisites

- **Python**: 3.12+
- **Node.js**: 18+
- **uv**: Python package manager (https://github.com/astral-sh/uv)
- **COMPOSIO_API_KEY**: Required for agent functionality

---

## Initial Setup

### 1. Clone/Verify Repository

```bash
cd /home/kjdragan/lrepos/universal_agent
```

Verify structure:
```bash
ls -la src/universal_agent/api/    # Should show server.py, events.py, agent_bridge.py
ls -la web-ui/                      # Should show package.json, app/, lib/, etc.
```

---

### 2. Backend Setup

#### Install Python Dependencies

```bash
uv sync
```

#### Verify Backend Files

```bash
ls src/universal_agent/api/
# Expected output:
# __init__.py  agent_bridge.py  events.py  server.py
```

#### Test Backend Import

```bash
uv run python -c "from universal_agent.api.server import app; print('✓ Backend imports OK')"
```

---

### 3. Frontend Setup

#### Navigate to web-ui Directory

```bash
cd web-ui
```

#### Install Node Dependencies

```bash
npm install
```

Expected output size: ~150-200 MB in `node_modules/`

#### Verify Installation

```bash
npm run build
```

Should complete successfully with route table:
```
Route (app)              Size    First Load JS
┌ ○ /                   6.26 kB    108 kB
└ ○ /_not-found         992 B      103 kB
```

---

## Environment Configuration

### Create `.env` File

In the project root (`/home/kjdragan/lrepos/universal_agent/`):

```bash
# Composio API Key (REQUIRED)
COMPOSIO_API_KEY=your_key_here

# Optional: Logfire token for tracing
LOGFIRE_TOKEN=your_token_here

# Optional: API server configuration
UA_API_PORT=8001
UA_API_HOST=0.0.0.0
```

### Get Composio API Key

1. Go to https://composio.dev
2. Sign up/login
3. Navigate to API Keys
4. Create new key (or use existing)
5. Paste into `.env`

---

## Running the Applications

### Option A: Development Mode (Recommended)

#### Terminal 1 - Backend

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run python -m universal_agent.api.server
```

Expected output:
```
╔══════════════════════════════════════════════════════════════╗
║         UNIVERSAL AGENT API SERVER v2.0                      ║
║══════════════════════════════════════════════════════════════║
║  API:     http://0.0.0.0:8001
║  WebSocket: ws://0.0.0.0:8001/ws/agent
║  Docs:    http://0.0.0.0:8001/docs
╚══════════════════════════════════════════════════════════════╝
```

#### Terminal 2 - Frontend

```bash
cd /home/kjdragan/lrepos/universal_agent/web-ui
npm run dev
```

Expected output:
```
  ▲ Next.js 15.5.9
  - Local:        http://localhost:3000
  - Network:      http://192.168.1.x:3000

 ✓ Ready in 2.3s
```

#### Access the UI

Open browser: http://localhost:3000

**Connection Flow:**
1. UI loads
2. WebSocket connects to `ws://localhost:8001/ws/agent`
3. Server creates new session
4. "Connected" status appears in header
5. Ready to send queries

---

### Option B: Production Mode

#### Backend

```bash
cd /home/kjdragan/lrepos/universal_agent
uv run uvicorn universal_agent.api.server:app --host 0.0.0.0 --port 8001
```

#### Frontend

```bash
cd /home/kjdragan/lrepos/universal_agent/web-ui
npm run build
npm start
```

Frontend will run on port 3000.

---

## Troubleshooting

### Backend Issues

#### Port Already in Use

```
Error: [Errno 48] Address already in use
```

**Solution:** Find and kill the process
```bash
lsof -ti:8001 | xargs kill -9
```

#### Import Error

```
ModuleNotFoundError: No module named 'universal_agent.api'
```

**Solution:** Ensure you're in the project root and run:
```bash
uv sync
```

#### Composio Connection Error

```
Error: Failed to connect to Composio API
```

**Solution:** Verify `COMPOSIO_API_KEY` in `.env` file.

---

### Frontend Issues

#### npm Install Fails

```
npm ERR! code ERESOLVE
```

**Solution:** Clear cache and retry:
```bash
rm -rf node_modules package-lock.json
npm cache clean --force
npm install
```

#### Build Fails - Type Errors

```
Type error: Property 'x' does not exist on type 'y'
```

**Solution:** Ensure all dependencies are installed:
```bash
npm install --legacy-peer-deps
```

#### WebSocket Connection Refused

```
WebSocket connection to 'ws://localhost:8001/ws/agent' failed
```

**Solution:** Verify backend is running on port 8001:
```bash
curl http://localhost:8001/api/health
```

---

### CORS Issues

If frontend (port 3000) can't connect to backend (port 8001):

**Backend Fix** - In `src/universal_agent/api/server.py`:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Add this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Development Workflow

### Making Backend Changes

1. Edit files in `src/universal_agent/api/`
2. Backend auto-reloads (uvicorn --reload)
3. Refresh browser to see changes

### Making Frontend Changes

1. Edit files in `web-ui/`
2. Next.js hot-reloads automatically
3. Changes appear instantly

### Checking Both Are Running

```bash
# Backend
curl http://localhost:8001/api/health

# Frontend
curl http://localhost:3000
```

---

## File Locations for Quick Reference

| What | Location |
|------|----------|
| Backend server | `src/universal_agent/api/server.py` |
| WebSocket handler | `src/universal_agent/api/server.py:~380` |
| Agent bridge | `src/universal_agent/api/agent_bridge.py` |
| Event definitions | `src/universal_agent/api/events.py` |
| Frontend main page | `web-ui/app/page.tsx` |
| WebSocket client | `web-ui/lib/websocket.ts` |
| Zustand store | `web-ui/lib/store.ts` |
| TypeScript types | `web-ui/types/agent.ts` |
| Global styles | `web-ui/app/globals.css` |
| Tailwind config | `web-ui/tailwind.config.ts` |

---

## IDE Setup Recommendations

### VS Code Extensions

- **TypeScript** - Microsoft
- **ESLint** - Microsoft
- **Tailwind CSS IntelliSense** - Tailwind Labs
- **Python** - Microsoft
- **Ruff** - Microsoft (Python linting)

### VS Code Workspace Settings

Create `.vscode/settings.json`:
```json
{
  "typescript.preferences.importModuleSpecifier": "relative",
  "editor.formatOnSave": true,
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "tailwindCSS.experimental.classRegex": [
    ["cn\\(([^)]*)\\)", "[\"'`]([^\"'`]*).*?[\"'`]"]
  ]
}
```

---

## Next Steps

After setup is complete:

1. **Read the Architecture** (`02_Architecture.md`)
2. **Review API Reference** (`03_API_Reference.md`)
3. **Understand Components** (`04_Component_Guide.md`)
4. **Follow Testing Guide** (`06_Testing_Guide.md`)
