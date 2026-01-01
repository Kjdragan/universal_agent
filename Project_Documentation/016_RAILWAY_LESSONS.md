# 🚂 Railway Deployment: Lessons Learned

## 1. The Tale of Two Services (Docker vs. Procfile)
When deploying to Railway, you have two main ways to tell it how to start your app:
1.  **Dockerfile**: The "Container Native" way. Railway builds an image and runs the `CMD` or `ENTRYPOINT`.
2.  **Procfile**: The "Heroku/PaaS" way. Railway reads a file that lists services like `web: ...` and `worker: ...`.

**The Issue**:
We had *both*.
- Our `Dockerfile` defined a monolithic start script `start.sh` (running everything together).
- Our `Procfile` defined split services (`web` and `worker`).

**The Result**:
Railway saw the `Procfile` and tried to be helpful by spinning up **two** separate containers: a Web container and a Worker container.
- This doubled our cost.
- It broke our architecture because both containers tried to access their own separate SQLite databases instead of sharing one.

**The Fix**:
We **deleted the `Procfile`**. This forced Railway to ignore the PaaS logic and just "run the Docker container as is," which executed our `start.sh` script, keeping everything in one happy, shared home.

---

## 2. The Case of the Missing Port (`$PORT`)
Railway assigns a random port (e.g., 5432) to your app and tells you about it via the environment variable `$PORT`. Your app **must** listen on this specific port, or Railway thinks it crashed.

**The Issue**:
Our start script ran:
```bash
exec uv run src/universal_agent/bot/main.py
```
Inside `main.py`, we had:
```python
uvicorn.run(..., port=os.getenv("PORT"))
```
However, the way `start.sh` invoked the script masked the environment variable handling or caused import errors before it could even start.

**The Fix**:
We changed `start.sh` to run `uvicorn` directly, which is the industry standard server runner:
```bash
exec uv run uvicorn universal_agent.bot.main:app --host 0.0.0.0 --port $PORT
```
This ensures `uvicorn` (the web server) takes control immediately and binds correctly to the port Railway expects.

---

## 3. The Python Version Trap (`cp314`)
**The Issue**:
Our `pyproject.toml` said `requires-python = ">=3.13"`.
- This meant: "I am okay with Python 3.13, 3.14, or anything newer."
- When `uv sync` ran, it saw "Oh, Python 3.14 (preview) exists! Let's try to use that."
- But our libraries (like `onnxruntime`) didn't have versions built for Python 3.14 yet, so the build exploded.

**The Fix**:
 We pinned the requirement down:
```toml
requires-python = ">=3.12"
```
And explicitly told Docker to use the system's Python 3.12:
```bash
uv sync --python /usr/local/bin/python
```
**Lesson**: Always be specific about your Python version in production. "Newer" isn't always better if your libraries aren't ready for it.

---

## 4. The Vanishing Dependencies
**The Issue**:
We noticed vital tools like `crawl4ai` (for web scraping) disappeared when we successfully synced dependencies.
- This happened because `uv sync` is "destructive" by default—it makes your environment match `pyproject.toml` *exactly*.
- If a package is installed but not written in `pyproject.toml`, `uv sync` deletes it.

**The Fix**:
We manually added the missing packages to `pyproject.toml`.
**Lesson**: If it's not in the file, it doesn't exist.

---

## 5. The "Custom Start Command" Override (Silent Killer)
**The Issue**:
Even after fixing our `Dockerfile` and `start.sh`, the app kept failing with errors we couldn't see in our script debugging.
**Why**: Railway has a "Start Command" field in Settings. If this is populated, **it ignores your Dockerfile CMD entirely**.
We had an old command cached there (`uvicorn src...`) that was broken.

**The Fix**:
We went to Settings -> Deploy -> Start Command and **deleted it**. This restored control to our `Dockerfile`.

---

## 6. Quotes in Variables (The Crash)
**The Issue**:
The app crashed with `Invalid webhook url`.
**Why**: We entered `"https://..."` (with quotes) in the Railway Variable UI. Railway passes this literally, so the app tried to use the quote character as part of the URL.

**The Fix**:
Never put quotes around values in Railway Variables.
- Bad: `"https://example.com"`
- Good: `https://example.com`

---

## 7. The Permissions Paradox (Root vs. AppUser)
**The Issue**:
The app crashed with `PermissionError: [Errno 13] Permission denied: '/app/data/workspaces'`.
**Why**:
1.  Railway mounts Persistent Volumes (`/app/data`) owned by **root**.
2.  We configured our Dockerfile to run as `USER appuser` (non-root) to satisfy the strict security checks of the Claude Agent SDK (`--dangerously-skip-permissions` fails as root).
3.  **Conflict**: `appuser` cannot write to the `root`-owned volume.

