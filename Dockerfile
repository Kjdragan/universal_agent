FROM python:3.12-slim-bookworm

# Install UV
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install System Dependencies
# - ffmpeg (Video)
# - libcairo2, libpango* (WeasyPrint)
# - curl, gnupg, wget, unzip (Utils)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    gnupg \
    unzip \
    curl \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome (for Crawl4AI local fallback & PDF)
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy project definition & lock file
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev --python /usr/local/bin/python

# Copy Source Code
COPY src/ ./src/
COPY AgentCollege/ ./AgentCollege/
COPY Memory_System/ ./Memory_System/
COPY .claude/ ./.claude/

# Copy start script
COPY start.sh ./
RUN chmod +x start.sh

# Environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src:/app"

# Create a non-root user and group
# This is required because the Claude CLI refuses to run with --dangerously-skip-permissions as root
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Create data directory and set permissions
# We do this before switching user so the directory exists with correct ownership
RUN mkdir -p /app/data && \
    mkdir -p /app/AGENT_RUN_WORKSPACES && \
    chown -R appuser:appuser /app

# Switch to non-root user (Moved to start.sh for runtime permission fix)
# USER appuser

# Entrypoint
CMD ["./start.sh"]
