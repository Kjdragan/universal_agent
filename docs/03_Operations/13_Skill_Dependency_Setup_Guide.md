# Skill Dependency Setup Guide

This guide walks you through installing the required binaries and dependencies for "Gated" skills—skills that appear as `(Unavailable: Missing binary: ...)` in your `capabilities.md`.

## 🔍 Diagnosing Missing Skills

1. Check `src/universal_agent/prompt_assets/capabilities.md`.
2. Look for lines like:
    - `~~**1password**~~ (Unavailable: Missing binary: op)`
    - `~~**obsidian**~~ (Unavailable: Missing binary: obsidian-cli)`

These messages indicate that the Universal Agent found the skill but disabled it because a required command-line tool is missing from your system `$PATH`.

## 🛠️ Installing Missing Dependencies

### 1. 1Password (`op`)

**Requirement**: The `1password` skill requires the 1Password CLI (`op`).

- **MacOS (Homebrew)**: `brew install 1password-cli`
- **Linux**: Follow the [official documentation](https://developer.1password.com/docs/cli/get-started/#install).
  - *Debian/Ubuntu*:

        ```bash
        curl -sS https://downloads.1password.com/linux/keys/1password.asc | sudo gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg
        echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main' | sudo tee /etc/apt/sources.list.d/1password.list
        sudo mkdir -p /etc/debsig/policies/AC2D62742012EA22/
        curl -sS https://downloads.1password.com/linux/debian/debsig/1password.pol | sudo tee /etc/debsig/policies/AC2D62742012EA22/1password.pol
        sudo mkdir -p /usr/share/debsig/keyrings/AC2D62742012EA22/
        curl -sS https://downloads.1password.com/linux/keys/1password.asc | sudo gpg --dearmor --output /usr/share/debsig/keyrings/AC2D62742012EA22/debsig.gpg
        sudo apt update && sudo apt install 1password-cli
        ```

* **Verification**: Run `op --version`.

### 2. Obsidian (`obsidian-cli`)

**Requirement**: The `obsidian` skill requires `obsidian-cli` (specifically the `yakitrak` version).

- **MacOS (Homebrew)**: `brew install yakitrak/yakitrak/obsidian-cli`
- **Linux (Go)**:

    ```bash
    go install github.com/yakitrak/obsidian-cli@latest
    # Ensure $HOME/go/bin is in your $PATH
    ```

* **Linux (Binary)**: Download the latest release from [GitHub](https://github.com/yakitrak/obsidian-cli/releases) and place it in your `$PATH`.
- **Verification**: Run `obsidian-cli version`.

### 3. tmux (`tmux`)

**Requirement**: The `tmux` skill requires the `tmux` terminal multiplexer.

- **MacOS**: `brew install tmux`
- **Linux (apt)**: `sudo apt install tmux`
- **Verification**: Run `tmux -V`.

### 4. Spotify (`spogo` or `spotify_player`)

**Requirement**: The `spotify-player` skill requires either `spogo` (preferred) or `spotify_player`.

- **spogo (Go)**:

    ```bash
    go install github.com/kelvinromerobenitez/spogo/cmd/spogo@latest
    ```

* **spotify_player (Rust/Cargo)**:

    ```bash
    cargo install spotify_player
    ```

  - *Note*: Requires `libssl-dev`, `libasound2-dev`, `libdbus-1-dev` on Linux.
- **Verification**: Run `spogo version` or `spotify_player --version`.

### 5. Summarize (`summarize`)

**Requirement**: The `summarize` skill requires the `summarize` binary.

- **Install via Go**:

    ```bash
    # Assuming the tool source is available or a specific repo
    # If using the 'summarize' gem or python tool, install accordingly.
    # The skill metadata references 'steipete/tap/summarize' which suggests a brew tap.
    # For Linux, check if a binary release exists or build from source.
    ```

* **Verification**: Run `summarize --help`.

## 📦 Managing Python Dependencies (`uv`)

For skills that depend on specific Python packages (defined in `pyproject.toml` or `uv.lock`), you should use `uv` to manage them.

1. **Add a dependency**:

    ```bash
    uv add <package_name>
    ```

2. **Sync environment**:

    ```bash
    uv sync
    ```

3. **Run a script with dependencies**:

    ```bash
    uv run scripts/my_script.py
    ```

## ✅ Verifying the Fix

After installing the missing binaries, you must restart the agent or force a refresh of the capabilities.

1. **Restart the Agent**: Just kill and restart your current session.
2. **Manual Verification**:

    ```bash
    uv run scripts/verify_skills.py
    ```

3. **Check Output**:
    - The skills should now appear as **Active**:
    - `- **1password**: ...` (instead of `~~**1password**~~`)