**The Fix (The "Sudo" Sandwich)**:
We implemented a startup pattern that gets the best of both worlds:
1.  **Start as Root**: The Dockerfile starts as root (removed `USER appuser`).
2.  **Fix Permissions**: `start.sh` runs `chown -R appuser /app/data`.
3.  **Drop Privileges**: `start.sh` then executes the app using `su appuser`.

```bash
# In start.sh
chown -R appuser:appuser /app/data
exec su -s /bin/bash appuser -c "exec uv run ..."
```
**Lesson**: When using volumes with non-root containers, you often need a root entrypoint to fix permissions before dropping privileges.

---

## 8. The "Forgotten History" (Git Sanitization)
**The Issue**:
We accidentally committed a directory `AI_DOCS/` containing a leaked API key. Simply adding it to `.gitignore` was not enough because the file persisted in the git history, keeping the repo vulnerable.

**The Fix**:
We performed a destructive history rewrite:
1.  **Filter Branch**: Used `git filter-branch` to scrub the folder from all commits.
2.  **Force Push**: Ran `git push origin main --force`.
    -   **Result**: Railway gracefully handled the force push, detected the new commit SHA, and triggered a fresh build automatically.

**Lesson**:
-   `.gitignore` is for the future. `git filter-branch` is for the past.
-   Railway is robust enough to handle force-pushed history rewrites without manual intervention.

---

## 9. Monolith vs Sidecar (Agent College)
**The Issue**:
We needed to run a secondary service ("Agent College") alongside the main Telegram Bot in the same container to share the filesystem and memory.

**The Fix**:
In `start.sh`, we launched the sidecar in the background before the main process:
```bash
# Start Sidecar (Background) - Internal port only
su -s /bin/bash appuser -c "uv run uvicorn AgentCollege.logfire_fetch.main:app --port 8000" &

# Start Main App (Foreground) - Public PORT
exec su -s /bin/bash appuser -c "uv run uvicorn universal_agent.bot.main:app --port $PORT"
```

**Lesson**: Only the main process receives external traffic via `$PORT`. Sidecars communicate internally (localhost) or just process background tasks.

---

## 10. The Immortal Zombie Process (Session Health)
**The Issue**:
Our Telegram Bot would successfully complete a task, but then become unresponsive to subsequent commands.
**Why**:
The `AgentAdapter` maintained a single, persistent session with the Anthropic SDK backend. If the background "worker actor" thread crashed (due to a network flutter or internal error) or hung, the `AgentAdapter` didn't know. It would happily queue new tasks into a void, waiting forever for a response that would never come.
This is a classic "Split Brain" problem: The frontend (webhooks) was alive, but the backend (worker) was dead.

**The Fix**:
We implemented a **Health Check & Timeout** strategy (Watchdog Pattern):
1.  **Check Pulse**: Before submitting any task, we check `if self.worker_task.done()`. If the worker is dead, we force a full re-initialization immediately.
2.  **Strict Timeout**: We wrapped the execution await in `asyncio.wait_for(..., timeout=300)`. If the agent takes longer than 5 minutes (or hangs), we kill the session and raise an error, freeing up the queue for the next request.

**Lesson**: In always-on services, never assume your background threads are immortal. Explicitly check their health and set hard timeouts on all internal communication.

---

## 11. Notification Reliability

**The Issue**:
Telegram bot sometimes fails to send the final summary message after a task, resulting in errors like `can't find end of the entity` due to malformed Markdown.

**Why**:
The formatted summary contained unescaped Markdown‑V2 special characters (underscores, asterisks, backticks) which Telegram's parser rejected.

**The Fix**:
1. Updated `format_telegram_response` to escape all Markdown‑V2 special characters.
2. Set `parse_mode="MarkdownV2"` when sending messages.
3. Added a fallback to plain‑text if escaping fails.
4. Implemented retry logic with clear logging for each attempt.

**Lesson**:
Always sanitize user‑generated content before sending it to Telegram and use a robust retry/timeout strategy.

---

## Summary Checklist for Future Deployments

## Summary Checklist for Future Deployments
- [ ] **One Entrypoint**: Use `Dockerfile` OR `Procfile`, not both.
- [ ] **Empty Start Command**: Clear Railway Settings overrides.
- [ ] **No Quotes**: Clean env vars in Railway UI.
- [ ] **Bind 0.0.0.0**: Listen on all interfaces.
- [ ] **Respect $PORT**: Bind main app to this variable.
- [ ] **Pin Versions**: Lock Python to stable (3.12).
- [ ] **Code Your Deps**: `pyproject.toml` is the source of truth.
- [ ] **Check Permissions**: Root startup -> Chown volumes -> AppUser runtime.
- [ ] **Sanitize History**: Force push clean history if secrets leak.
- [ ] **Verify**: Always curl `/health` after deploy.
- [ ] **Watchdog**: Implement timeouts for internal queues.
