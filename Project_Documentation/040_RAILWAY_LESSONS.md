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

## Summary Checklist for Future Deployments
- [ ] **One Entrypoint**: Use `Dockerfile` OR `Procfile`, not both (unless you really want split services).
- [ ] **Bind 0.0.0.0**: Always listen on all interfaces, not just localhost.
- [ ] **Respect $PORT**: Your app finds the internet through this variable.
- [ ] **Pin Versions**: Lock your Python version to something stable (3.12 is the current "Sweet Spot").
- [ ] **Code Your Deps**: Don't just `pip install`. Add it to `pyproject.toml`.
