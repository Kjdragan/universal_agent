FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# ffmpeg is needed for video expert
# build-essential and git for installing python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install project dependencies
COPY pyproject.toml .
# Attempt to install dependencies. Assuming standard pip installable.
# If using requirements.txt, copy that instead.
RUN pip install --no-cache-dir .

RUN pip install uvicorn python-telegram-bot nest_asyncio

# Copy Source Code
COPY src /app/src
# Copy Documentation (optional, for knowledge base?)
COPY Project_Documentation /app/Project_Documentation

# Set Environment Variables
ENV PYTHONPATH=/app/src
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

# Run Command
CMD ["python", "-m", "universal_agent.bot.main"]
