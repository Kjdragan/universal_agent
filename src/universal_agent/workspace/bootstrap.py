from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_PROMPT_ASSETS_DIR = Path(__file__).resolve().parents[1] / "prompt_assets"
_GLOBAL_MEMORY_DIR = _ROOT / "memory"

_KEY_FILES = (
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "IDENTITY.md",
    "USER.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
    "MEMORY.md",
)


def _read_file_if_exists(path: Path) -> str:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def _default_agents_md() -> str:
    return (
        "# AGENTS.md\n\n"
        "This workspace is managed by Universal Agent.\n\n"
        "## Startup Checklist\n"
        "1. Read `SOUL.md` for persona and behavior.\n"
        "2. Read `IDENTITY.md` and `USER.md` for relationship context.\n"
        "3. Use `MEMORY.md` and `memory/*.md` to maintain continuity.\n\n"
        "## Memory Contract\n"
        "- Use `memory_search` to recall context.\n"
        "- Use `memory_get` for exact line reads.\n"
        "- Keep durable notes concise and factual.\n"
    )


def _default_tools_md() -> str:
    return (
        "# TOOLS.md\n\n"
        "Core memory tools:\n"
        "- `memory_search`\n"
        "- `memory_get`\n\n"
        "Use tools directly; avoid ad-hoc SDK scripting when MCP/local tools are available.\n"
    )


def _default_identity_md() -> str:
    return (
        "# IDENTITY.md\n\n"
        "- Agent: Simone\n"
        "- Role: Universal Agent and execution orchestrator\n"
        "- Mission: operate as an autonomous AI organization that creates value 24/7 for Kevin\n"
        "- Operating style: direct, practical, and execution-first\n"
        "- Primary objective: reliable execution, continuity, and proactive support\n"
    )


def _default_user_md() -> str:
    return (
        "# USER.md\n\n"
        "- Name: Kevin\n"
        "- Preferred collaboration: direct, practical, no hedging\n"
        "- Current business: rebuilding ClearSpring CG as an AI-first consulting and agentic systems firm\n"
        "- 12-month target: monetize AI work and reach $10k/month in revenue\n"
        "- Highest priority track: AI-native freelance automation (win and deliver jobs with human-in-the-loop oversight)\n"
        "- Core values: scientific thinking, family prosperity, love for wife Marie, intellectual rigor\n"
        "- Main constraint: money/revenue urgency\n"
        "- Main strength: high focus availability for high-value opportunities\n"
        "- Keep this file updated as stable preferences and goals evolve.\n"
    )


def _default_heartbeat_md() -> str:
    return (
        "# HEARTBEAT.md\n\n"
        "- Keep this file small.\n"
        "- Put only actionable proactive checks here.\n"
        "- Leave empty (or comments only) to suppress no-op heartbeat runs.\n"
    )


def _default_bootstrap_md() -> str:
    return (
        "# BOOTSTRAP.md\n\n"
        "Workspace bootstrap complete.\n\n"
        "Review and personalize:\n"
        "- `SOUL.md`\n"
        "- `IDENTITY.md`\n"
        "- `USER.md`\n"
        "- `HEARTBEAT.md`\n"
    )


def _default_memory_md() -> str:
    return (
        "# MEMORY.md\n\n"
        "Durable memory for ongoing work and stable user/system context.\n\n"
        "Use daily files under `memory/YYYY-MM-DD.md` for session-derived notes.\n"
    )


def _resolve_template_content(name: str) -> str:
    if name == "SOUL.md":
        content = _read_file_if_exists(_PROMPT_ASSETS_DIR / "SOUL.md")
        if content:
            return content + "\n"
        return "# SOUL.md\n\nDefine persona, style, and operating boundaries.\n"

    if name == "HEARTBEAT.md":
        content = _read_file_if_exists(_GLOBAL_MEMORY_DIR / "HEARTBEAT.md")
        if content:
            return content + "\n"
        return _default_heartbeat_md()

    if name == "MEMORY.md":
        content = _read_file_if_exists(_GLOBAL_MEMORY_DIR / "MEMORY.md")
        if content:
            return content + "\n"
        return _default_memory_md()

    if name == "AGENTS.md":
        return _default_agents_md()
    if name == "TOOLS.md":
        return _default_tools_md()
    if name == "IDENTITY.md":
        return _default_identity_md()
    if name == "USER.md":
        return _default_user_md()
    if name == "BOOTSTRAP.md":
        return _default_bootstrap_md()
    return ""


def _write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    return True


def seed_workspace_bootstrap(workspace_dir: str) -> dict[str, object]:
    """
    Seed OpenClaw-parity key files into a workspace.

    Policy:
    - Seed-if-missing only (non-destructive)
    - Always ensure workspace/memory directory exists
    """
    workspace = Path(workspace_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "memory").mkdir(exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []
    for file_name in _KEY_FILES:
        destination = workspace / file_name
        content = _resolve_template_content(file_name)
        created_now = _write_if_missing(destination, content)
        if created_now:
            created.append(file_name)
        else:
            skipped.append(file_name)

    return {
        "workspace": str(workspace),
        "created": created,
        "skipped": skipped,
        "memory_dir": str(workspace / "memory"),
    }
