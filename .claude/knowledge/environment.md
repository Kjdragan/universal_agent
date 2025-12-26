# Environment & Dependency Management Rules

1. **UV Package Manager**: This project is managed by `uv`.
    - **DO NOT** use `pip install`. It will fail or install to the wrong location for the `uv` environment.
    - **DO NOT** use `python -m pip install`.
    - **DO NOT** use `apt-get` or `brew` (you do not have root access).

2. **Managing Dependencies**:
    - If you are missing a Python library (ImportError), **DO NOT** try to install it at runtime using `pip`.
    - Instead, use `uv add <package>` in the terminal if you need to add a permanent project dependency.
    - **Better yet**, checking `pyproject.toml` often reveals the valid dependencies.
    - `reportlab` and `pypdf` are readily available.

3. **System Binaries**:
    - Tools like `pandoc`, `ffmpeg`, `chrome` are system binaries, NOT Python packages.
    - You cannot install them via `uv` or `pip`.
    - If a system binary is missing, **FAIL GRACEFULLY** and try a pure-Python alternative (e.g., use `reportlab`, `weasyprint`, or `pythonhtml` conversion instead of `pandoc`).
    - The "Happy Path" for this agent is **Python-native tools** whenever possible.

4. **Python Execution Rule**:
    - When running Python scripts via `Bash`, **ALWAYS use `python`** (which points to the active venv) or `sys.executable`.
    - **NEVER** use `python3` explicitly, as it may point to the system Python (missing our dependencies).
    - Correct: `python script.py` or `python -c "..."`
    - Incorrect: `python3 script.py`
