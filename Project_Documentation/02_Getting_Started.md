# Getting Started

This guide covers how to set up the Universal Agent development environment and run the system locally.

## Prerequisites
*   **Operating System**: Linux (developed on Ubuntu/Debian).
*   **Python**: Version 3.13.
*   **Package Manager**: `uv` (The Universal Agent strictly uses `uv` for dependency management).

## Installation

1.  **Clone the Repository**
    ```bash
    git clone <repository_url>
    cd universal_agent
    ```

2.  **Install Dependencies**
    Use `uv` to sync the project environment. This reads `pyproject.toml` and `uv.lock`.
    ```bash
    uv sync
    ```

3.  **Environment Configuration**
    Copy the sample environment file and populate your keys.
    ```bash
    cp .env.sample .env
    ```
    *   **Required Keys**: `ANTHROPIC_API_KEY`, `COMPOSIO_API_KEY`, plus any specific tool keys needed for your workload.

## Running the Agent

### Quick Start (Helper Script)
The easiest way to start the agent in a local development loop is via the helper script:
```bash
./start_local.sh
```
This script handles environment activation and calls the entry point with standard flags.

### Manual Execution
To run the agent directly with `uv`:
```bash
uv run python src/universal_agent/main.py --task "Your task here"
```

### Common Flags
*   `--resume`: Attempt to resume a previous session if interrupted.
*   `--headless`: Run without the TUI (useful for automation/CI).
*   `--model`: Override the default model selection.

## Verification
To verify your installation is correct, run the usage help command:
```bash
uv run python src/universal_agent/main.py --help
```
If this prints the help menu without errors, your environment is correctly configured.
