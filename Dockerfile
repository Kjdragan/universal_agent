FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv (same package manager as local dev)
RUN pip install uv

# Copy project definition
COPY pyproject.toml .
COPY uv.lock .

# Install dependencies using uv (matches local .venv)
RUN uv sync --frozen

# Install additional bot dependencies
RUN uv pip install uvicorn python-telegram-bot nest_asyncio aiohttp

# Copy Source Code
COPY src /app/src
COPY Memory_System /app/Memory_System
COPY Project_Documentation /app/Project_Documentation

# Set Environment Variables
ENV PYTHONPATH=/app/src:/app
ENV PORT=8000
ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Create a non-root user (Claude SDK refuses --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash agentuser && \
    chown -R agentuser:agentuser /app

# Create workspace directory with correct ownership (AFTER chown)
RUN mkdir -p /app/AGENT_RUN_WORKSPACES && \
    chown agentuser:agentuser /app/AGENT_RUN_WORKSPACES

USER agentuser

# Run Command
CMD ["python", "-m", "universal_agent.bot.main"]
