# Front-End Design Documentation

This directory contains documentation explaining the Universal Agent UI and its integration with the Python backend.

## Document Index

| # | Document | Description |
|---|----------|-------------|
| 001 | [Architecture Overview](./001_Architecture_Overview.md) | High-level architecture, WebSocket communication |
| 002 | [HTML Structure](./002_HTML_Structure.md) | Layout, panels, key elements |
| 003 | [CSS Styling](./003_CSS_Styling.md) | Variables, grid, animations, glassmorphism |
| 004 | [JavaScript WebSocket Client](./004_JavaScript_WebSocket_Client.md) | Connection, sending/receiving, DOM manipulation |
| 005 | [Event Types Protocol](./005_Event_Types_Protocol.md) | Message format, event types, data flow |

## Quick Reference

### Start the UI
```bash
uv run uvicorn src.universal_agent.server:app --reload
```
Open http://localhost:8000

### Key Files
- `universal_agent_ui.html` - The frontend (HTML/CSS/JS)
- `src/universal_agent/server.py` - FastAPI + WebSocket server
- `src/universal_agent/agent_core.py` - Agent logic with event streaming
