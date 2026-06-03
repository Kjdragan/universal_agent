"""ArXiv MCP server runtime wiring.

Registers the third-party `arxiv-mcp-server` (https://pypi.org/project/arxiv-mcp-server/)
as a feature-gated stdio MCP server so agents can call
``mcp__arxiv-mcp-server__{search_papers,download_paper,read_paper,list_papers}``
directly.

Why this exists
---------------
The ``paper-to-podcast-tf`` skill (and the ``paper_to_podcast_daily`` cron that
invokes it) was *written against* these arXiv MCP tools but they were never
wired into the gateway's MCP surface — only ``notebooklm-mcp`` was (see
``notebooklm_runtime.build_notebooklm_mcp_server_config``). With no arXiv MCP
tools available, the in-process cron agent was forced to improvise with the raw
``arxiv`` Python library + ``curl``, which repeatedly hit arXiv's HTTP 429 rate
limit and failed the nightly run.

The ``arxiv-mcp-server`` package **enforces arXiv's 3-second rate limit
automatically** and backs off on 429, which is precisely the missing capability.

Enablement mirrors NotebookLM exactly: gated on ``UA_ENABLE_ARXIV_MCP`` (default
off), turned on durably in production via the Infisical ``UA_ENABLE_ARXIV_MCP``
secret which ``initialize_runtime_secrets()`` loads into ``os.environ`` at
process start.

Launch path
-----------
The server ships as a Python package run via uv:

    uv tool run arxiv-mcp-server [--storage-path <dir>]

We resolve ``uv`` to an absolute path because the systemd unit's PATH typically
omits ``/usr/local/bin`` (where uv lives on the VPS). Pre-install with
``uv tool install arxiv-mcp-server`` so ``uv tool run`` resolves the cached tool
instantly and offline rather than downloading on every cold gateway start.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return default


def arxiv_mcp_enabled() -> bool:
    """Feature flag for the arXiv MCP server (default off; on in prod)."""
    return _env_flag("UA_ENABLE_ARXIV_MCP", default=False)


def arxiv_mcp_uv_command() -> str:
    """Resolve the ``uv`` launcher used to run the arXiv MCP server.

    Honours ``UA_ARXIV_MCP_UV_COMMAND`` override; otherwise resolves ``uv`` on
    PATH and finally falls back to the canonical VPS install location. We avoid
    a bare ``"uv"`` so the server still launches when the gateway's (systemd)
    PATH does not include ``/usr/local/bin``.
    """
    override = str(os.getenv("UA_ARXIV_MCP_UV_COMMAND") or "").strip()
    if override:
        return override
    resolved = shutil.which("uv")
    if resolved:
        return resolved
    for candidate in ("/usr/local/bin/uv", "/home/ua/.local/bin/uv"):
        if os.path.exists(candidate):
            return candidate
    return "uv"


def build_arxiv_mcp_server_config() -> dict[str, Any] | None:
    """Return the stdio MCP server config for arXiv, or ``None`` when disabled.

    Shape mirrors ``build_notebooklm_mcp_server_config`` so registration in
    ``agent_setup``/``main`` is symmetric.
    """
    if not arxiv_mcp_enabled():
        return None

    command = arxiv_mcp_uv_command()
    args = ["tool", "run", "arxiv-mcp-server"]

    # Optional stable storage for downloaded papers (defaults to the server's
    # own ~/.arxiv-mcp-server/papers when unset).
    storage_path = str(os.getenv("UA_ARXIV_MCP_STORAGE_PATH") or "").strip()
    if storage_path:
        args += ["--storage-path", storage_path]

    # Pass through the small set of tuning vars the server honours, if present.
    env_payload: dict[str, str] = {}
    for key in ("MAX_RESULTS", "REQUEST_TIMEOUT"):
        value = os.getenv(key)
        if value is not None and str(value).strip() != "":
            env_payload[key] = str(value)

    return {
        "type": "stdio",
        "command": command,
        "args": args,
        "env": env_payload,
    }
