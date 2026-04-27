
from datetime import datetime
import os
from pathlib import Path
import sys

# Configuration
PROJECT_ROOT = Path(".").resolve()
DOCS_ROOT = PROJECT_ROOT / "docs"
SRC_ROOT = PROJECT_ROOT / "src" / "universal_agent"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def scaffold_structure():
    log("🏗️ Scaffolding Documentation Structure...")
    folders = [
        "01_Architecture",
        "02_Subsystems/Gateway",
        "02_Subsystems/Agents",
        "02_Subsystems/Memory",
        "02_Subsystems/Proactive",
        "03_Operations",
        "04_API_Reference",
        "05_Archive"
    ]
    
    if not DOCS_ROOT.exists():
        DOCS_ROOT.mkdir()
    
    for folder in folders:
        path = DOCS_ROOT / folder
        path.mkdir(parents=True, exist_ok=True)

def create_root_context_files():
    log("🔍 Checking Root Context Files (Source of Truth)...")
    
    # CLAUDE.md
    claude_md = PROJECT_ROOT / "CLAUDE.md"
    if not claude_md.exists():
        content = """# CLAUDE.md - Context & Rules for Claude (Universal Agent)

## Project Overview
This is the **Universal Agent** project. 
- **Gateway**: FastAPI-based server (`src/universal_agent/gateway_server.py`)
- **Telegram Bot**: (`src/universal_agent/bot/main.py`)
- **CLI**: (`src/universal_agent/main.py`)

## Core Principles
1.  **Source of Truth**: Always read the code in `src/` to understand behavior. Do not rely on old docs.
2.  **Tool Usage**: Use `uv run` for all python commands.
3.  **Testing**: Use `uv run pytest`.

## Key Architectures
- **Memory**: Hindsight system (JSON/Files).
- **Heartbeat**: Periodic wake-ups checked against `memory/HEARTBEAT.md`.
"""
        with open(claude_md, "w") as f:
            f.write(content)
        log("✅ Created CLAUDE.md")
    else:
        log("✅ CLAUDE.md exists")

    # AGENT.md
    agent_md = PROJECT_ROOT / "AGENT.md"
    if not agent_md.exists():
        content = """# AGENT.md - Context for General Agents

## Project Identity
**Universal Agent**: A flexible, multi-modal agent framework.

## Developer Rules
1.  **Code First**: Docs are secondary to code. Analyze `src/` to determine truth.
2.  **Artifacts**: Store designs in `docs/`.
"""
        with open(agent_md, "w") as f:
            f.write(content)
        log("✅ Created AGENT.md")
    else:
        log("✅ AGENT.md exists")

def create_system_overview():
    overview = DOCS_ROOT / "01_Architecture" / "System_Overview.md"
    if not overview.exists():
        content = """# System Overview

## 1. High-Level Architecture
```mermaid
graph TD
    User -->|Web/CLI/Tele| Gateway
    Gateway --> Agent
    Agent --> Memory
    Heartbeat -->|Wake| Agent
```

## 2. Request Flow
```mermaid
sequenceDiagram
    User->>Gateway: Request
    Gateway->>Agent: Session
    Agent->>Agent: Think & Act
    Agent-->>Gateway: Result
    Gateway-->>User: Response
```
"""
        with open(overview, "w") as f:
            f.write(content)
        log("✅ Created initial System_Overview.md with diagrams.")

def main():
    log("🚀 Starting Documentation Generation (Heartbeat Task)...")
    scaffold_structure()
    create_root_context_files()
    create_system_overview()
    
    # Create Status Report
    status = DOCS_ROOT / "Documentation_Status.md"
    with open(status, "w") as f:
        f.write(f"# Documentation Status\nLast Run: {datetime.now()}\n")
        f.write("✅ Structure Scaffolds\n✅ Root Context Files\n✅ System Overview\n")
    
    log("🏁 Documentation Generation Complete.")

if __name__ == "__main__":
    main()
